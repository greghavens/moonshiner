package verification_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"example.com/vcf-operations-networks-problem-collector/internal/contractmock"
	"example.com/vcf-operations-networks-problem-collector/vcfnetworks"
)

const (
	pinnedCommit = "c3f3b52c845dd967cabbc21680e893292077d5ba"
	specPath     = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
	specVersion  = "9.1.0.0"
	listOp       = "listProblemEvents"
	detailOp     = "getProblemEvent"
	testToken    = "local-contract-token"
	authValue    = "NetworkInsight " + testToken
)

// Fixture entity IDs. The collection is deliberately unsorted on the wire and
// one entity is repeated across an overlapping cursor window.
const (
	idNSXFirewall = "18230:35228:1832167524"
	idDuplicateA  = "18230:35230:1233393386"
	idVMotionB    = "18230:36050:1583312594"
	idDuplicateB  = "18230:35231:1010101010"
	idHighLatency = "18230:35229:1999999999"
	idAclChange   = "18230:36051:1111111111"
	idVMotionA    = "18230:35232:1222222222"
)

const (
	cursorPage2 = "MzA="
	cursorPage3 = "NjA="
)

func protectedPath(parts ...string) string {
	return filepath.Join(append([]string{"..", ".."}, parts...)...)
}

func pointer[T any](value T) *T { return &value }

// ---------------------------------------------------------------------------
// Fixture
// ---------------------------------------------------------------------------

type listEntry struct {
	EntityID   string `json:"entity_id"`
	EntityType string `json:"entity_type"`
	Time       int64  `json:"time"`
}

type listPage struct {
	Results    []listEntry `json:"results"`
	Cursor     string      `json:"cursor,omitempty"`
	TotalCount int         `json:"total_count"`
	StartTime  int64       `json:"start_time"`
	EndTime    int64       `json:"end_time"`
}

func entry(id string, at int64) listEntry {
	return listEntry{EntityID: id, EntityType: "ProblemEvent", Time: at}
}

// pagesByCursor maps the incoming cursor value to the page it must serve. The
// empty key is the first request, which carries no cursor at all.
func pagesByCursor() map[string]listPage {
	return map[string]listPage{
		"": {
			Results:    []listEntry{entry(idVMotionB, 1509283820), entry(idNSXFirewall, 1509283821), entry(idHighLatency, 1509283822)},
			Cursor:     cursorPage2,
			TotalCount: 7,
			StartTime:  1509231996,
			EndTime:    1509318396,
		},
		cursorPage2: {
			// idHighLatency repeats: cursor windows may overlap.
			Results:    []listEntry{entry(idHighLatency, 1509283822), entry(idDuplicateA, 1509283823), entry(idVMotionA, 1509283824)},
			Cursor:     cursorPage3,
			TotalCount: 7,
			StartTime:  1509231996,
			EndTime:    1509318396,
		},
		cursorPage3: {
			Results:    []listEntry{entry(idDuplicateB, 1509283825), entry(idAclChange, 1509283826)},
			TotalCount: 7,
			StartTime:  1509231996,
			EndTime:    1509318396,
		},
	}
}

func detailFixture() map[string]vcfnetworks.ProblemEvent {
	event := func(id, name, severity string) vcfnetworks.ProblemEvent {
		return vcfnetworks.ProblemEvent{
			EntityID:         id,
			Name:             name,
			EntityType:       "ProblemEvent",
			Message:          "fixture message for " + name,
			EventType:        "UserDefinedProblemEvent",
			EventTags:        []string{"Best Practices", "Firewall"},
			AdminState:       pointer("ENABLED"),
			Archived:         false,
			EventTimeEpochMs: 1509283819834,
			Severity:         pointer(severity),
		}
	}
	return map[string]vcfnetworks.ProblemEvent{
		idNSXFirewall: event(idNSXFirewall, "NSXFirewallDefaultAllowAllRulesEvent", "INFO"),
		idDuplicateA:  event(idDuplicateA, "DuplicateIPEvent", "CRITICAL"),
		idVMotionB:    event(idVMotionB, "VMotionEvent", "MODERATE"),
		idDuplicateB:  event(idDuplicateB, "DuplicateIPEvent", "WARNING"),
		idHighLatency: event(idHighLatency, "HighLatencyEvent", "WARNING"),
		idAclChange:   event(idAclChange, "AclChangeEvent", "INFO"),
		idVMotionA:    event(idVMotionA, "VMotionEvent", "CRITICAL"),
	}
}

