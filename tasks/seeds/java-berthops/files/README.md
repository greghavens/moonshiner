# berthops — Harwick bulk terminal berth desk

Booking and dispatch service for the quay wall. Layout:

- `api/` — the desk facade agents call, plus the availability board view
- `domain/` — vessels, berths, bookings, tariff and window policy
- `store/` — registers, the booking ledger, cached board snapshots
- `jobs/` — tug dispatch orders and the notification hub

Times are whole hour slots on the shared day board (no wall clock anywhere);
money is integer cents. `TestMain.java` is the acceptance suite:

```
java TestMain.java
```
