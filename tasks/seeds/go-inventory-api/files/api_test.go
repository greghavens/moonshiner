package inventory

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// Compile-time pins for the storage seam the server must be built on.
var (
	_ Store = (*MemStore)(nil)
	_ Store = failStore{}
)

func newSrv(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(NewServer(NewMemStore()))
	t.Cleanup(srv.Close)
	return srv
}

func doReq(t *testing.T, method, url string, hdr map[string]string, body string) (*http.Response, []byte) {
	t.Helper()
	resp, raw, err := doRaw(method, url, hdr, body)
	if err != nil {
		t.Fatalf("%s %s: %v", method, url, err)
	}
	return resp, raw
}

// doRaw is the goroutine-safe variant used by the concurrency tests.
func doRaw(method, url string, hdr map[string]string, body string) (*http.Response, []byte, error) {
	var rdr io.Reader
	if body != "" {
		rdr = strings.NewReader(body)
	}
	req, err := http.NewRequest(method, url, rdr)
	if err != nil {
		return nil, nil, err
	}
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, nil, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, nil, err
	}
	return resp, raw, nil
}

func asJSON(t *testing.T, raw []byte) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("body is not a JSON object: %v (%q)", err, raw)
	}
	return m
}

func wantErrJSON(t *testing.T, resp *http.Response, raw []byte, wantStatus int) map[string]any {
	t.Helper()
	if resp.StatusCode != wantStatus {
		t.Fatalf("status = %d, want %d (body %q)", resp.StatusCode, wantStatus, raw)
	}
	if ct := resp.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		t.Fatalf("error responses must be application/json, got %q", ct)
	}
	m := asJSON(t, raw)
	if msg, _ := m["error"].(string); msg == "" {
		t.Fatalf(`error body must carry a non-empty {"error":...}, got %q`, raw)
	}
	return m
}

func itemBody(sku, name string, qty, cents int) string {
	return fmt.Sprintf(`{"sku":%q,"name":%q,"quantity":%d,"price_cents":%d}`, sku, name, qty, cents)
}

func TestCreateAndGetRoundTrip(t *testing.T) {
	srv := newSrv(t)

	resp, raw := doReq(t, "POST", srv.URL+"/items", nil, itemBody("WID-100", "Widget, small", 5, 1999))
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("create status = %d, want 201 (body %q)", resp.StatusCode, raw)
	}
	if ct := resp.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		t.Fatalf("create Content-Type = %q, want application/json", ct)
	}
	if loc := resp.Header.Get("Location"); loc != "/items/WID-100" {
		t.Fatalf("create Location = %q, want /items/WID-100", loc)
	}
	if et := resp.Header.Get("ETag"); et != `"1"` {
		t.Fatalf("create ETag = %q, want %q (versions start at 1)", et, `"1"`)
	}
	m := asJSON(t, raw)
	if m["sku"] != "WID-100" || m["name"] != "Widget, small" || m["quantity"] != float64(5) || m["price_cents"] != float64(1999) {
		t.Fatalf("create echoed %v", m)
	}

	resp, raw = doReq(t, "GET", srv.URL+"/items/WID-100", nil, "")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("get status = %d, want 200", resp.StatusCode)
	}
	if et := resp.Header.Get("ETag"); et != `"1"` {
		t.Fatalf("get ETag = %q, want %q", et, `"1"`)
	}
	m = asJSON(t, raw)
	if m["sku"] != "WID-100" || m["quantity"] != float64(5) {
		t.Fatalf("get body %v", m)
	}
}

func TestItemJSONHasNoVersionField(t *testing.T) {
	srv := newSrv(t)
	doReq(t, "POST", srv.URL+"/items", nil, itemBody("VER-1", "v", 1, 1))
	_, raw := doReq(t, "GET", srv.URL+"/items/VER-1", nil, "")
	m := asJSON(t, raw)
	if len(m) != 4 {
		t.Fatalf("item JSON must have exactly sku/name/quantity/price_cents, got %v", m)
	}
	if _, ok := m["version"]; ok {
		t.Fatalf("the version travels in the ETag header, not the body: %v", m)
	}
}

