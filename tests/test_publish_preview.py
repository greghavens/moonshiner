"""Preview publisher change detection. Offline — no Hub, no trace parsing.

The repair lane replaces a trace's bytes under an unchanged task id; the
publisher must key on content, not the task list, or a repaired trace only
reaches the Hub if an unrelated new task happens to land in the same cycle.
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

import publish_preview as pub  # noqa: E402

ROW = {"task": "unit-x", "n_messages": 2,
       "messages": [{"role": "user", "content": "hi"}]}
ROW_REPAIRED = {"task": "unit-x", "n_messages": 4,
                "messages": [{"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "fixed"}]}


class ChangeDetection(unittest.TestCase):
    def setUp(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="moonshiner-test-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(tmp)]))
        self.uploads = []
        for attr, value in (
                ("CONFIG", {"publish": {"hf_dataset": "unit/test"}}),
                ("PREVIEW_DIR", tmp),
                ("PREVIEW_FILE", tmp / "traces.jsonl"),
                ("PREVIEW_CARD", tmp / "README.md"),
                ("STATE_FILE", tmp / "state.json"),
                ("build_card", lambda rows, stage: "card"),
                ("_sync_visibility", lambda dataset: None),
                ("_upload", lambda dataset, local, remote:
                    self.uploads.append(remote)),
        ):
            patcher = mock.patch.object(pub, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _publish(self, rows):
        with mock.patch.object(pub, "_publishable_rows",
                               return_value=list(rows)):
            pub.publish_once()

    def test_repaired_trace_republishes_under_same_task_list(self):
        self._publish([ROW])
        self.uploads.clear()
        self._publish([ROW_REPAIRED])  # same task id, different bytes
        self.assertEqual(self.uploads, ["traces.jsonl"])
        self.assertIn('"fixed"', pub.PREVIEW_FILE.read_text())

    def test_unchanged_corpus_uploads_nothing(self):
        self._publish([ROW])
        self.uploads.clear()
        self._publish([ROW])
        self.assertEqual(self.uploads, [])


if __name__ == "__main__":
    unittest.main()