// firstAppearanceOrder is the order in which distinct entities appear across
// the paged fixture.
var firstAppearanceOrder = []string{
	idVMotionB, idNSXFirewall, idHighLatency, idDuplicateA, idVMotionA, idDuplicateB, idAclChange,
}

// wantOrdered is the fixture sorted by Name then EntityID.
func wantOrdered() []vcfnetworks.ProblemEvent {
	details := detailFixture()
	order := []string{idAclChange, idDuplicateA, idDuplicateB, idHighLatency, idNSXFirewall, idVMotionA, idVMotionB}
	events := make([]vcfnetworks.ProblemEvent, 0, len(order))
	for _, id := range order {
		events = append(events, details[id])
	}
	return events
}

// ---------------------------------------------------------------------------
// Mock wiring
// ---------------------------------------------------------------------------

type responderSet struct {
	listFor   func(cursor string, request contractmock.Request) (contractmock.Response, bool)
	detailFor func(id string, request contractmock.Request) (contractmock.Response, bool)
}

func startMock(t *testing.T, overrides responderSet) *contractmock.Server {
	t.Helper()
	pages := pagesByCursor()
	details := detailFixture()

	return contractmock.New(t, protectedPath("docs", "contract.json"), map[string]contractmock.Responder{
		listOp: func(request contractmock.Request) contractmock.Response {
			query, err := request.Query()
			if err != nil {
				t.Errorf("mock could not parse query %q: %v", request.RawQuery, err)
				return contractmock.JSONResponse(t, http.StatusBadRequest, map[string]any{"code": 400})
			}
			cursor := query.Get("cursor")
			if overrides.listFor != nil {
				if response, ok := overrides.listFor(cursor, request); ok {
					return response
				}
			}
			page, ok := pages[cursor]
			if !ok {
				t.Errorf("mock received unknown cursor %q", cursor)
				return contractmock.JSONResponse(t, http.StatusBadRequest,
					map[string]any{"code": 400, "message": "unknown cursor"})
			}
			return contractmock.JSONResponse(t, http.StatusOK, page)
		},
		detailOp: func(request contractmock.Request) contractmock.Response {
			id := request.PathParams["id"]
			if overrides.detailFor != nil {
				if response, ok := overrides.detailFor(id, request); ok {
					return response
				}
			}
			event, ok := details[id]
			if !ok {
				return contractmock.JSONResponse(t, http.StatusNotFound,
					map[string]any{"code": 404, "message": "no such problem event"})
			}
			return contractmock.JSONResponse(t, http.StatusOK, event)
		},
	})
}

func newClient(t *testing.T, server *contractmock.Server) *vcfnetworks.Client {
	t.Helper()
	client, err := vcfnetworks.NewClient(server.URL(), testToken, server.Client())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if client == nil {
		t.Fatal("NewClient returned a nil client and a nil error")
	}
	return client
}

// ---------------------------------------------------------------------------
// Provenance and mock surface
// ---------------------------------------------------------------------------

