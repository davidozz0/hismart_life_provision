"""Bind a provisioned device to the user's Ayla/HiSense account."""

import time

from .auth import AylaAuth
from .config import AYLA_DEVICE_BASE_URL
from .log import get_logger

_log = get_logger("hismart.bind")


class DeviceBinder:
    """Bind a provisioned device to an Ayla cloud account."""

    def __init__(self, auth: AylaAuth):
        self._auth = auth

    def confirm_device_connected(self, dsn: str, setup_token: str, timeout: int = 60) -> dict | None:
        """Poll the Ayla cloud until the device appears as connected."""
        url = f"{AYLA_DEVICE_BASE_URL}/apiv1/devices/connected.json?dsn={dsn}&setup_token={setup_token}"
        _log.info("Polling: %s", url)

        deadline = time.time() + timeout
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                data = self._auth.api_get(url)
                _log.info("Device confirmed! (attempt %d)", attempt)
                return data
            except RuntimeError as e:
                _log.debug("Poll %d: not yet (%s)", attempt, e)
                time.sleep(2)
        _log.error("Device did not confirm within %ss", timeout)
        return None

    def bind_device(self, dsn: str, setup_token: str,
                    regtoken: str = "", lat: str = "0.0", lng: str = "0.0",
                    device_service_url: str = "") -> dict:
        """Register/bind the device to the user's account."""
        _log.info("Binding device: dsn=%s", dsn)
        body = {
            "device": {
                "dsn": dsn,
                "setup_token": setup_token,
                "lat": lat,
                "lng": lng,
            },
        }
        if regtoken:
            body["device"]["regtoken"] = regtoken

        # Try device-specific URL first (from status response), then generic
        urls = []
        if device_service_url:
            urls.append(f"https://{device_service_url}/apiv1/devices.json")
        urls.append(f"{AYLA_DEVICE_BASE_URL}/apiv1/devices.json")

        last_error = None
        for url in urls:
            try:
                result = self._auth.api_post(url, body, base_url="")
                _log.info("Device bound via %s!", url[:60])
                return result
            except RuntimeError as e:
                last_error = e
                _log.debug("Bind failed via %s: %s", url[:60], e)
        raise last_error or RuntimeError("Bind failed on all URLs")
