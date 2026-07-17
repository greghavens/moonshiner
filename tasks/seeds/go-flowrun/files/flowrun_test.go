// Acceptance tests for the sequential workflow executor — protected file.
//
// The engine under test (flowrun.go, not written yet) runs ONE workflow
// document at a time, strictly sequentially: a single cursor walks the
// do-list, `then:` moves it, and every effect (handler calls, events,
// clock reads) is deterministic. There is no DAG, no goroutine, no real
// time and no real I/O anywhere in here.
package flowrun

import (
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"
)

const hdr = `document:
  dsl: "1.0"
  namespace: ops
  name: unit-flow
`

// fakeClock is the injected logical clock. Handlers advance it to model
// work taking time; nothing in the engine may sleep or read wall time.
type fakeClock struct{ t time.Time }

func (c *fakeClock) Now() time.Time          { return c.t }
func (c *fakeClock) advance(d time.Duration) { c.t = c.t.Add(d) }
func newClock() *fakeClock                   { return &fakeClock{t: time.Unix(1_700_000_000, 0)} }

func mustLoad(t *testing.T, src string) *Workflow {
	t.Helper()
	wf, err := LoadWorkflow([]byte(src))
	if err != nil {
		t.Fatalf("LoadWorkflow: %v", err)
	}
	return wf
}

func mustRun(t *testing.T, src string, opts Options) *Result {
	t.Helper()
	res, err := mustLoad(t, src).Run(opts)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	return res
}

func loadErr(t *testing.T, src string) error {
	t.Helper()
	_, err := LoadWorkflow([]byte(src))
	if err == nil {
		t.Fatalf("LoadWorkflow accepted an invalid document")
	}
	return err
}

// ---------------------------------------------------------------- loading

func TestLoadRejectsWrongDSLVersion(t *testing.T) {
	err := loadErr(t, `document:
  dsl: "2.0"
  namespace: ops
  name: x
do:
  - a:
      set: { k: 1 }
`)
	if !strings.Contains(strings.ToLower(err.Error()), "dsl") {
		t.Fatalf("error should mention the dsl version, got: %v", err)
	}
}

func TestLoadRequiresNamespaceAndName(t *testing.T) {
	loadErr(t, `document:
  dsl: "1.0"
  name: x
do:
  - a:
      set: { k: 1 }
`)
	loadErr(t, `document:
  dsl: "1.0"
  namespace: ops
do:
  - a:
      set: { k: 1 }
`)
}

func TestLoadRequiresNonEmptyDo(t *testing.T) {
	loadErr(t, hdr+`
do: []
`)
}

func TestLoadRejectsDuplicateTaskNames(t *testing.T) {
	err := loadErr(t, hdr+`
do:
  - twice:
      set: { k: 1 }
  - twice:
      set: { k: 2 }
`)
	if !strings.Contains(err.Error(), "twice") {
		t.Fatalf("error should name the duplicated task, got: %v", err)
	}
}

func TestLoadRejectsTaskWithTwoTypeKeys(t *testing.T) {
	err := loadErr(t, hdr+`
do:
  - confused:
      set: { k: 1 }
      call: lookup
`)
	if !strings.Contains(err.Error(), "confused") {
		t.Fatalf("error should name the offending task, got: %v", err)
	}
}

func TestLoadRejectsTaskWithNoTypeKey(t *testing.T) {
	loadErr(t, hdr+`
do:
  - idle:
      if: "${ .x }"
`)
}

func TestLoadRejectsUnknownThenTarget(t *testing.T) {
	err := loadErr(t, hdr+`
do:
  - a:
      set: { k: 1 }
      then: nowhere
`)
	if !strings.Contains(err.Error(), "nowhere") {
		t.Fatalf("error should name the missing target, got: %v", err)
	}
}

func TestLoadRejectsUnknownSwitchCaseTarget(t *testing.T) {
	err := loadErr(t, hdr+`
do:
  - route:
      switch:
        - hot: { when: "${ .x }", then: elsewhere }
`)
	if !strings.Contains(err.Error(), "elsewhere") {
		t.Fatalf("error should name the missing case target, got: %v", err)
	}
}

