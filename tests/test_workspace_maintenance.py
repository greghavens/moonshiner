import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import workspace_maintenance  # noqa: E402


class AcceptedWorkspaceMaintenance(unittest.TestCase):
    def test_removes_only_currently_accepted_task_workspaces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            accepted = root / "task-a"; accepted.mkdir()
            (accepted / "flat-file").write_bytes(b"x" * 20)
            accepted_review = root / "review-task-a-acde123456"
            accepted_review.mkdir()
            pending = root / "task-b"; pending.mkdir()
            with mock.patch.object(workspace_maintenance, "connect") as connect, \
                 mock.patch.object(workspace_maintenance, "accepted_ids",
                                   return_value={"task-a"}):
                connect.return_value.close.return_value = None
                removed, reclaimed = workspace_maintenance.prune(root)
            self.assertEqual(removed, 2)
            self.assertEqual(reclaimed, 20)
            self.assertFalse(accepted.exists())
            self.assertFalse(accepted_review.exists())
            self.assertTrue(pending.exists())


if __name__ == "__main__":
    unittest.main()
