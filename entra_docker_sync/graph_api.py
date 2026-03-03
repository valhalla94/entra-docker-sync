import requests
import logging
import time
from entra_docker_sync.auth import get_access_token

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds

_token_cache = {
    "access_token": None,
    "expires_at": 0
}


def _get_cached_token(config):
    """Return a valid cached token or fetch a new one if expired."""
    now = time.time()
    if _token_cache["access_token"] and _token_cache["expires_at"] > now + 60:
        logger.debug("Using cached access token.")
        return _token_cache["access_token"]

    logger.info("Fetching new access token (expired or missing).")
    token_response = get_access_token(config)
    _token_cache["access_token"] = token_response["access_token"]
    # Default to 3600 seconds if expires_in not provided
    expires_in = token_response.get("expires_in", 3600)
    _token_cache["expires_at"] = now + expires_in
    return _token_cache["access_token"]


def _make_request(method, url, config, **kwargs):
    """Make an authenticated HTTP request with retry logic."""
    for attempt in range(1, MAX_RETRIES + 1):
        token = _get_cached_token(config)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        try:
            response = requests.request(method, url, headers=headers, timeout=10, **kwargs)

            if response.status_code == 401:
                logger.warning("Received 401 Unauthorized. Invalidating token cache and retrying...")
                _token_cache["access_token"] = None
                _token_cache["expires_at"] = 0
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF * attempt)
                    continue
                response.raise_for_status()

            elif response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", RETRY_BACKOFF * attempt))
                logger.warning(f"Rate limited by Graph API. Retrying after {retry_after}s (attempt {attempt}/{MAX_RETRIES}).")
                time.sleep(retry_after)
                continue

            elif response.status_code >= 500:
                logger.warning(f"Graph API server error {response.status_code} on attempt {attempt}/{MAX_RETRIES}.")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF * attempt)
                    continue
                response.raise_for_status()

            response.raise_for_status()
            return response

        except requests.exceptions.Timeout:
            logger.warning(f"Request timed out on attempt {attempt}/{MAX_RETRIES}: {url}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
            else:
                raise

        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error on attempt {attempt}/{MAX_RETRIES}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
            else:
                raise

    raise RuntimeError(f"All {MAX_RETRIES} attempts failed for {url}")


def get_group_members(group_id, config):
    """
    Retrieve members of an Entra ID group by group_id.
    Returns a list of member dicts with 'id', 'displayName', 'userPrincipalName'.
    """
    url = f"{GRAPH_BASE_URL}/groups/{group_id}/members"
    members = []

    while url:
        logger.info(f"Fetching group members from: {url}")
        response = _make_request("GET", url, config)
        data = response.json()

        for member in data.get("value", []):
            if member.get("@odata.type") == "#microsoft.graph.user":
                members.append({
                    "id": member.get("id"),
                    "displayName": member.get("displayName"),
                    "userPrincipalName": member.get("userPrincipalName")
                })

        url = data.get("@odata.nextLink")

    logger.info(f"Retrieved {len(members)} members from group {group_id}.")
    return members


def list_groups(config):
    """
    List all Entra ID groups visible to the service principal.
    Returns a list of group dicts with 'id' and 'displayName'.
    """
    url = f"{GRAPH_BASE_URL}/groups?$select=id,displayName"
    groups = []

    while url:
        logger.info(f"Fetching groups from: {url}")
        response = _make_request("GET", url, config)
        data = response.json()

        for group in data.get("value", []):
            groups.append({
                "id": group.get("id"),
                "displayName": group.get("displayName")
            })

        url = data.get("@odata.nextLink")

    logger.info(f"Retrieved {len(groups)} groups total.")
    return groups
