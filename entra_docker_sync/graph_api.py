"""
graph_api.py - Microsoft Graph API client helpers.

This module wraps the subset of Microsoft Graph API endpoints used by
entra-docker-sync to:

    1. List members of one or more Entra ID (Azure AD) security groups.
    2. Retrieve user profile details (displayName, userPrincipalName, etc.)
       for those members.

All functions accept a pre-obtained bearer token (via :mod:`auth`) and return
native Python data structures so that callers do not need to deal with raw
HTTP responses.

Pagination
----------
The Graph API uses OData-style ``@odata.nextLink`` continuation tokens for
large result sets.  Every ``_get_*`` helper in this module follows those links
automatically, accumulating results into a single list before returning.

Rate limiting
-------------
Graph enforces per-tenant throttling.  When the API responds with HTTP 429
this module waits for the duration specified in the ``Retry-After`` header
(defaulting to 30 s) and retries the request up to ``MAX_RETRIES`` times.

See also:
    https://learn.microsoft.com/en-us/graph/api/group-list-members
    https://learn.microsoft.com/en-us/graph/throttling
"""

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

# Maximum number of retry attempts when the API returns HTTP 429 or 503.
MAX_RETRIES = 5

# Default wait time (seconds) used when the server does not supply a
# ``Retry-After`` header.
DEFAULT_RETRY_WAIT = 30


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_group_members(token: str, group_id: str) -> list[dict[str, Any]]:
    """Return all direct members of an Entra ID group.

    Follows OData pagination automatically so the caller always receives the
    complete member list regardless of group size.

    Only objects of ``@odata.type`` ``#microsoft.graph.user`` are included;
    nested groups and service principals are silently ignored because the
    Docker provisioning workflow is user-centric.

    Args:
        token (str): A valid Microsoft Graph API bearer token.
        group_id (str): The GUID of the Entra ID security group to query.

    Returns:
        list[dict]: A list of member user objects as returned by Graph.
        Each dict contains at minimum:

        .. code-block:: json

            {
              "id": "<user-guid>",
              "displayName": "Jane Doe",
              "userPrincipalName": "jane.doe@contoso.com",
              "@odata.type": "#microsoft.graph.user"
            }

    Raises:
        requests.HTTPError: On non-recoverable HTTP errors.
    """
    url = f"{GRAPH_BASE_URL}/groups/{group_id}/members"
    # Request only the fields we actually use to reduce payload size.
    params = {
        "$select": "id,displayName,userPrincipalName,mail,accountEnabled",
        "$top": 999,  # Maximum page size allowed by Graph for this endpoint.
    }

    raw_members = _paginate(token, url, params=params)

    # Filter to user objects only.
    users = [
        m for m in raw_members
        if m.get("@odata.type") == "#microsoft.graph.user"
    ]
    logger.info(
        "Group %s: found %d member(s) (%d non-user object(s) excluded).",
        group_id,
        len(users),
        len(raw_members) - len(users),
    )
    return users


def get_user_details(token: str, user_id: str) -> dict[str, Any]:
    """Fetch profile details for a single Entra ID user.

    Args:
        token (str): A valid Microsoft Graph API bearer token.
        user_id (str): The GUID or userPrincipalName of the target user.

    Returns:
        dict: User profile object from Graph, e.g.:

        .. code-block:: json

            {
              "id": "<guid>",
              "displayName": "Jane Doe",
              "userPrincipalName": "jane.doe@contoso.com",
              "mail": "jane.doe@contoso.com",
              "accountEnabled": true
            }

    Raises:
        requests.HTTPError: If the user does not exist (404) or on other
            HTTP errors.
    """
    url = f"{GRAPH_BASE_URL}/users/{user_id}"
    params = {"$select": "id,displayName,userPrincipalName,mail,accountEnabled"}

    response = _graph_get(token, url, params=params)
    return response.json()


