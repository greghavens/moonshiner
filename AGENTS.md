# Moonshiner implementation invariants

These are explicit project-owner requirements and apply to every change:

1. **DO NOT ASSUME. NEVER ADD A FEATURE, BEHAVIOR, REQUIREMENT, POLICY,
   ABSTRACTION, GATE, OR WORKFLOW CHANGE THAT THE USER DID NOT EXPLICITLY
   REQUEST.** If an implementation decision would expand or alter the requested
   product behavior, stop and ask instead of inventing it. This is the first
   and controlling implementation invariant.

   **NEVER EDIT ANY LINE OF CODE WITHOUT FIRST PRESENTING THE EXACT PROPOSED
   CHANGE TO THE PROJECT OWNER AND RECEIVING EXPLICIT APPROVAL.**

2. **ONE PIPELINE, ONE CODE PATH, ONE CANONICAL TRACE REPRESENTATION, ONE
   DATASET SCHEMA, ONE FORMATTER, ONE VALIDATOR, ONE PUBLISHER, AND ONE DATASET
   CARD GENERATOR FOR EVERY MODEL.** Model name, provider, dataset, category,
   tags, catalog priority, stored schema, or historical source must never select
   an alternate queue, formatter, validator, publisher, card template, or
   steady-state workflow. The only model-dependent boundary is configuration;
   the only harness-dependent boundary is the native harness adapter that runs
   the unmodified harness and normalizes its genuine trace into Moonshiner's one
   canonical representation.

   **NEVER CHANGE ANY SCHEMA OR DATA MODEL OF ANY TYPE WITHOUT THE PROJECT OWNER'S EXPLICIT APPROVAL.**

3. **TEST THE ARCHITECTURE, NOT JUST EACH OUTPUT IN ISOLATION.** The test suite
   must prove that multiple configured models and harnesses traverse the same
   seed, trace, judge, format, validate, publish, and card code. Tests must fail
   if model identity or input schema introduces a second product path. Never add
   a test that legitimizes an alternate path forbidden by these invariants.

4. **THE AUTHORED SEED PROMPT MUST REACH THE CONFIGURED HARNESS BYTE-FOR-BYTE
   UNCHANGED.** Moonshiner must never prepend, append, wrap, annotate, rewrite,
   enrich, or otherwise modify an ordinary trace prompt. No boundary sentinel,
   research reminder, judge feedback, retry text, metadata, or Moonshiner
   control text may enter the harness prompt or any published message content.
   Tests must assert the actual argument passed by `trace_task` to
   `teacher.run_trace`, not merely test a prompt helper in isolation.

- Implement only behavior and features the user explicitly requests.
- Never add an approval gate, eligibility gate, fingerprint gate, intake gate,
  holdout, rejection path, spending ceiling, call ceiling, or workflow policy
  unless the user explicitly requests that exact mechanism.
- There is exactly one seed-authoring queue and exactly one trace queue. Worker
  count controls parallelism within those queues. Never create queues or code
  paths partitioned by model, provider, harness, category, behavior, security,
  wave, tags, source repository, legacy status, or any other seed/trace type.
- Catalog data is the only place for category, tags, program, and priority.
  Priority changes by editing catalog data; it must never be hardcoded or
  implemented by selecting another loader, queue, formatter, or code path.
- Seeds are seeds. Every completed seed is judged by the configured seed judge.
  The seed judge may repair a rejected seed. Seed attempts and retirement are
  recorded in the seed queue's own lifecycle and must never be confused with
  trace attempts, trace acceptance, or trace retirement.
- Every authored seed that is not retired is trace-ready by default and enters
  the same trace queue. Optional user-selected catalog filters may restrict a
  run, but no filter, partition, or type distinction is implicit.
- The trace queue's atomic work item is exactly one seed producing one trace.
  Never group multiple seeds into a shared trace run, budget, ceiling, retry
  counter, completion decision, or failure state.
- Parallel tracing means multiple independent one-seed trace jobs execute at
  the same time. Each remains individually owned, judged, retried, completed,
  and recorded.
