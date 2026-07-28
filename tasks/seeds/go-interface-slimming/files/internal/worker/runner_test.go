package worker

import (
	"context"
	"errors"
	"fmt"
	"sync/atomic"
	"testing"

	"example.com/go-interface-slimming/internal/dispatch"
)

func readyRuns(ids ...string) []dispatch.Run {
	runs := make([]dispatch.Run, 0, len(ids))
	for _, id := range ids {
		runs = append(runs, dispatch.Run{ID: id, Tenant: "north", State: "ready"})
	}
	return runs
}

func TestRunnerBoundsParallelismAndProcessesEveryRun(t *testing.T) {
	runs := readyRuns("r-0", "r-1", "r-2", "r-3", "r-4", "r-5", "r-6", "r-7")
	entered := make(chan string, len(runs))
	release := make(chan struct{})
	var active atomic.Int32
	var maximum atomic.Int32

	fake := &FakeDispatchQueue{
		ListReadyFunc: func(context.Context, int) ([]dispatch.Run, error) {
			return runs, nil
		},
		MarkDispatchedFunc: func(ctx context.Context, id string) error {
			now := active.Add(1)
			defer active.Add(-1)
			for {
				seen := maximum.Load()
				if now <= seen || maximum.CompareAndSwap(seen, now) {
					break
				}
			}
			entered <- id
			select {
			case <-release:
				return nil
			case <-ctx.Done():
				return ctx.Err()
			}
		},
	}

	result := make(chan error, 1)
	go func() {
		result <- New(fake, 3).Run(context.Background(), len(runs))
	}()

	for i := 0; i < 3; i++ {
		<-entered
	}
	select {
	case extra := <-entered:
		close(release)
		t.Fatalf("parallelism exceeded 3; extra run %q entered", extra)
	default:
		close(release)
	}

	if err := <-result; err != nil {
		t.Fatalf("Run returned error: %v", err)
	}
	if got := maximum.Load(); got != 3 {
		t.Fatalf("maximum parallel marks = %d, want 3", got)
	}
	if got := len(fake.MarkDispatchedCalls); got != len(runs) {
		t.Fatalf("recorded mark calls = %d, want %d", got, len(runs))
	}
}

func TestRunnerReturnsListErrorUnchanged(t *testing.T) {
	listErr := errors.New("queue snapshot unavailable")
	fake := &FakeDispatchQueue{
		ListReadyFunc: func(context.Context, int) ([]dispatch.Run, error) {
			return nil, listErr
		},
	}
	err := New(fake, 2).Run(context.Background(), 20)
	if err != listErr {
		t.Fatalf("Run error = %v, want identical list error %v", err, listErr)
	}
	if len(fake.MarkDispatchedCalls) != 0 {
		t.Fatalf("mark calls = %d after list failure, want 0", len(fake.MarkDispatchedCalls))
	}
}

func TestRunnerReturnsFirstMarkErrorAndCancelsSiblings(t *testing.T) {
	markErr := errors.New("lease lost")
	entered := make(chan string, 3)
	releaseFailure := make(chan struct{})
	cancelled := make(chan string, 2)

	fake := &FakeDispatchQueue{
		ListReadyFunc: func(context.Context, int) ([]dispatch.Run, error) {
			return readyRuns("bad", "peer-1", "peer-2"), nil
		},
		MarkDispatchedFunc: func(ctx context.Context, id string) error {
			entered <- id
			if id == "bad" {
				<-releaseFailure
				return markErr
			}
			<-ctx.Done()
			if !errors.Is(ctx.Err(), context.Canceled) {
				return fmt.Errorf("sibling %s context error: %v", id, ctx.Err())
			}
			cancelled <- id
			return ctx.Err()
		},
	}

	result := make(chan error, 1)
	go func() {
		result <- New(fake, 3).Run(context.Background(), 3)
	}()
	for i := 0; i < 3; i++ {
		<-entered
	}
	close(releaseFailure)

	if err := <-result; err != markErr {
		t.Fatalf("Run error = %v, want identical first mark error %v", err, markErr)
	}
	seen := map[string]bool{}
	for i := 0; i < 2; i++ {
		seen[<-cancelled] = true
	}
	if !seen["peer-1"] || !seen["peer-2"] {
		t.Fatalf("cancelled siblings = %v, want peer-1 and peer-2", seen)
	}
	if got := len(fake.MarkDispatchedCalls); got != 3 {
		t.Fatalf("recorded mark calls = %d, want 3", got)
	}
}

func TestRunnerPreservesParentCancellation(t *testing.T) {
	parent, cancel := context.WithCancel(context.Background())
	cancel()
	fake := &FakeDispatchQueue{
		ListReadyFunc: func(ctx context.Context, _ int) ([]dispatch.Run, error) {
			return nil, ctx.Err()
		},
	}
	if err := New(fake, 1).Run(parent, 1); err != context.Canceled {
		t.Fatalf("Run error = %v, want context.Canceled identity", err)
	}
}
