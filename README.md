# moonshiner

Moonshiner authors and judges deterministic coding seeds, produces coding-agent traces from those seeds, requests replacement traces when quality review rejects them, and builds accepted traces into training datasets.

The normal interface is the installed `moonshiner` command. Runs are bounded by default and recorded under Moonshiner's storage root; generated artifacts and credentials stay out of Git. The bundled seeds are never replaced by pipeline commands.

## Install

Release wheels are built by GitHub Actions; users do not build Moonshiner.

```bash
python3 -m pip install moonshiner
# or install a verified GitHub release into ~/.local:
curl -fsSL https://raw.githubusercontent.com/greghavens/moonshiner/main/install.sh | bash
moonshiner --version
```

PyPI publishing uses trusted publishing from the tagged release workflow. Before enabling it, the repository owner must create (or reserve through PyPI's pending-publisher flow) the `moonshiner` project, authorize this repository's `release.yml` workflow in the `pypi` environment, then set the GitHub Actions repository variable `PYPI_TRUSTED_PUBLISHING=enabled`; no API token is stored in GitHub. GitHub release wheels, source archives, checksums, and build attestations are produced from `v*` tags. The curl installer verifies the wheel against the release checksum before installation. Flatpak is not offered yet: this is a CLI that must invoke separately installed model CLIs, and Flatpak confinement would make that integration misleadingly brittle.

## Quick start

```bash
moonshiner
```

That is the complete first-run command. Moonshiner asks for any missing model, authentication, and storage settings, saves them, and starts a safe one-seed run. On later invocations, bare `moonshiner` immediately starts another one-seed run. Use `moonshiner setup` to change the answers or `moonshiner --help` to see the other normal tasks.

A bare invocation processes exactly one seed, with at most two author attempts. It generates a trace, runs local verification and deterministic replay gates, asks the configured independent judge, and requests a new trace only after a substantive rejection.

For a deliberate batch:

```bash
moonshiner run --limit 20 --max-attempts 2 --max-calls 80 --yes
moonshiner status
moonshiner inspect <run-id>
moonshiner dataset build
```

Add `--detach` to launch the same bounded command in a durable background scope; it prints the log path. The SQLite status commands remain authoritative.

`--all` is explicit and multi-seed metered work requires `--yes`. `--max-calls` is a hard combined author/judge call ceiling. Judge-format failures re-review the existing trace; they do not trigger another author call.

## Configure models

Repository-local choices are written to the gitignored `config.local.json`:

```bash
moonshiner config role trace-author pi moonshotai/kimi-k3 max
moonshiner config role trace-judge codex gpt-5.6-sol xhigh
moonshiner config role seed-author claude-code claude-opus-4-6 high
moonshiner config role seed-judge codex gpt-5.6-sol high
moonshiner config show
```

Configuration layers, lowest to highest priority, are `config.json`, the user file at `$XDG_CONFIG_HOME/moonshiner/config.json`, and repository `config.local.json`. Nested values are deep-merged.

Any individual setting can also be changed directly:

```bash
moonshiner config set pipeline.trace.max_attempts 2
moonshiner config get teacher.model
```

Installed packages default to `${XDG_DATA_HOME:-~/.local/share}/moonshiner`. Choose another durable location either during curl installation or later:

```bash
curl -fsSL https://raw.githubusercontent.com/greghavens/moonshiner/main/install.sh | bash -s -- --storage /mnt/training/moonshiner
moonshiner storage set /mnt/training/moonshiner
moonshiner storage status
```

`MOONSHINER_HOME` is the one-invocation/CI override. The storage root contains runs, trace artifacts, datasets, and installed seed-corpus versions. Credentials remain in the user configuration directory, not the data root.

## Authentication

Codex and Claude Code use their CLI account authentication. OpenAI-compatible Pi providers use the runtime's configured key environment variable or Moonshiner credential file:

```bash
moonshiner auth set pi
moonshiner auth status pi
moonshiner auth remove pi
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
moonshiner seed run \
  --id py-example-defect \
  --brief 'Create a focused offline Python repair task about incorrect cache eviction' \
  --dry-run

moonshiner seed run \
  --id py-example-defect \
  --brief 'Create a focused offline Python repair task about incorrect cache eviction' \
  --yes
```

The seed author creates `task.json`, `files/`, protected tests, and `reference_fix.patch` in an isolated candidate workspace. Moonshiner proves that the baseline fails twice, the reference patch applies without touching tests, the fix passes twice, reversing it restores the failure, and the workspace cleans up. A writable independent seed judge may repair prompt/test mismatch, weak tests, the reference patch, or nondeterminism. The repaired on-disk candidate is validated again and promoted only after a fully clear verdict.

An existing `tasks/seeds/<id>` is never overwritten. Rejected candidates remain under `tasks/candidates/<run-id>/`.

## Dataset and advanced commands

```bash
moonshiner dataset build       # build through validated HF staging/card
moonshiner phases              # list low-level phases
moonshiner pipeline --dry-run  # advanced legacy phase runner
moonshiner generate --help     # legacy standalone phase access
```

Compose local JSON/JSONL with one or more revision-pinned Hugging Face datasets, then emit generic chat JSONL or an Axolotl configuration:

```bash
moonshiner dataset compose \
  --source local:/data/private.jsonl \
  --source hf:HuggingFaceH4/ultrachat_200k@<commit>#train_sft \
  --weight 2 --weight 1 \
  --out /data/prepared/train.jsonl
moonshiner dataset prepare --trainer axolotl \
  --input /data/prepared/train.jsonl --model org/model \
  --out /data/prepared/axolotl.json
```

Composition can select rows by task/name, category, or tag. Values are repeatable shell-style globs; all requested include dimensions must match, while any exclusion wins:

```bash
moonshiner dataset compose --source local:/data/all.jsonl \
  --include-category 'code-*' --include-tag verified \
  --exclude-name 'internal-*' --exclude-tag sensitive \
  --out /data/prepared/selected.jsonl
```

Tags describe trained behavior rather than duplicating categories. Seeds may declare `training_tags` in `task.json`; accepted trace rows also derive `tool-use`, `parallel-tool-calls`, `multi-turn`, `iterative-repair`, and `tool:<name>` tags from what actually happened. The catalog displays explicit seed tags, while dataset manifests preserve the exact behavioral filter policy.

Hugging Face revisions are mandatory for reproducibility. Composition normalizes common conversation formats, deterministically samples and deduplicates rows, scrubs live credentials, token-like values, emails, and host identifiers, and records a content manifest. Axolotl can consume datasets itself, but Moonshiner's preparation layer is where mixed-source normalization, privacy enforcement, provenance, and deterministic composition occur.

Publishing is deliberately separate and interactive-by-flag:

```bash
moonshiner publish --yes
```

The final publisher validates the staged export and scans every upload file for credentials and user-identifying host data. The former continuous preview publisher is disabled so partially reviewed traces cannot leak to the Hub.

## Seed library releases

The application and seed corpus have independent versions. `seeds-*` tags publish a checksummed corpus archive without changing the application package:

```bash
moonshiner seeds status
moonshiner seeds catalog              # Markdown recipe book
moonshiner seeds catalog --json       # agent-friendly catalog
moonshiner seeds list
moonshiner seeds update
moonshiner seeds verify
```

Installed corpus releases are immutable under the storage root, and the selected release is copied to `corpora/active`. Source checkouts continue using all tracked `tasks/seeds/`; none are deleted during release or installation.

Seed identifiers are immutable. Import and authoring commands skip or reject an existing identifier; updating a recipe requires a new seed id and a new corpus version.

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