func TestPinnedSpecificationProvenanceAndMockSurface(t *testing.T) {
	t.Parallel()

	var sources struct {
		Repository   string   `json:"repository"`
		License      string   `json:"license"`
		CommitSHA    string   `json:"commitSha"`
		SpecPath     string   `json:"specPath"`
		SpecVersion  string   `json:"specVersion"`
		SourceKind   string   `json:"sourceKind"`
		OperationIDs []string `json:"operationIds"`
		Operations   []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
			BasePath    string `json:"basePath"`
			CommitSHA   string `json:"commitSha"`
			SpecPath    string `json:"specPath"`
			Source      string `json:"source"`
		} `json:"operations"`
	}
	data, err := os.ReadFile(protectedPath("docs", "official_sources.json"))
	if err != nil {
		t.Fatalf("read official sources: %v", err)
	}
	if err := json.Unmarshal(data, &sources); err != nil {
		t.Fatalf("decode official sources: %v", err)
	}
	if sources.Repository != "https://github.com/vmware/vcf-api-specs" ||
		sources.License != "Apache-2.0" || sources.CommitSHA != pinnedCommit ||
		sources.SpecPath != specPath || sources.SpecVersion != specVersion ||
		sources.SourceKind != "OpenAPI specification" {
		t.Fatalf("unexpected source provenance: %+v", sources)
	}
	if !reflect.DeepEqual(sources.OperationIDs, []string{listOp, detailOp}) {
		t.Fatalf("official sources name operations %v", sources.OperationIDs)
	}

	wantRoutes := map[string]struct{ method, path string }{
		listOp:   {http.MethodGet, "/entities/problems"},
		detailOp: {http.MethodGet, "/entities/problems/{id}"},
	}
	if len(sources.Operations) != len(wantRoutes) {
		t.Fatalf("official sources record %d operations", len(sources.Operations))
	}
	for _, op := range sources.Operations {
		want, ok := wantRoutes[op.OperationID]
		if !ok {
			t.Fatalf("unexpected recorded operation %q", op.OperationID)
		}
		if op.Method != want.method || op.Path != want.path || op.BasePath != "/api/ni" {
			t.Fatalf("operation %s recorded as %s %s%s", op.OperationID, op.Method, op.BasePath, op.Path)
		}
		if op.CommitSHA != pinnedCommit || op.SpecPath != specPath {
			t.Fatalf("operation %s is not pinned to the specification revision: %+v", op.OperationID, op)
		}
		if !strings.Contains(op.Source, pinnedCommit+"/"+specPath) {
			t.Fatalf("operation %s source %q is not the pinned specification blob", op.OperationID, op.Source)
		}
	}

	var served atomic.Int32
	count := func(contractmock.Request) contractmock.Response {
		served.Add(1)
		return contractmock.JSONResponse(t, http.StatusOK, listPage{Results: []listEntry{}, TotalCount: 0})
	}
	server := contractmock.New(t, protectedPath("docs", "contract.json"), map[string]contractmock.Responder{
		listOp: count, detailOp: count,
	})

	if got := server.OperationIDs(); !reflect.DeepEqual(got, []string{detailOp, listOp}) {
		t.Fatalf("mock exposes operations %v", got)
	}
	if server.BasePath() != "/api/ni" {
		t.Fatalf("mock base path is %q", server.BasePath())
	}
	for id, want := range wantRoutes {
		method, path, ok := server.Route(id)
		if !ok || method != want.method || path != "/api/ni"+want.path {
			t.Fatalf("mock route for %s is %s %s (ok=%v)", id, method, path, ok)
		}
	}
	if parsed, err := url.Parse(server.URL()); err != nil || parsed.Hostname() != "127.0.0.1" {
		t.Fatalf("mock is not IPv4 loopback-only: %q (%v)", server.URL(), err)
	}

	// An operation the contract does not name is not served at all.
	for _, absent := range []string{"/api/ni/entities/vms", "/entities/problems", "/api/ni/entities/problems/a/b"} {
		response, err := server.Client().Get(server.URL() + absent)
		if err != nil {
			t.Fatalf("call %s: %v", absent, err)
		}
		_ = response.Body.Close()
		if response.StatusCode != http.StatusNotFound {
			t.Fatalf("route %s absent from the contract returned %d", absent, response.StatusCode)
		}
	}
	if served.Load() != 0 {
		t.Fatal("the mock served an operation the contract does not name")
	}
	if len(server.Requests()) != 3 {
		t.Fatalf("request log captured %d rejected routes", len(server.Requests()))
	}
}

// ---------------------------------------------------------------------------
// Complete retrieval, stable order and exact wire shape
// ---------------------------------------------------------------------------

