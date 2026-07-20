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

## Start

Open the directory where you want Moonshiner to keep this project's configuration and data, then run:

```bash
moonshiner
```

On the first run, Moonshiner confirms the current directory and asks for anything it needs:

- which harness and model should create traces;
- which harness and model should judge traces;
- which harnesses and models should author and judge seeds, when seed authoring is enabled;
- which provider Pi should use;
- the provider credential, when that provider requires one;
- the Hugging Face dataset target, when publishing is enabled.

Codex and Claude Code can use their existing login sessions. Pi is a harness, so Moonshiner asks which provider Pi should call and configures the matching endpoint, protocol, model, and credential.

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
moonshiner config role trace-author pi anthropic/claude-fable-5 max
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
moonshiner config set pipeline.trace.max_attempts 2
```

Set Hugging Face publication batch size:

```bash
moonshiner config set publish.batch_size 10
```

These settings are reread between work items. Reducing concurrency does not cancel active model calls.

## Resume existing work

Import an existing directory of traces or prepared rows:

```bash
moonshiner trace import --directory /path/to/existing-data
```

Import an existing Hugging Face dataset:

```bash
moonshiner trace import --hf owner/dataset --revision COMMIT
```

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

Combine local data with revision-pinned Hugging Face datasets:

```bash
moonshiner dataset compose --source local:/data/private.jsonl --source hf:HuggingFaceH4/ultrachat_200k@COMMIT#train_sft --out /data/prepared/train.jsonl
```

Select rows by name, category, or training tag:

```bash
moonshiner dataset compose --source local:/data/all.jsonl --include-category 'tool-*' --include-tag parallel-tool-calls --exclude-tag sensitive --out /data/prepared/selected.jsonl
```

Prepare trainer configuration:

```bash
moonshiner dataset prepare --trainer axolotl --input /data/prepared/train.jsonl --model organization/model --out /data/prepared/axolotl.json
```

Moonshiner normalizes mixed conversation formats, scrubs private data, deduplicates rows, records provenance, and writes a reproducible composition manifest.

## Hugging Face publishing

Set the target dataset:

```bash
moonshiner config set publish.hf_dataset owner/dataset
```

Log in with the Hugging Face CLI or save the token through Moonshiner. Accepted trajectories are appended locally and published in configured batches. Dataset-card counts and percentages regenerate from the exact published rows.

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
