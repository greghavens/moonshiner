package verifier_test

import (
	"context"
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strconv"
	"strings"
	"testing"

	"example.com/vcfautomationtask/internal/contractmock"
	"example.com/vcfautomationtask/vcfautomation"
)

func TestListDeploymentsWireAndPagination(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name        string
		pageSize    int
		options     vcfautomation.ListOptions
		pages       map[int]contractmock.Page
		want        []vcfautomation.Deployment
		wantQueries []url.Values
	}{
		{
			name:     "unset optional fields are absent on every page",
			pageSize: 2,
			options:  vcfautomation.ListOptions{},
			pages: pages(
				[]contractmock.Deployment{
					{ID: "dep-z", Name: "Zulu", ProjectID: "project-z", Status: "CREATE_SUCCESSFUL", CreatedAt: "2026-01-05T00:00:00Z"},
					{ID: "dep-b", Name: "Beta", ProjectID: "project-b", Status: "UPDATE_FAILED", CreatedAt: "2026-01-04T00:00:00Z"},
				},
				[]contractmock.Deployment{
					{ID: "dep-y", Name: "Yankee", ProjectID: "project-y", Status: "CREATE_SUCCESSFUL", CreatedAt: "2026-01-03T00:00:00Z"},
					{ID: "dep-a", Name: "Alpha", ProjectID: "project-a", Status: "UPDATE_FAILED", CreatedAt: "2026-01-02T00:00:00Z"},
				},
				[]contractmock.Deployment{
					{ID: "dep-m", Name: "Mike", ProjectID: "project-m", Status: "CREATE_SUCCESSFUL", CreatedAt: "2026-01-01T00:00:00Z"},
				},
			),
			want: []vcfautomation.Deployment{
				{ID: "dep-a", Name: "Alpha", ProjectID: "project-a", Status: "UPDATE_FAILED", CreatedAt: "2026-01-02T00:00:00Z"},
				{ID: "dep-b", Name: "Beta", ProjectID: "project-b", Status: "UPDATE_FAILED", CreatedAt: "2026-01-04T00:00:00Z"},
				{ID: "dep-m", Name: "Mike", ProjectID: "project-m", Status: "CREATE_SUCCESSFUL", CreatedAt: "2026-01-01T00:00:00Z"},
				{ID: "dep-y", Name: "Yankee", ProjectID: "project-y", Status: "CREATE_SUCCESSFUL", CreatedAt: "2026-01-03T00:00:00Z"},
				{ID: "dep-z", Name: "Zulu", ProjectID: "project-z", Status: "CREATE_SUCCESSFUL", CreatedAt: "2026-01-05T00:00:00Z"},
			},
			wantQueries: []url.Values{
				{"page": {"0"}, "size": {"2"}, "sort": {"id,ASC"}},
				{"page": {"1"}, "size": {"2"}, "sort": {"id,ASC"}},
				{"page": {"2"}, "size": {"2"}, "sort": {"id,ASC"}},
			},
		},
		{
			name:     "populated options use documented encodings",
			pageSize: 3,
			options: vcfautomation.ListOptions{
				Projects: []string{"project-a", "project-b"},
				Status:   []string{"CREATE_SUCCESSFUL", "UPDATE_FAILED"},
				Search:   "edge cluster",
			},
			pages: pages(
				[]contractmock.Deployment{{ID: "dep-c", Name: "Cluster C", ProjectID: "project-a", Status: "CREATE_SUCCESSFUL", CreatedAt: "2026-02-02T00:00:00Z"}},
				[]contractmock.Deployment{{ID: "dep-a", Name: "Cluster A", ProjectID: "project-b", Status: "UPDATE_FAILED", CreatedAt: "2026-02-01T00:00:00Z"}},
			),
			want: []vcfautomation.Deployment{
				{ID: "dep-a", Name: "Cluster A", ProjectID: "project-b", Status: "UPDATE_FAILED", CreatedAt: "2026-02-01T00:00:00Z"},
				{ID: "dep-c", Name: "Cluster C", ProjectID: "project-a", Status: "CREATE_SUCCESSFUL", CreatedAt: "2026-02-02T00:00:00Z"},
			},
			wantQueries: []url.Values{
				{
					"page": {"0"}, "projects": {"project-a,project-b"}, "search": {"edge cluster"},
					"size": {"3"}, "sort": {"id,ASC"}, "status": {"CREATE_SUCCESSFUL,UPDATE_FAILED"},
				},
				{
					"page": {"1"}, "projects": {"project-a,project-b"}, "search": {"edge cluster"},
					"size": {"3"}, "sort": {"id,ASC"}, "status": {"CREATE_SUCCESSFUL,UPDATE_FAILED"},
				},
			},
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			mock := contractmock.New(test.pages)
			defer mock.Close()

			client, err := vcfautomation.NewClient(mock.URL(), "fixture-token", test.pageSize, mock.Client())
			if err != nil {
				t.Fatalf("NewClient() error = %v", err)
			}
			got, err := client.ListDeployments(context.Background(), test.options)
			if err != nil {
				t.Fatalf("ListDeployments() error = %v", err)
			}
			if !reflect.DeepEqual(got, test.want) {
				t.Fatalf("deployments = %#v, want %#v", got, test.want)
			}

			requests := mock.Requests()
			if len(requests) != len(test.wantQueries) {
				t.Fatalf("request count = %d, want %d", len(requests), len(test.wantQueries))
			}
			for i, request := range requests {
				if request.Method != http.MethodGet {
					t.Errorf("request %d method = %q, want GET", i, request.Method)
				}
				requestURI, err := url.ParseRequestURI(request.RequestURI)
				if err != nil {
					t.Errorf("request %d URI %q is invalid: %v", i, request.RequestURI, err)
				} else {
					if requestURI.EscapedPath() != contractmock.DeploymentsPath {
						t.Errorf("request %d path = %q, want %q", i, requestURI.EscapedPath(), contractmock.DeploymentsPath)
					}
					if gotQuery := requestURI.Query(); !reflect.DeepEqual(gotQuery, test.wantQueries[i]) {
						t.Errorf("request %d query = %v, want %v", i, gotQuery, test.wantQueries[i])
					}
				}
				if request.Authorization != "Bearer fixture-token" {
					t.Errorf("request %d Authorization = %q", i, request.Authorization)
				}
				if request.Accept != "application/json" {
					t.Errorf("request %d Accept = %q", i, request.Accept)
				}
				if len(request.Body) != 0 {
					t.Errorf("request %d body = %q, want empty", i, request.Body)
				}
			}
		})
	}
}

