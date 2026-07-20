# moonshiner

Moonshiner authors and judges deterministic coding seeds, produces coding-agent traces from those seeds, requests replacement traces when quality review rejects them, and builds accepted traces into training datasets.

The normal interface is the installed `moonshiner` command. It supervises every queue enabled for the current project; generated artifacts and credentials stay out of Git. The bundled seeds are never replaced by pipeline commands.

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

That is the complete first-run command. Moonshiner confirms the current directory, asks for missing model and authentication settings, then starts all configured durable queues: optional seed authoring/judging, trace/judge/retrace workers, formatting/privacy validation, append-only dataset updates, batched HF publication, and remote verification.

Each directory is independent: `cd` into another project and run `moonshiner` to create a separate configuration and output set. Provider credentials remain in the protected user credential store and are not copied into project directories.

Queues resume their ledgers rather than duplicating completed work. Advanced `run` commands remain available for explicitly bounded subsets and diagnostics.

For a deliberate batch:

```bash
moonshiner run --limit 20 --max-attempts 3 --yes
moonshiner status
moonshiner inspect <run-id>
moonshiner dataset build
```

Select recipes directly from the catalog. Filters compose, and `--tag` may be
repeated when every listed behavior must be present:

```bash
moonshiner seeds catalog --kind behavior --category parallel-same
moonshiner run --kind behavior --category parallel-same --tag execution:parallel --limit 20 --dry-run
moonshiner run --kind behavior --tag execution:parallel --tag tool:argument-grounding --limit 20 --yes
moonshiner run --kind behavior --name "calendar" --limit 20 --yes
moonshiner run --only behavior-parallel-same-0001 --yes
```

Behavior traces use the fictional tool schemas and deterministic state declared
by each recipe. Exact tool-stage validation runs first; an independent configured
judge must then accept the response. A substantive rejection requests a fresh
Fable trace. Coding and behavioral recipes never share a verifier.

Add `--detach` to launch the same bounded command in a durable background scope; it prints the log path. The SQLite status commands remain authoritative.

Trace workers atomically lease different seeds. Each seed remains serial—trace,
judge, then retrace only after substantive rejection—while unrelated seeds may
run concurrently. Change concurrency live; active paid calls always finish:

```bash
moonshiner config set pipeline.trace.workers 4
moonshiner config set pipeline.trace.workers 1
```

Scaling down drains excess workers between seeds. Crashed leases are recovered,
and the shared model-call ceiling is reserved transactionally.

`--all` is explicit and multi-seed metered work requires `--yes`. Each seed is an independent trace job with its own attempt limit. Judge-format failures re-review the existing trace; they do not trigger another author call.

## Configure models

Project choices are written to the current directory's gitignored `.moonshiner/config.json`:

```bash
moonshiner config role trace-author pi moonshotai/kimi-k3 max
moonshiner config role trace-judge codex gpt-5.6-sol xhigh
moonshiner config role seed-author claude-code claude-opus-4-6 high
moonshiner config role seed-judge codex gpt-5.6-sol high
moonshiner config show
```

Built-in defaults come from the installed `config.json`; `.moonshiner/config.json` overrides them for only the current directory. Nested values are deep-merged.

Any individual setting can also be changed directly:

```bash
moonshiner config set pipeline.trace.max_attempts 2
moonshiner config set pipeline.trace.workers 4
moonshiner config set publish.batch_size 10
moonshiner config get teacher.model
```

Configuration and output default to `.moonshiner/` in the current directory:

```bash
moonshiner storage status
```

To use another project location, change to that directory and run `moonshiner` there. Credentials remain in the protected user configuration directory, not the project data root.

## Authentication

Codex and Claude Code use their existing CLI account authentication. Pi is a harness, not a provider: setup asks which API provider Pi should call and configures its endpoint, protocol, model, and provider-specific credential. Provider keys can also be managed directly:

```bash
moonshiner auth set openrouter
moonshiner auth status openrouter
moonshiner auth remove openrouter
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

To take over an existing pre-Moonshiner run, import either a local trace/data
directory or a Hugging Face dataset. Imported task IDs are added to the resume
index, so normal trace runs skip completed work. Native Moonshiner
`traces/{raw,meta,reviews,diffs}` artifacts are preserved; prepared `messages`
JSONL rows are privacy-scrubbed, deduplicated, and included in later builds.

```bash
moonshiner trace import --directory /data/pre-moonshiner-run
moonshiner trace import --hf owner/dataset --revision <commit>
```

Hugging Face import requires `pip install 'moonshiner[huggingface]'`. Re-importing
the same source adds newly completed rows without duplicating old ones. Reusing
a label for a different origin, or importing conflicting native artifacts,
fails closed instead of silently replacing the earlier import.

For the configured publication target, the canonical `traces.jsonl` is
local-first and append-only. On the first trace run, Moonshiner downloads the
remote file only when the canonical local file is absent. It then records the
remote revision and appends accepted exports locally on later runs without
checking the Hub again. Enable an explicit pre-append revision check with:

```bash
moonshiner config set publish.check_before_append true
```

An existing trajectory-step identity can be skipped when byte-equivalent, but
it can never be replaced with different content. Export journals and bootstrap
metadata are retained outside the upload directory.

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

The independent publication queue consumes only deterministically accepted,
hash-pinned reviews. It formats and privacy-scans rows, appends without replacing
existing data, uploads the configured number of trajectories per commit, and
verifies the commit remotely. `publish.batch_size` is reread between batches,
so it can be changed while tracing continues. A final partial batch flushes when
tracing completes. `moonshiner publish --yes` remains an advanced manual repair
command, not part of normal operation.

## Seed library releases

Moonshiner also ships a separate 1,000-seed non-code tool-behavior curriculum
covering function selection, native parallel calls, dependency planning,
multi-turn state, clarification, missing tools, abstention, recovery, web
research, memory, and format robustness. Its exact allocation and planned
2K/4K/8K expansion rounds are in
[`docs/BEHAVIOR_SEED_ROADMAP.md`](docs/BEHAVIOR_SEED_ROADMAP.md).

Behavioral authoring is separate from coding seed authoring:

```sh
moonshiner config role seed-author codex gpt-5.6-sol xhigh
moonshiner behavior-seed author --all --batch-size 20 --yes --detach
moonshiner behavior-seed status
```

That command selects only `tool_behavior` seeds and resumes from its completed
batch ledger. It cannot spend the run authoring coding-repair seeds.

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
