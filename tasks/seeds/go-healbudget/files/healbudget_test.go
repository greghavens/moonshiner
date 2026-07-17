// Acceptance tests for the self-healing policy engine — protected file.
//
// The module under test (healbudget.go, not written yet) executes linear
// set/call workflows and owns the retry POLICY around handler failures:
// per-task self-healing metadata, the shared run-wide healing budget, the
// approval gate, and the deterministic attempt timeline with exponential
// backoff on an injected clock. There is no expression engine here — the
// data-flow layer resolves values before documents reach this module.
package healbudget

import (
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"
)

const hdr = `document:
  dsl: "1.0"
  namespace: ops
  name: heal-flow
`

const sharedCfg = `self_healing:
  budget: 3
  base_delay_seconds: 2
`

// healingTask renders a call task with complete healing metadata.
func healingTask(name, handler string, budget int, approval bool) string {
	return fmt.Sprintf(`  - %s:
      call: %s
      metadata:
        description: pull nightly rows
        expects: upstream configured
        produces: raw rows
        self_healing:
          enabled: true
          retry_budget: %d
          approval_required: %v
`, name, handler, budget, approval)
}

type fakeClock struct {
	t      time.Duration
	sleeps []time.Duration
}

func (c *fakeClock) Now() time.Duration { return c.t }

func (c *fakeClock) Sleep(d time.Duration) {
	c.t += d
	c.sleeps = append(c.sleeps, d)
}

// failFirst returns a handler that fails its first n calls with "boom #k"
// and then succeeds with result.
func failFirst(n int, result any) Handler {
	calls := 0
	return func(args map[string]any) (any, error) {
		calls++
		if calls <= n {
			return nil, fmt.Errorf("boom %d", calls)
		}
		return result, nil
	}
}

func ok(result any) Handler {
	return func(args map[string]any) (any, error) { return result, nil }
}

func mustLoadError(t *testing.T, src string, want string) {
	t.Helper()
	_, err := Run(src, Options{})
	var le *LoadError
	if !errors.As(err, &le) {
		t.Fatalf("want LoadError, got %v", err)
	}
	if want != "" && !strings.Contains(err.Error(), want) {
		t.Fatalf("LoadError %q does not mention %q", err.Error(), want)
	}
}

func mustComplete(t *testing.T, src string, opts Options) *Result {
	t.Helper()
	res, err := Run(src, opts)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if res.Status != "completed" {
		t.Fatalf("status = %q, error = %+v", res.Status, res.Error)
	}
	return res
}

func mustFail(t *testing.T, src string, opts Options, kind, task string) *Result {
	t.Helper()
	res, err := Run(src, opts)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if res.Status != "failed" {
		t.Fatalf("status = %q, want failed", res.Status)
	}
	if res.Error == nil {
		t.Fatal("failed run has nil Error")
	}
	if res.Error.Kind != kind {
		t.Fatalf("Error.Kind = %q, want %q (detail %q)",
			res.Error.Kind, kind, res.Error.Detail)
	}
	if res.Error.Task != task {
		t.Fatalf("Error.Task = %q, want %q", res.Error.Task, task)
	}
	return res
}

func checkAttempt(t *testing.T, got Attempt, task string, number int,
	at time.Duration, okAttempt bool) {
	t.Helper()
	if got.Task != task || got.Number != number || got.At != at ||
		got.OK != okAttempt {
		t.Fatalf("attempt = %+v, want {Task:%s Number:%d At:%v OK:%v}",
			got, task, number, at, okAttempt)
	}
}

// ------------------------------------------------------------------ loading

func TestLoadRejectsWrongDSLVersion(t *testing.T) {
	mustLoadError(t, `document:
  dsl: "2.0"
  namespace: ops
  name: x
do:
  - a:
      set: {k: 1}
`, "dsl")
}

func TestLoadRequiresHeaderFields(t *testing.T) {
	for _, missing := range []string{"namespace", "name"} {
		src := "document:\n  dsl: \"1.0\"\n"
		if missing != "namespace" {
			src += "  namespace: ops\n"
		}
		if missing != "name" {
			src += "  name: x\n"
		}
		src += "do:\n  - a:\n      set: {k: 1}\n"
		mustLoadError(t, src, missing)
	}
}