func TestCollectProblemEventsWireShapeAndStableOrder(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name             string
		options          vcfnetworks.CollectOptions
		wantListTargets  []string
		wantDetailSuffix string
	}{
		{
			name:    "unset options are omitted entirely",
			options: vcfnetworks.CollectOptions{Size: 3},
			wantListTargets: []string{
				"/api/ni/entities/problems?size=3",
				"/api/ni/entities/problems?cursor=MzA%3D&size=3",
				"/api/ni/entities/problems?cursor=NjA%3D&size=3",
			},
			wantDetailSuffix: "",
		},
		{
			name: "explicitly supplied zero and default values are preserved",
			options: vcfnetworks.CollectOptions{
				Size:           3,
				StartTime:      pointer(int64(0)),
				EndTime:        pointer(int64(1509318396)),
				EventType:      pointer("UserDefinedProblemEvent"),
				EventTags:      []string{"Best Practices", "Firewall"},
				EventStatus:    pointer("all"),
				UpdateTimeFrom: pointer(int64(0)),
				UpdateTimeTo:   pointer(int64(1509318396)),
				EventSeverity:  []string{"Critical", "Info"},
				Managers:       []string{"18230:7:824494449"},
				DetailTime:     pointer(int64(0)),
			},
			wantListTargets: []string{
				"/api/ni/entities/problems?" + allFilters(""),
				"/api/ni/entities/problems?" + allFilters(cursorPage2),
				"/api/ni/entities/problems?" + allFilters(cursorPage3),
			},
			wantDetailSuffix: "?time=0",
		},
	}

	for _, testCase := range cases {
		testCase := testCase
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()
			server := startMock(t, responderSet{})
			client := newClient(t, server)

			got, err := client.CollectProblemEvents(context.Background(), testCase.options)
			if err != nil {
				t.Fatalf("CollectProblemEvents: %v", err)
			}
			if want := wantOrdered(); !reflect.DeepEqual(got, want) {
				t.Fatalf("collection mismatch\n got: %s\nwant: %s", summarize(got), summarize(want))
			}

			listRequests := server.RequestsFor(listOp)
			if len(listRequests) != len(testCase.wantListTargets) {
				t.Fatalf("made %d %s requests, want %d", len(listRequests), listOp, len(testCase.wantListTargets))
			}
			for i, request := range listRequests {
				if request.RequestURI != testCase.wantListTargets[i] {
					t.Fatalf("%s request %d target\n got: %s\nwant: %s",
						listOp, i, request.RequestURI, testCase.wantListTargets[i])
				}
			}

			detailRequests := server.RequestsFor(detailOp)
			if len(detailRequests) != len(firstAppearanceOrder) {
				t.Fatalf("made %d %s requests, want %d (one per distinct entity)",
					len(detailRequests), detailOp, len(firstAppearanceOrder))
			}
			for i, request := range detailRequests {
				wantID := firstAppearanceOrder[i]
				if request.PathParams["id"] != wantID {
					t.Fatalf("%s request %d resolved id %q, want %q",
						detailOp, i, request.PathParams["id"], wantID)
				}
				wantTarget := "/api/ni/entities/problems/" + wantID + testCase.wantDetailSuffix
				if request.RequestURI != wantTarget {
					t.Fatalf("%s request %d target\n got: %s\nwant: %s",
						detailOp, i, request.RequestURI, wantTarget)
				}
			}

			for i, request := range server.Requests() {
				assertBodylessGET(t, i, request)
			}
		})
	}
}

// allFilters renders the fully populated filter set in url.Values.Encode order.
func allFilters(cursor string) string {
	values := url.Values{}
	values.Set("size", "3")
	if cursor != "" {
		values.Set("cursor", cursor)
	}
	values.Set("start_time", "0")
	values.Set("end_time", "1509318396")
	values.Set("event_type", "UserDefinedProblemEvent")
	values["event_tags"] = []string{"Best Practices", "Firewall"}
	values.Set("event_status", "all")
	values.Set("update_time_from", "0")
	values.Set("update_time_to", "1509318396")
	values["event_severity"] = []string{"Critical", "Info"}
	values["managers"] = []string{"18230:7:824494449"}
	return values.Encode()
}

