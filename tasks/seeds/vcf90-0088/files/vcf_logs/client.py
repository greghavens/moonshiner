"""VCF Operations for Logs client implementation."""


class VCFLogsClient:
    """Client for the contract documented in ``docs/contract.json``."""

    def __init__(self, base_url, username, password, provider="Local"):
        self.base_url = base_url
        self.username = username
        self.password = password
        self.provider = provider

    def authenticate(self):
        raise NotImplementedError("authenticate is not implemented")

    def list_events(
        self,
        *,
        since_timestamp,
        page_size=100,
        timeout=None,
        view=None,
        content_pack_fields=None,
    ):
        raise NotImplementedError("list_events is not implemented")
