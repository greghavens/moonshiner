#!/usr/bin/env python3
"""Convert screened, accepted traces into OpenAI-style agent training rows.

Runtime-agnostic: each trace records the ``trace_format`` of the runtime that
produced it, and :mod:`normalize` routes that format to the adapter which turns
the raw stream into ``messages`` + tool schemas. One build path therefore serves
a Codex, Claude Code, or Pi/GLM teacher without change.

Every row carries ``messages``, ``tools``, and provenance ``meta`` (including the
teacher runtime/model and its stream attestation). The judge is the sole quality
decision. Host paths and secrets are still scrubbed before output. Whole trajectories are kept
here as immutable derivation sources; Hugging Face receives cumulative
next-assistant-action prefixes built separately by ``expand_next_steps.py``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from common import (CONFIG, DATA, SECRET_RE, SYSTEM_PROMPT, TRACES, load_seeds, load_behavior_seeds,
                    scrub_text)
from normalize import parse_trace, tool_schemas_for
from screen_traces import validate_reviewer_verdict
from privacy import sanitize_object
from review_contract import is_accepted

RAW = TRACES / "raw"
META = TRACES / "meta"
DIFFS = TRACES / "diffs"
REVIEWS = TRACES / "reviews"

VERIFIER = "acceptance-tests+protected-file-hash+independent-review"
SCREENING = "deterministic-plus-independent-review-v1"


def _provider(runtime_name: str) -> str:
    runtime_config = CONFIG.get("runtimes", {}).get(runtime_name, {})
    if runtime_config.get("display_provider"):
        return runtime_config["display_provider"]
    return {"codex": "openai", "claude-code": "anthropic"}.get(runtime_name,
                                                               runtime_name)


def screening_acceptance(task_id: str, info: dict) -> tuple[bool, str | None]:
    """The judge's accepted decision is the only dataset-routing gate."""
    path = REVIEWS / f"{task_id}.json"
    if not path.exists():
        return False, "judge review is missing"
    try:
        review = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        return False, f"judge review is invalid JSON: {error}"
    if review.get("id") != task_id:
        return False, "judge review task id mismatch"
    return (True, None) if is_accepted(review) else (
        False, "judge rejected the trace")


def redact_secret_matches(value):
    """Return a deep copy with every secret-shaped token replaced."""
    serialized = json.dumps(value, ensure_ascii=False)
    redacted, count = SECRET_RE.subn("[REDACTED_SECRET]", serialized)
    return json.loads(redacted), count


def scrub_session(session: list[dict]) -> list[dict]:
    """Strip host-specific paths from every string in the assembled session."""
    return json.loads(scrub_text(json.dumps(session, ensure_ascii=False)))


def est_tokens(message: dict, chars_per_token: float = 3.3) -> int:
    size = len(message.get("content") or "")
    size += len(message.get("reasoning_content") or "")
    for call in message.get("tool_calls") or []:
        fn = call["function"]
        size += len(fn["name"]) + len(json.dumps(fn["arguments"])) + 20
    return int(size / chars_per_token) + 8


def raw_trace_path(task_id: str, info: dict) -> Path:
    """Resolve the runtime's recorded canonical artifact inside trace storage."""
    recorded = Path(str(info.get("raw_path") or f"raw/{task_id}.jsonl")).name
    return RAW / recorded


