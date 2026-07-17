package supervisor

import (
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"
)

// The supervisor is a synchronous state machine: nothing moves unless the
// test calls Start / ProcessExited / Tick / Stop, and all time comes from the
// injected clock. There are deliberately no goroutines and no sleeps here.

var t0 = time.Date(2026, 3, 1, 9, 0, 0, 0, time.UTC)

type fakeClock struct{ now time.Time }

func (c *fakeClock) Now() time.Time          { return c.now }
func (c *fakeClock) Advance(d time.Duration) { c.now = c.now.Add(d) }
func newClock() *fakeClock                   { return &fakeClock{now: t0} }

// scriptedProc fails Start once per entry in script (consumed in order) and
// succeeds on every call after the script is exhausted.
type scriptedProc struct {
	script []error
	starts int
}

func (p *scriptedProc) Start() error {
	i := p.starts
	p.starts++
	if i < len(p.script) {
		return p.script[i]
	}
	return nil
}

func mustNew(t *testing.T, cfg Config, clk Clock) *Supervisor {
	t.Helper()
	s, err := New(cfg, clk)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return s
}

func kinds(events []Event) []string {
	out := make([]string, 0, len(events))
	for _, e := range events {
		out = append(out, e.Kind)
	}
	return out
}

func TestStartLaunchesProcessAndLogsEvent(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{}
	s := mustNew(t, Config{Process: proc, Policy: Always, BackoffBase: time.Second}, clk)

	if got := s.State(); got != StateIdle {
		t.Fatalf("state before Start = %q, want %q", got, StateIdle)
	}
	if err := s.Start(); err != nil {
		t.Fatalf("Start: %v", err)
	}
	if proc.starts != 1 {
		t.Fatalf("process started %d times, want 1", proc.starts)
	}
	if got := s.State(); got != StateRunning {
		t.Fatalf("state = %q, want %q", got, StateRunning)
	}
	want := []Event{{At: t0, Kind: "started"}}
	if got := s.Events(); !reflect.DeepEqual(got, want) {
		t.Fatalf("events = %+v, want %+v", got, want)
	}
	if _, ok := s.PendingRestartAt(); ok {
		t.Fatal("PendingRestartAt reports a pending restart while running")
	}
}

func TestStartTwiceIsAnError(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{}
	s := mustNew(t, Config{Process: proc, Policy: Always}, clk)
	if err := s.Start(); err != nil {
		t.Fatalf("Start: %v", err)
	}
	if err := s.Start(); err == nil {
		t.Fatal("second Start succeeded, want error")
	}
	if proc.starts != 1 {
		t.Fatalf("process started %d times after double Start, want 1", proc.starts)
	}
	if n := len(s.Events()); n != 1 {
		t.Fatalf("event log has %d entries after rejected Start, want 1", n)
	}
}

func TestNewValidatesConfig(t *testing.T) {
	clk := newClock()
	cases := []struct {
		name string
		cfg  Config
	}{
		{"nil process", Config{Policy: Always}},
		{"unknown policy", Config{Process: &scriptedProc{}, Policy: Policy("sometimes")}},
		{"max restarts without window", Config{Process: &scriptedProc{}, Policy: Always, MaxRestarts: 3}},
		{"cap below base", Config{Process: &scriptedProc{}, Policy: Always, BackoffBase: 4 * time.Second, BackoffCap: time.Second}},
	}
	for _, tc := range cases {
		if _, err := New(tc.cfg, clk); err == nil {
			t.Errorf("%s: New accepted invalid config", tc.name)
		}
	}
}

func TestCrashRestartGoesThroughBackoffState(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{}
	s := mustNew(t, Config{Process: proc, Policy: Always, BackoffBase: 2 * time.Second}, clk)
	if err := s.Start(); err != nil {
		t.Fatalf("Start: %v", err)
	}

	clk.Advance(5 * time.Second)
	if err := s.ProcessExited(1); err != nil {
		t.Fatalf("ProcessExited: %v", err)
	}
	if got := s.State(); got != StateBackoff {
		t.Fatalf("state after crash = %q, want %q", got, StateBackoff)
	}
	due, ok := s.PendingRestartAt()
	if !ok || !due.Equal(t0.Add(7*time.Second)) {
		t.Fatalf("PendingRestartAt = (%v, %v), want (%v, true)", due, ok, t0.Add(7*time.Second))
	}
	want := []string{"started", "exited", "restart-scheduled"}
	if got := kinds(s.Events()); !reflect.DeepEqual(got, want) {
		t.Fatalf("event kinds = %v, want %v", got, want)
	}

	// One second early: nothing happens.
	clk.Advance(time.Second)
	s.Tick()
	if proc.starts != 1 || s.State() != StateBackoff {
		t.Fatalf("early Tick restarted: starts=%d state=%q", proc.starts, s.State())
	}

	// Exactly at the due time: the restart fires.
	clk.Advance(time.Second)
	s.Tick()
	if proc.starts != 2 {
		t.Fatalf("process started %d times after due Tick, want 2", proc.starts)
	}
	if got := s.State(); got != StateRunning {
		t.Fatalf("state after restart = %q, want %q", got, StateRunning)
	}
	last := s.Events()[len(s.Events())-1]
	if last.Kind != "started" || !last.At.Equal(t0.Add(7*time.Second)) {
		t.Fatalf("last event = %+v, want started at %v", last, t0.Add(7*time.Second))
	}
}

