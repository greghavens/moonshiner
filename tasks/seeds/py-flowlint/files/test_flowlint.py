"""Contract tests for the workflow-document linter — protected file.

lint(text) loads a YAML workflow document and returns a SORTED list of
"path: message" strings; [] means the document is clean. The paths and
messages asserted here are the CI contract for our authoring tools.
"""
import textwrap

from flowlint import lint


def errs(y):
    return lint(textwrap.dedent(y))


HEADER = 'document:\n  dsl: "1.0"\n  namespace: ops\n  name: sample\n'


def doc(body):
    """A valid header plus a dedented do-block (body starts at 'do:')."""
    return HEADER + textwrap.dedent(body)


# ------------------------------------------------------------- happy paths

def test_minimal_clean_document():
    assert errs(doc('do:\n  - stamp:\n      set:\n        seen: "yes"\n')) == []


def test_every_task_type_clean():
    y = """\
    document:
      dsl: "1.0"
      namespace: ops
      name: order-intake
      version: 2.1.0
      description: routes fresh orders
    timeout:
      after: PT10M
    input:
      from: .payload
    do:
      - fetch:
          call: http
          with:
            endpoint: /orders
            method: GET
          timeout:
            after: PT30S
          output:
            from: .body
      - stamp:
          set:
            received: ${ .fetch.received_at }
          if: ${ .fetch }
      - route:
          switch:
            - when: ${ .fetch.total > 100 }
              then: bulk
            - when: ${ .fetch.rush }
              then: notify
            - then: archive
      - bulk:
          run:
            workflow:
              name: bulk-intake
          then: archive
      - notify:
          emit:
            type: order.rush
            source: intake
            data:
              id: ${ .fetch.id }
          metadata:
            team: fulfilment
          then: archive
      - sweep:
          for:
            each: line
            in: ${ .fetch.lines }
            at: idx
            do:
              - check:
                  call: http
                  with:
                    endpoint: /stock
              - hold:
                  wait: PT5S
                  then: continue
          then: archive
      - pause:
          wait: PT1S
      - fail:
          raise:
            error:
              status: 502
              type: upstream_error
              title: upstream rejected the order
              detail: see the fetch task result
      - archive:
          run:
            shell:
              command: bin/archive
    """
    assert errs(y) == []


# ----------------------------------------------------------- root and top

def test_non_mapping_root():
    assert lint("- a\n- b\n") == ["workflow must be a mapping"]
    assert lint("just text\n") == ["workflow must be a mapping"]


def test_missing_required_sections():
    assert errs("input:\n  from: .x\n") == [
        "do: required section is missing",
        "document: required section is missing",
    ]


def test_unknown_top_level_section():
    y = doc('do:\n  - a:\n      wait: PT1S\ntriggers: []\n')
    assert errs(y) == ["triggers: unknown top-level section"]


def test_top_level_timeout_and_io_shapes():
    y = doc('do:\n  - a:\n      wait: PT1S\ntimeout: PT5M\noutput: .x\n')
    assert errs(y) == [
        'output: must be a mapping with only a "from" key',
        'timeout: must be a mapping with only an "after" key',
    ]
    y2 = doc('do:\n  - a:\n      wait: PT1S\ntimeout:\n  after: 30\n')
    assert errs(y2) == ["timeout.after: must be a non-empty string"]


# ----------------------------------------------------------- document header

def test_document_must_be_mapping():
    assert errs('document: v1\ndo:\n  - a:\n      wait: PT1S\n') == [
        "document: must be a mapping"
    ]


def test_dsl_version_must_be_the_string_one_dot_zero():
    base = 'document:\n  dsl: %s\n  namespace: ops\n  name: x\ndo:\n  - a:\n      wait: PT1S\n'
    # unquoted 1.0 arrives as a float — the classic authoring mistake
    assert errs(base % "1.0") == ['document.dsl: must be the string "1.0"']
    assert errs(base % '"2.0"') == ['document.dsl: must be the string "1.0"']
    y = 'document:\n  namespace: ops\n  name: x\ndo:\n  - a:\n      wait: PT1S\n'
    assert errs(y) == ["document.dsl: required field is missing"]


def test_namespace_and_name_rules():
    y = 'document:\n  dsl: "1.0"\n  namespace: ""\ndo:\n  - a:\n      wait: PT1S\n'
    assert errs(y) == [
        "document.name: required field is missing",
        "document.namespace: must be a non-empty string",
    ]


def test_document_unknown_field():
    y = HEADER.rstrip() + "\n  owner: kim\ndo:\n  - a:\n      wait: PT1S\n"
    assert errs(y) == ["document.owner: unknown field"]


