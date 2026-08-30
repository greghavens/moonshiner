// Protected acceptance harness. Do not modify this file.
//
// Everything here runs against 127.0.0.1 only; no live VMware endpoint is
// contacted.
package verify

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"go/ast"
	"go/parser"
	"go/token"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"

	"example.com/vcf90/vsphere/internal/mockvc"
	"example.com/vcf90/vsphere/vctag"
)

const (
	categoriesPath = "/api/vcenter/tagging/categories"
	tagsPath       = "/api/vcenter/tagging/tags"
	sessionPath    = "/api/session"

	specCommit = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
	specPath   = "specifications/vsphere/openapi/automation/vcenter.yaml"
	specTag    = "9.0.0.0"
)

var contractOperationIDs = []string{
	"Cis.Session_create",
	"Cis.Session_delete",
	"Vcenter.Tagging.Categories_list",
	"Vcenter.Tagging.Tags_list",
}

type rejectSecondLoginTransport struct {
	base     http.RoundTripper
	attempts atomic.Int32
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func (t *rejectSecondLoginTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if req.Method == http.MethodPost && req.URL.Path == sessionPath && t.attempts.Add(1) == 2 {
		clone := req.Clone(req.Context())
		clone.Header = req.Header.Clone()
		clone.SetBasicAuth(mockvc.DefaultUsername, "wrong-after-success")
		req = clone
	}
	return t.base.RoundTrip(req)
}

// wantRows is the complete tagging inventory of internal/mockvc/inventory.json,
// joined and ordered as the task requires: by category name, then tag name,
// then tag id, all compared byte by byte. Tags whose category is gone keep an
// empty category name and cardinality and therefore sort first.
var wantRows = []vctag.Row{
	{
		CategoryName: "",
		CategoryID:   "urn:vmomi:InventoryServiceCategory:99999999-8888-4777-8666-555555555501:GLOBAL",
		Cardinality:  "",
		TagName:      "orphaned",
		TagID:        "urn:vmomi:InventoryServiceTag:11111111-2222-4333-8444-555555555503:GLOBAL",
	},
	{
		CategoryName: "",
		CategoryID:   "urn:vmomi:InventoryServiceCategory:99999999-8888-4777-8666-555555555502:GLOBAL",
		Cardinality:  "",
		TagName:      "orphaned",
		TagID:        "urn:vmomi:InventoryServiceTag:11111111-2222-4333-8444-555555555509:GLOBAL",
	},
	{
		CategoryName: "Backup Policy",
		CategoryID:   "urn:vmomi:InventoryServiceCategory:0a3f1c5e-1b2d-4c8a-9f01-11a2b3c4d5e6:GLOBAL",
		Cardinality:  "SINGLE",
		TagName:      "Weekly",
		TagID:        "urn:vmomi:InventoryServiceTag:11111111-2222-4333-8444-555555555505:GLOBAL",
	},
	{
		CategoryName: "Backup Policy",
		CategoryID:   "urn:vmomi:InventoryServiceCategory:0a3f1c5e-1b2d-4c8a-9f01-11a2b3c4d5e6:GLOBAL",
		Cardinality:  "SINGLE",
		TagName:      "daily",
		TagID:        "urn:vmomi:InventoryServiceTag:11111111-2222-4333-8444-555555555502:GLOBAL",
	},
	{
		CategoryName: "Compliance",
		CategoryID:   "urn:vmomi:InventoryServiceCategory:2c5f3e70-3d4f-4eac-9b23-33c4d5e6f708:GLOBAL",
		Cardinality:  "SINGLE",
		TagName:      "pci-dss",
		TagID:        "urn:vmomi:InventoryServiceTag:11111111-2222-4333-8444-555555555507:GLOBAL",
	},
	{
		CategoryName: "Owner",
		CategoryID:   "urn:vmomi:InventoryServiceCategory:4e715092-5f61-40ce-bd45-55e6f708192a:GLOBAL",
		Cardinality:  "SINGLE",
		TagName:      "platform",
		TagID:        "urn:vmomi:InventoryServiceTag:11111111-2222-4333-8444-555555555508:GLOBAL",
	},
	{
		CategoryName: "app-tier",
		CategoryID:   "urn:vmomi:InventoryServiceCategory:1b4e2d6f-2c3e-4d9b-8a12-22b3c4d5e6f7:GLOBAL",
		Cardinality:  "MULTIPLE",
		TagName:      "db",
		TagID:        "urn:vmomi:InventoryServiceTag:11111111-2222-4333-8444-555555555506:GLOBAL",
	},
	{
		CategoryName: "app-tier",
		CategoryID:   "urn:vmomi:InventoryServiceCategory:1b4e2d6f-2c3e-4d9b-8a12-22b3c4d5e6f7:GLOBAL",
		Cardinality:  "MULTIPLE",
		TagName:      "web",
		TagID:        "urn:vmomi:InventoryServiceTag:11111111-2222-4333-8444-555555555501:GLOBAL",
	},
	{
		CategoryName: "zone",
		CategoryID:   "urn:vmomi:InventoryServiceCategory:3d604f81-4e50-4fbd-ac34-44d5e6f70819:GLOBAL",
		Cardinality:  "MULTIPLE",
		TagName:      "eu-west",
		TagID:        "urn:vmomi:InventoryServiceTag:11111111-2222-4333-8444-555555555504:GLOBAL",
	},
}

func wantRender() string {
	var sb strings.Builder
	for _, r := range wantRows {
		sb.WriteString(r.CategoryName + "\t" + r.Cardinality + "\t" + r.TagName + "\t" + r.TagID + "\n")
	}
	return sb.String()
}

func newClient(t *testing.T, srv *mockvc.Server, pageSize int) *vctag.Client {
	t.Helper()
	c, err := vctag.New(vctag.Config{
		BaseURL:  srv.URL(),
		Username: mockvc.DefaultUsername,
		Password: mockvc.DefaultPassword,
		PageSize: pageSize,
	})
	if err != nil {
		t.Fatalf("vctag.New: %v", err)
	}
	return c
}

func loggedIn(t *testing.T, srv *mockvc.Server, pageSize int) *vctag.Client {
	t.Helper()
	c := newClient(t, srv, pageSize)
	if err := c.Login(context.Background()); err != nil {
		t.Fatalf("Login: %v", err)
	}
	return c
}

func requestsFor(reqs []mockvc.Request, path string) []mockvc.Request {
	var out []mockvc.Request
	for _, r := range reqs {
		if r.Path == path {
			out = append(out, r)
		}
	}
	return out
}

// assertNoEmptyQueryValues fails when the client serialized an unset optional
// field as an empty value instead of leaving it out.
func assertNoEmptyQueryValues(t *testing.T, r mockvc.Request) {
	t.Helper()
	if r.RawQuery == "" {
		return
	}
	for _, pair := range strings.Split(r.RawQuery, "&") {
		if pair == "" || strings.HasSuffix(pair, "=") || !strings.Contains(pair, "=") {
			t.Errorf("%s %s?%s: query pair %q is empty; an unset optional field must be omitted", r.Method, r.Path, r.RawQuery, pair)
		}
	}
}

func assertSessionCall(t *testing.T, r mockvc.Request, sessionID string) {
	t.Helper()
	if got := r.SessionHeader; got != sessionID {
		t.Errorf("%s %s: vmware-api-session-id = %q, want %q", r.Method, r.Path, got, sessionID)
	}
	if r.Authorization != "" {
		t.Errorf("%s %s: Authorization header must not be sent on api_key_auth operations, got %q", r.Method, r.Path, r.Authorization)
	}
	if r.Accept != "application/json" {
		t.Errorf("%s %s: Accept = %q, want application/json", r.Method, r.Path, r.Accept)
	}
	if r.Status < 200 || r.Status >= 300 {
		t.Errorf("%s %s?%s: server answered %d", r.Method, r.Path, r.RawQuery, r.Status)
	}
	assertNoEmptyQueryValues(t, r)
}

// assertPageSequence checks a full marker walk over one collection.
func assertPageSequence(t *testing.T, reqs []mockvc.Request, wantPages int, wantPageSize string, sessionID string) {
	t.Helper()
	if len(reqs) != wantPages {
		t.Fatalf("collection was fetched with %d request(s), want %d", len(reqs), wantPages)
	}
	seen := map[string]bool{}
	for i, r := range reqs {
		if r.Method != http.MethodGet {
			t.Errorf("page %d: method = %s, want GET", i, r.Method)
		}
		assertSessionCall(t, r, sessionID)

		q, err := url.ParseQuery(r.RawQuery)
		if err != nil {
			t.Fatalf("page %d: unparsable query %q: %v", i, r.RawQuery, err)
		}
		for key := range q {
			if key != "marker" && key != "page_size" {
				t.Errorf("page %d: unexpected query key %q (raw %q)", i, key, r.RawQuery)
			}
		}
		if _, ok := q["names"]; ok {
			t.Errorf("page %d: names filter was sent although none was requested", i)
		}

		markers, hasMarker := q["marker"]
		if i == 0 {
			if hasMarker {
				t.Errorf("page 0: marker=%q was sent, but the first page must omit marker entirely (raw %q)", markers, r.RawQuery)
			}
		} else {
			if !hasMarker {
				t.Errorf("page %d: no marker was sent (raw %q)", i, r.RawQuery)
			} else if seen[markers[0]] {
				t.Errorf("page %d: marker %q was replayed", i, markers[0])
			}
			if hasMarker {
				seen[markers[0]] = true
			}
		}

		sizes, hasSize := q["page_size"]
		if wantPageSize == "" {
			if hasSize {
				t.Errorf("page %d: page_size=%q was sent, but the client was configured to leave it unset (raw %q)", i, sizes, r.RawQuery)
			}
			if i == 0 && r.RawQuery != "" {
				t.Errorf("page 0: query = %q, want an empty query string", r.RawQuery)
			}
		} else {
			if !hasSize {
				t.Fatalf("page %d: page_size was not sent (raw %q)", i, r.RawQuery)
			}
			if sizes[0] != wantPageSize {
				t.Errorf("page %d: page_size = %q, want %q", i, sizes[0], wantPageSize)
			}
		}
	}
}

func TestOfficialSourcesPinTheNineZeroSpecification(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "docs", "official_sources.json"))
	if err != nil {
		t.Fatalf("read docs/official_sources.json: %v", err)
	}
	var doc struct {
		Sources []struct {
			Repository   string `json:"repository"`
			License      string `json:"license"`
			Tag          string `json:"tag"`
			CommitSHA    string `json:"commit_sha"`
			SpecPath     string `json:"spec_path"`
			OperationIDs []struct {
				OperationID string `json:"operationId"`
			} `json:"operation_ids"`
		} `json:"sources"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parse docs/official_sources.json: %v", err)
	}
	if len(doc.Sources) != 1 {
		t.Fatalf("sources = %d, want 1", len(doc.Sources))
	}
	src := doc.Sources[0]
	if src.CommitSHA != specCommit {
		t.Errorf("commit_sha = %q, want %q (tag %s of vmware/vcf-api-specs)", src.CommitSHA, specCommit, specTag)
	}
	if src.SpecPath != specPath {
		t.Errorf("spec_path = %q, want %q", src.SpecPath, specPath)
	}
	if src.Tag != specTag {
		t.Errorf("tag = %q, want %q", src.Tag, specTag)
	}
	var got []string
	for _, op := range src.OperationIDs {
		got = append(got, op.OperationID)
	}
	if strings.Join(got, ",") != strings.Join(contractOperationIDs, ",") {
		t.Errorf("operation_ids = %v, want %v", got, contractOperationIDs)
	}

	rawContract, err := os.ReadFile(filepath.Join("..", "docs", "contract.json"))
	if err != nil {
		t.Fatalf("read docs/contract.json: %v", err)
	}
	var contract struct {
		Source struct {
			Commit   string `json:"commit"`
			SpecPath string `json:"spec_path"`
		} `json:"source"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			URL         string `json:"url"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(rawContract, &contract); err != nil {
		t.Fatalf("parse docs/contract.json: %v", err)
	}
	if contract.Source.Commit != specCommit || contract.Source.SpecPath != specPath {
		t.Errorf("docs/contract.json is not pinned to %s@%s", specPath, specCommit)
	}
	wantURLs := map[string]string{
		"Cis.Session_create":              "POST " + sessionPath,
		"Cis.Session_delete":              "DELETE " + sessionPath,
		"Vcenter.Tagging.Categories_list": "GET " + categoriesPath,
		"Vcenter.Tagging.Tags_list":       "GET " + tagsPath,
	}
	if len(contract.Operations) != len(wantURLs) {
		t.Fatalf("contract names %d operations, want %d", len(contract.Operations), len(wantURLs))
	}
	for _, op := range contract.Operations {
		want, ok := wantURLs[op.OperationID]
		if !ok {
			t.Errorf("contract names unexpected operationId %q", op.OperationID)
			continue
		}
		if got := op.Method + " " + op.URL; got != want {
			t.Errorf("%s: %q, want %q", op.OperationID, got, want)
		}
	}
}

func TestNewRejectsUnusableConfigurations(t *testing.T) {
	cases := []struct {
		name string
		cfg  vctag.Config
	}{
		{"empty base url", vctag.Config{Username: "u", Password: "p"}},
		{"blank base url", vctag.Config{BaseURL: "   ", Username: "u", Password: "p"}},
		{"base url without scheme", vctag.Config{BaseURL: "vc.example.com", Username: "u", Password: "p"}},
		{"unsupported scheme", vctag.Config{BaseURL: "ftp://vc.example.com", Username: "u", Password: "p"}},
		{"base url without host", vctag.Config{BaseURL: "https:///vcenter", Username: "u", Password: "p"}},
		{"base url with empty query", vctag.Config{BaseURL: "https://vc.example.com?", Username: "u", Password: "p"}},
		{"base url with query", vctag.Config{BaseURL: "https://vc.example.com?site=lab", Username: "u", Password: "p"}},
		{"base url with fragment", vctag.Config{BaseURL: "https://vc.example.com#inventory", Username: "u", Password: "p"}},
		{"empty username", vctag.Config{BaseURL: "https://vc.example.com", Password: "p"}},
		{"negative page size", vctag.Config{BaseURL: "https://vc.example.com", Username: "u", Password: "p", PageSize: -1}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := vctag.New(tc.cfg); err == nil {
				t.Fatalf("New(%+v) = nil error, want an error", tc.cfg)
			}
		})
	}
}

func TestNewAllowsAnEmptyPasswordAndDoesNoIO(t *testing.T) {
	var calls atomic.Int32
	httpClient := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		calls.Add(1)
		return nil, errors.New("unexpected request from New")
	})}
	if _, err := vctag.New(vctag.Config{
		BaseURL:    "https://vc.example.com",
		Username:   "administrator",
		Password:   "",
		HTTPClient: httpClient,
	}); err != nil {
		t.Fatalf("New with an empty password = %v, want no error", err)
	}
	if got := calls.Load(); got != 0 {
		t.Errorf("New made %d HTTP request(s), want 0", got)
	}
}

func TestLoginAndLogoutWireShape(t *testing.T) {
	srv := mockvc.Start(mockvc.Options{})
	defer srv.Close()

	c := newClient(t, srv, 0)
	if err := c.Login(context.Background()); err != nil {
		t.Fatalf("Login: %v", err)
	}
	if got := c.SessionID(); got != srv.IssuedSessionID() {
		t.Fatalf("SessionID() = %q, want %q", got, srv.IssuedSessionID())
	}

	reqs := srv.Requests()
	if len(reqs) != 1 {
		t.Fatalf("Login made %d request(s), want 1", len(reqs))
	}
	login := reqs[0]
	if login.Method != http.MethodPost || login.Path != sessionPath {
		t.Errorf("Login sent %s %s, want POST %s", login.Method, login.Path, sessionPath)
	}
	if login.RawQuery != "" {
		t.Errorf("Login query = %q, want empty", login.RawQuery)
	}
	wantAuth := "Basic " + base64.StdEncoding.EncodeToString([]byte(mockvc.DefaultUsername+":"+mockvc.DefaultPassword))
	if login.Authorization != wantAuth {
		t.Errorf("Login Authorization = %q, want %q", login.Authorization, wantAuth)
	}
	if login.SessionHeader != "" {
		t.Errorf("Login must not send vmware-api-session-id, got %q", login.SessionHeader)
	}
	if login.Accept != "application/json" {
		t.Errorf("Login Accept = %q, want application/json", login.Accept)
	}
	if login.Body != "" {
		t.Errorf("Login body = %q, want no body", login.Body)
	}
	if login.ContentType != "" {
		t.Errorf("Login Content-Type = %q, want no Content-Type", login.ContentType)
	}
	if login.Status != http.StatusCreated {
		t.Errorf("Login status = %d, want 201", login.Status)
	}

	if err := c.Logout(context.Background()); err != nil {
		t.Fatalf("Logout: %v", err)
	}
	if got := c.SessionID(); got != "" {
		t.Errorf("SessionID() after Logout = %q, want empty", got)
	}
	reqs = srv.Requests()
	if len(reqs) != 2 {
		t.Fatalf("after Logout there were %d request(s), want 2", len(reqs))
	}
	logout := reqs[1]
	if logout.Method != http.MethodDelete || logout.Path != sessionPath {
		t.Errorf("Logout sent %s %s, want DELETE %s", logout.Method, logout.Path, sessionPath)
	}
	assertSessionCall(t, logout, srv.IssuedSessionID())
	if logout.Status != http.StatusNoContent {
		t.Errorf("Logout status = %d, want 204", logout.Status)
	}
	if logout.Body != "" {
		t.Errorf("Logout body = %q, want no body", logout.Body)
	}
}

func TestInventoryWalksEveryPageWithExplicitPageSize(t *testing.T) {
	srv := mockvc.Start(mockvc.Options{})
	defer srv.Close()

	c := loggedIn(t, srv, 2)
	rows, err := c.Inventory(context.Background())
	if err != nil {
		t.Fatalf("Inventory: %v", err)
	}
	assertRows(t, rows)

	if got, want := vctag.Render(rows), wantRender(); got != want {
		t.Errorf("Render mismatch:\n got:\n%s\nwant:\n%s", got, want)
	}

	reqs := srv.Requests()
	if reqs[0].Path != sessionPath {
		t.Fatalf("first request was %s %s, want the session create", reqs[0].Method, reqs[0].Path)
	}
	cats := requestsFor(reqs, categoriesPath)
	tags := requestsFor(reqs, tagsPath)
	assertPageSequence(t, cats, 3, "2", srv.IssuedSessionID())
	assertPageSequence(t, tags, 5, "2", srv.IssuedSessionID())

	if len(reqs) != 1+len(cats)+len(tags) {
		t.Errorf("Inventory made %d request(s) in total, want %d", len(reqs), 1+len(cats)+len(tags))
	}
	firstCat, firstTag := -1, -1
	for i, r := range reqs {
		if r.Path == categoriesPath && firstCat < 0 {
			firstCat = i
		}
		if r.Path == tagsPath && firstTag < 0 {
			firstTag = i
		}
	}
	if firstCat > firstTag {
		t.Errorf("Inventory listed tags before categories; list categories first")
	}
}

func TestInventoryOmitsPageSizeWhenUnset(t *testing.T) {
	srv := mockvc.Start(mockvc.Options{DefaultPageSize: 4})
	defer srv.Close()

	c := loggedIn(t, srv, 0)
	rows, err := c.Inventory(context.Background())
	if err != nil {
		t.Fatalf("Inventory: %v", err)
	}
	assertRows(t, rows)

	reqs := srv.Requests()
	assertPageSequence(t, requestsFor(reqs, categoriesPath), 2, "", srv.IssuedSessionID())
	assertPageSequence(t, requestsFor(reqs, tagsPath), 3, "", srv.IssuedSessionID())
}

func TestInventoryUsesTagIDToBreakTies(t *testing.T) {
	data := mockvc.Dataset{
		Tags: []mockvc.TagRecord{
			{Tag: "tag-z", Info: mockvc.TagInfo{Name: "same", Category: "gone-z"}},
			{Tag: "tag-a", Info: mockvc.TagInfo{Name: "same", Category: "gone-a"}},
		},
	}
	srv := mockvc.Start(mockvc.Options{Dataset: &data, DefaultPageSize: 1})
	defer srv.Close()

	rows, err := loggedIn(t, srv, 1).Inventory(context.Background())
	if err != nil {
		t.Fatalf("Inventory: %v", err)
	}
	if len(rows) != 2 || rows[0].TagID != "tag-a" || rows[1].TagID != "tag-z" {
		t.Fatalf("Inventory tie order = %+v, want tag-a then tag-z", rows)
	}
}

func TestRenderUsesItsRowsInOrder(t *testing.T) {
	rows := []vctag.Row{
		{CategoryName: "zone", CategoryID: "ignored-c1", Cardinality: "MULTIPLE", TagName: "west", TagID: "tag-2"},
		{CategoryName: "Backup", CategoryID: "ignored-c2", Cardinality: "SINGLE", TagName: "Daily", TagID: "tag-1"},
	}
	if got, want := vctag.Render(nil), ""; got != want {
		t.Errorf("Render(nil) = %q, want empty", got)
	}
	if got, want := vctag.Render(rows), "zone\tMULTIPLE\twest\ttag-2\nBackup\tSINGLE\tDaily\ttag-1\n"; got != want {
		t.Errorf("Render(rows) = %q, want %q", got, want)
	}
}

func TestBaseURLWithTrailingSlashStillHitsExactPaths(t *testing.T) {
	srv := mockvc.Start(mockvc.Options{})
	defer srv.Close()

	c, err := vctag.New(vctag.Config{
		BaseURL:  srv.URL() + "/",
		Username: mockvc.DefaultUsername,
		Password: mockvc.DefaultPassword,
		PageSize: 3,
	})
	if err != nil {
		t.Fatalf("vctag.New: %v", err)
	}
	if err := c.Login(context.Background()); err != nil {
		t.Fatalf("Login: %v", err)
	}
	if _, err := c.Inventory(context.Background()); err != nil {
		t.Fatalf("Inventory: %v", err)
	}
	for _, r := range srv.Requests() {
		switch r.Path {
		case sessionPath, categoriesPath, tagsPath:
		default:
			t.Errorf("request path %q is not one of the contract paths", r.Path)
		}
	}
}

func TestListsReturnServerOrderBeforeTheJoin(t *testing.T) {
	srv := mockvc.Start(mockvc.Options{})
	defer srv.Close()

	c := loggedIn(t, srv, 2)
	inv := mockvc.Inventory()

	cats, err := c.ListCategories(context.Background())
	if err != nil {
		t.Fatalf("ListCategories: %v", err)
	}
	if len(cats) != len(inv.Categories) {
		t.Fatalf("ListCategories returned %d categories, want %d", len(cats), len(inv.Categories))
	}
	for i, want := range inv.Categories {
		got := cats[i]
		if got.ID != want.CategoryID || got.Name != want.Info.Name || got.Cardinality != want.Info.Cardinality {
			t.Errorf("category %d = %+v, want id %q name %q cardinality %q", i, got, want.CategoryID, want.Info.Name, want.Info.Cardinality)
		}
		if got.Description != want.Info.Description {
			t.Errorf("category %d description = %q, want %q", i, got.Description, want.Info.Description)
		}
		if strings.Join(got.AssociableTypes, ",") != strings.Join(want.Info.AssociableTypes, ",") {
			t.Errorf("category %d associable types = %v, want %v", i, got.AssociableTypes, want.Info.AssociableTypes)
		}
		if strings.Join(got.UsedBy, ",") != strings.Join(want.Info.UsedBy, ",") {
			t.Errorf("category %d used by = %v, want %v", i, got.UsedBy, want.Info.UsedBy)
		}
	}

	tags, err := c.ListTags(context.Background())
	if err != nil {
		t.Fatalf("ListTags: %v", err)
	}
	if len(tags) != len(inv.Tags) {
		t.Fatalf("ListTags returned %d tags, want %d", len(tags), len(inv.Tags))
	}
	for i, want := range inv.Tags {
		got := tags[i]
		if got.ID != want.Tag || got.Name != want.Info.Name || got.CategoryID != want.Info.Category {
			t.Errorf("tag %d = %+v, want id %q name %q category %q", i, got, want.Tag, want.Info.Name, want.Info.Category)
		}
		if got.Description != want.Info.Description {
			t.Errorf("tag %d description = %q, want %q", i, got.Description, want.Info.Description)
		}
		if strings.Join(got.UsedBy, ",") != strings.Join(want.Info.UsedBy, ",") {
			t.Errorf("tag %d used by = %v, want %v", i, got.UsedBy, want.Info.UsedBy)
		}
	}
}

func TestListsRequireASession(t *testing.T) {
	srv := mockvc.Start(mockvc.Options{})
	defer srv.Close()

	c := newClient(t, srv, 0)
	if _, err := c.ListTags(context.Background()); !errors.Is(err, vctag.ErrNoSession) {
		t.Errorf("ListTags without a session = %v, want ErrNoSession", err)
	}
	if _, err := c.ListCategories(context.Background()); !errors.Is(err, vctag.ErrNoSession) {
		t.Errorf("ListCategories without a session = %v, want ErrNoSession", err)
	}
	if _, err := c.Inventory(context.Background()); !errors.Is(err, vctag.ErrNoSession) {
		t.Errorf("Inventory without a session = %v, want ErrNoSession", err)
	}
	if err := c.Logout(context.Background()); !errors.Is(err, vctag.ErrNoSession) {
		t.Errorf("Logout without a session = %v, want ErrNoSession", err)
	}
	if n := len(srv.Requests()); n != 0 {
		t.Errorf("%d request(s) reached the server, want 0", n)
	}
}

func TestRepeatedMarkerDoesNotLoopForever(t *testing.T) {
	srv := mockvc.Start(mockvc.Options{RepeatMarker: true})
	defer srv.Close()

	c := loggedIn(t, srv, 2)
	_, err := c.ListTags(context.Background())
	if !errors.Is(err, vctag.ErrRepeatedMarker) {
		t.Fatalf("ListTags against a server that replays its marker = %v, want ErrRepeatedMarker", err)
	}
	if n := len(requestsFor(srv.Requests(), tagsPath)); n != 2 {
		t.Errorf("client sent %d tag pages before giving up, want 2 so it stops as soon as a requested marker repeats", n)
	}
}

func TestServerFaultsSurfaceAsAPIError(t *testing.T) {
	srv := mockvc.Start(mockvc.Options{TagsUnavailable: true})
	defer srv.Close()

	c := loggedIn(t, srv, 2)
	_, err := c.ListTags(context.Background())
	var apiErr *vctag.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("ListTags = %v, want a *vctag.APIError", err)
	}
	if apiErr.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("StatusCode = %d, want 503", apiErr.StatusCode)
	}
	if apiErr.ErrorType != "SERVICE_UNAVAILABLE" {
		t.Errorf("ErrorType = %q, want SERVICE_UNAVAILABLE", apiErr.ErrorType)
	}
	if apiErr.Op != "Vcenter.Tagging.Tags_list" {
		t.Errorf("Op = %q, want Vcenter.Tagging.Tags_list", apiErr.Op)
	}
	if apiErr.Message == "" {
		t.Error("Message is empty, want the default_message of the first localizable message")
	}
	if got, want := apiErr.Message, "the tagging service is not reachable from this vCenter Server"; got != want {
		t.Errorf("Message = %q, want first default_message %q", got, want)
	}
	if text := apiErr.Error(); !strings.Contains(text, "Vcenter.Tagging.Tags_list") || !strings.Contains(text, "503") {
		t.Errorf("Error() = %q, want it to name the operation and status", text)
	}
}

func TestBadCredentialsSurfaceAsAPIError(t *testing.T) {
	srv := mockvc.Start(mockvc.Options{})
	defer srv.Close()

	c, err := vctag.New(vctag.Config{
		BaseURL:  srv.URL(),
		Username: mockvc.DefaultUsername,
		Password: "wrong",
	})
	if err != nil {
		t.Fatalf("vctag.New: %v", err)
	}
	err = c.Login(context.Background())
	var apiErr *vctag.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("Login with bad credentials = %v, want a *vctag.APIError", err)
	}
	if apiErr.StatusCode != http.StatusUnauthorized || apiErr.Op != "Cis.Session_create" {
		t.Errorf("APIError = %+v, want 401 for Cis.Session_create", apiErr)
	}
	if c.SessionID() != "" {
		t.Errorf("SessionID() = %q after a failed login, want empty", c.SessionID())
	}
	if strings.Contains(apiErr.Error(), "wrong") {
		t.Errorf("Error() leaks the password: %q", apiErr.Error())
	}
}

func TestFailedReloginClearsAnExistingSession(t *testing.T) {
	srv := mockvc.Start(mockvc.Options{})
	defer srv.Close()

	transport := &rejectSecondLoginTransport{base: http.DefaultTransport}
	c, err := vctag.New(vctag.Config{
		BaseURL:    srv.URL(),
		Username:   mockvc.DefaultUsername,
		Password:   mockvc.DefaultPassword,
		HTTPClient: &http.Client{Transport: transport},
	})
	if err != nil {
		t.Fatalf("vctag.New: %v", err)
	}
	if err := c.Login(context.Background()); err != nil {
		t.Fatalf("first Login: %v", err)
	}
	if got := c.SessionID(); got == "" {
		t.Fatal("SessionID() after the successful login is empty")
	}

	err = c.Login(context.Background())
	var apiErr *vctag.APIError
	if !errors.As(err, &apiErr) || apiErr.StatusCode != http.StatusUnauthorized {
		t.Fatalf("second Login = %v, want a 401 *vctag.APIError", err)
	}
	if got := c.SessionID(); got != "" {
		t.Errorf("SessionID() = %q after a failed re-login, want empty", got)
	}
}

func TestContextCancellationStopsBeforeAnyRequest(t *testing.T) {
	srv := mockvc.Start(mockvc.Options{})
	defer srv.Close()

	c := loggedIn(t, srv, 2)
	before := len(srv.Requests())

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := c.Inventory(ctx); err == nil {
		t.Fatal("Inventory with a cancelled context = nil error, want an error")
	}
	if got := len(srv.Requests()); got != before {
		t.Errorf("%d request(s) were sent with a cancelled context, want 0", got-before)
	}
}

func TestDoubleServesOnlyTheContractOperations(t *testing.T) {
	srv := mockvc.Start(mockvc.Options{})
	defer srv.Close()
	serverURL, err := url.Parse(srv.URL())
	if err != nil {
		t.Fatalf("parse loopback URL: %v", err)
	}
	if got := serverURL.Hostname(); got != "127.0.0.1" {
		t.Fatalf("loopback double hostname = %q, want 127.0.0.1", got)
	}

	c := loggedIn(t, srv, 0)
	_ = c

	cases := []struct {
		name   string
		method string
		path   string
	}{
		{"associations list is out of contract", http.MethodGet, "/api/vcenter/tagging/associations"},
		{"session get is out of contract", http.MethodGet, sessionPath},
		{"vm list is out of contract", http.MethodGet, "/api/vcenter/vm"},
		{"categories create is out of contract", http.MethodPost, categoriesPath},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequestWithContext(context.Background(), tc.method, srv.URL()+tc.path, nil)
			if err != nil {
				t.Fatalf("build request: %v", err)
			}
			req.Header.Set("vmware-api-session-id", srv.IssuedSessionID())
			req.Header.Set("Accept", "application/json")
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatalf("do request: %v", err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != http.StatusNotFound {
				t.Fatalf("%s %s = %d, want 404", tc.method, tc.path, resp.StatusCode)
			}
			var body struct {
				ErrorType string `json:"error_type"`
				Messages  []struct {
					DefaultMessage string `json:"default_message"`
				} `json:"messages"`
			}
			if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
				t.Fatalf("decode fault: %v", err)
			}
			if body.ErrorType != "NOT_FOUND" || len(body.Messages) == 0 {
				t.Fatalf("fault body = %+v, want a NOT_FOUND Vapi.Std.Errors.Error", body)
			}
		})
	}
}

func TestVctagShipsTableDrivenTests(t *testing.T) {
	entries, err := os.ReadDir(filepath.Join("..", "vctag"))
	if err != nil {
		t.Fatalf("read vctag package directory: %v", err)
	}
	var (
		testFiles      int
		hasDrivenTable bool
		usesMock       bool
	)
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), "_test.go") {
			continue
		}
		testFiles++
		path := filepath.Join("..", "vctag", e.Name())
		file, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", e.Name(), err)
		}
		for _, imp := range file.Imports {
			if imp.Path.Value == `"example.com/vcf90/vsphere/internal/mockvc"` {
				usesMock = true
			}
		}
		for _, decl := range file.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok || fn.Body == nil || !strings.HasPrefix(fn.Name.Name, "Test") {
				continue
			}
			tables := map[string]bool{}
			ast.Inspect(fn.Body, func(node ast.Node) bool {
				assign, ok := node.(*ast.AssignStmt)
				if !ok {
					return true
				}
				for i, rhs := range assign.Rhs {
					lit, ok := rhs.(*ast.CompositeLit)
					if !ok || !isAnonymousStructSlice(lit.Type) || i >= len(assign.Lhs) {
						continue
					}
					if id, ok := assign.Lhs[i].(*ast.Ident); ok {
						tables[id.Name] = true
					}
				}
				return true
			})
			ast.Inspect(fn.Body, func(node ast.Node) bool {
				rangeStmt, ok := node.(*ast.RangeStmt)
				if !ok {
					return true
				}
				id, ok := rangeStmt.X.(*ast.Ident)
				if ok && tables[id.Name] && containsTRun(rangeStmt.Body) {
					hasDrivenTable = true
				}
				return true
			})
		}
	}
	if testFiles == 0 {
		t.Fatal("the vctag package ships no _test.go file")
	}
	if !hasDrivenTable {
		t.Error("the vctag tests are not table driven: expected a []struct{...} case table driven with t.Run")
	}
	if !usesMock {
		t.Error("the vctag tests do not exercise the contract pinned loopback double in internal/mockvc")
	}
}

func isAnonymousStructSlice(expr ast.Expr) bool {
	array, ok := expr.(*ast.ArrayType)
	if !ok || array.Len != nil {
		return false
	}
	_, ok = array.Elt.(*ast.StructType)
	return ok
}

func containsTRun(node ast.Node) bool {
	found := false
	ast.Inspect(node, func(node ast.Node) bool {
		call, ok := node.(*ast.CallExpr)
		if !ok {
			return true
		}
		sel, ok := call.Fun.(*ast.SelectorExpr)
		if ok && sel.Sel.Name == "Run" {
			found = true
			return false
		}
		return true
	})
	return found
}

func assertRows(t *testing.T, got []vctag.Row) {
	t.Helper()
	if len(got) != len(wantRows) {
		t.Fatalf("Inventory returned %d row(s), want %d: %+v", len(got), len(wantRows), got)
	}
	for i := range wantRows {
		if got[i] != wantRows[i] {
			t.Errorf("row %d =\n %+v\nwant\n %+v", i, got[i], wantRows[i])
		}
	}
}
