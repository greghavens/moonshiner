"""Behavior checks for pick-list reconciliation. Run: python3 test_reconcile.py"""
from reconcile import parse_scan_log, reconcile


def main():
    # A stuttered event (same seq+sku twice in a row) counts once.
    stutter_log = "\n".join([
        "10,CBL-USB-2M,2",
        "11,CBL-USB-2M,3",
        "11,CBL-USB-2M,3",
        "12,PSU-650W,1",
    ])
    counts = parse_scan_log(stutter_log)
    assert counts == {"CBL-USB-2M": 5, "PSU-650W": 1}, (
        f"stuttered line must be counted once, got {counts!r}")
    problems = reconcile([("CBL-USB-2M", 5), ("PSU-650W", 1)], stutter_log)
    assert problems == [], f"counts match the pick list, got {problems!r}"

    # Re-scanning the same sku later (different seq) is a real second event.
    resume_log = "20,LBL-ROLL,2\n21,PSU-650W,1\n22,LBL-ROLL,2"
    counts = parse_scan_log(resume_log)
    assert counts == {"LBL-ROLL": 4, "PSU-650W": 1}, f"got {counts!r}"

    # A big pallet order that matches exactly is clean.
    pallet_log = "30,PLT-STD-40,150\n31,PLT-STD-40,150"
    problems = reconcile([("PLT-STD-40", 300)], pallet_log)
    assert problems == [], (
        f"300 expected, 300 scanned — nothing to report, got {problems!r}")

    # Genuine mismatches are still caught.
    short_log = "40,BIN-A4,3"
    problems = reconcile([("BIN-A4", 4), ("TAPE-48MM", 2)], short_log)
    assert problems == [("BIN-A4", 4, 3), ("TAPE-48MM", 2, 0)], f"got {problems!r}"

    # Extra sku that nobody ordered shows up with expected=0.
    extra_log = "50,BIN-A4,4\n51,DOCK-XL,1"
    problems = reconcile([("BIN-A4", 4)], extra_log)
    assert problems == [("DOCK-XL", 0, 1)], f"got {problems!r}"

    print("all checks passed")


if __name__ == "__main__":
    main()