def training_tags(seed: dict, turns: list[dict], info: dict) -> list[str]:
    """Describe behavior demonstrated by the accepted trajectory."""
    explicit = seed.get("training_tags") or seed.get("tags") or []
    if isinstance(explicit, str):
        explicit = [explicit]
    tags = {str(tag) for tag in explicit}
    assistant_turns = [message for message in turns if message.get("role") == "assistant"]
    calls = [call for message in assistant_turns for call in message.get("tool_calls") or []]
    if calls:
        tags.add("tool-use")
        tags.add("reasoning:planning")
    else:
        tags.add("response:direct")
    if any(len(message.get("tool_calls") or []) > 1 for message in assistant_turns):
        tags.update({"parallel-tool-calls", "execution:parallel"})
    if len(assistant_turns) > 1:
        tags.update({"multi-turn", "interaction:multi-turn"})
    if info.get("feedback_used"):
        tags.update({"iterative-repair", "reasoning:self-correction"})
    if sum(est_tokens(message) for message in assistant_turns
           if message.get("reasoning_content")) >= 256:
        tags.add("reasoning:extended")
    for call in calls:
        name = (call.get("function") or {}).get("name")
        if name:
            tags.add(f"tool:{name}")
            if any(word in name.casefold() for word in ("test", "check", "verify", "validate")):
                tags.add("reasoning:verification")
    final_content = assistant_turns[-1].get("content") if assistant_turns else None
    if isinstance(final_content, str) and final_content.strip():
        try:
            json.loads(final_content)
        except (TypeError, ValueError):
            pass
        else:
            tags.add("format:strict-json")
    return sorted(tags)


def build_row(seed: dict, info: dict) -> tuple[dict | None, str | None]:
    raw = raw_trace_path(seed["id"], info)
    if not raw.exists():
        return None, "no raw trace"
    trace_format = info.get("trace_format") or (info.get("teacher") or {}).get(
        "trace_format")
    if not trace_format:
        return None, "trace has no recorded trace_format"
    turns, _ = parse_trace(raw, trace_format, workspace=None)
    if not any(message["role"] == "assistant" for message in turns):
        return None, "no assistant turns"

    system_prompt = SYSTEM_PROMPT
    if seed.get("kind") == "tool_behavior":
        from behavior_trace import SYSTEM as system_prompt
    session = ([{"role": "system", "content": system_prompt},
                {"role": "user", "content":
                 info.get("prompt") or seed["prompt"]}] + turns)
    session = sanitize_object(scrub_session(session))
    session, secret_redactions = redact_secret_matches(session)
    if SECRET_RE.search(json.dumps(session, ensure_ascii=False)):
        return None, "SECRET MATCH AFTER REDACTION — dropped"

    used = sorted({call["function"]["name"]
                   for message in turns if message["role"] == "assistant"
                   for call in message.get("tool_calls") or []})
    if seed.get("kind") == "tool_behavior":
        from behavior_trace import schemas_for_seed
        tools = schemas_for_seed(seed, set(seed["available_tools"]))
    else:
        tools = tool_schemas_for(trace_format, turns)

    teacher = info.get("teacher") or {}
    observed = teacher.get("observed_models") or (
        [teacher["observed_model"]] if teacher.get("observed_model") else [])
    meta = {
        "task": seed["id"],
        "lang": seed.get("lang"),
        "category": seed.get("category"),
        "domain": "tool_behavior" if seed.get("kind") == "tool_behavior" else "coding",
        "passed": info.get("passed"),
        "tools_used": used,
        "tags": training_tags(seed, turns, info),
        "teacher_runtime": teacher.get("runtime"),
        "teacher_model": teacher.get("model"),
        "reasoning_effort": teacher.get("reasoning"),
        "observed_models": observed,
        "model_attested": bool(teacher.get("model_attested")),
        "provider": _provider(teacher.get("runtime", "")),
        "trace_format": trace_format,
        "verifier": VERIFIER,
        "screening": SCREENING,
    }
    if secret_redactions:
        meta["secret_redactions"] = secret_redactions
    return {"messages": session, "tools": tools, "meta": meta}, None


