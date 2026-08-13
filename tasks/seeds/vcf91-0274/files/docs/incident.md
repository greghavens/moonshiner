# INC-48812 — vc-prod-01 objects stopped collecting in VCF Operations

**Reported by:** platform on-call
**Affected resource:** `9d3a1f52-77b0-4c1e-9a44-0f9b2c7e51ad`
(the vCenter adapter instance object for `vc-prod-01.rainpole.io`)

Dashboards for the `vc-prod-01` workload domain went flat. Capacity and health
badges are stale and a large share of the objects under that adapter instance
have dropped out of collection.

Three teams have already offered a cause from memory — a rotated service
account, a cloud proxy that was rebooted during the ToR upgrade, and a
monitoring pause left over from a storage migration. All three have happened on
this fleet before, and each team is confident it is theirs.

VCF Operations already holds the record of what happened. The alert list says
what fired, the contributing symptoms say which conditions the alert was built
from, the symptom records carry the criticality and the start time of each
condition, the alert notes carry what operators wrote down, and the system audit
report carries how many objects are configured versus actually collecting.

The triage runbook is explicit that the cause is whichever contributing
condition **started first**, not whichever one is loudest. A collection lag
symptom is nearly always CRITICAL and nearly always a downstream consequence:
it is the condition that began before it that explains the outage.

Cancelled symptoms and symptoms that are not contributing to the alert are
history, not cause. A contributing symptom that has no matching symptom record
cannot be timed, so it cannot be nominated as the root.
