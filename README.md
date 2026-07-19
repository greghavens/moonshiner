# moonshiner

Moonshiner authors and judges deterministic coding seeds, produces coding-agent traces from those seeds, requests replacement traces when quality review rejects them, and builds accepted traces into training datasets.

The normal interface is `./moonshiner` (or `python3 moonshiner.py`). Runs are bounded by default and recorded in `runs/moonshiner.sqlite3`; generated artifacts and credentials stay out of Git. The 893 tracked seeds under `tasks/seeds/` are the corpus and are never replaced by pipeline commands.

## Quick start

```bash
python3 moonshiner.py doctor
python3 moonshiner.py run --dry-run
python3 moonshiner.py run
```

A bare `run` processes exactly one seed, with at most two author attempts. It generates a trace, runs local verification and deterministic replay gates, asks the configured independent judge, and requests a new trace only after a substantive rejection.

For a deliberate batch:

```bash
python3 moonshiner.py run --limit 20 --max-attempts 2 --max-calls 80 --yes
python3 moonshiner.py status
python3 moonshiner.py inspect <run-id>
python3 moonshiner.py dataset build
```

Add `--detach` to launch the same bounded command in a durable background scope; it prints the log path. The SQLite status commands remain authoritative.

`--all` is explicit and multi-seed metered work requires `--yes`. `--max-calls` is a hard combined author/judge call ceiling. Judge-format failures re-review the existing trace; they do not trigger another author call.

## Configure models

Repository-local choices are written to the gitignored `config.local.json`:

```bash
python3 moonshiner.py config role trace-author pi moonshotai/kimi-k3 max
python3 moonshiner.py config role trace-judge codex gpt-5.6-sol xhigh
python3 moonshiner.py config role seed-author claude-code claude-opus-4-6 high
python3 moonshiner.py config role seed-judge codex gpt-5.6-sol high
python3 moonshiner.py config show
```

Configuration layers, lowest to highest priority, are `config.json`, the user file at `$XDG_CONFIG_HOME/moonshiner/config.json`, and repository `config.local.json`. Nested values are deep-merged.

Any individual setting can also be changed directly:

```bash
python3 moonshiner.py config set pipeline.trace.max_attempts 2
python3 moonshiner.py config get teacher.model
```

## Authentication

Codex and Claude Code use their CLI account authentication. OpenAI-compatible Pi providers use the runtime's configured key environment variable or Moonshiner credential file:

```bash
python3 moonshiner.py auth set pi
python3 moonshiner.py auth status pi
python3 moonshiner.py auth remove pi
```

The command reads keys silently and stores them mode 0600 outside the repository. Environment variables remain supported for CI. `scripts/stage_key.sh` remains as a compatibility wrapper for older automation.

## Trace quality pipeline

For each selected seed Moonshiner records immutable run/job/attempt state and executes:

```text
author candidate
  → local verification and protected-test check
  → fresh-workspace patch replay and double verification
  → static scope/safety checks
  → independent read-only judge
      → accept
      → substantive reject → new author trace with structured feedback
      → malformed verdict → re-judge the same trace
```

Accepted traces alone enter dataset construction. A failed candidate does not abort or discard other successful jobs. An infrastructure exception fails the run visibly. Existing legacy artifacts under `traces/` remain compatible with dataset building.

## Seed authoring pipeline

```bash
python3 moonshiner.py seed run \
  --id py-example-defect \
  --brief 'Create a focused offline Python repair task about incorrect cache eviction' \
  --dry-run

python3 moonshiner.py seed run \
  --id py-example-defect \
  --brief 'Create a focused offline Python repair task about incorrect cache eviction' \
  --yes
```

The seed author creates `task.json`, `files/`, protected tests, and `reference_fix.patch` in an isolated candidate workspace. Moonshiner proves that the baseline fails twice, the reference patch applies without touching tests, the fix passes twice, reversing it restores the failure, and the workspace cleans up. A writable independent seed judge may repair prompt/test mismatch, weak tests, the reference patch, or nondeterminism. The repaired on-disk candidate is validated again and promoted only after a fully clear verdict.

An existing `tasks/seeds/<id>` is never overwritten. Rejected candidates remain under `tasks/candidates/<run-id>/`.

## Dataset and advanced commands

```bash
python3 moonshiner.py dataset build       # build through validated HF staging/card
python3 moonshiner.py phases              # list low-level phases
python3 moonshiner.py pipeline --dry-run  # advanced legacy phase runner
python3 moonshiner.py generate --help     # legacy standalone phase access
```

The old broad `run` behavior has moved to `pipeline`. This prevents a casual command from silently authorizing the entire corpus. The security lane and all original import/audit/generate/screen/export phases remain available there.

## Agent use

The repository bundles `skills/moonshiner-runner/`. Install or expose that skill to a coding agent and ask it to operate Moonshiner. The skill requires agents to run `doctor`, dry-run metered work, preserve explicit ceilings, and use structured status instead of parsing logs.

## Offline verification

```bash
scripts/check.sh
```

This byte-compiles the source, runs the model-free unit suite, and audits every tracked seed. It makes no model calls.

## License

Moonshiner is free to copy, use, modify, and fork with attribution to <https://github.com/greghavens/moonshiner>. Datasets and models produced with Moonshiner must also credit Moonshiner. Dataset card generation adds that credit automatically. See `LICENSE`.