func TestClientReportsHTTPAndJSONErrors(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name    string
		handler http.HandlerFunc
	}{
		{
			name: "non success response",
			handler: func(w http.ResponseWriter, _ *http.Request) {
				http.Error(w, "upstream unavailable", http.StatusServiceUnavailable)
			},
		},
		{
			name: "malformed JSON",
			handler: func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				_, _ = io.WriteString(w, `{not-json`)
			},
		},
		{
			name: "trailing JSON data",
			handler: func(w http.ResponseWriter, _ *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				_, _ = io.WriteString(w, `{"content":[],"totalPages":1} trailing`)
			},
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server := newLoopbackServer(t, test.handler)
			client, err := vcfautomation.NewClient(server.url, "fixture-token", 2, server.client)
			if err != nil {
				t.Fatalf("NewClient() error = %v", err)
			}
			_, err = client.ListDeployments(context.Background(), vcfautomation.ListOptions{})
			if err == nil {
				t.Fatal("ListDeployments() returned nil error")
			}
		})
	}
}

func TestSubmissionIncludesTableDrivenListDeploymentsTest(t *testing.T) {
	t.Parallel()

	root := repositoryRoot(t)
	testFiles, err := filepath.Glob(filepath.Join(root, "vcfautomation", "*_test.go"))
	if err != nil {
		t.Fatal(err)
	}
	if len(testFiles) == 0 {
		t.Fatal("no vcfautomation/*_test.go submission test file found")
	}

	fset := token.NewFileSet()
	hasTestLoop := false
	callsListDeployments := false
	for _, filename := range testFiles {
		file, err := parser.ParseFile(fset, filename, nil, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", filename, err)
		}
		ast.Inspect(file, func(node ast.Node) bool {
			call, ok := node.(*ast.CallExpr)
			if !ok {
				return true
			}
			selector, ok := call.Fun.(*ast.SelectorExpr)
			if ok && selector.Sel.Name == "ListDeployments" {
				callsListDeployments = true
			}
			return true
		})
		for _, declaration := range file.Decls {
			function, ok := declaration.(*ast.FuncDecl)
			if !ok || function.Body == nil || !strings.HasPrefix(function.Name.Name, "Test") {
				continue
			}
			ast.Inspect(function.Body, func(node ast.Node) bool {
				switch node.(type) {
				case *ast.RangeStmt, *ast.ForStmt:
					hasTestLoop = true
				}
				return true
			})
		}
	}
	if hasTestLoop && callsListDeployments {
		return
	}
	t.Fatal("submission does not include a table-driven test of ListDeployments")
}

