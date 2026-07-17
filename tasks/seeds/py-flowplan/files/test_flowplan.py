"""Contract tests for the workflow execution planner — protected file.

compile_plan(text) turns a validated workflow document into a flat
execution plan: {"tasks": [...]} with jump targets resolved to indices.
PlanError carries the exact diagnostics asserted here.
"""
import textwrap

import pytest

from flowplan import PlanError, compile_plan


HEADER = 'document:\n  dsl: "1.0"\n  namespace: ops\n  name: sample\n'


def plan(y):
    return compile_plan(HEADER + textwrap.dedent(y))


def err(y):
    with pytest.raises(PlanError) as excinfo:
        plan(y)
    return str(excinfo.value)


# ------------------------------------------------------------ linear plans

def test_linear_three_tasks():
    p = plan("""\
    do:
      - fetch:
          call: http
      - stamp:
          set:
            seen: "yes"
      - ship:
          wait: PT1S
    """)
    assert p == {"tasks": [
        {"index": 0, "name": "fetch", "type": "call", "next": 1},
        {"index": 1, "name": "stamp", "type": "set", "next": 2},
        {"index": 2, "name": "ship", "type": "wait", "next": "end"},
    ]}


def test_single_task_plan():
    p = plan("do:\n  - only:\n      call: http\n")
    assert p["tasks"][0]["next"] == "end"


def test_then_continue_is_the_default():
    p = plan("""\
    do:
      - a:
          call: http
          then: continue
      - b:
          wait: PT1S
    """)
    assert p["tasks"][0]["next"] == 1


def test_then_end_stops_early():
    p = plan("""\
    do:
      - route:
          switch:
            - when: ${ .skip }
              then: wrap
            - then: work
      - work:
          call: http
          then: end
      - wrap:
          set:
            done: "yes"
    """)
    assert p["tasks"][1]["next"] == "end"
    assert p["tasks"][2]["next"] == "end"


# ------------------------------------------------------- switch compilation

def test_switch_targets_and_fallthrough():
    p = plan("""\
    do:
      - route:
          switch:
            - when: ${ .rush }
              then: heavy
            - then: light
      - heavy:
          call: big
          then: merge
      - light:
          call: small
      - merge:
          set:
            ok: "yes"
    """)
    route = p["tasks"][0]
    assert route["type"] == "switch"
    assert route["cases"] == [
        {"when": "${ .rush }", "target": 1},
        {"when": None, "target": 2},
    ]
    assert route["next"] == 1  # fall-through is always computed
    assert [t["next"] for t in p["tasks"][1:]] == [3, 3, "end"]


def test_case_then_continue_points_past_the_switch():
    p = plan("""\
    do:
      - route:
          switch:
            - when: ${ .x }
              then: continue
            - then: end
      - after:
          call: http
    """)
    assert p["tasks"][0]["cases"] == [
        {"when": "${ .x }", "target": 1},
        {"when": None, "target": "end"},
    ]


def test_switch_as_last_task_falls_through_to_end():
    p = plan("""\
    do:
      - route:
          switch:
            - then: end
    """)
    assert p["tasks"][0]["next"] == "end"
    assert p["tasks"][0]["cases"] == [{"when": None, "target": "end"}]


def test_retry_loop_through_a_switch_is_legal():
    # redo jumps back to the switch; conditional routing breaks the cycle.
    p = plan("""\
    do:
      - route:
          switch:
            - when: ${ .retry }
              then: redo
            - then: end
      - redo:
          call: refresh
          then: route
    """)
    assert p["tasks"][1]["next"] == 0


# ---------------------------------------------------------- for compilation

def test_for_body_is_a_nested_plan():
    p = plan("""\
    do:
      - sweep:
          for:
            each: line
            in: ${ .lines }
            at: idx
            do:
              - check:
                  call: stock
              - hold:
                  wait: PT2S
    """)
    sweep = p["tasks"][0]
    assert sweep["type"] == "for"
    assert sweep["each"] == "line"
    assert sweep["in"] == "${ .lines }"
    assert sweep["at"] == "idx"
    assert sweep["next"] == "end"
    assert sweep["body"] == {"tasks": [
        {"index": 0, "name": "check", "type": "call", "next": 1},
        {"index": 1, "name": "hold", "type": "wait", "next": "end"},
    ]}


def test_for_without_at_records_none():
    p = plan("""\
    do:
      - sweep:
          for:
            each: item
            in: ${ .items }
            do:
              - ping:
                  call: http
    """)
    assert p["tasks"][0]["at"] is None


def test_end_inside_a_body_ends_the_iteration_only():
    p = plan("""\
    do:
      - sweep:
          for:
            each: item
            in: ${ .items }
            do:
              - bail:
                  call: http
                  then: end
      - after:
          wait: PT1S
    """)
    assert p["tasks"][0]["body"]["tasks"][0]["next"] == "end"
    assert p["tasks"][0]["next"] == 1


# ------------------------------------------------------------- whole plans

