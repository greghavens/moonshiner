// Acceptance harness for the vaultkv package: a loopback fake Vault server
// exercising the KV v2 wire contract pinned in docs/contract.json. No real
// Vault, no real credentials. Protected — do not modify.
// Run: go test -race -timeout 30s ./...
package vaultkv_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"testing"

	vaultkv "go-vault-kv-cas"
)

const (
	token     = "hvs.test-dummy-token-8891" // dummy; never a real credential
	namespace = "team-eng/"
	mount     = "kv-app"
	secPath   = "services/billing/config"
)

type recorded struct {
	Method       string
	Path         string
	RawQuery     string
	Token        string
	Namespace    string
	HasNamespace bool
	ContentType  string
	Body         []byte
}

type canned struct {
	status int
	body   string
}

type fakeVault struct {
	mu     sync.Mutex
	reqs   []recorded
	routes map[string]canned // "METHOD /path" -> response
	srv    *httptest.Server
}

func newFake(t *testing.T) *fakeVault {
	t.Helper()
	f := &fakeVault{routes: map[string]canned{}}
	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		_, hasNS := r.Header["X-Vault-Namespace"]
		rec := recorded{
			Method:       r.Method,
			Path:         r.URL.Path,
			RawQuery:     r.URL.RawQuery,
			Token:        r.Header.Get("X-Vault-Token"),
			Namespace:    r.Header.Get("X-Vault-Namespace"),
			HasNamespace: hasNS,
			ContentType:  r.Header.Get("Content-Type"),
			Body:         body,
		}
		f.mu.Lock()
		f.reqs = append(f.reqs, rec)
		resp, ok := f.routes[r.Method+" "+r.URL.Path]
		f.mu.Unlock()
		if !ok {
			resp = canned{status: 404, body: `{"errors":[]}`}
		}
		if resp.body != "" {
			w.Header().Set("Content-Type", "application/json")
		}
		w.WriteHeader(resp.status)
		if resp.body != "" {
			io.WriteString(w, resp.body)
		}
	}))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeVault) route(method, path string, status int, body string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.routes[method+" "+path] = canned{status: status, body: body}
}

func (f *fakeVault) requests() []recorded {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]recorded, len(f.reqs))
	copy(out, f.reqs)
	return out
}

func (f *fakeVault) client(t *testing.T) *vaultkv.Client {
	t.Helper()
	return vaultkv.New(vaultkv.Config{
		BaseURL:   f.srv.URL,
		Token:     token,
		Namespace: namespace,
		Mount:     mount,
	}, f.srv.Client())
}

func one(t *testing.T, f *fakeVault) recorded {
	t.Helper()
	reqs := f.requests()
	if len(reqs) != 1 {
		t.Fatalf("expected exactly 1 request, got %d", len(reqs))
	}
	return reqs[0]
}

func mustJSON(t *testing.T, raw []byte, into any) {
	t.Helper()
	if err := json.Unmarshal(raw, into); err != nil {
		t.Fatalf("request body is not valid JSON: %v\nbody: %s", err, raw)
	}
}

const dataPath = "/v1/" + mount + "/data/" + secPath

func TestGetLatestSecret(t *testing.T) {
	f := newFake(t)
	f.route("GET", dataPath, 200, `{
		"request_id": "a3b1",
		"data": {
			"data": {"api_key": "dummy-billing-key", "region": "eu-central-1"},
			"metadata": {
				"created_time": "2026-07-01T09:30:00.123456Z",
				"custom_metadata": null,
				"deletion_time": "",
				"destroyed": false,
				"version": 3
			}
		}
	}`)
	c := f.client(t)

	sec, err := c.Get(context.Background(), secPath)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	want := map[string]any{"api_key": "dummy-billing-key", "region": "eu-central-1"}
	if !reflect.DeepEqual(sec.Data, want) {
		t.Errorf("Data = %#v, want %#v", sec.Data, want)
	}
	if sec.Metadata.Version != 3 {
		t.Errorf("Metadata.Version = %d, want 3", sec.Metadata.Version)
	}
	if sec.Metadata.CreatedTime != "2026-07-01T09:30:00.123456Z" {
		t.Errorf("Metadata.CreatedTime = %q", sec.Metadata.CreatedTime)
	}
	if sec.Metadata.DeletionTime != "" {
		t.Errorf("Metadata.DeletionTime = %q, want empty", sec.Metadata.DeletionTime)
	}
	if sec.Metadata.Destroyed {
		t.Errorf("Metadata.Destroyed = true, want false")
	}

	r := one(t, f)
	if r.Method != http.MethodGet {
		t.Errorf("method = %s, want GET", r.Method)
	}
	if r.Path != dataPath {
		t.Errorf("path = %q, want %q (mount-aware /v1/<mount>/data/<path>)", r.Path, dataPath)
	}
	if r.RawQuery != "" {
		t.Errorf("latest read must not send a version parameter, got query %q", r.RawQuery)
	}
	if r.Token != token {
		t.Errorf("X-Vault-Token = %q, want %q", r.Token, token)
	}
	if r.Namespace != namespace {
		t.Errorf("X-Vault-Namespace = %q, want %q", r.Namespace, namespace)
	}
}

