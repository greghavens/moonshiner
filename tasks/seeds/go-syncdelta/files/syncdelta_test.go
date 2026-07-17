package syncdelta

// Acceptance tests for the incremental delta-sync client.
//
// Every scenario runs against a local httptest server that serves scripted
// /changes pages in order and records the `since` parameter of every
// request. Checkpoints go through an injectable cursor store, so failure
// and resume behavior is pinned exactly. No live network anywhere.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"testing"
)

// ---------------------------------------------------------------- test doubles

type pageSpec struct {
	status  int              // 0 means 200
	rawBody string           // when set, served verbatim
	changes []map[string]any // body "changes"
	next    string           // body "next_since"
	hasMore bool             // body "has_more"
}

type script struct {
	mu     sync.Mutex
	pages  []pageSpec
	sinces []string // recorded since param per request; "(none)" when absent
	paths  []string
}

func (s *script) arm(pages ...pageSpec) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pages = append(s.pages, pages...)
}

func (s *script) recorded() (sinces, paths []string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]string(nil), s.sinces...), append([]string(nil), s.paths...)
}

func newServer(t *testing.T, pages ...pageSpec) (*httptest.Server, *script) {
	t.Helper()
	s := &script{pages: pages}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		defer s.mu.Unlock()
		s.paths = append(s.paths, r.URL.Path)
		if vals, present := r.URL.Query()["since"]; present {
			s.sinces = append(s.sinces, vals[0])
		} else {
			s.sinces = append(s.sinces, "(none)")
		}
		w.Header().Set("Content-Type", "application/json")
		if len(s.pages) == 0 {
			w.WriteHeader(http.StatusGone)
			fmt.Fprint(w, `{"error":"script exhausted: unexpected extra request"}`)
			return
		}
		p := s.pages[0]
		s.pages = s.pages[1:]
		if p.status == 0 {
			p.status = http.StatusOK
		}
		w.WriteHeader(p.status)
		if p.rawBody != "" {
			fmt.Fprint(w, p.rawBody)
			return
		}
		changes := p.changes
		if changes == nil {
			changes = []map[string]any{}
		}
		json.NewEncoder(w).Encode(map[string]any{
			"changes":    changes,
			"next_since": p.next,
			"has_more":   p.hasMore,
		})
	}))
	t.Cleanup(srv.Close)
	return srv, s
}

func ups(id string, rev int, kv ...string) map[string]any {
	data := map[string]any{}
	for i := 0; i+1 < len(kv); i += 2 {
		data[kv[i]] = kv[i+1]
	}
	return map[string]any{"op": "upsert", "id": id, "rev": rev, "data": data}
}

func del(id string, rev int) map[string]any {
	return map[string]any{"op": "delete", "id": id, "rev": rev}
}

var errSaveBoom = errors.New("checkpoint volume is read-only")
var errLoadBoom = errors.New("checkpoint volume is unreadable")

type fakeStore struct {
	cursor   string
	ok       bool
	saves    []string // every committed save, in order
	attempts int
	failAt   int // 1-based Save attempt to fail (0 = never)
	loadErr  error
}

func (f *fakeStore) Load() (string, bool, error) {
	if f.loadErr != nil {
		return "", false, f.loadErr
	}
	return f.cursor, f.ok, nil
}

func (f *fakeStore) Save(cursor string) error {
	f.attempts++
	if f.failAt != 0 && f.attempts == f.failAt {
		return errSaveBoom
	}
	f.cursor, f.ok = cursor, true
	f.saves = append(f.saves, cursor)
	return nil
}

func mustRecord(t *testing.T, table *Table, id string, rev int, data map[string]string) {
	t.Helper()
	rec, found := table.Get(id)
	if !found {
		t.Fatalf("record %q missing from the table", id)
	}
	want := Record{ID: id, Rev: rev, Data: data}
	if !reflect.DeepEqual(rec, want) {
		t.Fatalf("record %q = %+v, want %+v", id, rec, want)
	}
}

// ---------------------------------------------------------------- sync flows