func TestBackoffDelaysDoubleUpToCap(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{}
	s := mustNew(t, Config{
		Process:     proc,
		Policy:      Always,
		BackoffBase: time.Second,
		BackoffCap:  4 * time.Second,
		StableAfter: time.Hour, // nothing in this test counts as a stable run
	}, clk)
	if err := s.Start(); err != nil {
		t.Fatalf("Start: %v", err)
	}

	wantDelays := []time.Duration{1 * time.Second, 2 * time.Second, 4 * time.Second, 4 * time.Second}
	for i, want := range wantDelays {
		if err := s.ProcessExited(1); err != nil {
			t.Fatalf("crash %d: %v", i+1, err)
		}
		events := s.Events()
		sched := events[len(events)-1]
		if sched.Kind != "restart-scheduled" || sched.Delay != want {
			t.Fatalf("crash %d: last event = %+v, want restart-scheduled with delay %v", i+1, sched, want)
		}
		clk.Advance(want)
		s.Tick()
		if s.State() != StateRunning {
			t.Fatalf("crash %d: state after Tick = %q, want running", i+1, s.State())
		}
	}
}

func TestStableRunResetsBackoffSequence(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{}
	s := mustNew(t, Config{
		Process:     proc,
		Policy:      Always,
		BackoffBase: time.Second,
		StableAfter: 10 * time.Second,
	}, clk)
	if err := s.Start(); err != nil {
		t.Fatalf("Start: %v", err)
	}

	// Two quick crashes push the delay to 2s.
	s.ProcessExited(1)
	clk.Advance(time.Second)
	s.Tick()
	s.ProcessExited(1)
	events := s.Events()
	if d := events[len(events)-1].Delay; d != 2*time.Second {
		t.Fatalf("second crash delay = %v, want 2s", d)
	}
	clk.Advance(2 * time.Second)
	s.Tick()

	// This run survives exactly StableAfter, which counts as stable, so the
	// next crash starts the sequence over at the base delay.
	clk.Advance(10 * time.Second)
	s.ProcessExited(1)
	events = s.Events()
	if d := events[len(events)-1].Delay; d != time.Second {
		t.Fatalf("delay after stable run = %v, want reset to 1s", d)
	}
}

func TestOnFailurePolicyIgnoresCleanExit(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{}
	s := mustNew(t, Config{Process: proc, Policy: OnFailure, BackoffBase: time.Second}, clk)
	if err := s.Start(); err != nil {
		t.Fatalf("Start: %v", err)
	}
	if err := s.ProcessExited(0); err != nil {
		t.Fatalf("ProcessExited: %v", err)
	}
	if got := s.State(); got != StateDone {
		t.Fatalf("state after clean exit = %q, want %q", got, StateDone)
	}
	clk.Advance(time.Minute)
	s.Tick()
	if proc.starts != 1 {
		t.Fatalf("process restarted after clean exit under on-failure: starts=%d", proc.starts)
	}
	want := []string{"started", "exited"}
	if got := kinds(s.Events()); !reflect.DeepEqual(got, want) {
		t.Fatalf("event kinds = %v, want %v", got, want)
	}
}

func TestOnFailurePolicyRestartsOnNonZeroExit(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{}
	s := mustNew(t, Config{Process: proc, Policy: OnFailure, BackoffBase: time.Second}, clk)
	s.Start()
	s.ProcessExited(3)
	if got := s.State(); got != StateBackoff {
		t.Fatalf("state after exit 3 = %q, want %q", got, StateBackoff)
	}
}

func TestAlwaysPolicyRestartsOnCleanExit(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{}
	s := mustNew(t, Config{Process: proc, Policy: Always, BackoffBase: time.Second}, clk)
	s.Start()
	s.ProcessExited(0)
	if got := s.State(); got != StateBackoff {
		t.Fatalf("state after clean exit under always = %q, want %q", got, StateBackoff)
	}
}