func TestGetVersionSendsQuery(t *testing.T) {
	f := newFake(t)
	f.route("GET", dataPath, 200, `{
		"data": {
			"data": {"api_key": "old-key"},
			"metadata": {"created_time": "2026-06-25T08:00:00Z", "deletion_time": "", "destroyed": false, "version": 2}
		}
	}`)
	c := f.client(t)

	sec, err := c.GetVersion(context.Background(), secPath, 2)
	if err != nil {
		t.Fatalf("GetVersion: %v", err)
	}
	if sec.Metadata.Version != 2 {
		t.Errorf("Metadata.Version = %d, want 2", sec.Metadata.Version)
	}
	r := one(t, f)
	if r.RawQuery != "version=2" {
		t.Errorf("query = %q, want version=2", r.RawQuery)
	}
}

func TestRootNamespaceOmitsHeader(t *testing.T) {
	f := newFake(t)
	f.route("GET", "/v1/secret/data/app/config", 200, `{
		"data": {"data": {"k": "v"}, "metadata": {"created_time": "2026-07-01T00:00:00Z", "deletion_time": "", "destroyed": false, "version": 1}}
	}`)
	c := vaultkv.New(vaultkv.Config{BaseURL: f.srv.URL, Token: token, Mount: "secret"}, f.srv.Client())

	if _, err := c.Get(context.Background(), "app/config"); err != nil {
		t.Fatalf("Get: %v", err)
	}
	r := one(t, f)
	if r.Path != "/v1/secret/data/app/config" {
		t.Errorf("path = %q, want mount-aware /v1/secret/data/app/config", r.Path)
	}
	if r.HasNamespace {
		t.Errorf("root-namespace client must not send X-Vault-Namespace at all, got %q", r.Namespace)
	}
}

func TestPutWithoutCASOmitsOptions(t *testing.T) {
	f := newFake(t)
	f.route("POST", dataPath, 200, `{
		"data": {"created_time": "2026-07-02T10:00:00Z", "deletion_time": "", "destroyed": false, "version": 4}
	}`)
	c := f.client(t)

	meta, err := c.Put(context.Background(), secPath, map[string]any{"api_key": "rotated-key"})
	if err != nil {
		t.Fatalf("Put: %v", err)
	}
	if meta.Version != 4 {
		t.Errorf("returned version = %d, want 4", meta.Version)
	}
	if meta.CreatedTime != "2026-07-02T10:00:00Z" {
		t.Errorf("returned created_time = %q", meta.CreatedTime)
	}

	r := one(t, f)
	if r.Method != http.MethodPost {
		t.Errorf("method = %s, want POST", r.Method)
	}
	if !strings.HasPrefix(r.ContentType, "application/json") {
		t.Errorf("Content-Type = %q, want application/json", r.ContentType)
	}
	var body map[string]json.RawMessage
	mustJSON(t, r.Body, &body)
	if _, hasOpts := body["options"]; hasOpts {
		t.Errorf("write without CAS must omit the options object entirely, body: %s", r.Body)
	}
	var data map[string]any
	mustJSON(t, body["data"], &data)
	if !reflect.DeepEqual(data, map[string]any{"api_key": "rotated-key"}) {
		t.Errorf("body data = %#v", data)
	}
}

