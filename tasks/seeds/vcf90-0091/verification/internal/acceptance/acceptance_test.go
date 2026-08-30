package acceptance

import (
	"context"
	"encoding/json"
	"errors"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	vcflogs "example.com/vcfopslogs"
	"example.com/vcfopslogs/mocklogs"
)

const (
	specPath = "specifications/vcf-operations/vcf-operations-for-logs-openapi.json"
	fullSHA  = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
)

func rootPath(parts ...string) string {
	return filepath.Join(append([]string{"..", ".."}, parts...)...)
}

func readJSON(t *testing.T, path string) any {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var value any
	if err := json.Unmarshal(b, &value); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
	return value
}

func TestContractIsExactSpecExtract(t *testing.T) {
	source := readJSON(t, rootPath(specPath))
	contract := readJSON(t, rootPath("docs", "contract.json"))
	if !reflect.DeepEqual(source, contract) {
		t.Fatal("docs/contract.json is not the focused extract of the pinned 9.0 specification")
	}

	root := contract.(map[string]any)
	paths := root["paths"].(map[string]any)
	want := map[string]string{
		"/deployment/join":             "POST_deployment-join",
		"/deployment/waitUntilStarted": "POST_deployment-waitUntilStarted",
	}
	if len(paths) != len(want) {
		t.Fatalf("contract has %d paths, want %d", len(paths), len(want))
	}
	for path, operationID := range want {
		item, ok := paths[path].(map[string]any)
		if !ok || len(item) != 1 {
			t.Fatalf("path %s is missing or has extra methods", path)
		}
		post := item["post"].(map[string]any)
		if got := post["operationId"]; got != operationID {
			t.Fatalf("%s operationId = %v, want %s", path, got, operationID)
		}
	}
}

func TestOfficialSources(t *testing.T) {
	got := readJSON(t, rootPath("docs", "official_sources.json")).(map[string]any)
	wantScalars := map[string]string{
		"repository": "vmware/vcf-api-specs",
		"license":    "Apache-2.0",
		"spec_path":  specPath,
		"tag":        "9.0.0.0",
		"commit_sha": fullSHA,
	}
	for key, want := range wantScalars {
		if got[key] != want {
			t.Errorf("%s = %v, want %q", key, got[key], want)
		}
	}
	ids, ok := got["operation_ids"].([]any)
	if !ok || len(ids) != 2 || !sameStrings(ids, []string{"POST_deployment-join", "POST_deployment-waitUntilStarted"}) {
		t.Errorf("operation_ids = %#v", got["operation_ids"])
	}
}

func TestJoinWireAndPolling(t *testing.T) {
	tests := []struct {
		name     string
		req      vcflogs.JoinRequest
		wantBody map[string]any
	}{
		{
			name:     "minimal omits optional fields",
			req:      vcflogs.JoinRequest{MasterFQDN: "li-01.example.com"},
			wantBody: map[string]any{"masterFQDN": "li-01.example.com"},
		},
		{
			name: "populated fields are present",
			req: vcflogs.JoinRequest{
				MasterFQDN: "li-02.example.com",
				MasterPort: intPtr(9543),
				AcceptCert: boolPtr(true),
			},
			wantBody: map[string]any{"masterFQDN": "li-02.example.com", "masterPort": float64(9543), "acceptCert": true},
		},
		{
			name: "explicit false is present",
			req: vcflogs.JoinRequest{
				MasterFQDN: "li-03.example.com",
				AcceptCert: boolPtr(false),
			},
			wantBody: map[string]any{"masterFQDN": "li-03.example.com", "acceptCert": false},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			server, err := mocklogs.New(http.StatusInternalServerError, http.StatusInternalServerError, http.StatusOK)
			if err != nil {
				t.Fatal(err)
			}
			defer server.Close()

			client, err := vcflogs.NewClient(server.URL(), server.Client())
			if err != nil {
				t.Fatal(err)
			}
			got, err := client.JoinAndWait(context.Background(), tc.req, 0)
			if err != nil {
				t.Fatal(err)
			}
			wantResponse := vcflogs.JoinResponse{
				MasterAddress: "10.0.0.123",
				WorkerAddress: "10.0.0.124",
				WorkerPort:    16520,
				WorkerToken:   "0ae94cb9-550a-4c01-85b9-3b7095e92321",
				MasterUIPort:  80,
			}
			if !reflect.DeepEqual(got, wantResponse) {
				t.Fatalf("JoinAndWait response = %#v, want %#v", got, wantResponse)
			}

			requests := server.Requests()
			if len(requests) != 4 {
				t.Fatalf("request count = %d, want join plus three polls", len(requests))
			}
			assertRequest(t, requests[0], http.MethodPost, "/api/v2/deployment/join")
			if requests[0].Header.Get("Content-Type") != "application/json" {
				t.Errorf("join Content-Type = %q, want application/json", requests[0].Header.Get("Content-Type"))
			}
			assertJSONBody(t, requests[0].Body, tc.wantBody)
			for i, request := range requests[1:] {
				assertRequest(t, request, http.MethodPost, "/api/v2/deployment/waitUntilStarted")
				if len(request.Body) != 0 {
					t.Fatalf("poll %d sent a body", i+1)
				}
			}
		})
	}
}

