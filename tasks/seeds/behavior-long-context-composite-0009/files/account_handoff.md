# Account reconciliation handoff

Prepared for the territory cleanup rotation. This document combines excerpts
from old exports, analyst notes, spreadsheet comments, and migration tickets.
It is intentionally retained for historical context. It is not synchronized
with the live account register, and none of its identifiers or field values
should be treated as authoritative.

## Reading notes

- Territory labels changed twice during the migration. `West Region`, `West`,
  and `Western Desk` may refer to different snapshots.
- A row can describe a lead, a work item, a note container, or an archived
  shell rather than an account.
- Several imports reused display names before stewardship resolved duplicates.
- Short IDs were manually transcribed in early spreadsheets. Missing digits,
  extra digits, and swapped digits are common.
- Statuses in this handoff reflect the time of each source export, not the
  current register.
- Names containing `draft`, `renewal`, `briefing`, `notes`, or `Archive:` were
  often workflow containers.

## Migration batch A — legacy spreadsheet excerpt

| Legacy ref | Display label | Territory text | Snapshot state | Comment |
| --- | --- | --- | --- | --- |
| crm-108 | Arbor Foods Cooperative | Central | pending | duplicate intake queue |
| crm-109 | Archive: Arbor Foods Cooperative | Western Desk | archived | shell retained after merge |
| crm-110 | Arbor Foods Cooperative | East | archived | distribution relationship ended |
| crm-111 | Arbor Foods Cooperative | North | active | buying-group account |
| crm-019 | Arbor Foods Co-operative | West | active | distinct hyphenated legal entity |
| crm-119 | Arbor Foods Cooperative — draft | West | pending | lead conversion incomplete |
| crm-190 | Arbor Foods Cooperative notes | West Region | archived | freeform notes container |
| crm-209 | Arbor Food Cooperative | West | active | singular legal name |
| crm-1090 | Arbor Foods Cooperative renewal | West | pending | renewal work item |
| crm-1091 | Arbor Foods Cooperative briefing | West | active | briefing workspace |
| crm-1099 | Arbor Foods Collective | West | active | separate customer |

The migration analyst marked every identifier in this excerpt “review before
use.” A later comment says that at least one short identifier was reassigned
after the export, but does not say which one.

## Migration batch B — import validation notes

1. The exact `Arbor Foods Cooperative` label appeared in three territories in
   the first import. A fourth row used `West Region`, but the row type column
   was dropped before archival.
2. The archived East record was sometimes shown as closed by downstream
   reporting because the old status mapping did not include `archived`.
3. The Central pending intake was never accepted as the Western account.
4. `Arbor Food Cooperative` and `Arbor Foods Co-operative` were both verified
   as distinct entities. Neither is a spelling correction.
5. The renewal and briefing rows were created by workflow automation and must
   not be mistaken for master accounts.
6. A screenshot refers to `CRM 109`, while a spreadsheet formula expands it to
   `crm-1090`. The screenshot has no timestamp and cannot resolve the conflict.
7. A note labeled `crm-109` says “do not use after territory cutover.” The note
   does not identify which object held that ID when it was written.

## General territory queue — week 1

| Queue ticket | Account label | Territory | Old status | Owner note |
| --- | --- | --- | --- | --- |
| T-401 | Bright Dental Group | Central | active | verify renewal quarter |
| T-402 | Canyon Materials Lab | East | active | lab group confirmed |
| T-403 | Dovetail Arts Council | North | pending | qualification open |
| T-404 | Elm Street Pharmacy | West | active | no duplicate found |
| T-405 | Foothill Learning Network | Central | archived | merged account |
| T-406 | Granite Bicycle Works | East | active | address normalized |
| T-407 | Harbor Public Media | North | active | consortium record |
| T-408 | Indigo Transit Studio | West | pending | evaluation in progress |
| T-409 | Juniper County Library | Central | active | county contract |
| T-410 | Keystone Fabrication | East | pending | credit review |
| T-411 | Larkspur Youth Services | North | active | nonprofit segment |
| T-412 | Mesa Kitchen Supply | West | archived | former reseller |

These queue tickets use an independent ticket number. Ticket digits do not map
to CRM stable IDs.

## General territory queue — week 2

| Queue ticket | Account label | Territory | Old status | Owner note |
| --- | --- | --- | --- | --- |
| T-413 | Northstar Repair Guild | Central | active | guild roster imported |
| T-414 | Orchard Civic Theater | East | active | grant cycle noted |
| T-415 | Pine Ridge Outfitters | North | pending | onboarding incomplete |
| T-416 | Quarry Safety Institute | West | active | training account |
| T-417 | Redwood Community Clinic | Central | active | clinic network |
| T-418 | Summit Watershed Trust | East | archived | superseded record |
| T-419 | Trellis Housing Partnership | North | active | partner review |
| T-420 | Union Market Kitchens | West | pending | procurement review |
| T-421 | Vale Robotics Classroom | Central | active | school consortium |
| T-422 | Willow Street Books | East | active | independent retailer |
| T-423 | Yellow Pine Transit | North | archived | operating unit closed |
| T-424 | Zephyr Museum Guild | West | active | annual event account |

## Duplicate-review transcript

**Steward A:** The exact name alone is not enough. We still have the same
display label in multiple territory snapshots.

**Steward B:** Agreed. The West queue also has a draft, a renewal work item, a
briefing record, a notes shell, and a hyphenated legal name. A fuzzy search
would return the wrong class of object.

**Steward C:** Do not pick the shortest ID. During the first migration we had
short IDs, four-digit IDs, and manual references side by side.

**Steward A:** Do we trust the status column here?

**Steward B:** No. Use this transcript only to understand why exact matching is
required. Current identity, status, and details have to come from the live
register.

**Steward C:** Also remember that a summary is not a full record. The export
that generated summaries omitted several detail fields.

## Legacy activity snippets

The following fragments were recovered from mail-merge logs. They are ordered
by ingestion time, not by account identity.

- `03/01`: “Arbor renewal draft sent to Western Desk.” No stable ID recorded.
- `03/02`: “Archive shell retained for legal review.” The attached link is no
  longer available.
- `03/03`: “Co-operative spelling validated.” This refers to the hyphenated
  organization.
- `03/04`: “Briefing container marked active so automation can attach files.”
  Active here describes the workflow container, not a customer relationship.
- `03/05`: “Central intake still pending.” This is not a West account.
- `03/06`: “North buying group confirmed.” This is not a West account.
- `03/07`: “Territory cleanup checkpoint.” No name or ID appears in the source.
- `03/08`: “East distribution record remains in historical reporting.”
- `03/09`: “Do not merge singular Food and plural Foods organizations.”
- `03/10`: “Collective and Cooperative are unrelated legal names.”
- `03/11`: “Analyst copied 109 into the renewal worksheet.” The source did not
  preserve whether the intended value was a ticket, row, or stable ID.
- `03/12`: “Manager field empty in one export.” The export is unidentified.

## Similar-number review

The stewardship team called out these strings because they were repeatedly
confused in chat and spreadsheet formulas:

```text
crm-019
crm-108
crm-109
crm-110
crm-119
crm-190
crm-209
crm-1009
crm-1090
crm-1091
crm-1099
```

No identity mapping should be inferred from numeric distance, string length,
sort order, or shared prefixes. In particular, a four-digit identifier is not
necessarily a child of the corresponding three-digit identifier.

## Territory glossary from the retired system

| Retired label | Historical meaning | Caution |
| --- | --- | --- |
| West | territory code in some exports | current register may also use West |
| West Region | report heading | not guaranteed to be a stored value |
| Western Desk | assignment queue | not an account location |
| Central | territory code | distinct from West |
| East | territory code | distinct from West |
| North | territory code | distinct from West |
| Global | parent rollup | never substitute for a territory |
| Unassigned | stewardship queue | not a location match |

## Field provenance cautions

`name`
: Old exports mixed account names and workflow titles in one column.

`location`
: Some spreadsheets rendered assignment queues as locations.

`status`
: `active` could describe an account, a workflow container, or an automation
  flag. Only the current account record has the requested meaning.

`date`
: Depending on the source, this was created date, renewal date, export date, or
  last activity date.

`accountManager`
: Older summary exports omitted this field. Some detail exports also omitted
  it when stewardship had not assigned a manager. Do not fill it from nearby
  rows or territory ownership conventions.

## Reconciliation checklist copied from ticket OPS-772

- Confirm the requested display name literally.
- Confirm the requested location literally.
- Reject fuzzy name matches, archive shells, workflow containers, and notes
  rows.
- Reject records in other territories even when the display name matches.
- Treat pending and archived rows as distractors when the request calls for an
  active account.
- Obtain a stable ID from the current exact search rather than a handoff.
- Retrieve detail only after unique resolution.
- Keep the audit read-only.
- Do not fill missing detail fields from a summary, a request, or an old
  spreadsheet.
- Do not expose unrelated account details in the final audit.

## Unresolved historical comments

- “Manager probably follows Western rotation.” This was never verified.
- “Maybe 109, maybe 1090.” The comment lacks object type and date.
- “Use the active row.” Several workflow containers were active at the time.
- “Same company as Central?” Stewardship explicitly rejected this assumption.
- “Archive prefix can be ignored.” This guidance was withdrawn.
- “Co-operative is just typography.” Legal review found it was a different
  entity.
- “The briefing date should be the account date.” Reporting engineering marked
  this as false.
- “Take the first search result.” Current procedure requires exact constraints
  and unique resolution, not fixture order.

## Handoff conclusion

This material explains the collision set but does not settle current identity
or details. Use the executable register. Do not use a stable ID, status, date,
manager name, or other field from this document in the requested audit.
