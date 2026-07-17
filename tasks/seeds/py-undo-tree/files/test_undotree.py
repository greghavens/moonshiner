"""Acceptance checks for undotree.py. Run: python3 test_undotree.py"""
from undotree import UndoTree


def editor():
    """Root(0)='', 1='hello', 2='hello world'; cursor at 2."""
    t = UndoTree("")
    assert t.commit("hello") == 1
    assert t.commit("hello world") == 2
    return t


def test_linear_commit_undo_redo():
    t = editor()
    assert t.head() == 2
    assert t.current() == "hello world"
    assert t.undo() == "hello"
    assert t.head() == 1
    assert t.undo() == ""
    assert t.redo() == "hello"
    assert t.redo() == "hello world"
    assert t.path() == [0, 1, 2]


def test_undo_at_root_and_redo_at_leaf():
    t = editor()
    try:
        t.redo()
        assert False, "redo past the newest state"
    except IndexError:
        pass
    t.undo()
    t.undo()
    assert t.head() == 0
    try:
        t.undo()
        assert False, "undo past the root"
    except IndexError:
        pass


def test_commit_after_undo_forks():
    t = editor()
    t.undo()                                   # back to 1
    assert t.commit("hello there") == 3        # fork under 1
    assert t.path() == [0, 1, 3]
    t.undo()
    assert t.branches() == [2, 3]              # old future survives
    assert t.redo() == "hello there"           # default = newest branch
    t.undo()
    assert t.redo(branch=2) == "hello world"   # the abandoned branch
    assert t.path() == [0, 1, 2]


def test_redo_branch_must_be_direct_child():
    t = editor()
    t.undo()
    t.commit("hello there")                    # node 3
    t.undo()                                   # cursor at 1, children [2, 3]
    for bad in [0, 1, 99]:
        try:
            t.redo(branch=bad)
            assert False, "redo jumped to non-child %r" % (bad,)
        except ValueError:
            pass


def test_checkout_jumps_anywhere():
    t = editor()
    t.undo()
    t.commit("hello there")                    # node 3
    assert t.checkout(2) == "hello world"
    assert t.head() == 2
    assert t.checkout(3) == "hello there"
    assert t.path() == [0, 1, 3]
    assert t.checkout(0) == ""
    try:
        t.checkout(42)
        assert False, "checked out a ghost node"
    except KeyError:
        pass
    assert t.head() == 0, "failed checkout moved the cursor"


def test_fork_from_interior_checkout():
    t = editor()
    t.checkout(1)
    assert t.commit("hello, world!") == 3
    t.checkout(1)
    assert t.branches() == [2, 3]
    assert t.commit("HELLO") == 4
    t.checkout(1)
    assert t.branches() == [2, 3, 4]
    assert t.redo() == "HELLO"                 # newest of three branches


def test_tree_owns_its_states():
    doc = {"tiles": [1, 2], "name": "castle"}
    t = UndoTree(doc)
    doc["tiles"].append(3)                     # caller keeps mutating
    assert t.current() == {"tiles": [1, 2], "name": "castle"}
    layer = {"tiles": [9], "name": "cave"}
    t.commit(layer)
    layer["name"] = "CHANGED"
    got = t.current()
    assert got == {"tiles": [9], "name": "cave"}
    got["tiles"].clear()                       # scribble on the returned copy
    assert t.current() == {"tiles": [9], "name": "cave"}
    assert t.undo() == {"tiles": [1, 2], "name": "castle"}


def test_ids_keep_counting_across_branches():
    t = UndoTree(0)
    t.commit(10)          # 1
    t.undo()
    t.commit(20)          # 2
    t.undo()
    t.commit(30)          # 3
    assert t.head() == 3
    t.checkout(1)
    assert t.commit(11) == 4
    assert t.path() == [0, 1, 4]


CHECKS = [
    test_linear_commit_undo_redo,
    test_undo_at_root_and_redo_at_leaf,
    test_commit_after_undo_forks,
    test_redo_branch_must_be_direct_child,
    test_checkout_jumps_anywhere,
    test_fork_from_interior_checkout,
    test_tree_owns_its_states,
    test_ids_keep_counting_across_branches,
]


def main():
    failures = 0
    for t in CHECKS:
        try:
            t()
        except Exception as e:
            failures += 1
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
        else:
            print("ok   %s" % t.__name__)
    if failures:
        print("\n%d check(s) failed" % failures)
        raise SystemExit(1)
    print("\nall %d checks passed" % len(CHECKS))


if __name__ == "__main__":
    main()