func TestCreateValidation(t *testing.T) {
	srv := newSrv(t)
	cases := []struct {
		name string
		body string
	}{
		{"malformed json", `{nope`},
		{"missing sku", `{"name":"x","quantity":1,"price_cents":1}`},
		{"lowercase sku", itemBody("wid-1", "x", 1, 1)},
		{"sku too short", itemBody("AB", "x", 1, 1)},
		{"sku too long", itemBody(strings.Repeat("A", 33), "x", 1, 1)},
		{"sku leading hyphen", itemBody("-AB1", "x", 1, 1)},
		{"sku inner space", itemBody("AB 1", "x", 1, 1)},
		{"blank name", itemBody("OK-1", "   ", 1, 1)},
		{"negative quantity", itemBody("OK-1", "x", -1, 1)},
		{"negative price", itemBody("OK-1", "x", 1, -20)},
		{"unknown field", `{"sku":"OK-1","name":"x","quantity":1,"price_cents":1,"color":"red"}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			resp, raw := doReq(t, "POST", srv.URL+"/items", nil, tc.body)
			wantErrJSON(t, resp, raw, http.StatusBadRequest)
		})
	}
	// boundary cases that must be accepted
	resp, raw := doReq(t, "POST", srv.URL+"/items", nil, itemBody("A-1", "min sku", 0, 0))
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("A-1 (3 chars, zero qty/price) must be valid: %d %q", resp.StatusCode, raw)
	}
	resp, raw = doReq(t, "POST", srv.URL+"/items", nil, itemBody(strings.Repeat("Z", 32), "max sku", 1, 1))
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("32-char sku must be valid: %d %q", resp.StatusCode, raw)
	}
}

func TestCreateConflict(t *testing.T) {
	srv := newSrv(t)
	resp, _ := doReq(t, "POST", srv.URL+"/items", nil, itemBody("DUP-9", "original", 3, 300))
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("first create: %d", resp.StatusCode)
	}
	resp, raw := doReq(t, "POST", srv.URL+"/items", nil, itemBody("DUP-9", "impostor", 9, 900))
	wantErrJSON(t, resp, raw, http.StatusConflict)

	_, raw = doReq(t, "GET", srv.URL+"/items/DUP-9", nil, "")
	if m := asJSON(t, raw); m["name"] != "original" || m["quantity"] != float64(3) {
		t.Fatalf("conflicting create must not touch the stored item: %v", m)
	}
}

func TestListEmptyAndSorted(t *testing.T) {
	srv := newSrv(t)

	resp, raw := doReq(t, "GET", srv.URL+"/items", nil, "")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("empty list status = %d", resp.StatusCode)
	}
	if string(bytes.TrimSpace(raw)) != "[]" {
		t.Fatalf("empty listing must be a JSON [] array (not null), got %q", raw)
	}

	for _, sku := range []string{"ZZZ-9", "AAA-1", "MMM-5"} {
		doReq(t, "POST", srv.URL+"/items", nil, itemBody(sku, "item "+sku, 1, 100))
	}
	_, raw = doReq(t, "GET", srv.URL+"/items", nil, "")
	var list []map[string]any
	if err := json.Unmarshal(raw, &list); err != nil {
		t.Fatalf("list is not a JSON array: %v (%q)", err, raw)
	}
	if len(list) != 3 {
		t.Fatalf("list has %d entries, want 3", len(list))
	}
	got := []string{list[0]["sku"].(string), list[1]["sku"].(string), list[2]["sku"].(string)}
	want := []string{"AAA-1", "MMM-5", "ZZZ-9"}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("listing must be sorted by sku ascending: got %v, want %v", got, want)
		}
	}
	if list[0]["name"] != "item AAA-1" || list[0]["quantity"] != float64(1) {
		t.Fatalf("list entries must be full items: %v", list[0])
	}
}

func TestConditionalGet(t *testing.T) {
	srv := newSrv(t)
	doReq(t, "POST", srv.URL+"/items", nil, itemBody("CG-1", "first", 1, 100))

	resp, raw := doReq(t, "GET", srv.URL+"/items/CG-1", map[string]string{"If-None-Match": `"1"`}, "")
	if resp.StatusCode != http.StatusNotModified {
		t.Fatalf("If-None-Match with the current ETag: status = %d, want 304", resp.StatusCode)
	}
	if len(bytes.TrimSpace(raw)) != 0 {
		t.Fatalf("304 must have an empty body, got %q", raw)
	}
	if et := resp.Header.Get("ETag"); et != `"1"` {
		t.Fatalf("304 must still carry the ETag, got %q", et)
	}

	resp, _ = doReq(t, "GET", srv.URL+"/items/CG-1", map[string]string{"If-None-Match": `"7"`}, "")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("If-None-Match with a stale ETag: status = %d, want 200", resp.StatusCode)
	}

	doReq(t, "PUT", srv.URL+"/items/CG-1", map[string]string{"If-Match": `"1"`}, itemBody("CG-1", "second", 2, 200))
	resp, raw = doReq(t, "GET", srv.URL+"/items/CG-1", map[string]string{"If-None-Match": `"1"`}, "")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("after an update the old ETag must no longer match: status = %d, want 200", resp.StatusCode)
	}
	if m := asJSON(t, raw); m["name"] != "second" {
		t.Fatalf("got %v", m)
	}
}

func TestPutPreconditionChain(t *testing.T) {
	srv := newSrv(t)
	doReq(t, "POST", srv.URL+"/items", nil, itemBody("PRE-1", "orig", 1, 100))

	// unknown sku is 404 no matter what If-Match says
	resp, raw := doReq(t, "PUT", srv.URL+"/items/NOPE-1", map[string]string{"If-Match": `"1"`}, itemBody("NOPE-1", "x", 1, 1))
	wantErrJSON(t, resp, raw, http.StatusNotFound)
	resp, raw = doReq(t, "PUT", srv.URL+"/items/NOPE-1", nil, itemBody("NOPE-1", "x", 1, 1))
	wantErrJSON(t, resp, raw, http.StatusNotFound)

	// existing item, no If-Match at all -> 428 Precondition Required
	resp, raw = doReq(t, "PUT", srv.URL+"/items/PRE-1", nil, itemBody("PRE-1", "x", 1, 1))
	wantErrJSON(t, resp, raw, http.StatusPreconditionRequired)

	// stale version -> 412, and the precondition is checked before the body
	resp, raw = doReq(t, "PUT", srv.URL+"/items/PRE-1", map[string]string{"If-Match": `"99"`}, itemBody("PRE-1", "x", -5, 1))
	wantErrJSON(t, resp, raw, http.StatusPreconditionFailed)

	// garbage If-Match can never match
	resp, raw = doReq(t, "PUT", srv.URL+"/items/PRE-1", map[string]string{"If-Match": "not-an-etag"}, itemBody("PRE-1", "x", 1, 1))
	wantErrJSON(t, resp, raw, http.StatusPreconditionFailed)

	resp, raw = doReq(t, "GET", srv.URL+"/items/PRE-1", nil, "")
	if resp.Header.Get("ETag") != `"1"` {
		t.Fatalf("failed writes must not bump the version, ETag = %q", resp.Header.Get("ETag"))
	}
	if m := asJSON(t, raw); m["name"] != "orig" {
		t.Fatalf("failed writes must not change the item: %v", m)
	}

	// matching If-Match but invalid body -> 400, version not consumed
	resp, raw = doReq(t, "PUT", srv.URL+"/items/PRE-1", map[string]string{"If-Match": `"1"`}, itemBody("PRE-1", "x", -1, 1))
	wantErrJSON(t, resp, raw, http.StatusBadRequest)
	// body sku disagreeing with the path -> 400
	resp, raw = doReq(t, "PUT", srv.URL+"/items/PRE-1", map[string]string{"If-Match": `"1"`}, itemBody("OTHER-1", "x", 1, 1))
	wantErrJSON(t, resp, raw, http.StatusBadRequest)
	resp, _ = doReq(t, "GET", srv.URL+"/items/PRE-1", nil, "")
	if resp.Header.Get("ETag") != `"1"` {
		t.Fatalf("rejected bodies must not bump the version, ETag = %q", resp.Header.Get("ETag"))
	}

	// the winning update
	resp, raw = doReq(t, "PUT", srv.URL+"/items/PRE-1", map[string]string{"If-Match": `"1"`}, itemBody("PRE-1", "renamed", 7, 250))
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("valid conditional PUT: status = %d (body %q)", resp.StatusCode, raw)
	}
	if et := resp.Header.Get("ETag"); et != `"2"` {
		t.Fatalf("update must bump the ETag to \"2\", got %q", et)
	}
	if m := asJSON(t, raw); m["name"] != "renamed" || m["quantity"] != float64(7) {
		t.Fatalf("update response %v", m)
	}

	// sku may be omitted from the body; the path wins
	resp, _ = doReq(t, "PUT", srv.URL+"/items/PRE-1", map[string]string{"If-Match": `"2"`}, `{"name":"again","quantity":8,"price_cents":300}`)
	if resp.StatusCode != http.StatusOK || resp.Header.Get("ETag") != `"3"` {
		t.Fatalf("PUT without body sku: status %d ETag %q, want 200 \"3\"", resp.StatusCode, resp.Header.Get("ETag"))
	}

	// If-Match: * matches whatever is current
	resp, _ = doReq(t, "PUT", srv.URL+"/items/PRE-1", map[string]string{"If-Match": "*"}, itemBody("PRE-1", "starred", 9, 400))
	if resp.StatusCode != http.StatusOK || resp.Header.Get("ETag") != `"4"` {
		t.Fatalf("PUT with If-Match *: status %d ETag %q, want 200 \"4\"", resp.StatusCode, resp.Header.Get("ETag"))
	}

	_, raw = doReq(t, "GET", srv.URL+"/items/PRE-1", nil, "")
	if m := asJSON(t, raw); m["name"] != "starred" || m["quantity"] != float64(9) {
		t.Fatalf("final state %v", m)
	}
}

func TestDeletePreconditions(t *testing.T) {
	srv := newSrv(t)
	doReq(t, "POST", srv.URL+"/items", nil, itemBody("DEL-1", "doomed", 1, 100))

	resp, raw := doReq(t, "DELETE", srv.URL+"/items/GHOST-1", map[string]string{"If-Match": `"1"`}, "")
	wantErrJSON(t, resp, raw, http.StatusNotFound)

	resp, raw = doReq(t, "DELETE", srv.URL+"/items/DEL-1", nil, "")
	wantErrJSON(t, resp, raw, http.StatusPreconditionRequired)

	resp, raw = doReq(t, "DELETE", srv.URL+"/items/DEL-1", map[string]string{"If-Match": `"9"`}, "")
	wantErrJSON(t, resp, raw, http.StatusPreconditionFailed)
	resp, _ = doReq(t, "GET", srv.URL+"/items/DEL-1", nil, "")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("item must survive a failed delete, GET = %d", resp.StatusCode)
	}

	resp, raw = doReq(t, "DELETE", srv.URL+"/items/DEL-1", map[string]string{"If-Match": `"1"`}, "")
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("delete status = %d, want 204", resp.StatusCode)
	}
	if len(bytes.TrimSpace(raw)) != 0 {
		t.Fatalf("204 must have no body, got %q", raw)
	}

	resp, _ = doReq(t, "GET", srv.URL+"/items/DEL-1", nil, "")
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("GET after delete = %d, want 404", resp.StatusCode)
	}
	resp, raw = doReq(t, "DELETE", srv.URL+"/items/DEL-1", map[string]string{"If-Match": `"1"`}, "")
	wantErrJSON(t, resp, raw, http.StatusNotFound)

	// re-creating the sku starts a fresh version history
	resp, _ = doReq(t, "POST", srv.URL+"/items", nil, itemBody("DEL-1", "second life", 2, 200))
	if resp.StatusCode != http.StatusCreated || resp.Header.Get("ETag") != `"1"` {
		t.Fatalf("re-create after delete: status %d ETag %q, want 201 \"1\"", resp.StatusCode, resp.Header.Get("ETag"))
	}
	resp, _ = doReq(t, "DELETE", srv.URL+"/items/DEL-1", map[string]string{"If-Match": "*"}, "")
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("delete with If-Match *: status = %d, want 204", resp.StatusCode)
	}
}

func TestMethodNotAllowed(t *testing.T) {
	srv := newSrv(t)
	for _, tc := range []struct{ method, path string }{
		{"PATCH", "/items/ANY-1"},
		{"POST", "/items/ANY-1"},
		{"DELETE", "/items"},
		{"PUT", "/items"},
	} {
		resp, _ := doReq(t, tc.method, srv.URL+tc.path, nil, `{}`)
		if resp.StatusCode != http.StatusMethodNotAllowed {
			t.Fatalf("%s %s: status = %d, want 405", tc.method, tc.path, resp.StatusCode)
		}
	}
}

func TestNotFoundIsJSON(t *testing.T) {
	srv := newSrv(t)
	resp, raw := doReq(t, "GET", srv.URL+"/items/MISSING-1", nil, "")
	wantErrJSON(t, resp, raw, http.StatusNotFound)
}

// failStore stands in for a storage backend having a very bad day. Every
// method fails with an internal error the API must not leak to clients.
type failStore struct{}

var errDisk = errors.New("disk failure: sector 7 unreadable")

func (failStore) List(ctx context.Context) ([]Item, error)          { return nil, errDisk }
func (failStore) Get(ctx context.Context, sku string) (Item, error) { return Item{}, errDisk }
func (failStore) Create(ctx context.Context, it Item) (Item, error) { return Item{}, errDisk }
func (failStore) Update(ctx context.Context, it Item, expectVersion int) (Item, error) {
	return Item{}, errDisk
}
func (failStore) Delete(ctx context.Context, sku string, expectVersion int) error { return errDisk }

func TestStoreFailuresMapTo500(t *testing.T) {
	srv := httptest.NewServer(NewServer(failStore{}))
	t.Cleanup(srv.Close)

	for _, tc := range []struct{ method, path, body string }{
		{"GET", "/items", ""},
		{"GET", "/items/ANY-1", ""},
		{"POST", "/items", itemBody("ANY-1", "x", 1, 1)},
	} {
		resp, raw := doReq(t, tc.method, srv.URL+tc.path, nil, tc.body)
		m := wantErrJSON(t, resp, raw, http.StatusInternalServerError)
		if msg, _ := m["error"].(string); strings.Contains(strings.ToLower(msg), "sector 7") {
			t.Fatalf("%s %s: internal error details leaked to the client: %q", tc.method, tc.path, msg)
		}
	}
}

func TestMemStoreCASAndSentinels(t *testing.T) {
	ctx := context.Background()
	st := NewMemStore()

	created, err := st.Create(ctx, Item{SKU: "CAS-1", Name: "n", Quantity: 1, PriceCents: 2})
	if err != nil || created.Version != 1 {
		t.Fatalf("Create = %+v, %v; want Version 1, nil", created, err)
	}
	if _, err := st.Create(ctx, Item{SKU: "CAS-1", Name: "again"}); !errors.Is(err, ErrExists) {
		t.Fatalf("duplicate Create err = %v, want ErrExists", err)
	}
	if _, err := st.Get(ctx, "MISSING-1"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("Get(missing) err = %v, want ErrNotFound", err)
	}
	if _, err := st.Update(ctx, Item{SKU: "CAS-1", Name: "x"}, 99); !errors.Is(err, ErrVersionMismatch) {
		t.Fatalf("Update with wrong version err = %v, want ErrVersionMismatch", err)
	}
	upd, err := st.Update(ctx, Item{SKU: "CAS-1", Name: "x", Quantity: 3, PriceCents: 4}, 1)
	if err != nil || upd.Version != 2 {
		t.Fatalf("Update = %+v, %v; want Version 2, nil", upd, err)
	}
	if _, err := st.Update(ctx, Item{SKU: "GHOST-1", Name: "x"}, 1); !errors.Is(err, ErrNotFound) {
		t.Fatalf("Update(missing) err = %v, want ErrNotFound", err)
	}
	if err := st.Delete(ctx, "CAS-1", 1); !errors.Is(err, ErrVersionMismatch) {
		t.Fatalf("Delete with stale version err = %v, want ErrVersionMismatch", err)
	}
	if err := st.Delete(ctx, "CAS-1", 2); err != nil {
		t.Fatalf("Delete = %v", err)
	}
	if _, err := st.Get(ctx, "CAS-1"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("Get after Delete err = %v, want ErrNotFound", err)
	}
	if err := st.Delete(ctx, "CAS-1", 2); !errors.Is(err, ErrNotFound) {
		t.Fatalf("second Delete err = %v, want ErrNotFound", err)
	}
}
