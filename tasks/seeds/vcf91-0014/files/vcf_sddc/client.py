"""Resilient VCF 9.1 SDDC Manager inventory client."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_NO_BODY = object()


class SddcManagerError(RuntimeError):
    """Raised when SDDC Manager traffic or response metadata is invalid."""

    def __init__(
        self,
        message: str,
        *,
        operation_id: str | None = None,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.operation_id = operation_id
        self.status = status


class SddcManagerClient:
    """Client for the four operations named by the protected contract."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self._access_token: str | None = None
        self._refresh_token_id: str | None = None
        self._has_refreshed = False

    def collect_inventory(
        self,
        *,
        page_size: int = 2,
        domain_type: str | None = None,
        host_status: str | None = None,
    ) -> dict[str, list[dict[str, object]]]:
        """Return complete domain and host collections."""

        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or page_size <= 0
        ):
            raise ValueError("page_size must be a positive integer")

        self._ensure_authenticated()
        domains = self._collect_pages(
            operation_id="getDomains",
            path="/v1/domains",
            page_size=page_size,
            filter_name="type",
            filter_value=domain_type,
        )
        hosts = self._collect_pages(
            operation_id="getHosts",
            path="/v1/hosts",
            page_size=page_size,
            filter_name="status",
            filter_value=host_status,
        )
        return {"domains": domains, "hosts": hosts}

    def _ensure_authenticated(self) -> None:
        if self._access_token is not None:
            return
        pair = self._request_json(
            operation_id="createToken",
            method="POST",
            path="/v1/tokens",
            body={
                "username": self.username,
                "password": self.password,
            },
            authenticated=False,
        )
        if not isinstance(pair, dict):
            raise SddcManagerError(
                "createToken returned a non-object response",
                operation_id="createToken",
            )
        access_token = pair.get("accessToken")
        refresh_token = pair.get("refreshToken")
        refresh_id = (
            refresh_token.get("id")
            if isinstance(refresh_token, dict)
            else None
        )
        if (
            not isinstance(access_token, str)
            or not access_token
            or not isinstance(refresh_id, str)
            or not refresh_id
        ):
            raise SddcManagerError(
                "createToken response is missing accessToken or refreshToken.id",
                operation_id="createToken",
            )
        self._access_token = access_token
        self._refresh_token_id = refresh_id

    def _refresh_access_token(self) -> None:
        if self._refresh_token_id is None:
            raise SddcManagerError(
                "cannot refresh without refreshToken.id",
                operation_id="refreshAccessToken",
            )
        access_token = self._request_json(
            operation_id="refreshAccessToken",
            method="PATCH",
            path="/v1/tokens/access-token/refresh",
            body=self._refresh_token_id,
            authenticated=False,
        )
        if not isinstance(access_token, str) or not access_token:
            raise SddcManagerError(
                "refreshAccessToken returned a non-string access token",
                operation_id="refreshAccessToken",
            )
        self._access_token = access_token
        self._has_refreshed = True

    def _collect_pages(
        self,
        *,
        operation_id: str,
        path: str,
        page_size: int,
        filter_name: str,
        filter_value: str | None,
    ) -> list[dict[str, object]]:
        collected: list[dict[str, object]] = []
        requested_page: int | None = None
        expected_total: int | None = None

        while True:
            # TODO: optional query values cannot be represented as empty
            # strings. Build the request from only the values that are set.
            query: list[tuple[str, str]] = [
                ("pageSize", str(page_size)),
                (
                    "pageNumber",
                    "" if requested_page is None else str(requested_page),
                ),
                (filter_name, "" if filter_value is None else filter_value),
            ]

            page = self._request_json(
                operation_id=operation_id,
                method="GET",
                path=path,
                query=query,
                authenticated=True,
            )
            if not isinstance(page, dict):
                self._metadata_error(
                    operation_id, "response is not an object"
                )
            elements = page.get("elements")
            metadata = page.get("pageMetadata")
            if not isinstance(elements, list) or not isinstance(metadata, dict):
                self._metadata_error(
                    operation_id,
                    "response must contain list elements and object pageMetadata",
                )

            current_page = metadata.get("pageNumber")
            total_elements = metadata.get("totalElements")
            if (
                isinstance(current_page, bool)
                or not isinstance(current_page, int)
                or current_page < 0
                or isinstance(total_elements, bool)
                or not isinstance(total_elements, int)
                or total_elements < 0
            ):
                self._metadata_error(
                    operation_id,
                    "pageNumber and totalElements must be non-negative integers",
                )
            if requested_page is not None and current_page != requested_page:
                self._metadata_error(
                    operation_id,
                    f"requested page {requested_page} but received {current_page}",
                )
            if expected_total is None:
                expected_total = total_elements
            elif total_elements != expected_total:
                self._metadata_error(
                    operation_id,
                    f"totalElements changed from {expected_total} "
                    f"to {total_elements}",
                )
            if any(not isinstance(element, dict) for element in elements):
                self._metadata_error(
                    operation_id, "elements must contain only objects"
                )
            if len(collected) + len(elements) > expected_total:
                self._metadata_error(
                    operation_id, "elements overshoot totalElements"
                )

            collected.extend(elements)
            if len(collected) == expected_total:
                return collected
            if not elements:
                self._metadata_error(
                    operation_id,
                    "page made no progress before totalElements was reached",
                )
            requested_page = current_page + 1

    @staticmethod
    def _metadata_error(operation_id: str, detail: str) -> None:
        raise SddcManagerError(
            f"{operation_id} returned invalid pagination metadata: {detail}",
            operation_id=operation_id,
        )

    def _request_json(
        self,
        *,
        operation_id: str,
        method: str,
        path: str,
        query: list[tuple[str, str]] | None = None,
        body: Any = _NO_BODY,
        authenticated: bool,
        allow_refresh: bool = True,
    ) -> Any:
        target = self.base_url + path
        if query:
            target += "?" + urlencode(query)

        headers = {"Accept": "application/json"}
        data: bytes | None = None
        if body is not _NO_BODY:
            data = json.dumps(
                body, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authenticated:
            if self._access_token is None:
                raise SddcManagerError(
                    "authenticated request has no access token",
                    operation_id=operation_id,
                )
            headers["Authorization"] = f"Bearer {self._access_token}"

        request = Request(
            target,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read()
            if (
                exc.code == 401
                and authenticated
                and allow_refresh
                and not self._has_refreshed
            ):
                self._refresh_access_token()
                # TODO: restarting loses successful pages and repeats work.
                # Retry only the request that received the 401.
                return self.collect_inventory()
            detail = raw.decode("utf-8", errors="replace")
            raise SddcManagerError(
                f"{operation_id} returned HTTP {exc.code}: {detail}",
                operation_id=operation_id,
                status=exc.code,
            ) from exc
        except URLError as exc:
            raise SddcManagerError(
                f"{operation_id} request failed: {exc.reason}",
                operation_id=operation_id,
            ) from exc

        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SddcManagerError(
                f"{operation_id} returned invalid JSON",
                operation_id=operation_id,
            ) from exc
