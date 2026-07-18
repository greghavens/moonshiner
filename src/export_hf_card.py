#!/usr/bin/env python3
"""Render the Hugging Face dataset card for the published next-step export.

Writes ``data/hf-publish/README.md`` from the ACTUAL published rows
(``data/hf-publish/traces.jsonl``) plus ``config.json`` — so the card always
represents the real mix (languages, task categories, coding vs security), the
teacher/judge configuration, the tool surface, split sizes, and the model
attestation rate. Nothing is hand-maintained: re-run after any export and the
numbers, tags, and size category follow the data.

Style mirrors the sibling house cards — rich YAML front-matter, a regenerable
``screened-snapshot`` status block, and provenance + limitations sections that
document the verification and independent-judge screening effort behind the
corpus. This phase calls no model; it is safe to run offline.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from statistics import median

from common import CONFIG, DATA, ROOT

PUBLISH_DIR = DATA / "hf-publish"
TRACES = PUBLISH_DIR / "traces.jsonl"
CARD = PUBLISH_DIR / "README.md"
SEEDS_DIR = ROOT / "tasks" / "seeds"

# One row per published column, in the exact export order, with a consumer-facing
# description. Kept in lockstep with export_hf_next_steps.PUBLISH_KEY_ORDER.
COLUMN_DOCS: tuple[tuple[str, str, str], ...] = (
    ("task", "string", "Seed/task id the source trajectory solved."),
    ("source_trajectory_id", "string",
     "Stable id of the whole-session trajectory this row is a prefix of."),
    ("source_trajectory_sha256", "string",
     "Content hash of the source trajectory (provenance)."),
    ("lang", "string", "Primary programming language."),
    ("category", "string", "Task kind (build, debug, feature, refactor, …)."),
    ("domain", "string", "coding, security, or harness."),
    ("verifier", "string", "How the source trajectory was verified before export."),
    ("split", "string", "train or val (trajectory-disjoint)."),
    ("teacher_runtime", "string", "Coding-agent runtime that produced the trace."),
    ("teacher_model", "string", "Teacher model id."),
    ("reasoning_effort", "string", "Teacher thinking/reasoning level."),
    ("provider", "string", "Serving provider for the teacher model."),
    ("observed_models", "list[string]",
     "Model id(s) actually observed in the attested stream."),
    ("model_attested", "bool",
     "Both the agent stream and the upstream proxy confirmed the model."),
    ("trace_format", "string", "Native runtime trace format before normalization."),
    ("tools_used", "list[string]", "Tools the assistant actually called."),
    ("derivation", "string", "How the next-step row was derived."),
    ("assistant_step", "int", "This row's position (1..N) among assistant turns."),
    ("assistant_steps", "int", "Total assistant turns in the source trajectory."),
    ("target_message_index", "int", "Index of the final (target) assistant message."),
    ("original_n_messages", "int", "Message count of the full source trajectory."),
    ("n_messages", "int", "Message count in this cumulative-prefix row."),
    ("messages", "list[object]",
     "The conversation (system/user/assistant/tool) in OpenAI chat format."),
    ("tools", "string",
     "JSON-encoded tool schemas — the full offered action space for the row."),
)

BASE_TAGS = (
    "traces", "code", "agentic", "tool-use", "coding-agent", "coding-agents",
    "agent-traces", "sft", "distillation", "reasoning", "chain-of-thought", "cot",
)
SECURITY_TAGS = (
    "cybersecurity", "security", "secure-coding", "defensive-security",
    "vulnerability-detection", "static-analysis", "code-review", "cwe", "owasp",
)


def _load_rows(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"no published export at {path} — run `export-next` first")
    rows = [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]
    if not rows:
        raise SystemExit(f"published export {path} is empty")
    return rows


def _size_category(n: int) -> str:
    if n < 1_000:
        return "n<1K"
    if n < 10_000:
        return "1K<n<10K"
    if n < 100_000:
        return "10K<n<100K"
    if n < 1_000_000:
        return "100K<n<1M"
    return "1M<n<10M"


def _pct(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


def _display_model(model_id: str) -> str:
    """`moonshotai/kimi-k3` -> `Kimi K3` for the human-facing title."""
    tail = (model_id or "").split("/")[-1]
    return " ".join(word.upper() if word.isdigit() or len(word) <= 2
                    else word.capitalize()
                    for word in tail.replace("_", "-").split("-")) or model_id


def _model_tags(model_id: str, provider: str) -> list[str]:
    tags: list[str] = []

    def add(tag: str) -> None:
        tag = (tag or "").strip().lower()
        if tag and tag not in tags:
            tags.append(tag)

    for piece in (model_id or "").replace("/", "-").split("-"):
        add(piece)                      # org + each component: moonshotai, kimi, k3
    tail = (model_id or "").split("/")[-1].lower().split("-")
    for size in range(1, len(tail) + 1):
        add("-".join(tail[:size]))      # progressive family: kimi, kimi-k3
    add(provider)
    add("pi-agent")
    add("pi-coding-agent")
    return tags


def _trajectories(rows: list[dict]) -> dict[str, dict]:
    """Collapse cumulative-prefix rows to one entry per source trajectory."""
    view: dict[str, dict] = {}
    for row in rows:
        tid = row.get("source_trajectory_id") or row.get("task")
        entry = view.setdefault(tid, {
            "task": row.get("task"),
            "lang": row.get("lang") or "unknown",
            "category": row.get("category") or "unknown",
            "domain": row.get("domain") or "coding",
            "split": row.get("split"),
            "steps": row.get("assistant_steps") or 0,
            "tools_used": set(),
        })
        entry["tools_used"].update(row.get("tools_used") or [])
    return view


def _offered_tools(rows: list[dict]) -> list[str]:
    for row in rows:
        try:
            schemas = json.loads(row.get("tools") or "[]")
        except json.JSONDecodeError:
            continue
        names = [s.get("function", {}).get("name") for s in schemas
                 if isinstance(s, dict)]
        names = [n for n in names if n]
        if names:
            return names
    return []


def _mix_table(counter: Counter, total: int, head: str) -> str:
    lines = [f"| {head} | Trajectories | Share |", "| --- | ---: | ---: |"]
    for key, count in counter.most_common():
        lines.append(f"| `{key}` | {count} | {_pct(count, total)}% |")
    return "\n".join(lines)


def _front_matter(pretty_name: str, license_id: str, tags: list[str],
                  size_cat: str, has_security: bool) -> str:
    task_categories = ["text-generation"]
    if has_security:
        task_categories.append("question-answering")
    lines = ["---", f"pretty_name: {pretty_name}", f"license: {license_id}",
             "language:", "  - en", "annotations_creators:",
             "  - machine-generated", "task_categories:"]
    lines += [f"  - {cat}" for cat in task_categories]
    lines += ["size_categories:", f"  - {size_cat}", "tags:"]
    lines += [f"  - {tag}" for tag in tags]
    lines.append("---")
    return "\n".join(lines)


def build_card(rows: list[dict]) -> str:
    publish = CONFIG.get("publish", {})
    teacher = CONFIG.get("teacher", {})
    judge = CONFIG.get("judge", {})

    trajectories = _trajectories(rows)
    total_traj = len(trajectories)
    total_rows = len(rows)
    row_splits = Counter(row.get("split") for row in rows)
    traj_splits = Counter(entry["split"] for entry in trajectories.values())
    categories = Counter(entry["category"] for entry in trajectories.values())
    languages = Counter(entry["lang"] for entry in trajectories.values())
    domains = Counter(entry["domain"] for entry in trajectories.values())
    step_counts = [entry["steps"] for entry in trajectories.values()
                   if entry["steps"]]
    used_tools = sorted({tool for entry in trajectories.values()
                         for tool in entry["tools_used"]})
    offered_tools = _offered_tools(rows)
    attested = sum(1 for row in rows if row.get("model_attested"))
    attest_pct = _pct(attested, total_rows)
    has_security = domains.get("security", 0) > 0

    teacher_model = teacher.get("model", "the teacher model")
    provider = (rows[0].get("provider") if rows else None) \
        or teacher.get("provider") or "the serving provider"
    teacher_runtime = teacher.get("runtime", "pi")
    reasoning = teacher.get("reasoning", "max")
    judge_model = judge.get("model", "an independent reviewer")
    judge_runtime = judge.get("runtime", "codex")
    model_display = _display_model(teacher_model)

    default_pretty = (f"{model_display} Coding & Defensive-Security Agent Traces"
                      if has_security
                      else f"{model_display} Coding & Debugging Agent Traces")
    pretty_name = publish.get("pretty_name") or default_pretty
    license_id = publish.get("license") or "cc-by-4.0"
    hub_id = publish.get("hf_dataset") or "<namespace>/<dataset>"

    tags = list(BASE_TAGS) + _model_tags(teacher_model, provider)
    if has_security:
        tags += [t for t in SECURITY_TAGS if t not in tags]
    size_cat = _size_category(total_rows)

    seed_count = sum(1 for child in SEEDS_DIR.iterdir() if child.is_dir()) \
        if SEEDS_DIR.exists() else 0
    lang_list = ", ".join(f"`{lang}`" for lang, _ in languages.most_common())
    step_line = (f"{min(step_counts)}–{max(step_counts)} "
                 f"(median {int(median(step_counts))})") if step_counts else "n/a"

    # One unbroken sentence: Markdown collapses newlines, but keeping the computed
    # counts on a single logical line keeps the raw card clean and greppable.
    snapshot = (
        f"**This snapshot:** {total_rows:,} next-step rows across "
        f"{total_traj:,} accepted trajectories "
        f"(train {traj_splits.get('train', 0):,} / "
        f"val {traj_splits.get('val', 0):,} trajectories; "
        f"{row_splits.get('train', 0):,} / {row_splits.get('val', 0):,} rows). "
        f"Model-attested: **{attest_pct}%**. Drawn from a corpus of "
        f"{seed_count:,} verifiable repair seeds. Regenerate this card from the "
        f"published rows with `python3 moonshiner.py card`.")

    schema_rows = "\n".join(
        f"| `{name}` | {dtype} | {desc} |" for name, dtype, desc in COLUMN_DOCS)

    security_bullet = (
        "\n- **Held-out security grading.** Security trajectories are graded "
        "against hidden reference findings (CWE/OWASP mapping and path-line "
        "recall); the teacher never sees the answer key, and hostile fixtures "
        "run under a bubblewrap sandbox."
        if has_security else "")

    security_program = (
        "\n- **defensive security** — blind vulnerability findings and repair "
        "chains, rejection-sampled and graded against a firewalled answer key."
        if has_security else "")

    security_limit = (
        "\n- **Authorization scope.** Security material is for defensive use — "
        "detection, secure coding, and code review. It documents classes of "
        "weakness, not exploitation playbooks."
        if has_security else "")

    intended_security = (
        "\n- Training or evaluating **defensive secure-coding and code-review** "
        "agents." if has_security else "")

    return f"""{_front_matter(pretty_name, license_id, tags, size_cat, has_security)}

