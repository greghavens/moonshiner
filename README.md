# Moonshiner

Moonshiner authors seeds, creates model traces, judges every trace, retries rejected traces, prepares training data, and publishes accepted trajectories to Hugging Face.

## Install

With pip:

```bash
python3 -m pip install moonshiner
```

With the installer:

```bash
curl -fsSL https://raw.githubusercontent.com/greghavens/moonshiner/main/install.sh | bash
```

## Quick start

After installation, run:

```bash
moonshiner
```

That is the complete quick start. On the first run, Moonshiner confirms where to keep the project's configuration and data, asks for your preferences and anything else it needs, saves the configuration, and starts the pipeline.

Moonshiner asks:

- which harness and model should create traces;
- which harness and model should judge traces;
- which harnesses and models should author and judge seeds, when seed authoring is enabled;
- which provider Pi should use;
- the provider credential, when that provider requires one;
- the Hugging Face dataset target, when publishing is enabled.
- the Hugging Face publication format when changing advanced setup choices.

Trace and seed sources may use Pi or Codex. Claude Code is supported only as a
judge because its native trace output does not expose readable reasoning text.
Codex and Claude Code can use their existing login sessions. Pi is a harness,
so Moonshiner asks which provider Pi should call and configures the matching
endpoint, protocol, model, and credential.

After setup, `moonshiner` starts every enabled queue for that project. Seed authoring, tracing, judging, retries, formatting, privacy checks, local append-only storage, and Hugging Face publication continue independently. Active model calls are allowed to finish.

## Check progress

```bash
moonshiner status
```

Status reports active workers, authored seeds, accepted and rejected traces, retraces, rates, session progress, and publishing progress.

Check the installation and project configuration:

```bash
moonshiner doctor
```

## Configure

Run the setup assistant again:

```bash
moonshiner setup
```

Show the current project's configuration:

```bash
moonshiner config show
```

Configure a role directly:

```bash
moonshiner config role trace-author pi PROVIDER/MODEL REASONING
```

```bash
moonshiner config role trace-judge codex gpt-5.6-sol xhigh
```

```bash
moonshiner config role seed-author codex gpt-5.6-sol xhigh
```

```bash
moonshiner config role seed-judge codex gpt-5.6-sol xhigh
```

Save a provider credential:

```bash
moonshiner auth set openrouter
```

Moonshiner stores credentials outside the project directory and removes credentials, user keys, email addresses, and host-identifying paths from data before publication.

## Project storage

Each working directory is a separate Moonshiner project. Configuration and output default to `.moonshiner/` in the current directory.

```bash
moonshiner storage status
```

To use another location, change to that directory and run `moonshiner`. Moonshiner asks you to confirm the directory before creating the project configuration.

## Seeds

Browse the seed catalog:

```bash
moonshiner seeds catalog
```

Search the catalog by category, name, or training tag:

```bash
moonshiner seeds catalog --category parallel-same
```

```bash
moonshiner seeds catalog --name calendar
```

```bash
moonshiner seeds catalog --tag execution:parallel
```

Author and review one seed:

```bash
moonshiner seed run --id calendar-reschedule --brief "Reschedule independent appointments while preserving all constraints" --yes
```

Completed seeds enter the trace queue automatically. Categories and tags organize the catalog and can optionally select training data; they do not create separate trace pipelines.

## Traces

Running `moonshiner` is the normal way to keep tracing continuously. To request a deliberate bounded run:

```bash
moonshiner run --limit 20 --yes
```

Trace workers process separate seeds concurrently. Each seed remains an independent work item: create one trace, judge it, retry it after a judge rejection when attempts remain, then format, scrub, and queue an accepted trace for publication.

Set trace concurrency:

```bash
moonshiner config set pipeline.trace.workers 3
```

Set the maximum attempts for each individual trace:

```bash
moonshiner config set pipeline.trace.max_attempts 3
```

Trace retries step down reasoning effort by default: `xhigh`, then `medium`,
then `low`. Each later attempt runs only after the preceding attempt is rejected,
and the first judge-accepted trace is retained. Attempt counts above three repeat
that cycle. To keep the teacher's configured reasoning effort unchanged across
all trace attempts:

```bash
moonshiner config set pipeline.trace.step_down_reasoning_on_failure false
```

For catalogued coding programs, Moonshiner appends its coding guidance to Pi's
native system prompt by default. It does not replace Pi's native prompt, disable
Pi skills or project context, restrict Pi's native tools, or alter authored seed
turns.

