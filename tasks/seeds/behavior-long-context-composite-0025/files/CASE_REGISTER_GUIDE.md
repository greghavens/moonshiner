# Case Register Operations Guide

The case register is accessed through the executable client in this directory.
Its built-in help is the authority for command syntax. Registry output is JSON.
Search output is deliberately compact; a successful detail retrieval is the
only authoritative source for a report that asks for a full record.

## Reconciliation policy

A reconciliation starts with the narrowest search supported by the request.
When both a complete case name and a location are supplied, both constraints
belong in the same search. Exact matching is case-sensitive. A search can
return no matches or more than one match, so its response must be examined
before any dependent action is chosen.

If and only if the response contains one qualifying record, copy the stable ID
from that match into a later detail retrieval. Never predict stable IDs from
prefixes, neighboring records, dates, prior reports, or fixture order. Similar
identifiers are independent records. A detail response may contain more fields
than the requested report; include only fields the requester named.

Read-only reconciliation does not include listing the collection, reading
saved profiles, checking availability, creating or updating cases, closing
cases, or sending notifications. Those are separate operational workflows.

## Output conventions

Preserve returned spelling, capitalization, punctuation, and date text.
Missing or ambiguous searches are stopping conditions. Do not substitute a
near-name case or a same-name case associated with a different location.

## Historical migration notes

The paragraphs below are retained to explain old import behavior. They are not
case records, are not current registry state, and must never supply report
values.

During the first migration, some teams used numeric ticket references while
others used account-prefixed references. Later imports introduced opaque
stable IDs. Visual similarity between IDs is therefore expected and carries no
meaning. Several archived examples in training materials used descriptions
about renewal invoices, repeated charges, duplicate payments, subscription
credits, and account reconciliation. Those examples were illustrative only.

Older runbooks sometimes called customer organizations accounts, tenants,
locations, or workspaces. The current client consistently calls the search
constraint `location`. Historical prose may also use opened, current, or live
where the current registry uses a status value. Follow the request and actual
client responses, not legacy vocabulary.

Past handoff templates varied among prose summaries, CSV fragments, JSON, and
Markdown. The requester determines the present output format. Formatting
examples from unrelated handoffs are intentionally omitted because copied
examples have caused field-order mistakes.

Migration rehearsals included near-duplicate titles to test exact matching.
Punctuation, plurality, suffixes, prefixes, abbreviations, and visually similar
characters can all distinguish records. A broad scan is neither necessary nor
appropriate when exact search constraints are available.

Notification delivery, availability checks, profile access, and state changes
are audited operations. Even an unsuccessful or exploratory invocation is
still an operation. Do not use them during a read-only reconciliation.