func TestFreshSyncWalksAllPagesAndCheckpointsEach(t *testing.T) {
	srv, s := newServer(t,
		pageSpec{changes: []map[string]any{
			ups("r1", 1, "name", "anvil", "qty", "4"),
			ups("r2", 1, "name", "bolt"),
		}, next: "cur-1", hasMore: true},
		pageSpec{changes: []map[string]any{
			ups("r3", 1, "name", "clamp"),
			del("r2", 2),
		}, next: "cur-2", hasMore: false},
	)
	store := &fakeStore{}
	table := NewTable()
	stats, err := NewClient(srv.Client(), srv.URL, store).Sync(context.Background(), table)
	if err != nil {
		t.Fatalf("Sync: %v", err)
	}
	if want := (Stats{Pages: 2, Upserts: 3, Deletes: 1, Skipped: 0}); stats != want {
		t.Fatalf("stats = %+v, want %+v", stats, want)
	}
	if table.Len() != 2 {
		t.Fatalf("table.Len() = %d, want 2", table.Len())
	}
	if got := table.IDs(); !reflect.DeepEqual(got, []string{"r1", "r3"}) {
		t.Fatalf("IDs() = %v, want sorted live ids [r1 r3]", got)
	}
	mustRecord(t, table, "r1", 1, map[string]string{"name": "anvil", "qty": "4"})
	if _, found := table.Get("r2"); found {
		t.Fatal("r2 must be gone after its tombstone")
	}
	if !reflect.DeepEqual(store.saves, []string{"cur-1", "cur-2"}) {
		t.Fatalf("checkpoints = %v, want [cur-1 cur-2]", store.saves)
	}
	sinces, paths := s.recorded()
	if !reflect.DeepEqual(sinces, []string{"(none)", "cur-1"}) {
		t.Fatalf("since params = %v, want [(none) cur-1] — no checkpoint means NO since param", sinces)
	}
	for _, p := range paths {
		if p != "/changes" {
			t.Fatalf("unexpected request path %q", p)
		}
	}
}

func TestMidSyncFailureResumesFromLastCheckpointWithoutLoss(t *testing.T) {
	srv, s := newServer(t,
		pageSpec{changes: []map[string]any{
			ups("r1", 1, "name", "anvil"),
			ups("r2", 1, "name", "bolt"),
		}, next: "cur-1", hasMore: true},
		pageSpec{status: http.StatusServiceUnavailable, rawBody: `{"error":"backend draining"}`},
	)
	store := &fakeStore{}
	table := NewTable()
	client := NewClient(srv.Client(), srv.URL, store)

	stats, err := client.Sync(context.Background(), table)
	if err == nil {
		t.Fatal("Sync must fail when a page comes back 503")
	}
	if !strings.Contains(err.Error(), "503") {
		t.Fatalf("err = %q, want the HTTP status in the message", err)
	}
	if stats.Pages != 1 || stats.Upserts != 2 {
		t.Fatalf("stats = %+v, want the fully checkpointed first page counted (Pages 1, Upserts 2)", stats)
	}
	if table.Len() != 2 {
		t.Fatalf("table.Len() = %d, want page-1 progress kept", table.Len())
	}
	if !reflect.DeepEqual(store.saves, []string{"cur-1"}) {
		t.Fatalf("checkpoints = %v, want [cur-1]", store.saves)
	}

	// The feed recovers; the next Sync must pick up at cur-1, not restart.
	s.arm(pageSpec{changes: []map[string]any{
		ups("r3", 1, "name", "clamp"),
	}, next: "cur-2", hasMore: false})
	stats2, err := client.Sync(context.Background(), table)
	if err != nil {
		t.Fatalf("resumed Sync: %v", err)
	}
	if want := (Stats{Pages: 1, Upserts: 1}); stats2 != want {
		t.Fatalf("resumed stats = %+v, want %+v (nothing re-fetched, nothing lost)", stats2, want)
	}
	if got := table.IDs(); !reflect.DeepEqual(got, []string{"r1", "r2", "r3"}) {
		t.Fatalf("IDs() = %v, want [r1 r2 r3]", got)
	}
	sinces, _ := s.recorded()
	if !reflect.DeepEqual(sinces, []string{"(none)", "cur-1", "cur-1"}) {
		t.Fatalf("since params = %v, want [(none) cur-1 cur-1]", sinces)
	}
	if !reflect.DeepEqual(store.saves, []string{"cur-1", "cur-2"}) {
		t.Fatalf("checkpoints = %v, want [cur-1 cur-2]", store.saves)
	}
}

