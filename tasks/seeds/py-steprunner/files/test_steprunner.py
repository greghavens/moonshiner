"""Contract tests for the workflow data-flow runner — protected file.

The module under test (steprunner.py, not written yet) executes the
data-flow subset of our workflow DSL: set/call tasks, input/output
transforms at both workflow and task level, and the interpolation rules.
Everything is injected — the handler registry is a plain dict — so there
is no I/O of any kind in here.
"""
import textwrap

import pytest

from steprunner import (
    ExpressionError,
    LoadError,
    UnknownHandlerError,
    run_workflow,
)


HDR = """\
document:
  dsl: "1.0"
  namespace: etl
  name: unit-flow
"""


def run(body, input=None, handlers=None, full=False):
    """Dedent the YAML body and prepend the standard header (unless full)."""
    doc = textwrap.dedent(body)
    if not full:
        doc = HDR + doc
    return run_workflow(doc, input=input, handlers=handlers or {})


# ------------------------------------------------------------------ loading


def test_load_rejects_wrong_dsl_version():
    with pytest.raises(LoadError):
        run("""\
            document:
              dsl: "0.9"
              namespace: etl
              name: x
            do:
              - a:
                  set: {k: 1}
            """, full=True)


@pytest.mark.parametrize("missing", ["namespace", "name"])
def test_load_requires_document_fields(missing):
    doc = {"namespace": "etl", "name": "x"}
    del doc[missing]
    lines = "".join(f"  {k}: {v}\n" for k, v in doc.items())
    with pytest.raises(LoadError):
        run_workflow('document:\n  dsl: "1.0"\n' + lines +
                     "do:\n  - a:\n      set: {k: 1}\n", handlers={})


def test_load_requires_nonempty_do():
    with pytest.raises(LoadError):
        run("do: []\n")


def test_load_rejects_duplicate_task_names():
    with pytest.raises(LoadError) as exc:
        run("""\
            do:
              - twice:
                  set: {k: 1}
              - twice:
                  set: {k: 2}
            """)
    assert "twice" in str(exc.value)


def test_load_reserves_the_input_task_name():
    with pytest.raises(LoadError):
        run("""\
            do:
              - input:
                  set: {k: 1}
            """)


def test_load_rejects_task_with_two_or_zero_type_keys():
    with pytest.raises(LoadError):
        run("""\
            do:
              - both:
                  set: {k: 1}
                  call: probe
            """)
    with pytest.raises(LoadError):
        run("""\
            do:
              - neither:
                  input:
                    from: "${ . }"
            """)


# ---------------------------------------------------------------- data flow


def test_set_results_accumulate_and_input_lives_under_input():
    res = run("""\
        do:
          - base:
              set: {region: "${ .input.region }", fixed: 2}
          - more:
              set: {again: "${ .base.fixed }"}
        """, input={"region": "us-east"})
    assert res["context"] == {
        "input": {"region": "us-east"},
        "base": {"region": "us-east", "fixed": 2},
        "more": {"again": 2},
    }


def test_call_gets_evaluated_args_and_result_is_stored_raw():
    seen = {}

    def probe(args):
        seen.update(args)
        return {"rows": [1, 2, 3], "note": "ok"}

    res = run("""\
        do:
          - fetch:
              call: probe
              with: {region: "${ .input.region }", limit: 10}
        """, input={"region": "eu-west"}, handlers={"probe": probe})
    assert seen == {"region": "eu-west", "limit": 10}
    assert res["context"]["fetch"] == {"rows": [1, 2, 3], "note": "ok"}


def test_call_without_with_gets_empty_args():
    seen = []

    def probe(args):
        seen.append(args)
        return 1

    run("""\
        do:
          - fetch:
              call: probe
        """, handlers={"probe": probe})
    assert seen == [{}]


def test_unknown_handler_is_a_typed_error():
    with pytest.raises(UnknownHandlerError) as exc:
        run("""\
            do:
              - fetch:
                  call: nowhere
            """)
    assert "nowhere" in str(exc.value)


def test_handler_exceptions_propagate():
    def probe(args):
        raise RuntimeError("upstream said no")

    with pytest.raises(RuntimeError, match="upstream said no"):
        run("""\
            do:
              - fetch:
                  call: probe
            """, handlers={"probe": probe})


# ------------------------------------------------------- task input.from


def test_task_input_from_string_narrows_the_view():
    seen = {}

    def send(args):
        seen.update(args)
        return None

    run("""\
        do:
          - fetch:
              set: {rows: 42, junk: true}
          - ship:
              call: send
              with: {n: "${ .rows }"}
              input:
                from: "${ .fetch }"
        """, handlers={"send": send})
    assert seen == {"n": 42}


def test_task_input_from_object_map_builds_the_view():
    res = run("""\
        do:
          - a:
              set: {x: 1}
          - b:
              set: {y: 2}
          - joined:
              set: {sum_src: "${ .left } and ${ .right }"}
              input:
                from:
                  left: "${ .a.x }"
                  right: "${ .b.y }"
        """)
    assert res["context"]["joined"] == {"sum_src": "1 and 2"}


def test_task_input_from_replaces_not_merges():
    seen = {}

    def send(args):
        seen.update(args)
        return None

    run("""\
        do:
          - ship:
              call: send
              with:
                old: "${ .input.region }"
                new: "${ .only }"
              input:
                from: {only: "${ .input.city }"}
        """, input={"region": "us-east", "city": "fresno"},
        handlers={"send": send})
    # The replaced view has no .input anymore: the old context must be
    # invisible, not merged in.
    assert seen == {"old": None, "new": "fresno"}


# ------------------------------------------------------ task output.from


