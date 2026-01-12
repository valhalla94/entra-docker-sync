#!/usr/bin/env python3
"""Microsoft Graph API authentication module for entra-docker-sync.

Handles OAuth2 client credentials flow to obtain access tokens
for querying Entra ID (Azure AD) group memberships.
"""

import os
import time
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class AuthenticationError(Exception):
    """Raised when authentication with Microsoft identity platform fails."""
    pass


class GraphAuthClient:
    """Manages OAuth2 client credentials authentication for Microsoft Graph API.

    Attributes:
        tenant_id (str): Azure AD tenant ID.
        client_id (str): Application (client) ID registered in Entra ID.
        client_secret (str): Client secret for the registered application.
        _token (Optional[str]): Cached access token.
        _token_expiry (float): Unix timestamp when the cached token expires.
    """

    def __init__(
        self,
        tenant_id: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        """Initialise GraphAuthClient.

        Credentials are resolved from arguments first, then from environment
        variables ENTRA_TENANT_ID, ENTRA_CLIENT_ID, and ENTRA_CLIENT_SECRET.

        Args:
            tenant_id: Azure AD tenant ID.
            client_id: Application client ID.
            client_secret: Application client secret.

        Raises:
            AuthenticationError: If any required credential is missing.
        """
        self.tenant_id = tenant_id or os.environ.get("ENTRA_TENANT_ID", "")
        self.client_id = client_id or os.environ.get("ENTRA_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("ENTRA_CLIENT_SECRET", "")

        missing = []
        if not self.tenant_id:
            missing.append("ENTRA_TENANT_ID")
        if not self.client_id:
            missing.append("ENTRA_CLIENT_ID")
        if not self.client_secret:
            missing.append("ENTRA_CLIENT_SECRET")

        if missing:
            raise AuthenticationError(
                f"Missing required credentials: {', '.join(missing)}. "
                "Set them as environment variables or pass them explicitly."
            )

        self._token: Optional[str] = None
        self._token_expiry: float = 0.0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing it if necessary.

        Returns:
            A bearer token string suitable for use in Authorization headers.

        Raises:
            AuthenticationError: If the token request fails.
        """
        if self._is_token_valid():
            logger.debug("Using cached access token.")
            return self._token  # type: ignore[return-value]

        logger.info("Requesting new access token from Microsoft identity platform.")
        self._token, self._token_expiry = self._fetch_token()
        return self._token

    def get_headers(self) -> dict:
        """Build HTTP headers containing a valid Bearer token.

        Returns:
            Dictionary with Authorization and Content-Type headers.
        """
        return {
            "Authorization": f"Bearer {self.get_access_token()}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _is_token_valid(self) -> bool:
        """Check whether the cached token is present and not expired.

        A 60-second buffer is applied so tokens are refreshed before they
        actually expire.

        Returns:
            True if the cached token can still be used, False otherwise.
        """
        buffer_seconds = 60
        return bool(self._token) and time.time() < (self._token_expiry - buffer_seconds)

    def _fetch_token(self) -> tuple[str, float]:
        """Request a new client-credentials access token.

        Returns:
            A tuple of (access_token_string, expiry_unix_timestamp).

        Raises:
            AuthenticationError: If the HTTP request fails or returns an error.
        """
        token_url = TOKEN_URL_TEMPLATE.format(tenant_id=self.tenant_id)
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": GRAPH_SCOPE,
            "grant_type": "client_credentials",
        }

        try:
            response = requests.post(token_url, data=payload, timeout=30)
        except requests.RequestException as exc:
            raise AuthenticationError(f"Token request failed with network error: {exc}") from exc

        if response.status_code != 200:
            error_detail = response.json().get("error_description", response.text)
            raise AuthenticationError(
                f"Token request returned HTTP {response.status_code}: {error_detail}"
            )

        token_data = response.json()
        access_token = token_data.get("access_token")
        expires_in = int(token_data.get("expires_in", 3600))

        if not access_token:
            raise AuthenticationError("Response did not contain an access_token field.")

        expiry = time.time() + expires_in
        logger.info("Access token obtained; expires in %d seconds.", expires_in)
        return access_token, expiry
