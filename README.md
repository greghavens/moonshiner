# moonshiner

A single, unified harness for **full-model distillation of coding ability**. It
drives a configurable *teacher* coding agent through a corpus of real repair
tasks, keeps only the traces that provably pass a deterministic screen **and** an
independent *judge*, and renders the survivors into a supervised fine-tuning
dataset for a student model.

One codebase, two ways to run it:

- **As one process** — `python3 moonshiner.py run` executes the whole pipeline
  end to end, idempotent and fail-closed.
- **By an AI agent, one phase at a time** — every phase is its own subcommand
  (`python3 moonshiner.py generate --all`, `… screen --all --review`, …) with
  its native arguments, so an agent can drive, inspect, and safely re-run any
  step.

The teacher runtime, its model and reasoning level, and the judge (model and
runtime) are all configurable — a Codex teacher can be judged by Claude, a Pi/GLM
teacher judged by Codex, and so on.

---

## Quickstart

```bash
# Offline sanity gate — byte-compile + unit tests + seed audit. No model, no net.
scripts/check.sh

# See exactly what a full run would do, without running it.
python3 moonshiner.py run --dry-run

# Check that the configured teacher and judge are reachable and authenticated.
python3 moonshiner.py preflight

# Run the whole pipeline, end to end.
python3 moonshiner.py run
```

Everything up to (but not including) trace generation is **offline** — no model
calls, no network — so the seed corpus, audits, dataset assembly, and exports
can all be exercised for free:

```bash
python3 moonshiner.py run --offline          # only phases that call no model
python3 moonshiner.py run --to audit          # import + audit, then stop
python3 moonshiner.py run --offline --with validate   # add the solvability proof
```

---

## The pipeline

`run` executes the non-optional phases in order. Optional phases (a no-model
solvability proof, and a repair lane for rejected traces) fold in only when asked
for with `--with`.

| # | phase | cost | what it does |
|---|-------|------|--------------|
| 1 | `import` | offline | Import the seed corpus (canonical source + fallback) |
| 1.1 | `sec-import`\* | offline | Import the fable-secure security corpus (catalog + held-out keys) |
| 1.2 | `sec-fetch`\* | offline | Hydrate the pinned security-review repositories |
| 2 | `audit` | offline | Audit seed integrity, fail-closed |
| 2.5 | `validate`\* | offline | Prove each seed is solvable, with no model calls |
| 3 | `generate` | **metered** | Drive the teacher to produce candidate traces |
| 3.1 | `sec-generate`\* | **metered** | Drive the teacher over the security corpus (rejection-sampled) |
| 4 | `screen` | **metered** | Screen traces: deterministic gates, then the judge |
| 4.5 | `retry`\* | **metered** | Retrace + rescreen standing rejections |
| 4.9 | `sec-build`\* | offline | Build the security SFT partition (folds into `build`) |
| 5 | `build` | offline | Build the SFT dataset from accepted traces |
| 6 | `expand` | offline | Expand cumulative next-step prefixes |
| 7 | `export` | offline | Export the whole-session HF dataset |
| 8 | `export-next` | offline | Export the next-steps HF dataset |
| 9 | `parquet` | offline | Export Parquet shards |
| 10 | `prepare` | offline | Render rows with the student's chat template for local training |
| 11 | `verify-export` | offline | Validate the export against provenance + privacy gates |
| 11.5 | `card` | offline | Render the Hugging Face dataset card from the published rows |