def test_task_output_from_reshapes_before_store():
    def probe(args):
        return {"rows": [5, 6], "meta": {"count": 2, "cursor": "abc"}}

    res = run("""\
        do:
          - fetch:
              call: probe
              output:
                from: {n: "${ .meta.count }"}
        """, handlers={"probe": probe})
    assert res["context"]["fetch"] == {"n": 2}


def test_task_output_from_single_expression_stores_raw_value():
    def probe(args):
        return {"rows": [5, 6], "meta": {}}

    res = run("""\
        do:
          - fetch:
              call: probe
              output:
                from: "${ .rows }"
        """, handlers={"probe": probe})
    assert res["context"]["fetch"] == [5, 6]


def test_output_from_sees_the_result_not_the_context():
    def probe(args):
        return {"value": 9}

    res = run("""\
        do:
          - first:
              set: {value: 1}
          - fetch:
              call: probe
              output:
                from: {got: "${ .value }", other: "${ .first.value }"}
        """, handlers={"probe": probe})
    # .value comes from the raw result; .first.value is not addressable here.
    assert res["context"]["fetch"] == {"got": 9, "other": None}


# ------------------------------------- workflow-level input/output transforms


def test_workflow_input_from_shapes_the_initial_input():
    res = run("""\
        input:
          from: "${ .payload }"
        do:
          - probe:
              set: {region: "${ .input.region }"}
        """, input={"payload": {"region": "ap-south"}, "noise": True})
    assert res["context"]["input"] == {"region": "ap-south"}
    assert res["context"]["probe"] == {"region": "ap-south"}


def test_workflow_output_from_shapes_result_output():
    res = run("""\
        do:
          - fetch:
              set: {rows: 3}
        output:
          from: {total: "${ .fetch.rows }"}
        """)
    assert res["output"] == {"total": 3}
    # the context itself stays complete
    assert res["context"]["fetch"] == {"rows": 3}


def test_without_workflow_output_the_output_is_the_context():
    res = run("""\
        do:
          - fetch:
              set: {rows: 3}
        """, input={"seq": 1})
    assert res["output"] == res["context"]
    assert res["output"] == {"input": {"seq": 1}, "fetch": {"rows": 3}}


def test_transforms_compose_end_to_end():
    def rows_for(args):
        assert args == {"region": "us-east"}
        return {"count": 42, "rows": ["a", "b"]}

    res = run("""\
        input:
          from: "${ .payload }"
        do:
          - normalize:
              set:
                region: "${ .input.region }"
                label: "run-${ .input.seq }"
          - fetch:
              call: rows_for
              with: {region: "${ .normalize.region }"}
              output:
                from: {n: "${ .count }"}
        output:
          from:
            summary: "${ .normalize.label }: ${ .fetch.n } rows"
            region: "${ .normalize.region }"
        """, input={"payload": {"region": "us-east", "seq": 12}, "noise": 1},
        handlers={"rows_for": rows_for})
    assert res["output"] == {"summary": "run-12: 42 rows", "region": "us-east"}
    assert res["context"] == {
        "input": {"region": "us-east", "seq": 12},
        "normalize": {"region": "us-east", "label": "run-12"},
        "fetch": {"n": 42},
    }


# ------------------------------------------------------------ interpolation


def test_single_expression_yields_raw_typed_values():
    res = run("""\
        do:
          - vals:
              set:
                n: "${ .input.n }"
                flag: "${ .input.flag }"
                obj: "${ .input.obj }"
                items: "${ .input.items }"
                gone: "${ .input.missing }"
        """, input={"n": 7, "flag": True, "obj": {"a": 1}, "items": [1, 2]})
    assert res["context"]["vals"] == {
        "n": 7,
        "flag": True,
        "obj": {"a": 1},
        "items": [1, 2],
        "gone": None,
    }


def test_mixed_text_always_yields_a_string():
    res = run("""\
        do:
          - msg:
              set:
                line: "order ${ .input.id }/${ .input.region }"
                flag: "dry=${ .input.dry }"
                hole: "<${ .input.missing }>"
        """, input={"id": 7, "region": "us-east", "dry": True})
    assert res["context"]["msg"] == {
        "line": "order 7/us-east",
        "flag": "dry=true",
        "hole": "<>",
    }


def test_identity_expression_yields_the_whole_view():
    res = run("""\
        do:
          - snap:
              set: {copy: "${ . }"}
              input:
                from: {a: "${ .input.a }"}
        """, input={"a": 5})
    assert res["context"]["snap"] == {"copy": {"a": 5}}


def test_index_paths_and_out_of_range():
    res = run("""\
        do:
          - pick:
              set:
                second: "${ .input.items[1].sku }"
                beyond: "${ .input.items[9] }"
                text: "got ${ .input.items[9] }!"
        """, input={"items": [{"sku": "A"}, {"sku": "B"}]})
    assert res["context"]["pick"] == {
        "second": "B",
        "beyond": None,
        "text": "got !",
    }


def test_nested_structures_evaluate_recursively():
    res = run("""\
        do:
          - built:
              set:
                body:
                  region: "${ .input.region }"
                  tags: ["${ .input.region }", fixed]
        """, input={"region": "us-east"})
    assert res["context"]["built"] == {
        "body": {"region": "us-east", "tags": ["us-east", "fixed"]},
    }


@pytest.mark.parametrize("bad", [
    '"${ .a..b }"',
    '"${ .a"',
    '"${ a }"',
    '"${ .items[x] }"',
])
def test_malformed_expressions_raise(bad):
    with pytest.raises(ExpressionError):
        run(f"""\
            do:
              - odd:
                  set: {{v: {bad}}}
            """)


def test_interpolating_a_structure_into_text_raises():
    with pytest.raises(ExpressionError):
        run("""\
            do:
              - odd:
                  set: {v: "structure: ${ .input.obj }"}
            """, input={"obj": {"a": 1}})
