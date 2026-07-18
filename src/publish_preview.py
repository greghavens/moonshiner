#!/usr/bin/env python3
"""Publish attested traces to the Hugging Face dataset as they finish.

The full pipeline still owns the final, screened release (`export-next` →
`verify-export` → `card`). This preview publisher closes the gap while a long
generate phase runs: every completed, model-attested trace is normalized,
scrubbed, secret-scanned, and pushed to the configured dataset immediately, so
the repo always shows the real corpus-in-progress. The final export overwrites
the preview file wholesale at release time.

One-shot by default; `--watch` re-publishes whenever new attested traces land.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from common import CONFIG, DATA, ROOT, _staged_secret_values
from runtimes.pi import PiRuntime
from validate_hf_export import FORBIDDEN_SUBSTRINGS

PREVIEW_DIR = DATA / "hf-preview"
PREVIEW_FILE = PREVIEW_DIR / "traces.jsonl"
STATE_FILE = PREVIEW_DIR / "state.json"
META_DIR = ROOT / "traces" / "meta"
RAW_DIR = ROOT / "traces" / "raw"


def _attested_rows() -> list[dict]:
    teacher = CONFIG.get("teacher", {})
    pi_cfg = CONFIG.get("runtimes", {}).get("pi", {})
    rows = []
    for meta_path in sorted(META_DIR.glob("*.json")):
        meta = json.loads(meta_path.read_text())
        attested = meta.get("teacher", {})
        if attested.get("model_attested") is not True:
            continue
        raw = RAW_DIR / f"{meta_path.stem}.events.jsonl"
        if not raw.exists():
            continue
        workspace = str((ROOT / "workspaces" / meta_path.stem).resolve())
        messages, _stats = PiRuntime.parse_stream(raw, workspace)
        if not messages:
            continue
        rows.append({
            "task": meta_path.stem,
            "teacher_runtime": teacher.get("runtime"),
            "teacher_model": attested.get("model"),
            "provider": pi_cfg.get("display_provider") or pi_cfg.get("provider"),
            "reasoning_effort": teacher.get("reasoning"),
            "model_attested": True,
            "observed_models": [attested.get("observed_model")],
            "trace_format": "pi-coding-agent-json-v3",
            "n_messages": len(messages),
            "messages": messages,
        })
    return rows


def _secret_scan(text: str) -> None:
    hits = [s for s in FORBIDDEN_SUBSTRINGS if s in text]
    if any(value and value in text for value in _staged_secret_values()):
        hits.append("<staged provider key value>")
    if str(ROOT) in text:
        hits.append(str(ROOT))
    if hits:
        raise SystemExit(f"preview blocked, secrets/host paths present: {hits}")


def publish_once() -> int:
    dataset = CONFIG.get("publish", {}).get("hf_dataset")
    if not dataset:
        raise SystemExit("config.publish.hf_dataset is not set")
    rows = _attested_rows()
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    published = state.get("published_tasks", [])
    current = [row["task"] for row in rows]
    if current == published:
        return len(rows)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    _secret_scan(text)
    PREVIEW_FILE.write_text(text)
    subprocess.run(["hf", "upload", dataset, str(PREVIEW_FILE), "traces.jsonl",
                    "--repo-type", "dataset"],
                   check=True, capture_output=True, text=True)
    STATE_FILE.write_text(json.dumps({"published_tasks": current}, indent=2))
    print(f"published {len(rows)} attested traces -> {dataset}", flush=True)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true",
                        help="keep publishing as new attested traces land")
    parser.add_argument("--interval", type=int, default=180,
                        help="seconds between checks in --watch mode")
    args = parser.parse_args()
    count = publish_once()
    print(f"preview holds {count} traces", flush=True)
    while args.watch:
        time.sleep(args.interval)
        try:
            publish_once()
        except subprocess.CalledProcessError as exc:
            print(f"upload failed, will retry: {exc.stderr[-300:]}", flush=True)


if __name__ == "__main__":
    main()