`\*` optional — folded in only via `--with` (e.g. `--with validate`, `--with retry`,
or the security lane `--with sec-import --with sec-fetch --with sec-generate
--with sec-build`). See [The security lane](#the-security-lane-optional).

Run selection:

```bash
python3 moonshiner.py run --from generate --to screen   # a slice, inclusive
python3 moonshiner.py run --skip parquet                 # drop a phase
python3 moonshiner.py run --continue-on-error            # don't stop at the first failure
python3 moonshiner.py phases                             # list the pipeline
```

`run` is **fail-closed**: a non-zero phase stops the pipeline unless
`--continue-on-error`. Metered phases preflight their runtime and refuse to run
if it is unreachable, unauthenticated, or (for a paid runtime) not
credit-unlocked — nothing is silently skipped.

Each phase is also a standalone subcommand that takes its own arguments and
passes `--help` straight through:

```bash
python3 moonshiner.py generate --all
python3 moonshiner.py screen --all --review
python3 moonshiner.py retry --limit 4
python3 moonshiner.py audit --ids
python3 moonshiner.py screen --help
```

---

## Configuration

Everything is in `config.json`. (This is the harness's own operating config, not
per-run user state.)

### Teacher and judge

```json
"teacher": { "runtime": "codex", "model": "gpt-5.6-sol", "reasoning": "xhigh", "timeout_s": 3600 },
"judge":   { "runtime": "codex", "model": "gpt-5.6-sol", "reasoning": "xhigh", "timeout_s": 1800 }
```

- **runtime** — which coding-agent runtime to drive: `codex`, `claude-code`, or `pi`.
- **model** / **reasoning** — the model and reasoning/effort level for that role.
- The teacher and judge are independent. Point them at different runtimes and
  models to get a genuinely independent review.

### Runtimes

```json
"runtimes": {
  "claude-code": { "cli": "claude", "paid_unlock_required": true },
  "codex":       { "cli": "codex", "sandbox": "danger-full-access", "web_search": "live" },
  "pi":          { "cli": "node_modules/.bin/pi", "provider": "openrouter",
                   "base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY" }
}
```

Each runtime is an adapter under `src/runtimes/` implementing a common interface
(generate a trace, run a read-only review). A trace records the `trace_format`
its runtime produced; on the way back in, `normalize` routes that format to the
matching parser, so the rest of the pipeline is runtime-agnostic.

Supported trace formats: `claude-stream-json`, `codex-exec-events`,
`codex-rollout`, `pi-coding-agent-json-v3`.

### Student, build, source

```json
"student": { "base_model": "unsloth/NVIDIA-Nemotron-3-Super-120B-A12B",
             "output_dir": "~/nemotron-super-finetune/data-moonshiner" },
"build":   { "keep_reasoning": true, "keep_thinking": true, "val_frac": 0.08 },
"source":  { "seed_repository": "../fable-code", "fallback_repository": "../sol-code" },
"holdout_tasks": ["ts-event-emitter", "go-interval-merge"]
```

---

## The seed corpus

Seeds are real repair tasks (a prompt, a verify command, protected test files,
and — for solvability — a reference fix). They are tracked in-tree under
`tasks/seeds/`, imported from two sources with a deliberate precedence:

- **`seed_repository`** (`../fable-code`) is **canonical**. Its version of a seed
  wins whenever it is complete.
- **`fallback_repository`** (`../sol-code`) is used **only** for a seed the
  canonical source lacks or left incomplete (an authoring agent that died
  mid-write, a rejected stub).

This encodes the rule *"canonical unless it is off, then fall back"*. A seed
that is incomplete in **both** sources is reported invalid and never
half-copied. The merge is deterministic and reproducible:

```bash
python3 moonshiner.py import --dry-run   # report provenance without copying
python3 moonshiner.py import --force      # reproduce the merge deterministically
```

A seed is **complete** when its `task.json` parses with every required field,
its `id` matches the directory name, `files/` exists, and every protected
`test_files` entry is present. `audit` additionally requires a non-empty
`reference_fix.patch` (holdout and pre-spec pilot tasks are exempt — their
solvability is proven by held-out evaluation or actual passing traces).

`validate` (optional, offline) goes further and *proves* solvability without a
model: it materializes a fresh workspace, applies the reference fix, and runs the
seed's own verify command.

---

## Screening

A trace is publishable only if it clears both screens.

**Deterministic gates** (fail-closed, in order):

1. **Freshness** — the seed, raw trace, and diff still hash to what the trace's
   metadata pinned. A seed that changed since generation is stale and rejected.
2. **Attestation** — the teacher's model was attested (no silent fallback, no
   safeguard refusal).