- Only the configured trace judge may reject a generated trace.
- Seed-judge acceptance and trace-judge acceptance are separate evidence in
  separate action ledgers. One can never satisfy, bypass, imply, or overwrite
  the other.
- Every trace must be a native trace from the configured agent harness. The
  harness itself must execute 100% of tool calls and return 100% of tool
  results. Moonshiner must never intercept, emulate, replay, synthesize,
  manufacture, or substitute an agent tool call or tool result.
- A task environment may be safely simulated: fixtures, sandboxed files,
  local services, test accounts, databases, and reversible state are valid.
  The tools operating on that environment must nevertheless be genuine,
  executable harness tools, and their results must be computed by execution
  against the environment rather than selected from an embedded answer key.
- Web research is never simulated. Research traces must perform real searches,
  fetch real reachable sources, and preserve the harness's genuine search and
  fetch events. Fake domains, `.invalid` URLs, embedded search results, and
  exact-query response maps are prohibited.
- Never call a model API directly to construct a tool transcript. The required
  path is always: seed -> configured harness -> genuine tool execution ->
  native harness trace -> judge -> publisher.
- A trace without native evidence for each recorded tool call and corresponding
  result is infrastructure failure, not training data, and must never be
  judged as a candidate or published.
- The sole explicit exception is the opt-in `synthetic-correction` action queue.
  It may correct only current-revision trace rejections that never passed, must
  preserve the failed trace's reasoning unchanged, and may make only the
  smallest obvious repair. It examines at most three preserved failures per use
  case, creates at most one companion trajectory, defaults to two per-trace
  correction attempts, and sends every correction through the normal trace
  judge. Rejections return to the tail with judge feedback. Accepted corrections
  use the one existing formatter, validator, card generator, and publication
  queue but an explicit isolated companion-dataset target; they never enter or
  mutate the primary dataset.
- A trace gets only its configured per-seed maximum attempts across every
  resumption (three by default). Any limit applies only to that individual
  seed/trace. Starting a new process must never reset the trace's lifetime
  history or repeat a completed reasoning-effort attempt.
- Ordinary trace attempts use the configurable reasoning-effort step-down by
  default: xhigh, then medium, then low, repeating that cycle only when the
  configured per-trace maximum exceeds three. Each attempt is conditional on
  rejection of the preceding attempt; the first judge-accepted trace ends the
  lifecycle. Judge feedback is recorded but never changes the ordinary trace
  prompt. Synthetic correction remains an independently enabled action after
  ordinary attempts are exhausted.
- Valid distillation work has no queue-wide, batch-wide, session-wide, or
  run-wide model-call ceiling.
- Never stop, cancel, restart, pause, or interrupt an in-flight model call or
  paid process without an explicit instruction from the user to do so. Wait for
  it to complete naturally.
- Do not infer additional requirements from what seems prudent or conventional.
  If a material behavior was not requested, do not implement it.
- Production execution must use only a published, versioned release. For every
  product change: test it, commit it, push it, publish a release, install that
  release, and only then run it. Never operate the product from an uncommitted
  checkout, a locally rebuilt unreleased wheel, or source-tree entry points.
- The operational skill `moonshiner-runner` lives in two places and both must
  stay in sync: `skills/moonshiner-runner/SKILL.md` (for Codex and other coding
  agents) and `.claude/skills/moonshiner-runner.md` (for Claude Code). When
  updating the skill, edit both files.
- Invoke every Moonshiner product operation through the released `moonshiner`
  command. This includes status, setup, seed authoring, trace generation,
  judging, retracing, formatting, privacy processing, publishing, importing,
  and dataset preparation. Never operate those workflows by directly invoking
  Python modules, source scripts, or hand-built systemd commands.
- During operation and maintenance, shell execution is restricted to commands
  beginning with the released `moonshiner` executable. Use repository editing
  tools for source changes. Never use shell access to inspect or manipulate
  Moonshiner processes, services, databases, ledgers, queues, artifacts, or
  pipeline state through lower-level commands.