func assertBodylessGET(t *testing.T, index int, request contractmock.Request) {
	t.Helper()
	if request.Method != http.MethodGet {
		t.Fatalf("request %d used method %s", index, request.Method)
	}
	if got := request.Header.Values("Authorization"); len(got) != 1 || got[0] != authValue {
		t.Fatalf("request %d Authorization headers = %q, want exactly one %q", index, got, authValue)
	}
	if got := request.Header.Values("Accept"); len(got) != 1 || got[0] != "application/json" {
		t.Fatalf("request %d Accept headers = %q", index, got)
	}
	if got := request.Header.Values("Content-Type"); len(got) != 0 {
		t.Fatalf("bodyless request %d carried Content-Type %q", index, got)
	}
	if len(request.Body) != 0 {
		t.Fatalf("bodyless request %d carried a %d byte body", index, len(request.Body))
	}
	if request.ContentLength > 0 {
		t.Fatalf("bodyless request %d declared Content-Length %d", index, request.ContentLength)
	}
	if len(request.TransferEncoding) != 0 {
		t.Fatalf("bodyless request %d used transfer encoding %v", index, request.TransferEncoding)
	}
	if strings.HasSuffix(request.RequestURI, "?") {
		t.Fatalf("request %d emitted a bare query delimiter: %s", index, request.RequestURI)
	}
	if strings.Contains(request.RequestURI, "%00") {
		t.Fatalf("request %d target contains a NUL escape: %s", index, request.RequestURI)
	}
	query, err := url.ParseQuery(request.RawQuery)
	if err != nil {
		t.Fatalf("request %d has an unparseable query %q: %v", index, request.RawQuery, err)
	}
	for name, values := range query {
		for _, value := range values {
			if value == "" {
				t.Fatalf("request %d sent optional field %q as an empty value: %s",
					index, name, request.RequestURI)
			}
		}
	}
}

func summarize(events []vcfnetworks.ProblemEvent) string {
	parts := make([]string, 0, len(events))
	for _, event := range events {
		parts = append(parts, event.Name+"/"+event.EntityID)
	}
	return "[" + strings.Join(parts, " ") + "]"
}

// ---------------------------------------------------------------------------
// Failures never yield a partial collection
// ---------------------------------------------------------------------------