func TestNeverPolicyNeverRestarts(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{}
	s := mustNew(t, Config{Process: proc, Policy: Never, BackoffBase: time.Second}, clk)
	s.Start()
	s.ProcessExited(5)
	if got := s.State(); got != StateDone {
		t.Fatalf("state = %q, want %q", got, StateDone)
	}
	want := []Event{
		{At: t0, Kind: "started"},
		{At: t0, Kind: "exited", Code: 5},
	}
	if got := s.Events(); !reflect.DeepEqual(got, want) {
		t.Fatalf("events = %+v, want %+v", got, want)
	}
}

func TestFailedLaunchFollowsRestartPolicy(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{script: []error{errors.New("binary not found")}}
	s := mustNew(t, Config{Process: proc, Policy: OnFailure, BackoffBase: 3 * time.Second}, clk)

	// Start returns nil: supervision began; the launch failure is recorded in
	// the event log and handled by the restart policy.
	if err := s.Start(); err != nil {
		t.Fatalf("Start: %v", err)
	}
	want := []Event{
		{At: t0, Kind: "start-failed", Err: "binary not found"},
		{At: t0, Kind: "restart-scheduled", Delay: 3 * time.Second},
	}
	if got := s.Events(); !reflect.DeepEqual(got, want) {
		t.Fatalf("events = %+v, want %+v", got, want)
	}
	if got := s.State(); got != StateBackoff {
		t.Fatalf("state = %q, want %q", got, StateBackoff)
	}

	clk.Advance(3 * time.Second)
	s.Tick()
	if proc.starts != 2 || s.State() != StateRunning {
		t.Fatalf("after retry: starts=%d state=%q, want 2/running", proc.starts, s.State())
	}
}

func TestFailedRestartSchedulesNextBackoff(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{script: []error{nil, errors.New("port in use")}}
	s := mustNew(t, Config{Process: proc, Policy: Always, BackoffBase: time.Second, StableAfter: time.Hour}, clk)
	s.Start()
	s.ProcessExited(1)
	clk.Advance(time.Second)
	s.Tick() // this restart attempt fails to launch
	if got := s.State(); got != StateBackoff {
		t.Fatalf("state after failed restart = %q, want %q", got, StateBackoff)
	}
	events := s.Events()
	last := events[len(events)-1]
	prev := events[len(events)-2]
	if prev.Kind != "start-failed" || prev.Err != "port in use" {
		t.Fatalf("expected start-failed event, got %+v", prev)
	}
	if last.Kind != "restart-scheduled" || last.Delay != 2*time.Second {
		t.Fatalf("expected doubled restart-scheduled delay 2s, got %+v", last)
	}
	clk.Advance(2 * time.Second)
	s.Tick()
	if proc.starts != 3 || s.State() != StateRunning {
		t.Fatalf("after second retry: starts=%d state=%q", proc.starts, s.State())
	}
}

func TestRestartBudgetExhaustedWithinWindowGivesUp(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{}
	s := mustNew(t, Config{
		Process:     proc,
		Policy:      Always,
		BackoffBase: time.Second,
		StableAfter: time.Hour,
		MaxRestarts: 2,
		Window:      time.Minute,
	}, clk)
	s.Start()

	for i := 0; i < 2; i++ {
		s.ProcessExited(1)
		delay := s.Events()[len(s.Events())-1].Delay
		clk.Advance(delay)
		s.Tick()
		if s.State() != StateRunning {
			t.Fatalf("restart %d did not run: state=%q", i+1, s.State())
		}
	}

	// Third crash inside the window: budget (2 restarts / 60s) is spent.
	s.ProcessExited(1)
	if got := s.State(); got != StateGivenUp {
		t.Fatalf("state = %q, want %q", got, StateGivenUp)
	}
	last := s.Events()[len(s.Events())-1]
	if last.Kind != "gave-up" {
		t.Fatalf("last event = %+v, want gave-up", last)
	}
	if _, ok := s.PendingRestartAt(); ok {
		t.Fatal("restart still pending after giving up")
	}
	clk.Advance(time.Hour)
	s.Tick()
	if proc.starts != 3 {
		t.Fatalf("process restarted after gave-up: starts=%d", proc.starts)
	}
}

func TestRestartsOutsideWindowNoLongerCount(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{}
	s := mustNew(t, Config{
		Process:     proc,
		Policy:      Always,
		MaxRestarts: 1,
		Window:      10 * time.Second,
		// BackoffBase 0: restarts are immediate (delay 0), and 0 never doubles.
	}, clk)
	s.Start()
	s.ProcessExited(1)
	sched := s.Events()[len(s.Events())-1]
	if sched.Kind != "restart-scheduled" || sched.Delay != 0 {
		t.Fatalf("expected zero-delay restart-scheduled, got %+v", sched)
	}
	s.Tick() // restart happens at t0, using the whole budget

	// The process then runs for exactly the window length. A restart exactly
	// Window ago has aged out, so the next crash may schedule again.
	clk.Advance(10 * time.Second)
	s.ProcessExited(1)
	if got := s.State(); got != StateBackoff {
		t.Fatalf("state = %q, want %q (budget entry should have expired)", got, StateBackoff)
	}
}