func TestLoadRejectsDuplicateTaskNames(t *testing.T) {
	mustLoadError(t, hdr+`do:
  - twice:
      set: {k: 1}
  - twice:
      set: {k: 2}
`, "twice")
}

func TestLoadRequiresExactlyOneTypeKey(t *testing.T) {
	mustLoadError(t, hdr+`do:
  - both:
      set: {k: 1}
      call: probe
`, "")
	mustLoadError(t, hdr+`do:
  - neither:
      metadata:
        description: no work here
`, "")
}

func TestLoadRejectsHealingOnSetTasks(t *testing.T) {
	mustLoadError(t, hdr+sharedCfg+`do:
  - fill:
      set: {k: 1}
      metadata:
        description: fill a constant
        expects: nothing
        produces: k
        self_healing:
          enabled: true
          retry_budget: 1
`, "fill")
}

func TestLoadRequiresPositiveRetryBudgetWhenEnabled(t *testing.T) {
	// missing retry_budget
	mustLoadError(t, hdr+sharedCfg+`do:
  - fetch:
      call: probe
      metadata:
        description: pull rows
        expects: upstream configured
        produces: rows
        self_healing:
          enabled: true
`, "retry_budget")
	// zero retry_budget
	mustLoadError(t, hdr+sharedCfg+`do:
  - fetch:
      call: probe
      metadata:
        description: pull rows
        expects: upstream configured
        produces: rows
        self_healing:
          enabled: true
          retry_budget: 0
`, "retry_budget")
}

func TestLoadRejectsUnknownPolicyKeys(t *testing.T) {
	// under the per-task block
	mustLoadError(t, hdr+sharedCfg+`do:
  - fetch:
      call: probe
      metadata:
        description: pull rows
        expects: upstream configured
        produces: rows
        self_healing:
          enabled: true
          retry_budget: 1
          jitter: wide
`, "jitter")
	// under the workflow-level block
	mustLoadError(t, hdr+`self_healing:
  budget: 3
  mode: aggressive
do:
  - a:
      set: {k: 1}
`, "mode")
}

func TestLoadRequiresCompleteMetadataWhenEnabled(t *testing.T) {
	fields := map[string]string{
		"description": "        expects: upstream configured\n        produces: rows\n",
		"expects":     "        description: pull rows\n        produces: rows\n",
		"produces":    "        description: pull rows\n        expects: upstream configured\n",
	}
	for missing, present := range fields {
		src := hdr + sharedCfg + "do:\n  - fetch:\n      call: probe\n      metadata:\n" +
			present +
			"        self_healing:\n          enabled: true\n          retry_budget: 1\n"
		mustLoadError(t, src, missing)
	}
}

func TestLoadRequiresSharedBudgetDeclaration(t *testing.T) {
	// a task enables healing but the workflow declares no shared budget
	mustLoadError(t, hdr+"do:\n"+healingTask("fetch", "probe", 1, false),
		"budget")
}

func TestLoadRejectsNonPositiveSharedBudget(t *testing.T) {
	mustLoadError(t, hdr+`self_healing:
  budget: 0
do:
  - a:
      set: {k: 1}
`, "budget")
}

func TestDisabledHealingNeedsNoExtraMetadata(t *testing.T) {
	res := mustComplete(t, hdr+`do:
  - fetch:
      call: probe
  - note:
      set: {k: 1}
      metadata:
        description: only a description, healing off
`, Options{Handlers: map[string]Handler{"probe": ok("rows")}})
	if res.Context["fetch"] != "rows" {
		t.Fatalf("context = %+v", res.Context)
	}
}

// ------------------------------------------------------------------ running

func TestUnknownHandlersAreCheckedUpFront(t *testing.T) {
	called := false
	handlers := map[string]Handler{
		"probe": func(args map[string]any) (any, error) {
			called = true
			return nil, nil
		},
	}
	_, err := Run(hdr+`do:
  - first:
      call: probe
  - second:
      call: nowhere
`, Options{Handlers: handlers})
	if !errors.Is(err, ErrUnknownHandler) {
		t.Fatalf("want ErrUnknownHandler, got %v", err)
	}
	if !strings.Contains(err.Error(), "nowhere") {
		t.Fatalf("error %q does not name the handler", err.Error())
	}
	if called {
		t.Fatal("first handler ran before the registry was validated")
	}
}