3. **Patch replay** — apply the candidate diff to a fresh workspace and run the
   verify command **twice**; both must pass.
4. **Protected files** — the seed's test files are byte-for-byte unchanged.
5. **Static scope** — no prohibited action (git-state mutation, workspace
   escape, `/tmp` use, global install, nested agent, leaked secret) appears in
   the trace.

**Judge** (independent, configurable): the runtime named by `config.judge`
reviews the trace read-only and returns a schema-constrained verdict. Acceptance
requires every review category clear and every stated requirement met.

Rejections feed the optional **repair lane** (`retry`): a rejected trace is
re-run with concrete feedback derived from exactly what the screen or judge
found, then rescreened.

---

## The security lane (optional)

A parallel, opt-in lane distills **defensive** security ability — finding and
classifying vulnerabilities — from the sibling `../fable-secure` corpus. It is
off by default; fold it in with `--with sec-import --with sec-fetch --with
sec-generate --with sec-build`, or run any phase on its own
(`python3 moonshiner.py sec-generate --only sec-answer-42`).

Two case kinds, each with its own held-out grader:

- **Answer cases** — a blind question (classify a snippet by CWE/OWASP, explain
  an attacker primitive, review a diff). The teacher never sees the reference
  answer; a separate low-effort judge grades against it, behind a deterministic
  label-shape gate that requires well-formed `CWE-###` / `A##:20##` labels for
  classification tasks.
- **Repo reviews** — a whole, pinned vulnerable repository. The teacher writes
  `findings.json`; a deterministic path/line-recall oracle scores it against the
  planted findings (recall floor, spray cap, line window). No model is in the
  grading loop.

Three properties make the lane safe to run:

- **Firewall.** The teacher only ever sees `security/catalog/`; the reference
  answers and planted-finding keys live in `security/keys/` and are opened only
  by the host-side grader, never copied into a teacher workspace.
- **Rejection sampling.** Each case is retried a few times and only a *passing*
  attempt is exposed to the dataset builder.
- **Sandbox.** The security teacher runs inside a Bubblewrap namespace that hides
  the real home (repositories and saved auth) and re-binds only a disposable
  workspace plus a short-lived `CODEX_HOME`; the copied credential is unlinked at
  `thread.started`, before any model-generated command can run. This requires
  `bwrap` and refuses to run without it.

`sec-build` renders passing traces into `data/security/{train,val}.jsonl` using
the same row schema as the coding lane (and the same full-tool-list contract —
every row lists the sandboxed teacher's whole `exec`/`apply_patch`/`update_plan`
surface). The main `build` phase folds those files in automatically when they are
present, so the security rows ride the same expand/export/prepare path as
everything else. The corpus, keys, traces, and ephemeral runtime are all
gitignored.

---

## Dataset outputs

Accepted traces are assembled into training data, with secrets redacted and
host-specific paths scrubbed throughout:

- **`build`** turns accepted traces into OpenAI-style agent rows (full sessions,
  optionally keeping reasoning/thinking).
- **`expand`** derives one cumulative *next-assistant-action* row per assistant
  step, so the student learns to take the next action from any prefix.
- **`export` / `export-next`** stage the whole-session and next-step datasets
  into tracked HF JSONL.
- **`parquet`** writes optional Parquet shards.
- **`prepare`** renders each row with the *student model's own* chat template
  into the local training directory (`student.output_dir`).
- **`verify-export`** fails closed if any export violates a provenance or privacy
  gate.
- **`card`** renders `data/hf-publish/README.md` — a Hugging Face dataset card
  auto-populated from the published rows (the real language/category/domain mix,
  tool surface, splits, teacher/judge, and model-attestation rate), so every
  dataset ships a card that matches its data. Override the `pretty_name`,
  `license`, and Hub id via `config.publish`.

