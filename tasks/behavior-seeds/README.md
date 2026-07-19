# Non-code tool-behavior seeds

Each JSON file in this directory is an immutable, concrete training recipe for
one non-code tool-use trajectory. These are deliberately separate from
`tasks/seeds/`, whose entries are executable coding-repair repositories with a
reference patch.

The records are validated against `schemas/behavior_seed.schema.json`. Tool
names refer to the deterministic world registry in `tasks/behavior-worlds.json`.
Expected stages express the call dependency graph: every call in a stage marked
`parallel: true` must appear in one assistant action, while later stages wait
for the earlier tool results. The trace runner must never serialize a parallel
stage into separate assistant actions.

Regenerate the checked-in corpus with:

```sh
python3 scripts/author_behavior_seeds.py
```

The generator refuses to replace an existing file whose content differs, so a
published seed identifier remains immutable.
