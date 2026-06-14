"""Ayla Cloud authentication for Hisense Hi Smart Life."""

import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

from .config import AYLA_APP_ID, AYLA_APP_SECRET, AYLA_USER_BASE_URL
from .log import get_logger

_log = get_logger("hismart.auth")


class AylaAuth:
    """Authenticate with Ayla cloud and manage tokens."""

    def __init__(self):
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at: datetime | None = None

    def login(self, email: str, password: str) -> bool:
        """Login with email/password. Returns True on success."""
        url = f"{AYLA_USER_BASE_URL}/users/sign_in.json"
        _log.info("POST %s", url)
        _log.debug("Login body: user.email=%s app_id=%s", email, AYLA_APP_ID)
        body = json.dumps({
            "user": {
                "email": email,
                "password": password,
                "application": {
                    "app_id": AYLA_APP_ID,
                    "app_secret": AYLA_APP_SECRET,
                },
            },
        }).encode("utf-8")

        req = urllib.request.Request(
            url, data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.access_token = data["access_token"]
                self.refresh_token = data.get("refresh_token", "")
                expires_in = int(data.get("expires_in", 86400))
                self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                _log.info("Login OK. Token expires in %ss", expires_in)
                return True
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            _log.error("Login HTTP %s: %s", e.code, body[:200])
            raise RuntimeError(f"Login failed (HTTP {e.code}): {body}") from e
        except urllib.error.URLError as e:
            _log.error("Login network error: %s", e.reason)
            raise RuntimeError(f"Network error during login: {e.reason}") from e

    def auth_header(self) -> str:
        """Return the Authorization header value."""
        if not self.access_token:
            raise RuntimeError("Not logged in. Call login() first.")
        return f"auth_token {self.access_token}"

    def is_expired(self) -> bool:
        """Check if the access token is expired or about to expire (<5min)."""
        if not self.expires_at:
            return True
        return datetime.now(timezone.utc) + timedelta(minutes=5) >= self.expires_at

    def api_get(self, path: str, base_url: str = AYLA_USER_BASE_URL) -> dict:
        """Make an authenticated GET request to Ayla API."""
        url = f"{base_url}/{path.lstrip('/')}" if not path.startswith("http") else path
        _log.info("REQ GET %s", url)
        _log.debug("  Headers: Authorization=auth_token ***")
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": self.auth_header(),
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                _log.info("RES %s (%dB)", resp.status, len(json.dumps(data)))
                _log.debug("  Body: %s", json.dumps(data)[:500])
                return data
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            _log.error("RES %s: %s", e.code, body[:200])
            raise RuntimeError(f"API error (HTTP {e.code}): {body}") from e

    def api_post(self, path: str, data: dict, base_url: str = AYLA_USER_BASE_URL) -> dict:
        """Make an authenticated POST request to Ayla API."""
        url = f"{base_url}/{path.lstrip('/')}" if not path.startswith("http") else path
        body = json.dumps(data).encode("utf-8")
        _log.info("REQ POST %s", url)
        _log.debug("  Body: %s", json.dumps(data)[:300])
        _log.debug("  Headers: Authorization=auth_token ***, Content-Type=application/json")
        req = urllib.request.Request(
            url, data=body,
            headers={
                "Authorization": self.auth_header(),
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Connection": "Keep-Alive",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                _log.info("RES %s (%dB)", resp.status, len(json.dumps(data)))
                return data
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            _log.error("RES %s: %s", e.code, body[:200])
            raise RuntimeError(f"API error (HTTP {e.code}): {body}") from e
