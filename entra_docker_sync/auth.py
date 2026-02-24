import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

TOKEN_EXPIRY_BUFFER = 300  # Refresh token 5 minutes before expiry

class AuthClient:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._token = None
        self._token_expiry = 0
        self._token_url = (
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        )

    def _is_token_valid(self) -> bool:
        """Check if the current token is still valid with buffer time."""
        if not self._token:
            return False
        remaining = self._token_expiry - time.time()
        if remaining < TOKEN_EXPIRY_BUFFER:
            logger.debug(
                "Token expires in %.0f seconds, will refresh.", remaining
            )
            return False
        return True

    def _fetch_token(self) -> None:
        """Fetch a new OAuth2 token from Microsoft identity platform."""
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
        }
        try:
            response = requests.post(
                self._token_url, data=payload, timeout=15
            )
            response.raise_for_status()
        except requests.exceptions.Timeout:
            logger.error("Token request timed out for tenant %s", self.tenant_id)
            raise RuntimeError(
                f"Authentication timed out for tenant: {self.tenant_id}"
            )
        except requests.exceptions.ConnectionError as exc:
            logger.error(
                "Network error during token fetch for tenant %s: %s",
                self.tenant_id,
                exc,
            )
            raise RuntimeError(
                f"Network error while authenticating: {exc}"
            ) from exc
        except requests.exceptions.HTTPError as exc:
            status = response.status_code
            logger.error(
                "HTTP %d error fetching token for tenant %s: %s",
                status,
                self.tenant_id,
                response.text,
            )
            if status == 401:
                raise PermissionError(
                    "Invalid client credentials. Check client_id and client_secret."
                ) from exc
            if status == 400:
                error_body = response.json()
                error_code = error_body.get("error", "unknown")
                raise ValueError(
                    f"Bad token request ({error_code}): {error_body.get('error_description', '')}"
                ) from exc
            raise RuntimeError(
                f"Unexpected HTTP {status} during authentication."
            ) from exc

        token_data = response.json()
        access_token = token_data.get("access_token")
        expires_in = token_data.get("expires_in", 3600)

        if not access_token:
            logger.error(
                "Token response missing 'access_token' field: %s", token_data
            )
            raise RuntimeError(
                "Authentication succeeded but response contained no access_token."
            )

        self._token = access_token
        self._token_expiry = time.time() + int(expires_in)
        logger.info(
            "Successfully obtained token for tenant %s (expires in %ds).",
            self.tenant_id,
            expires_in,
        )

    def get_token(self) -> str:
        """Return a valid access token, refreshing if necessary."""
        if not self._is_token_valid():
            logger.debug("Fetching new access token.")
            self._fetch_token()
        return self._token

    def get_auth_headers(self) -> dict:
        """Return HTTP headers with a valid Bearer token."""
        token = self.get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }


def create_auth_client_from_env() -> AuthClient:
    """Instantiate AuthClient using environment variables.

    Required environment variables:
        ENTRA_TENANT_ID
        ENTRA_CLIENT_ID
        ENTRA_CLIENT_SECRET
    """
    missing = []
    tenant_id = os.environ.get("ENTRA_TENANT_ID", "").strip()
    client_id = os.environ.get("ENTRA_CLIENT_ID", "").strip()
    client_secret = os.environ.get("ENTRA_CLIENT_SECRET", "").strip()

    if not tenant_id:
        missing.append("ENTRA_TENANT_ID")
    if not client_id:
        missing.append("ENTRA_CLIENT_ID")
    if not client_secret:
        missing.append("ENTRA_CLIENT_SECRET")

    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    return AuthClient(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )
