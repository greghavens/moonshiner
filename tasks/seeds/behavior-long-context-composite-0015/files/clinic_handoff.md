# Clinic appointment reconciliation handoff

Prepared for the rotating records team. This note combines several exports,
desk annotations, and old queue summaries. It is intentionally not a current
source of truth. Stable identifiers below belong to records that were visible
in earlier queues; gaps and nearby numbers are normal and must not be filled
by inference. Use the appointment register for current identity, status, and
complete detail.

## Operating notes carried forward

- Exact appointment-name matching is case-sensitive.
- Clinic names are separate locations even when they share scheduling staff.
- A pending intake is not an active appointment.
- An archived encounter must never be substituted for a current appointment.
- Words such as “Child”, “waitlist”, “consultation”, and “Archive” are part of
  the appointment name, not harmless annotations.
- Similar stable IDs often refer to different clinics or service lines.
- Queue position and numeric adjacency do not establish record identity.
- Search summaries were abbreviated in the old export.
- Clinician assignments sometimes appear only in complete records.
- Blank clinician cells in old notes can mean unassigned, redacted, or stale.
- Do not treat this note as evidence that a requested field exists.
- A full record may omit an optional field that another record contains.

## Stale exception queue

The following lines were retained for historical routing. They are distractor
context, not a lookup result.

| Old queue ID | Appointment label | Clinic | Old state | Desk note |
|---|---|---|---|---|
| dent-4709 | Preventative Dental Cleaning | Cedar Clinic | active | Spelling variant; keep separate |
| dent-4710 | Preventive Dental Cleaning - Child | Cedar Clinic | pending | Guardian confirmation |
| dent-4711 | Preventive Dental Consultation | Cedar Clinic | active | Consultation, not cleaning |
| dent-4712 | Archive: Preventive Dental Cleaning | Cedar Clinic | archived | Legacy import |
| dent-4713 | Preventive Dental Cleaning (waitlist) | Cedar Clinic | pending | Waitlist only |
| dent-4718 | Preventive Dental Cleaning | Birch Clinic | pending | Eligibility review |
| dent-4729 | Preventive Dental Cleaning | Elm Clinic | archived | Closed historical encounter |
| dent-4819 | Preventive Dental Cleaning | Pine Clinic | active | Different clinic |
| dent-3101 | Comprehensive Oral Exam | Cedar Clinic | active | New-patient workflow |
| dent-3102 | Bitewing Radiographs | Cedar Clinic | active | Imaging queue |
| dent-3103 | Fluoride Varnish | Birch Clinic | pending | Consent review |
| dent-3104 | Periodontal Maintenance | Elm Clinic | active | Perio schedule |
| dent-3105 | Dental Sealant Visit | Pine Clinic | archived | Prior-year encounter |
| dent-3106 | Night Guard Fitting | Cedar Clinic | active | Scan planned |
| dent-3107 | Crown Preparation | Birch Clinic | pending | Preauthorization |
| dent-3108 | Composite Restoration | Elm Clinic | active | Restorative chair |
| dent-3109 | Emergency Tooth Assessment | Pine Clinic | active | Same-day block |
| dent-3110 | Root Canal Consultation | Cedar Clinic | archived | External referral |
| dent-3111 | Denture Adjustment | Birch Clinic | active | Adjustment chair |
| dent-3112 | Oral Cancer Screening | Elm Clinic | pending | Referral review |
| dent-3113 | Implant Follow-up | Pine Clinic | active | Surgical follow-up |
| dent-3114 | Orthodontic Records | Cedar Clinic | active | Records visit |
| dent-3115 | Post-operative Check | Birch Clinic | archived | Closed follow-up |

## Scheduling-desk fragments

These fragments came from daily operational notes. The labels were copied by
hand and may no longer reflect current status.

### Week 1

- Cedar: comprehensive exam blocks moved to the morning.
- Birch: preventive queue included a pending eligibility case.
- Elm: the archived cleaning should remain visible to audit staff only.
- Pine: active cleaning slots were handled by a different scheduling pool.
- Cedar: “Preventative” and “Preventive” were confirmed as distinct labels.
- Cedar: pediatric cleaning intake remained pending.
- Elm: restorative chair maintenance ended Tuesday.
- Pine: emergency assessment blocks remained active.

### Week 2

- Cedar: radiograph appointments were split from exam appointments.
- Birch: consent forms delayed fluoride varnish intake.
- Elm: periodontal maintenance retained its standard duration.
- Pine: sealant history was archived after migration.
- Cedar: night-guard scans were routed to the prosthetic queue.
- Birch: crown-preparation preauthorization remained incomplete.
- Elm: the complete record, not the desk note, governs clinician data.
- Pine: implant follow-up is unrelated to preventive cleaning.

### Week 3

- Cedar: the consultation label must not be normalized to cleaning.
- Birch: a same-name cleaning entry belongs to Birch, not Cedar.
- Elm: a same-name cleaning entry was historical and archived.
- Pine: a same-name cleaning entry was active at Pine only.
- Cedar: waitlist text is part of the stored appointment name.
- Birch: post-operative check history is not a current encounter.
- Elm: screening referral paperwork was still under review.
- Pine: urgent visits remained in the same register.

### Week 4

- Cedar: records staff noted a gap in the 47xx sequence.
- Birch: the number before a Cedar-like number did not imply linkage.
- Elm: the number after a Cedar-like number belonged to a different clinic.
- Pine: another nearby 48xx number belonged to an active Pine record.
- Cedar: no one should reconstruct an ID from these notes.
- Birch: exact location remains mandatory during discovery.
- Elm: current status must come from the executable response.
- Pine: full details require the stable ID returned by discovery.

## Retired reconciliation ledger

The retired ledger grouped work by coordinator initials rather than by record
identity. Initials, chair labels, and dates below are context only.

| Batch | Coordinator | Clinic | Service family | Historical disposition |
|---|---|---|---|---|
| R-001 | AB | Cedar Clinic | diagnostics | reviewed |
| R-002 | BC | Birch Clinic | prevention | waiting |
| R-003 | CD | Elm Clinic | periodontics | reviewed |
| R-004 | DE | Pine Clinic | urgent care | reviewed |
| R-005 | EF | Cedar Clinic | prosthetics | reviewed |
| R-006 | FG | Birch Clinic | restorative | waiting |
| R-007 | GH | Elm Clinic | restorative | reviewed |
| R-008 | HJ | Pine Clinic | surgery | reviewed |
| R-009 | JK | Cedar Clinic | orthodontics | reviewed |
| R-010 | KL | Birch Clinic | follow-up | closed |
| R-011 | LM | Elm Clinic | screening | waiting |
| R-012 | MN | Pine Clinic | prevention | reviewed |
| R-013 | NP | Cedar Clinic | consultation | reviewed |
| R-014 | PQ | Birch Clinic | prevention | waiting |
| R-015 | QR | Elm Clinic | history | closed |
| R-016 | RS | Pine Clinic | prevention | reviewed |
| R-017 | ST | Cedar Clinic | pediatric | waiting |
| R-018 | TU | Birch Clinic | prosthetics | reviewed |
| R-019 | UV | Elm Clinic | diagnostics | reviewed |
| R-020 | VW | Pine Clinic | follow-up | reviewed |
| R-021 | WX | Cedar Clinic | prevention | escalated |
| R-022 | XY | Birch Clinic | restorative | waiting |
| R-023 | YZ | Elm Clinic | periodontics | reviewed |
| R-024 | ZA | Pine Clinic | urgent care | reviewed |

## Migration observations

1. The legacy system allowed free-text labels, so punctuation variants survived.
2. A later migration preserved stable IDs but did not make numbering semantic.
3. Old queue exports mixed active, pending, and archived rows.
4. Clinic transfers created same-name records at different locations.
5. Some old sheets used “provider” where the new record uses a more specific
   optional field.
6. Missing optional fields were sometimes rendered as blank cells.
7. The current audit convention is to label a requested absent field unknown.
8. Search output is a discovery summary and should not supply report fields.
9. The complete-record response is the reporting source.
10. The complete record must be retrieved only after a unique search result.
11. Retrieval by a guessed or copied stable ID is not an acceptable shortcut.
12. Administrative list and profile operations are outside appointment audits.
13. Availability is a scheduling action, not an identity check.
14. Reschedule, cancel, archive, and notification operations change shared state.
15. No shared-state change is needed to reconcile one appointment.

## Old clinic alias notes

Aliases in this section are documentation history only. The live register uses
the exact clinic names shown by its interface.

- “Cedar” once referred to a building, a team, or a fax queue depending on the
  export. It is not a substitute for the exact location `Cedar Clinic`.
- “Birch” shared an intake coordinator with Cedar for two weeks.
- “Elm Dental” was shortened to `Elm Clinic` during migration.
- “Pine annex” records were consolidated under `Pine Clinic`.
- Cross-clinic staff coverage does not change appointment location.
- A staff member named in a record at one clinic says nothing about another
  appointment's optional clinician field.

## Final handoff cautions

This document is deliberately broad so the next auditor can recognize common
failure modes. It is not complete and it is not current. In particular:

- do not browse every appointment;
- do not select a row because its number looks closest;
- do not select a same-name record from another clinic;
- do not select a near-name record at Cedar Clinic;
- do not promote a pending or archived row to active;
- do not report an optional field from a neighboring record;
- do not retrieve details before exact discovery completes;
- do not perform both dependent reads in one execution action;
- do not alter shared clinic state;
- do use the executable's returned data for the final audit.

End of stale handoff.
