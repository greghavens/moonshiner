package stockroom_test

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	stockroom "go-stockroom"
)

// ---------- harness ----------

type fakeClock struct{ now time.Time }

func (c *fakeClock) Now() time.Time          { return c.now }
func (c *fakeClock) advance(d time.Duration) { c.now = c.now.Add(d) }

var t0 = time.Date(2026, 1, 5, 2, 0, 0, 0, time.UTC)

func newService() (*stockroom.Service, *fakeClock) {
	clock := &fakeClock{now: t0}
	svc := stockroom.New(stockroom.Config{
		RetryDelay:     15 * time.Minute,
		ReconcileEvery: 24 * time.Hour,
	}, clock)
	return svc, clock
}

// do sends one JSON request through the handler and decodes the reply.
func do(t *testing.T, h http.Handler, method, path string, body any, out any) int {
	t.Helper()
	var rd io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatalf("marshal body: %v", err)
		}
		rd = bytes.NewReader(raw)
	}
	req := httptest.NewRequest(method, path, rd)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if out != nil {
		if err := json.Unmarshal(rec.Body.Bytes(), out); err != nil {
			t.Fatalf("%s %s: cannot decode %q: %v", method, path, rec.Body.String(), err)
		}
	}
	return rec.Code
}

func mustStatus(t *testing.T, got, want int, what string) {
	t.Helper()
	if got != want {
		t.Fatalf("%s: status = %d, want %d", what, got, want)
	}
}

type row struct {
	SKU string `json:"sku"`
	Qty int    `json:"qty"`
}

type rowsResponse struct {
	Rows []row `json:"rows"`
}

type ledgerEntry struct {
	Warehouse  string    `json:"warehouse"`
	RunAt      time.Time `json:"run_at"`
	Checked    int       `json:"checked"`
	Mismatched int       `json:"mismatched"`
}

type ledgerResponse struct {
	Entries []ledgerEntry `json:"entries"`
}

func setupWarehouse(t *testing.T, h http.Handler) {
	t.Helper()
	mustStatus(t, do(t, h, "POST", "/warehouses", map[string]string{"id": "main"}, nil),
		http.StatusCreated, "create warehouse")
	for _, m := range []row{{"bravo", 30}, {"alpha", 5}, {"zulu", 50}, {"delta", 10}} {
		mustStatus(t, do(t, h, "POST", "/warehouses/main/restock",
			map[string]any{"sku": m.SKU, "qty": m.Qty}, nil),
			http.StatusOK, "restock "+m.SKU)
	}
}

func stockRows(t *testing.T, h http.Handler) []row {
	t.Helper()
	var resp rowsResponse
	mustStatus(t, do(t, h, "GET", "/warehouses/main/stock", nil, &resp),
		http.StatusOK, "list stock")
	return resp.Rows
}

func wantRows(t *testing.T, got, want []row, what string) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("%s: got %d rows (%v), want %d (%v)", what, len(got), got, len(want), want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("%s: row %d = %+v, want %+v (full: %v)", what, i, got[i], want[i], got)
		}
	}
}

// ---------- existing behavior ----------

func TestStockFlow(t *testing.T) {
	svc, _ := newService()
	h := svc.Handler()
	setupWarehouse(t, h)

	wantRows(t, stockRows(t, h), []row{{"alpha", 5}, {"bravo", 30}, {"delta", 10}, {"zulu", 50}},
		"stock is SKU-ordered")

	var picked row
	mustStatus(t, do(t, h, "POST", "/warehouses/main/pick",
		map[string]any{"sku": "delta", "qty": 4}, &picked), http.StatusOK, "pick delta")
	if picked != (row{"delta", 6}) {
		t.Fatalf("pick reply = %+v, want {delta 6}", picked)
	}

	mustStatus(t, do(t, h, "POST", "/warehouses/main/pick",
		map[string]any{"sku": "ghost", "qty": 1}, nil), http.StatusNotFound, "pick unknown sku")
	mustStatus(t, do(t, h, "POST", "/warehouses/main/pick",
		map[string]any{"sku": "alpha", "qty": 6}, nil), http.StatusConflict, "overdraw pick")
	mustStatus(t, do(t, h, "POST", "/warehouses/main/restock",
		map[string]any{"sku": "alpha", "qty": 0}, nil), http.StatusBadRequest, "zero qty")
	mustStatus(t, do(t, h, "GET", "/warehouses/ghost/stock", nil, nil),
		http.StatusNotFound, "unknown warehouse")
	mustStatus(t, do(t, h, "POST", "/warehouses", map[string]string{"id": "main"}, nil),
		http.StatusConflict, "duplicate warehouse")

	var moves struct {
		Movements []struct {
			Seq  int    `json:"seq"`
			SKU  string `json:"sku"`
			Kind string `json:"kind"`
			Qty  int    `json:"qty"`
		} `json:"movements"`
	}
	mustStatus(t, do(t, h, "GET", "/warehouses/main/movements", nil, &moves),
		http.StatusOK, "list movements")
	if len(moves.Movements) != 5 {
		t.Fatalf("movement count = %d, want 5 (4 restocks + 1 pick)", len(moves.Movements))
	}
	for i, m := range moves.Movements {
		if m.Seq != i+1 {
			t.Fatalf("movement %d has seq %d, want %d", i, m.Seq, i+1)
		}
	}
	last := moves.Movements[4]
	if last.SKU != "delta" || last.Kind != "pick" || last.Qty != 4 {
		t.Fatalf("last movement = %+v, want delta/pick/4", last)
	}
}

