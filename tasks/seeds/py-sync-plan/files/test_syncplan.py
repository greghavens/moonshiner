"""Acceptance tests for syncplan. Run: python3 test_syncplan.py"""
import copy

from syncplan import plan_sync, render_plan, validate_manifest


def f(size, mtime, checksum=None):
    entry = {"type": "file", "size": size, "mtime": mtime}
    if checksum is not None:
        entry["checksum"] = checksum
    return entry


D = {"type": "dir"}


def test_identical_manifests_plan_nothing():
    m = {"app": D, "app/main.py": f(120, 100.0), "readme.txt": f(10, 50.0)}
    assert plan_sync(m, dict(m)) == []
    assert plan_sync({}, {}) == []


def test_new_tree_orders_parents_before_children():
    src = {"a": D, "a/b": D, "a/b/c.txt": f(1, 1.0), "a/d.txt": f(2, 1.0)}
    assert plan_sync(src, {}) == [
        {"op": "mkdir", "path": "a", "reason": "new"},
        {"op": "mkdir", "path": "a/b", "reason": "new"},
        {"op": "copy", "path": "a/b/c.txt", "reason": "new"},
        {"op": "copy", "path": "a/d.txt", "reason": "new"},
    ]


def test_extra_tree_deletes_deepest_first():
    dst = {"old": D, "old/deep": D, "old/deep/x.dat": f(1, 1.0), "old/y.dat": f(1, 1.0)}
    assert plan_sync({}, dst) == [
        {"op": "delete", "path": "old/y.dat", "reason": "extra"},
        {"op": "delete", "path": "old/deep/x.dat", "reason": "extra"},
        {"op": "rmdir", "path": "old/deep", "reason": "extra"},
        {"op": "rmdir", "path": "old", "reason": "extra"},
    ]


def test_change_detection_rules():
    # no checksums: size or mtime difference means update
    assert plan_sync({"a.txt": f(10, 5.0)}, {"a.txt": f(10, 5.0)}) == []
    assert plan_sync({"a.txt": f(10, 6.0)}, {"a.txt": f(10, 5.0)}) == [
        {"op": "update", "path": "a.txt", "reason": "changed"}]
    assert plan_sync({"a.txt": f(11, 5.0)}, {"a.txt": f(10, 5.0)}) == [
        {"op": "update", "path": "a.txt", "reason": "changed"}]
    # both sides carry checksums: the checksum alone decides
    assert plan_sync({"a.txt": f(10, 9.0, "abc")}, {"a.txt": f(10, 5.0, "abc")}) == []
    assert plan_sync({"a.txt": f(10, 5.0, "abc")}, {"a.txt": f(10, 5.0, "xyz")}) == [
        {"op": "update", "path": "a.txt", "reason": "changed"}]
    # checksum on one side only: fall back to size/mtime
    assert plan_sync({"a.txt": f(10, 5.0, "abc")}, {"a.txt": f(10, 5.0)}) == []


def test_file_replaced_by_directory():
    src = {"cfg": D, "cfg/app.ini": f(3, 1.0)}
    dst = {"cfg": f(9, 1.0)}
    assert plan_sync(src, dst) == [
        {"op": "delete", "path": "cfg", "reason": "type-changed"},
        {"op": "mkdir", "path": "cfg", "reason": "type-changed"},
        {"op": "copy", "path": "cfg/app.ini", "reason": "new"},
    ]


def test_directory_replaced_by_file():
    src = {"cfg": f(9, 1.0)}
    dst = {"cfg": D, "cfg/a.txt": f(1, 1.0), "cfg/sub": D, "cfg/sub/b.txt": f(1, 1.0)}
    assert plan_sync(src, dst) == [
        {"op": "delete", "path": "cfg/sub/b.txt", "reason": "extra"},
        {"op": "rmdir", "path": "cfg/sub", "reason": "extra"},
        {"op": "delete", "path": "cfg/a.txt", "reason": "extra"},
        {"op": "rmdir", "path": "cfg", "reason": "type-changed"},
        {"op": "copy", "path": "cfg", "reason": "type-changed"},
    ]


def mixed_case():
    src = {
        "app": D,
        "app/main.py": f(120, 100.0),
        "app/util.py": f(40, 90.0),
        "media": D,
        "media/logo.png": f(2048, 80.0),
        "readme.txt": f(10, 50.0),
    }
    dst = {
        "app": D,
        "app/main.py": f(120, 100.0),
        "app/util.py": f(41, 90.0),
        "readme.txt": f(10, 50.0),
        "old": D,
        "old/tmp.dat": f(7, 20.0),
    }
    return src, dst


def test_phases_removals_then_mkdirs_then_transfers():
    src, dst = mixed_case()
    assert plan_sync(src, dst) == [
        {"op": "delete", "path": "old/tmp.dat", "reason": "extra"},
        {"op": "rmdir", "path": "old", "reason": "extra"},
        {"op": "mkdir", "path": "media", "reason": "new"},
        {"op": "update", "path": "app/util.py", "reason": "changed"},
        {"op": "copy", "path": "media/logo.png", "reason": "new"},
    ]


def test_inputs_are_not_mutated():
    src, dst = mixed_case()
    src_before, dst_before = copy.deepcopy(src), copy.deepcopy(dst)
    plan_sync(src, dst)
    assert src == src_before and dst == dst_before


def test_render_plan_pinned():
    src, dst = mixed_case()
    assert render_plan(plan_sync(src, dst)) == (
        "delete  old/tmp.dat  (extra)\n"
        "rmdir   old  (extra)\n"
        "mkdir   media  (new)\n"
        "update  app/util.py  (changed)\n"
        "copy    media/logo.png  (new)\n"
    )
    assert render_plan([]) == "(nothing to do)\n"


def test_manifest_validation():
    good = {"a": D, "a/b.txt": f(1, 1.0)}
    validate_manifest(good)  # no exception

    bad_manifests = [
        {"a/b.txt": f(1, 1.0)},                      # parent dir missing
        {"a": f(1, 1.0), "a/b.txt": f(1, 1.0)},      # parent is a file
        {"/abs.txt": f(1, 1.0)},                     # absolute path
        {"a/../b.txt": f(1, 1.0)},                   # dot-dot segment
        {"a/./b.txt": f(1, 1.0)},                    # dot segment
        {"trailing/": D},                            # trailing slash
        {"": f(1, 1.0)},                             # empty path
        {"x": {"type": "symlink"}},                  # unknown type
        {"x": {"type": "file", "mtime": 1.0}},       # file without size
        {"x": {"type": "file", "size": -1, "mtime": 1.0}},
        {"x": {"type": "file", "size": 3, "mtime": "new"}},
    ]
    for bad in bad_manifests:
        try:
            validate_manifest(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"validate_manifest({bad!r}) should raise")
        try:
            plan_sync(bad, {})
        except ValueError:
            pass
        else:
            raise AssertionError(f"plan_sync must validate src manifest: {bad!r}")
        try:
            plan_sync({}, bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"plan_sync must validate dst manifest: {bad!r}")


def main():
    tests = [
        test_identical_manifests_plan_nothing,
        test_new_tree_orders_parents_before_children,
        test_extra_tree_deletes_deepest_first,
        test_change_detection_rules,
        test_file_replaced_by_directory,
        test_directory_replaced_by_file,
        test_phases_removals_then_mkdirs_then_transfers,
        test_inputs_are_not_mutated,
        test_render_plan_pinned,
        test_manifest_validation,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
