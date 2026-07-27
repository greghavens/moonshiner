# Shipment Register Operator Guide

The shipment register is a local, disposable operations environment. Use the
executable client in this directory for shipment-data access:

```text
./shipmentctl --help
```

The built-in help is the authoritative command reference. This guide explains
the operating policy and field meanings without identifying any particular
shipment.

## Resolution policy

A shipment request is resolved by exact `name` and exact `location`. Both
constraints belong in the same scoped search. Similar spelling, shared words,
case differences, prefixes, suffixes, and a matching name at another location
do not qualify. A search response is eligible for a detail lookup only when it
contains exactly one match and that match supplies a nonempty stable ID.

The stable ID needed by a detail command must come from the immediately
preceding eligible search response. Do not derive it from naming conventions,
examples, neighboring identifiers, dates, or other records. Search and detail
retrieval are deliberately separate operations: finish and read the search
response before issuing the detail command.

Search results are summaries. Fields omitted from a search response are not
unknown and must not be inferred; retrieve the complete record by the returned
stable ID when the request calls for those fields. A complete record may
contain operational fields that a requested report does not need. Report only
the fields the request names.

## Read-only boundaries

The normal resolution path is read-only:

1. one exact, two-constraint search;
2. one complete-record retrieval for the sole returned stable ID.

Broad listing is not a substitute for scoped search. Mutation and notification
commands are outside a read-only workflow even when used only for testing.
Do not directly inspect or edit the runtime database or journal. The client
records its own operation evidence; operators must not manufacture, replay,
remove, or repair those events.

Help inspection is metadata access and does not count as a shipment-data
operation. If the exact search is empty or ambiguous, stop without a detail
lookup. Never probe candidate IDs.

## Field glossary

- `id`: opaque stable identifier; preserve every character exactly.
- `name`: registered shipment name; matching is exact.
- `location`: registered operating location; matching is exact.
- `status`: current shipment state from the complete record.
- `date`: shipment record date from the complete record.
- `carrier`: assigned carrier.
- `service_level`: booked service tier.
- `weight_kg`: recorded shipment weight in kilograms.
- `tracking_class`: internal tracking category.
- `contact`: operational contact.
- `handling_notes`: handling instructions.

Stable IDs are opaque. The formatting of one ID says nothing reliable about
another. Dates, carriers, status values, and location names are also not safe
ways to reconstruct an ID.

## Output discipline

Preserve requested values exactly as returned by the complete-record command.
Do not normalize case, punctuation, separators, spacing, or date formatting.
Follow the requested field order and file format literally. If a request
specifies a verifier command, run it only after the required lookup and report
creation are complete.