# --------------------------------------------------------------- do entries

def test_do_shape():
    assert errs(HEADER + "do: {}\n") == ["do: must be a list"]
    assert errs(HEADER + "do: []\n") == ["do: must not be empty"]


def test_task_entry_must_be_single_key_mapping():
    y = doc('do:\n  - fetch\n  - a: {wait: PT1S}\n')
    assert errs(y) == ["do[0]: task entry must be a mapping with exactly one key"]
    y2 = doc('do:\n  - a:\n      wait: PT1S\n    b:\n      wait: PT2S\n')
    assert errs(y2) == ["do[0]: task entry must be a mapping with exactly one key"]


def test_task_name_must_be_string():
    y = doc('do:\n  - 5:\n      wait: PT1S\n')
    assert errs(y) == ["do[0]: task name must be a non-empty string"]


def test_duplicate_task_name_flagged_where_it_repeats():
    y = doc('do:\n  - fetch:\n      wait: PT1S\n  - fetch:\n      wait: PT2S\n')
    assert errs(y) == ["do[1].fetch: duplicate task name"]


def test_task_body_must_be_mapping():
    y = doc('do:\n  - fetch: hi\n')
    assert errs(y) == ["do[0].fetch: task must be a mapping"]


def test_exactly_one_task_type():
    y = doc('do:\n  - fetch:\n      then: end\n')
    assert errs(y) == [
        "do[0].fetch: task must declare exactly one task type (found none)"
    ]
    y2 = doc('do:\n  - fetch:\n      call: http\n      set:\n        a: b\n')
    assert errs(y2) == [
        "do[0].fetch: task must declare exactly one task type (found call, set)"
    ]


def test_unknown_task_field():
    y = doc('do:\n  - fetch:\n      call: http\n      retries: 3\n')
    assert errs(y) == ["do[0].fetch.retries: unknown task field"]


def test_with_is_only_for_call_tasks():
    y = doc('do:\n  - pause:\n      wait: PT1S\n      with:\n        a: b\n')
    assert errs(y) == ["do[0].pause.with: only valid on call tasks"]
    y2 = doc('do:\n  - fetch:\n      call: http\n      with: nope\n')
    assert errs(y2) == ["do[0].fetch.with: must be a mapping"]


def test_common_field_shapes():
    y = doc(
        'do:\n'
        '  - fetch:\n'
        '      call: http\n'
        '      if: ""\n'
        '      metadata: prod\n'
        '      input: .x\n'
    )
    assert errs(y) == [
        "do[0].fetch.if: must be a non-empty string",
        'do[0].fetch.input: must be a mapping with only a "from" key',
        "do[0].fetch.metadata: must be a mapping",
    ]


def test_then_targets():
    y = doc('do:\n  - fetch:\n      call: http\n      then: cleanup\n')
    assert errs(y) == ['do[0].fetch.then: unknown jump target "cleanup"']
    y2 = doc('do:\n  - fetch:\n      call: http\n      then: 5\n')
    assert errs(y2) == ["do[0].fetch.then: must be a string"]
    y3 = doc(
        'do:\n'
        '  - fetch:\n'
        '      call: http\n'
        '      then: end\n'
        '  - retry:\n'
        '      call: http\n'
        '      then: fetch\n'
    )
    assert errs(y3) == []


# --------------------------------------------------------------------- run

def test_run_exactly_one_of():
    y = doc('do:\n  - build:\n      run: {}\n')
    assert errs(y) == [
        "do[0].build.run: must declare exactly one of shell, script, workflow (found none)"
    ]
    y2 = doc(
        'do:\n'
        '  - build:\n'
        '      run:\n'
        '        shell:\n'
        '          command: make\n'
        '        script:\n'
        '          language: python\n'
    )
    assert errs(y2) == [
        "do[0].build.run: must declare exactly one of shell, script, workflow (found script, shell)"
    ]


def test_run_field_shapes():
    y = doc('do:\n  - build:\n      run:\n        shell: make\n')
    assert errs(y) == ["do[0].build.run.shell: must be a mapping"]
    y2 = doc(
        'do:\n'
        '  - build:\n'
        '      run:\n'
        '        shell:\n'
        '          command: make\n'
        '        env: prod\n'
    )
    assert errs(y2) == ["do[0].build.run.env: unknown field"]


# ------------------------------------------------------------------ switch

def test_switch_shape():
    y = doc('do:\n  - route:\n      switch: {}\n')
    assert errs(y) == ["do[0].route.switch: must be a non-empty list"]
    y2 = doc('do:\n  - route:\n      switch:\n        - hi\n')
    assert errs(y2) == ["do[0].route.switch[0]: case must be a mapping"]


