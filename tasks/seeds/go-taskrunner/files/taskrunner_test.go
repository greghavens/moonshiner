package taskrunner

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type eventLog struct {
	mu     sync.Mutex
	events []string
}

func (l *eventLog) add(ev string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.events = append(l.events, ev)
}

func (l *eventLog) snapshot() []string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]string(nil), l.events...)
}

func indexOf(events []string, ev string) int {
	for i, e := range events {
		if e == ev {
			return i
		}
	}
	return -1
}

func noop(context.Context) error { return nil }

func tracked(l *eventLog, name string, deps ...string) Task {
	return Task{
		Name: name,
		Deps: deps,
		Action: func(context.Context) error {
			l.add("start:" + name)
			l.add("end:" + name)
			return nil
		},
	}
}

func TestStatusStringValues(t *testing.T) {
	if StatusOK != "ok" || StatusFailed != "failed" || StatusSkipped != "skipped" {
		t.Fatalf("status constants have wrong values: %q %q %q", StatusOK, StatusFailed, StatusSkipped)
	}
}

func TestNewRunnerValidation(t *testing.T) {
	if _, err := NewRunner([]Task{{Name: "build", Action: noop}, {Name: "build", Action: noop}}); err == nil || !strings.Contains(err.Error(), "build") {
		t.Fatalf("duplicate task name: want error naming the task, got %v", err)
	}
	if _, err := NewRunner([]Task{{Name: "deploy", Deps: []string{"package"}, Action: noop}}); err == nil || !strings.Contains(err.Error(), "package") {
		t.Fatalf("unknown dep: want error naming the missing dep, got %v", err)
	}
	if _, err := NewRunner([]Task{{Name: "", Action: noop}}); err == nil {
		t.Fatal("empty task name accepted")
	}
	if _, err := NewRunner([]Task{{Name: "x"}}); err == nil {
		t.Fatal("nil action accepted")
	}
}

func TestNewRunnerRejectsCycles(t *testing.T) {
	_, err := NewRunner([]Task{
		{Name: "a", Deps: []string{"c"}, Action: noop},
		{Name: "b", Deps: []string{"a"}, Action: noop},
		{Name: "c", Deps: []string{"b"}, Action: noop},
	})
	if err == nil || !strings.Contains(err.Error(), "cycle") {
		t.Fatalf("three-task cycle: want error containing \"cycle\", got %v", err)
	}
	_, err = NewRunner([]Task{{Name: "self", Deps: []string{"self"}, Action: noop}})
	if err == nil || !strings.Contains(err.Error(), "cycle") {
		t.Fatalf("self-dependency: want error containing \"cycle\", got %v", err)
	}
}

func TestDepsCompleteBeforeDependentsStart(t *testing.T) {
	log := &eventLog{}
	r, err := NewRunner([]Task{
		tracked(log, "generate"),
		tracked(log, "build", "generate"),
		tracked(log, "lint", "generate"),
		tracked(log, "package", "build", "lint"),
	})
	if err != nil {
		t.Fatalf("NewRunner: %v", err)
	}
	rep, err := r.Run(context.Background(), "package")
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	for _, name := range []string{"generate", "build", "lint", "package"} {
		if rep.Statuses[name] != StatusOK {
			t.Fatalf("status[%s] = %q, want %q", name, rep.Statuses[name], StatusOK)
		}
	}
	events := log.snapshot()
	for _, pair := range [][2]string{
		{"end:generate", "start:build"},
		{"end:generate", "start:lint"},
		{"end:build", "start:package"},
		{"end:lint", "start:package"},
	} {
		b, a := indexOf(events, pair[0]), indexOf(events, pair[1])
		if b == -1 || a == -1 || b >= a {
			t.Fatalf("want %q before %q, events: %v", pair[0], pair[1], events)
		}
	}
}

func TestRunOnlyTouchesTargetClosure(t *testing.T) {
	log := &eventLog{}
	var stray int32
	r, err := NewRunner([]Task{
		tracked(log, "fmt"),
		tracked(log, "vet", "fmt"),
		{Name: "docs", Action: func(context.Context) error { atomic.AddInt32(&stray, 1); return nil }},
	})
	if err != nil {
		t.Fatalf("NewRunner: %v", err)
	}
	rep, err := r.Run(context.Background(), "vet")
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if atomic.LoadInt32(&stray) != 0 {
		t.Fatal("task outside the target's dependency closure was executed")
	}
	if len(rep.Statuses) != 2 {
		t.Fatalf("report should cover exactly the closure, got %v", rep.Statuses)
	}
	if _, ok := rep.Statuses["docs"]; ok {
		t.Fatal("unrelated task appears in report")
	}
}

func TestRunUnknownTarget(t *testing.T) {
	r, err := NewRunner([]Task{{Name: "only", Action: noop}})
	if err != nil {
		t.Fatalf("NewRunner: %v", err)
	}
	if _, err := r.Run(context.Background(), "release"); err == nil || !strings.Contains(err.Error(), "release") {
		t.Fatalf("unknown target: want error naming it, got %v", err)
	}
}