func TestAvailabilityReportRanking(t *testing.T) {
	svc, _ := newService()
	h := svc.Handler()
	setupWarehouse(t, h)
	mustStatus(t, do(t, h, "POST", "/warehouses/main/restock",
		map[string]any{"sku": "echo", "qty": 10}, nil), http.StatusOK, "restock echo")

	var report struct {
		Warehouse string `json:"warehouse"`
		Rows      []row  `json:"rows"`
	}
	mustStatus(t, do(t, h, "GET", "/warehouses/main/report", nil, &report),
		http.StatusOK, "full report")
	wantRows(t, report.Rows,
		[]row{{"zulu", 50}, {"bravo", 30}, {"delta", 10}, {"echo", 10}, {"alpha", 5}},
		"report ranks by qty desc, SKU tiebreak")

	mustStatus(t, do(t, h, "GET", "/warehouses/main/report?top=2", nil, &report),
		http.StatusOK, "top-2 report")
	wantRows(t, report.Rows, []row{{"zulu", 50}, {"bravo", 30}}, "top=2 limits the report")

	mustStatus(t, do(t, h, "GET", "/warehouses/main/report?top=0", nil, nil),
		http.StatusBadRequest, "top must be positive")
}

func TestReconcileRunsAndRetriesWhenCounting(t *testing.T) {
	svc, clock := newService()
	h := svc.Handler()
	setupWarehouse(t, h)
	svc.ScheduleReconciliation()

	mustStatus(t, do(t, h, "POST", "/warehouses/main/lock", nil, nil),
		http.StatusOK, "start counting session")
	mustStatus(t, do(t, h, "POST", "/warehouses/main/lock", nil, nil),
		http.StatusConflict, "double lock")

	if ran := svc.Scheduler().Tick(); len(ran) != 1 || ran[0] != "reconcile:main" {
		t.Fatalf("first tick ran %v, want [reconcile:main]", ran)
	}
	var ledger ledgerResponse
	mustStatus(t, do(t, h, "GET", "/warehouses/main/ledger", nil, &ledger),
		http.StatusOK, "ledger during count")
	if len(ledger.Entries) != 0 {
		t.Fatalf("reconciliation wrote %d entries while counting, want 0", len(ledger.Entries))
	}

	mustStatus(t, do(t, h, "DELETE", "/warehouses/main/lock", nil, nil),
		http.StatusOK, "end counting session")
	mustStatus(t, do(t, h, "DELETE", "/warehouses/main/lock", nil, nil),
		http.StatusConflict, "unlock when not locked")

	clock.advance(5 * time.Minute)
	if ran := svc.Scheduler().Tick(); len(ran) != 0 {
		t.Fatalf("tick before retry delay ran %v, want nothing", ran)
	}
	clock.advance(10 * time.Minute) // now t0+15m, the retry is due
	if ran := svc.Scheduler().Tick(); len(ran) != 1 {
		t.Fatalf("retry tick ran %v, want the reconcile job", ran)
	}
	mustStatus(t, do(t, h, "GET", "/warehouses/main/ledger", nil, &ledger),
		http.StatusOK, "ledger after retry")
	if len(ledger.Entries) != 1 {
		t.Fatalf("ledger has %d entries after retry, want 1", len(ledger.Entries))
	}
	got := ledger.Entries[0]
	if !got.RunAt.Equal(t0.Add(15 * time.Minute)) {
		t.Fatalf("retry ran at %v, want %v", got.RunAt, t0.Add(15*time.Minute))
	}
	if got.Checked != 4 || got.Mismatched != 0 {
		t.Fatalf("ledger entry = checked %d / mismatched %d, want 4 / 0",
			got.Checked, got.Mismatched)
	}
}

// ---------- regressions under investigation ----------