def accepted_author_rows(traces_dir: Path = TRACES, quiet: bool = True):
    """Seed-authoring trajectories, if the seed-authoring tooling is present.

    Author rows are produced by the seed-authoring flow (its acceptance gate
    lives in ``seed_author_quality``). When that module or its traces are not
    present this is a no-op, so a pure coding-distill build stands alone.
    """
    try:
        from seed_author_quality import accepted_author_rows as _rows
    except ImportError:
        return [], []
    return _rows(traces_dir, quiet=quiet)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="moonshiner dataset build",
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--include-failed", action="store_true",
                        help="keep verification failures (not recommended)")
    parser.add_argument("--include-unverified", action="store_true",
                        help="keep deferred/unverified traces")
    parser.add_argument("--sample", action="store_true",
                        help="print the first built row and exit")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    seeds = {seed["id"]: seed for seed in [*load_seeds(include_holdout=True),
                                            *load_behavior_seeds()]}
    holdouts = set(CONFIG.get("holdout_tasks", []))
    rows, dropped = [], []
    for meta_path in sorted(META.glob("*.json")):
        info = json.loads(meta_path.read_text())
        seed = seeds.get(info["id"])
        if not seed:
            dropped.append((info["id"], "seed no longer exists"))
            continue
        if info["id"] in holdouts:
            dropped.append((info["id"], "holdout (eval-only)"))
            continue
        accepted, screening_error = screening_acceptance(info["id"], info)
        if not accepted:
            dropped.append((info["id"], screening_error))
            continue
        row, error = build_row(seed, info)
        if error:
            dropped.append((info["id"], error))
            continue
        rows.append(row)
        if not args.quiet:
            print(f"  {info['id']}: tools used: "
                  f"{', '.join(row['meta']['tools_used']) or '(none)'}")

    author_rows, author_dropped = accepted_author_rows(TRACES, quiet=args.quiet)
    rows.extend(author_rows)
    dropped.extend(author_dropped)

    # Prepared rows imported from a pre-Moonshiner directory or Hugging Face
    # dataset were already sanitized and deduplicated by import_existing.
    imported_seen = {hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
                     for row in rows}
    for imported_path in sorted((DATA / "imported").glob("*/rows.jsonl")):
        for line in imported_path.read_text().splitlines():
            if not line.strip():
                continue
            imported = sanitize_object(json.loads(line))
            fingerprint = hashlib.sha256(
                json.dumps(imported, sort_keys=True).encode()).hexdigest()
            if fingerprint in imported_seen:
                continue
            imported_seen.add(fingerprint)
            rows.append(imported)

    if args.sample and rows:
        print(json.dumps(rows[0], indent=2)[:10000])
        return

    val_fraction = CONFIG["build"]["val_frac"]

    def is_validation(row: dict) -> bool:
        digest = int(hashlib.sha1(row["meta"]["task"].encode()).hexdigest(), 16)
        return digest % 100 < int(val_fraction * 100)

    train = [row for row in rows if not is_validation(row)]
    validation = [row for row in rows if is_validation(row)]

    # Security and harness partitions are built separately (their verification
    # contracts differ) and appended only after their own gates pass.
    extra_counts = {name: {"train": 0, "val": 0}
                    for name in ("security", "harness")}
    for source_name in extra_counts:
        source_dir = DATA / source_name
        for split, partition in (("train", train), ("val", validation)):
            source_path = source_dir / f"{split}.jsonl"
            if not source_path.exists():
                continue
            for line in source_path.read_text().splitlines():
                if line.strip():
                    partition.append(sanitize_object(json.loads(line)))
                    extra_counts[source_name][split] += 1

    output = DATA / "full"
    output.mkdir(parents=True, exist_ok=True)
    for filename, partition in (("train.jsonl", train), ("val.jsonl", validation)):
        with (output / filename).open("w") as handle:
            for row in partition:
                handle.write(json.dumps(row) + "\n")

    tokens = sorted(sum(est_tokens(message) for message in row["messages"])
                    for row in train + validation) or [0]
    print(f"\ntrain={len(train)} val={len(validation)} "
          f"security={sum(extra_counts['security'].values())} "
          f"harness={sum(extra_counts['harness'].values())} "
          f"est-tokens p50={tokens[len(tokens) // 2]} max={tokens[-1]}")
    for task_id, reason in dropped:
        print(f"  dropped {task_id}: {reason}")
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