func TestPollingHasNoAttemptLimit(t *testing.T) {
	statuses := make([]int, 66)
	for i := range statuses[:65] {
		statuses[i] = http.StatusInternalServerError
	}
	statuses[65] = http.StatusOK
	server, err := mocklogs.New(statuses...)
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	client, err := vcflogs.NewClient(server.URL(), server.Client())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.JoinAndWait(context.Background(), vcflogs.JoinRequest{MasterFQDN: "li-01.example.com"}, 0); err != nil {
		t.Fatal(err)
	}
	if got := len(server.Requests()); got != 67 {
		t.Fatalf("request count = %d, want one join plus 66 polls", got)
	}
}

func TestNonSuccessResponsesAreErrors(t *testing.T) {
	tests := []struct {
		name       string
		joinStatus int
		waitStatus int
		wantCalls  int
	}{
		{name: "join failure", joinStatus: http.StatusBadRequest, waitStatus: http.StatusOK, wantCalls: 1},
		{name: "wait failure", joinStatus: http.StatusOK, waitStatus: http.StatusNotFound, wantCalls: 2},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			var mu sync.Mutex
			calls := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				mu.Lock()
				calls++
				mu.Unlock()
				switch r.URL.Path {
				case "/api/v2/deployment/join":
					w.WriteHeader(tc.joinStatus)
					if tc.joinStatus == http.StatusOK {
						io.WriteString(w, `{"masterAddress":"10.0.0.123","workerAddress":"10.0.0.124","workerPort":16520,"workerToken":"token","masterUiPort":80}`)
					}
				case "/api/v2/deployment/waitUntilStarted":
					w.WriteHeader(tc.waitStatus)
				default:
					http.NotFound(w, r)
				}
			}))
			defer server.Close()
			client, err := vcflogs.NewClient(server.URL, server.Client())
			if err != nil {
				t.Fatal(err)
			}
			if _, err := client.JoinAndWait(context.Background(), vcflogs.JoinRequest{MasterFQDN: "li-01.example.com"}, 0); err == nil {
				t.Fatal("JoinAndWait returned nil error for a non-success response")
			}
			mu.Lock()
			gotCalls := calls
			mu.Unlock()
			if gotCalls != tc.wantCalls {
				t.Fatalf("request count = %d, want %d", gotCalls, tc.wantCalls)
			}
		})
	}
}

func TestContextCancellationStopsPolling(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	calls := 0
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		calls++
		switch calls {
		case 1:
			return response(http.StatusOK, `{"masterAddress":"10.0.0.123","workerAddress":"10.0.0.124","workerPort":16520,"workerToken":"token","masterUiPort":80}`), nil
		case 2:
			cancel()
			return response(http.StatusInternalServerError, `{"errorMessage":"not started"}`), nil
		default:
			t.Fatalf("request %d was made after cancellation", calls)
			return nil, errors.New("request after cancellation")
		}
	})
	client, err := vcflogs.NewClient("http://vcf.test", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	_, err = client.JoinAndWait(ctx, vcflogs.JoinRequest{MasterFQDN: "li-01.example.com"}, time.Hour)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context cancellation", err)
	}
	if calls != 2 {
		t.Fatalf("request count = %d, want one join and one poll", calls)
	}
}

func TestMockServesOnlyContractOperations(t *testing.T) {
	server, err := mocklogs.New(http.StatusOK)
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()

	tests := []struct {
		method string
		path   string
	}{
		{http.MethodGet, "/api/v2/deployment/join"},
		{http.MethodPost, "/api/v2/deployment/new"},
		{http.MethodPost, "/api/v2/upgrades"},
		{http.MethodPost, "/deployment/join"},
	}
	for _, tc := range tests {
		req, err := http.NewRequest(tc.method, server.URL()+tc.path, nil)
		if err != nil {
			t.Fatal(err)
		}
		resp, err := server.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		if resp.StatusCode != http.StatusNotFound {
			t.Errorf("%s %s = %d, want 404", tc.method, tc.path, resp.StatusCode)
		}
	}
}

func TestMockRejectsUnknownWaitStatuses(t *testing.T) {
	if server, err := mocklogs.New(http.StatusAccepted); err == nil {
		server.Close()
		t.Fatal("mocklogs.New accepted a wait status outside the contract")
	}
}

func TestMockWaitScriptDefaultsAndRepeats(t *testing.T) {
	tests := []struct {
		name     string
		statuses []int
		want     int
	}{
		{name: "default is success", want: http.StatusOK},
		{name: "last scripted status repeats", statuses: []int{http.StatusInternalServerError}, want: http.StatusInternalServerError},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			server, err := mocklogs.New(tc.statuses...)
			if err != nil {
				t.Fatal(err)
			}
			defer server.Close()
			for i := 0; i < 2; i++ {
				response, err := server.Client().Post(server.URL()+"/api/v2/deployment/waitUntilStarted", "", nil)
				if err != nil {
					t.Fatal(err)
				}
				io.Copy(io.Discard, response.Body)
				response.Body.Close()
				if response.StatusCode != tc.want {
					t.Fatalf("response %d status = %d, want %d", i+1, response.StatusCode, tc.want)
				}
			}
		})
	}
}

