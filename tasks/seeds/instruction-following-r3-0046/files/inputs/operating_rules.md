# Dispatch operating rules

- Run date: 2026-09-17.
- Planned minutes are 45 for `cold`, 30 for `hazmat`, and 60 for `standard` work, regardless of unit count or lotting.
- Process active work in ascending priority number, then ship-by time, then order ID.
- For each piece of work in that order, use qualified staff only. Assign it to the qualified person whose station becomes available earliest; break an availability tie by roster order in `staff.csv`. A person's station is dedicated to that person for this handoff.
- Schedule each person's work back-to-back from their shift start. Work must finish within the person's shift and by the order's ship-by time.
- Final check codes are `CHILL` for cold work, `HAZMAT-SEAL` for hazmat work, and `COUNT` for standard work.
- A held order is not scheduled and must be identified as excluded in the shift brief.
- Escalation owner: to be confirmed by the operations lead.