Set Hugging Face publication batch size:

```bash
moonshiner config set publish.batch_size 10
```

Choose the Hugging Face publication format:

```bash
moonshiner config set publish.format parquet-shards
moonshiner config set publish.include_jsonl false
```

Supported values are `jsonl`, `jsonl-hf-parquet`, and `parquet-shards`.
All three publish the same validated canonical rows. `parquet-shards` publishes
both the canonical JSONL and compressed active shards, points the viewer only at
the shards, and commits the JSONL, shards, manifest, and updated dataset card
together. `jsonl-hf-parquet` publishes JSONL for Hugging Face to convert, while
`jsonl` keeps JSONL as the configured source format.
With `parquet-shards`, set `publish.include_jsonl` to `false` to publish only
the card, manifest, banner, and active Parquet shards while retaining the
canonical JSONL locally.

These settings are reread between work items. Reducing concurrency does not cancel active model calls.

Hugging Face keeps old LFS objects in repository history. To preview an
occasional history cleanup while retaining the newest 10 complete, distinct
dataset snapshots:

```bash
moonshiner maintenance prune-hf-history --keep 10
```

The preview lists every retained snapshot and the exact amount eligible for
deletion. After reviewing it, run the permanent history rewrite explicitly:

```bash
moonshiner maintenance prune-hf-history --keep 10 --yes
```

This maintenance action is never automatic. It refuses incomplete snapshots,
preserves LFS objects reachable from tags, pull requests, conversion refs, and
branches besides `main`, and lets an active publish finish before rewriting
history.

## Synthetic corrections companion

Synthetic corrections are optional and disabled by default. They are intended
only for traces that exhausted their normal trace attempts, never passed the
trace judge, and missed an otherwise correct result by a small, obvious defect.
Examples include an omitted tool call, a one-line code fix, or a genuinely
missing small file. Rewritten reasoning, refactoring, broad replanning, invented
work, and unrelated changes are rejected.

Configure the feature:

```bash
moonshiner synthetic-corrections configure
```

The correction harness and model default to the current trace judge. Moonshiner
asks for another provider credential only if the selected runtime needs one and
no credential is already configured. The companion Hugging Face target defaults
to the primary dataset name with `-synthetic-corrections` appended. Each trace
gets at most two correction attempts by default.

Preview eligible work without model calls:

```bash
moonshiner synthetic-corrections run --dry-run
```

Start paid correction processing explicitly:

```bash
moonshiner synthetic-corrections run --yes
```

Moonshiner examines up to three preserved failures for each use case but creates
at most one corrected trajectory. The eligibility reviewer chooses the correctable
failure requiring the smallest valid change; correction retries continue from that
same preserved attempt. A use case is excluded if any current-revision
trace ever passed. The source reasoning must remain unchanged, the correction
must be minimal, and the corrected trace must pass normal verification and the
normal independent trace judge. A judge rejection returns the item to the end of
the correction queue with feedback while attempts remain.

Corrections use the existing judge path and publication queue. Accepted corrections
are routed explicitly to an isolated companion dataset with the same canonical
schema, publication format, generated card, and banner as the primary dataset.
They never enter or modify the primary dataset.

Show correction status:

```bash
moonshiner synthetic-corrections status
```

## Resume existing work

Import an existing directory of traces or prepared rows:

```bash
moonshiner trace import --directory /path/to/existing-data
```

Import an existing Hugging Face dataset:

```bash
moonshiner trace import --hf owner/dataset --revision COMMIT
```

Convert imported prepared rows to Moonshiner's current canonical schema, then
publish them with the project's configured JSONL or Parquet-shard format:

```bash
moonshiner migrate-dataset --yes
```

```bash
moonshiner publish --yes
```

Migration preserves existing messages and tools. It does not generate or infer
missing trace content such as model reasoning.

For the configured Hugging Face target, Moonshiner downloads the remote trace file only when the matching local file does not yet exist. Later accepted traces append to the local canonical file. Existing rows are never replaced.

Enable a remote revision check before each append when you need it:

```bash
moonshiner config set publish.check_before_append true
```

## Build and prepare datasets

Build the accepted local traces into a validated dataset:

```bash
moonshiner dataset build
```

Analyze one or more local or revision-pinned Hugging Face datasets before combining them:

```bash
moonshiner dataset analyze --source local:/data/private.jsonl --source hf:HuggingFaceH4/ultrachat_200k@COMMIT#train_sft
```