# {pretty_name}

Real, end-to-end **agentic coding trajectories** produced by
`{teacher_model}` driving the **{teacher_runtime}** coding-agent runtime over
`{provider}`, at `{reasoning}` reasoning. Each trajectory solves a concrete
repair or build task in a real repository — reading, editing, and running code
with tools — and is kept only after its work **verifiably passes** and clears an
independent screen by **{judge_model}** (`{judge_runtime}`). Rows are exported as
**cumulative next-step prefixes**: one training row per assistant turn, each
carrying the full prior context and targeting exactly the next action.

<!-- screened-snapshot:start -->
{snapshot}
<!-- screened-snapshot:end -->

## What makes it different

- **Every trajectory verifiably solved its task.** Acceptance tests pass and the
  protected test/spec files are unmodified (hash-checked) before a trace is ever
  eligible — no self-reported success.
- **Independent judge.** A separate reviewer (`{judge_model}`) screens each
  trajectory for quality; the teacher does not grade itself.
- **Attested provenance.** {attest_pct}% of rows are model-attested: both the
  agent event stream *and* a host-side loopback proxy observed the upstream
  answering as the declared model — a sandbox cannot self-certify its identity.
- **Runtime-normalized.** Native runtime traces are converted to a single
  OpenAI-style `messages` + `tools` schema, so the rows are agent-agnostic.