func TestCleanRunRecordsSingleAttempts(t *testing.T) {
	clock := &fakeClock{}
	res := mustComplete(t, hdr+`do:
  - fetch:
      call: probe
  - note:
      set: {kind: nightly, count: 2}
`, Options{
		Handlers: map[string]Handler{"probe": ok("rows")},
		Clock:    clock,
	})
	if res.Context["fetch"] != "rows" {
		t.Fatalf("context = %+v", res.Context)
	}
	note, _ := res.Context["note"].(map[string]any)
	if note["kind"] != "nightly" || note["count"] != 2 {
		t.Fatalf("note = %+v", note)
	}
	if len(res.Timeline) != 1 {
		t.Fatalf("timeline = %+v", res.Timeline)
	}
	checkAttempt(t, res.Timeline[0], "fetch", 1, 0, true)
	if res.Healed != 0 {
		t.Fatalf("Healed = %d, want 0", res.Healed)
	}
	if len(clock.sleeps) != 0 {
		t.Fatalf("sleeps = %v, want none", clock.sleeps)
	}
}

func TestArgsAndSetValuesPassLiterally(t *testing.T) {
	var seen map[string]any
	handlers := map[string]Handler{
		"probe": func(args map[string]any) (any, error) {
			seen = args
			return nil, nil
		},
	}
	res := mustComplete(t, hdr+`do:
  - fetch:
      call: probe
      with:
        region: us-east
        limit: 5
        template: "${ .not.evaluated }"
`, Options{Handlers: handlers})
	// no expression engine in this module: values arrive verbatim
	if seen["region"] != "us-east" || seen["limit"] != 5 ||
		seen["template"] != "${ .not.evaluated }" {
		t.Fatalf("args = %+v", seen)
	}
	if res.Context["fetch"] != nil {
		t.Fatalf("fetch result = %v, want nil", res.Context["fetch"])
	}
}

func TestFailureWithoutHealingFailsImmediately(t *testing.T) {
	clock := &fakeClock{}
	res := mustFail(t, hdr+`do:
  - fetch:
      call: probe
  - after:
      set: {k: 1}
`, Options{
		Handlers: map[string]Handler{"probe": failFirst(99, nil)},
		Clock:    clock,
	}, "handler", "fetch")
	if !strings.Contains(res.Error.Detail, "boom 1") {
		t.Fatalf("Detail = %q", res.Error.Detail)
	}
	if len(res.Timeline) != 1 {
		t.Fatalf("timeline = %+v", res.Timeline)
	}
	checkAttempt(t, res.Timeline[0], "fetch", 1, 0, false)
	if len(clock.sleeps) != 0 {
		t.Fatalf("sleeps = %v, want none", clock.sleeps)
	}
	if _, ran := res.Context["after"]; ran {
		t.Fatal("task after the failure still ran")
	}
}

func TestHealingRetriesAfterBackoff(t *testing.T) {
	clock := &fakeClock{}
	src := hdr + sharedCfg + "do:\n" +
		healingTask("fetch", "probe", 2, false) +
		"  - after:\n      set: {k: 1}\n"
	res := mustComplete(t, src, Options{
		Handlers: map[string]Handler{"probe": failFirst(1, "rows")},
		Clock:    clock,
	})
	if len(clock.sleeps) != 1 || clock.sleeps[0] != 2*time.Second {
		t.Fatalf("sleeps = %v, want [2s]", clock.sleeps)
	}
	if len(res.Timeline) != 2 {
		t.Fatalf("timeline = %+v", res.Timeline)
	}
	checkAttempt(t, res.Timeline[0], "fetch", 1, 0, false)
	checkAttempt(t, res.Timeline[1], "fetch", 2, 2*time.Second, true)
	if !strings.Contains(res.Timeline[0].Detail, "boom 1") {
		t.Fatalf("attempt detail = %q", res.Timeline[0].Detail)
	}
	if res.Timeline[1].Detail != "" {
		t.Fatalf("ok attempt has detail %q", res.Timeline[1].Detail)
	}
	if res.Healed != 1 {
		t.Fatalf("Healed = %d, want 1", res.Healed)
	}
	if res.Context["fetch"] != "rows" {
		t.Fatalf("context = %+v", res.Context)
	}
	if _, ran := res.Context["after"]; !ran {
		t.Fatal("task after the healed one did not run")
	}
}

