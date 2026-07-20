# Behavioral seed curriculum and expansion

The first behavioral corpus contains exactly 1,000 non-code tool-use seeds. It
is a breadth pass over external function-calling evaluations behavior, not a benchmark replica. Seeds use
original entities, prompts, tool names, backend states, and call graphs.

## Round 1: 1,000 seeds

| Behavioral lane | Seeds |
|---|---:|
| Exact tool selection and argument construction | 80 |
| Multiple-function discrimination | 70 |
| Same-tool parallel calls | 80 |
| Mixed-function parallel calls | 80 |
| Dependency-aware parallel/sequential planning | 90 |
| Base multi-turn state tracking and corrections | 110 |
| Missing parameters versus inferable parameters | 60 |
| Missing functions introduced later | 30 |
| Long-context retrieval and composite cases | 50 |
| Relevance, irrelevance, abstention, and non-action | 70 |
| Error recovery, partial failure, and idempotency | 60 |
| Multihop web research and retrieval recovery | 100 |
| Persistent memory: key-value, vector, and summary | 100 |
| Format-sensitivity stress cases | 20 |
| **Total** | **1,000** |

Instruction-following requirements are overlays across every lane: prohibitions,
conditional behavior, correction handling, exact response contracts, ordering,
grounding, scope boundaries, and confirmation before consequential actions.
Domain labels are secondary; behavior tags are the training-control surface.

## Doubling policy

Never double by copying rows or making name-only paraphrases. Each round retains
the prior corpus and adds new states, tool schemas, call graphs, prompt forms,
failure schedules, and domain combinations.

### Round 2: 2,000 total

- Add 1,000 new seeds after running external function-calling evaluations on the first trained checkpoint.
- Assign 600 benchmark-directed seeds to the weakest measured behavioral slices.
- Reserve the remaining 400 seeds for guaranteed breadth: 150 parallel/dependency
  cases, 100 multi-turn cases, 75 recovery cases, 50 clarification/abstention
  cases, and 25 complex schema/format cases.
- Expand weak slices with new tool signatures and dependency graphs, not just nouns.
- Add no-action and over-action counterexamples when precision and recall diverge.
- Tag additions with `round:2`, `source:benchmark-directed` when applicable,
  and `weakness:<slice>` so training composition can select the measured repair.

The pre-benchmark Round 2 allocation commissioned for the next mixed-wave
training run is fixed at 400 breadth-reserve seeds and 600 benchmark-informed
seeds. It adds 350 parallel/dependency cases, 150 multi-turn cases, 100 exact
selection/discrimination cases, 75 clarification/missing-function cases, 75
abstention cases, 75 recovery cases, and 175 long-context, web, memory, and
format cases. Every addition carries `round:2`, its `weakness:*` behavior tag,
and exactly one of `source:breadth-reserve` or `source:benchmark-informed`.

### Round 3: 4,000 total

- Cross lanes with nested objects, arrays, enums, optional values, dates,
  timezones, units, and identifiers.
- Add more cross-domain workflows and partially parallel DAGs.
- Expand uncertain commits, idempotency, partial success, and recovery.
- Vary prompt and tool-documentation formats across at least half the corpus.

### Round 4: 8,000 total

- Concentrate on empirically weak long-tail combinations, not uniform expansion.
- Add longer state, denser distractors, delayed tools, and memory consolidation.
- Require two-source evidence and alternate-source recovery in web workflows.
- Add adversarially similar tools without copying held-out benchmark cases.

## Training-time weighting

Balance target assistant decisions, not source files or raw rows. A long trace
must not outweigh many short function-selection cases because it has more
prefixes. Report calls per action, native multi-call actions, parallel groups,
dependency violations, clarification and abstention, unnecessary mutations,
recovery/web/memory coverage, and targets contributed by each trajectory/tag.

The 1,000-seed corpus may be sampled up to 2x initially to balance it against
coding data, but repetition is not expansion and does not replace new seeds.
