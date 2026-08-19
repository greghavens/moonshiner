---
name: moonshiner-runner
description: Configure, diagnose, run, monitor, and export Moonshiner seed-authoring and coding-trace pipelines. Use when an agent is asked to operate Moonshiner, produce judged traces, author or repair seeds, control model usage, inspect a run, or build the accepted dataset.
---

# Moonshiner Runner

Operate Moonshiner through `moonshiner`; do not manually coordinate its internal scripts or edit trace state.

## Before metered work

1. Run `moonshiner doctor`.
2. Configure roles when requested:
   `moonshiner config role <trace-author|trace-judge|seed-author|seed-judge> <runtime> <model> [reasoning]`.
3. For keyed providers, use `moonshiner auth set <provider>` (for example, `openrouter`). Never put a key in argv, repository files, logs, or chat.
4. Dry-run the exact command and report its seed, attempt, and model-call ceilings.

Never add `--all`, `--yes`, raise a limit, worker count, or attempt ceiling unless the user explicitly authorized that scope. Bare `moonshiner` starts the queues already enabled by the project configuration; `moonshiner run` remains a bounded diagnostic interface.

## Trace workflow

Use `moonshiner run --dry-run` for a smoke plan. For an authorized batch:

```bash
moonshiner run --limit 20 --max-attempts 3 --yes
```

Add `--detach` for a long run that must survive the current agent or terminal session.

The command generates, deterministically verifies, judges, and retraces substantive rejections. It re-judges malformed verdicts without buying a replacement trace. Candidate rejection is ordinary run state; infrastructure failure is not.

A seed blocked on infrastructure stops being selected and keeps its workspace, so it waits for the cause to be repaired instead of burning its attempts. `moonshiner trace repair-infrastructure` sorts the blocked seeds by cause — a missing tool, exhausted provider credit, an unreadable workspace, a runtime that will not start — proves each cause repaired before it touches anything, and requeues only what it proved. Run it without `--yes` first: it names every class and what it found. Content-filtered seeds are never requeued; a refusal is a property of the prompt, so a rerun only buys another one at full price.

Inspect with `moonshiner status` and `moonshiner inspect <run-id>`. Prefer `--json` when another program or agent consumes the result.

## Local model traces with distribution capture

The `vllm` runtime traces against a local OpenAI-compatible server:

```bash
moonshiner config role trace-author vllm <model>
moonshiner config set runtimes.vllm.base_url http://127.0.0.1:8000/v1
```

For self-distillation, capture the teacher's token distributions. Capture is opt-in and never degrades silently: if the server's `--max-logprobs` is below the configured `top_k`, the attempt fails and names the flag. Start vLLM with `--max-logprobs` at least as large as `top_k` before enabling it:

```bash
moonshiner config set runtimes.vllm.logprobs.enabled true
moonshiner config set runtimes.vllm.logprobs.top_k 100
```

Distributions are written as one Parquet sidecar per trajectory, joined to published rows by `source_trajectory_id` and `assistant_step`, and published under `logprobs/` with `logprobs/MANIFEST.json`. `moonshiner dataset analyze` counts their bytes, so expect storage totals well above the row text alone.

A distillation run may also want the teacher's failures and its unchanged reasoning effort. Both settings default to normal behavior. Confirm with the user before changing either: with judging skipped, a trace is accepted with no independent review behind it.

```bash
moonshiner config set pipeline.trace.skip_judging true
moonshiner config set pipeline.trace.step_down_reasoning_on_failure false
```

## Seed workflow

Author one new seed with an explicit unique id and brief:

```bash
moonshiner seed run --id <id> --brief '<objective>' --dry-run
moonshiner seed run --id <id> --brief '<objective>' --yes
```

The candidate remains outside `tasks/seeds/` until deterministic validation and the writable seed judge both accept it. Never replace or delete an existing seed. An exhausted candidate remains under `tasks/candidates/<run-id>/` for inspection.

## Dataset workflow

Normal operation automatically formats, privacy-checks, appends, publishes, and remotely verifies accepted traces. `moonshiner dataset build` is an advanced rebuild/diagnostic command. Do not bypass export validation or manually mark a trace accepted.

Use `moonshiner pipeline --dry-run` only for advanced access to the legacy phase runner. It is not the normal metered entry point.