func TestLoadRejectsBadDuration(t *testing.T) {
	loadErr(t, hdr+`
timeout: { after: "5 minutes" }
do:
  - a:
      set: { k: 1 }
`)
	loadErr(t, hdr+`
do:
  - a:
      set: { k: 1 }
      timeout: { after: PT }
`)
}

// ------------------------------------------------- sequence, context, calls

func TestResultsAccumulateUnderTaskNames(t *testing.T) {
	var got map[string]any
	res := mustRun(t, hdr+`
do:
  - base:
      set: { region: us-east, retries: 2 }
  - fetch:
      call: lookup
      with: { region: "${ .base.region }" }
`, Options{Handlers: map[string]Handler{
		"lookup": func(args map[string]any) (any, error) {
			got = args
			return map[string]any{"rows": 42}, nil
		},
	}})
	if res.Status != "completed" {
		t.Fatalf("status = %q, want completed", res.Status)
	}
	if !reflect.DeepEqual(got, map[string]any{"region": "us-east"}) {
		t.Fatalf("handler args = %#v", got)
	}
	want := map[string]any{
		"base":  map[string]any{"region": "us-east", "retries": 2},
		"fetch": map[string]any{"rows": 42},
	}
	if !reflect.DeepEqual(res.Context, want) {
		t.Fatalf("context = %#v, want %#v", res.Context, want)
	}
}

func TestSingleExpressionKeepsRawType(t *testing.T) {
	var got map[string]any
	mustRun(t, hdr+`
do:
  - pick:
      set: { qty: 7, ok: true, note: plain }
  - ship:
      call: send
      with:
        qty: "${ .pick.qty }"
        ok: "${ .pick.ok }"
        note: "${ .pick.note }"
        label: no-brackets-here
`, Options{Handlers: map[string]Handler{
		"send": func(args map[string]any) (any, error) {
			got = args
			return nil, nil
		},
	}})
	want := map[string]any{"qty": 7, "ok": true, "note": "plain", "label": "no-brackets-here"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("args = %#v, want %#v (expressions must yield raw typed values)", got, want)
	}
}

func TestNestedWithValuesAreEvaluated(t *testing.T) {
	var got map[string]any
	mustRun(t, hdr+`
do:
  - base:
      set: { sku: A-100 }
  - post:
      call: send
      with:
        body:
          item: "${ .base.sku }"
          tags: ["${ .base.sku }", fixed]
`, Options{Handlers: map[string]Handler{
		"send": func(args map[string]any) (any, error) {
			got = args
			return nil, nil
		},
	}})
	want := map[string]any{"body": map[string]any{
		"item": "A-100",
		"tags": []any{"A-100", "fixed"},
	}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("args = %#v, want %#v", got, want)
	}
}

func TestMissingPathEvaluatesToNil(t *testing.T) {
	var got map[string]any
	mustRun(t, hdr+`
do:
  - probe:
      call: send
      with: { v: "${ .nothing.here }" }
`, Options{Handlers: map[string]Handler{
		"send": func(args map[string]any) (any, error) {
			got = args
			return nil, nil
		},
	}})
	if v, present := got["v"]; !present || v != nil {
		t.Fatalf("args = %#v, want v present and nil", got)
	}
}

func TestUnknownHandlerIsTaskError(t *testing.T) {
	_, err := mustLoad(t, hdr+`
do:
  - fetch:
      call: not-registered
`).Run(Options{})
	var te *TaskError
	if !errors.As(err, &te) {
		t.Fatalf("want *TaskError, got %v", err)
	}
	if te.Task != "fetch" {
		t.Fatalf("TaskError.Task = %q, want fetch", te.Task)
	}
}

func TestHandlerFailureWrapsCauseInTaskError(t *testing.T) {
	boom := errors.New("upstream said no")
	_, err := mustLoad(t, hdr+`
do:
  - fetch:
      call: flaky
`).Run(Options{Handlers: map[string]Handler{
		"flaky": func(map[string]any) (any, error) { return nil, boom },
	}})
	var te *TaskError
	if !errors.As(err, &te) {
		t.Fatalf("want *TaskError, got %v", err)
	}
	if te.Task != "fetch" || !errors.Is(err, boom) {
		t.Fatalf("TaskError should carry the task name and wrap the cause: %v", err)
	}
}