def resolve_group_ids(token: str, group_names: list[str]) -> dict[str, str]:
    """Resolve human-readable group display names to their Entra ID GUIDs.

    This is a convenience helper for configurations that specify groups by
    name rather than by ID.  It performs one Graph request per name.

    Args:
        token (str): A valid Microsoft Graph API bearer token.
        group_names (list[str]): Display names of the groups to resolve.

    Returns:
        dict[str, str]: Mapping of ``{display_name: group_id}``.
            Names that cannot be resolved are logged as warnings and omitted
            from the returned dict.

    Raises:
        requests.HTTPError: On non-recoverable HTTP errors.
    """
    resolved: dict[str, str] = {}

    for name in group_names:
        url = f"{GRAPH_BASE_URL}/groups"
        # Use OData filter to find the group by exact display name.
        params = {
            "$filter": f"displayName eq '{name}'",
            "$select": "id,displayName",
        }
        results = _paginate(token, url, params=params)

        if not results:
            logger.warning("Group '%s' not found in Entra ID - skipping.", name)
            continue
        if len(results) > 1:
            logger.warning(
                "Group name '%s' matched %d groups; using first match (%s).",
                name,
                len(results),
                results[0]["id"],
            )

        resolved[name] = results[0]["id"]
        logger.debug("Resolved group '%s' -> %s.", name, results[0]["id"])

    return resolved


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _graph_get(
    token: str,
    url: str,
    params: dict | None = None,
) -> requests.Response:
    """Perform a single authenticated GET request against the Graph API.

    Handles 429 / 503 responses by honouring the ``Retry-After`` header and
    retrying up to :data:`MAX_RETRIES` times.

    Args:
        token (str): Bearer token.
        url (str): Absolute URL to request.
        params (dict | None): Optional query-string parameters.

    Returns:
        requests.Response: The successful HTTP response object.

    Raises:
        requests.HTTPError: If the request fails after all retries.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        # Consistent-read header reduces chance of stale replica data.
        "ConsistencyLevel": "eventual",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        response = requests.get(url, headers=headers, params=params, timeout=30)

        if response.status_code in (429, 503):
            # Respect the server-supplied back-off window.
            retry_after = int(
                response.headers.get("Retry-After", DEFAULT_RETRY_WAIT)
            )
            logger.warning(
                "Graph API throttled (HTTP %d). Waiting %d s before retry %d/%d.",
                response.status_code,
                retry_after,
                attempt,
                MAX_RETRIES,
            )
            time.sleep(retry_after)
            continue

        # Raise for any other 4xx / 5xx.
        try:
            response.raise_for_status()
        except requests.HTTPError:
            logger.error(
                "Graph API request failed: GET %s -> HTTP %d\n%s",
                url,
                response.status_code,
                response.text,
            )
            raise

        return response

    # If we exhausted all retries the last response is still throttled.
    response.raise_for_status()
    return response  # unreachable, satisfies type-checkers


def _paginate(
    token: str,
    url: str,
    params: dict | None = None,
) -> list[dict[str, Any]]:
    """Collect all pages of a Graph API list response.

    The Graph API returns a maximum of ``$top`` items per response.  When more
    items exist the JSON body includes an ``@odata.nextLink`` URL that points
    to the next page.  This function follows those links until exhausted.

    Args:
        token (str): Bearer token.
        url (str): URL of the first page.
        params (dict | None): Query parameters applied to the *first* request
            only.  Subsequent pages use the ``nextLink`` URL verbatim.

    Returns:
        list[dict]: Aggregated ``value`` arrays from all pages.
    """
    items: list[dict[str, Any]] = []
    next_url: str | None = url
    page_params: dict | None = params  # Only pass params to the first request.

    while next_url:
        response = _graph_get(token, next_url, params=page_params)
        data = response.json()

        page_items = data.get("value", [])
        items.extend(page_items)
        logger.debug(
            "Fetched page from %s: %d item(s) (total so far: %d).",
            next_url,
            len(page_items),
            len(items),
        )

        # After the first request, params are encoded in nextLink - don't
        # pass them again or they will be duplicated in the query string.
        next_url = data.get("@odata.nextLink")
        page_params = None

    return items