- **Full offered tool surface.** Every row's `tools` lists the complete action
  space the teacher had, not only the tools a given trajectory happened to call.
- **Privacy-scrubbed.** Host paths, runtime scratch directories, and credential
  patterns are redacted; reference answers and provider keys are gated out of the
  export and fail the build if present.{security_bullet}

## Task program

Accepted trajectories by category (one count per whole trajectory, not per row):

{_mix_table(categories, total_traj, "Category")}

Spanning domains:

{_mix_table(domains, total_traj, "Domain")}
{security_program}

Multi-step depth: assistant turns per trajectory range {step_line}.

## Languages

{lang_list}

## Tool surface

Offered to the teacher on every task: {', '.join(f'`{t}`' for t in offered_tools) or 'n/a'}.
Exercised across the corpus: {', '.join(f'`{t}`' for t in used_tools) or 'n/a'}.

## Schema

| Column | Type | Description |
| --- | --- | --- |
{schema_rows}

## Layout

A single file, `traces.jsonl`, holds every row. For each source trajectory the
export emits an ordered, gap-free sequence of **cumulative prefixes**: step *k*
is exactly the first *k* assistant turns with all intervening user/tool
messages, and its final message (`target_message_index`) is the assistant action
to learn. All rows from one trajectory stay on the **same side** of the
train/val split, so no prefix leaks across the boundary. The export is validated
for exact-prefix continuity and split-disjointness before publication.

