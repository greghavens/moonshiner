"""Contract tests for the sub-workflow composition runner — protected file.

The module under test (reusewf.py, not written yet) executes the composition
subset of our workflow DSL: `run: workflow:` tasks that invoke other
workflows from a registry by name + exact version, input contracts declared
by the callee, output exports, cycle detection, and a depth cap. Handlers
are plain callables in a dict — no I/O of any kind in here.
"""
import pytest
import yaml

from reusewf import (
    CycleError,
    DepthError,
    InputContractError,
    LoadError,
    NotFoundError,
    UnknownHandlerError,
    run_workflow,
)


def wf(name, version, do, input_decl=None, output=None, dsl="1.0", drop=None):
    """Build a workflow document as YAML text."""
    header = {"dsl": dsl, "namespace": "etl", "name": name, "version": version}
    if drop:
        del header[drop]
    doc = {"document": header}
    if input_decl is not None:
        doc["input"] = input_decl
    doc["do"] = do
    if output is not None:
        doc["output"] = output
    return yaml.safe_dump(doc, sort_keys=False)


def run_task(name, version, input=None):
    spec = {"name": name, "version": version}
    if input is not None:
        spec["input"] = input
    return {"run": {"workflow": spec}}


TRIVIAL_DO = [{"noop": {"set": {"ok": True}}}]


# ------------------------------------------------------------------ loading


def test_load_rejects_wrong_dsl_version():
    with pytest.raises(LoadError):
        run_workflow(wf("x", "1.0.0", TRIVIAL_DO, dsl="0.9"))


@pytest.mark.parametrize("missing", ["namespace", "name", "version"])
def test_load_requires_header_fields(missing):
    with pytest.raises(LoadError):
        run_workflow(wf("x", "1.0.0", TRIVIAL_DO, drop=missing))


def test_load_rejects_duplicate_task_names():
    with pytest.raises(LoadError) as exc:
        run_workflow(wf("x", "1.0.0", [
            {"twice": {"set": {"k": 1}}},
            {"twice": {"set": {"k": 2}}},
        ]))
    assert "twice" in str(exc.value)


def test_load_reserves_the_input_task_name():
    with pytest.raises(LoadError):
        run_workflow(wf("x", "1.0.0", [{"input": {"set": {"k": 1}}}]))


def test_load_requires_exactly_one_type_key():
    with pytest.raises(LoadError):
        run_workflow(wf("x", "1.0.0", [
            {"both": {"set": {"k": 1}, "call": "probe"}},
        ]))
    with pytest.raises(LoadError):
        run_workflow(wf("x", "1.0.0", [{"neither": {}}]))


def test_load_rejects_unknown_output_export():
    with pytest.raises(LoadError) as exc:
        run_workflow(wf("x", "1.0.0", TRIVIAL_DO, output=["nosuch"]))
    assert "nosuch" in str(exc.value)


def test_run_task_shape_is_validated():
    # no name
    with pytest.raises(LoadError):
        run_workflow(wf("x", "1.0.0", [
            {"r": {"run": {"workflow": {"version": "1.0.0"}}}},
        ]))
    # no version
    with pytest.raises(LoadError):
        run_workflow(wf("x", "1.0.0", [
            {"r": {"run": {"workflow": {"name": "child"}}}},
        ]))
    # unknown key under workflow
    with pytest.raises(LoadError):
        run_workflow(wf("x", "1.0.0", [
            {"r": {"run": {"workflow": {
                "name": "child", "version": "1.0.0", "args": {}}}}},
        ]))
    # run without workflow
    with pytest.raises(LoadError):
        run_workflow(wf("x", "1.0.0", [{"r": {"run": {}}}]))


def test_registry_rejects_duplicate_name_version():
    copy1 = wf("mapper", "1.0.0", TRIVIAL_DO)
    copy2 = wf("mapper", "1.0.0", [{"other": {"set": {"k": 2}}}])
    with pytest.raises(LoadError) as exc:
        run_workflow(wf("x", "1.0.0", TRIVIAL_DO), registry=[copy1, copy2])
    assert "mapper" in str(exc.value)