func TestMalformedExpressionIsAnError(t *testing.T) {
	_, err := mustLoad(t, hdr+`
do:
  - odd:
      set: { v: "${ .a ~~ 3 }" }
`).Run(Options{})
	if err == nil {
		t.Fatalf("malformed expression must fail the run")
	}
}

// ------------------------------------------------------------------ if:

func TestIfFalseSkipsTask(t *testing.T) {
	res := mustRun(t, hdr+`
do:
  - flags:
      set: { dryrun: "" }
  - apply:
      if: "${ .flags.dryrun }"
      set: { applied: true }
  - note:
      set: { done: true }
`, Options{})
	if _, ok := res.Context["apply"]; ok {
		t.Fatalf("skipped task must not store a result: %#v", res.Context)
	}
	if !reflect.DeepEqual(res.Context["note"], map[string]any{"done": true}) {
		t.Fatalf("tasks after a skipped one must still run: %#v", res.Context)
	}
}

func TestIfLiteralFalseAndTruthyValueRun(t *testing.T) {
	res := mustRun(t, hdr+`
do:
  - a:
      if: false
      set: { ran: true }
  - b:
      if: "${ .missing.path }"
      set: { ran: true }
  - c:
      if: "${ .a }"
      set: { ran: true }
  - d:
      set: { n: 3 }
  - e:
      if: "${ .d.n }"
      set: { ran: true }
`, Options{})
	for _, skipped := range []string{"a", "b", "c"} {
		if _, ok := res.Context[skipped]; ok {
			t.Fatalf("task %q should have been skipped: %#v", skipped, res.Context)
		}
	}
	if !reflect.DeepEqual(res.Context["e"], map[string]any{"ran": true}) {
		t.Fatalf("task e should have run (non-zero number is truthy): %#v", res.Context)
	}
}

func TestSkippedTaskThenIsNotFollowed(t *testing.T) {
	res := mustRun(t, hdr+`
do:
  - detour:
      if: false
      set: { ran: true }
      then: end
  - after:
      set: { ran: true }
`, Options{})
	if !reflect.DeepEqual(res.Context["after"], map[string]any{"ran": true}) {
		t.Fatalf("a skipped task's then must be ignored: %#v", res.Context)
	}
}

// ---------------------------------------------------------------- then:

func TestThenEndStopsWorkflow(t *testing.T) {
	res := mustRun(t, hdr+`
do:
  - first:
      set: { ran: true }
      then: end
  - second:
      set: { ran: true }
`, Options{})
	if res.Status != "completed" {
		t.Fatalf("status = %q, want completed", res.Status)
	}
	if _, ok := res.Context["second"]; ok {
		t.Fatalf("then: end must stop the run: %#v", res.Context)
	}
}

func TestThenForwardJumpSkipsIntermediateTasks(t *testing.T) {
	res := mustRun(t, hdr+`
do:
  - start:
      set: { ran: true }
      then: finish
  - middle:
      set: { ran: true }
  - finish:
      set: { ran: true }
`, Options{})
	if _, ok := res.Context["middle"]; ok {
		t.Fatalf("forward jump must skip intermediate tasks: %#v", res.Context)
	}
	if _, ok := res.Context["finish"]; !ok {
		t.Fatalf("jump target did not run: %#v", res.Context)
	}
}

func TestBackwardJumpLoopsUntilConditionFlips(t *testing.T) {
	calls := 0
	res := mustRun(t, hdr+`
do:
  - bump:
      call: inc
      with: { n: "${ .bump.n }" }
  - more:
      switch:
        - again: { when: "${ .bump.n < 3 }", then: bump }
        - done: { then: continue }
  - wrap:
      set: { total: "${ .bump.n }" }
`, Options{Handlers: map[string]Handler{
		"inc": func(args map[string]any) (any, error) {
			calls++
			n := 0
			if v, ok := args["n"].(int); ok {
				n = v
			}
			return map[string]any{"n": n + 1}, nil
		},
	}})
	if calls != 3 {
		t.Fatalf("handler ran %d times, want 3", calls)
	}
	if !reflect.DeepEqual(res.Context["wrap"], map[string]any{"total": 3}) {
		t.Fatalf("context after loop = %#v", res.Context)
	}
}