func TestClientUsesOnlyStandardLibrary(t *testing.T) {
	t.Parallel()

	filename := filepath.Join(repositoryRoot(t), "vcfautomation", "client.go")
	file, err := parser.ParseFile(token.NewFileSet(), filename, nil, parser.ImportsOnly)
	if err != nil {
		t.Fatalf("parse %s: %v", filename, err)
	}
	for _, importSpec := range file.Imports {
		importPath, err := strconv.Unquote(importSpec.Path.Value)
		if err != nil {
			t.Fatalf("decode import %s: %v", importSpec.Path.Value, err)
		}
		firstElement := strings.SplitN(importPath, "/", 2)[0]
		if strings.Contains(firstElement, ".") {
			t.Errorf("vcfautomation/client.go imports non-standard package %q", importPath)
		}
	}
}

func TestContractProvenanceAndMockSurface(t *testing.T) {
	t.Parallel()

	root := repositoryRoot(t)
	contractData, err := os.ReadFile(filepath.Join(root, "docs", "contract.json"))
	if err != nil {
		t.Fatal(err)
	}
	var contract struct {
		Provenance struct {
			SourceKind string `json:"source_kind"`
			Statement  string `json:"statement"`
		} `json:"provenance"`
		Operations []struct {
			Operation string `json:"operation"`
			Method    string `json:"method"`
			Path      string `json:"path"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(contractData, &contract); err != nil {
		t.Fatalf("decode contract: %v", err)
	}
	if contract.Provenance.SourceKind != "reference_documentation" ||
		!strings.Contains(contract.Provenance.Statement, "not from a published API specification") {
		t.Fatalf("contract provenance does not identify reference documentation: %+v", contract.Provenance)
	}
	if len(contract.Operations) != 1 || contract.Operations[0].Operation != "Get Deployments" ||
		contract.Operations[0].Method != http.MethodGet || contract.Operations[0].Path != contractmock.DeploymentsPath {
		t.Fatalf("unexpected contract operations: %+v", contract.Operations)
	}

	sourcesData, err := os.ReadFile(filepath.Join(root, "docs", "official_sources.json"))
	if err != nil {
		t.Fatal(err)
	}
	var sources struct {
		FetchedOn string `json:"fetched_on"`
		Sources   []struct {
			URL       string `json:"url"`
			Operation string `json:"operation"`
			FetchedOn string `json:"fetched_on"`
		} `json:"sources"`
	}
	if err := json.Unmarshal(sourcesData, &sources); err != nil {
		t.Fatalf("decode official sources: %v", err)
	}
	if sources.FetchedOn == "" || len(sources.Sources) != 1 ||
		!strings.HasPrefix(sources.Sources[0].URL, "https://developer.broadcom.com/xapis/") ||
		!strings.Contains(sources.Sources[0].Operation, "Get Deployments") ||
		sources.Sources[0].FetchedOn != sources.FetchedOn {
		t.Fatalf("official source index is incomplete: %+v", sources)
	}

	mock := contractmock.New(map[int]contractmock.Page{})
	defer mock.Close()
	for _, request := range []struct {
		method string
		path   string
	}{
		{method: http.MethodGet, path: "/not-in-contract"},
		{method: http.MethodPost, path: contractmock.DeploymentsPath},
	} {
		req, err := http.NewRequest(request.method, mock.URL()+request.path, nil)
		if err != nil {
			t.Fatal(err)
		}
		response, err := mock.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		_ = response.Body.Close()
		if response.StatusCode >= 200 && response.StatusCode < 300 {
			t.Fatalf("mock served unnamed operation %s %s", request.method, request.path)
		}
	}
}

func pages(content ...[]contractmock.Deployment) map[int]contractmock.Page {
	totalElements := 0
	for _, pageContent := range content {
		totalElements += len(pageContent)
	}
	result := make(map[int]contractmock.Page, len(content))
	for number, pageContent := range content {
		result[number] = contractmock.Page{
			Content:          pageContent,
			Number:           number,
			NumberOfElements: len(pageContent),
			Size:             len(pageContent),
			TotalElements:    totalElements,
			TotalPages:       len(content),
			First:            number == 0,
			Last:             number == len(content)-1,
			Empty:            len(pageContent) == 0,
		}
	}
	return result
}

type loopbackServer struct {
	url    string
	client *http.Client
}

func newLoopbackServer(t *testing.T, handler http.Handler) loopbackServer {
	t.Helper()
	// Keep this helper local to the verifier so all error-path traffic is also
	// guaranteed to terminate on a loopback listener.
	listener, err := newLocalListener()
	if err != nil {
		t.Fatalf("listen on loopback: %v", err)
	}
	server := &http.Server{Handler: handler}
	go func() { _ = server.Serve(listener) }()
	t.Cleanup(func() { _ = server.Close() })
	return loopbackServer{
		url:    "http://" + listener.Addr().String(),
		client: &http.Client{Transport: &http.Transport{Proxy: nil}},
	}
}

func repositoryRoot(t *testing.T) string {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve verifier source path")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(filename), "..", ".."))
}
