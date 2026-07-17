"""Regression tests for the incremental backup manifest helpers.

All file mtimes are pinned with os.utime and all backup timestamps are fixed
strings — nothing here reads the clock. Run: python3 test_backman.py
"""
import os
import shutil

from backman import (load_manifest, record_backup, restore_order,
                     save_manifest, scan_changes)

ROOT = "backman_fixture"
MANIFEST_PATH = "backman_manifest.json"  # kept outside the scanned tree


def reset_tree(files):
    """(Re)build the fixture tree; files maps rel path -> (content, mtime)."""
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT)
    for rel, (content, mtime) in files.items():
        full = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.utime(full, (mtime, mtime))


def touch(rel, content, mtime):
    full = os.path.join(ROOT, rel)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.utime(full, (mtime, mtime))


def test_new_files_are_flagged():
    reset_tree({
        "docs/notes.txt": ("alpha\n", 1741000000.0),
        "app.cfg": ("mode = full\n", 1741000000.0),
    })
    manifest = {"entries": {}}
    assert scan_changes(ROOT, manifest) == ["app.cfg", "docs/notes.txt"]


def test_recorded_files_are_skipped_when_untouched():
    reset_tree({
        "docs/notes.txt": ("alpha\n", 1741000000.0),
        "app.cfg": ("mode = full\n", 1741000000.0),
    })
    manifest = {"entries": {}}
    for rel in scan_changes(ROOT, manifest):
        record_backup(manifest, ROOT, rel, "2026-03-03T01:00:00+00:00")
    assert scan_changes(ROOT, manifest) == []


def test_edit_within_the_same_second_is_detected():
    reset_tree({"config.ini": ("retries = 3\n", 1741000000.25)})
    manifest = {"entries": {}}
    record_backup(manifest, ROOT, "config.ini", "2026-03-03T01:00:00+00:00")
    assert scan_changes(ROOT, manifest) == []

    # same byte count, mtime moves 0.5s within the same wall-clock second
    touch("config.ini", "retries = 7\n", 1741000000.75)
    assert scan_changes(ROOT, manifest) == ["config.ini"], \
        "a changed file must be scheduled for backup"


def test_edit_across_a_second_boundary_is_detected():
    reset_tree({"config.ini": ("retries = 3\n", 1741000000.25)})
    manifest = {"entries": {}}
    record_backup(manifest, ROOT, "config.ini", "2026-03-03T01:00:00+00:00")
    touch("config.ini", "retries = 7\n", 1741000001.10)
    assert scan_changes(ROOT, manifest) == ["config.ini"]


def test_size_change_is_detected():
    reset_tree({"data.bin": ("12345", 1741000000.0)})
    manifest = {"entries": {}}
    record_backup(manifest, ROOT, "data.bin", "2026-03-03T01:00:00+00:00")
    touch("data.bin", "1234567", 1741000000.0)
    assert scan_changes(ROOT, manifest) == ["data.bin"]


def test_manifest_survives_a_json_round_trip():
    reset_tree({"docs/notes.txt": ("alpha\n", 1741000042.625)})
    manifest = {"entries": {}}
    record_backup(manifest, ROOT, "docs/notes.txt", "2026-03-03T01:00:00+00:00")
    save_manifest(MANIFEST_PATH, manifest)
    reloaded = load_manifest(MANIFEST_PATH)
    assert scan_changes(ROOT, reloaded) == [], \
        "an untouched file must stay skipped after reloading the manifest"
    assert load_manifest("backman_missing.json") == {"entries": {}}


def test_restore_order_follows_actual_backup_instants():
    # three agents, three regions; offsets differ but the instants are clear:
    # report  2026-03-02T00:15+02:00 == 2026-03-01T22:15Z  (first)
    # draft   2026-03-01T23:30+00:00 == 2026-03-01T23:30Z  (second)
    # pic     2026-03-01T20:45-04:00 == 2026-03-02T00:45Z  (third)
    manifest = {"entries": {
        "notes/report.txt": {"size": 10, "mtime": 1741000000.0,
                             "backed_up_at": "2026-03-02T00:15:00+02:00"},
        "notes/draft.txt": {"size": 11, "mtime": 1741000001.0,
                            "backed_up_at": "2026-03-01T23:30:00+00:00"},
        "media/pic.jpg": {"size": 12, "mtime": 1741000002.0,
                          "backed_up_at": "2026-03-01T20:45:00-04:00"},
    }}
    assert restore_order(manifest) == [
        "notes/report.txt", "notes/draft.txt", "media/pic.jpg"]


def test_restore_order_ties_break_by_path():
    # the same instant stamped by two different regions
    manifest = {"entries": {
        "a.txt": {"size": 1, "mtime": 1741000000.0,
                  "backed_up_at": "2026-03-01T14:00:00+02:00"},
        "b.txt": {"size": 1, "mtime": 1741000000.0,
                  "backed_up_at": "2026-03-01T12:00:00+00:00"},
    }}
    assert restore_order(manifest) == ["a.txt", "b.txt"]


def main():
    tests = [
        test_new_files_are_flagged,
        test_recorded_files_are_skipped_when_untouched,
        test_edit_within_the_same_second_is_detected,
        test_edit_across_a_second_boundary_is_detected,
        test_size_change_is_detected,
        test_manifest_survives_a_json_round_trip,
        test_restore_order_follows_actual_backup_instants,
        test_restore_order_ties_break_by_path,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
