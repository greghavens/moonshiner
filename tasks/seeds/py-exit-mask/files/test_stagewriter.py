"""Behavior contract for StageSession / export_records."""

from stagewriter import CleanupError, StageSession, export_records


class ScriptedBackend:
    def __init__(self, fail_finalize=False, fail_discard=False):
        self.calls = []
        self.fail_finalize = fail_finalize
        self.fail_discard = fail_discard

    def open(self):
        self.calls.append("open")

    def write(self, record):
        self.calls.append(("write", record))

    def finalize(self):
        self.calls.append("finalize")
        if self.fail_finalize:
            raise CleanupError("finalize failed: staging volume offline")

    def discard(self):
        self.calls.append("discard")
        if self.fail_discard:
            raise CleanupError("discard failed: staging volume offline")

    def terminal_calls(self):
        return [c for c in self.calls if c in ("finalize", "discard")]


def test_success_path_finalizes_once():
    backend = ScriptedBackend()
    count = export_records(backend, ["a", "b", "c"], str.upper)
    assert count == 3
    assert backend.calls == [
        "open",
        ("write", "A"),
        ("write", "B"),
        ("write", "C"),
        "finalize",
    ]
    assert backend.terminal_calls() == ["finalize"]


def test_session_state_after_success():
    backend = ScriptedBackend()
    with StageSession(backend) as session:
        session.write("x")
    assert session.is_open is False
    assert session.cleanup_error is None
    assert session.written == 1


def test_body_error_discards_and_propagates():
    backend = ScriptedBackend()
    caught = None
    try:
        with StageSession(backend) as session:
            session.write("one")
            raise ValueError("bad record 17")
    except Exception as err:
        caught = err
    assert type(caught) is ValueError and caught.args == ("bad record 17",)
    assert backend.terminal_calls() == ["discard"]
    assert session.is_open is False
    assert session.cleanup_error is None


def test_cleanup_error_does_not_mask_primary():
    backend = ScriptedBackend(fail_discard=True)
    caught = None
    try:
        with StageSession(backend) as session:
            raise ValueError("bad record 41")
    except Exception as err:
        caught = err
    assert type(caught) is ValueError, (
        f"visible failure should be the processing error, got {type(caught).__name__}"
    )
    assert caught.args == ("bad record 41",)
    assert isinstance(session.cleanup_error, CleanupError)
    assert session.cleanup_error.__context__ is caught
    assert backend.terminal_calls() == ["discard"], backend.terminal_calls()
    assert session.is_open is False, "session must not stay marked open"


def test_finalize_error_is_the_failure_with_no_fallback_discard():
    backend = ScriptedBackend(fail_finalize=True)
    caught = None
    try:
        with StageSession(backend) as session:
            session.write("x")
    except Exception as err:
        caught = err
    assert type(caught) is CleanupError
    assert backend.terminal_calls() == ["finalize"], backend.terminal_calls()
    assert session.is_open is False
    assert caught.__context__ is None


def test_export_records_propagates_transform_error():
    backend = ScriptedBackend(fail_discard=True)

    def bad(record):
        raise KeyError(record)

    caught = None
    try:
        export_records(backend, ["r9"], bad)
    except Exception as err:
        caught = err
    assert type(caught) is KeyError
    assert caught.args == ("r9",)
    assert backend.terminal_calls() == ["discard"]


def test_write_after_close_rejected():
    backend = ScriptedBackend()
    with StageSession(backend) as session:
        pass
    caught = None
    try:
        session.write("late")
    except Exception as err:
        caught = err
    assert type(caught) is RuntimeError
    assert backend.terminal_calls() == ["finalize"]


def main():
    tests = [fn for name, fn in sorted(list(globals().items())) if name.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok {fn.__name__}")
    print(f"{len(tests)} checks passed")


if __name__ == "__main__":
    main()
