"""Microsoft Graph API client for polling Entra ID group memberships."""

import logging
import time
from typing import Any

import requests

from .auth import get_access_token

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


def get_group_members(group_id: str, token: str) -> list[dict[str, Any]]:
    """Fetch all members of an Entra ID group via Microsoft Graph API.

    Args:
        group_id: The object ID of the Entra ID group.
        token: A valid Bearer token for the Graph API.

    Returns:
        A list of member objects returned by the Graph API.

    Raises:
        requests.HTTPError: If the Graph API returns a non-2xx response.
    """
    url = f"{GRAPH_BASE_URL}/groups/{group_id}/members"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    members: list[dict[str, Any]] = []

    while url:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        members.extend(data.get("value", []))
        url = data.get("@odata.nextLink")  # handle pagination

    logger.debug("Fetched %d members from group %s", len(members), group_id)
    return members


def get_group_display_name(group_id: str, token: str) -> str:
    """Retrieve the display name of an Entra ID group.

    Args:
        group_id: The object ID of the Entra ID group.
        token: A valid Bearer token for the Graph API.

    Returns:
        The display name string of the group.
    """
    url = f"{GRAPH_BASE_URL}/groups/{group_id}?$select=displayName"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json().get("displayName", group_id)


def poll_group_memberships(
    group_ids: list[str],
    poll_interval: int = 60,
    max_retries: int = 3,
) -> dict[str, list[dict[str, Any]]]:
    """Poll multiple Entra ID groups and return their current memberships.

    Fetches a fresh access token and queries each group in sequence.
    Retries on transient failures up to max_retries times.

    Args:
        group_ids: List of Entra ID group object IDs to poll.
        poll_interval: Seconds to wait between retry attempts.
        max_retries: Maximum number of retry attempts per group.

    Returns:
        A dict mapping group_id -> list of member objects.
    """
    token = get_access_token()
    memberships: dict[str, list[dict[str, Any]]] = {}

    for group_id in group_ids:
        attempt = 0
        while attempt < max_retries:
            try:
                display_name = get_group_display_name(group_id, token)
                members = get_group_members(group_id, token)
                memberships[group_id] = members
                logger.info(
                    "Group '%s' (%s): %d member(s) found.",
                    display_name,
                    group_id,
                    len(members),
                )
                break
            except requests.HTTPError as exc:
                attempt += 1
                logger.warning(
                    "HTTP error polling group %s (attempt %d/%d): %s",
                    group_id,
                    attempt,
                    max_retries,
                    exc,
                )
                if attempt < max_retries:
                    time.sleep(poll_interval)
                else:
                    logger.error(
                        "Failed to fetch membership for group %s after %d attempts.",
                        group_id,
                        max_retries,
                    )
                    memberships[group_id] = []

    return memberships


def extract_user_principals(
    members: list[dict[str, Any]],
) -> list[str]:
    """Extract userPrincipalName values from a list of Graph API member objects.

    Only objects of type '#microsoft.graph.user' are included.

    Args:
        members: List of member dicts as returned by the Graph API.

    Returns:
        Sorted list of userPrincipalName strings.
    """
    principals: list[str] = []
    for member in members:
        odata_type = member.get("@odata.type", "")
        if odata_type == "#microsoft.graph.user":
            upn = member.get("userPrincipalName")
            if upn:
                principals.append(upn)
    return sorted(principals)
