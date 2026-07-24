# Moonshiner

Seed authoring, trace distillation, and training dataset preparation for coding-agent models.

## Project structure

- `moonshiner.py` — CLI entry point and pipeline orchestrator
- `src/` — all pipeline modules (import_seeds, generate_traces, screen_traces, build_dataset, etc.)
- `src/runtimes/` — harness adapters: `pi.py`, `claude_code.py`, `codex.py`, `base.py`
- `src/moonshiner_app/` — installed package (`cli.py`, `__init__.py`)
- `src/configuration.py` — config loading; `config.json` (defaults), `config.local.json` (overrides)
- `schemas/` — JSON schemas
- `tasks/seeds/`, `tasks/retired-seeds/` — seed corpus
- `tests/` — pytest test suite
- `scripts/` — batch/utility scripts
- `.moonshiner/` — per-project runtime state (database, runs, traces, HF sync)

## Build and test

```bash
pip install -e .              # editable install
pytest                        # full test suite
pytest tests/test_foo.py      # single file
moonshiner doctor             # runtime preflight check
```

Python >= 3.11. Dependencies: `huggingface-hub`, `pyarrow`.

## Common commands

```bash
moonshiner                    # start all configured queues (setup on first run)
moonshiner status             # pipeline status (seeds, traces, publishing, services)
moonshiner status --json      # machine-readable status
moonshiner run --limit N --yes  # trace N seeds
moonshiner seed run --id NAME --brief "..." --yes  # author one seed
moonshiner config show        # show merged config
moonshiner service logs NAME  # service logs
moonshiner service stop NAME  # stop a service
moonshiner service restart NAME
```

## Pipeline phases (in order)

import → audit → validate* → generate → screen → retry* → build → expand → export → export-next → verify-export → parquet → card → prepare*

Phases marked `*` are optional (enabled via `--with`). Metered phases (generate, screen, retry) call teacher/judge models.

## Architecture

One pipeline, one code path for all models. Model identity only affects configuration (which harness adapter, which model ID). The harness adapter runs the real agent and normalizes its native trace into Moonshiner's single canonical representation. No model-specific queues, formatters, validators, or publishers.

Three queues run as systemd user services:
- Seed authoring queue
- Trace continuous queue (trace → judge → retry → format → publish)
- Synthetic corrections queue (opt-in companion)

## Key invariants

- Seed prompts reach the harness byte-for-byte unchanged — no wrapping, annotation, or enrichment.
- Every trace is a native trace from the configured harness with genuine tool execution.
- Only the configured trace judge may reject a trace.
- A trace gets at most its configured per-seed maximum attempts (default 3) across all resumptions.
- Reasoning step-down cycle: xhigh → medium → low, repeating if max_attempts > 3.
- Production runs use only published, versioned releases via the `moonshiner` command.
- Web research traces use real searches and real fetches — never simulated.

## Configuration

`config.json` has defaults. `config.local.json` has per-project overrides (created by `moonshiner setup`). Key paths:

- `teacher.runtime` / `teacher.model` — trace author
- `judge.runtime` / `judge.model` — trace judge
- `pipeline.trace.workers` — parallel trace workers (1–64)
- `pipeline.trace.max_attempts` — per-seed attempt limit
- `pipeline.queues.seed_authoring` — enable seed queue
- `pipeline.queues.tracing` — enable trace queue
- `synthetic_corrections.enabled` — enable correction companion
- `publish.hf_dataset` — target Hugging Face dataset
- `publish.batch_size` — upload after N accepted traces

## Skills

The operational skill `moonshiner-runner` lives in two places and both must stay in sync:

- `skills/moonshiner-runner/SKILL.md` — for Codex and other coding agents
- `.claude/skills/moonshiner-runner.md` — for Claude Code

When updating the skill, edit both files.

## Shell restrictions

During operation and maintenance, use the `moonshiner` CLI for all pipeline operations. Do not directly invoke Python modules, manipulate the SQLite database, or hand-build systemd commands. Use repository editing tools for source changes.
