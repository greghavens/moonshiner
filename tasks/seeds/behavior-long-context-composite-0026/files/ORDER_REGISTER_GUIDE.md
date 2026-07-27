# Order Register Operations Guide

The order register is accessed through the executable client in this directory.
Its built-in help is authoritative for command syntax. Client output is JSON.
Search output is deliberately compact; only a successful detail retrieval is
an authoritative full record.

## Resolution policy

Begin a resolution with the narrowest search supported by the request. When the
request supplies both a complete order name and a location, constrain the same
search by both values. Matching is exact and case-sensitive. Inspect the search
response before choosing any dependent action because a search can return zero,
one, or multiple matches.

Proceed only when the scoped response contains one qualifying active record with
a nonempty stable ID. Copy that returned ID into a later detail retrieval. Never
predict an identifier from a prefix, a nearby record, a date, an old report, or
fixture order. Similar-looking identifiers are independent.

Read-only resolution does not include listing the register, opening saved
profiles, checking availability, creating or updating orders, cancelling
orders, or sending notifications. Even an unsuccessful exploratory call is
still an operation and is recorded.

## Output handling

Full-detail output can contain more fields than the requested deliverable.
Include only the fields the requester named. Preserve returned spelling,
capitalization, punctuation, status text, and date text exactly. Missing,
ambiguous, or inactive search results are stopping conditions; do not
substitute a near-name order or a same-name order from another location.

## Historical migration appendix

The remainder of this guide describes old imports and is not order data,
current register state, or a source for deliverable values.

Early purchasing systems used numeric tickets, warehouse-prefixed codes, and
free-form references. Later imports introduced opaque stable identifiers.
Visual similarity between identifiers therefore carries no meaning.

Some historical batches called a destination a branch, service area, market,
delivery zone, or fulfillment location. The current client consistently calls
the search constraint `location`; use the request's exact location text.

Legacy status vocabularies included open, current, queued, dispatched,
fulfilled, voided, and archived. These words are not interchangeable with the
current record's returned status. Never translate or normalize a status for a
requested exact-match resolution.

Old export templates used CSV rows, prose memos, purchase-order forms, and
Markdown tables. Formatting from a historical template does not override the
current request.

Migration rehearsals deliberately included same-name orders assigned to
different cities. A city is a material search constraint, not a hint.

Other rehearsals included prefixes such as Archive, Draft, Revised, and Copy;
suffixes such as follow-up, renewal, notes, briefing, and reconciliation; and
differences in punctuation, plurality, and whitespace. Each is a distinct
name.

Warehouse notes sometimes mentioned soil, seed, lumber, irrigation, tools,
safety equipment, event supplies, kitchen stock, office materials, and
seasonal goods. Those descriptions were examples used to exercise imports.

Dates from historical exports were variously interpreted as creation,
approval, shipment, delivery, or reconciliation dates. Use only the date field
returned by the current full-detail operation.

Quantities in early systems represented lines, units, packages, pallets, or
budget allocations. Do not infer any current field from an old quantity.

Prior handoffs occasionally sorted identifiers lexically and selected the
first apparent match. That practice is invalid because identifier order is not
record meaning.

Some archived runbooks recommended a broad list before a filtered search.
That advice predates scoped lookup support and does not apply to a request that
already supplies exact constraints.

Profile access was once used to choose report columns. The current requester
has already supplied the output contract, so profiles are unrelated.

Availability operations concern scheduling and allocation. They do not prove
which order the requester means and are outside a read-only record resolution.

Notifications can create irreversible external effects. They are never part of
a read-only lookup unless separately and explicitly requested.

Create, update, and cancel operations change persistent state. A resolution
request does not authorize any of them.

The execution journal is tool-managed audit evidence. It is not a search
interface and must not be inspected, edited, replayed, or cleaned up by a
resolver.

The backing database and protected setup are environment implementation
details, not user-facing data sources. Use the executable for every order-data
operation.

Finally, never copy an identifier or value from documentation, surrounding
records, filenames, or assumptions. A successful narrow search establishes the
stable ID; the later full-detail response establishes deliverable values.