def test_registry_entries_are_validated_eagerly():
    bad = wf("never-called", "1.0.0", TRIVIAL_DO, dsl="0.5")
    with pytest.raises(LoadError):
        run_workflow(wf("x", "1.0.0", TRIVIAL_DO), registry=[bad])


# ------------------------------------------------------------------- basics


def test_set_and_call_results_accumulate():
    seen = {}

    def probe(args):
        seen.update(args)
        return {"rows": 3}

    res = run_workflow(wf("x", "1.0.0", [
        {"base": {"set": {"region": "${ .input.region }"}}},
        {"fetch": {"call": "probe", "with": {"r": "${ .base.region }"}}},
    ], input_decl={"required": ["region"]}),
        input={"region": "us-east"}, handlers={"probe": probe})
    assert seen == {"r": "us-east"}
    assert res["context"] == {
        "input": {"region": "us-east"},
        "base": {"region": "us-east"},
        "fetch": {"rows": 3},
    }


def test_unknown_handler_is_typed_and_named():
    with pytest.raises(UnknownHandlerError) as exc:
        run_workflow(wf("x", "1.0.0", [{"f": {"call": "nowhere"}}]))
    assert "nowhere" in str(exc.value)


def test_root_input_is_validated_against_its_own_contract():
    src = wf("x", "1.0.0", [
        {"got": {"set": {"r": "${ .input.region }"}}},
    ], input_decl={"required": ["region"]})
    with pytest.raises(InputContractError):
        run_workflow(src, input={})
    with pytest.raises(InputContractError):
        run_workflow(src, input=[1, 2])
    res = run_workflow(src, input={"region": "x"})
    assert res["context"]["got"] == {"r": "x"}


# -------------------------------------------------------------- composition


def test_child_output_lands_under_the_task_name():
    child = wf("loader", "1.0.0", [
        {"got": {"set": {"region": "${ .input.region }"}}},
    ], input_decl={"required": ["region"]})
    parent = wf("pa", "1.0.0", [
        {"l": run_task("loader", "1.0.0", input={"region": "eu-west"})},
    ])
    res = run_workflow(parent, registry=[child])
    assert res["context"]["l"] == {"got": {"region": "eu-west"}}


def test_resolution_is_by_name_and_exact_version():
    v1 = wf("mapper", "1.0.0", [{"tag": {"set": {"v": "one"}}}])
    v2 = wf("mapper", "2.0.0", [{"tag": {"set": {"v": "two"}}}])
    parent = wf("pa", "1.0.0", [{"m": run_task("mapper", "2.0.0")}])
    res = run_workflow(parent, registry=[v1, v2])
    assert res["context"]["m"] == {"tag": {"v": "two"}}
    parent1 = wf("pa", "1.0.0", [{"m": run_task("mapper", "1.0.0")}])
    res1 = run_workflow(parent1, registry=[v1, v2])
    assert res1["context"]["m"] == {"tag": {"v": "one"}}


def test_unknown_workflow_name_is_not_found():
    parent = wf("pa", "1.0.0", [{"m": run_task("ghost", "1.0.0")}])
    with pytest.raises(NotFoundError) as exc:
        run_workflow(parent, registry=[])
    assert "ghost" in str(exc.value)


def test_missing_version_lists_the_available_ones():
    v1 = wf("mapper", "1.0.0", TRIVIAL_DO)
    v2 = wf("mapper", "2.0.0", TRIVIAL_DO)
    parent = wf("pa", "1.0.0", [{"m": run_task("mapper", "9.9.9")}])
    with pytest.raises(NotFoundError) as exc:
        run_workflow(parent, registry=[v1, v2])
    msg = str(exc.value)
    assert "mapper" in msg and "9.9.9" in msg
    assert "1.0.0" in msg and "2.0.0" in msg


def test_missing_required_input_key_is_a_contract_error():
    child = wf("loader", "1.0.0", TRIVIAL_DO,
               input_decl={"required": ["region"], "optional": ["limit"]})
    parent = wf("pa", "1.0.0", [
        {"l": run_task("loader", "1.0.0", input={"limit": 5})},
    ])
    with pytest.raises(InputContractError) as exc:
        run_workflow(parent, registry=[child])
    msg = str(exc.value)
    assert "loader" in msg and "region" in msg