def test_full_workflow_compiles_to_the_documented_plan():
    p = plan("""\
    do:
      - fetch:
          call: http
      - route:
          switch:
            - when: ${ .fetch.rush }
              then: notify
            - when: ${ .fetch.bulk }
              then: sweep
            - then: pause
      - notify:
          emit:
            type: order.rush
          then: pause
      - sweep:
          for:
            each: line
            in: ${ .fetch.lines }
            at: idx
            do:
              - check:
                  call: stock
              - hold:
                  wait: PT2S
      - pause:
          wait: PT1S
      - stamp:
          set:
            done: "yes"
      - ship:
          run:
            shell:
              command: bin/ship
      - fail:
          raise:
            error:
              status: 500
              type: never
              title: should not get here
    """)
    assert p == {"tasks": [
        {"index": 0, "name": "fetch", "type": "call", "next": 1},
        {"index": 1, "name": "route", "type": "switch", "next": 2, "cases": [
            {"when": "${ .fetch.rush }", "target": 2},
            {"when": "${ .fetch.bulk }", "target": 3},
            {"when": None, "target": 4},
        ]},
        {"index": 2, "name": "notify", "type": "emit", "next": 4},
        {"index": 3, "name": "sweep", "type": "for", "next": 4,
         "each": "line", "in": "${ .fetch.lines }", "at": "idx",
         "body": {"tasks": [
             {"index": 0, "name": "check", "type": "call", "next": 1},
             {"index": 1, "name": "hold", "type": "wait", "next": "end"},
         ]}},
        {"index": 4, "name": "pause", "type": "wait", "next": 5},
        {"index": 5, "name": "stamp", "type": "set", "next": 6},
        {"index": 6, "name": "ship", "type": "run", "next": 7},
        {"index": 7, "name": "fail", "type": "raise", "next": "end"},
    ]}


# ------------------------------------------------------------- jump cycles

def test_two_task_cycle():
    msg = err("""\
    do:
      - a:
          call: http
      - b:
          call: http
          then: a
    """)
    assert msg == "jump cycle detected: a -> b -> a"


def test_self_cycle():
    msg = err("""\
    do:
      - a:
          call: http
          then: a
    """)
    assert msg == "jump cycle detected: a -> a"


def test_cycle_report_starts_at_its_lowest_indexed_member():
    # start leads INTO the loop but is not part of it.
    msg = err("""\
    do:
      - start:
          call: http
          then: m
      - m:
          call: http
          then: n
      - n:
          call: http
          then: m
    """)
    assert msg == "jump cycle detected: m -> n -> m"


def test_cycles_are_reported_before_unreachable_tasks():
    msg = err("""\
    do:
      - a:
          call: http
          then: b
      - b:
          call: http
          then: a
      - c:
          call: http
    """)
    assert msg == "jump cycle detected: a -> b -> a"


def test_body_cycles_surface_before_outer_analyses():
    msg = err("""\
    do:
      - outer:
          for:
            each: x
            in: ${ .xs }
            do:
              - ping:
                  call: a
              - pong:
                  call: b
                  then: ping
          then: end
      - orphan:
          call: http
    """)
    assert msg == "jump cycle detected: ping -> pong -> ping"


# ------------------------------------------------------- unreachable tasks

def test_unreachable_after_then_end():
    msg = err("""\
    do:
      - a:
          call: http
          then: end
      - b:
          call: http
    """)
    assert msg == "unreachable tasks: b"


def test_unreachable_names_come_in_index_order():
    msg = err("""\
    do:
      - a:
          call: http
          then: end
      - b:
          call: http
      - c:
          call: http
    """)
    assert msg == "unreachable tasks: b, c"


def test_backward_jump_without_a_cycle_leaves_a_task_stranded():
    msg = err("""\
    do:
      - a:
          call: http
          then: end
      - b:
          call: http
          then: a
    """)
    assert msg == "unreachable tasks: b"


def test_a_default_case_blocks_the_fallthrough_edge():
    # With a default present, execution can never fall through the switch,
    # so the task after it is only reachable if something targets it.
    msg = err("""\
    do:
      - route:
          switch:
            - when: ${ .x }
              then: done
            - then: done
      - skipped:
          call: http
      - done:
          set:
            ok: "yes"
    """)
    assert msg == "unreachable tasks: skipped"


def test_without_a_default_the_fallthrough_edge_counts():
    p = plan("""\
    do:
      - check:
          switch:
            - when: ${ .done }
              then: done
      - work:
          call: http
      - done:
          set:
            ok: "yes"
    """)
    assert p["tasks"][0]["next"] == 1  # reachable as fall-through


def test_unreachable_inside_a_body():
    msg = err("""\
    do:
      - sweep:
          for:
            each: x
            in: ${ .xs }
            do:
              - a:
                  call: http
                  then: end
              - b:
                  call: http
    """)
    assert msg == "unreachable tasks: b"


# ----------------------------------------------------------------- plumbing

def test_planerror_is_an_exception():
    assert issubclass(PlanError, Exception)
