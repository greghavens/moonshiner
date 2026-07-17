"""Acceptance tests for the unified diff generator. Run: python3 test_udiff.py"""


def parse_range(spec):
    """'1,3' -> (1, 3); '7' -> (7, 1)."""
    if "," in spec:
        start, count = spec.split(",")
        return int(start), int(count)
    return int(spec), 1


def apply_udiff(diff, a):
    """Reference patch applier: strict about format, offsets and counts."""
    if not diff:
        return list(a)
    assert diff[0].startswith("--- ") and diff[1].startswith("+++ "), diff[:2]
    out = []
    ai = 0
    i = 2
    while i < len(diff):
        header = diff[i]
        assert header.startswith("@@ -") and header.endswith(" @@"), header
        old_spec, new_spec = header[4:-3].split(" +")
        old_start, old_count = parse_range(old_spec)
        new_start, new_count = parse_range(new_spec)
        # zero-count ranges anchor on the line BEFORE the change
        copy_to = old_start - 1 if old_count > 0 else old_start
        assert copy_to >= ai, f"hunks overlap or run backwards at {header}"
        while ai < copy_to:
            out.append(a[ai])
            ai += 1
        expected_new = len(out) + 1 if new_count > 0 else len(out)
        assert new_start == expected_new, \
            f"{header}: new side says {new_start}, patched file is at {expected_new}"
        i += 1
        seen_old = seen_new = 0
        while i < len(diff) and not diff[i].startswith("@@ "):
            tag, text = diff[i][0], diff[i][1:]
            if tag == " ":
                assert ai < len(a) and a[ai] == text, f"context mismatch: {diff[i]!r}"
                out.append(text)
                ai += 1
                seen_old += 1
                seen_new += 1
            elif tag == "-":
                assert ai < len(a) and a[ai] == text, f"deletion mismatch: {diff[i]!r}"
                ai += 1
                seen_old += 1
            elif tag == "+":
                out.append(text)
                seen_new += 1
            else:
                assert False, f"bad hunk line: {diff[i]!r}"
            i += 1
        assert (seen_old, seen_new) == (old_count, new_count), \
            f"{header} promises ({old_count},{new_count}), body has ({seen_old},{seen_new})"
    out.extend(a[ai:])
    return out


def roundtrip(a, b, context=3):
    from udiff import unified_diff
    diff = unified_diff(a, b, "old", "new", context=context)
    patched = apply_udiff(diff, a)
    assert patched == b, f"patch does not rebuild target:\n{diff}\ngot {patched}\nwant {b}"
    return diff


def main():
    from udiff import unified_diff

    # -- identical inputs: no output at all, not even headers --
    assert unified_diff(["a", "b"], ["a", "b"], "old", "new") == []
    assert unified_diff([], [], "old", "new") == []

    # -- single replacement, deletions before insertions --
    diff = unified_diff(["alpha", "beta", "gamma"], ["alpha", "BETA", "gamma"],
                        "old", "new")
    assert diff == [
        "--- old",
        "+++ new",
        "@@ -1,3 +1,3 @@",
        " alpha",
        "-beta",
        "+BETA",
        " gamma",
    ], diff

    # -- pure insertion --
    diff = unified_diff(["one", "two", "four"], ["one", "two", "three", "four"],
                        "old", "new")
    assert diff == [
        "--- old",
        "+++ new",
        "@@ -1,3 +1,4 @@",
        " one",
        " two",
        "+three",
        " four",
    ], diff

    # -- pure deletion --
    diff = unified_diff(["one", "two", "three"], ["one", "three"], "old", "new")
    assert diff == [
        "--- old",
        "+++ new",
        "@@ -1,3 +1,2 @@",
        " one",
        "-two",
        " three",
    ], diff

    # -- growing an empty file: the -0,0 convention --
    diff = unified_diff([], ["x", "y"], "old", "new")
    assert diff == [
        "--- old",
        "+++ new",
        "@@ -0,0 +1,2 @@",
        "+x",
        "+y",
    ], diff

    # -- emptying a file: the +0,0 convention --
    diff = unified_diff(["x", "y"], [], "old", "new")
    assert diff == [
        "--- old",
        "+++ new",
        "@@ -1,2 +0,0 @@",
        "-x",
        "-y",
    ], diff

    # -- a count of exactly 1 drops the ',1' --
    diff = unified_diff(["only"], ["changed"], "old", "new")
    assert diff == [
        "--- old",
        "+++ new",
        "@@ -1 +1 @@",
        "-only",
        "+changed",
    ], diff

    # -- far-apart changes with small context split into two hunks --
    a = [f"l{n}" for n in range(1, 10)]
    b = list(a)
    b[1] = "X"       # line 2
    b[7] = "Y"       # line 8
    diff = unified_diff(a, b, "old", "new", context=1)
    assert diff == [
        "--- old",
        "+++ new",
        "@@ -1,3 +1,3 @@",
        " l1",
        "-l2",
        "+X",
        " l3",
        "@@ -7,3 +7,3 @@",
        " l7",
        "-l8",
        "+Y",
        " l9",
    ], diff

    # -- nearby changes share one hunk when the gap fits inside the context --
    a = [f"l{n}" for n in range(1, 8)]
    b = list(a)
    b[1] = "X"       # line 2
    b[4] = "Y"       # line 5
    diff = unified_diff(a, b, "old", "new", context=2)
    assert diff == [
        "--- old",
        "+++ new",
        "@@ -1,7 +1,7 @@",
        " l1",
        "-l2",
        "+X",
        " l3",
        " l4",
        "-l5",
        "+Y",
        " l6",
        " l7",
    ], diff

    # -- context=0: bare change lines, insertion anchored on the line before --
    diff = unified_diff(["a", "b", "c"], ["a", "x", "c"], "old", "new", context=0)
    assert diff == [
        "--- old",
        "+++ new",
        "@@ -2 +2 @@",
        "-b",
        "+x",
    ], diff
    diff = unified_diff(["a", "c"], ["a", "b", "c"], "old", "new", context=0)
    assert diff == [
        "--- old",
        "+++ new",
        "@@ -1,0 +2 @@",
        "+b",
    ], diff

    # -- labels are verbatim --
    diff = unified_diff(["a"], ["b"], "src/app.py (deployed)", "src/app.py (local)")
    assert diff[0] == "--- src/app.py (deployed)"
    assert diff[1] == "+++ src/app.py (local)"

    # -- negative context is rejected --
    try:
        unified_diff(["a"], ["b"], "old", "new", context=-1)
        assert False, "expected ValueError"
    except ValueError:
        pass

    # -- round-trips: whatever the alignment, applying must rebuild b --
    cases = [
        (["a", "b", "c", "d"], ["b", "c", "d", "e"]),
        (["x"] * 5, ["x"] * 3),
        ([""] * 3 + ["end"], [""] * 5 + ["end"]),
        (["dup", "mid", "dup"], ["dup"]),
        (["1", "2", "3", "4", "5", "6"], ["6", "5", "4", "3", "2", "1"]),
        (list("abcdefghij"), list("abXdefYhiZ")),
        ([], ["new file"]),
        (["gone"], []),
        (["same"], ["same"]),
        (["tab\there", "trail "], ["tab\there", "trail", ""]),
    ]
    for a, b in cases:
        for ctx in (0, 1, 3):
            roundtrip(a, b, context=ctx)

    print("all udiff checks passed")


if __name__ == "__main__":
    main()
