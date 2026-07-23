import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from migrate_canonical_dataset import _generation  # noqa: E402


def test_prior_canonical_revision_is_normalized_as_current():
    row = {
        "task": "example",
        "source_trajectory_id": "example",
        "messages": [{"role": "user", "content": "Do the task"}],
        "tools": "[]",
        # Deliberately omit columns added by the current published schema.
        "assistant_step": 0,
    }

    assert _generation(row) == "current"


def test_whole_session_source_record_is_not_mistaken_for_published_row():
    row = {
        "task": "example",
        "messages": [{"role": "user", "content": "Do the task"}],
        "tools": [],
        "teacher_runtime": "pi",
    }

    assert _generation(row) is None