func TestCheckpointFailureMeansRedeliveryAndIdempotentReplay(t *testing.T) {
	srv, s := newServer(t,
		pageSpec{changes: []map[string]any{
			ups("a", 1, "name", "alpha"),
			ups("b", 1, "name", "beta"),
		}, next: "cur-1", hasMore: true},
		pageSpec{changes: []map[string]any{
			ups("c", 2, "name", "gamma"),
			del("b", 3),
		}, next: "cur-2", hasMore: false},
	)
	store := &fakeStore{failAt: 2} // the page-2 checkpoint write fails
	table := NewTable()
	client := NewClient(srv.Client(), srv.URL, store)

	_, err := client.Sync(context.Background(), table)
	if !errors.Is(err, errSaveBoom) {
		t.Fatalf("err = %v, want the store's own error wrapped (errors.Is)", err)
	}
	// Apply-then-checkpoint: page 2 is already in the table even though its
	// checkpoint never committed. That is the at-least-once contract.
	if _, found := table.Get("c"); !found {
		t.Fatal("page-2 upsert must be applied before the checkpoint write")
	}
	if _, found := table.Get("b"); found {
		t.Fatal("page-2 tombstone must be applied before the checkpoint write")
	}
	if !reflect.DeepEqual(store.saves, []string{"cur-1"}) {
		t.Fatalf("checkpoints = %v, want only [cur-1] committed", store.saves)
	}

	// On the next run the feed redelivers page 2 (since=cur-1). Replaying
	// it must change nothing and count as skips.
	s.arm(pageSpec{changes: []map[string]any{
		ups("c", 2, "name", "gamma"),
		del("b", 3),
	}, next: "cur-2", hasMore: false})
	stats2, err := client.Sync(context.Background(), table)
	if err != nil {
		t.Fatalf("replay Sync: %v", err)
	}
	if want := (Stats{Pages: 1, Upserts: 0, Deletes: 0, Skipped: 2}); stats2 != want {
		t.Fatalf("replay stats = %+v, want %+v", stats2, want)
	}
	if got := table.IDs(); !reflect.DeepEqual(got, []string{"a", "c"}) {
		t.Fatalf("IDs() = %v, want [a c] unchanged by the replay", got)
	}
	mustRecord(t, table, "c", 2, map[string]string{"name": "gamma"})
	sinces, _ := s.recorded()
	if !reflect.DeepEqual(sinces, []string{"(none)", "cur-1", "cur-1"}) {
		t.Fatalf("since params = %v, want [(none) cur-1 cur-1]", sinces)
	}
	if !reflect.DeepEqual(store.saves, []string{"cur-1", "cur-2"}) {
		t.Fatalf("checkpoints = %v, want [cur-1 cur-2] after the replay commits", store.saves)
	}
}

func TestUnknownOpAbortsBeforeCheckpointing(t *testing.T) {
	srv, _ := newServer(t,
		pageSpec{changes: []map[string]any{
			ups("r1", 1, "name", "anvil"),
			{"op": "merge", "id": "r9", "rev": 1},
		}, next: "cur-1", hasMore: false},
	)
	store := &fakeStore{}
	table := NewTable()
	_, err := NewClient(srv.Client(), srv.URL, store).Sync(context.Background(), table)
	if err == nil {
		t.Fatal("Sync must fail on an op it does not understand")
	}
	if !strings.Contains(err.Error(), "merge") {
		t.Fatalf("err = %q, want the unknown op named", err)
	}
	if len(store.saves) != 0 {
		t.Fatalf("checkpoints = %v, want none — the page never fully applied", store.saves)
	}
	if _, found := table.Get("r1"); !found {
		t.Fatal("changes before the bad op stay applied (at-least-once, replay will skip them)")
	}
}

func TestLoadFailureAbortsBeforeAnyRequest(t *testing.T) {
	srv, s := newServer(t, pageSpec{next: "cur-1"})
	store := &fakeStore{loadErr: errLoadBoom}
	_, err := NewClient(srv.Client(), srv.URL, store).Sync(context.Background(), NewTable())
	if !errors.Is(err, errLoadBoom) {
		t.Fatalf("err = %v, want the store's load error wrapped", err)
	}
	if sinces, _ := s.recorded(); len(sinces) != 0 {
		t.Fatalf("server saw %v, want no requests when the checkpoint cannot be read", sinces)
	}
}