With no `--source`, Moonshiner analyzes the configured local append-only dataset. A direct Hugging Face file also works without installing dataset tooling:

```bash
moonshiner dataset analyze --source hf-file:owner/dataset@REVISION/path/to/traces.jsonl
```

You can also paste the file's Hugging Face URL directly:

```bash
moonshiner dataset analyze --source https://huggingface.co/datasets/owner/dataset/blob/REVISION/path/to/traces.jsonl
```

The report compares trajectories, rows, target tokens, total tokens, length distributions, categories, tags, sources, multi-turn conversations, direct responses, sequential tool calls, and parallel tool calls. Add `--tokenizer organization/model` for exact tokenizer counts; otherwise Moonshiner clearly labels its token estimate.

Combine local data with revision-pinned Hugging Face datasets:

```bash
moonshiner dataset compose --source local:/data/private.jsonl --source hf:HuggingFaceH4/ultrachat_200k@COMMIT#train_sft --out /data/prepared/train.jsonl
```

Preview a token-budgeted composition without writing it:

```bash
moonshiner dataset compose --source local:/data/private.jsonl --source hf:owner/dataset@COMMIT#train --target-tokens 50000000 --weight-category 'tool-calling=2' --weight-tag 'execution:parallel=3' --tokenizer organization/model --dry-run
```

Remove `--dry-run` to write the composition after reviewing the reported realized mix. Weight rules use `GLOB=WEIGHT`; category, tag, and source-pattern weights require `--target-tokens`. `--weight-unit` selects whether sampling balances rows, target tokens, or total tokens. No curriculum percentage is hard-coded.

Select rows by name, category, or training tag:

```bash
moonshiner dataset compose --source local:/data/all.jsonl --include-category 'tool-*' --include-tag parallel-tool-calls --exclude-tag sensitive --out /data/prepared/selected.jsonl
```

Review training risks:

```bash
moonshiner dataset readiness --source local:/data/prepared/train.jsonl --tokenizer organization/model --context-length 32768
```

Readiness checks context truncation, empty final answers, duplicate prompts, malformed tool sequences, repetitive reasoning, mixed-language scripts, privacy findings, cumulative trajectory prefixes, and small category shares. Analysis and readiness report privacy finding types and counts without printing the affected row contents. They are advisory only; composition and publication remain fail-closed for privacy findings.

Prepare trainer configuration:

```bash
moonshiner dataset prepare --trainer axolotl --input /data/prepared/train.jsonl --model organization/model --tokenizer organization/model --sequence-len 32768 --out /data/prepared/axolotl.json
```

Packing is off unless `--sample-packing` is supplied. Moonshiner normalizes mixed conversation formats, scrubs private data, deduplicates rows, and writes reproducible composition and trainer manifests. Manifests include pinned Hugging Face revisions or local file hashes, tokenizer accounting, filters, requested and realized mixtures, input and output hashes, trainer configuration, package versions, and the exact trainer command.

Accepted traces also receive observed tags such as `response:direct`, `reasoning:planning`, `reasoning:extended`, `reasoning:self-correction`, `reasoning:verification`, `interaction:multi-turn`, `execution:parallel`, and `format:strict-json`. These describe what the trace actually demonstrated; they are catalog and composition metadata, never queue partitions or acceptance gates.

## Hugging Face publishing

Set the target dataset:

```bash
moonshiner config set publish.hf_dataset owner/dataset
```

Log in with the Hugging Face CLI or save the token through Moonshiner. Accepted trajectories are appended locally and published in configured batches. Dataset-card counts and percentages regenerate from the exact published rows.

Parquet publication keeps `dataset-manifest.json` as the authoritative active
shard list. Replaced trajectories supersede their prior active shard without
losing neighboring trajectories; superseded shard content remains recoverable
from repository history. Every publish commit contains the data artifacts,
manifest, and matching card together.

Manual publication is available for repair or verification:

```bash
moonshiner publish --yes
```

## Seed library

Application releases and seed-catalog releases are versioned separately, so the catalog can grow without requiring an application update.

```bash
moonshiner seeds status
```

```bash
moonshiner seeds update
```

```bash
moonshiner seeds verify
```

Seed identifiers are immutable. Moonshiner never overwrites or removes an existing seed during normal operation.

## Agent use

The repository includes the `skills/moonshiner-runner` skill for agents operating Moonshiner. Agents should use the same installed `moonshiner` commands, project configuration, status output, and released application as human users.

## License

Moonshiner is licensed under the [Apache License 2.0](LICENSE).
