package pagewalk

// Acceptance tests for the unified pagination walker.
//
// Every scenario runs against a local httptest server that serves a fixed
// script of pages, one response per request, and records each request it
// receives. Nothing here touches a real network.

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"strings"
	"sync"
	"testing"
)

// page is one scripted response.
type page struct {
	status    int              // 0 means 200
	linkNext  string           // when set, emitted inside the Link header as rel="next"
	linkExtra string           // when set, prepended verbatim to the Link header (other relations)
	items     []map[string]any // body "items"
	cursor    string           // when set, body "next_cursor"
	rawBody   string           // when set, overrides the JSON body entirely
}

type script struct {
	mu    sync.Mutex
	pages []page
	reqs  []string // method-less request URI (path?query) of every request, in order
}

func (s *script) requests() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]string, len(s.reqs))
	copy(out, s.reqs)
	return out
}

func newServer(t *testing.T, pages ...page) (*httptest.Server, *script) {
	t.Helper()
	s := &script{pages: pages}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		defer s.mu.Unlock()
		s.reqs = append(s.reqs, r.URL.RequestURI())
		if len(s.pages) == 0 {
			w.WriteHeader(http.StatusGone)
			fmt.Fprint(w, `{"error":"script exhausted: the walker requested a page the scenario did not expect"}`)
			return
		}
		p := s.pages[0]
		s.pages = s.pages[1:]
		var links []string
		if p.linkExtra != "" {
			links = append(links, p.linkExtra)
		}
		if p.linkNext != "" {
			links = append(links, fmt.Sprintf("<%s>; rel=%q", p.linkNext, "next"))
		}
		if len(links) > 0 {
			w.Header().Set("Link", strings.Join(links, ", "))
		}
		w.Header().Set("Content-Type", "application/json")
		if p.status == 0 {
			p.status = http.StatusOK
		}
		w.WriteHeader(p.status)
		if p.rawBody != "" {
			fmt.Fprint(w, p.rawBody)
			return
		}
		body := map[string]any{"items": p.items}
		if p.items == nil {
			body["items"] = []any{}
		}
		if p.cursor != "" {
			body["next_cursor"] = p.cursor
		}
		json.NewEncoder(w).Encode(body)
	}))
	t.Cleanup(srv.Close)
	return srv, s
}

func item(id string, kv ...string) map[string]any {
	m := map[string]any{"id": id}
	for i := 0; i+1 < len(kv); i += 2 {
		m[kv[i]] = kv[i+1]
	}
	return m
}

// drain walks the iterator to the end and returns everything it yielded.
func drain(it *Iterator) []map[string]any {
	var out []map[string]any
	for it.Next() {
		out = append(out, it.Item())
	}
	return out
}

func ids(items []map[string]any) []string {
	var out []string
	for _, m := range items {
		if s, ok := m["id"].(string); ok {
			out = append(out, s)
		} else {
			out = append(out, "<none>")
		}
	}
	return out
}

func mustNoErr(t *testing.T, it *Iterator) {
	t.Helper()
	if err := it.Err(); err != nil {
		t.Fatalf("Err() = %v, want nil", err)
	}
}

func queryOf(t *testing.T, requestURI string) (string, url.Values) {
	t.Helper()
	u, err := url.Parse(requestURI)
	if err != nil {
		t.Fatalf("recorded request %q does not parse: %v", requestURI, err)
	}
	return u.Path, u.Query()
}

func TestSingleTerminalPageYieldsItemsInOrder(t *testing.T) {
	srv, s := newServer(t, page{items: []map[string]any{
		item("w1", "name", "anvil"), item("w2", "name", "bolt"), item("w3", "name", "clamp"),
	}})
	it := New(srv.Client()).Walk(context.Background(), srv.URL+"/v1/widgets?limit=50")
	got := drain(it)
	mustNoErr(t, it)
	if want := []string{"w1", "w2", "w3"}; !reflect.DeepEqual(ids(got), want) {
		t.Fatalf("ids = %v, want %v", ids(got), want)
	}
	if got[0]["name"] != "anvil" {
		t.Fatalf("item fields not carried through: %v", got[0])
	}
	if reqs := s.requests(); len(reqs) != 1 {
		t.Fatalf("server saw %d requests %v, want exactly 1", len(reqs), reqs)
	}
}

func TestFollowsLinkHeaderAbsoluteThenRelative(t *testing.T) {
	srv, s := newServer(t)
	// Fill the script after the server exists so page 1 can carry an absolute URL.
	s.pages = []page{
		{items: []map[string]any{item("w1"), item("w2")}, linkNext: srv.URL + "/v1/widgets?page=2"},
		{items: []map[string]any{item("w3"), item("w4")}, linkNext: "/v1/widgets?page=3"},
		{items: []map[string]any{item("w5")}},
	}
	it := New(srv.Client()).Walk(context.Background(), srv.URL+"/v1/widgets?limit=2")
	got := drain(it)
	mustNoErr(t, it)
	if want := []string{"w1", "w2", "w3", "w4", "w5"}; !reflect.DeepEqual(ids(got), want) {
		t.Fatalf("ids = %v, want %v", ids(got), want)
	}
	want := []string{"/v1/widgets?limit=2", "/v1/widgets?page=2", "/v1/widgets?page=3"}
	if reqs := s.requests(); !reflect.DeepEqual(reqs, want) {
		t.Fatalf("requests = %v, want %v", reqs, want)
	}
}

