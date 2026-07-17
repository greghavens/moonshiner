package consumer

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestDrainsQueueUntilClosed(t *testing.T) {
	jobs := make(chan Job, 8)
	for i := 1; i <= 5; i++ {
		jobs <- Job{ID: i, Payload: "row"}
	}
	close(jobs)
	var seen []int
	n, err := Run(context.Background(), jobs, func(j Job) { seen = append(seen, j.ID) })
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if n != 5 || len(seen) != 5 {
		t.Fatalf("processed %d jobs (%v), want 5", n, seen)
	}
	for i, id := range seen {
		if id != i+1 {
			t.Fatalf("jobs out of order: %v", seen)
		}
	}
}

func TestEmptyClosedQueue(t *testing.T) {
	jobs := make(chan Job)
	close(jobs)
	n, err := Run(context.Background(), jobs, func(Job) {})
	if n != 0 || err != nil {
		t.Fatalf("Run = (%d, %v), want (0, nil)", n, err)
	}
}

func TestShutdownWhileQueueIsBusy(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	jobs := make(chan Job)
	stopProducer := make(chan struct{})
	defer close(stopProducer)
	go func() { // a tenant with a deep backlog: the queue never goes quiet
		id := 0
		for {
			select {
			case jobs <- Job{ID: id, Payload: "row"}:
				id++
			case <-stopProducer:
				return
			}
		}
	}()

	handled := make(chan Job, 4096)
	type result struct {
		n   int
		err error
	}
	done := make(chan result, 1)
	go func() {
		n, err := Run(ctx, jobs, func(j Job) { handled <- j })
		done <- result{n, err}
	}()

	for i := 0; i < 3; i++ { // consumer is demonstrably up and working
		<-handled
	}
	cancel()

	select {
	case r := <-done:
		if !errors.Is(r.err, context.Canceled) {
			t.Fatalf("Run returned err = %v, want context.Canceled", r.err)
		}
		if r.n < 3 {
			t.Fatalf("Run reported %d processed jobs, want at least 3", r.n)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("shutdown requested but Run kept consuming the queue")
	}
}
