"""Acceptance tests for the dependency resolver. Run: python3 test_depresolve.py"""


def expect_cycle(graph, want_cycle, want_msg):
    from depresolve import resolve, CycleError
    try:
        resolve(graph)
    except CycleError as e:
        assert e.cycle == want_cycle, f"cycle {e.cycle} != {want_cycle}"
        assert want_msg in str(e), f"{want_msg!r} not in {str(e)!r}"
        return
    assert False, f"expected CycleError for {graph}"


def main():
    from depresolve import resolve, stages, CycleError

    assert issubclass(CycleError, ValueError)

    # -- dependencies come before dependents --
    assert resolve({"app": ["lib"], "lib": ["core"], "core": []}) == \
        ["core", "lib", "app"]
    assert resolve({"b": [], "a": ["b"]}) == ["b", "a"], \
        "topological order beats alphabetical order"

    # -- ties break lexicographically among READY nodes --
    assert resolve({"z": [], "a": [], "m": []}) == ["a", "m", "z"]
    graph = {
        "tool": ["pkg-b", "pkg-a"],
        "pkg-a": ["libc"],
        "pkg-b": ["libc"],
        "libc": [],
    }
    assert resolve(graph) == ["libc", "pkg-a", "pkg-b", "tool"]

    # -- insertion order of the dict must not leak into the result --
    scrambled = {
        "libc": [],
        "pkg-b": ["libc"],
        "tool": ["pkg-b", "pkg-a"],
        "pkg-a": ["libc"],
    }
    assert resolve(scrambled) == resolve(graph)

    # -- deps not listed as keys are implicit leaf nodes --
    assert resolve({"app": ["mystery"]}) == ["mystery", "app"]
    assert resolve({"a": ["x", "y"], "y": ["x"]}) == ["x", "y", "a"]

    # -- duplicate dep entries are harmless --
    assert resolve({"a": ["b", "b", "b"]}) == ["b", "a"]

    # -- trivia --
    assert resolve({}) == []
    assert resolve({"solo": []}) == ["solo"]

    # -- cycles: normalized to start at their smallest member --
    expect_cycle({"a": ["a"]}, ["a"], "a -> a")
    expect_cycle({"d": ["b"], "b": ["c"], "c": ["b"]}, ["b", "c"], "b -> c -> b")
    expect_cycle({"a": ["y"], "y": ["x"], "x": ["y"]}, ["x", "y"], "x -> y -> x")
    expect_cycle(
        {"m": ["n"], "n": ["o"], "o": ["m"], "clean": []},
        ["m", "n", "o"], "m -> n -> o -> m",
    )

    # -- a healthy subgraph does not excuse the cyclic one --
    try:
        resolve({"ok": [], "p": ["q"], "q": ["p"]})
        assert False, "cycle must be fatal even with resolvable nodes present"
    except CycleError:
        pass

    # -- stages: maximal parallel waves, each wave sorted --
    graph = {
        "libc": [],
        "zlib": [],
        "docs": [],
        "pkg": ["libc", "zlib"],
        "app": ["pkg"],
    }
    assert stages(graph) == [["docs", "libc", "zlib"], ["pkg"], ["app"]]
    assert stages({}) == []
    assert stages({"app": ["mystery"]}) == [["mystery"], ["app"]]
    # flattened stages agree with resolve's contract
    flat = [n for stage in stages(graph) for n in stage]
    assert flat == resolve(graph), "stage order and resolve order must agree"
    try:
        stages({"p": ["q"], "q": ["p"]})
        assert False, "stages must report cycles too"
    except CycleError:
        pass

    # -- a wide deterministic graph, twice, same answer --
    wide = {f"n{i:02d}": [f"n{j:02d}" for j in range(i + 1, min(i + 4, 20))]
            for i in range(20)}
    first = resolve(wide)
    assert first == resolve(wide)
    assert first[0] == "n19" and first[-1] == "n00"
    position = {n: i for i, n in enumerate(first)}
    for node, deps in wide.items():
        for dep in deps:
            assert position[dep] < position[node], f"{dep} must precede {node}"

    print("all depresolve checks passed")


if __name__ == "__main__":
    main()