## Provenance & reproducibility

- **Seeds.** Tasks are real repair fixtures (prompt, verify command, protected
  test files, reference fix) imported with a deterministic precedence — a
  canonical source, falling back to a secondary only where the canonical lacks a
  seed. The reference fix is used **only** to prove solvability and is never
  exported.
- **Teacher.** `{teacher_model}` via `{teacher_runtime}` over `{provider}`,
  `{reasoning}` reasoning. The real provider key stays host-side behind a
  loopback proxy; the sandboxed agent receives only a dummy token.
- **Screening.** Deterministic gates (acceptance tests + protected-file hashes)
  then an independent `{judge_model}` review; only trajectories that clear both
  are built. Standing rejections may be retraced and re-screened.
- **Derivation.** `{rows[0].get('derivation', 'cumulative-next-step-prefixes')}`
  — validated for exact cumulative-prefix continuity and trajectory-disjoint
  splits at export time.

## Limitations

- **Success-filtered.** Only solved, screened trajectories are included, so the
  data reflects successful problem-solving, not the full distribution of attempts
  or failure recovery.
- **Judge bias.** Screening reflects the judge model's preferences; a second
  model's blind spots carry through.
- **Cumulative prefixes.** Rows from one trajectory share context and are highly
  correlated; treat the **trajectory** (`source_trajectory_id`), not the row, as
  the independent unit for held-out splits or de-duplication.
- **Reasoning is as-emitted.** Only reasoning the teacher surfaced in-stream is
  present; no hidden chain-of-thought is reconstructed.{security_limit}

## Intended use

- **SFT / distillation** of agentic coding models on verified, multi-step,
  tool-using trajectories.
- Studying **next-step planning** and tool-use behavior in real repositories.
- Behavioral analysis of a strong teacher under deterministic verification.{intended_security}

## License

Released under **{license_id}**. If you use this dataset, please attribute it:

> {pretty_name} — https://huggingface.co/datasets/{hub_id}
"""


def main() -> None:
    rows = _load_rows(TRACES)
    CARD.parent.mkdir(parents=True, exist_ok=True)
    CARD.write_text(build_card(rows))
    print(f"wrote {CARD}: dataset card for {len(rows):,} rows "
          f"({len(_trajectories(rows)):,} trajectories)")


if __name__ == "__main__":
    main()