def test_switch_case_rules():
    y = doc(
        'do:\n'
        '  - route:\n'
        '      switch:\n'
        '        - when: ${ .x }\n'
        '          then: missing\n'
        '        - when: ""\n'
        '        - label: fallback\n'
        '          then: end\n'
    )
    assert errs(y) == [
        'do[0].route.switch[0].then: unknown jump target "missing"',
        "do[0].route.switch[1].then: required field is missing",
        "do[0].route.switch[1].when: must be a non-empty expression",
        "do[0].route.switch[2].label: unknown case field",
    ]


def test_duplicate_default_case():
    y = doc(
        'do:\n'
        '  - route:\n'
        '      switch:\n'
        '        - when: ${ .x }\n'
        '          then: end\n'
        '        - then: end\n'
        '        - then: continue\n'
    )
    assert errs(y) == ["do[0].route.switch[2]: duplicate default case"]


# --------------------------------------------------------------------- for

def test_for_required_fields():
    y = doc(
        'do:\n'
        '  - sweep:\n'
        '      for:\n'
        '        in: ""\n'
        '        limit: 3\n'
    )
    assert errs(y) == [
        "do[0].sweep.for.do: required field is missing",
        "do[0].sweep.for.each: required field is missing",
        "do[0].sweep.for.in: must be a non-empty string",
        "do[0].sweep.for.limit: unknown field",
    ]


def test_for_body_is_validated_recursively():
    y = doc(
        'do:\n'
        '  - sweep:\n'
        '      for:\n'
        '        each: item\n'
        '        in: ${ .items }\n'
        '        do:\n'
        '          - step: {}\n'
        '          - step:\n'
        '              wait: PT1S\n'
    )
    assert errs(y) == [
        "do[0].sweep.for.do[0].step: task must declare exactly one task type (found none)",
        "do[0].sweep.for.do[1].step: duplicate task name",
    ]


def test_for_body_jump_targets_stay_inside_the_body():
    y = doc(
        'do:\n'
        '  - sweep:\n'
        '      for:\n'
        '        each: item\n'
        '        in: ${ .items }\n'
        '        do:\n'
        '          - check:\n'
        '              call: http\n'
        '              then: sweep\n'
        '          - hold:\n'
        '              wait: PT1S\n'
        '              then: check\n'
    )
    assert errs(y) == [
        'do[0].sweep.for.do[0].check.then: unknown jump target "sweep"'
    ]


# ------------------------------------------------------------- raise / emit

def test_raise_shape():
    y = doc('do:\n  - fail:\n      raise: oops\n')
    assert errs(y) == ['do[0].fail.raise: must be a mapping with only an "error" key']
    y2 = doc(
        'do:\n'
        '  - fail:\n'
        '      raise:\n'
        '        error:\n'
        '          status: "502"\n'
        '          type: upstream\n'
        '          code: E42\n'
    )
    assert errs(y2) == [
        "do[0].fail.raise.error.code: unknown field",
        "do[0].fail.raise.error.status: must be an integer",
        "do[0].fail.raise.error.title: required field is missing",
    ]


def test_raise_status_rejects_booleans():
    y = doc(
        'do:\n'
        '  - fail:\n'
        '      raise:\n'
        '        error:\n'
        '          status: true\n'
        '          type: upstream\n'
        '          title: nope\n'
    )
    assert errs(y) == ["do[0].fail.raise.error.status: must be an integer"]


def test_emit_shape():
    y = doc('do:\n  - ping:\n      emit:\n        source: intake\n        loud: true\n')
    assert errs(y) == [
        "do[0].ping.emit.loud: unknown field",
        "do[0].ping.emit.type: required field is missing",
    ]


# ------------------------------------------------------- scalar type values

def test_scalar_task_type_values():
    y = doc('do:\n  - a:\n      call: ""\n  - b:\n      wait: 30\n  - c:\n      set: {}\n')
    assert errs(y) == [
        "do[0].a.call: must be a non-empty string",
        "do[1].b.wait: must be a non-empty string",
        "do[2].c.set: must be a non-empty mapping",
    ]


# -------------------------------------------------------------- output order

def test_errors_come_back_sorted():
    y = (
        'document:\n'
        '  dsl: 1.0\n'
        '  namespace: ops\n'
        'do:\n'
        '  - fetch: {}\n'
        '  - fetch:\n'
        '      call: http\n'
        '      with: {}\n'
    )
    out = lint(y)
    assert out == sorted(out)
    assert out == [
        "do[0].fetch: task must declare exactly one task type (found none)",
        "do[1].fetch: duplicate task name",
        'document.dsl: must be the string "1.0"',
        "document.name: required field is missing",
    ]