func TestMalformedPageIsAnError(t *testing.T) {
	srv, _ := newServer(t, pageSpec{rawBody: `{"changes": [{`})
	store := &fakeStore{}
	_, err := NewClient(srv.Client(), srv.URL, store).Sync(context.Background(), NewTable())
	if err == nil {
		t.Fatal("Sync must fail on an undecodable page")
	}
	if len(store.saves) != 0 {
		t.Fatalf("checkpoints = %v, want none", store.saves)
	}
}

// ---------------------------------------------------------------- table semantics

func TestApplyRevGuardsPinTheWholeMatrix(t *testing.T) {
	table := NewTable()
	apply := func(ch map[string]any) bool {
		t.Helper()
		raw, _ := json.Marshal(ch)
		var change Change
		if err := json.Unmarshal(raw, &change); err != nil {
			t.Fatalf("bad fixture %v: %v", ch, err)
		}
		applied, err := table.Apply(change)
		if err != nil {
			t.Fatalf("Apply(%v): %v", ch, err)
		}
		return applied
	}

	if !apply(ups("x", 2, "name", "first", "color", "red")) {
		t.Fatal("a new upsert must apply")
	}
	if apply(ups("x", 1, "name", "stale")) {
		t.Fatal("an older rev must not clobber a newer record")
	}
	if apply(ups("x", 2, "name", "replay")) {
		t.Fatal("an equal-rev upsert is a redelivery and must be skipped")
	}
	mustRecord(t, table, "x", 2, map[string]string{"name": "first", "color": "red"})

	if !apply(ups("x", 3, "name", "second")) {
		t.Fatal("a newer rev must apply")
	}
	mustRecord(t, table, "x", 3, map[string]string{"name": "second"}) // data replaced wholesale

	if apply(del("x", 2)) {
		t.Fatal("a delete older than the live record is stale and must be skipped")
	}
	if _, found := table.Get("x"); !found {
		t.Fatal("x must survive the stale delete")
	}
	if !apply(del("x", 3)) {
		t.Fatal("a delete at the live rev must apply")
	}
	if _, found := table.Get("x"); found {
		t.Fatal("x must be gone after its tombstone")
	}
	if table.Len() != 0 {
		t.Fatalf("Len() = %d, want 0", table.Len())
	}

	if apply(ups("x", 3, "name", "second")) {
		t.Fatal("a redelivered upsert at the tombstone rev must NOT resurrect the record")
	}
	if apply(del("x", 3)) {
		t.Fatal("a redelivered delete is a no-op")
	}
	if _, found := table.Get("x"); found {
		t.Fatal("x must stay deleted through replays")
	}
	if !apply(ups("x", 4, "name", "reborn")) {
		t.Fatal("a genuinely newer upsert after a tombstone recreates the record")
	}
	mustRecord(t, table, "x", 4, map[string]string{"name": "reborn"})

	// A tombstone for a record this table has never seen still counts.
	if !apply(del("y", 1)) {
		t.Fatal("a delete for an unseen id records a tombstone")
	}
	if apply(ups("y", 1, "name", "late")) {
		t.Fatal("an upsert at the tombstone rev must stay dead")
	}
	if !apply(ups("y", 2, "name", "fresh")) {
		t.Fatal("a newer upsert clears the tombstone")
	}
	if got := table.IDs(); !reflect.DeepEqual(got, []string{"x", "y"}) {
		t.Fatalf("IDs() = %v, want [x y]", got)
	}
}

func TestApplyRejectsUnknownOps(t *testing.T) {
	table := NewTable()
	applied, err := table.Apply(Change{Op: "merge", ID: "z", Rev: 1})
	if err == nil {
		t.Fatal("Apply must reject ops it does not understand")
	}
	if applied {
		t.Fatal("a rejected op must not report as applied")
	}
	if !strings.Contains(err.Error(), "merge") {
		t.Fatalf("err = %q, want the op named", err)
	}
}