func TestOnlyTheNextRelationIsFollowed(t *testing.T) {
	srv, s := newServer(t,
		page{
			items:     []map[string]any{item("w1")},
			linkExtra: `</v1/widgets?page=0>; rel="prev", </v1/widgets>; rel="first"`,
			linkNext:  "/v1/widgets?page=2",
		},
		page{items: []map[string]any{item("w2")}},
	)
	it := New(srv.Client()).Walk(context.Background(), srv.URL+"/v1/widgets?page=1")
	got := drain(it)
	mustNoErr(t, it)
	if want := []string{"w1", "w2"}; !reflect.DeepEqual(ids(got), want) {
		t.Fatalf("ids = %v, want %v", ids(got), want)
	}
	reqs := s.requests()
	if len(reqs) != 2 || reqs[1] != "/v1/widgets?page=2" {
		t.Fatalf("requests = %v, want the rel=\"next\" target second and nothing else", reqs)
	}
}

func TestBodyCursorAddsThenReplacesQueryParam(t *testing.T) {
	srv, s := newServer(t,
		page{items: []map[string]any{item("w1")}, cursor: "c-2"},
		page{items: []map[string]any{item("w2")}, cursor: "c-3"},
		page{items: []map[string]any{item("w3")}},
	)
	it := New(srv.Client()).Walk(context.Background(), srv.URL+"/v1/widgets?limit=2")
	got := drain(it)
	mustNoErr(t, it)
	if want := []string{"w1", "w2", "w3"}; !reflect.DeepEqual(ids(got), want) {
		t.Fatalf("ids = %v, want %v", ids(got), want)
	}
	reqs := s.requests()
	if len(reqs) != 3 {
		t.Fatalf("requests = %v, want 3", reqs)
	}
	path, q := queryOf(t, reqs[1])
	if path != "/v1/widgets" || q.Get("cursor") != "c-2" || q.Get("limit") != "2" {
		t.Fatalf("second request %q: want path /v1/widgets with cursor=c-2 and limit=2 preserved", reqs[1])
	}
	_, q = queryOf(t, reqs[2])
	if got := q["cursor"]; !reflect.DeepEqual(got, []string{"c-3"}) {
		t.Fatalf("third request cursor values = %v, want exactly [c-3] (replaced, not appended)", got)
	}
	if q.Get("limit") != "2" {
		t.Fatalf("third request lost the original limit param: %v", reqs[2])
	}
}

func TestLinkHeaderWinsOverBodyCursor(t *testing.T) {
	srv, s := newServer(t,
		page{items: []map[string]any{item("w1")}, linkNext: "/v1/widgets?via=link", cursor: "c-body"},
		page{items: []map[string]any{item("w2")}},
	)
	it := New(srv.Client()).Walk(context.Background(), srv.URL+"/v1/widgets")
	got := drain(it)
	mustNoErr(t, it)
	if want := []string{"w1", "w2"}; !reflect.DeepEqual(ids(got), want) {
		t.Fatalf("ids = %v, want %v", ids(got), want)
	}
	reqs := s.requests()
	path, q := queryOf(t, reqs[1])
	if path != "/v1/widgets" || q.Get("via") != "link" || q.Get("cursor") != "" {
		t.Fatalf("second request %q: the Link header target must win over next_cursor", reqs[1])
	}
}

func TestStopsOnEmptyPageEvenWithNextCursor(t *testing.T) {
	srv, s := newServer(t,
		page{items: []map[string]any{item("w1"), item("w2")}, cursor: "c-2"},
		page{items: []map[string]any{}, cursor: "c-3", linkNext: "/v1/widgets?cursor=c-3"},
		page{items: []map[string]any{item("never")}},
	)
	it := New(srv.Client()).Walk(context.Background(), srv.URL+"/v1/widgets")
	got := drain(it)
	mustNoErr(t, it)
	if want := []string{"w1", "w2"}; !reflect.DeepEqual(ids(got), want) {
		t.Fatalf("ids = %v, want %v", ids(got), want)
	}
	if reqs := s.requests(); len(reqs) != 2 {
		t.Fatalf("server saw %d requests %v; an empty page must end the walk", len(reqs), reqs)
	}
}

