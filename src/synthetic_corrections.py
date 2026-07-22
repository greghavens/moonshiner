"""Opt-in correction queue for narrowly repairable, never-accepted traces."""
from __future__ import annotations

import argparse
import difflib
import getpass
import hashlib
import shutil
import json
from dataclasses import dataclass
from pathlib import Path

from common import (CONFIG, RUNS, STORAGE_ROOT, clear_runtime_caches, git_diff,
                    load_seeds, materialize, protected_hashes, run_setup,
                    run_verify, scrub_text)
from run_state import (connect, create_run, finish_attempt, set_run_status,
                       start_attempt)
from runtimes import get_judge, get_runtime
from normalize import parse_trace, tool_schemas_for
from screen_traces import (apply_candidate_patch, feedback_from_review, screen)
import publish_queue

KIND = "synthetic-correction"


@dataclass(frozen=True)
class CorrectionPaths:
    root: Path
    traces: Path
    publish: Path


@dataclass(frozen=True)
class PublicationTarget:
    dataset: str | None
    source_root: Path
    publish_dir: Path


@dataclass
class QueueItem:
    seed_id: str
    attempt: int = 1
    feedback: str | None = None


class CorrectionQueue:
    """Small deterministic tail-retry policy; durable orchestration wraps this."""
    def __init__(self, seed_ids: list[str], max_attempts: int = 2):
        self.pending = [QueueItem(seed_id) for seed_id in seed_ids]
        self.max_attempts = max_attempts
        self.accepted: set[str] = set()
        self.exhausted: set[str] = set()

    def pop(self) -> QueueItem:
        return self.pending.pop(0)

    def record_judgment(self, item: QueueItem, review: dict) -> None:
        if review.get("accepted"):
            self.accepted.add(item.seed_id)
        elif item.attempt < self.max_attempts:
            self.pending.append(QueueItem(item.seed_id, item.attempt + 1,
                                         feedback_from_review(review)
                                         or review.get("reason")))
        else:
            self.exhausted.add(item.seed_id)


def default_dataset(primary: str | None) -> str | None:
    return f"{primary}-synthetic-corrections" if primary else None


def settings(config: dict | None = None) -> dict:
    config = config or CONFIG
    judge = config.get("judge") or {}
    configured = config.get("synthetic_corrections") or {}
    resolved = {
        "enabled": bool(configured.get("enabled", False)),
        "runtime": configured.get("runtime") or judge.get("runtime"),
        "model": configured.get("model") or judge.get("model"),
        "reasoning": configured.get("reasoning") or judge.get("reasoning"),
        "max_attempts": int(configured.get("max_attempts", 2)),
        "hf_dataset": configured.get("hf_dataset") or default_dataset(
            (config.get("publish") or {}).get("hf_dataset")),
    }
    if resolved["max_attempts"] < 1:
        raise ValueError("synthetic_corrections.max_attempts must be positive")
    return resolved


def correction_paths(storage_root: Path = STORAGE_ROOT) -> CorrectionPaths:
    root = storage_root / "synthetic-corrections"
    return CorrectionPaths(root, root / "traces", root / "hf-publish")


publish_worker = publish_queue.main


def publication_target(kind: str, config: dict | None = None,
                       storage_root: Path = STORAGE_ROOT) -> PublicationTarget:
    """Resolve an explicit target for the one shared publication queue."""
    config = config or CONFIG
    if kind == "trace":
        return PublicationTarget((config.get("publish") or {}).get("hf_dataset"),
                                 storage_root / "traces", storage_root / "data" / "hf-publish")
    if kind == KIND:
        paths = correction_paths(storage_root)
        return PublicationTarget(settings(config)["hf_dataset"], paths.traces, paths.publish)
    raise ValueError(f"unsupported publication kind: {kind}")


