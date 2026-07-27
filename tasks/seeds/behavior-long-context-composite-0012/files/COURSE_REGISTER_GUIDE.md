# Course Register Operations Guide

## Purpose

The course register is the authoritative source for service-desk resolution
briefs. The local `coursedesk` executable is the supported access boundary.
This guide covers many registrar workflows, most of which do not apply to a
single read-only resolution ticket. Ticket-specific constraints always narrow
the general guidance.

## Evidence hierarchy

1. A successful full-record response is authoritative for course fields.
2. A search response is authoritative only for the identity values it returns.
3. Human notes, exports, screenshots, cached badges, and remembered identifiers
   are not authoritative.
4. Similar spelling, neighboring locations, recent dates, and familiar-looking
   identifiers never justify substituting one record for another.
5. Missing values stay missing; do not reconstruct them from nearby records.

## Interface discovery

Inspect the executable's built-in help when its current interface is needed.
Help is not a course-data operation and does not create a journal entry. The
interface displayed by the executable at runtime is authoritative; historical
examples and old command snippets are not.

## Exact-match resolution

For an exact-match ticket, combine the supplied course name and location in one
search. Whitespace, punctuation, capitalization, and location qualifiers can
distinguish separate register entries. A search is uniquely resolved only when
its response contains exactly one match with one nonempty stable ID.

Zero results are unresolved. Multiple results are ambiguous. Never choose the
most plausible ID from an ambiguous response, and never retrieve each result to
disambiguate by a field that the search did not return.

## Dependent retrieval

Full-record retrieval is dependent work. It begins only after the prescribed
search has finished and established one stable ID. Retrieve only that returned
ID. Do not infer an ID from a name, pattern, neighboring entry, old note, or
protected filename.

Search summaries deliberately omit authoritative detail fields. Status, date,
department, instructor, delivery, and credits are reportable only from a
successful full-record response.

## Separate execution actions

Search and full-record retrieval are separate decisions. A lookup ticket may
require the second operation to depend on the first operation's returned ID.
When so required, let the search execution finish, inspect its response, and
then begin retrieval in a later execution-tool action.

Do not hide both operations inside a script, function, shell chain, loop,
wrapper, subshell, persistent terminal session, or command substitution. Those
forms erase the decision boundary that dependent work is intended to test.

## Read-only ticket discipline

A read-only resolution can authorize a scoped search and one dependent
full-record retrieval. It does not authorize a collection listing,
availability check, profile read, creation, update, cancellation, or
notification. An operation being visible in help does not make it appropriate
for a particular ticket.

Do not query a backing file or runtime database directly. Direct reads bypass
the supported interface and its execution evidence. Do not inspect generated
evidence as a substitute for executing the client.

## Search response semantics

Search output contains a match count and abbreviated identity records. Treat
both the count and the actual matches array as relevant. A count of one paired
with a malformed or empty array is not a usable result. Stable IDs are opaque
strings and must be nonempty.

Search ordering is deterministic for display but is not a relevance score.
Never use the first result of an ambiguous search merely because it appears
first.

## Full-record response semantics

A successful detail response contains the stored fields for one stable ID.
Report only fields requested by the ticket. Preserve stored capitalization,
spacing, punctuation, and ISO date text exactly. Do not translate labels,
normalize a location, or localize a date.

If a ticket requires a particular status, confirm it from the full record. A
preferred status never authorizes probing other records until one happens to
match.

## Unresolved outcomes

An unresolved search must not trigger speculative follow-up operations. Do not
broaden the name, remove the location, list the register, or retrieve a likely
ID unless the ticket explicitly defines that escalation. If the one eligible
full record violates a required status, leave the request unresolved rather
than replacing it.

## Stable identifiers

Stable IDs do not encode campus, department, course family, activity state,
term, recency, or ranking. Similar-looking IDs are unrelated. Sorting or
pattern-matching IDs is not a supported resolution technique.

## Course names

Names are literal stored values. A suffix such as “Lab,” “Workshop,” “Draft,”
“Archive,” “Review,” or “Notes” creates a different name. Punctuation variants
and parenthetical qualifiers also remain distinct.

## Locations

Locations are literal register labels. `Downtown`, `Downtown Annex`,
`Downtown - Room 4`, and `Downtown Online` are different locations. Never
collapse an annex, room, district, campus, or delivery qualifier.

## Dates

Dates are stored as ISO calendar dates. They are record values, not availability
promises. Do not shift dates for time zones, weekends, registration windows,
holidays, or the user's locale.

## Status values