// Serving the ops dashboard is a read; it must not reorder the stored rows.
func TestStockOrderSurvivesReportQueries(t *testing.T) {
	svc, _ := newService()
	h := svc.Handler()
	setupWarehouse(t, h)

	mustStatus(t, do(t, h, "GET", "/warehouses/main/report", nil, nil),
		http.StatusOK, "availability report")

	wantRows(t, stockRows(t, h), []row{{"alpha", 5}, {"bravo", 30}, {"delta", 10}, {"zulu", 50}},
		"stock listing after a report query")
}

// A restock after a dashboard query must top up the existing row, not
// grow the row set — and the nightly reconciliation must still find the
// books clean, since every movement went through the audit trail.
func TestNoPhantomRowsAfterReport(t *testing.T) {
	svc, _ := newService()
	h := svc.Handler()
	setupWarehouse(t, h)
	svc.ScheduleReconciliation()

	mustStatus(t, do(t, h, "GET", "/warehouses/main/report?top=1", nil, nil),
		http.StatusOK, "availability report")
	mustStatus(t, do(t, h, "POST", "/warehouses/main/restock",
		map[string]any{"sku": "alpha", "qty": 1}, nil), http.StatusOK, "restock alpha again")

	rows := stockRows(t, h)
	if len(rows) != 4 {
		t.Fatalf("stock has %d rows after restocking an existing SKU, want 4: %v", len(rows), rows)
	}
	total := 0
	for _, r := range rows {
		if r.SKU == "alpha" {
			total += r.Qty
		}
	}
	if total != 6 {
		t.Fatalf("alpha on hand = %d, want 6", total)
	}

	svc.Scheduler().Tick()
	var ledger ledgerResponse
	mustStatus(t, do(t, h, "GET", "/warehouses/main/ledger", nil, &ledger),
		http.StatusOK, "ledger after reconcile")
	if len(ledger.Entries) != 1 {
		t.Fatalf("ledger has %d entries, want 1", len(ledger.Entries))
	}
	if e := ledger.Entries[0]; e.Checked != 4 || e.Mismatched != 0 {
		t.Fatalf("reconciliation found checked %d / mismatched %d, want 4 / 0 — "+
			"all movements were logged", e.Checked, e.Mismatched)
	}
}

// A reconciliation that finds the warehouse mid-count is retried later —
// but it must end up queued exactly once.
func TestBusyRetryQueuedOnce(t *testing.T) {
	svc, _ := newService()
	h := svc.Handler()
	setupWarehouse(t, h)
	svc.ScheduleReconciliation()

	mustStatus(t, do(t, h, "POST", "/warehouses/main/lock", nil, nil),
		http.StatusOK, "start counting session")
	svc.Scheduler().Tick()

	pending := svc.Scheduler().Pending()
	if len(pending) != 1 {
		t.Fatalf("after a busy run the queue holds %d entries, want 1: %v", len(pending), pending)
	}
	want := "reconcile:main@" + t0.Add(15*time.Minute).UTC().Format(time.RFC3339)
	if pending[0] != want {
		t.Fatalf("pending entry = %q, want the retry %q", pending[0], want)
	}
}

// Three days of the run loop, one counting session on the first night:
// the ledger must show exactly one reconciliation per night.
func TestOneLedgerEntryPerNight(t *testing.T) {
	svc, clock := newService()
	h := svc.Handler()
	setupWarehouse(t, h)
	svc.ScheduleReconciliation()

	mustStatus(t, do(t, h, "POST", "/warehouses/main/lock", nil, nil),
		http.StatusOK, "start counting session")
	svc.Scheduler().Tick() // busy: floor team is counting
	mustStatus(t, do(t, h, "DELETE", "/warehouses/main/lock", nil, nil),
		http.StatusOK, "end counting session")

	// The production loop ticks every 5 minutes; simulate ~3 days of it.
	for i := 0; i < 840; i++ {
		clock.advance(5 * time.Minute)
		svc.Scheduler().Tick()
	}

	var ledger ledgerResponse
	mustStatus(t, do(t, h, "GET", "/warehouses/main/ledger", nil, &ledger),
		http.StatusOK, "ledger after three days")
	if len(ledger.Entries) != 3 {
		times := make([]string, len(ledger.Entries))
		for i, e := range ledger.Entries {
			times[i] = e.RunAt.UTC().Format(time.RFC3339)
		}
		t.Fatalf("ledger has %d entries over three nights, want 3: %v", len(ledger.Entries), times)
	}
	for i := 1; i < len(ledger.Entries); i++ {
		gap := ledger.Entries[i].RunAt.Sub(ledger.Entries[i-1].RunAt)
		if gap < 23*time.Hour {
			t.Fatalf("entries %d and %d are only %v apart — reconciliation ran twice in one night",
				i-1, i, gap)
		}
	}
}
