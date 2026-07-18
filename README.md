# moonshiner

A single, unified harness for **full-model distillation of coding ability** (with
an optional defensive-security lane). It drives a configurable *teacher* coding
agent through a corpus of real repair tasks, keeps only the traces that provably
pass a deterministic screen **and** an independent *judge*, and renders the
survivors into a supervised fine-tuning dataset — plus a matching Hugging Face
dataset card — for a student model.

---

## Quickstart

The intended way to run moonshiner is through **[`KICKOFF.md`](KICKOFF.md)** — a
templated prompt you hand to a coding agent:

1. **Clone this repository — your clone *is* your trace repo.** Every run's
   traces, datasets, and run logs accumulate inside it (all gitignored, so they
   stay local to your clone and are never pushed back here). Work from the clone.

   ```bash
   git clone https://github.com/greghavens/moonshiner.git
   cd moonshiner
   ```
2. **Open [`KICKOFF.md`](KICKOFF.md) and fill in the run-specific values** — your
   teacher model and provider, the judge, and the Hugging Face dataset id (all of
   which live in `config.json`).
3. **Point a coding agent at your clone and tell it: "Follow KICKOFF.md."**
4. The agent runs the whole pipeline end to end — generate → screen → build →
   export — and stages a Hugging Face dataset with an auto-populated card.

That's the whole story. Everything below is reference for tuning the config or
driving the pipeline by hand.

---

## License

moonshiner is free to **copy, use, modify, and fork** — the one condition is
**attribution**:

- **Credit the original repository** — <https://github.com/greghavens/moonshiner> —
  in any copy or fork.
- **Datasets and models produced with moonshiner must also credit moonshiner.**
  The `card` phase writes that credit onto every dataset card automatically; don't
  strip it.

See [`LICENSE`](LICENSE) for the full terms.

---

## Run it yourself (by hand)

If you'd rather drive the pipeline directly instead of through an agent:

```bash
scripts/check.sh                       # offline sanity gate: byte-compile + tests + seed audit. No model, no net.
python3 moonshiner.py run --dry-run    # see exactly what a full run would do
python3 moonshiner.py preflight        # are the configured teacher + judge reachable and authed?
python3 moonshiner.py run              # the whole pipeline, end to end
```

Everything up to trace generation is **offline** — the seed corpus, audits,
dataset assembly, and exports all run for free:

```bash
python3 moonshiner.py run --offline    # only phases that call no model
python3 moonshiner.py run --to audit    # import + audit, then stop
```

Each phase is also a standalone subcommand that takes its own arguments and passes
`--help` straight through:

```bash
python3 moonshiner.py generate --all
python3 moonshiner.py screen --all --review
python3 moonshiner.py retry --limit 4
python3 moonshiner.py phases            # list the pipeline
```

---

## The pipeline

`run` executes the non-optional phases in order. Optional phases (a repair lane
for rejected traces, and the security lane) fold in only when asked for with
`--with`.

| # | phase | cost | what it does |
|---|-------|------|--------------|
| 1 | `import` | offline | Import the seed corpus (canonical source + fallback) |
| 1.1 | `sec-import`\* | offline | Import the fable-secure security corpus (catalog + held-out keys) |
| 1.2 | `sec-fetch`\* | offline | Hydrate the pinned security-review repositories |
| 2 | `audit` | offline | Audit seed integrity, fail-closed |
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

