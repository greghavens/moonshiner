#!/usr/bin/env python3
"""Convert passing security traces into the common whole-session row schema.

The ``build`` phase folds ``data/security/{train,val}.jsonl`` into the full SFT
dataset when they are present, so this stage runs before it (order 4.9) whenever
the security lane is enabled.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_dataset import est_tokens
from common import CONFIG, DATA, SECRET_RE, schemas_for
from normalize import parse_trace
from runtimes.codex import TOOL_REGISTRY as CODEX_TOOL_REGISTRY
from security_runtime import SECURITY

CATALOG = SECURITY / "catalog"
TRACES = SECURITY / "traces"
ATTEMPTS = TRACES / "attempts"
META = TRACES / "meta"
OUT = DATA / "security"

# The security teacher is a Codex agent run in a network-isolated bwrap sandbox
# (no web_search), so its full offered surface is read/patch/plan. Every row lists
# this whole surface, unioned with any tool actually observed — the same
# full-tool-list contract the runtime adapters enforce for the coding lane.
SECURITY_OFFERED_TOOLS = ("exec", "apply_patch", "update_plan")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def language_for(case: dict) -> str:
    meta = case.get("meta") or {}
    if meta.get("language"):
        return str(meta["language"])
    source = str(meta.get("source") or "").lower()
    suffixes = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".java": "java", ".go": "go", ".rb": "ruby", ".php": "php",
        ".cs": "csharp", ".rs": "rust", ".c": "c", ".cpp": "cpp",
        ".tf": "hcl", ".hcl": "hcl", ".yaml": "yaml", ".yml": "yaml",
        ".sh": "bash", ".ps1": "powershell", ".kt": "kotlin",
    }
    for suffix, language in suffixes.items():
        if source.endswith(suffix):
            return language
    return "multi" if case.get("clone_dir") else "security"


def attempt_info(case_id: str, number: int) -> tuple[Path, dict]:
    attempt = ATTEMPTS / case_id / f"attempt-{number:04d}"
    info = json.loads((attempt / "attempt.json").read_text())
    return attempt, info


def build_row(case: dict, meta: dict) -> tuple[dict | None, str | None]:
    number = meta.get("passing_attempt")
    if not number:
        return None, "no passing attempt"
    attempt, info = attempt_info(case["id"], int(number))
    raw = attempt / "teacher.jsonl"
    if not raw.exists():
        return None, "passing rollout is missing"
    workspace = info.get("workspace")
    trace_format = info.get("trace_format") or "codex-rollout"
    turns, _ = parse_trace(raw, trace_format, workspace)
    if not any(message.get("role") == "assistant" for message in turns):
        return None, "no assistant turns"

    session = [
        {"role": "system", "content": case["system"]},
        {"role": "user", "content": case["prompt"]},
        *turns,
    ]
    if SECRET_RE.search(json.dumps(session)):
        return None, "SECRET MATCH — dropped"

    used = sorted({
        call["function"]["name"]
        for message in turns if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
    })
    unknown: list[str] = []
    tools = schemas_for(list(dict.fromkeys(list(SECURITY_OFFERED_TOOLS) + used)),
                        CODEX_TOOL_REGISTRY, warn=unknown)
    source_meta = case.get("meta") or {}
    security_task = (
        "whole_repo_review" if case["kind"] == "repo_review"
        else str(source_meta.get("task") or "answer")
    )
    verifier = (
        "hidden-path-line-recall"
        if case["kind"] == "repo_review"
        else "hidden-reference-judge+structured-label-gate"
    )
    return {
        "messages": session,
        "tools": tools,
        "meta": {
            "task": case["id"],
            "lang": language_for(case),
            "category": f"security-{security_task}",
            "passed": True,
            "tools_used": used,
            "teacher_model": meta.get("teacher_model") or CONFIG["teacher"]["model"],
            "reasoning_effort": meta.get("reasoning_effort"),
            "trace_format": info.get("trace_format") or "codex-rollout",
            "domain": "security",
            "security_task": security_task,
            "verifier": verifier,
            "source_dataset": "fable-secure",
            "source_sample_id": source_meta.get("sample_id") or case.get("seed_id"),
            "technique_id": source_meta.get("technique_id"),
            "repo": case.get("repo") or source_meta.get("repo"),
            "split_hint": case["split"],
        },
    }, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    if not (CATALOG / "cases.jsonl").exists() or not META.exists():
        print("[sec-build] no security catalog/traces present — nothing to build")
        return 0
    cases = {
        case["id"]: case
        for path in (CATALOG / "cases.jsonl", CATALOG / "repo_reviews.jsonl")
        for case in load_jsonl(path)
    }
    partitions: dict[str, list[dict]] = {"train": [], "val": []}
    dropped: list[tuple[str, str]] = []
    for meta_path in sorted(META.glob("*.json")):
        meta = json.loads(meta_path.read_text())
        case = cases.get(meta.get("id"))
        if case is None:
            dropped.append((meta_path.stem, "case missing from catalog"))
            continue
        if not meta.get("passed"):
            dropped.append((case["id"], "verification did not pass"))
            continue
        row, error = build_row(case, meta)
        if error:
            dropped.append((case["id"], error))
            continue
        partitions[case["split"]].append(row)
        if not args.quiet:
            print(f"  {case['id']}: {row['meta']['verifier']}; "
                  f"tools={','.join(row['meta']['tools_used']) or '(none)'}")

    OUT.mkdir(parents=True, exist_ok=True)
    for split, rows in partitions.items():
        with (OUT / f"{split}.jsonl").open("w") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    all_rows = partitions["train"] + partitions["val"]
    tokens = sorted(sum(est_tokens(message) for message in row["messages"])
                    for row in all_rows) or [0]
    manifest = {
        "train": len(partitions["train"]),
        "val": len(partitions["val"]),
        "total": len(all_rows),
        "planned": 2409,
        "dropped_or_incomplete": len(dropped),
        "estimated_tokens_p50": tokens[len(tokens) // 2],
        "estimated_tokens_max": tokens[-1],
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))
    if not args.quiet:
        for case_id, reason in dropped:
            print(f"  dropped {case_id}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