func TestBackoffDoublesPerRetry(t *testing.T) {
	clock := &fakeClock{}
	src := hdr + sharedCfg + "do:\n" + healingTask("fetch", "probe", 3, false)
	res := mustComplete(t, src, Options{
		Handlers: map[string]Handler{"probe": failFirst(2, "rows")},
		Clock:    clock,
	})
	want := []time.Duration{2 * time.Second, 4 * time.Second}
	if len(clock.sleeps) != 2 || clock.sleeps[0] != want[0] ||
		clock.sleeps[1] != want[1] {
		t.Fatalf("sleeps = %v, want %v", clock.sleeps, want)
	}
	if len(res.Timeline) != 3 {
		t.Fatalf("timeline = %+v", res.Timeline)
	}
	checkAttempt(t, res.Timeline[0], "fetch", 1, 0, false)
	checkAttempt(t, res.Timeline[1], "fetch", 2, 2*time.Second, false)
	checkAttempt(t, res.Timeline[2], "fetch", 3, 6*time.Second, true)
	if res.Healed != 2 {
		t.Fatalf("Healed = %d, want 2", res.Healed)
	}
}

func TestBaseDelayIsHonored(t *testing.T) {
	clock := &fakeClock{}
	src := hdr + `self_healing:
  budget: 5
  base_delay_seconds: 3
do:
` + healingTask("fetch", "probe", 3, false)
	mustComplete(t, src, Options{
		Handlers: map[string]Handler{"probe": failFirst(2, "rows")},
		Clock:    clock,
	})
	if len(clock.sleeps) != 2 || clock.sleeps[0] != 3*time.Second ||
		clock.sleeps[1] != 6*time.Second {
		t.Fatalf("sleeps = %v, want [3s 6s]", clock.sleeps)
	}
}

func TestDefaultBaseDelayIsOneSecond(t *testing.T) {
	clock := &fakeClock{}
	src := hdr + `self_healing:
  budget: 5
do:
` + healingTask("fetch", "probe", 1, false)
	mustComplete(t, src, Options{
		Handlers: map[string]Handler{"probe": failFirst(1, "rows")},
		Clock:    clock,
	})
	if len(clock.sleeps) != 1 || clock.sleeps[0] != time.Second {
		t.Fatalf("sleeps = %v, want [1s]", clock.sleeps)
	}
}

func TestTaskBudgetExhaustionFailsTheRun(t *testing.T) {
	src := hdr + `self_healing:
  budget: 10
  base_delay_seconds: 2
do:
` + healingTask("fetch", "probe", 2, false)
	res := mustFail(t, src, Options{
		Handlers: map[string]Handler{"probe": failFirst(99, nil)},
		Clock:    &fakeClock{},
	}, "task-budget", "fetch")
	// 1 first try + 2 healing retries, then the task budget is spent
	if len(res.Timeline) != 3 {
		t.Fatalf("timeline = %+v", res.Timeline)
	}
	if res.Healed != 2 {
		t.Fatalf("Healed = %d, want 2", res.Healed)
	}
}

func TestSharedBudgetSpansTasks(t *testing.T) {
	src := hdr + sharedCfg + "do:\n" +
		healingTask("first", "flaky", 5, false) +
		healingTask("second", "broken", 5, false)
	res := mustFail(t, src, Options{
		Handlers: map[string]Handler{
			"flaky":  failFirst(2, "rows"), // consumes 2 of the 3 shared units
			"broken": failFirst(99, nil),   // gets the last unit, still fails
		},
		Clock: &fakeClock{},
	}, "shared-budget", "second")
	if res.Healed != 3 {
		t.Fatalf("Healed = %d, want 3", res.Healed)
	}
	if res.Context["first"] != "rows" {
		t.Fatalf("context = %+v", res.Context)
	}
	// second: one first try + exactly one retry before the pool ran dry
	var secondAttempts int
	for _, a := range res.Timeline {
		if a.Task == "second" {
			secondAttempts++
		}
	}
	if secondAttempts != 2 {
		t.Fatalf("second attempts = %d, want 2", secondAttempts)
	}
}

