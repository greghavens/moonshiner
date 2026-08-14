"""vSAN Data Protection report client.

The public surface is present so callers can integrate it while the report
pagination implementation is completed.
"""


class VsanDataProtectionError(RuntimeError):
    """Raised when the report service returns an unusable response."""


class VsanDataProtectionClient:
    """Client for the VCF vSAN Data Protection report endpoint."""

    def __init__(self, base_url, session_id, timeout=10.0):
        self.base_url = base_url
        self.session_id = session_id
        self.timeout = timeout

    def list_protection_group_snapshots(
        self,
        cluster,
        *,
        page_size=None,
        pgs=None,
        start_time=None,
        end_time=None,
    ):
        """Return every protection-group snapshot report record."""
        raise NotImplementedError("protection-group snapshot pagination is not implemented")
