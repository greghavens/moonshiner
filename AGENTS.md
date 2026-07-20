# Moonshiner implementation invariants

These are explicit project-owner requirements and apply to every change:

- Implement only behavior and features the user explicitly requests.
- Never add an approval gate, eligibility gate, fingerprint gate, intake gate,
  holdout, rejection path, spending ceiling, call ceiling, or workflow policy
  unless the user explicitly requests that exact mechanism.
- Seeds are seeds. Every catalog seed is eligible for tracing; seed authoring
  does not create a separate approval or rejection state.
- The trace queue's atomic work item is exactly one seed producing one trace.
  Never group multiple seeds into a shared trace run, budget, ceiling, retry
  counter, completion decision, or failure state.
- Parallel tracing means multiple independent one-seed trace jobs execute at
  the same time. Each remains individually owned, judged, retried, completed,
  and recorded.
- Only the configured trace judge may reject a generated trace.
- A trace gets at most two total attempts for its one seed across every
  resumption. Any limit applies only to that individual seed/trace. Starting a
  new process must never reset the trace's lifetime count.
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