def test_unexpected_input_key_is_a_contract_error():
    child = wf("loader", "1.0.0", TRIVIAL_DO,
               input_decl={"required": ["region"]})
    parent = wf("pa", "1.0.0", [
        {"l": run_task("loader", "1.0.0",
                       input={"region": "x", "junk": 1})},
    ])
    with pytest.raises(InputContractError) as exc:
        run_workflow(parent, registry=[child])
    assert "junk" in str(exc.value)


def test_optional_keys_may_be_given_or_omitted():
    child = wf("loader", "1.0.0", [
        {"got": {"set": {"region": "${ .input.region }",
                         "limit": "${ .input.limit }"}}},
    ], input_decl={"required": ["region"], "optional": ["limit"]})
    with_opt = wf("pa", "1.0.0", [
        {"l": run_task("loader", "1.0.0",
                       input={"region": "x", "limit": 5})},
    ])
    without_opt = wf("pa", "1.0.0", [
        {"l": run_task("loader", "1.0.0", input={"region": "x"})},
    ])
    res = run_workflow(with_opt, registry=[child])
    assert res["context"]["l"]["got"] == {"region": "x", "limit": 5}
    res = run_workflow(without_opt, registry=[child])
    assert res["context"]["l"]["got"] == {"region": "x", "limit": None}


def test_child_without_input_decl_accepts_no_input_keys():
    child = wf("plain", "1.0.0", TRIVIAL_DO)
    rejecting = wf("pa", "1.0.0", [
        {"p": run_task("plain", "1.0.0", input={"x": 1})},
    ])
    with pytest.raises(InputContractError):
        run_workflow(rejecting, registry=[child])
    accepting = wf("pa", "1.0.0", [{"p": run_task("plain", "1.0.0")}])
    res = run_workflow(accepting, registry=[child])
    assert res["context"]["p"] == {"noop": {"ok": True}}


def test_input_maps_from_parent_context_and_child_is_isolated():
    child = wf("peek", "1.0.0", [
        {"look": {"set": {"stolen": "${ .prep.secret }",
                          "mine": "${ .input.given }"}}},
    ], input_decl={"required": ["given"]})
    parent = wf("pa", "1.0.0", [
        {"prep": {"set": {"secret": 42}}},
        {"p": run_task("peek", "1.0.0",
                       input={"given": "${ .prep.secret }"})},
    ])
    res = run_workflow(parent, registry=[child])
    # the mapping is evaluated against the parent's context (42 flows in);
    # the parent's tasks themselves are invisible inside the child
    assert res["context"]["p"]["look"] == {"stolen": None, "mine": 42}


def test_output_exports_filter_what_the_caller_sees():
    child = wf("loader", "1.0.0", [
        {"fetch": {"set": {"rows": 3}}},
        {"scratch": {"set": {"tmp": 1}}},
    ], output=["fetch"])
    parent = wf("pa", "1.0.0", [
        {"l": run_task("loader", "1.0.0")},
    ], output=["l"])
    res = run_workflow(parent, registry=[child])
    assert res["context"]["l"] == {"fetch": {"rows": 3}}
    assert "scratch" not in res["context"]["l"]
    # the same export rule shapes the root run's output
    assert res["output"] == {"l": {"fetch": {"rows": 3}}}


def test_default_output_is_the_context_minus_input():
    child = wf("loader", "1.0.0", [
        {"a": {"set": {"x": 1}}},
        {"b": {"set": {"y": 2}}},
    ])
    parent = wf("pa", "1.0.0", [{"l": run_task("loader", "1.0.0")}])
    res = run_workflow(parent, registry=[child])
    assert res["context"]["l"] == {"a": {"x": 1}, "b": {"y": 2}}
    assert res["output"] == {"l": {"a": {"x": 1}, "b": {"y": 2}}}