func TestRunawayLoopHitsVisitBudget(t *testing.T) {
	_, err := mustLoad(t, hdr+`
do:
  - spin:
      set: { again: true }
      then: spin
`).Run(Options{MaxVisits: 3})
	var le *LoopError
	if !errors.As(err, &le) {
		t.Fatalf("want *LoopError, got %v", err)
	}
	if le.Task != "spin" || le.Visits != 4 {
		t.Fatalf("LoopError = %+v, want Task=spin Visits=4", le)
	}
}

// --------------------------------------------------------------- switch:

func TestSwitchFirstTruthyCaseWins(t *testing.T) {
	res := mustRun(t, hdr+`
do:
  - triage:
      set: { score: 9 }
  - route:
      switch:
        - high: { when: "${ .triage.score >= 8 }", then: page }
        - alsohigh: { when: "${ .triage.score >= 4 }", then: ticket }
        - low: { then: log }
  - page:
      set: { via: pager }
      then: end
  - ticket:
      set: { via: ticket }
      then: end
  - log:
      set: { via: log }
`, Options{})
	if _, ok := res.Context["route"]; ok {
		t.Fatalf("a switch task must not store a result: %#v", res.Context)
	}
	if !reflect.DeepEqual(res.Context["page"], map[string]any{"via": "pager"}) {
		t.Fatalf("first truthy case must win: %#v", res.Context)
	}
	for _, name := range []string{"ticket", "log"} {
		if _, ok := res.Context[name]; ok {
			t.Fatalf("only the chosen branch may run, %q also ran: %#v", name, res.Context)
		}
	}
}

func TestSwitchFallsBackToDefaultCase(t *testing.T) {
	res := mustRun(t, hdr+`
do:
  - triage:
      set: { score: 1 }
  - route:
      switch:
        - high: { when: "${ .triage.score >= 8 }", then: page }
        - low: { then: log }
  - page:
      set: { via: pager }
      then: end
  - log:
      set: { via: log }
`, Options{})
	if !reflect.DeepEqual(res.Context["log"], map[string]any{"via": "log"}) {
		t.Fatalf("default case (no when) must be taken: %#v", res.Context)
	}
}

func TestSwitchWithNoMatchAndNoDefaultContinues(t *testing.T) {
	res := mustRun(t, hdr+`
do:
  - triage:
      set: { score: 1 }
  - route:
      switch:
        - high: { when: "${ .triage.score >= 8 }", then: end }
  - after:
      set: { ran: true }
`, Options{})
	if !reflect.DeepEqual(res.Context["after"], map[string]any{"ran": true}) {
		t.Fatalf("no matching case and no default must fall through: %#v", res.Context)
	}
}

func TestSwitchStringEquality(t *testing.T) {
	res := mustRun(t, hdr+`
do:
  - env:
      set: { name: staging }
  - route:
      switch:
        - prod: { when: "${ .env.name == \"prod\" }", then: strict }
        - other: { then: relaxed }
  - strict:
      set: { mode: strict }
      then: end
  - relaxed:
      set: { mode: relaxed }
`, Options{})
	if !reflect.DeepEqual(res.Context["relaxed"], map[string]any{"mode": "relaxed"}) {
		t.Fatalf("string equality routed wrong: %#v", res.Context)
	}
}

// ---------------------------------------------------------------- raise:

func TestRaiseFailsWorkflowWithStructuredError(t *testing.T) {
	res := mustRun(t, hdr+`
do:
  - check:
      set: { balance: -3 }
  - stop:
      raise:
        error:
          status: 402
          type: payment.declined
          title: Payment required
          detail: balance below zero
  - never:
      set: { ran: true }
`, Options{})
	if res.Status != "failed" {
		t.Fatalf("status = %q, want failed", res.Status)
	}
	want := &ErrorInfo{Status: 402, Type: "payment.declined", Title: "Payment required", Detail: "balance below zero"}
	if !reflect.DeepEqual(res.Error, want) {
		t.Fatalf("error = %#v, want %#v", res.Error, want)
	}
	if _, ok := res.Context["never"]; ok {
		t.Fatalf("tasks after raise must not run: %#v", res.Context)
	}
	if !reflect.DeepEqual(res.Context["check"], map[string]any{"balance": -3}) {
		t.Fatalf("context from before the raise must be kept: %#v", res.Context)
	}
}

func TestLoadRequiresRaiseErrorType(t *testing.T) {
	loadErr(t, hdr+`
do:
  - stop:
      raise:
        error:
          status: 500
`)
}

// ----------------------------------------------------------------- emit:

func TestEmitAppendsOrderedEvents(t *testing.T) {
	res := mustRun(t, hdr+`
do:
  - tally:
      set: { rows: 42 }
  - first:
      emit:
        event:
          type: sync.started
          source: nightly
  - second:
      emit:
        event:
          type: sync.done
          data:
            count: "${ .tally.rows }"
            tag: full
`, Options{})
	if len(res.Events) != 2 {
		t.Fatalf("events = %#v, want exactly 2", res.Events)
	}
	if res.Events[0].Type != "sync.started" || res.Events[0].Source != "nightly" {
		t.Fatalf("first event = %#v", res.Events[0])
	}
	if res.Events[1].Type != "sync.done" || res.Events[1].Source != "" {
		t.Fatalf("second event = %#v", res.Events[1])
	}
	wantData := map[string]any{"count": 42, "tag": "full"}
	if !reflect.DeepEqual(res.Events[1].Data, wantData) {
		t.Fatalf("event data = %#v, want %#v", res.Events[1].Data, wantData)
	}
	if _, ok := res.Context["first"]; ok {
		t.Fatalf("emit tasks must not store a result: %#v", res.Context)
	}
}

func TestEventsEmittedBeforeFailureAreKept(t *testing.T) {
	res := mustRun(t, hdr+`
do:
  - note:
      emit:
        event:
          type: run.attempted
  - stop:
      raise:
        error:
          status: 500
          type: internal
`, Options{})
	if res.Status != "failed" || len(res.Events) != 1 || res.Events[0].Type != "run.attempted" {
		t.Fatalf("events before a raise must survive: %#v / %#v", res.Status, res.Events)
	}
}

// -------------------------------------------------------------- timeouts

// timed returns handlers that advance the clock by the given amount per call.
func timed(c *fakeClock, names map[string]time.Duration) map[string]Handler {
	h := map[string]Handler{}
	for name, d := range names {
		d := d
		h[name] = func(map[string]any) (any, error) {
			c.advance(d)
			return map[string]any{"ok": true}, nil
		}
	}
	return h
}

func TestWorkflowBudgetExceededByTask(t *testing.T) {
	c := newClock()
	_, err := mustLoad(t, hdr+`
timeout: { after: PT1M }
do:
  - slow:
      call: work
`).Run(Options{Clock: c, Handlers: timed(c, map[string]time.Duration{"work": 90 * time.Second})})
	var te *TimeoutError
	if !errors.As(err, &te) {
		t.Fatalf("want *TimeoutError, got %v", err)
	}
	if te.Task != "slow" || te.Limit != time.Minute {
		t.Fatalf("TimeoutError = %+v, want Task=slow Limit=1m", te)
	}
}

func TestTaskTimeoutClampedByRemainingWorkflowBudget(t *testing.T) {
	c := newClock()
	_, err := mustLoad(t, hdr+`
timeout: { after: PT1M }
do:
  - warm:
      call: fast
  - cold:
      call: slow
      timeout: { after: PT45S }
`).Run(Options{Clock: c, Handlers: timed(c, map[string]time.Duration{
		"fast": 30 * time.Second,
		"slow": 31 * time.Second,
	})})
	var te *TimeoutError
	if !errors.As(err, &te) {
		t.Fatalf("want *TimeoutError, got %v", err)
	}
	// 30s of the 60s budget is gone, so the task's own PT45S must be
	// clamped down to the 30s that remain.
	if te.Task != "cold" || te.Limit != 30*time.Second {
		t.Fatalf("TimeoutError = %+v, want Task=cold Limit=30s", te)
	}
}