func TestPutCASSendsOptions(t *testing.T) {
	f := newFake(t)
	f.route("POST", dataPath, 200, `{
		"data": {"created_time": "2026-07-02T11:00:00Z", "deletion_time": "", "destroyed": false, "version": 4}
	}`)
	c := f.client(t)

	if _, err := c.PutCAS(context.Background(), secPath, map[string]any{"k": "v"}, 3); err != nil {
		t.Fatalf("PutCAS(3): %v", err)
	}
	if _, err := c.PutCAS(context.Background(), secPath, map[string]any{"k": "v"}, 0); err != nil {
		t.Fatalf("PutCAS(0): %v", err)
	}

	reqs := f.requests()
	if len(reqs) != 2 {
		t.Fatalf("expected 2 requests, got %d", len(reqs))
	}
	type writeBody struct {
		Options map[string]any `json:"options"`
		Data    map[string]any `json:"data"`
	}
	var b0, b1 writeBody
	mustJSON(t, reqs[0].Body, &b0)
	mustJSON(t, reqs[1].Body, &b1)
	if got, ok := b0.Options["cas"]; !ok || got != float64(3) {
		t.Errorf("first write options.cas = %v (present=%v), want 3", got, ok)
	}
	if got, ok := b1.Options["cas"]; !ok || got != float64(0) {
		t.Errorf("create-only write options.cas = %v (present=%v), want explicit 0", got, ok)
	}
	if b0.Data == nil || b1.Data == nil {
		t.Errorf("both CAS writes must still carry the data object")
	}
}

func TestCASConflict(t *testing.T) {
	f := newFake(t)
	f.route("POST", dataPath, 400, `{"errors":["check-and-set parameter did not match the current version"]}`)
	c := f.client(t)

	_, err := c.PutCAS(context.Background(), secPath, map[string]any{"k": "v"}, 2)
	if err == nil {
		t.Fatalf("PutCAS must fail on a check-and-set conflict")
	}
	var ae *vaultkv.APIError
	if !errors.As(err, &ae) {
		t.Fatalf("error must unwrap to *vaultkv.APIError, got %T: %v", err, err)
	}
	if ae.StatusCode != 400 {
		t.Errorf("StatusCode = %d, want 400", ae.StatusCode)
	}
	if len(ae.Errors) != 1 || !strings.Contains(ae.Errors[0], "check-and-set parameter did not match") {
		t.Errorf("Errors = %q, want the vault check-and-set message preserved", ae.Errors)
	}
	if !vaultkv.IsCASMismatch(err) {
		t.Errorf("IsCASMismatch = false, want true for a 400 check-and-set error")
	}
	if vaultkv.IsCASMismatch(vaultkv.ErrNotFound) {
		t.Errorf("IsCASMismatch(ErrNotFound) = true, want false")
	}
}

func TestDeleteLatestVersion(t *testing.T) {
	f := newFake(t)
	f.route("DELETE", dataPath, 204, "")
	c := f.client(t)

	if err := c.DeleteLatest(context.Background(), secPath); err != nil {
		t.Fatalf("DeleteLatest: %v", err)
	}
	r := one(t, f)
	if r.Method != http.MethodDelete {
		t.Errorf("method = %s, want DELETE", r.Method)
	}
	if r.Path != dataPath {
		t.Errorf("path = %q, want the data path %q (soft delete of latest)", r.Path, dataPath)
	}
	if len(r.Body) != 0 {
		t.Errorf("DELETE of latest version must carry no body, got %s", r.Body)
	}
}

func TestDeleteVersions(t *testing.T) {
	f := newFake(t)
	p := "/v1/" + mount + "/delete/" + secPath
	f.route("POST", p, 204, "")
	c := f.client(t)

	if err := c.DeleteVersions(context.Background(), secPath, []int{1, 2}); err != nil {
		t.Fatalf("DeleteVersions: %v", err)
	}
	r := one(t, f)
	if r.Method != http.MethodPost || r.Path != p {
		t.Errorf("request = %s %s, want POST %s", r.Method, r.Path, p)
	}
	var body struct {
		Versions []int `json:"versions"`
	}
	mustJSON(t, r.Body, &body)
	if !reflect.DeepEqual(body.Versions, []int{1, 2}) {
		t.Errorf("versions = %v, want [1 2]", body.Versions)
	}
}