func TestMockRequestSnapshotsAreConcurrentAndIndependent(t *testing.T) {
	server, err := mocklogs.New()
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()

	request, err := http.NewRequest(http.MethodPost, server.URL()+"/api/v2/deployment/join", strings.NewReader(`{"masterFQDN":"li-01.example.com"}`))
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("X-Snapshot", "original")
	resp, err := server.Client().Do(request)
	if err != nil {
		t.Fatal(err)
	}
	io.Copy(io.Discard, resp.Body)
	resp.Body.Close()

	first := server.Requests()
	if len(first) != 1 || len(first[0].Body) == 0 {
		t.Fatalf("initial request log = %#v", first)
	}
	first[0].Body[0] = '!'
	first[0].Header.Set("X-Snapshot", "mutated")
	second := server.Requests()
	if second[0].Body[0] == '!' || second[0].Header.Get("X-Snapshot") != "original" {
		t.Fatal("mutating a request snapshot changed the server's stored log")
	}

	var workers sync.WaitGroup
	for i := 0; i < 8; i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for j := 0; j < 16; j++ {
				req, err := http.NewRequest(http.MethodPost, server.URL()+"/api/v2/deployment/join", strings.NewReader(`{"masterFQDN":"li.example.com"}`))
				if err != nil {
					t.Error(err)
					return
				}
				response, err := server.Client().Do(req)
				if err != nil {
					t.Error(err)
					return
				}
				io.Copy(io.Discard, response.Body)
				response.Body.Close()
			}
		}()
	}
	done := make(chan struct{})
	go func() {
		workers.Wait()
		close(done)
	}()
	for {
		select {
		case <-done:
			if got := len(server.Requests()); got != 129 {
				t.Fatalf("request log length = %d, want 129", got)
			}
			return
		default:
			_ = server.Requests()
		}
	}
}

func TestImplementationTestsAreTableDriven(t *testing.T) {
	foundTestFile := false
	foundTable := false
	err := filepath.Walk(rootPath(), func(path string, info os.FileInfo, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		relative, err := filepath.Rel(rootPath(), path)
		if err != nil {
			return err
		}
		if info.IsDir() {
			if relative == ".git" || relative == ".sandbox-home" || relative == ".toolchain" || relative == ".gocache" || relative == filepath.Join("internal", "acceptance") {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(info.Name(), "_test.go") {
			return nil
		}
		foundTestFile = true
		parsed, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
		if err != nil {
			return err
		}
		for _, declaration := range parsed.Decls {
			function, ok := declaration.(*ast.FuncDecl)
			if !ok || function.Body == nil || !strings.HasPrefix(function.Name.Name, "Test") {
				continue
			}
			hasRange := false
			hasCases := false
			hasClientCase := false
			ast.Inspect(function.Body, func(node ast.Node) bool {
				switch value := node.(type) {
				case *ast.RangeStmt:
					hasRange = true
				case *ast.CompositeLit:
					if len(value.Elts) >= 3 {
						hasCases = true
					}
				case *ast.Ident:
					if value.Name == "JoinRequest" || value.Name == "JoinAndWait" {
						hasClientCase = true
					}
				}
				return true
			})
			foundTable = foundTable || hasRange && hasCases && hasClientCase
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if !foundTestFile || !foundTable {
		t.Fatal("implementation tests must include a client table with at least three cases")
	}
}

func assertRequest(t *testing.T, got mocklogs.RequestLog, method, requestURI string) {
	t.Helper()
	_ = got.ContentLength
	if got.Method != method || got.RequestURI != requestURI {
		t.Errorf("request = %s %s, want %s %s", got.Method, got.RequestURI, method, requestURI)
	}
	if strings.Contains(got.RequestURI, "?") {
		t.Errorf("request URI contains a query string: %s", got.RequestURI)
	}
}

func assertJSONBody(t *testing.T, body []byte, want map[string]any) {
	t.Helper()
	var got map[string]any
	if err := json.Unmarshal(body, &got); err != nil {
		t.Fatalf("request body is not a JSON object: %v", err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("request JSON = %#v, want %#v", got, want)
	}
}

func sameStrings(got []any, want []string) bool {
	counts := make(map[string]int, len(want))
	for _, value := range want {
		counts[value]++
	}
	for _, value := range got {
		text, ok := value.(string)
		if !ok || counts[text] == 0 {
			return false
		}
		counts[text]--
	}
	return true
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func response(status int, body string) *http.Response {
	return &http.Response{
		StatusCode: status,
		Header:     make(http.Header),
		Body:       io.NopCloser(strings.NewReader(body)),
	}
}

func intPtr(value int) *int    { return &value }
func boolPtr(value bool) *bool { return &value }
