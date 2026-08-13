"""Certificate rotation flow against VCF Operations for Networks 9.1.

Implement :meth:`CertificateRotationClient.rotate` here. The four operations that
may be called, their methods, paths, request shapes and success statuses are
pinned in ``docs/contract.json``; nothing outside that contract may be requested.

Standard library only.
"""

from typing import Optional

from .model import ApiError, PollTimeoutError, RotationOutcome

__all__ = ["CertificateRotationClient"]


class CertificateRotationClient:
    """Replaces an appliance certificate and follows the update to a terminal state.

    ``base_url`` is the appliance root, for example ``https://vcfonw.example.com``.
    The contract's ``source.server_base_path`` is appended to it to reach the API.
    ``timeout`` is the per-request socket timeout in seconds.
    """

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def rotate(
        self,
        *,
        username: str,
        password: str,
        certificate_id: str,
        certificate_pem: str,
        private_key_pem: str,
        domain_type: Optional[str] = None,
        domain_value: Optional[str] = None,
        chain_pem: Optional[str] = None,
        poll_interval: float = 5.0,
        poll_timeout: float = 300.0,
    ) -> RotationOutcome:
        """Authenticate, submit the certificate update, follow it, drop the token.

        Returns a :class:`RotationOutcome` once the update reaches a terminal
        state. Raises :class:`ApiError` for a non-success HTTP status and
        :class:`PollTimeoutError` if no terminal state is observed within
        ``poll_timeout`` seconds.
        """
        raise NotImplementedError("rotate() is not implemented yet")