func TestCollectProblemEventsFailuresReturnNoPartialResults(t *testing.T) {
	t.Parallel()

	badPage := func(page listPage) func(string, contractmock.Request) (contractmock.Response, bool) {
		return func(cursor string, _ contractmock.Request) (contractmock.Response, bool) {
			if cursor != cursorPage2 {
				return contractmock.Response{}, false
			}
			return contractmock.JSONResponse(t, http.StatusOK, page), true
		}
	}

	cases := []struct {
		name           string
		overrides      responderSet
		wantAPI        bool
		wantAPIStatus  int
		wantAPICode    int
		wantAPIMessage string
		wantProto      bool
	}{
		{
			name: "second page fails with an ApiError",
			overrides: responderSet{listFor: func(cursor string, _ contractmock.Request) (contractmock.Response, bool) {
				if cursor != cursorPage2 {
					return contractmock.Response{}, false
				}
				return contractmock.JSONResponse(t, http.StatusInternalServerError,
					map[string]any{"code": 500, "message": "collector unavailable"}), true
			}},
			wantAPI:        true,
			wantAPIStatus:  http.StatusInternalServerError,
			wantAPICode:    500,
			wantAPIMessage: "collector unavailable",
		},
		{
			name:      "total_count changes between pages",
			overrides: responderSet{listFor: badPage(listPage{Results: []listEntry{entry(idDuplicateA, 1), entry(idVMotionA, 2), entry(idDuplicateB, 3)}, Cursor: cursorPage3, TotalCount: 9})},
			wantProto: true,
		},
		{
			name:      "a non-final page is not full",
			overrides: responderSet{listFor: badPage(listPage{Results: []listEntry{entry(idDuplicateA, 1)}, Cursor: cursorPage3, TotalCount: 7})},
			wantProto: true,
		},
		{
			name:      "the server repeats a cursor",
			overrides: responderSet{listFor: badPage(listPage{Results: []listEntry{entry(idDuplicateA, 1), entry(idVMotionA, 2), entry(idDuplicateB, 3)}, Cursor: cursorPage2, TotalCount: 7})},
			wantProto: true,
		},
		{
			name: "the server sends a null cursor instead of omitting it",
			overrides: responderSet{listFor: func(cursor string, _ contractmock.Request) (contractmock.Response, bool) {
				switch cursor {
				case "":
					return contractmock.JSONResponse(t, http.StatusOK, listPage{
						Results: []listEntry{entry(idVMotionB, 1), entry(idNSXFirewall, 2), entry(idHighLatency, 3)},
						Cursor:  cursorPage2, TotalCount: 6,
					}), true
				case cursorPage2:
					return contractmock.JSONResponse(t, http.StatusOK, map[string]any{
						"results":     []listEntry{entry(idDuplicateA, 4), entry(idVMotionA, 5), entry(idDuplicateB, 6)},
						"cursor":      nil,
						"total_count": 6,
					}), true
				default:
					return contractmock.Response{}, false
				}
			}},
			wantProto: true,
		},
		{
			name:      "results is null",
			overrides: responderSet{listFor: badPage(listPage{Results: nil, Cursor: cursorPage3, TotalCount: 7})},
			wantProto: true,
		},
		{
			name:      "total_count is negative",
			overrides: responderSet{listFor: badPage(listPage{Results: []listEntry{entry(idDuplicateA, 1), entry(idVMotionA, 2), entry(idDuplicateB, 3)}, Cursor: cursorPage3, TotalCount: -1})},
			wantProto: true,
		},
		{
			name: "total_count is omitted",
			overrides: responderSet{listFor: func(cursor string, _ contractmock.Request) (contractmock.Response, bool) {
				if cursor != cursorPage2 {
					return contractmock.Response{}, false
				}
				return contractmock.JSONResponse(t, http.StatusOK, map[string]any{
					"results": []listEntry{entry(idDuplicateA, 1), entry(idVMotionA, 2), entry(idDuplicateB, 3)},
					"cursor":  cursorPage3,
				}), true
			}},
			wantProto: true,
		},
		{
			name:      "a page overshoots the requested size",
			overrides: responderSet{listFor: badPage(listPage{Results: []listEntry{entry(idDuplicateA, 1), entry(idVMotionA, 2), entry(idDuplicateB, 3), entry(idAclChange, 4)}, Cursor: cursorPage3, TotalCount: 7})},
			wantProto: true,
		},
		{
			name: "a result carries the wrong entity_type",
			overrides: responderSet{listFor: badPage(listPage{
				Results:    []listEntry{{EntityID: idDuplicateA, EntityType: "VirtualMachine", Time: 1}, entry(idVMotionA, 2), entry(idDuplicateB, 3)},
				Cursor:     cursorPage3,
				TotalCount: 7,
			})},
			wantProto: true,
		},
		{
			name: "a result carries a blank entity_id",
			overrides: responderSet{listFor: badPage(listPage{
				Results:    []listEntry{entry("   ", 1), entry(idVMotionA, 2), entry(idDuplicateB, 3)},
				Cursor:     cursorPage3,
				TotalCount: 7,
			})},
			wantProto: true,
		},
		{
			name: "a page body has trailing malformed JSON",
			overrides: responderSet{listFor: func(cursor string, _ contractmock.Request) (contractmock.Response, bool) {
				if cursor != cursorPage2 {
					return contractmock.Response{}, false
				}
				page := pagesByCursor()[cursor]
				response := contractmock.JSONResponse(t, http.StatusOK, page)
				response.Body = append(response.Body, []byte(`{"unexpected":`)...)
				return response, true
			}},
			wantProto: true,
		},
		{
			name: "the final page leaves the collection short of total_count",
			overrides: responderSet{listFor: func(cursor string, _ contractmock.Request) (contractmock.Response, bool) {
				if cursor != cursorPage3 {
					return contractmock.Response{}, false
				}
				return contractmock.JSONResponse(t, http.StatusOK,
					listPage{Results: []listEntry{entry(idDuplicateB, 1509283825)}, TotalCount: 7}), true
			}},
			wantProto: true,
		},
		{
			name: "the collection overshoots total_count",
			overrides: responderSet{listFor: func(cursor string, _ contractmock.Request) (contractmock.Response, bool) {
				page, ok := pagesByCursor()[cursor]
				if !ok {
					return contractmock.Response{}, false
				}
				page.TotalCount = 2
				return contractmock.JSONResponse(t, http.StatusOK, page), true
			}},
			wantProto: true,
		},
		{
			name: "a detail lookup is not found",
			overrides: responderSet{detailFor: func(id string, _ contractmock.Request) (contractmock.Response, bool) {
				if id != idHighLatency {
					return contractmock.Response{}, false
				}
				return contractmock.JSONResponse(t, http.StatusNotFound,
					map[string]any{"code": 404, "message": "no such problem event"}), true
			}},
			wantAPI:        true,
			wantAPIStatus:  http.StatusNotFound,
			wantAPICode:    404,
			wantAPIMessage: "no such problem event",
		},
		{
			name: "a detail body answers a different entity",
			overrides: responderSet{detailFor: func(id string, _ contractmock.Request) (contractmock.Response, bool) {
				if id != idAclChange {
					return contractmock.Response{}, false
				}
				swapped := detailFixture()[idVMotionA]
				return contractmock.JSONResponse(t, http.StatusOK, swapped), true
			}},
			wantProto: true,
		},
		{
			name: "a detail body omits the name",
			overrides: responderSet{detailFor: func(id string, _ contractmock.Request) (contractmock.Response, bool) {
				if id != idAclChange {
					return contractmock.Response{}, false
				}
				event := detailFixture()[id]
				event.Name = ""
				return contractmock.JSONResponse(t, http.StatusOK, event), true
			}},
			wantProto: true,
		},
		{
			name: "a detail body carries the wrong entity_type",
			overrides: responderSet{detailFor: func(id string, _ contractmock.Request) (contractmock.Response, bool) {
				if id != idAclChange {
					return contractmock.Response{}, false
				}
				event := detailFixture()[id]
				event.EntityType = "VirtualMachine"
				return contractmock.JSONResponse(t, http.StatusOK, event), true
			}},
			wantProto: true,
		},
		{
			name: "a detail body is not JSON",
			overrides: responderSet{detailFor: func(id string, _ contractmock.Request) (contractmock.Response, bool) {
				if id != idVMotionB {
					return contractmock.Response{}, false
				}
				return contractmock.Response{
					Status: http.StatusOK, ContentType: "text/plain", Body: []byte("not json"),
				}, true
			}},
			wantProto: true,
		},
	}

	for _, testCase := range cases {
		testCase := testCase
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()
			server := startMock(t, testCase.overrides)
			client := newClient(t, server)

			got, err := client.CollectProblemEvents(context.Background(), vcfnetworks.CollectOptions{Size: 3})
			if err == nil {
				t.Fatalf("expected a failure, got %d events", len(got))
			}
			if got != nil {
				t.Fatalf("failure returned %d partial results", len(got))
			}
			var apiError *vcfnetworks.APIError
			var protocolError *vcfnetworks.ProtocolError
			switch {
			case testCase.wantAPI:
				if !errors.As(err, &apiError) {
					t.Fatalf("want *APIError, got %T: %v", err, err)
				}
				if apiError.StatusCode != testCase.wantAPIStatus || apiError.Code != testCase.wantAPICode || apiError.Message != testCase.wantAPIMessage {
					t.Fatalf("APIError = %+v, want status=%d code=%d message=%q",
						apiError, testCase.wantAPIStatus, testCase.wantAPICode, testCase.wantAPIMessage)
				}
			case testCase.wantProto:
				if !errors.As(err, &protocolError) {
					t.Fatalf("want *ProtocolError, got %T: %v", err, err)
				}
				if strings.TrimSpace(protocolError.Reason) == "" {
					t.Fatal("ProtocolError carried no reason")
				}
			}
			if strings.Contains(err.Error(), testToken) {
				t.Fatal("error message disclosed the API token")
			}
		})
	}
}

