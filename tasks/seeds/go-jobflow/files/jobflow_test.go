package jobflow

import (
	"context"
	"errors"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// waitBudget bounds every blocking wait in this suite; hitting it is a
// liveness failure, never part of the happy path.
const waitBudget = 5 * time.Second

func recvOrFatal(t *testing.T, ch <-chan struct{}, what string) {
	t.Helper()
	select {
	case <-ch:
	case <-time.After(waitBudget):
		t.Fatalf("timed out after %v waiting for %s", waitBudget, what)
	}
}

type runResult struct {
	states map[JobID]State
	err    error
}

func runAsync(ctx context.Context, o *Orchestrator) <-chan runResult {
	done := make(chan runResult, 1)
	go func() {
		states, err := o.Run(ctx)
		done <- runResult{states, err}
	}()
	return done
}

func waitRun(t *testing.T, done <-chan runResult, what string) runResult {
	t.Helper()
	select {
	case res := <-done:
		return res
	case <-time.After(waitBudget):
		t.Fatalf("timed out after %v waiting for %s", waitBudget, what)
		panic("unreachable")
	}
}

func newOrch(t *testing.T, maxParallel int) *Orchestrator {
	t.Helper()
	o, err := New(Options{MaxParallel: maxParallel})
	if err != nil {
		t.Fatalf("New(MaxParallel=%d): %v", maxParallel, err)
	}
	return o
}

func mustAdd(t *testing.T, o *Orchestrator, spec JobSpec) {
	t.Helper()
	if err := o.Add(spec); err != nil {
		t.Fatalf("Add(%q): %v", spec.ID, err)
	}
}

func ok(context.Context) error { return nil }

func wantStates(t *testing.T, got map[JobID]State, want map[JobID]State) {
	t.Helper()
	if got == nil {
		t.Fatal("Run returned a nil state map")
	}
	if len(got) != len(want) {
		t.Fatalf("state map covers %d job(s), want %d (got %v)", len(got), len(want), got)
	}
	for id, st := range want {
		if got[id] != st {
			t.Fatalf("state[%s] = %q, want %q (full: %v)", id, got[id], st, got)
		}
	}
}

// ----------------------------------------------------------- construction

func TestStateStringValues(t *testing.T) {
	pairs := map[State]string{
		StatePending: "pending", StateRunning: "running",
		StateSucceeded: "succeeded", StateFailed: "failed",
		StateSkipped: "skipped", StateCancelled: "cancelled",
	}
	for st, s := range pairs {
		if string(st) != s {
			t.Fatalf("state constant %v has value %q, want %q", st, string(st), s)
		}
	}
}

func TestNewValidatesOptions(t *testing.T) {
	for _, bad := range []int{0, -3} {
		if _, err := New(Options{MaxParallel: bad}); err == nil {
			t.Fatalf("New accepted MaxParallel=%d", bad)
		}
	}
}

func TestAddValidation(t *testing.T) {
	o := newOrch(t, 1)
	if err := o.Add(JobSpec{ID: "", Run: ok}); err == nil {
		t.Fatal("empty job ID accepted")
	}
	if err := o.Add(JobSpec{ID: "compile"}); err == nil || !strings.Contains(err.Error(), "compile") {
		t.Fatalf("nil Run: want error naming the job, got %v", err)
	}
	mustAdd(t, o, JobSpec{ID: "compile", Run: ok})
	if err := o.Add(JobSpec{ID: "compile", Run: ok}); err == nil || !strings.Contains(err.Error(), "compile") {
		t.Fatalf("duplicate ID: want error naming the job, got %v", err)
	}
}

func TestRunValidatesTheGraph(t *testing.T) {
	o := newOrch(t, 1)
	mustAdd(t, o, JobSpec{ID: "publish", Deps: []JobID{"sign"}, Run: ok})
	if _, err := o.Run(context.Background()); err == nil || !strings.Contains(err.Error(), "sign") {
		t.Fatalf("unknown dep: want error naming the missing dep, got %v", err)
	}

	o = newOrch(t, 1)
	mustAdd(t, o, JobSpec{ID: "a", Deps: []JobID{"c"}, Run: ok})
	mustAdd(t, o, JobSpec{ID: "b", Deps: []JobID{"a"}, Run: ok})
	mustAdd(t, o, JobSpec{ID: "c", Deps: []JobID{"b"}, Run: ok})
	if _, err := o.Run(context.Background()); err == nil || !strings.Contains(err.Error(), "cycle") {
		t.Fatalf("three-job cycle: want error containing \"cycle\", got %v", err)
	}

	o = newOrch(t, 1)
	mustAdd(t, o, JobSpec{ID: "self", Deps: []JobID{"self"}, Run: ok})
	if _, err := o.Run(context.Background()); err == nil || !strings.Contains(err.Error(), "cycle") {
		t.Fatalf("self-dependency: want error containing \"cycle\", got %v", err)
	}
}

// ------------------------------------------------------------- scheduling

func TestDependenciesGateStartsAndEverythingSucceeds(t *testing.T) {
	var mu sync.Mutex
	var events []string
	log := func(ev string) {
		mu.Lock()
		defer mu.Unlock()
		events = append(events, ev)
	}
	tracked := func(id JobID, deps ...JobID) JobSpec {
		return JobSpec{ID: id, Deps: deps, Run: func(context.Context) error {
			log("start:" + string(id))
			log("end:" + string(id))
			return nil
		}}
	}
	o := newOrch(t, 4)
	mustAdd(t, o, tracked("fetch"))
	mustAdd(t, o, tracked("parse", "fetch"))
	mustAdd(t, o, tracked("lint", "fetch"))
	mustAdd(t, o, tracked("report", "parse", "lint"))

	states, err := o.Run(context.Background())
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	wantStates(t, states, map[JobID]State{
		"fetch": StateSucceeded, "parse": StateSucceeded,
		"lint": StateSucceeded, "report": StateSucceeded,
	})

	mu.Lock()
	got := append([]string(nil), events...)
	mu.Unlock()
	index := func(ev string) int {
		for i, e := range got {
			if e == ev {
				return i
			}
		}
		t.Fatalf("event %q never happened: %v", ev, got)
		panic("unreachable")
	}
	for _, pair := range [][2]string{
		{"end:fetch", "start:parse"},
		{"end:fetch", "start:lint"},
		{"end:parse", "start:report"},
		{"end:lint", "start:report"},
	} {
		if index(pair[0]) >= index(pair[1]) {
			t.Fatalf("want %q before %q, events: %v", pair[0], pair[1], got)
		}
	}
}

func TestParallelismIsBoundedAndActuallyUsed(t *testing.T) {
	o := newOrch(t, 2)
	var mu sync.Mutex
	current, peak := 0, 0
	started := make(chan struct{}, 4)
	release := make(chan struct{})
	for _, id := range []JobID{"resize", "transcode", "thumbnail", "checksum"} {
		mustAdd(t, o, JobSpec{ID: id, Run: func(context.Context) error {
			mu.Lock()
			current++
			if current > peak {
				peak = current
			}
			mu.Unlock()
			started <- struct{}{}
			<-release
			mu.Lock()
			current--
			mu.Unlock()
			return nil
		}})
	}
	done := runAsync(context.Background(), o)
	for phase := 1; phase <= 2; phase++ {
		for i := 0; i < 2; i++ {
			recvOrFatal(t, started, "a worker slot to start a job")
		}
		release <- struct{}{}
		release <- struct{}{}
	}
	res := waitRun(t, done, "Run to finish")
	if res.err != nil {
		t.Fatalf("Run: %v", res.err)
	}
	mu.Lock()
	gotPeak := peak
	mu.Unlock()
	if gotPeak > 2 {
		t.Fatalf("%d jobs ran at once, MaxParallel is 2", gotPeak)
	}
	if gotPeak < 2 {
		t.Fatalf("peak concurrency %d: independent jobs are being serialized", gotPeak)
	}
}

func TestPriorityDecidesWhoGetsAFreeSlot(t *testing.T) {
	var mu sync.Mutex
	var order []JobID
	logged := func(id JobID, pri int, deps ...JobID) JobSpec {
		return JobSpec{ID: id, Deps: deps, Priority: pri, Run: func(context.Context) error {
			mu.Lock()
			order = append(order, id)
			mu.Unlock()
			return nil
		}}
	}

	// Four jobs, all ready at Run start, one slot: strict priority order,
	// ties broken by Add order.
	o := newOrch(t, 1)
	mustAdd(t, o, logged("alpha", 1))
	mustAdd(t, o, logged("bravo", 9))
	mustAdd(t, o, logged("charlie", 5))
	mustAdd(t, o, logged("delta", 9))
	if _, err := o.Run(context.Background()); err != nil {
		t.Fatalf("Run: %v", err)
	}
	mu.Lock()
	got := append([]JobID(nil), order...)
	order = order[:0]
	mu.Unlock()
	want := []JobID{"bravo", "delta", "charlie", "alpha"}
	for i := range want {
		if i >= len(got) || got[i] != want[i] {
			t.Fatalf("execution order %v, want %v", got, want)
		}
	}

	// Both dependents become ready when root finishes; the higher priority
	// one must claim the single slot first even though it was added later.
	o = newOrch(t, 1)
	mustAdd(t, o, logged("root", 0))
	mustAdd(t, o, logged("low", 1, "root"))
	mustAdd(t, o, logged("high", 8, "root"))
	if _, err := o.Run(context.Background()); err != nil {
		t.Fatalf("Run: %v", err)
	}
	mu.Lock()
	got = append([]JobID(nil), order...)
	mu.Unlock()
	want = []JobID{"root", "high", "low"}
	for i := range want {
		if i >= len(got) || got[i] != want[i] {
			t.Fatalf("execution order %v, want %v", got, want)
		}
	}
}

// ---------------------------------------------------------- status queries

func TestStatusThroughTheLifecycle(t *testing.T) {
	started := make(chan struct{})
	release := make(chan struct{})
	o := newOrch(t, 1)
	mustAdd(t, o, JobSpec{ID: "extract", Run: func(context.Context) error {
		close(started)
		<-release
		return nil
	}})
	mustAdd(t, o, JobSpec{ID: "load", Deps: []JobID{"extract"}, Run: ok})

	if _, err := o.Status("nope"); err == nil {
		t.Fatal("Status of an unknown job must be an error")
	}
	for _, id := range []JobID{"extract", "load"} {
		if st, err := o.Status(id); err != nil || st != StatePending {
			t.Fatalf("before Run: Status(%s) = %q, %v; want %q", id, st, err, StatePending)
		}
	}

	done := runAsync(context.Background(), o)
	recvOrFatal(t, started, "the extract job to start")
	if st, _ := o.Status("extract"); st != StateRunning {
		t.Fatalf("mid-run: Status(extract) = %q, want %q", st, StateRunning)
	}
	if st, _ := o.Status("load"); st != StatePending {
		t.Fatalf("mid-run: Status(load) = %q, want %q", st, StatePending)
	}
	close(release)

	res := waitRun(t, done, "Run to finish")
	if res.err != nil {
		t.Fatalf("Run: %v", res.err)
	}
	wantStates(t, res.states, map[JobID]State{
		"extract": StateSucceeded, "load": StateSucceeded,
	})
	if st, _ := o.Status("load"); st != StateSucceeded {
		t.Fatalf("after Run: Status(load) = %q, want %q", st, StateSucceeded)
	}
}

// -------------------------------------------------------------- failures

func TestFailureSkipsDependentsButUnrelatedWorkFinishes(t *testing.T) {
	boom := errors.New("migration table locked")
	var seedRan, deployRan, assetsRan int32
	o := newOrch(t, 2)
	mustAdd(t, o, JobSpec{ID: "migrate", Run: func(context.Context) error { return boom }})
	mustAdd(t, o, JobSpec{ID: "seed", Deps: []JobID{"migrate"}, Run: func(context.Context) error {
		atomic.AddInt32(&seedRan, 1)
		return nil
	}})
	mustAdd(t, o, JobSpec{ID: "deploy", Deps: []JobID{"seed"}, Run: func(context.Context) error {
		atomic.AddInt32(&deployRan, 1)
		return nil
	}})
	mustAdd(t, o, JobSpec{ID: "assets", Run: func(context.Context) error {
		atomic.AddInt32(&assetsRan, 1)
		return nil
	}})

	states, err := o.Run(context.Background())
	if err == nil || !errors.Is(err, boom) {
		t.Fatalf("want Run error wrapping the job's error (errors.Is), got %v", err)
	}
	if !strings.Contains(err.Error(), "migrate") {
		t.Fatalf("Run error should name the failed job, got %v", err)
	}
	if atomic.LoadInt32(&assetsRan) != 1 {
		t.Fatal("job outside the failed branch must still run")
	}
	if atomic.LoadInt32(&seedRan) != 0 || atomic.LoadInt32(&deployRan) != 0 {
		t.Fatal("dependents of a failed job must never run")
	}
	wantStates(t, states, map[JobID]State{
		"migrate": StateFailed, "seed": StateSkipped,
		"deploy": StateSkipped, "assets": StateSucceeded,
	})
}

// ------------------------------------------------------------ cancellation

func TestCancellationStopsJobPickup(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	firstStarted := make(chan struct{})
	var secondRan, thirdRan int32
	o := newOrch(t, 1)
	// Priority guarantees "first" claims the single slot; it holds it until
	// the context we control is cancelled, so nothing else can ever start.
	mustAdd(t, o, JobSpec{ID: "first", Priority: 10, Run: func(jobCtx context.Context) error {
		close(firstStarted)
		<-jobCtx.Done()
		return jobCtx.Err()
	}})
	mustAdd(t, o, JobSpec{ID: "second", Run: func(context.Context) error {
		atomic.AddInt32(&secondRan, 1)
		return nil
	}})
	mustAdd(t, o, JobSpec{ID: "third", Deps: []JobID{"first"}, Run: func(context.Context) error {
		atomic.AddInt32(&thirdRan, 1)
		return nil
	}})

	done := runAsync(ctx, o)
	recvOrFatal(t, firstStarted, "the first job to start")
	cancel()
	res := waitRun(t, done, "Run to return after cancellation")

	if res.err == nil || !errors.Is(res.err, context.Canceled) {
		t.Fatalf("want Run error satisfying errors.Is(err, context.Canceled), got %v", res.err)
	}
	if atomic.LoadInt32(&secondRan) != 0 || atomic.LoadInt32(&thirdRan) != 0 {
		t.Fatal("no new job may start once the context is cancelled")
	}
	wantStates(t, res.states, map[JobID]State{
		"first": StateFailed, "second": StateCancelled, "third": StateCancelled,
	})
}

func TestCancellationWaitsForInFlightJobs(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	started := make(chan struct{})
	release := make(chan struct{})
	o := newOrch(t, 1)
	// flush ignores the context on purpose: it finishes on our signal and
	// must be reported as succeeded, proving Run drained in-flight work.
	mustAdd(t, o, JobSpec{ID: "flush", Run: func(context.Context) error {
		close(started)
		<-release
		return nil
	}})
	mustAdd(t, o, JobSpec{ID: "notify", Deps: []JobID{"flush"}, Run: ok})

	done := runAsync(ctx, o)
	recvOrFatal(t, started, "the flush job to start")
	cancel()
	close(release)
	res := waitRun(t, done, "Run to return after cancellation")

	if res.err == nil || !errors.Is(res.err, context.Canceled) {
		t.Fatalf("want Run error satisfying errors.Is(err, context.Canceled), got %v", res.err)
	}
	wantStates(t, res.states, map[JobID]State{
		"flush": StateSucceeded, "notify": StateCancelled,
	})
}

// --------------------------------------------------------------- lifecycle

func TestRunIsSingleUseAndSealsAdd(t *testing.T) {
	o := newOrch(t, 1)
	mustAdd(t, o, JobSpec{ID: "once", Run: ok})
	if _, err := o.Run(context.Background()); err != nil {
		t.Fatalf("first Run: %v", err)
	}
	if _, err := o.Run(context.Background()); err == nil {
		t.Fatal("second Run must be rejected")
	}
	if err := o.Add(JobSpec{ID: "late", Run: ok}); err == nil {
		t.Fatal("Add after Run must be rejected")
	}
}

func TestRunWithNoJobs(t *testing.T) {
	o := newOrch(t, 3)
	states, err := o.Run(context.Background())
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if len(states) != 0 {
		t.Fatalf("empty orchestrator produced states %v", states)
	}
}
