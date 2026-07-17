"""Acceptance harness for the GitLab job-artifact downloader.

Runs two loopback HTTP servers: a fake GitLab REST v4 API origin and a
separate object-storage origin, pinning the wire contract recorded in
docs/contract.json. No real GitLab, no credentials, no network, no sleeps.
Protected -- do not modify. Run: python3 test_job_artifact.py
"""

import hashlib
import io
import json
import os
import shutil
import threading
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from glartifacts.client import (
    GitLabClient,
    GitLabAPIError,
    NotFoundMaskedError,
    TruncatedDownloadError,
)
from glartifacts.downloader import (
    ArtifactDownloader,
    ArtifactLookupError,
    ArtifactsExpiredError,
)

TOKEN = "glpat-dummy-t0ken-9184x"  # dummy credential, never real
PROJECT = 9184
OUT_DIR = "artifact_out"

FROZEN_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


def build_zip():
    """Deterministic artifact archive bytes (fixed zip timestamps)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        info = zipfile.ZipInfo("report/bundle-sizes.json", (2026, 7, 10, 8, 0, 0))
        zf.writestr(info, json.dumps({"main.js": 48123, "vendor.js": 190882}))
        info = zipfile.ZipInfo("report/warnings.txt", (2026, 7, 10, 8, 0, 0))
        zf.writestr(info, "2 chunks exceed the recommended size budget\n")
    return buf.getvalue()


ZIP_BYTES = build_zip()
ZIP_SHA = hashlib.sha256(ZIP_BYTES).hexdigest()


class Mock:
    """One loopback origin: scripted routes plus a full request log."""

    def __init__(self, name):
        self.name = name
        self.requests = []
        self.routes = {}
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                parts = urlsplit(self.path)
                headers = {k.lower(): v for k, v in self.headers.items()}
                mock.requests.append(
                    {
                        "method": "GET",
                        "path": parts.path,
                        "query": {k: v[0] for k, v in parse_qs(parts.query).items()},
                        "headers": headers,
                    }
                )
                route = mock.routes.get(parts.path)
                if route is None:
                    body = json.dumps({"message": "404 Not Found"}).encode()
                    self._reply(404, {"Content-Type": "application/json"}, body)
                    return
                route(self)

            def _reply(self, status, headers, body, content_length=None):
                self.send_response(status)
                length = len(body) if content_length is None else content_length
                self.send_header("Content-Length", str(length))
                for key, value in headers.items():
                    self.send_header(key, value)
                if content_length is not None:
                    # Deliberately truncated payload: advertise more bytes
                    # than are sent, then drop the connection.
                    self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                if content_length is not None:
                    self.wfile.flush()
                    self.connection.close()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def json_route(self, path, status, payload, extra_headers=None):
        def route(handler):
            body = json.dumps(payload).encode()
            headers = {"Content-Type": "application/json"}
            headers.update(extra_headers or {})
            handler._reply(status, headers, body)

        self.routes[path] = route

    def custom_route(self, path, fn):
        self.routes[path] = fn

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()


def pipeline(pid, status, sha):
    return {
        "id": pid,
        "iid": pid - 8700,
        "project_id": PROJECT,
        "status": status,
        "source": "push",
        "ref": "main",
        "sha": sha,
        "web_url": "https://gitlab.example.com/acme/bundler/-/pipelines/%d" % pid,
    }


def job(jid, name, status, expire_at, with_artifacts=True):
    entry = {
        "id": jid,
        "name": name,
        "stage": "report",
        "status": status,
        "ref": "main",
        "allow_failure": False,
        "artifacts_expire_at": expire_at,
        "web_url": "https://gitlab.example.com/acme/bundler/-/jobs/%d" % jid,
    }
    entry["artifacts"] = (
        [
            {
                "file_type": "archive",
                "size": len(ZIP_BYTES),
                "filename": "artifacts.zip",
                "file_format": "zip",
            }
        ]
        if with_artifacts
        else []
    )
    return entry


def wire_happy_path(api, storage, expire_at="2026-08-01T00:00:00.000Z"):
    api.json_route(
        "/api/v4/projects/%d/pipelines" % PROJECT,
        200,
        [
            pipeline(8812, "success", "e83c5163316f89bfbde7d9ab23ca2e25604af290"),
            pipeline(8790, "success", "62eb1655bd9e551a1b0adbbc7462fa4372dbb15e"),
        ],
    )
    page_one = [
        job(501, "assemble", "success", None),
        job(502, "unit-suite", "failed", None, with_artifacts=False),
    ]
    page_two = [job(503, "bundle-report", "success", expire_at)]

    def jobs_route(handler):
        query = {
            k: v[0]
            for k, v in parse_qs(urlsplit(handler.path).query).items()
        }
        page = int(query.get("page", "1"))
        payload = page_one if page == 1 else page_two
        body = json.dumps(payload).encode()
        headers = {
            "Content-Type": "application/json",
            "x-page": str(page),
            "x-per-page": query.get("per_page", "20"),
            "x-total": "3",
            "x-total-pages": "2",
            "x-next-page": "2" if page == 1 else "",
            "x-prev-page": "1" if page == 2 else "",
        }
        handler._reply(200, headers, body)

    api.custom_route("/api/v4/projects/%d/pipelines/8812/jobs" % PROJECT, jobs_route)

    def artifacts_route(handler):
        handler._reply(
            302,
            {
                "Content-Type": "text/plain",
                "Location": "/object-store/prep/503",
            },
            b"",
        )

    api.custom_route("/api/v4/projects/%d/jobs/503/artifacts" % PROJECT, artifacts_route)

    def object_store_hop(handler):
        handler._reply(
            302,
            {
                "Content-Type": "text/plain",
                "Location": storage.base + "/blobs/ab/503/artifacts.zip",
            },
            b"",
        )

    api.custom_route("/object-store/prep/503", object_store_hop)

    def blob_route(handler):
        handler._reply(200, {"Content-Type": "application/zip"}, ZIP_BYTES)

    storage.custom_route("/blobs/ab/503/artifacts.zip", blob_route)


def fresh_downloader(api):
    client = GitLabClient(api.base, TOKEN)
    return ArtifactDownloader(client, now=lambda: FROZEN_NOW)


def requests_for(mock, path_prefix):
    return [r for r in mock.requests if r["path"].startswith(path_prefix)]


def test_happy_path(api, storage):
    wire_happy_path(api, storage)
    downloader = fresh_downloader(api)
    dest = os.path.join(OUT_DIR, "bundle-report.zip")
    report = downloader.fetch_latest(PROJECT, "bundle-report", dest)

    pipeline_calls = requests_for(api, "/api/v4/projects/%d/pipelines" % PROJECT)
    listing = pipeline_calls[0]
    assert listing["path"] == "/api/v4/projects/%d/pipelines" % PROJECT, listing
    assert listing["query"].get("status") == "success", listing["query"]
    assert listing["query"].get("order_by") == "id", listing["query"]
    assert listing["query"].get("sort") == "desc", listing["query"]
    assert listing["headers"].get("private-token") == TOKEN, listing["headers"]

    job_calls = requests_for(api, "/api/v4/projects/%d/pipelines/8812/jobs" % PROJECT)
    assert len(job_calls) == 2, [c["query"] for c in job_calls]
    assert job_calls[0]["query"].get("per_page") == "100", job_calls[0]["query"]
    assert job_calls[1]["query"].get("page") == "2", job_calls[1]["query"]
    assert job_calls[1]["query"].get("per_page") == "100", job_calls[1]["query"]
    for call in job_calls:
        assert call["headers"].get("private-token") == TOKEN

    download_calls = requests_for(api, "/api/v4/projects/%d/jobs/503/artifacts" % PROJECT)
    assert len(download_calls) == 1, download_calls
    assert download_calls[0]["headers"].get("private-token") == TOKEN

    # Same-origin redirect hop keeps the token.
    hop_calls = requests_for(api, "/object-store/prep/503")
    assert len(hop_calls) == 1, hop_calls
    assert hop_calls[0]["headers"].get("private-token") == TOKEN, hop_calls[0]["headers"]

    # Cross-origin storage hop must NOT see any credential.
    blob_calls = requests_for(storage, "/blobs/ab/503/artifacts.zip")
    assert len(blob_calls) == 1, storage.requests
    assert "private-token" not in blob_calls[0]["headers"], blob_calls[0]["headers"]
    assert "authorization" not in blob_calls[0]["headers"], blob_calls[0]["headers"]

    assert report["project_id"] == PROJECT, report
    assert report["pipeline_id"] == 8812, report
    assert report["job_id"] == 503, report
    assert report["size"] == len(ZIP_BYTES), report
    assert report["sha256"] == ZIP_SHA, report
    assert report["path"] == dest, report
    with open(dest, "rb") as fh:
        assert fh.read() == ZIP_BYTES
    with zipfile.ZipFile(dest) as zf:
        assert sorted(zf.namelist()) == [
            "report/bundle-sizes.json",
            "report/warnings.txt",
        ]


def test_null_expiry_is_not_expired(api, storage):
    wire_happy_path(api, storage, expire_at=None)
    downloader = fresh_downloader(api)
    dest = os.path.join(OUT_DIR, "keep-forever.zip")
    report = downloader.fetch_latest(PROJECT, "bundle-report", dest)
    assert report["sha256"] == ZIP_SHA, report
    assert os.path.exists(dest)


def test_expired_artifacts(api, storage):
    wire_happy_path(api, storage, expire_at="2026-07-16T11:59:00.000Z")
    downloader = fresh_downloader(api)
    dest = os.path.join(OUT_DIR, "expired.zip")
    try:
        downloader.fetch_latest(PROJECT, "bundle-report", dest)
    except ArtifactsExpiredError as exc:
        text = str(exc)
        assert "2026-07-16T11:59" in text, text
        assert "expire" in text.lower(), text
        assert TOKEN not in text, text
    else:
        raise AssertionError("expired artifacts must raise ArtifactsExpiredError")
    # Expiry is decided from job metadata; no download attempt may be made.
    assert not requests_for(api, "/api/v4/projects/%d/jobs/503/artifacts" % PROJECT)
    assert not os.path.exists(dest)


def test_permission_masked_404(api, storage):
    api.json_route(
        "/api/v4/projects/%d/pipelines" % PROJECT,
        404,
        {"message": "404 Project Not Found"},
    )
    downloader = fresh_downloader(api)
    try:
        downloader.fetch_latest(PROJECT, "bundle-report", os.path.join(OUT_DIR, "x.zip"))
    except NotFoundMaskedError as exc:
        assert exc.status == 404, exc.status
        text = str(exc)
        assert "404 Project Not Found" in text, text
        assert "permission" in text.lower(), text
        assert TOKEN not in text, text
    except ArtifactsExpiredError:
        raise AssertionError("a 404 project must not be reported as expiry")
    else:
        raise AssertionError("masked 404 must raise NotFoundMaskedError")


def test_no_successful_pipeline(api, storage):
    api.json_route("/api/v4/projects/%d/pipelines" % PROJECT, 200, [])
    downloader = fresh_downloader(api)
    try:
        downloader.fetch_latest(PROJECT, "bundle-report", os.path.join(OUT_DIR, "x.zip"))
    except ArtifactLookupError as exc:
        assert str(PROJECT) in str(exc), exc
    else:
        raise AssertionError("no successful pipeline must raise ArtifactLookupError")


def test_job_not_in_pipeline(api, storage):
    wire_happy_path(api, storage)
    downloader = fresh_downloader(api)
    try:
        downloader.fetch_latest(PROJECT, "nightly-fuzz", os.path.join(OUT_DIR, "x.zip"))
    except ArtifactLookupError as exc:
        text = str(exc)
        assert "nightly-fuzz" in text, text
        assert "8812" in text, text
    else:
        raise AssertionError("missing job name must raise ArtifactLookupError")
    # The failed unit-suite job must never be selected for download.
    assert not requests_for(api, "/api/v4/projects/%d/jobs/502/artifacts" % PROJECT)


def test_truncated_download_removes_partial_file(api, storage):
    wire_happy_path(api, storage)

    def truncated_blob(handler):
        handler._reply(
            200,
            {"Content-Type": "application/zip"},
            ZIP_BYTES[: len(ZIP_BYTES) // 2],
            content_length=len(ZIP_BYTES),
        )

    storage.custom_route("/blobs/ab/503/artifacts.zip", truncated_blob)
    downloader = fresh_downloader(api)
    dest = os.path.join(OUT_DIR, "truncated.zip")
    try:
        downloader.fetch_latest(PROJECT, "bundle-report", dest)
    except TruncatedDownloadError:
        pass
    else:
        raise AssertionError("short body vs Content-Length must raise TruncatedDownloadError")
    assert not os.path.exists(dest), "partial download must be removed"


def test_redirect_loop_is_bounded(api, storage):
    wire_happy_path(api, storage)

    def looping(handler):
        handler._reply(
            302,
            {"Content-Type": "text/plain", "Location": "/object-store/prep/503"},
            b"",
        )

    api.custom_route("/object-store/prep/503", looping)
    downloader = fresh_downloader(api)
    try:
        downloader.fetch_latest(PROJECT, "bundle-report", os.path.join(OUT_DIR, "loop.zip"))
    except GitLabAPIError as exc:
        assert "redirect" in str(exc).lower(), exc
    else:
        raise AssertionError("a redirect loop must raise GitLabAPIError")
    hops = requests_for(api, "/object-store/prep/503")
    assert len(hops) <= 10, "redirect following must be bounded, saw %d hops" % len(hops)


def run(name, fn):
    api = Mock("api")
    storage = Mock("storage")
    try:
        fn(api, storage)
    finally:
        api.shutdown()
        storage.shutdown()
    print("ok - %s" % name)


def main():
    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR)
    tests = [
        test_happy_path,
        test_null_expiry_is_not_expired,
        test_expired_artifacts,
        test_permission_masked_404,
        test_no_successful_pipeline,
        test_job_not_in_pipeline,
        test_truncated_download_removes_partial_file,
        test_redirect_loop_is_bounded,
    ]
    for fn in tests:
        run(fn.__name__, fn)
    print("all %d scenarios passed" % len(tests))


if __name__ == "__main__":
    main()