// ---------------------------------------------------------------------------
// Input validation
// ---------------------------------------------------------------------------

func TestNewClientRejectsUnusableInput(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name    string
		baseURL string
		token   string
	}{
		{"empty service root", "", testToken},
		{"non http scheme", "ftp://appliance.example.com", testToken},
		{"missing host", "http://", testToken},
		{"service root carries a query", "http://appliance.example.com?a=b", testToken},
		{"service root carries an empty query", "http://appliance.example.com?", testToken},
		{"service root carries a fragment", "http://appliance.example.com#part", testToken},
		{"blank token", "http://appliance.example.com", "   "},
		{"token with a newline", "http://appliance.example.com", "abc\ndef"},
		{"token with a carriage return", "http://appliance.example.com", "abc\rdef"},
		{"token with a tab", "http://appliance.example.com", "abc\tdef"},
	}

	for _, testCase := range cases {
		testCase := testCase
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()
			client, err := vcfnetworks.NewClient(testCase.baseURL, testCase.token, nil)
			if err == nil {
				t.Fatal("expected NewClient to reject the input")
			}
			if client != nil {
				t.Fatal("NewClient returned a client alongside an error")
			}
			if strings.Contains(err.Error(), testCase.token) && strings.TrimSpace(testCase.token) != "" {
				t.Fatal("error message disclosed the API token")
			}
		})
	}
}

