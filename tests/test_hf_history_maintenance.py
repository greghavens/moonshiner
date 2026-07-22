import datetime
import pathlib
import sys
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hf_history_maintenance import (HistorySafetyError, Snapshot,
                                    deletion_plan, select_snapshots,
                                    validate_manifest, validate_repo_refs)


def commit(name):
    return SimpleNamespace(commit_id=name, title=name,
                           created_at=datetime.datetime(2026, 7, 22,
                                                        tzinfo=datetime.UTC))


class SnapshotRetention(unittest.TestCase):
    def test_keeps_newest_ten_distinct_verified_snapshots(self):
        commits = [commit(f"c{number}") for number in range(12, 0, -1)]
        snapshots = {
            item.commit_id: Snapshot(item.commit_id, item.title, item.created_at,
                                     f"state-{(int(item.commit_id[1:]) + 1) // 2}",
                                     {f"oid-{item.commit_id}"})
            for item in commits
        }
        selected = select_snapshots(
            commits, lambda item: snapshots.get(item.commit_id), keep=5)
        self.assertEqual([item.commit_id for item in selected],
                         ["c12", "c10", "c8", "c6", "c4"])

    def test_invalid_intermediate_commits_are_not_snapshots(self):
        commits = [commit("bad"), commit("good")]
        selected = select_snapshots(
            commits, lambda item: None if item.commit_id == "bad" else
            Snapshot("good", "good", item.created_at, "complete", {"kept"}),
            keep=1)
        self.assertEqual([item.commit_id for item in selected], ["good"])

    def test_refuses_when_requested_verified_history_does_not_exist(self):
        with self.assertRaisesRegex(HistorySafetyError, "only 1 verified"):
            select_snapshots([commit("one")], lambda item: Snapshot(
                "one", "one", item.created_at, "state", {"oid"}), keep=10)

    def test_deletion_plan_preserves_every_object_used_by_retained_snapshots(self):
        retained = [Snapshot("a", "a", None, "a", {"shared", "current"}),
                    Snapshot("b", "b", None, "b", {"shared", "previous"})]
        files = [SimpleNamespace(file_oid=oid, size=size, filename=f"{oid}.bin")
                 for oid, size in (("shared", 10), ("current", 20),
                                   ("previous", 30), ("obsolete", 40))]
        plan = deletion_plan(files, retained)
        self.assertEqual([item.file_oid for item in plan.files], ["obsolete"])
        self.assertEqual(plan.bytes, 40)

    def test_zero_deletion_plan_is_safe_and_explicit(self):
        retained = [Snapshot("a", "a", None, "a", {"only"})]
        plan = deletion_plan(
            [SimpleNamespace(file_oid="only", size=10, filename="only")], retained)
        self.assertEqual(plan.files, [])
        self.assertEqual(plan.bytes, 0)

    def test_manifest_requires_one_complete_description_of_active_shards(self):
        valid = {"schema_version": 1,
                 "format": "moonshiner-parquet-shards-v1",
                 "active_shards": ["data/a.parquet", "data/b.parquet"],
                 "shards": [{"path": "data/a.parquet"},
                            {"path": "data/b.parquet"}]}
        self.assertTrue(validate_manifest(valid, {
            "README.md", "dataset-manifest.json",
            "data/a.parquet", "data/b.parquet"}))
        partial = dict(valid, active_shards=["data/a.parquet"])
        self.assertFalse(validate_manifest(partial, {
            "README.md", "dataset-manifest.json", "data/a.parquet"}))
        missing = dict(valid, active_shards=["data/a.parquet", "data/missing.parquet"])
        self.assertFalse(validate_manifest(missing, {
            "README.md", "dataset-manifest.json", "data/a.parquet"}))

    def test_history_rewrite_refuses_user_managed_refs(self):
        ref = lambda name: SimpleNamespace(ref=name)
        safe = SimpleNamespace(branches=[ref("refs/heads/main")], tags=[],
                               pull_requests=[], converts=[ref("refs/convert/parquet")])
        validate_repo_refs(safe)
        unsafe = SimpleNamespace(
            branches=[ref("refs/heads/main"), ref("refs/heads/archive")],
            tags=[ref("refs/tags/v1")], pull_requests=[ref("refs/pr/1")],
            converts=[])
        with self.assertRaisesRegex(HistorySafetyError, "refs/heads/archive"):
            validate_repo_refs(unsafe)


if __name__ == "__main__":
    unittest.main()