func TestIndependentTasksRunConcurrently(t *testing.T) {
	started := make(chan string, 2)
	release := make(chan struct{})
	mk := func(name string) func(context.Context) error {
		return func(context.Context) error {
			started <- name
			select {
			case <-release:
				return nil
			case <-time.After(3 * time.Second):
				return fmt.Errorf("%s: peer never started — independent tasks appear to run one at a time", name)
			}
		}
	}
	r, err := NewRunner([]Task{
		{Name: "lint", Action: mk("lint")},
		{Name: "vet", Action: mk("vet")},
		{Name: "check", Deps: []string{"lint", "vet"}, Action: noop},
	})
	if err != nil {
		t.Fatalf("NewRunner: %v", err)
	}
	go func() {
		<-started
		<-started
		close(release)
	}()
	rep, err := r.Run(context.Background(), "check")
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if rep.Statuses["check"] != StatusOK {
		t.Fatalf("check = %q, want %q", rep.Statuses["check"], StatusOK)
	}
}

func TestFailureSkipsDependentsButIndependentWorkFinishes(t *testing.T) {
	boom := errors.New("migration table locked")
	var seedRan, assetsRan, deployRan int32
	r, err := NewRunner([]Task{
		{Name: "migrate", Action: func(context.Context) error { return boom }},
		{Name: "seed", Deps: []string{"migrate"}, Action: func(context.Context) error { atomic.AddInt32(&seedRan, 1); return nil }},
		{Name: "assets", Action: func(context.Context) error { atomic.AddInt32(&assetsRan, 1); return nil }},
		{Name: "deploy", Deps: []string{"seed", "assets"}, Action: func(context.Context) error { atomic.AddInt32(&deployRan, 1); return nil }},
	})
	if err != nil {
		t.Fatalf("NewRunner: %v", err)
	}
	rep, err := r.Run(context.Background(), "deploy")
	if err == nil || !errors.Is(err, boom) {
		t.Fatalf("want Run error wrapping the action error (errors.Is), got %v", err)
	}
	if !strings.Contains(err.Error(), "migrate") {
		t.Fatalf("Run error should name the failed task, got %v", err)
	}
	if rep == nil {
		t.Fatal("Run must return a report even on failure")
	}
	if atomic.LoadInt32(&assetsRan) != 1 {
		t.Fatal("independent branch should still run after an unrelated failure")
	}
	if atomic.LoadInt32(&seedRan) != 0 || atomic.LoadInt32(&deployRan) != 0 {
		t.Fatal("dependents of a failed task must not run")
	}
	want := map[string]Status{"migrate": StatusFailed, "seed": StatusSkipped, "assets": StatusOK, "deploy": StatusSkipped}
	for name, st := range want {
		if rep.Statuses[name] != st {
			t.Fatalf("status[%s] = %q, want %q (full: %v)", name, rep.Statuses[name], st, rep.Statuses)
		}
	}
}

func TestContextCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	fetchStarted := make(chan struct{})
	var reportRan int32
	r, err := NewRunner([]Task{
		{Name: "fetch", Action: func(ctx context.Context) error {
			close(fetchStarted)
			<-ctx.Done()
			return ctx.Err()
		}},
		{Name: "report", Deps: []string{"fetch"}, Action: func(context.Context) error { atomic.AddInt32(&reportRan, 1); return nil }},
	})
	if err != nil {
		t.Fatalf("NewRunner: %v", err)
	}
	go func() {
		<-fetchStarted
		cancel()
	}()
	rep, err := r.Run(ctx, "report")
	if err == nil {
		t.Fatal("Run should fail when the context is canceled mid-flight")
	}
	if atomic.LoadInt32(&reportRan) != 0 {
		t.Fatal("dependent of a canceled task must not run")
	}
	if rep == nil {
		t.Fatal("Run must return a report even when canceled")
	}
	if rep.Statuses["fetch"] != StatusFailed || rep.Statuses["report"] != StatusSkipped {
		t.Fatalf("statuses after cancel = %v, want fetch=failed report=skipped", rep.Statuses)
	}
}

func TestRunnerIsReusable(t *testing.T) {
	var n int32
	r, err := NewRunner([]Task{{Name: "count", Action: func(context.Context) error { atomic.AddInt32(&n, 1); return nil }}})
	if err != nil {
		t.Fatalf("NewRunner: %v", err)
	}
	for i := 0; i < 2; i++ {
		rep, err := r.Run(context.Background(), "count")
		if err != nil || rep.Statuses["count"] != StatusOK {
			t.Fatalf("run %d: err=%v statuses=%v", i, err, rep.Statuses)
		}
	}
	if atomic.LoadInt32(&n) != 2 {
		t.Fatalf("action ran %d times, want 2", n)
	}
}
