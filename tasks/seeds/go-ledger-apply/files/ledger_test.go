package ledger

import (
	"testing"
	"time"
)

func applyBounded(t *testing.T, l *Ledger, txns []Txn) (int, error) {
	t.Helper()
	type result struct {
		n   int
		err error
	}
	ch := make(chan result, 1)
	go func() {
		n, err := l.ApplyBatch(txns)
		ch <- result{n, err}
	}()
	select {
	case r := <-ch:
		return r.n, r.err
	case <-time.After(2 * time.Second):
		t.Fatal("ApplyBatch did not return in time")
		return 0, nil
	}
}

func TestSingleTransfer(t *testing.T) {
	l := New(map[string]int64{"ops": 500_00, "dev": 100_00})
	n, err := applyBounded(t, l, []Txn{{From: "ops", To: "dev", Amount: 250_00}})
	if err != nil || n != 1 {
		t.Fatalf("ApplyBatch = (%d, %v), want (1, nil)", n, err)
	}
	if got := l.Balance("ops"); got != 250_00 {
		t.Fatalf("ops balance = %d, want 25000", got)
	}
	if got := l.Balance("dev"); got != 350_00 {
		t.Fatalf("dev balance = %d, want 35000", got)
	}
}

func TestBatchOfTransfers(t *testing.T) {
	l := New(map[string]int64{"ops": 1000_00, "dev": 0, "qa": 0})
	batch := []Txn{
		{From: "ops", To: "dev", Amount: 300_00},
		{From: "ops", To: "qa", Amount: 200_00},
		{From: "dev", To: "qa", Amount: 50_00},
	}
	n, err := applyBounded(t, l, batch)
	if err != nil || n != 3 {
		t.Fatalf("ApplyBatch = (%d, %v), want (3, nil)", n, err)
	}
	if got := l.Balance("ops"); got != 500_00 {
		t.Fatalf("ops balance = %d, want 50000", got)
	}
	if got := l.Balance("dev"); got != 250_00 {
		t.Fatalf("dev balance = %d, want 25000", got)
	}
	if got := l.Balance("qa"); got != 250_00 {
		t.Fatalf("qa balance = %d, want 25000", got)
	}
}

func TestBatchStopsAtBadTransaction(t *testing.T) {
	l := New(map[string]int64{"ops": 100_00, "dev": 0})
	batch := []Txn{
		{From: "ops", To: "dev", Amount: 80_00},
		{From: "ops", To: "dev", Amount: 80_00}, // overdraws
		{From: "dev", To: "ops", Amount: 10_00}, // must not run
	}
	n, err := applyBounded(t, l, batch)
	if err == nil {
		t.Fatal("expected an error for the overdrawing transaction")
	}
	if n != 1 {
		t.Fatalf("applied = %d, want 1", n)
	}
	if got := l.Balance("dev"); got != 80_00 {
		t.Fatalf("dev balance = %d, want 8000 (later txns must not run)", got)
	}
}

func TestReadsWhileBatchesRun(t *testing.T) {
	l := New(map[string]int64{"a": 10_000_00, "b": 0})
	stop := make(chan struct{})
	go func() {
		for {
			select {
			case <-stop:
				return
			default:
				l.Balance("a")
				l.Balance("b")
			}
		}
	}()
	for i := 0; i < 20; i++ {
		if _, err := applyBounded(t, l, []Txn{
			{From: "a", To: "b", Amount: 1_00},
			{From: "b", To: "a", Amount: 1_00},
		}); err != nil {
			close(stop)
			t.Fatalf("batch %d failed: %v", i, err)
		}
	}
	close(stop)
	if got := l.Balance("a"); got != 10_000_00 {
		t.Fatalf("a balance = %d, want 1000000", got)
	}
}