func TestTaskTimeoutSmallerThanRemainingBudgetApplies(t *testing.T) {
	c := newClock()
	_, err := mustLoad(t, hdr+`
timeout: { after: PT5M }
do:
  - probe:
      call: slow
      timeout: { after: PT10S }
`).Run(Options{Clock: c, Handlers: timed(c, map[string]time.Duration{"slow": 11 * time.Second})})
	var te *TimeoutError
	if !errors.As(err, &te) {
		t.Fatalf("want *TimeoutError, got %v", err)
	}
	if te.Task != "probe" || te.Limit != 10*time.Second {
		t.Fatalf("TimeoutError = %+v, want Task=probe Limit=10s", te)
	}
}

func TestExactBudgetPassesThenNextTaskCannotStart(t *testing.T) {
	c := newClock()
	_, err := mustLoad(t, hdr+`
timeout: { after: PT1M }
do:
  - fill:
      call: work
  - next:
      set: { ran: true }
`).Run(Options{Clock: c, Handlers: timed(c, map[string]time.Duration{"work": time.Minute})})
	var te *TimeoutError
	if !errors.As(err, &te) {
		t.Fatalf("want *TimeoutError for the task after the budget ran dry, got %v", err)
	}
	// Using exactly the budget is fine; the FOLLOWING task must refuse to
	// start with zero budget left.
	if te.Task != "next" || te.Limit != 0 {
		t.Fatalf("TimeoutError = %+v, want Task=next Limit=0", te)
	}
}

func TestTaskTimeoutWithoutWorkflowBudget(t *testing.T) {
	c := newClock()
	_, err := mustLoad(t, hdr+`
do:
  - probe:
      call: slow
      timeout: { after: PT2S }
`).Run(Options{Clock: c, Handlers: timed(c, map[string]time.Duration{"slow": 3 * time.Second})})
	var te *TimeoutError
	if !errors.As(err, &te) {
		t.Fatalf("want *TimeoutError, got %v", err)
	}
	if te.Task != "probe" || te.Limit != 2*time.Second {
		t.Fatalf("TimeoutError = %+v, want Task=probe Limit=2s", te)
	}
}

func TestWithinBudgetsCompletes(t *testing.T) {
	c := newClock()
	res, err := mustLoad(t, hdr+`
timeout: { after: PT2M }
do:
  - a:
      call: work
      timeout: { after: PT1M }
  - b:
      call: work
`).Run(Options{Clock: c, Handlers: timed(c, map[string]time.Duration{"work": 20 * time.Second})})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if res.Status != "completed" {
		t.Fatalf("status = %q, want completed", res.Status)
	}
}

func TestNoTimeoutsRunWithoutClock(t *testing.T) {
	res := mustRun(t, hdr+`
do:
  - a:
      set: { ok: true }
`, Options{})
	if res.Status != "completed" {
		t.Fatalf("a workflow with no timeouts must run without a clock, got %q", res.Status)
	}
}

func TestDurationParsingComposite(t *testing.T) {
	c := newClock()
	// PT1M30S = 90s: a 89s task passes, then a second 89s task blows the
	// remaining 1s — proving composite durations parse to the right value.
	_, err := mustLoad(t, hdr+`
timeout: { after: PT1M30S }
do:
  - one:
      call: work
  - two:
      call: work
`).Run(Options{Clock: c, Handlers: timed(c, map[string]time.Duration{"work": 89 * time.Second})})
	var te *TimeoutError
	if !errors.As(err, &te) {
		t.Fatalf("want *TimeoutError, got %v", err)
	}
	if te.Task != "two" || te.Limit != time.Second {
		t.Fatalf("TimeoutError = %+v, want Task=two Limit=1s", te)
	}
}