func TestEmptyFirstPageYieldsNothing(t *testing.T) {
	srv, s := newServer(t, page{items: []map[string]any{}})
	it := New(srv.Client()).Walk(context.Background(), srv.URL+"/v1/widgets")
	if got := drain(it); len(got) != 0 {
		t.Fatalf("yielded %v from an empty feed", got)
	}
	mustNoErr(t, it)
	if reqs := s.requests(); len(reqs) != 1 {
		t.Fatalf("server saw %d requests, want 1", len(reqs))
	}
}

func TestMissingItemsKeyEndsTheWalkCleanly(t *testing.T) {
	srv, s := newServer(t, page{rawBody: `{"next_cursor":"c-2"}`})
	it := New(srv.Client()).Walk(context.Background(), srv.URL+"/v1/widgets")
	if got := drain(it); len(got) != 0 {
		t.Fatalf("yielded %v from a page with no items key", got)
	}
	mustNoErr(t, it)
	if reqs := s.requests(); len(reqs) != 1 {
		t.Fatalf("server saw %d requests %v; a missing items key means an empty page", len(reqs), s.requests())
	}
}

func TestBoundaryDuplicatesYieldOnceFirstOccurrenceWins(t *testing.T) {
	srv, _ := newServer(t,
		page{items: []map[string]any{item("w1"), item("w2"), item("w3", "name", "first")}, cursor: "c-2"},
		page{items: []map[string]any{item("w3", "name", "second"), item("w4")}, cursor: "c-3"},
		page{items: []map[string]any{item("w1", "name", "late-repeat"), item("w5")}},
	)
	it := New(srv.Client()).Walk(context.Background(), srv.URL+"/v1/widgets")
	got := drain(it)
	mustNoErr(t, it)
	if want := []string{"w1", "w2", "w3", "w4", "w5"}; !reflect.DeepEqual(ids(got), want) {
		t.Fatalf("ids = %v, want each id exactly once in first-seen order %v", ids(got), want)
	}
	if got[2]["name"] != "first" {
		t.Fatalf("w3 = %v, want the first occurrence kept and the boundary repeat dropped", got[2])
	}
	if _, hasName := got[0]["name"]; hasName {
		t.Fatalf("w1 = %v, want the page-1 copy (no name), not the late repeat", got[0])
	}
}

func TestItemsWithoutStringIDAreNeverDeduped(t *testing.T) {
	srv, _ := newServer(t, page{items: []map[string]any{
		{"note": "no id at all"},
		item("w1"),
		{"note": "no id at all"},
		{"id": float64(7), "note": "numeric id"},
		{"id": float64(7), "note": "numeric id"},
	}})
	it := New(srv.Client()).Walk(context.Background(), srv.URL+"/v1/widgets")
	got := drain(it)
	mustNoErr(t, it)
	if len(got) != 5 {
		t.Fatalf("yielded %d items %v, want all 5 (only string ids participate in dedupe)", len(got), got)
	}
}

func TestServerErrorMidWalkStopsWithStatusInError(t *testing.T) {
	srv, s := newServer(t,
		page{items: []map[string]any{item("w1"), item("w2")}, cursor: "c-2"},
		page{status: http.StatusInternalServerError, rawBody: `{"error":"backend hiccup"}`},
	)
	it := New(srv.Client()).Walk(context.Background(), srv.URL+"/v1/widgets")
	got := drain(it)
	if want := []string{"w1", "w2"}; !reflect.DeepEqual(ids(got), want) {
		t.Fatalf("items yielded before the failure = %v, want %v to stand", ids(got), want)
	}
	err := it.Err()
	if err == nil {
		t.Fatal("Err() = nil after a 500 page, want an error")
	}
	if !strings.Contains(err.Error(), "500") {
		t.Fatalf("Err() = %q, want the HTTP status in the message", err)
	}
	if it.Next() {
		t.Fatal("Next() = true after the iterator already failed")
	}
	if reqs := s.requests(); len(reqs) != 2 {
		t.Fatalf("server saw %d requests %v, want the walk to stop at the failing page", len(reqs), reqs)
	}
}

func TestMalformedJSONSurfacesAsError(t *testing.T) {
	srv, _ := newServer(t, page{rawBody: `{"items": [{"id": "w1"},`})
	it := New(srv.Client()).Walk(context.Background(), srv.URL+"/v1/widgets")
	if it.Next() {
		t.Fatalf("Next() = true on an undecodable page (yielded %v)", it.Item())
	}
	if it.Err() == nil {
		t.Fatal("Err() = nil after an undecodable page, want an error")
	}
}

func TestCancelledContextFailsBeforeAnyRequest(t *testing.T) {
	srv, s := newServer(t, page{items: []map[string]any{item("w1")}})
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	it := New(srv.Client()).Walk(ctx, srv.URL+"/v1/widgets")
	if it.Next() {
		t.Fatal("Next() = true with an already-cancelled context")
	}
	if it.Err() == nil {
		t.Fatal("Err() = nil with an already-cancelled context, want an error")
	}
	if reqs := s.requests(); len(reqs) != 0 {
		t.Fatalf("server saw %v, want no requests once the context is dead", reqs)
	}
}
