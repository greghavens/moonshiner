"""Acceptance tests for the undo/redo command stack. Run: python3 test_undostack.py"""


class SetCell:
    """Realistic command: set doc[key] = value, remembering how to revert."""

    def __init__(self, doc, key, value):
        self.doc, self.key, self.value = doc, key, value
        self._had_key = False
        self._prev = None

    def apply(self):
        self._had_key = self.key in self.doc
        self._prev = self.doc.get(self.key)
        self.doc[self.key] = self.value

    def revert(self):
        if self._had_key:
            self.doc[self.key] = self._prev
        else:
            del self.doc[self.key]


class Traced:
    """Command that just logs its lifecycle."""

    def __init__(self, name, log):
        self.name, self.log = name, log

    def apply(self):
        self.log.append(f"+{self.name}")

    def revert(self):
        self.log.append(f"-{self.name}")


def expect_runtime_error(fn, what):
    try:
        fn()
        assert False, f"{what} should raise RuntimeError"
    except RuntimeError:
        pass


def main():
    from undostack import UndoStack

    # -- execute applies immediately; undo/redo round-trip the document --
    doc = {}
    s = UndoStack()
    s.execute(SetCell(doc, "a", 1))
    assert doc == {"a": 1}
    s.execute(SetCell(doc, "a", 2))
    s.execute(SetCell(doc, "b", 7))
    assert doc == {"a": 2, "b": 7}
    assert s.can_undo and not s.can_redo
    assert s.undo_depth == 3 and s.redo_depth == 0

    assert s.undo() is True
    assert doc == {"a": 2}, "undoing SetCell(b) must remove the key it created"
    assert s.undo() is True
    assert doc == {"a": 1}, "undoing an overwrite must restore the old value"
    assert s.can_redo and s.redo_depth == 2
    assert s.redo() is True
    assert doc == {"a": 2}
    assert s.redo() is True
    assert doc == {"a": 2, "b": 7}
    assert s.redo() is False, "nothing left to redo"

    # -- undo on empty stack: polite False, no exception --
    s = UndoStack()
    assert s.undo() is False
    assert s.redo() is False

    # -- a fresh execute invalidates the whole redo branch --
    log = []
    s = UndoStack()
    s.execute(Traced("A", log))
    s.execute(Traced("B", log))
    assert s.undo()
    assert s.redo_depth == 1
    s.execute(Traced("C", log))
    assert s.redo_depth == 0 and not s.can_redo
    assert s.redo() is False
    assert log == ["+A", "+B", "-B", "+C"], log
    assert s.undo_depth == 2                    # A and C

    # -- transactions: one undo unit, reverse-order revert, forward redo --
    log = []
    s = UndoStack()
    s.execute(Traced("setup", log))
    s.begin()
    s.execute(Traced("t1", log))
    s.execute(Traced("t2", log))
    s.execute(Traced("t3", log))
    s.commit()
    assert log == ["+setup", "+t1", "+t2", "+t3"], "commands apply as executed"
    assert s.undo_depth == 2, "the transaction is a single undo unit"
    assert s.undo() is True
    assert log[-3:] == ["-t3", "-t2", "-t1"], log
    assert s.undo() is True                     # setup
    assert s.redo() is True                     # setup again
    assert s.redo() is True
    assert log[-3:] == ["+t1", "+t2", "+t3"], "redo replays the group in order"

    # -- rollback reverts the open transaction and leaves no undo entry --
    doc = {"x": 0}
    s = UndoStack()
    s.execute(SetCell(doc, "x", 1))
    s.begin()
    s.execute(SetCell(doc, "x", 100))
    s.execute(SetCell(doc, "y", 200))
    assert doc == {"x": 100, "y": 200}
    s.rollback()
    assert doc == {"x": 1}, doc
    assert s.undo_depth == 1
    assert s.undo() is True
    assert doc == {"x": 0}, doc

    # -- executes inside a rolled-back transaction still clear redo --
    s = UndoStack()
    log = []
    s.execute(Traced("A", log))
    s.undo()
    assert s.can_redo
    s.begin()
    s.execute(Traced("tmp", log))
    s.rollback()
    assert not s.can_redo, "the redo branch died the moment new work appeared"

    # -- an empty transaction leaves no trace --
    s = UndoStack()
    s.begin()
    s.commit()
    assert s.undo_depth == 0 and s.undo() is False

    # -- nested transactions fold into the enclosing one --
    log = []
    s = UndoStack()
    s.begin()
    s.execute(Traced("outer1", log))
    s.begin()
    s.execute(Traced("inner", log))
    s.commit()                                   # inner folds into outer
    s.execute(Traced("outer2", log))
    s.commit()
    assert s.undo_depth == 1
    s.undo()
    assert log[-3:] == ["-outer2", "-inner", "-outer1"], log

    # -- rolling back an inner transaction spares the outer one --
    log = []
    s = UndoStack()
    s.begin()
    s.execute(Traced("keep1", log))
    s.begin()
    s.execute(Traced("drop", log))
    s.rollback()
    assert log[-1] == "-drop"
    s.execute(Traced("keep2", log))
    s.commit()
    assert s.undo_depth == 1
    s.undo()
    assert log[-2:] == ["-keep2", "-keep1"], log

    # -- guard rails --
    s = UndoStack()
    expect_runtime_error(s.commit, "commit without begin")
    expect_runtime_error(s.rollback, "rollback without begin")
    log = []
    s.execute(Traced("A", log))
    s.begin()
    expect_runtime_error(s.undo, "undo inside an open transaction")
    expect_runtime_error(s.redo, "redo inside an open transaction")
    s.commit()

    print("all undostack checks passed")


if __name__ == "__main__":
    main()