func TestUndeleteVersions(t *testing.T) {
	f := newFake(t)
	p := "/v1/" + mount + "/undelete/" + secPath
	f.route("POST", p, 204, "")
	c := f.client(t)

	if err := c.Undelete(context.Background(), secPath, []int{2}); err != nil {
		t.Fatalf("Undelete: %v", err)
	}
	r := one(t, f)
	if r.Method != http.MethodPost || r.Path != p {
		t.Errorf("request = %s %s, want POST %s", r.Method, r.Path, p)
	}
	var body struct {
		Versions []int `json:"versions"`
	}
	mustJSON(t, r.Body, &body)
	if !reflect.DeepEqual(body.Versions, []int{2}) {
		t.Errorf("versions = %v, want [2]", body.Versions)
	}
}

func TestDestroyUsesPUT(t *testing.T) {
	f := newFake(t)
	p := "/v1/" + mount + "/destroy/" + secPath
	f.route("PUT", p, 204, "")
	c := f.client(t)

	if err := c.Destroy(context.Background(), secPath, []int{1}); err != nil {
		t.Fatalf("Destroy: %v", err)
	}
	r := one(t, f)
	if r.Method != http.MethodPut {
		t.Errorf("method = %s, want PUT (destroy is documented as PUT)", r.Method)
	}
	if r.Path != p {
		t.Errorf("path = %q, want %q", r.Path, p)
	}
	var body struct {
		Versions []int `json:"versions"`
	}
	mustJSON(t, r.Body, &body)
	if !reflect.DeepEqual(body.Versions, []int{1}) {
		t.Errorf("versions = %v, want [1]", body.Versions)
	}
}

func TestReadSoftDeletedVersion(t *testing.T) {
	f := newFake(t)
	f.route("GET", dataPath, 404, `{
		"data": {
			"data": null,
			"metadata": {"created_time": "2026-06-20T08:00:00Z", "deletion_time": "2026-07-03T12:00:00Z", "destroyed": false, "version": 1}
		}
	}`)
	c := f.client(t)

	sec, err := c.GetVersion(context.Background(), secPath, 1)
	if err != nil {
		t.Fatalf("a 404 that carries a data.metadata object is a soft-deleted version, not an error; got: %v", err)
	}
	if sec.Data != nil {
		t.Errorf("Data = %#v, want nil for a deleted version", sec.Data)
	}
	if sec.Metadata.DeletionTime != "2026-07-03T12:00:00Z" {
		t.Errorf("DeletionTime = %q, want the deletion timestamp", sec.Metadata.DeletionTime)
	}
	if sec.Metadata.Version != 1 {
		t.Errorf("Version = %d, want 1", sec.Metadata.Version)
	}
	if sec.Metadata.Destroyed {
		t.Errorf("Destroyed = true, want false (soft delete)")
	}
}

func TestReadDestroyedVersion(t *testing.T) {
	f := newFake(t)
	f.route("GET", dataPath, 404, `{
		"data": {
			"data": null,
			"metadata": {"created_time": "2026-06-20T08:00:00Z", "deletion_time": "", "destroyed": true, "version": 2}
		}
	}`)
	c := f.client(t)

	sec, err := c.GetVersion(context.Background(), secPath, 2)
	if err != nil {
		t.Fatalf("a destroyed version still returns its metadata: %v", err)
	}
	if sec.Data != nil {
		t.Errorf("Data = %#v, want nil for a destroyed version", sec.Data)
	}
	if !sec.Metadata.Destroyed {
		t.Errorf("Destroyed = false, want true")
	}
}

func TestNotFoundIsSentinel(t *testing.T) {
	f := newFake(t) // default route: 404 {"errors":[]}
	c := f.client(t)

	sec, err := c.Get(context.Background(), "no/such/secret")
	if sec != nil {
		t.Errorf("secret = %#v, want nil", sec)
	}
	if !errors.Is(err, vaultkv.ErrNotFound) {
		t.Fatalf("a 404 with an empty errors array must satisfy errors.Is(err, ErrNotFound), got %T: %v", err, err)
	}
}

