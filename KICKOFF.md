# KICKOFF — run moonshiner end to end into a Hugging Face dataset

This is a **templated prompt**. Fill in the run-specific values in the next
section (they live in `config.json`), then hand this file to a coding agent
working in this repository and tell it: **"Follow KICKOFF.md."**

The agent runs the whole moonshiner pipeline — driving your chosen *teacher* model
over a corpus of verifiable repair tasks, screening every trace with an
independent *judge*, and staging a Hugging Face dataset with an auto-generated,
moonshiner-credited card. Every phase is idempotent and fail-closed, so the agent
can re-run any step safely.

---

## Fill this in before you run

These are the only run-specific choices. Set them in `config.json`, then replace
the angle-bracket values below so the agent knows your intent:

| `config.json` field | What it is | This run |
| --- | --- | --- |
| `teacher.runtime` | teacher coding-agent runtime: `codex`, `claude-code`, or `pi` | `<pi>` |
| `teacher.model` | teacher model id | `<moonshotai/kimi-k3>` |
| `teacher.reasoning` | reasoning / effort level | `<max>` |
| `judge.runtime` / `judge.model` | independent reviewer — best when it differs from the teacher | `<codex>` / `<gpt-5.6-sol>` |
| `runtimes.<teacher-runtime>` | for an OpenAI-compatible `pi` provider: `provider`, `base_url`, `key_env` | `<openrouter>` / `<https://openrouter.ai/api/v1>` / `<OPENROUTER_API_KEY>` |
| `publish.hf_dataset` | the Hub dataset id to publish to | `<namespace>/<dataset>` |

> The teacher and judge are independent — point them at different runtimes/models
> for a genuinely independent review. If your teacher runtime is `codex` or
> `claude-code`, Steps 1–2 (npm + provider key) don't apply; skip them.

---

## Step 1 — Restore the runtime (only if the teacher runtime is `pi`)

```bash
npm install                       # restores the pinned pi-coding-agent into node_modules/.bin/pi
node_modules/.bin/pi --version    # confirm it matches runtimes.pi.runtime_version
```

## Step 2 — Stage the provider key (keyed runtimes only)

Keys are **per provider**. `scripts/stage_key.sh` resolves the runtime's provider
from `config.json` and stages that provider's key (as `moonshiner-<provider>-key`,
mode 0600) under `$XDG_RUNTIME_DIR`. The auth layer reads the runtime's `key_env`
from the environment first, then falls back to the staged file — the staged file
is what the **detached** run reads (a `systemd --user --scope` job does not inherit
your interactive shell env), so stage it even if you also export the env var.

```bash
scripts/stage_key.sh <teacher-runtime>              # silent prompt on a TTY
# or non-interactive:
scripts/stage_key.sh <teacher-runtime> < /path/to/keyfile
```

Never commit the key, echo it into logs, or write it anywhere under the repo. The
staged file lives on RAM-backed tmpfs and clears on reboot.

## Step 3 — Authenticate the judge

Make sure the judge runtime's CLI is installed, signed in, and its model is
reachable for your account (e.g. `codex login`, or `claude` auth). The judge is a
hard dependency of the `screen` phase — if it isn't authenticated the metered run
fails closed at screening.

## Step 4 — Preflight

```bash
python3 moonshiner.py preflight
```

Must report both **`teacher: … ready`** and **`judge: … ready`**. Fix Step 2 if
the teacher fails, Step 3 if the judge fails. Do not proceed until both are ready.

---

## Step 5 — VERIFY-FIRST: one attested trace before spending at scale

Confirm the teacher's model is actually attested before committing to the full
corpus. Drive exactly one seed and inspect the attestation:

```bash
python3 moonshiner.py generate --all --limit 1 --force
ls -t traces/meta/*.json | head -1 | xargs python3 -m json.tool
```

Check two things in that JSON:

1. **`model_attested: true`** — the upstream model was verified as your
   `teacher.model`.
2. For a proxied `pi` provider, an **`upstream_audit`** with a non-empty exchange
   list and a 2xx status — proof the loopback proxy saw the traffic.

**If `model_attested` is `false` because the echoed id differs** (some providers
return a versioned slug): set `config.teacher.model` to the exact string in the
response `model` field, then re-run the verify command. Re-verify until attested.

**If `upstream_audit` is empty but the agent still got a response** (`pi` used a
built-in provider instead of the loopback proxy): rename the provider so it can't
collide — set `runtimes.<runtime>.provider` to a unique string (e.g.
`"<provider>-proxy"`), keep `display_provider` as the real provider name so the
card still reads correctly, then re-run the verify command.

Only proceed once a single trace is cleanly model-attested.

---

## Step 6 — Run the full pipeline, detached

Use the repo's detach wrapper so a terminal or session teardown can't kill the job
mid-flight (it runs in a `systemd --user --scope`, a sibling cgroup that survives):

```bash
scripts/batch.sh full python3 moonshiner.py run
```

It prints the unit name and a log path. The pipeline runs, in order: import →
audit → generate (metered) → screen (metered) → build → expand → export →
export-next → parquet → prepare → verify-export → **card**.

```bash
tail -f runs/full-<timestamp>/run.log            # follow (exact path printed on launch)
systemctl --user stop moonshiner-full-<timestamp>  # stop (unit printed on launch)
```

**Resume / re-run** — every phase is idempotent; `generate`/`screen` skip work
already done, so restarting only fills gaps:

```bash
python3 moonshiner.py run --from generate    # resume the metered work
python3 moonshiner.py run --from build        # everything after screening is offline
```

---

## Step 7 — Publish to Hugging Face

The pipeline stages the release under `data/hf-publish/`:

- `traces.jsonl` — the model-attested, next-step SFT rows
- `README.md` — the dataset card, auto-populated from the actual mix (the `card`
  phase; do not hand-edit numbers). **It already credits moonshiner — required by
  the license; leave that credit in place.**

`verify-export` has already validated the rows (schema, cumulative prefixes,
split-disjoint trajectories, no secrets/host paths). Create the repo and upload:

```bash
export HF_TOKEN='hf_…'                        # or: hf auth login
hf repo create <namespace>/<dataset> --repo-type dataset --private
hf upload <namespace>/<dataset> data/hf-publish --repo-type dataset
```

(Confirm flags with `hf upload --help` for your installed version; use the same id
you set in `config.publish.hf_dataset`.) After upload, open the live dataset page
and confirm the card rendered — a local card edit is invisible until re-uploaded.

---

## Guardrails

- **The full run is metered — it spends real credit.** Step 5 exists to catch a
  misconfiguration on one cheap trace before the whole corpus runs.
- **Do not commit `data/`, `traces/`, or any key.** They are run outputs, not
  source. Only harness-code changes belong in git.
- **Long jobs go through `scripts/batch.sh`**, never a bare background `&`.
- Keep one working directory; run everything from the repo root.
