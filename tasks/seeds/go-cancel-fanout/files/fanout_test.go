package fanout

import (
	"context"
	"errors"
	"testing"
	"time"
)

// within fails the test when fn does not finish inside d.
func within(t *testing.T, d time.Duration, what string, fn func()) {
	t.Helper()
	done := make(chan struct{})
	go func() { fn(); close(done) }()
	select {
	case <-done:
	case <-time.After(d):
		t.Fatal(what)
	}
}

// hangUntilCancelled models a slow replica: it reports that it started,
// then blocks until its context is cancelled.
func hangUntilCancelled(started chan<- struct{}) Backend {
	return func(ctx context.Context, query string) (string, error) {
		started <- struct{}{}
		<-ctx.Done()
		return "", ctx.Err()
	}
}

func TestEarlySuccessReleasesBackends(t *testing.T) {
	started := make(chan struct{}, 2)
	backends := []Backend{
		hangUntilCancelled(started),
		func(ctx context.Context, query string) (string, error) {
			return "doc-fast", nil
		},
		hangUntilCancelled(started),
	}
	doc, wait, err := Search(context.Background(), backends, "invoices")
	if err != nil {
		t.Fatalf("Search returned error %v, want first hit", err)
	}
	if doc != "doc-fast" {
		t.Fatalf("doc = %q, want %q", doc, "doc-fast")
	}
	within(t, 2*time.Second, "backend goroutines still running after the first result was returned", wait)
}

func TestAllFailuresAreAggregated(t *testing.T) {
	errIdx := errors.New("index corrupt")
	errNet := errors.New("replica unreachable")
	errBusy := errors.New("replica overloaded")
	mk := func(e error) Backend {
		return func(ctx context.Context, query string) (string, error) { return "", e }
	}
	doc, wait, err := Search(context.Background(), []Backend{mk(errIdx), mk(errNet), mk(errBusy)}, "invoices")
	if doc != "" {
		t.Fatalf("doc = %q, want empty on total failure", doc)
	}
	if err == nil {
		t.Fatal("Search reported success although every backend failed")
	}
	for _, want := range []error{errIdx, errNet, errBusy} {
		if !errors.Is(err, want) {
			t.Fatalf("aggregated error %v is missing %v", err, want)
		}
	}
	within(t, 2*time.Second, "backend goroutines still running after total failure", wait)
}

func TestSuccessAfterFailuresWins(t *testing.T) {
	gate := make(chan struct{})
	backends := []Backend{
		func(ctx context.Context, query string) (string, error) {
			close(gate)
			return "", errors.New("replica cold")
		},
		func(ctx context.Context, query string) (string, error) {
			<-gate
			return "doc-late", nil
		},
	}
	doc, wait, err := Search(context.Background(), backends, "invoices")
	if err != nil {
		t.Fatalf("Search returned error %v, want the late hit", err)
	}
	if doc != "doc-late" {
		t.Fatalf("doc = %q, want %q", doc, "doc-late")
	}
	within(t, 2*time.Second, "backend goroutines still running after a late hit", wait)
}

func TestCallerCancellationReleasesBackends(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	started := make(chan struct{}, 3)
	backends := []Backend{
		hangUntilCancelled(started),
		hangUntilCancelled(started),
		hangUntilCancelled(started),
	}
	type reply struct {
		doc  string
		wait func()
		err  error
	}
	got := make(chan reply, 1)
	go func() {
		doc, wait, err := Search(ctx, backends, "invoices")
		got <- reply{doc, wait, err}
	}()
	for i := 0; i < len(backends); i++ {
		select {
		case <-started:
		case <-time.After(2 * time.Second):
			t.Fatalf("backend %d never started", i)
		}
	}
	cancel()
	var r reply
	select {
	case r = <-got:
	case <-time.After(2 * time.Second):
		t.Fatal("Search did not return after the caller cancelled")
	}
	if !errors.Is(r.err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", r.err)
	}
	if r.doc != "" {
		t.Fatalf("doc = %q, want empty after cancellation", r.doc)
	}
	within(t, 2*time.Second, "backend goroutines still running after caller cancellation", r.wait)
}

func TestCollectOwnsAndClosesItsChannel(t *testing.T) {
	errDown := errors.New("replica down")
	backends := []Backend{
		func(ctx context.Context, query string) (string, error) { return "doc-a", nil },
		func(ctx context.Context, query string) (string, error) { return "", errDown },
		func(ctx context.Context, query string) (string, error) { return "doc-b", nil },
	}
	var outcomes []Outcome
	within(t, 2*time.Second, "Collect never closed its output channel", func() {
		for o := range Collect(context.Background(), backends, "invoices") {
			outcomes = append(outcomes, o)
		}
	})
	if len(outcomes) != 3 {
		t.Fatalf("received %d outcomes, want 3", len(outcomes))
	}
	docs := map[string]bool{}
	sawErr := false
	for _, o := range outcomes {
		if o.Err != nil {
			if !errors.Is(o.Err, errDown) {
				t.Fatalf("unexpected outcome error %v", o.Err)
			}
			sawErr = true
			continue
		}
		docs[o.Doc] = true
	}
	if !sawErr || !docs["doc-a"] || !docs["doc-b"] {
		t.Fatalf("outcomes incomplete: %+v", outcomes)
	}
}