def accepted_ids_for_publish(db, kind: str = KIND) -> set[str]:
    return {str(row[0]) for row in db.execute("""
        SELECT DISTINCT a.seed_id FROM attempts a JOIN runs r ON r.id=a.run_id
        WHERE r.kind=? AND a.status='accepted'""", (kind,))}


def eligible_exhausted_attempts(db) -> list[dict]:
    """One candidate/use case, with at most three current-revision failures."""
    rows = db.execute("""
      SELECT a.id,a.seed_id,a.finished_at,a.artifact_path,a.review_json
      FROM attempts a JOIN runs r ON r.id=a.run_id
      WHERE r.kind='trace' AND a.status IN ('retry','exhausted')
        AND a.artifact_path IS NOT NULL
        AND a.id > COALESCE((SELECT MAX(sa.id) FROM attempts sa
          JOIN runs sr ON sr.id=sa.run_id WHERE sr.kind='seed'
          AND sa.status='accepted' AND sa.seed_id=a.seed_id),0)
        AND NOT EXISTS (SELECT 1 FROM attempts ok JOIN runs rr ON rr.id=ok.run_id
          WHERE rr.kind='trace' AND ok.seed_id=a.seed_id AND ok.status='accepted'
          AND ok.id > COALESCE((SELECT MAX(sa.id) FROM attempts sa
            JOIN runs sr ON sr.id=sa.run_id WHERE sr.kind='seed'
            AND sa.status='accepted' AND sa.seed_id=a.seed_id),0))
        AND NOT EXISTS (SELECT 1 FROM attempts ca JOIN runs cr ON cr.id=ca.run_id
          WHERE cr.kind='synthetic-correction' AND ca.seed_id=a.seed_id
          AND ca.status IN ('accepted','exhausted'))
      ORDER BY a.id
    """).fetchall()
    grouped: dict[str, list[dict]] = {}
    first: dict[str, int] = {}
    for row in rows:
        item = dict(row)
        grouped.setdefault(item["seed_id"], []).append(item)
        first.setdefault(item["seed_id"], item["id"])
    candidates = []
    for seed_id in sorted(grouped, key=first.get):
        failures = list(reversed(grouped[seed_id][-3:]))
        candidates.append({**failures[0], "failures": failures})
    return candidates


def eligibility_prompt(seed: dict, review: dict, trace_excerpt: str) -> str:
    return scrub_text(f"""You are deciding whether a failed agent trace is eligible for a
narrow synthetic correction. Return ineligible unless the reasoning is already substantially correct
and the only defect is missing an obvious tool call, an action clearly implied by the
reasoning, or minor broken code. No refactoring, redesign, broad replanning, or invented work.
Code edits must be small; one or two files may be added only when genuinely missing. The final
correction must be thoroughly tested. If uncertain, return ineligible.
Return only JSON with: eligible (boolean), reasoning_already_correct (boolean), minor_change
(boolean), and repair_instructions (string). The instructions must identify only the minimal fix.

TASK: {seed.get('prompt', '')}
REJECTION: {json.dumps(review, sort_keys=True)}
FAILED TRACE EXCERPT: {trace_excerpt[-12000:]}
""")


def validate_eligibility(verdict: dict | None) -> tuple[bool, str]:
    if not isinstance(verdict, dict):
        return False, "missing verdict"
    required = (verdict.get("eligible") is True
                and verdict.get("reasoning_already_correct") is True
                and verdict.get("minor_change") is True
                and bool(str(verdict.get("repair_instructions") or "").strip()))
    return (True, "") if required else (False, "not narrowly eligible")


def _reasoning(messages: list[dict]) -> list[str]:
    payloads: list[str] = []

    def visit(value, *, parent_type: str = "") -> None:
        if isinstance(value, dict):
            kind = str(value.get("type") or parent_type).lower()
            for key, child in value.items():
                lowered = key.lower()
                if (lowered in {"reasoning", "reasoning_content", "thinking"}
                        or kind in {"thinking", "reasoning"} and lowered in {
                            "content", "text"}):
                    payloads.append(json.dumps(child, ensure_ascii=False,
                                               separators=(",", ":")))
                else:
                    visit(child, parent_type=kind)
        elif isinstance(value, list):
            for child in value:
                visit(child, parent_type=parent_type)

    for message in messages:
        if message.get("role") == "assistant":
            visit(message)
    return payloads


