"""Thread-safe VCF Log Management agent-secret rotation.

Complete the TODO methods without adding non-stdlib dependencies.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


_MAX_RESPONSE_BYTES = 64 * 1024
_MIN_TTL_MS = 60_000
_MAX_TTL_MS = 15_552_000_000


class LogManagementError(Exception):
    """Base error for the focused client."""


class ApiError(LogManagementError):
    """The service returned a non-success status."""


class ProtocolError(LogManagementError):
    """The service returned a malformed success response."""


@dataclass(frozen=True, slots=True)
class AgentSession:
    access_token: str
    name: str
    new_secret: str
    ttl: int


@dataclass(slots=True)
class _SecretLease:
    name: str
    secret: str
    active_exchanges: int = 0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


class RotatingAgentSessionClient:
    """Create sessions while safely rotating their backing agent secret."""

    def __init__(
        self, base_url: str, ops_token: str, *, timeout: float = 5.0
    ) -> None:
        self._base_url = _validate_origin(base_url)
        self._ops_token = _required_text(ops_token, "ops_token")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("timeout must be a positive number")
        if timeout <= 0:
            raise ValueError("timeout must be a positive number")
        self._timeout = float(timeout)
        self._condition = threading.Condition()
        self._rotation_lock = threading.Lock()
        self._current: _SecretLease | None = None
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )

    @property
    def current_secret_name(self) -> str | None:
        with self._condition:
            return None if self._current is None else self._current.name

    def bootstrap(self, secret_name: str) -> None:
        """Create and install the first managed secret."""
        raise NotImplementedError("TODO: create and install the initial lease")

    def create_session(self, *, ttl_ms: int | None = None) -> AgentSession:
        """Exchange the currently leased secret for an agent session."""
        raise NotImplementedError("TODO: pin, exchange, and release a lease")

    def rotate(self, new_secret_name: str) -> None:
        """Publish a replacement, drain the old lease, then revoke it."""
        raise NotImplementedError("TODO: publish, drain, and revoke")

    def _create_secret(self, secret_name: str) -> _SecretLease:
        raise NotImplementedError("TODO: invoke createAgentSecret")

    def _exchange(
        self, lease: _SecretLease, ttl_ms: int | None
    ) -> AgentSession:
        raise NotImplementedError("TODO: invoke createAgentSession")

    def _revoke(self, secret_name: str) -> None:
        raise NotImplementedError("TODO: invoke revokeAgentSecret")

    def _post_json(
        self,
        operation: str,
        path: str,
        expected_status: int,
        body: dict[str, object] | None,
    ) -> dict[str, object]:
        raise NotImplementedError("TODO: issue and validate a bounded request")


def _validate_origin(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("base_url must be an absolute HTTP(S) origin")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("base_url must be an absolute HTTP(S) origin") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("base_url must be an absolute HTTP(S) origin")
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    authority = host if port is None else f"{host}:{port}"
    return f"{parsed.scheme}://{authority}"


def _required_text(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\r" in value
        or "\n" in value
    ):
        raise ValueError(f"{field} must be a nonblank single-line string")
    return value


def _validate_ttl(value: int | None) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("ttl_ms must be an integer or None")
    if value != 0 and not (_MIN_TTL_MS <= value <= _MAX_TTL_MS):
        raise ValueError("ttl_ms is outside the documented range")


def _segment(value: str) -> str:
    return urllib.parse.quote(value, safe="-._~", encoding="utf-8", errors="strict")


def _compact_json(value: dict[str, object]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def _json_object(body: bytes, operation: str) -> dict[str, object]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError(f"{operation} returned malformed JSON") from error
    if not isinstance(value, dict):
        raise ProtocolError(f"{operation} returned the wrong JSON shape")
    return value


def _response_text(value: object, field: str, operation: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{operation} response is missing {field}")
    return value


def _response_int(value: object, field: str, operation: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{operation} response is missing {field}")
    return value