---

## Running long jobs detached

Generation and screening are long and metered. Run them detached in a systemd
`--user` scope so a terminal or session teardown can't kill them mid-flight:

```bash
scripts/batch.sh full   python3 moonshiner.py run           # whole pipeline
scripts/batch.sh traces python3 moonshiner.py generate --all
scripts/batch.sh review bash scripts/quality_loop.sh         # rolling screen+repair loop
# follow:  tail -f runs/<name>-<ts>/run.log
# stop:    systemctl --user stop <unit>   (printed on launch)
```

`scripts/quality_loop.sh` alternates screening pending first-pass traces and
repairing standing rejections, a few at a time, until both drain — and defers
cleanly when a runtime hits a usage-limit backoff.

---

## Offline gate and tests

`scripts/check.sh` is the pre-commit / CI gate — byte-compile, the unit suite,
and the seed audit, with no model calls or network:

```bash
scripts/check.sh
# == byte-compile … ==  == unit tests …  OK ==  868 complete, 0 partial  check: OK
```

The `tests/` suite is model-free and offline: usage-limit backoff, seed
import/audit, secret/path scrubbing, fingerprinting, format routing, screening
gates, dataset transforms, and the orchestrator's phase planner. Run it directly
with `python3 -m unittest discover -s tests`.

---

## Repository layout

```
moonshiner.py          Single-process orchestrator + per-phase dispatch (the entry point)
config.json            Teacher, judge, runtimes, student, source, holdout config
tasks/seeds/           The tracked seed corpus (imported; canonical + fallback)
schemas/               JSON Schemas for the judge's verdict
scripts/
  check.sh             Offline gate: byte-compile + tests + seed audit
  batch.sh             Detach a long job into a systemd --user scope
  quality_loop.sh      Rolling screen + repair loop
tests/                 Model-free offline unit suite
src/
  common.py            Shared core: config, seed loading, workspaces, scrubbing, hashing
  import_seeds.py      Import corpus from canonical + fallback sources
  audit_seeds.py       Fail-closed seed-integrity audit
  validate_seeds.py    No-model solvability proof
  generate_traces.py   Drive the teacher runtime to produce traces
  screen_traces.py     Deterministic gates + independent judge
  retry_rejected_traces.py   Repair lane for rejections
  build_dataset.py     Accepted traces -> agent training rows
  expand_next_steps.py Cumulative next-step derivation
  export_hf.py / export_hf_next_steps.py / export_parquet.py   Dataset exports
  export_hf_card.py    Hugging Face dataset card (auto-populated from the export)
  prepare_local.py     Render rows with the student chat template
  validate_hf_export.py      Provenance + privacy export gate
  normalize.py         trace_format -> the adapter that parses it
  import_security_cases.py   Import the fable-secure security corpus (catalog + keys)
  fetch_security_corpus.py   Hydrate the 18 pinned security-review repositories
  security_runtime.py  Bubblewrap-sandboxed Codex runner for the security lane
  generate_security_traces.py   Blind-solve + grade security cases (rejection-sampled)
  build_security_dataset.py     Passing security traces -> data/security partition
  runtimes/
    __init__.py        Registry: select teacher/judge adapter from config
    base.py            Runtime-agnostic interfaces (trace generation, review)
    codex.py           Codex (codex exec) adapter
    claude_code.py     Claude Code (claude -p headless) adapter
    pi.py              Pi coding-agent adapter (OpenAI-compatible provider)
    auth.py            Provider-credential loading for metered runtimes
    availability.py    Fail-closed usage-limit backoff, shared by runtimes
    zai_proxy.py       Loopback credential proxy for the Pi runtime
```

Generated artifacts — `workspaces/`, `traces/`, `data/`, `runs/`,
`node_modules/` — are gitignored and never committed.