func TestNewClientUsesDefaultHTTPClient(t *testing.T) {
	t.Parallel()

	server := startMock(t, responderSet{})
	root := strings.Replace(server.URL(), "http://", "HTTP://", 1)
	client, err := vcfnetworks.NewClient(root, testToken, nil)
	if err != nil {
		t.Fatalf("NewClient with an HTTP service root and nil client: %v", err)
	}
	got, err := client.CollectProblemEvents(context.Background(), vcfnetworks.CollectOptions{Size: 3})
	if err != nil {
		t.Fatalf("CollectProblemEvents through http.DefaultClient: %v", err)
	}
	if want := wantOrdered(); !reflect.DeepEqual(got, want) {
		t.Fatalf("collection mismatch\n got: %s\nwant: %s", summarize(got), summarize(want))
	}
}

func TestCollectProblemEventsRejectsUnusableOptions(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name    string
		options vcfnetworks.CollectOptions
		nilCtx  bool
	}{
		{name: "zero page size", options: vcfnetworks.CollectOptions{Size: 0}},
		{name: "negative page size", options: vcfnetworks.CollectOptions{Size: -1}},
		{name: "page size above the accepted maximum", options: vcfnetworks.CollectOptions{Size: 1001}},
		{name: "event status outside the enum", options: vcfnetworks.CollectOptions{Size: 3, EventStatus: pointer("resolved")}},
		{name: "severity outside the enum", options: vcfnetworks.CollectOptions{Size: 3, EventSeverity: []string{"Fatal"}}},
		{name: "empty event tag", options: vcfnetworks.CollectOptions{Size: 3, EventTags: []string{""}}},
		{name: "empty manager", options: vcfnetworks.CollectOptions{Size: 3, Managers: []string{""}}},
		{name: "empty event type", options: vcfnetworks.CollectOptions{Size: 3, EventType: pointer("")}},
		{name: "negative start time", options: vcfnetworks.CollectOptions{Size: 3, StartTime: pointer(int64(-1))}},
		{name: "negative end time", options: vcfnetworks.CollectOptions{Size: 3, EndTime: pointer(int64(-1))}},
		{name: "negative update time from", options: vcfnetworks.CollectOptions{Size: 3, UpdateTimeFrom: pointer(int64(-1))}},
		{name: "negative update time to", options: vcfnetworks.CollectOptions{Size: 3, UpdateTimeTo: pointer(int64(-1))}},
		{name: "negative detail time", options: vcfnetworks.CollectOptions{Size: 3, DetailTime: pointer(int64(-1))}},
		{name: "nil context", options: vcfnetworks.CollectOptions{Size: 3}, nilCtx: true},
	}

	for _, testCase := range cases {
		testCase := testCase
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()
			server := startMock(t, responderSet{})
			client := newClient(t, server)

			ctx := context.Background()
			if testCase.nilCtx {
				ctx = nil
			}
			got, err := client.CollectProblemEvents(ctx, testCase.options)
			if err == nil {
				t.Fatalf("expected rejection, got %d events", len(got))
			}
			if got != nil {
				t.Fatal("rejected call returned results")
			}
			if len(server.Requests()) != 0 {
				t.Fatalf("rejected call still issued %d requests", len(server.Requests()))
			}
		})
	}
}
