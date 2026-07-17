"""Client for the internal device-registry HTTP service.

The registry is the source of truth for every field device we manage
(sensors, gateways, kiosks). This client is read-only: reporting jobs use
it to pull device records; provisioning writes go through a different path.
"""
import requests


class RegistryError(Exception):
    """Raised when the registry returns an unusable response."""

    def __init__(self, status_code, message):
        super().__init__(f"registry returned {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class RegistryClient:
    """Thin wrapper over the registry's JSON-over-HTTP API."""

    def __init__(self, base_url, session=None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()

    def get_device(self, device_id):
        """Fetch one device record by id."""
        resp = self.session.get(
            f"{self.base_url}/devices/{device_id}", timeout=10)
        if resp.status_code == 200:
            return resp.json()
        raise RegistryError(resp.status_code, resp.text)
