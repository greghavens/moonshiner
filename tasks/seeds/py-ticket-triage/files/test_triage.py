"""Behavior checks for the triage module. Run: python3 test_triage.py"""
from triage import Router, escalation_chain, triage


def main():
    # Escalation depends only on the ticket being triaged.
    sev3 = {"id": "T-100", "severity": 3, "subject": "checkout is down"}
    got = escalation_chain(sev3)
    assert got == ["duty-manager", "oncall-engineer"], (
        f"sev-3 ticket should page manager then oncall, got {got!r}")

    quiet = {"id": "T-101", "severity": 0, "subject": "how do I export a report?"}
    got = escalation_chain(quiet)
    assert got == [], f"a low-severity question must page nobody, got {got!r}"

    refund = {"id": "T-102", "severity": 2, "subject": "Refund posted twice"}
    got = escalation_chain(refund)
    assert got == ["duty-manager", "payments-lead"], (
        f"sev-2 refund should page manager and payments lead only, got {got!r}")

    # Overrides configured on one tenant's router stay on that router.
    acme = Router()
    acme.add_override("invoice", "acme-billing-desk")
    globex = Router()
    got = globex.route({"subject": "invoice overdue"})
    assert got == "general-support", (
        f"tenant B must not see tenant A's overrides, got {got!r}")
    got = acme.route({"subject": "invoice overdue"})
    assert got == "acme-billing-desk", f"tenant A override should apply, got {got!r}"

    # End-to-end triage of consecutive tickets: no carry-over between them.
    first = triage({"id": "a", "severity": 3, "subject": "outage in eu-west"})
    assert first["queue"] == "sre-frontline", f"outage should route to SRE, got {first!r}"
    assert first["escalate"] == ["duty-manager", "oncall-engineer"], (
        f"sev-3 outage escalation wrong: {first['escalate']!r}")

    second = triage({"id": "b", "severity": 1, "subject": "password reset loop"})
    assert second["escalate"] == [], (
        f"sev-1 ticket right after a sev-3 one must page nobody, got {second['escalate']!r}")
    assert second["queue"] == "general-support", f"got {second!r}"

    print("all checks passed")


if __name__ == "__main__":
    main()
