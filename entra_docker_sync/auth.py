"""
auth.py - Authentication module for Entra ID (Azure AD) via Microsoft Identity Platform.

This module handles OAuth 2.0 client credentials flow to obtain bearer tokens
for use with the Microsoft Graph API. Tokens are cached in memory for their
lifetime to avoid redundant requests.

Typical usage:
    from entra_docker_sync.auth import get_access_token

    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

Environment / config requirements:
    - AZURE_TENANT_ID   : The Directory (tenant) ID of the Entra ID tenant.
    - AZURE_CLIENT_ID   : Application (client) ID of the registered app.
    - AZURE_CLIENT_SECRET : Client secret generated for the registered app.

The registered application must be granted the following Graph API
application permissions (not delegated):
    - GroupMember.Read.All   (read group memberships)
    - User.Read.All          (read user profile details)
"""

import time
import logging
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level token cache
# ---------------------------------------------------------------------------
# We cache the token dict returned by the token endpoint so we can reuse it
# until it expires.  The cache is intentionally simple (no persistence) because
# the script is designed to be run as a short-lived process or periodic cron job.
_token_cache: dict = {
    "access_token": None,
    "expires_at": 0,  # Unix timestamp after which the token must be refreshed
}

# Refresh the token this many seconds before the reported expiry to give a
# safety margin against clock skew or slow network round-trips.
_TOKEN_REFRESH_BUFFER_SECONDS = 60


def get_access_token(config: dict) -> str:
    """Return a valid bearer token for the Microsoft Graph API.

    The function checks the in-memory cache first.  A new token is requested
    from the Microsoft Identity Platform only when the cached token is absent
    or within ``_TOKEN_REFRESH_BUFFER_SECONDS`` of expiry.

    Args:
        config (dict): Parsed application configuration.  Must contain an
            ``azure`` sub-dict with the following keys:

            .. code-block:: yaml

                azure:
                  tenant_id: "<GUID>"
                  client_id: "<GUID>"
                  client_secret: "<secret>"

    Returns:
        str: A valid OAuth 2.0 bearer access token.

    Raises:
        KeyError: If required keys are missing from *config*.
        requests.HTTPError: If the token endpoint returns a non-2xx status.
        RuntimeError: If the response body does not contain an ``access_token``.
    """
    global _token_cache

    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"]:
        logger.debug("Returning cached access token (expires in %.0f s).",
                     _token_cache["expires_at"] - now)
        return _token_cache["access_token"]

    logger.info("Requesting new access token from Microsoft Identity Platform.")
    token_url, payload = _build_token_request(config)
    response = requests.post(token_url, data=payload, timeout=30)

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        logger.error("Token request failed: %s - %s", response.status_code, response.text)
        raise

    token_data = response.json()

    if "access_token" not in token_data:
        raise RuntimeError(
            f"Token endpoint returned unexpected payload (missing 'access_token'): {token_data}"
        )

    expires_in = int(token_data.get("expires_in", 3600))
    _token_cache["access_token"] = token_data["access_token"]
    _token_cache["expires_at"] = now + expires_in - _TOKEN_REFRESH_BUFFER_SECONDS

    logger.info(
        "Access token acquired successfully (expires in %d s, cache valid for %d s).",
        expires_in,
        expires_in - _TOKEN_REFRESH_BUFFER_SECONDS,
    )
    return _token_cache["access_token"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_token_request(config: dict) -> tuple[str, dict]:
    """Construct the token endpoint URL and POST payload.

    Separated from :func:`get_access_token` to make unit-testing easier
    without mocking the network.

    Args:
        config (dict): Application configuration (see :func:`get_access_token`).

    Returns:
        tuple[str, dict]: A 2-tuple of ``(token_url, form_payload)``.
    """
    azure_cfg = config["azure"]
    tenant_id = azure_cfg["tenant_id"]
    client_id = azure_cfg["client_id"]
    client_secret = azure_cfg["client_secret"]

    token_url = (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    )
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        # Graph API audience - trailing slash is required by the endpoint.
        "scope": "https://graph.microsoft.com/.default",
    }
    return token_url, payload


def clear_token_cache() -> None:
    """Invalidate the in-memory token cache.

    Useful in tests or when you need to force re-authentication without
    restarting the process.
    """
    global _token_cache
    _token_cache = {"access_token": None, "expires_at": 0}
    logger.debug("Token cache cleared.")
