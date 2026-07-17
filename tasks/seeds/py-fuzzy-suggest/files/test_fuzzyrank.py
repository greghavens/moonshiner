"""Acceptance tests for the fuzzy suggestion ranker. Run: python3 test_fuzzyrank.py"""


COMMANDS = ["status", "start", "stash", "stop", "push", "pull", "commit", "checkout"]


def expect_value_error(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ValueError:
        return
    raise AssertionError(f"{fn.__name__} should raise ValueError for {args!r}")


def main():
    from fuzzyrank import edit_distance, rank, did_you_mean

    # -- Levenshtein distance, unit costs, case-sensitive as given --
    assert edit_distance("kitten", "sitting") == 3
    assert edit_distance("", "abc") == 3
    assert edit_distance("abc", "") == 3
    assert edit_distance("abc", "abc") == 0
    assert edit_distance("flaw", "lawn") == 2
    assert edit_distance("Cat", "cat") == 1        # no case folding here
    assert edit_distance("a", "b") == 1
    assert edit_distance("", "") == 0

    # -- rank: distance beats everything, bonuses break the rest --
    got = rank("stat", COMMANDS)
    assert got == ["start", "status", "stash"], got
    got = rank("stat", COMMANDS, limit=10)
    assert got == ["start", "status", "stash", "stop"], got

    # equal score + distance falls back to alphabetical order — always
    got = rank("rat", ["hat", "cat", "bat"])
    assert got == ["bat", "cat", "hat"], got
    got = rank("rat", ["hat", "cat", "bat"], limit=2)
    assert got == ["bat", "cat"], got

    # -- matching is case-insensitive, results keep original spelling --
    got = rank("stat", ["Status", "START"])
    assert got == ["START", "Status"], got
    got = rank("STASH", COMMANDS, limit=1)
    assert got == ["stash"], got

    # exact (case-insensitive) match always comes first
    got = rank("Push", COMMANDS)
    assert got[0] == "push", got

    # -- prefix completions survive even past max_distance --
    got = rank("che", COMMANDS)
    assert got == ["checkout"], got
    got = rank("dep", ["deploy", "deposit", "grip"])
    assert got == ["deploy", "deposit"], got       # both prefixes, closer first

    # -- max_distance is a hard filter for non-prefix candidates --
    got = rank("helo", ["hello", "help", "shell"], max_distance=1)
    assert got == ["hello", "help"], got
    got = rank("helo", ["hello", "help", "shell"], max_distance=3)
    assert got == ["hello", "help", "shell"], got
    got = rank("zzz", COMMANDS)
    assert got == [], got

    # -- empty queries are refused --
    expect_value_error(rank, "", COMMANDS)
    expect_value_error(did_you_mean, "", COMMANDS)

    # -- did-you-mean output format is part of the contract --
    assert did_you_mean("comit", COMMANDS) == 'did you mean "commit"?'
    assert did_you_mean("statuss", COMMANDS) == 'did you mean "status"?'
    assert did_you_mean("pusj", COMMANDS) == 'did you mean "push"?'

    # the command exists (any case): no suggestion at all
    assert did_you_mean("status", COMMANDS) is None
    assert did_you_mean("STATUS", COMMANDS) is None

    # nothing plausible: no suggestion
    assert did_you_mean("zzzzzz", COMMANDS) is None
    assert did_you_mean("xyzzy", []) is None

    # respects a custom max_distance
    assert did_you_mean("pux", COMMANDS, max_distance=1) is None
    assert did_you_mean("pux", COMMANDS, max_distance=2) == 'did you mean "pull"?'

    print("ok")


if __name__ == "__main__":
    main()
