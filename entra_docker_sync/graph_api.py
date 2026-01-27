import requests
import logging
import time
from entra_docker_sync.auth import get_access_token

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds

_token_cache = {
    "token": None,
    "expires_at": 0
}


def _get_valid_token(config):
    """Return a cached token or fetch a new one if expired."""
    now = time.time()
    if _token_cache["token"] is None or now >= _token_cache["expires_at"]:
        logger.debug("Access token missing or expired, fetching a new one.")
        token_data = get_access_token(config)
        _token_cache["token"] = token_data["access_token"]
        # Default to 55 minutes if expires_in not provided
        expires_in = token_data.get("expires_in", 3300)
        _token_cache["expires_at"] = now + int(expires_in) - 60  # 1-minute buffer
    return _token_cache["token"]


def _make_request(method, url, config, **kwargs):
    """Make an authenticated HTTP request with retry logic."""
    for attempt in range(1, MAX_RETRIES + 1):
        token = _get_valid_token(config)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        try:
            response = requests.request(method, url, headers=headers, timeout=10, **kwargs)

            if response.status_code == 401:
                logger.warning("Received 401 Unauthorized. Invalidating token cache and retrying.")
                _token_cache["token"] = None
                _token_cache["expires_at"] = 0
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF * attempt)
                    continue

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", RETRY_BACKOFF * attempt))
                logger.warning(f"Rate limited by Graph API. Retrying after {retry_after}s (attempt {attempt}/{MAX_RETRIES}).")
                time.sleep(retry_after)
                continue

            response.raise_for_status()
            return response

        except requests.exceptions.Timeout:
            logger.error(f"Request timed out on attempt {attempt}/{MAX_RETRIES}: {url}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error on attempt {attempt}/{MAX_RETRIES}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error: {e}")
            raise

    raise RuntimeError(f"Failed to complete request after {MAX_RETRIES} attempts: {url}")


def get_group_members(group_id, config):
    """
    Fetch all members of an Entra ID group by group_id.
    Handles pagination via @odata.nextLink.

    Returns a list of member dicts with keys: id, displayName, userPrincipalName.
    """
    url = f"{GRAPH_BASE_URL}/groups/{group_id}/members"
    members = []

    while url:
        response = _make_request("GET", url, config)
        data = response.json()

        for member in data.get("value", []):
            members.append({
                "id": member.get("id"),
                "displayName": member.get("displayName"),
                "userPrincipalName": member.get("userPrincipalName"),
            })

        url = data.get("@odata.nextLink")
        if url:
            logger.debug(f"Fetching next page of members for group {group_id}.")

    logger.info(f"Retrieved {len(members)} member(s) for group {group_id}.")
    return members


def get_group_by_name(group_name, config):
    """
    Look up an Entra ID group by display name.

    Returns the first matching group dict or None if not found.
    """
    url = f"{GRAPH_BASE_URL}/groups"
    params = {"$filter": f"displayName eq '{group_name}'", "$select": "id,displayName"}

    response = _make_request("GET", url, config, params=params)
    data = response.json()
    groups = data.get("value", [])

    if not groups:
        logger.warning(f"No group found with name '{group_name}'.")
        return None

    if len(groups) > 1:
        logger.warning(f"Multiple groups found for name '{group_name}'. Using the first result.")

    return groups[0]