`\*` optional — folded in only via `--with` (e.g. `--with retry`, or the security
lane `--with sec-import --with sec-fetch --with sec-generate --with sec-build`).
See [The security lane](#the-security-lane-optional).

Run selection:

```bash
python3 moonshiner.py run --from generate --to screen   # a slice, inclusive
python3 moonshiner.py run --skip parquet                 # drop a phase
python3 moonshiner.py run --continue-on-error            # don't stop at the first failure
```

`run` is **fail-closed**: a non-zero phase stops the pipeline unless
`--continue-on-error`. Metered phases preflight their runtime and refuse to run if
it is unreachable, unauthenticated, or (for a paid runtime) not credit-unlocked —
nothing is silently skipped.

---

## Configuration

Everything is in `config.json`. (This is the harness's own operating config, not
per-run user state.)

### Teacher and judge

```json
"teacher": { "runtime": "pi", "model": "moonshotai/kimi-k3", "reasoning": "max", "timeout_s": 3600 },
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
(generate a trace, run a read-only review). A trace records the `trace_format` its
runtime produced; on the way back in, `normalize` routes that format to the
matching parser, so the rest of the pipeline is runtime-agnostic.

Supported trace formats: `claude-stream-json`, `codex-exec-events`,
`codex-rollout`, `pi-coding-agent-json-v3`.

Keyed runtimes (an OpenAI-compatible `pi` provider) read their API key from
`key_env` first, then a mode-0600 file staged by `scripts/stage_key.sh` under
`$XDG_RUNTIME_DIR` — see [`KICKOFF.md`](KICKOFF.md), Step 2.

### Student, build, source

```json
"student": { "base_model": "unsloth/NVIDIA-Nemotron-3-Super-120B-A12B",
             "output_dir": "~/nemotron-super-finetune/data-moonshiner" },
"build":   { "keep_reasoning": true, "keep_thinking": true, "val_frac": 0.08 },
"source":  { "seed_repository": "../fable-code", "fallback_repository": "../sol-code" },
"holdout_tasks": ["ts-event-emitter", "go-interval-merge"]
```

### Publish

```json
"publish": { "hf_dataset": "<namespace>/<dataset>", "private": true }
```

Set `hf_dataset` before a run so the generated card's attribution URL is correct.
`pretty_name` and `license` are also overridable here (the card defaults to
`cc-by-4.0`). The moonshiner credit on the card is a license requirement and is
not overridable.

---

## The seed corpus

Seeds are real repair tasks (a prompt, a verify command, protected test files, and
a reference fix). They are tracked in-tree under `tasks/seeds/`, imported from two
sources with a deliberate precedence:

- **`seed_repository`** (`../fable-code`) is **canonical**. Its version of a seed
  wins whenever it is complete.
- **`fallback_repository`** (`../sol-code`) is used **only** for a seed the
  canonical source lacks or left incomplete (an authoring agent that died
  mid-write, a rejected stub).

This encodes the rule *"canonical unless it is off, then fall back"*. A seed that
is incomplete in **both** sources is reported invalid and never half-copied. The
merge is deterministic and reproducible:

```bash
python3 moonshiner.py import --dry-run   # report provenance without copying
python3 moonshiner.py import --force      # reproduce the merge deterministically
```

A seed is **complete** when its `task.json` parses with every required field, its
`id` matches the directory name, `files/` exists, and every protected `test_files`
entry is present. `audit` additionally requires a non-empty `reference_fix.patch`
(holdout and pre-spec pilot tasks are exempt — their solvability is proven by
held-out evaluation or actual passing traces).

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
5. **Static scope** — no prohibited action (git-state mutation, workspace escape,
   `/tmp` use, global install, nested agent, leaked secret) appears in the trace.

**Judge** (independent, configurable): the runtime named by `config.judge` reviews
the trace read-only and returns a schema-constrained verdict. Acceptance requires
every review category clear and every stated requirement met.

Rejections feed the optional **repair lane** (`retry`): a rejected trace is re-run
with concrete feedback derived from exactly what the screen or judge found, then
rescreened.

---

## The security lane (optional)

A parallel, opt-in lane distills **defensive** security ability — finding and
classifying vulnerabilities — from the sibling `../fable-secure` corpus. It is off
by default; fold it in with `--with sec-import --with sec-fetch --with
sec-generate --with sec-build`, or run any phase on its own
(`python3 moonshiner.py sec-generate --only sec-answer-42`).

Two case kinds, each with its own held-out grader:

- **Answer cases** — a blind question (classify a snippet by CWE/OWASP, explain an
  attacker primitive, review a diff). The teacher never sees the reference answer;
  a separate low-effort judge grades against it, behind a deterministic
  label-shape gate that requires well-formed `CWE-###` / `A##:20##` labels for
  classification tasks.
- **Repo reviews** — a whole, pinned vulnerable repository. The teacher writes
  `findings.json`; a deterministic path/line-recall oracle scores it against the
  planted findings (recall floor, spray cap, line window). No model is in the
  grading loop.

Three properties make the lane safe to run:

- **Firewall.** The teacher only ever sees `security/catalog/`; the reference
  answers and planted-finding keys live in `security/keys/` and are opened only by
  the host-side grader, never copied into a teacher workspace.
- **Rejection sampling.** Each case is retried a few times and only a *passing*
  attempt is exposed to the dataset builder.
- **Sandbox.** The security teacher runs inside a Bubblewrap namespace that hides
  the real home and re-binds only a disposable workspace plus a short-lived
  `CODEX_HOME`; the copied credential is unlinked before any model-generated
  command can run. This requires `bwrap` and refuses to run without it.

`sec-build` renders passing traces into `data/security/{train,val}.jsonl` using the
same row schema as the coding lane. The main `build` phase folds those files in
automatically when present, so the security rows ride the same
expand/export/prepare path as everything else. The corpus, keys, traces, and
ephemeral runtime are all gitignored.

---

## Dataset outputs

Accepted traces are assembled into training data, with secrets redacted and
host-specific paths scrubbed throughout:

- **`build`** turns accepted traces into OpenAI-style agent rows (full sessions,
  optionally keeping reasoning/thinking).
- **`expand`** derives one cumulative *next-assistant-action* row per assistant
  step, so the student learns to take the next action from any prefix.
- **`export` / `export-next`** stage the whole-session and next-step datasets into
  tracked HF JSONL.
- **`parquet`** writes optional Parquet shards.
- **`prepare`** renders each row with the *student model's own* chat template into
  the local training directory (`student.output_dir`).
- **`verify-export`** fails closed if any export violates a provenance or privacy
  gate.
- **`card`** renders `data/hf-publish/README.md` — a Hugging Face dataset card
  auto-populated from the published rows (the real language/category/domain mix,
  tool surface, splits, teacher/judge, and model-attestation rate), so every
  dataset ships a card that matches its data and credits moonshiner.

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

`scripts/check.sh` is the pre-commit / CI gate — byte-compile, the unit suite, and
the seed audit, with no model calls or network:

```bash
scripts/check.sh
# == byte-compile … ==  == unit tests …  OK ==  873 complete, 0 partial  check: OK
```

The `tests/` suite is model-free and offline: usage-limit backoff, seed
import/audit, secret/path scrubbing, fingerprinting, format routing, screening
gates, dataset transforms, the card generator, and the orchestrator's phase
planner. Run it directly with `python3 -m unittest discover -s tests`.

---

## Repository layout

```
KICKOFF.md             Templated kickoff prompt — fill in, hand to a coding agent to run the pipeline
LICENSE                Free to use/fork with attribution; datasets must credit moonshiner
moonshiner.py          Single-process orchestrator + per-phase dispatch (the entry point)
config.json            Teacher, judge, runtimes, student, source, publish, holdout config
tasks/seeds/           The tracked seed corpus (imported; canonical + fallback)
schemas/               JSON Schemas for the judge's verdict
scripts/
  check.sh             Offline gate: byte-compile + tests + seed audit
  batch.sh             Detach a long job into a systemd --user scope
  stage_key.sh         Stage a per-provider API key under $XDG_RUNTIME_DIR (0600)
  quality_loop.sh      Rolling screen + repair loop
tests/                 Model-free offline unit suite
src/
  common.py            Shared core: config, seed loading, workspaces, scrubbing, hashing
  import_seeds.py      Import corpus from canonical + fallback sources
  audit_seeds.py       Fail-closed seed-integrity audit
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
  fetch_security_corpus.py   Hydrate the pinned security-review repositories
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
```