func TestExhaustedSharedBudgetBlocksRetriesEntirely(t *testing.T) {
	clock := &fakeClock{}
	src := hdr + `self_healing:
  budget: 1
  base_delay_seconds: 2
do:
` + healingTask("first", "flaky", 5, false) +
		healingTask("second", "broken", 5, false)
	res := mustFail(t, src, Options{
		Handlers: map[string]Handler{
			"flaky":  failFirst(1, "rows"),
			"broken": failFirst(99, nil),
		},
		Clock: clock,
	}, "shared-budget", "second")
	if res.Healed != 1 {
		t.Fatalf("Healed = %d, want 1", res.Healed)
	}
	// second never got a retry: no second sleep, one attempt only
	if len(clock.sleeps) != 1 {
		t.Fatalf("sleeps = %v, want exactly the first task's retry", clock.sleeps)
	}
	var secondAttempts int
	for _, a := range res.Timeline {
		if a.Task == "second" {
			secondAttempts++
		}
	}
	if secondAttempts != 1 {
		t.Fatalf("second attempts = %d, want 1", secondAttempts)
	}
}

// ----------------------------------------------------------------- approval

func TestApprovalIsRequestedBeforeEveryRetry(t *testing.T) {
	var reqs []ApprovalRequest
	approver := func(req ApprovalRequest) bool {
		reqs = append(reqs, req)
		return true
	}
	src := hdr + sharedCfg + "do:\n" + healingTask("fetch", "probe", 3, true)
	res := mustComplete(t, src, Options{
		Handlers: map[string]Handler{"probe": failFirst(2, "rows")},
		Approver: approver,
		Clock:    &fakeClock{},
	})
	if len(reqs) != 2 {
		t.Fatalf("approval requests = %+v", reqs)
	}
	if reqs[0].Task != "fetch" || reqs[0].Attempt != 2 ||
		!strings.Contains(reqs[0].Reason, "boom 1") {
		t.Fatalf("first request = %+v", reqs[0])
	}
	if reqs[1].Task != "fetch" || reqs[1].Attempt != 3 ||
		!strings.Contains(reqs[1].Reason, "boom 2") {
		t.Fatalf("second request = %+v", reqs[1])
	}
	if res.Healed != 2 {
		t.Fatalf("Healed = %d, want 2", res.Healed)
	}
}

func TestDeniedApprovalFailsImmediately(t *testing.T) {
	clock := &fakeClock{}
	src := hdr + sharedCfg + "do:\n" + healingTask("fetch", "probe", 3, true)
	res := mustFail(t, src, Options{
		Handlers: map[string]Handler{"probe": failFirst(99, nil)},
		Approver: func(req ApprovalRequest) bool { return false },
		Clock:    clock,
	}, "approval-denied", "fetch")
	if len(res.Timeline) != 1 {
		t.Fatalf("timeline = %+v, want the single denied first try", res.Timeline)
	}
	if len(clock.sleeps) != 0 {
		t.Fatalf("sleeps = %v, want none — denial must not back off", clock.sleeps)
	}
	if res.Healed != 0 {
		t.Fatalf("Healed = %d, want 0 — denial must not consume budget", res.Healed)
	}
}

func TestApprovalWithoutApproverFailsImmediately(t *testing.T) {
	src := hdr + sharedCfg + "do:\n" + healingTask("fetch", "probe", 3, true)
	res := mustFail(t, src, Options{
		Handlers: map[string]Handler{"probe": failFirst(99, nil)},
		Clock:    &fakeClock{},
	}, "no-approver", "fetch")
	if res.Healed != 0 {
		t.Fatalf("Healed = %d, want 0", res.Healed)
	}
}