func TestPermissionDeniedDecoded(t *testing.T) {
	f := newFake(t)
	f.route("GET", dataPath, 403, `{"errors":["1 error occurred:\n\t* permission denied\n\n"]}`)
	c := f.client(t)

	_, err := c.Get(context.Background(), secPath)
	var ae *vaultkv.APIError
	if !errors.As(err, &ae) {
		t.Fatalf("error must unwrap to *vaultkv.APIError, got %T: %v", err, err)
	}
	if ae.StatusCode != 403 {
		t.Errorf("StatusCode = %d, want 403", ae.StatusCode)
	}
	if len(ae.Errors) != 1 || !strings.Contains(ae.Errors[0], "permission denied") {
		t.Errorf("Errors = %q, want the vault message preserved", ae.Errors)
	}
	if !strings.Contains(err.Error(), "403") || !strings.Contains(err.Error(), "permission denied") {
		t.Errorf("Error() should carry status and detail, got %q", err.Error())
	}
	if errors.Is(err, vaultkv.ErrNotFound) {
		t.Errorf("a decoded API error must not satisfy errors.Is(err, ErrNotFound)")
	}
}

func TestMetadataDecoding(t *testing.T) {
	f := newFake(t)
	p := "/v1/" + mount + "/metadata/" + secPath
	f.route("GET", p, 200, `{
		"data": {
			"cas_required": true,
			"created_time": "2026-06-20T08:00:00Z",
			"current_version": 3,
			"max_versions": 10,
			"oldest_version": 1,
			"updated_time": "2026-07-02T10:00:00Z",
			"custom_metadata": null,
			"versions": {
				"1": {"created_time": "2026-06-20T08:00:00Z", "deletion_time": "2026-07-03T12:00:00Z", "destroyed": false},
				"2": {"created_time": "2026-06-25T08:00:00Z", "deletion_time": "", "destroyed": true},
				"3": {"created_time": "2026-07-02T10:00:00Z", "deletion_time": "", "destroyed": false}
			}
		}
	}`)
	c := f.client(t)

	meta, err := c.Metadata(context.Background(), secPath)
	if err != nil {
		t.Fatalf("Metadata: %v", err)
	}
	r := one(t, f)
	if r.Method != http.MethodGet || r.Path != p {
		t.Errorf("request = %s %s, want GET %s (metadata path, not data path)", r.Method, r.Path, p)
	}
	if meta.CurrentVersion != 3 {
		t.Errorf("CurrentVersion = %d, want 3", meta.CurrentVersion)
	}
	if meta.OldestVersion != 1 {
		t.Errorf("OldestVersion = %d, want 1", meta.OldestVersion)
	}
	if meta.MaxVersions != 10 {
		t.Errorf("MaxVersions = %d, want 10", meta.MaxVersions)
	}
	if !meta.CASRequired {
		t.Errorf("CASRequired = false, want true")
	}
	if len(meta.Versions) != 3 {
		t.Fatalf("Versions has %d entries, want 3", len(meta.Versions))
	}
	v1, ok := meta.Versions[1]
	if !ok {
		t.Fatalf("Versions[1] missing (map keys are the numeric version numbers)")
	}
	if v1.DeletionTime != "2026-07-03T12:00:00Z" {
		t.Errorf("Versions[1].DeletionTime = %q", v1.DeletionTime)
	}
	v2 := meta.Versions[2]
	if !v2.Destroyed {
		t.Errorf("Versions[2].Destroyed = false, want true")
	}
	if v2.Version != 2 {
		t.Errorf("Versions[2].Version = %d, want 2 (filled from the map key)", v2.Version)
	}
}

func TestNamespaceHeaderOnWriteAndLifecycle(t *testing.T) {
	f := newFake(t)
	f.route("POST", dataPath, 200, `{"data":{"created_time":"2026-07-02T10:00:00Z","deletion_time":"","destroyed":false,"version":1}}`)
	f.route("POST", "/v1/"+mount+"/undelete/"+secPath, 204, "")
	c := f.client(t)

	if _, err := c.Put(context.Background(), secPath, map[string]any{"k": "v"}); err != nil {
		t.Fatalf("Put: %v", err)
	}
	if err := c.Undelete(context.Background(), secPath, []int{1}); err != nil {
		t.Fatalf("Undelete: %v", err)
	}
	for i, r := range f.requests() {
		if r.Namespace != namespace {
			t.Errorf("request %d: X-Vault-Namespace = %q, want %q on every request", i, r.Namespace, namespace)
		}
		if r.Token != token {
			t.Errorf("request %d: X-Vault-Token = %q, want %q on every request", i, r.Token, token)
		}
	}
}
