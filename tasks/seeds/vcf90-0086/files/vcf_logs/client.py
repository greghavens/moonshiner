"""VCF Operations for Logs 9.0 client."""


class VCFLogsAPIError(RuntimeError):
    """Raised when the API returns a non-success response."""


class VCFLogsClient:
    """Client for the asynchronous cluster-join workflow."""

    def __init__(self, base_url: str, *, poll_interval: float = 1.0) -> None:
        self.base_url = base_url
        self.poll_interval = poll_interval

    def join_cluster_and_wait(
        self,
        master_fqdn: str,
        *,
        master_port: int | None = None,
        accept_cert: bool | None = None,
    ) -> dict:
        """Start a cluster join and wait for the server to start."""
        raise NotImplementedError
