"""Preview publisher change detection. Offline — no Hub, no trace parsing.

The repair lane replaces a trace's bytes under an unchanged task id; the
publisher must key on content, not the task list, or a repaired trace only
reaches the Hub if an unrelated new task happens to land in the same cycle.
"""
import json
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


class AcceptanceGate(unittest.TestCase):
    """Only judge-accepted traces publish. A generation-passed, attested trace
    with no accepting verdict — pending, rejected, or a judge-side fault such as
    the spend-limit notice that once replaced every verdict — stays off the Hub.
    """

    def _tree(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="moonshiner-test-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(tmp)]))
        for sub in ("meta", "raw", "reviews"):
            (tmp / sub).mkdir()
        patches = {
            "META_DIR": tmp / "meta", "RAW_DIR": tmp / "raw",
            "REVIEWS_DIR": tmp / "reviews", "CONFIG": {},
        }
        for attr, value in patches.items():
            p = mock.patch.object(pub, attr, value)
            p.start()
            self.addCleanup(p.stop)
        # parse_stream is IO-heavy; any non-empty message list stands in for it.
        p = mock.patch.object(
            pub.PiRuntime, "parse_stream",
            staticmethod(lambda raw, ws: ([{"role": "user", "content": "x"}], {})))
        p.start()
        self.addCleanup(p.stop)
        return tmp

    def _seed(self, tmp, stem, *, review):
        meta = {"passed": True,
                "teacher": {"model_attested": True, "model": "m",
                            "observed_model": "m"}}
        (tmp / "meta" / f"{stem}.json").write_text(json.dumps(meta))
        (tmp / "raw" / f"{stem}.events.jsonl").write_text("{}\n")
        if review is not None:
            (tmp / "reviews" / f"{stem}.json").write_text(json.dumps(review))

    def test_only_accepted_traces_publish(self):
        tmp = self._tree()
        self._seed(tmp, "good", review={"accepted": True, "status": "accepted"})
        self._seed(tmp, "rejected",
                   review={"accepted": False, "status": "review_reject"})
        self._seed(tmp, "judge-walled",
                   review={"accepted": False, "status": "judge_error"})
        self._seed(tmp, "pending", review=None)
        self.assertEqual([r["task"] for r in pub._publishable_rows()], ["good"])


if __name__ == "__main__":
    unittest.main()