def correction_delta(source: list[dict], corrected: list[dict],
                     *, changed_files: int = 0) -> dict:
    """Summarize a correction without treating a rewritten trace as minimal."""
    source_lines = [json.dumps(message, sort_keys=True, ensure_ascii=False)
                    for message in source]
    corrected_lines = [json.dumps(message, sort_keys=True, ensure_ascii=False)
                       for message in corrected]
    matcher = difflib.SequenceMatcher(a=source_lines, b=corrected_lines, autojunk=False)
    changed = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2
                  in matcher.get_opcodes() if op != "equal")
    return {"reasoning_unchanged": _reasoning(source) == _reasoning(corrected),
            "changed_messages": changed, "changed_files": changed_files,
            "source_messages": len(source), "corrected_messages": len(corrected)}


def validate_correction_delta(delta: dict, *, max_changed_messages: int = 3,
                              max_changed_files: int = 2) -> tuple[bool, str]:
    if not delta.get("reasoning_unchanged"):
        return False, "assistant reasoning changed"
    if int(delta.get("changed_messages", 10**9)) > max_changed_messages:
        return False, "trace correction is not minimal"
    if int(delta.get("changed_files", 10**9)) > max_changed_files:
        return False, "workspace correction is not minimal"
    return True, ""


def _patch_sections(patch: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git a/"):
            parts = line.split()
            current = parts[2].removeprefix("a/") if len(parts) >= 3 else line
            sections.setdefault(current, [])
        if current:
            sections[current].append(line)
    return {name: "".join(lines) for name, lines in sections.items()}


def changed_patch_files(before: str, after: str) -> int:
    left, right = _patch_sections(before), _patch_sections(after)
    return sum(left.get(name) != right.get(name) for name in set(left) | set(right))


def correction_judge_prompt(source: str, corrected: str, delta: dict) -> str:
    return scrub_text(f"""This is a synthetic correction audit performed before the normal
trace acceptance verdict. Compare the failed source and correction. Confirm the source model's
reasoning is unchanged and the correction is minimal in nature. Rewritten reasoning, refactoring,
broad message replacement, invented tool results, or unrelated edits are an automatic rejection.
Only an omitted obvious tool call/result, a tiny code fix such as one line, or a genuinely missing
small file is eligible.

MACHINE DELTA: {json.dumps(delta, sort_keys=True)}
FAILED SOURCE TRACE:\n{source[-12000:]}
CORRECTED TRACE:\n{corrected[-12000:]}
""")


def write_corrected_trace(path: Path, messages: list[dict], tools: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_text(json.dumps({"messages": messages, "tools": tools},
                                  ensure_ascii=False) + "\n")
    pending.replace(path)


def read_corrected_trace(path: Path) -> tuple[list[dict], list[dict]]:
    value = json.loads(path.read_text())
    messages, tools = value.get("messages"), value.get("tools")
    if not isinstance(messages, list) or not isinstance(tools, list):
        raise ValueError("corrected trace must contain messages and tools lists")
    return messages, tools


def validate_tool_pairs(messages: list[dict]) -> tuple[bool, str]:
    calls = {str(call.get("id")) for message in messages
             for call in (message.get("tool_calls") or []) if call.get("id")}
    results = {str(message.get("tool_call_id")) for message in messages
               if message.get("role") == "tool" and message.get("tool_call_id")}
    missing = calls - results
    orphaned = results - calls
    if missing:
        return False, f"tool calls lack results: {sorted(missing)}"
    if orphaned:
        return False, f"tool results lack calls: {sorted(orphaned)}"
    return True, ""


def validate_tools_unchanged(source: list[dict], corrected: list[dict]) -> tuple[bool, str]:
    if json.dumps(source, sort_keys=True) != json.dumps(corrected, sort_keys=True):
        return False, "offered tool schemas changed"
    return True, ""


def _source_artifact(artifact: Path) -> tuple[dict, Path, Path]:
    meta = json.loads((artifact / "meta.json").read_text())
    raw_name = Path(str(meta.get("raw_path") or "")).name
    raw = artifact / raw_name
    patch = artifact / "diffs.patch"
    if not raw.is_file() or not patch.is_file():
        raise ValueError("failed attempt archive lacks its raw trace or patch")
    return meta, raw, patch


def _correction_prompt(seed: dict, source_messages: list[dict], tools: list[dict],
                       feedback: str) -> str:
    return scrub_text(f"""Repair this failed trace with the smallest possible correction.
The assistant reasoning_content values are immutable: copy each byte-for-byte. Do not refactor,
rewrite reasoning, or improve unrelated material. You may insert only an obvious missing tool
call/result, fix a tiny code defect, or add a genuinely missing small file. Thoroughly run the
task's existing verification. Return ONLY JSON with the key corrected_messages. Moonshiner
preserves the offered schemas itself. Every tool call needs its matching real result.

TASK: {seed.get('prompt', '')}
JUDGE FEEDBACK: {feedback}
OFFERED TOOLS: {json.dumps(tools, ensure_ascii=False)}
FAILED MESSAGES: {json.dumps(source_messages, ensure_ascii=False)}
""")


def create_candidate(seed: dict, runtime, storage_root: Path, feedback: str,
                     source_artifact: Path) -> dict:
    """Surgically correct one archived failure while preserving its reasoning."""
    paths = correction_paths(storage_root)
    for directory in (paths.traces / "raw", paths.traces / "meta",
                      paths.traces / "diffs", paths.traces / "reviews"):
        directory.mkdir(parents=True, exist_ok=True)
    source_meta, source_raw, source_patch = _source_artifact(source_artifact)
    source_messages, parsed = parse_trace(
        source_raw, source_meta["trace_format"], workspace=None)
    tools = (parsed or {}).get("tools") or tool_schemas_for(
        source_meta["trace_format"], source_messages)
    workspace = materialize(seed, name=f"synthetic-correction-{seed['id']}")
    setup_ok, setup_output = run_setup(seed, workspace)
    protected_before = protected_hashes(seed, workspace)
    applied, apply_error = apply_candidate_patch(workspace, source_patch.read_text())
    if not setup_ok or not applied:
        return {"passed": False, "reason": setup_output or apply_error}
    before_patch = git_diff(workspace)
    out_dir = paths.root / "runtime" / seed["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    result = runtime.run_review(
        _correction_prompt(seed, source_messages, tools, feedback), workspace,
        out_dir=out_dir, schema=None, read_only=False)
    verdict = result.verdict or {}
    corrected = verdict.get("corrected_messages")
    corrected_tools = tools
    if not isinstance(corrected, list):
        return {"passed": False, "reason": "correction runtime returned no corrected trace"}
    delta = correction_delta(source_messages, corrected)
    delta_ok, delta_error = validate_correction_delta(delta)
    pairs_ok, pairs_error = validate_tool_pairs(corrected)
    tools_ok, tools_error = validate_tools_unchanged(tools, corrected_tools)
    clear_runtime_caches(workspace)
    verified_once, verify_output = run_verify(seed, workspace)
    verified_twice, _ = run_verify(seed, workspace)
    protected_intact = protected_before == protected_hashes(seed, workspace)
    final_patch = git_diff(workspace)
    changed_files = changed_patch_files(before_patch, final_patch)
    delta["changed_files"] = changed_files
    delta_ok, delta_error = validate_correction_delta(delta)
    passed = bool(result.return_code == 0 and not result.timed_out and not result.error
                  and result.model_attested and delta_ok
                  and pairs_ok and tools_ok and verified_once and verified_twice
                  and protected_intact)
    raw_path = paths.traces / "raw" / f"{seed['id']}.synthetic-correction.json"
    write_corrected_trace(raw_path, corrected, corrected_tools)
    patch_path = paths.traces / "diffs" / f"{seed['id']}.patch"
    patch_path.write_text(final_patch)
    raw_text = raw_path.read_text()
    meta = {**source_meta, "id": seed["id"], "passed": passed,
            "verify_passed": bool(verified_once and verified_twice),
            "protected_intact": protected_intact,
            "verify_output": scrub_text(verify_output)[:8000],
            "trace_format": "moonshiner-synthetic-correction-v1",
            "raw_path": str(raw_path.relative_to(paths.traces.parent)),
            "raw_sha256": hashlib.sha256(raw_text.encode()).hexdigest(),
            "diff_sha256": hashlib.sha256(final_patch.encode()).hexdigest(),
            "synthetic_correction": {"source_artifact": str(source_artifact),
                "delta": delta, "source_trace": source_raw.read_text(errors="replace")[-12000:],
                "correction_runtime": runtime.name,
                "correction_model": runtime.role.get("model")},
            "correction_failure": delta_error or pairs_error or tools_error or None}
    meta_path = paths.traces / "meta" / f"{seed['id']}.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def correct_once(seed: dict, runtime, judge, storage_root: Path,
                 feedback: str, source_artifact: Path) -> dict:
    """Create one surgical candidate, then invoke the normal judge."""
    paths = correction_paths(storage_root)
    meta = create_candidate(seed, runtime, storage_root, feedback, source_artifact)
    if not meta.get("passed"):
        return {"accepted": False, "status": "trace_failed",
                "reason": meta.get("verify_output") or "correction trace failed"}
    return screen(seed, judge=judge, traces_root=paths.traces)


def companion_notice(primary: str, model_name: str) -> str:
    url = f"https://huggingface.co/datasets/{primary}"
    return (f"# {model_name} Synthetic Corrections\n\n"
            f"> Synthetic Corrections companion to [{primary}]({url}).\n\n"
            "This dataset contains narrowly corrected traces that never passed "
            "the source model's trace judge. Every correction was independently judged.")


def merge_canonical_rows(existing: list[dict], generated: list[dict]) -> list[dict]:
    """Task-keyed replacement without dropping unrelated companion rows."""
    replaced = {str(row.get("task")) for row in generated}
    return [row for row in existing if str(row.get("task")) not in replaced] + generated


def build_companion(paths: CorrectionPaths, config: dict | None = None) -> dict:
    """Use the canonical formatter/expander/exporter for accepted corrections."""
    from build_dataset import build_row, screening_acceptance
    from expand_next_steps import write_split
    from export_hf_next_steps import build_row as export_row, validate_export
    config = config or CONFIG
    seeds = {seed["id"]: seed for seed in load_seeds(include_holdout=True)}
    full = paths.root / "full"
    next_step = paths.root / "next_step"
    full.mkdir(parents=True, exist_ok=True)
    next_step.mkdir(parents=True, exist_ok=True)
    partitions = {"train": [], "val": []}
    dropped = {}
    for meta_path in sorted((paths.traces / "meta").glob("*.json")):
        info = json.loads(meta_path.read_text())
        seed = seeds.get(info.get("id"))
        accepted, reason = screening_acceptance(info.get("id"), info, paths.traces)
        if not seed or not accepted:
            dropped[info.get("id")] = reason or "seed missing"
            continue
        row, reason = build_row(seed, info, paths.traces)
        if not row:
            dropped[seed["id"]] = reason
            continue
        row["meta"]["synthetic_correction"] = True
        digest = int(__import__("hashlib").sha1(seed["id"].encode()).hexdigest(), 16)
        split = "val" if digest % 100 < int(config["build"]["val_frac"] * 100) else "train"
        partitions[split].append(row)
    for split, rows in partitions.items():
        source = full / f"{split}.jsonl"
        source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        write_split(source, next_step / f"{split}.jsonl")
    paths.publish.mkdir(parents=True, exist_ok=True)
    output = paths.publish / "traces.jsonl"
    generated_rows = []
    for split in ("train", "val"):
        for line in (next_step / f"{split}.jsonl").read_text().splitlines():
            if line.strip():
                generated_rows.append(export_row(json.loads(line), split))
    existing_rows = []
    if output.is_file():
        existing_rows = [json.loads(line) for line in output.read_text().splitlines()
                         if line.strip()]
    merged_rows = merge_canonical_rows(existing_rows, generated_rows)
    pending = output.with_suffix(".jsonl.pending")
    with pending.open("w") as handle:
        for row in merged_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    pending.replace(output)
    validation = validate_export(output) if output.stat().st_size else {"trajectories": 0}
    if output.stat().st_size:
        from export_hf_card import build_card, _banner_source
        rows = [json.loads(line) for line in output.read_text().splitlines() if line.strip()]
        companion_config = json.loads(json.dumps(config))
        companion_config.setdefault("publish", {})["hf_dataset"] = settings(config)["hf_dataset"]
        display = companion_config["publish"].get("model_display") or "Model"
        companion_config["publish"]["pretty_name"] = f"{display} Synthetic Corrections"
        (paths.publish / "README.md").write_text(build_card(
            rows, config=companion_config, publish_dir=paths.publish,
            companion_primary=(config.get("publish") or {}).get("hf_dataset")))
        banner = _banner_source()
        if banner.is_file():
            shutil.copy2(banner, paths.publish / "moonshiner-dataset-banner.png")
    return {"rows": sum(len(value) for value in partitions.values()),
            "dropped": dropped, **validation}


def _correction_runtime(config: dict):
    role = settings(config)
    merged = dict(config)
    merged["synthetic_correction"] = {key: role[key]
        for key in ("runtime", "model", "reasoning")}
    return get_runtime("synthetic_correction", merged)


def _archive_candidate(run_id: str, seed_id: str, number: int,
                       paths: CorrectionPaths) -> str:
    destination = RUNS / run_id / "artifacts" / seed_id / f"attempt-{number:04d}"
    destination.mkdir(parents=True, exist_ok=True)
    for directory, suffix, name in (("meta", ".json", "meta.json"),
                                     ("diffs", ".patch", "diffs.patch"),
                                     ("reviews", ".json", "reviews.json")):
        source = paths.traces / directory / f"{seed_id}{suffix}"
        if source.is_file():
            shutil.copy2(source, destination / name)
    meta_path = paths.traces / "meta" / f"{seed_id}.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text())
        raw = paths.traces.parent / str(meta.get("raw_path") or "")
        if raw.is_file():
            shutil.copy2(raw, destination / raw.name)
    return str(destination)


def run(*, dry_run: bool = False, config: dict | None = None,
        db_path: Path = RUNS / "moonshiner.sqlite3", runtime=None, judge=None) -> dict:
    config = config or CONFIG
    opts = settings(config)
    if not opts["enabled"]:
        return {"enabled": False, "eligible": 0, "model_calls": 0}
    db = connect(db_path)
    candidates = eligible_exhausted_attempts(db)
    report = {"enabled": True, "eligible": len(candidates), "model_calls": 0,
              "dataset": opts["hf_dataset"], "max_attempts": opts["max_attempts"]}
    if dry_run:
        db.close()
        return report
    if not candidates:
        report.update({"accepted": 0, "exhausted": 0})
        db.close()
        return report
    # Paid execution is deliberately explicit; every attempt and verdict is
    # durable, and rejected corrections return to the tail.
    runtime = runtime or _correction_runtime(config)
    judge = judge or get_judge(config)
    seeds = {seed["id"]: seed for seed in load_seeds(include_holdout=True)}
    run_id = create_run(db, KIND, {"runtime": opts["runtime"],
        "model": opts["model"], "dataset": opts["hf_dataset"]},
        {"max_attempts": opts["max_attempts"]},
        [candidate["seed_id"] for candidate in candidates])
    queue = CorrectionQueue([], max_attempts=opts["max_attempts"])
    candidate_by_id = {candidate["seed_id"]: candidate for candidate in candidates}
    for candidate in candidates:
        seed = seeds.get(candidate["seed_id"])
        if not seed:
            start_attempt(db, run_id, candidate["seed_id"], 1)
            finish_attempt(db, run_id, candidate["seed_id"], 1, "exhausted",
                           error="seed no longer exists")
            continue
        failure_sections = []
        for failure in candidate["failures"]:
            artifact = Path(failure["artifact_path"]) if failure.get("artifact_path") else None
            trace_text = ""
            if artifact and artifact.is_dir():
                trace_text = "\n".join(path.read_text(errors="replace")
                    for path in sorted(artifact.iterdir()) if path.is_file())
            failure_sections.append({"review": json.loads(failure.get("review_json") or "{}"),
                                     "trace": trace_text[-12000:]})
        latest_artifact = Path(candidate["failures"][0]["artifact_path"])
        prompt = eligibility_prompt(seed, {"failures": failure_sections},
                                    "\n\n".join(item["trace"] for item in failure_sections))
        eligibility_dir = correction_paths().root / "eligibility" / seed["id"]
        eligibility_dir.mkdir(parents=True, exist_ok=True)
        eligibility = runtime.run_review(prompt, latest_artifact,
            out_dir=eligibility_dir,
            schema=None, read_only=True)
        report["model_calls"] += 1
        if (eligibility.return_code != 0 or eligibility.timed_out
                or not eligibility.model_attested or eligibility.error):
            error = "correction eligibility reviewer failed or was not model-attested"
            set_run_status(db, run_id, "failed", error)
            db.close()
            raise RuntimeError(error)
        eligible, _ = validate_eligibility(eligibility.verdict)
        if eligible:
            queue.pending.append(QueueItem(seed["id"], 1,
                eligibility.verdict["repair_instructions"]))
        else:
            start_attempt(db, run_id, seed["id"], 1)
            finish_attempt(db, run_id, seed["id"], 1, "exhausted",
                           review={"eligibility": eligibility.verdict},
                           error="not narrowly correctable")
    paths = correction_paths()
    while queue.pending:
        item = queue.pop()
        seed = seeds[item.seed_id]
        source = Path(candidate_by_id[item.seed_id]["failures"][0]["artifact_path"])
        start_attempt(db, run_id, item.seed_id, item.attempt)
        review = correct_once(seed, runtime, judge, STORAGE_ROOT,
                              item.feedback or "", source)
        report["model_calls"] += 2
        archive = _archive_candidate(run_id, item.seed_id, item.attempt, paths)
        queue.record_judgment(item, review)
        status = "accepted" if review.get("accepted") else (
            "retry" if item.attempt < opts["max_attempts"] else "exhausted")
        finish_attempt(db, run_id, item.seed_id, item.attempt, status,
                       review=review, error=None if status == "accepted" else review.get("reason"),
                       artifact_path=archive)
    rows = db.execute("SELECT status FROM jobs WHERE run_id=?", (run_id,)).fetchall()
    set_run_status(db, run_id, "complete" if all(row[0] == "accepted" for row in rows)
                   else "complete_with_rejections")
    report.update({"accepted": len(queue.accepted), "exhausted": len(queue.exhausted)})
    db.close()
    return report


def status_report(*, config: dict | None = None,
                  db_path: Path = RUNS / "moonshiner.sqlite3") -> dict:
    config = config or CONFIG
    opts = settings(config)
    db = connect(db_path)
    report = {"enabled": opts["enabled"], "dataset": opts["hf_dataset"],
              "max_attempts": opts["max_attempts"],
              "eligible": len(eligible_exhausted_attempts(db)),
              "accepted": 0, "exhausted": 0, "published": 0}
    for status_name in ("accepted", "exhausted"):
        report[status_name] = int(db.execute(
            "SELECT COUNT(DISTINCT a.seed_id) FROM attempts a "
            "JOIN runs r ON r.id=a.run_id WHERE r.kind=? AND a.status=?",
            (KIND, status_name)).fetchone()[0])
    db.close()
    acknowledgement = correction_paths().root / "published-trajectories.json"
    if acknowledgement.is_file():
        try:
            state = json.loads(acknowledgement.read_text())
            report["published"] = len(state.get("published_attempts") or {})
        except (OSError, json.JSONDecodeError):
            pass
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="moonshiner synthetic-corrections")
    sub = parser.add_subparsers(dest="action", required=True)
    configure = sub.add_parser("configure", help="configure the opt-in correction queue")
    configure.add_argument("--disable", action="store_true")
    run_parser = sub.add_parser("run", help="process the correction queue")
    run_parser.add_argument("--dry-run", action="store_true",
                            help="show eligible exhausted traces without model calls")
    run_parser.add_argument("--yes", action="store_true",
                            help="authorize configured provider/model calls")
    sub.add_parser("status", help="show correction configuration and candidate count")
    args = parser.parse_args(argv)
    if args.action == "configure":
        from configuration import load_config, update_local
        if args.disable:
            update_local("synthetic_corrections.enabled", False)
            print("synthetic corrections disabled")
            return 0
        config = load_config()
        judge = config["judge"]
        runtime = input(f"Correction harness [{judge['runtime']}]: ").strip() or judge["runtime"]
        if runtime == "pi":
            import moonshiner
            runtime, _ = moonshiner._configure_pi_provider(config, runtime)
            config = load_config()
        known = set((config.get("runtimes") or {})) | {"codex", "claude-code", "pi"}
        if runtime not in known:
            raise SystemExit(f"unknown correction harness {runtime!r}")
        model = input(f"Correction model [{judge['model']}]: ").strip() or judge["model"]
        default_target = default_dataset((config.get("publish") or {}).get("hf_dataset")) or ""
        dataset = input(f"Companion Hugging Face dataset [{default_target}]: ").strip() or default_target
        if not dataset:
            raise SystemExit("a companion Hugging Face dataset name is required")
        attempts_text = input("Maximum correction attempts per trace [2]: ").strip() or "2"
        attempts = int(attempts_text)
        if attempts < 1:
            raise SystemExit("maximum correction attempts must be positive")
        update_local("synthetic_corrections.enabled", True)
        update_local("synthetic_corrections.runtime", runtime)
        update_local("synthetic_corrections.model", model)
        update_local("synthetic_corrections.reasoning", judge.get("reasoning"))
        update_local("synthetic_corrections.max_attempts", attempts)
        update_local("synthetic_corrections.hf_dataset", dataset or None)
        config = load_config()
        runtime_config = (config.get("runtimes") or {}).get(runtime) or {}
        from common import key_env_name, key_persist_path
        try:
            env_name = key_env_name(runtime_config)
            key_path = key_persist_path(runtime_config)
        except RuntimeError:
            print(f"{runtime}: using its existing CLI login; no API key requested.")
        else:
            import os
            if not os.environ.get(env_name) and not key_path.is_file():
                secret = getpass.getpass(f"API key ({env_name}; hidden): ").strip()
                if not secret:
                    raise SystemExit("a key is required for the selected provider")
                key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                key_path.write_text(secret); key_path.chmod(0o600)
        print(f"synthetic corrections enabled -> {dataset}")
        return 0
    if args.action == "status":
        print(json.dumps(status_report(), indent=2))
        return 0
    if not args.dry_run and not args.yes:
        parser.error("paid correction processing requires --yes (or use --dry-run)")
    print(json.dumps(run(dry_run=args.dry_run), indent=2))
    return 0