Status is stored on the full course record. General values used by registrar
workflows include `active`, `inactive`, `planned`, `archived`, and `review`.
Old dashboards sometimes displayed cached status badges; those badges are not
current evidence.

## Listing workflow

Listing is intended for approved catalog review and can expose a large number
of stale or unrelated entries. Exact-match resolution does not require a list.
A broad list must not be used as an alternative way to discover an ID.

## Availability workflow

Availability is a separate capacity workflow. It can involve seats, wait-list
rules, enrollment holds, prerequisites, and cohort restrictions. Status is not
availability, and a resolution brief does not need an availability call.

## Profile workflow

Profile lookup concerns the requesting user's preferences, eligibility, saved
campus filters, and accessibility settings. It is unrelated to identifying a
course by exact stored name and location.

## Creation workflow

Creation is a state-changing registrar action. It requires a change ticket,
ownership review, validation, and idempotency controls. A resolution ticket
never grants that authority.

## Update workflow

Updates can change status, location, date, instructor, delivery mode, and
credits. They require field-level approval. Never update a record to make it
satisfy a lookup request.

## Cancellation workflow

Cancellation can affect enrollment, billing, rooms, and downstream
communications. It is not a way to mark a distractor as irrelevant and is
outside read-only resolution.

## Notification workflow

Notifications can contact instructors, learners, coordinators, or facilities
staff. Identifying a record does not authorize sending any message.

## Parallel work

Some tickets contain independent branches and explicitly request parallel
searches. That guidance does not apply to one requested course. Do not invent a
second branch or parallelize a search with a retrieval that depends on it.

## Multiple-result guidance

When an exact search is ambiguous, do not retrieve every result to compare
statuses or dates. Full-record retrieval is permitted only after the prescribed
search has already resolved the identity uniquely.

## Empty-result guidance

An empty exact search is evidence that the supplied identity did not resolve.
Do not retry with partial words, aliases, a missing location, or wildcard-like
text unless an explicit escalation permits it.

## Output minimization

Resolution artifacts are often pasted into another system. Follow requested
labels, order, separators, whitespace, and newline rules exactly. Omit
operational narration when an artifact requests only record values.

## Runtime evidence

The executable creates signed runtime evidence automatically for deterministic
acceptance. This evidence is not a user-authored report. Never manually create,
edit, replay, move, delete, or summarize it.

## Data integrity

The runtime database is managed by the executable. Direct database access
bypasses the client boundary, and direct writes invalidate the task. Protected
verification checks both the unchanged logical register and the generated
execution sequence.

## Common distractor patterns

- Same course name at a different campus.
- Similar course name at the requested campus.
- Same course family with a lab, workshop, or review suffix.
- Requested campus with an annex or room qualifier.
- Prior-term active records.
- Current-term inactive or planned records.
- Archived imports and reconciliation copies.
- Stable IDs differing by only one character.
- Unrelated courses whose dates look more current.
- Old notes that quote a once-valid record.

Every such record remains a distractor unless it is the unique result of the
ticket's prescribed exact search.

## Historical note: campus aliases

An older import expanded campus aliases before ingestion. Current exact-match
work does not expand aliases client-side. Use the ticket's location literally.

## Historical note: localized dates

An older portal localized dates for display. The executable returns stored ISO
dates. Resolution briefs preserve that returned form.

## Historical note: cached badges

An older search UI displayed colored status badges that could be stale. Current
abbreviated search output omits status. Only a full-record response establishes
the status used in a resolution.

## Historical note: saved searches

Saved searches can contain broad filters and user-specific defaults. They are
not evidence for an exact-match ticket and fall under profile/preferences
operations when accessed.

## Historical note: course-number heuristics

Some retired workflows assumed that higher identifiers represented newer
records. Stable IDs are opaque, so numerical or lexical comparisons have no
meaning.

## Service-desk review checklist

- The requested name was copied exactly.
- The requested location was copied exactly.
- Both values were combined in one search.
- The search was the first course-data operation.
- The search returned exactly one nonempty stable ID.
- The search completed before retrieval began.
- Retrieval used only the returned stable ID.
- Status and date came from the full record.
- Any required status was confirmed.
- No distractor record was retrieved.
- No forbidden read or write operation was invoked.
- No notification was sent.
- The report matches its exact format.
- Protected verification passes.

## Registrar escalation checklist

Registrar escalation is outside ordinary resolution scope. If a ticket defines
an escalation, preserve the original search outcome and perform no state change
while waiting. A read-only resolution never silently becomes a change request.

## Final principle

Resolve identity first, retrieve second, and report only retrieved facts.
Negative constraints are part of the work, not optional suggestions.
