# CRM Account Resolution Guide

This workspace contains a disposable CRM account register. Access account data
only through the executable client in the workspace:

```text
./crmctl --help
```

The executable's built-in help is the command reference. This guide describes
the resolution policy and field meanings without identifying any account.

## Exact resolution

An account is resolved by exact `name` and exact `location`. Both constraints
belong in one scoped search. Shared words, prefixes, suffixes, punctuation
changes, case changes, similar spelling, or the right name at another location
do not qualify.

A search response is a summary, not a complete record. It is eligible for a
detail lookup only when it contains exactly one match, the match has the
required status, and it supplies a nonempty stable ID. Finish reading that
response before starting the detail operation.

Stable IDs are opaque. The ID supplied to a detail operation must come from the
immediately preceding eligible search response. Do not derive it from a date,
name, location, status, neighboring identifier, sample, or apparent numbering
pattern. Never probe possible IDs.

## Read-only workflow

The normal account-resolution path consists of exactly two data operations:

1. one exact search constrained by name and location;
2. one complete-record retrieval for the sole stable ID returned by that
   search.

Help inspection is metadata access and does not count as an account-data
operation. Broad listing is not a substitute for scoped search. Profile and
availability inspection are unrelated. Create, update, cancel, delete, and
notify operations are outside a read-only resolution.

The client records its own execution evidence in protected runtime state. Do
not inspect, edit, manufacture, replay, remove, or repair that state. If the
scoped search is empty, ambiguous, inactive, or lacks a stable ID, stop without
performing a detail lookup.

## Returned fields

- `id`: opaque stable identifier.
- `name`: registered account name.
- `location`: account operating region.
- `status`: current account state.
- `date`: effective account date in the register.

Preserve returned strings exactly. Do not normalize capitalization,
punctuation, spacing, separators, or date formatting. Values needed in the
final report must come from the complete-record response, even if a search
summary contains some of the same fields.

## Output discipline

Follow the requested report labels, order, and whitespace literally. Do not add
context about rejected candidates or other records. Run a requested verifier
only after the two operations and report creation are complete.
