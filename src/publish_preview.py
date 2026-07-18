#!/usr/bin/env python3
"""Publish verified, attested traces to the Hugging Face dataset as they finish.

The full pipeline still owns the final, screened release (`export-next` →
`verify-export` → `card`). This preview publisher closes the gap while a long
generate phase runs: every completed trace that PASSED verification (acceptance
tests + protected-file hashes) and is model-attested is normalized, scrubbed,
secret-scanned, and pushed to the configured dataset immediately — together
with the dataset card rendered from the house template
(`export_hf_card.build_card`, preview stage) — so the repo always shows the
real corpus-in-progress. Repo visibility is synced from `config.publish`. The
final export overwrites the preview wholesale at release time.

Everything that reaches the Hub goes through this program; nothing is
hand-written or hand-uploaded.

One-shot by default; `--watch` re-publishes whenever new traces land.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from common import CONFIG, DATA, ROOT, _staged_secret_values
from export_hf_card import build_card
from runtimes.pi import PiRuntime
from validate_hf_export import FORBIDDEN_SUBSTRINGS

PREVIEW_DIR = DATA / "hf-preview"
PREVIEW_FILE = PREVIEW_DIR / "traces.jsonl"
PREVIEW_CARD = PREVIEW_DIR / "README.md"
STATE_FILE = PREVIEW_DIR / "state.json"
META_DIR = ROOT / "traces" / "meta"
RAW_DIR = ROOT / "traces" / "raw"


def _publishable_rows() -> list[dict]:
    teacher = CONFIG.get("teacher", {})
    pi_cfg = CONFIG.get("runtimes", {}).get("pi", {})
    rows = []
    for meta_path in sorted(META_DIR.glob("*.json")):
        meta = json.loads(meta_path.read_text())
        attested = meta.get("teacher", {})
        if meta.get("passed") is not True:
            continue
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
            "lang": meta.get("lang"),
            "category": meta.get("category"),
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


def _hf_token() -> str:
    token = os.environ.get("HF_TOKEN", "").strip()
    if token:
        return token
    hf_home = Path(os.environ.get("HF_HOME",
                                  Path.home() / ".cache" / "huggingface"))
    for candidate in (hf_home / "token", Path.home() / ".huggingface" / "token"):
        if candidate.exists():
            return candidate.read_text().strip()
    raise RuntimeError("no Hugging Face token (run `hf auth login`)")


def _sync_visibility(dataset: str) -> None:
    """Make the Hub repo's visibility follow config.publish.private."""
    private = bool(CONFIG.get("publish", {}).get("private"))
    try:
        request = urllib.request.Request(
            f"https://huggingface.co/api/datasets/{dataset}/settings",
            data=json.dumps({"private": private}).encode(),
            headers={"Authorization": f"Bearer {_hf_token()}",
                     "Content-Type": "application/json"},
            method="PUT")
        with urllib.request.urlopen(request) as response:
            response.read()
    except Exception as exc:  # visibility must not block trace publishing
        print(f"warning: could not sync visibility for {dataset}: {exc}",
              flush=True)


def _upload(dataset: str, local: Path, remote: str) -> None:
    subprocess.run(["hf", "upload", dataset, str(local), remote,
                    "--repo-type", "dataset"],
                   check=True, capture_output=True, text=True)


def publish_once() -> int:
    dataset = CONFIG.get("publish", {}).get("hf_dataset")
    if not dataset:
        raise SystemExit("config.publish.hf_dataset is not set")
    rows = _publishable_rows()
    card = build_card(rows, stage="preview")
    card_sha = hashlib.sha256(card.encode()).hexdigest()
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    tasks_changed = [row["task"] for row in rows] != state.get("published_tasks")
    card_changed = card_sha != state.get("card_sha256")
    if not tasks_changed and not card_changed:
        return len(rows)

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    _sync_visibility(dataset)
    if tasks_changed:
        text = "".join(json.dumps(row, ensure_ascii=False) + "\n"
                       for row in rows)
        _secret_scan(text)
        PREVIEW_FILE.write_text(text)
        _upload(dataset, PREVIEW_FILE, "traces.jsonl")
    if card_changed:
        _secret_scan(card)
        PREVIEW_CARD.write_text(card)
        _upload(dataset, PREVIEW_CARD, "README.md")
    STATE_FILE.write_text(json.dumps(
        {"published_tasks": [row["task"] for row in rows],
         "card_sha256": card_sha}, indent=2))
    print(f"published {len(rows)} verified traces"
          f"{' + card' if card_changed else ''} -> {dataset}", flush=True)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true",
                        help="keep publishing as new verified traces land")
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