def test_three_levels_of_nesting_flow_data_through():
    leaf = wf("leaf", "1.0.0", [
        {"got": {"set": {"n": "${ .input.n }"}}},
    ], input_decl={"required": ["n"]})
    mid = wf("mid", "1.0.0", [
        {"lf": run_task("leaf", "1.0.0", input={"n": "${ .input.n }"})},
    ], input_decl={"required": ["n"]})
    root = wf("root", "1.0.0", [
        {"md": run_task("mid", "1.0.0", input={"n": 7})},
    ])
    res = run_workflow(root, registry=[leaf, mid])
    assert res["context"]["md"] == {"lf": {"got": {"n": 7}}}


def test_repeated_and_diamond_reuse_is_not_a_cycle():
    util = wf("util", "1.0.0", [{"u": {"set": {"ok": True}}}])
    c1 = wf("c1", "1.0.0", [{"a": run_task("util", "1.0.0")}])
    c2 = wf("c2", "1.0.0", [{"b": run_task("util", "1.0.0")}])
    parent = wf("pa", "1.0.0", [
        {"first": run_task("c1", "1.0.0")},
        {"second": run_task("c2", "1.0.0")},
        {"third": run_task("util", "1.0.0")},
        {"fourth": run_task("util", "1.0.0")},
    ])
    res = run_workflow(parent, registry=[util, c1, c2])
    assert res["context"]["first"] == {"a": {"u": {"ok": True}}}
    assert res["context"]["fourth"] == {"u": {"ok": True}}


def test_handlers_are_shared_with_children():
    seen = []

    def emit(args):
        seen.append(args)
        return {"sent": True}

    leaf = wf("leaf", "1.0.0", [
        {"send": {"call": "emit", "with": {"tag": "${ .input.tag }"}}},
    ], input_decl={"required": ["tag"]})
    parent = wf("pa", "1.0.0", [
        {"p": run_task("leaf", "1.0.0", input={"tag": "night-run"})},
    ])
    res = run_workflow(parent, registry=[leaf], handlers={"emit": emit})
    assert seen == [{"tag": "night-run"}]
    assert res["context"]["p"] == {"send": {"sent": True}}


# ------------------------------------------------------------------- safety


def test_direct_self_recursion_reports_the_chain():
    loop = wf("loop", "1.0.0", [{"again": run_task("loop", "1.0.0")}])
    with pytest.raises(CycleError) as exc:
        run_workflow(loop, registry=[loop])
    assert "loop@1.0.0 -> loop@1.0.0" in str(exc.value)


def test_mutual_recursion_reports_the_offending_chain():
    pa = wf("pa", "1.0.0", [{"down": run_task("ch", "1.0.0")}])
    ch = wf("ch", "1.0.0", [{"up": run_task("pa", "1.0.0")}])
    with pytest.raises(CycleError) as exc:
        run_workflow(pa, registry=[pa, ch])
    assert "pa@1.0.0 -> ch@1.0.0 -> pa@1.0.0" in str(exc.value)


def chain_of(n):
    """w0 runs w1 runs ... w{n-1}; returns (root_source, registry_sources)."""
    regs = []
    for i in range(1, n):
        if i == n - 1:
            do = [{"leaf": {"set": {"depth": i}}}]
        else:
            do = [{"next": run_task(f"w{i + 1}", "1.0.0")}]
        regs.append(wf(f"w{i}", "1.0.0", do))
    if n > 1:
        root_do = [{"next": run_task("w1", "1.0.0")}]
    else:
        root_do = [{"leaf": {"set": {"depth": 0}}}]
    return wf("w0", "1.0.0", root_do), regs


def test_depth_cap_counts_active_frames():
    root, regs = chain_of(3)
    with pytest.raises(DepthError) as exc:
        run_workflow(root, registry=regs, max_depth=2)
    assert "2" in str(exc.value)
    res = run_workflow(root, registry=regs, max_depth=3)
    assert res["context"]["next"] == {"next": {"leaf": {"depth": 2}}}


def test_default_depth_cap_is_eight():
    root, regs = chain_of(9)
    with pytest.raises(DepthError):
        run_workflow(root, registry=regs)
    root, regs = chain_of(8)
    res = run_workflow(root, registry=regs)
    ctx = res["context"]
    for _ in range(7):
        ctx = ctx["next"]
    assert ctx == {"leaf": {"depth": 7}}
