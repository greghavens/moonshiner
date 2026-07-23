"""One-time local bootstrap and optional remote check for append-only HF data."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from common import CONFIG, DATA, RUNS


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _headers() -> dict:
    headers = {"User-Agent": "moonshiner/0.2"}
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        from common import key_file_path, key_persist_path
        runtime = {"provider": "huggingface", "key_env": "HF_TOKEN",
                   "key_file_name": "moonshiner-huggingface-key"}
        for path in (key_file_path(runtime), key_persist_path(runtime)):
            if path.is_file() and path.read_text().strip():
                token = path.read_text().strip()
                break
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _dataset_info(dataset: str) -> dict | None:
    url = "https://huggingface.co/api/datasets/" + urllib.parse.quote(dataset, safe="/")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=_headers()),
                                    timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def dataset_has_file(dataset: str, filename: str = "traces.jsonl") -> bool:
    info = _dataset_info(dataset)
    return bool(info and filename in {
        item.get("rfilename") for item in info.get("siblings", [])})


def _remote_file_url(dataset: str, revision: str, filename: str) -> str:
    return ("https://huggingface.co/datasets/" + urllib.parse.quote(dataset, safe="/")
            + "/resolve/" + urllib.parse.quote(revision, safe="") + "/"
            + urllib.parse.quote(filename, safe="/") + "?download=true")


def _download(dataset: str, revision: str, filename: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(_remote_file_url(dataset, revision, filename),
                                     headers=_headers())
    with urllib.request.urlopen(request, timeout=120) as response, \
            destination.open("xb") as output:
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            output.write(block)


def _backfill_baseline(state: dict, marker: Path) -> dict:
    """Recover baseline dimensions from the retained first download."""
    if state.get("bootstrap_size") is not None and state.get("bootstrap_rows") is not None:
        return state
    expected=state.get("bootstrap_sha256")
    for candidate in sorted((RUNS/"hf-bootstrap-downloads").glob("*-traces.jsonl")):
        if sha256(candidate) != expected:
            continue
        with candidate.open() as handle:
            rows = sum(1 for _ in handle)
        state={**state,"bootstrap_size":candidate.stat().st_size,
               "bootstrap_rows":rows}
        marker.write_text(json.dumps(state,indent=2,sort_keys=True)+"\n")
        return state
    raise RuntimeError("cannot prove retained HF append baseline")


def ensure_local_dataset(*, check_remote: bool | None = None,
                         target: Path | None = None,
                         dataset: str | None = None,
                         filename: str | None = None) -> dict:
    """Bootstrap the canonical file once; thereafter trust and append locally."""
    publish = CONFIG.get("publish") or {}
    dataset = dataset or publish.get("hf_dataset")
    filename = str(filename or publish.get("filename") or "traces.jsonl")
    target = target or DATA / "hf-publish" / filename
    marker_name = hashlib.sha256(f"{dataset}:{filename}".encode()).hexdigest()[:16]
    marker = DATA / "hf-sync" / f"{marker_name}.json"
    check_remote = (bool(publish.get("check_before_append", False))
                    if check_remote is None else check_remote)
    if not dataset:
        return {"status": "unconfigured", "path": str(target)}

    if marker.exists() and not check_remote:
        state = _backfill_baseline(json.loads(marker.read_text()), marker)
        if state.get("dataset") != dataset or state.get("filename") != filename:
            raise RuntimeError("local HF bootstrap belongs to a different configured target")
        if not target.exists():
            raise RuntimeError("HF bootstrap marker exists but canonical local file is missing")
        return {**state, "status": "local_append"}

    info = _dataset_info(dataset)
    revision = (info or {}).get("sha")
    remote_files = {item.get("rfilename") for item in (info or {}).get("siblings", [])}
    remote_exists = filename in remote_files

    if marker.exists():
        state = json.loads(marker.read_text())
        if state.get("dataset") != dataset or state.get("filename") != filename:
            raise RuntimeError("local HF bootstrap belongs to a different configured target")
        if check_remote and revision != state.get("remote_revision"):
            raise RuntimeError(
                "configured pre-append check found a newer HF revision; "
                "explicitly resynchronize before appending")
        return {**state, "status": "remote_checked"}

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        # Existing local data wins. We never replace or truncate it. Record its
        # current hash as the append baseline; an explicit check can be enabled.
        origin = "existing_local"
    elif remote_exists:
        download_dir = RUNS / "hf-bootstrap-downloads"
        download_dir.mkdir(parents=True, exist_ok=True)
        retained_download = download_dir / f"{uuid.uuid4().hex}-{Path(filename).name}"
        _download(dataset, revision, filename, retained_download)
        with retained_download.open("rb") as source, target.open("xb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
        origin = "downloaded_remote"
    else:
        target.touch(exist_ok=False)
        origin = "new_local"

    with target.open() as handle:
        bootstrap_rows = sum(1 for _ in handle)
    state = {
        "schema_version": 1,
        "dataset": dataset,
        "filename": filename,
        "path": str(target),
        "origin": origin,
        "remote_revision": revision,
        "remote_file_existed": remote_exists,
        "bootstrap_sha256": sha256(target),
        "bootstrap_size": target.stat().st_size,
        "bootstrap_rows": bootstrap_rows,
        "bootstrapped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    return {**state, "status": "bootstrapped"}