func TestStopFromRunning(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{}
	s := mustNew(t, Config{Process: proc, Policy: Always}, clk)
	s.Start()
	clk.Advance(2 * time.Second)
	if err := s.Stop(); err != nil {
		t.Fatalf("Stop: %v", err)
	}
	if got := s.State(); got != StateStopped {
		t.Fatalf("state = %q, want %q", got, StateStopped)
	}
	last := s.Events()[len(s.Events())-1]
	if last.Kind != "stopped" || !last.At.Equal(t0.Add(2*time.Second)) {
		t.Fatalf("last event = %+v, want stopped at %v", last, t0.Add(2*time.Second))
	}
	if err := s.ProcessExited(0); err == nil {
		t.Fatal("ProcessExited accepted after Stop")
	}
	if err := s.Start(); err == nil {
		t.Fatal("Start accepted after Stop")
	}
}

func TestStopCancelsPendingRestart(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{}
	s := mustNew(t, Config{Process: proc, Policy: Always, BackoffBase: time.Second}, clk)
	s.Start()
	s.ProcessExited(1)
	if err := s.Stop(); err != nil {
		t.Fatalf("Stop in backoff: %v", err)
	}
	clk.Advance(time.Minute)
	s.Tick()
	if proc.starts != 1 {
		t.Fatalf("cancelled restart still ran: starts=%d", proc.starts)
	}
	if got := s.State(); got != StateStopped {
		t.Fatalf("state = %q, want %q", got, StateStopped)
	}
}

func TestStopWhenNotSupervisingErrors(t *testing.T) {
	clk := newClock()
	s := mustNew(t, Config{Process: &scriptedProc{}, Policy: Never}, clk)
	if err := s.Stop(); err == nil {
		t.Fatal("Stop accepted while idle")
	}
	s.Start()
	s.ProcessExited(0)
	if s.State() != StateDone {
		t.Fatalf("state = %q, want done", s.State())
	}
	if err := s.Stop(); err == nil {
		t.Fatal("Stop accepted after process is done")
	}
}

func TestProcessExitedWhenNotRunningErrors(t *testing.T) {
	clk := newClock()
	s := mustNew(t, Config{Process: &scriptedProc{}, Policy: Always, BackoffBase: time.Second}, clk)
	if err := s.ProcessExited(1); err == nil {
		t.Fatal("ProcessExited accepted while idle")
	}
	s.Start()
	s.ProcessExited(1) // now in backoff
	if err := s.ProcessExited(1); err == nil {
		t.Fatal("ProcessExited accepted while in backoff")
	}
}

func TestEventLogRecordsFullLifecycle(t *testing.T) {
	clk := newClock()
	proc := &scriptedProc{}
	s := mustNew(t, Config{Process: proc, Policy: OnFailure, BackoffBase: 2 * time.Second}, clk)

	s.Start()
	clk.Advance(3 * time.Second)
	s.ProcessExited(2)
	clk.Advance(2 * time.Second)
	s.Tick()
	clk.Advance(time.Second)
	s.ProcessExited(0)

	want := []Event{
		{At: t0, Kind: "started"},
		{At: t0.Add(3 * time.Second), Kind: "exited", Code: 2},
		{At: t0.Add(3 * time.Second), Kind: "restart-scheduled", Delay: 2 * time.Second},
		{At: t0.Add(5 * time.Second), Kind: "started"},
		{At: t0.Add(6 * time.Second), Kind: "exited", Code: 0},
	}
	if got := s.Events(); !reflect.DeepEqual(got, want) {
		t.Fatalf("events =\n%+v\nwant\n%+v", got, want)
	}
	if got := s.State(); got != StateDone {
		t.Fatalf("state = %q, want %q", got, StateDone)
	}
}

func TestEventsReturnsACopy(t *testing.T) {
	clk := newClock()
	s := mustNew(t, Config{Process: &scriptedProc{}, Policy: Always}, clk)
	s.Start()
	got := s.Events()
	got[0].Kind = "scribbled"
	if s.Events()[0].Kind != "started" {
		t.Fatal("mutating the returned slice corrupted the supervisor's event log")
	}
}

func TestInvalidConfigErrorsMentionTheProblem(t *testing.T) {
	clk := newClock()
	_, err := New(Config{Process: &scriptedProc{}, Policy: Always, MaxRestarts: 3}, clk)
	if err == nil || !strings.Contains(err.Error(), "Window") {
		t.Fatalf("error for MaxRestarts without Window should mention Window, got %v", err)
	}
}
