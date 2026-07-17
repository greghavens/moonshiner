"""Behavior checks for the scrub pipeline. Run: python3 test_scrub.py"""
import threading

from scrub import ScrubPipeline


def redact_email(record):
    clean = dict(record)
    clean["email"] = "<redacted>"
    return clean


def test_scrubs_everything_submitted():
    pipe = ScrubPipeline(redact_email, workers=1)
    pipe.submit({"id": "T-1", "email": "ana@example.com"})
    pipe.submit({"id": "T-2", "email": "raj@example.com"})
    got = pipe.drain()
    pipe.close()
    assert sorted(r["id"] for r in got) == ["T-1", "T-2"], got
    assert all(r["email"] == "<redacted>" for r in got), got


def test_drain_covers_slow_records():
    # Region rule sets call out to a regex engine that can take a while on
    # attachment-heavy tickets; simulate one such slow record with a gate.
    gate = threading.Event()

    def redact(record):
        if record["id"] == "T-3":
            gate.wait(timeout=2.0)
        return redact_email(record)

    pipe = ScrubPipeline(redact, workers=1)
    pipe.submit({"id": "T-1", "email": "ana@example.com"})
    pipe.submit({"id": "T-2", "email": "raj@example.com"})
    pipe.submit({"id": "T-3", "email": "kim@example.com"})

    got = pipe.drain()
    gate.set()
    pipe.close()

    ids = sorted(r["id"] for r in got)
    assert ids == ["T-1", "T-2", "T-3"], f"archive would be missing records: {ids}"
    assert all(r["email"] == "<redacted>" for r in got), got


def main():
    test_scrubs_everything_submitted()
    test_drain_covers_slow_records()
    print("all checks passed")


if __name__ == "__main__":
    main()
