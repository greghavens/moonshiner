// Package contractmock provides a loopback-only NSX Policy server whose route
// allow-list is loaded from the protected, specification-derived contract.
package contractmock

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
)

const ListAllInfraSegments = "ListAllInfraSegments"

type Contract struct {
	Source struct {
		Repository string `json:"repository"`
		Commit     string `json:"commit"`
		Path       string `json:"path"`
		License    string `json:"license"`
	} `json:"source"`
	Swagger    string      `json:"swagger"`
	Info       Info        `json:"info"`
	BasePath   string      `json:"basePath"`
	Security   Security    `json:"security"`
	Operations []Operation `json:"operations"`
}

type Info struct {
	Title   string `json:"title"`
	Version string `json:"version"`
}

type Security struct {
	Name string `json:"name"`
	Type string `json:"type"`
}

type Operation struct {
	OperationID string              `json:"operationId"`
	Method      string              `json:"method"`
	Path        string              `json:"path"`
	Produces    []string            `json:"produces"`
	Parameters  []ContractParameter `json:"parameters"`
}

type ContractParameter struct {
	Name     string `json:"name"`
	In       string `json:"in"`
	Required bool   `json:"required"`
	Type     string `json:"type"`
}

func LoadContract(path string) (Contract, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Contract{}, err
	}
	var contract Contract
	if err := json.Unmarshal(data, &contract); err != nil {
		return Contract{}, err
	}
	if contract.Swagger != "2.0" || contract.Info.Title == "" ||
		contract.Info.Version == "" || contract.BasePath == "" {
		return Contract{}, fmt.Errorf("contract is not a usable OpenAPI 2.0 extraction")
	}
	if len(contract.Operations) == 0 {
		return Contract{}, fmt.Errorf("contract names no operations")
	}
	ids := make(map[string]bool)
	routes := make(map[string]bool)
	for _, operation := range contract.Operations {
		if operation.OperationID == "" || operation.Method == "" || operation.Path == "" {
			return Contract{}, fmt.Errorf("contract contains an incomplete operation")
		}
		route := strings.ToUpper(operation.Method) + " " + contract.BasePath + operation.Path
		if ids[operation.OperationID] || routes[route] {
			return Contract{}, fmt.Errorf("contract contains a duplicate operation or route")
		}
		ids[operation.OperationID] = true
		routes[route] = true
	}
	return contract, nil
}

type Segment struct {
	ID          string `json:"id"`
	DisplayName string `json:"display_name"`
	Path        string `json:"path"`
}

type Page struct {
	Results     []Segment
	Cursor      string
	ResultCount *int64
}

type Request struct {
	OperationID string
	Method      string
	Path        string
	RawQuery    string
	Header      http.Header
	Body        string
}

type Server struct {
	URL string

	contract Contract
	pages    map[string]Page
	client   *http.Client

	mu       sync.Mutex
	requests []Request
}

func New(t testing.TB, contractPath string, pages map[string]Page) *Server {
	t.Helper()
	contract, err := LoadContract(contractPath)
	if err != nil {
		t.Fatalf("load contract mock: %v", err)
	}
	copiedPages := make(map[string]Page, len(pages))
	for cursor, page := range pages {
		copiedPages[cursor] = page
	}
	mock := &Server{
		URL:      "http://127.0.0.1",
		contract: contract,
		pages:    copiedPages,
	}
	mock.client = &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		recorder := httptest.NewRecorder()
		mock.serveHTTP(recorder, request)
		response := recorder.Result()
		response.Request = request
		return response, nil
	})}
	t.Cleanup(mock.Close)
	return mock
}

func (s *Server) Close() {}

func (s *Server) Client() *http.Client {
	return s.client
}

func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	requests := make([]Request, len(s.requests))
	for i, request := range s.requests {
		requests[i] = request
		requests[i].Header = request.Header.Clone()
	}
	return requests
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	var body []byte
	if r.Body != nil {
		body, _ = io.ReadAll(r.Body)
	}
	operationID, pathKnown := s.resolve(r.Method, r.URL.Path)
	request := Request{
		OperationID: operationID,
		Method:      r.Method,
		Path:        r.URL.Path,
		RawQuery:    r.URL.RawQuery,
		Header:      r.Header.Clone(),
		Body:        string(body),
	}
	s.mu.Lock()
	s.requests = append(s.requests, request)
	s.mu.Unlock()

	if operationID == "" {
		if pathKnown {
			w.WriteHeader(http.StatusMethodNotAllowed)
		} else {
			http.NotFound(w, r)
		}
		return
	}
	if operationID != ListAllInfraSegments {
		http.Error(w, "operation has no fixture", http.StatusNotImplemented)
		return
	}
	page, ok := s.pages[r.URL.Query().Get("cursor")]
	if !ok {
		http.Error(w, "unknown cursor", http.StatusBadRequest)
		return
	}

	results := page.Results
	if results == nil {
		results = []Segment{}
	}
	response := map[string]any{"results": results}
	if page.Cursor != "" {
		response["cursor"] = page.Cursor
	}
	if page.ResultCount != nil {
		response["result_count"] = *page.ResultCount
	}
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(response); err != nil {
		panic(err)
	}
}

func (s *Server) resolve(method, path string) (operationID string, pathKnown bool) {
	for _, operation := range s.contract.Operations {
		fullPath := s.contract.BasePath + operation.Path
		if path != fullPath {
			continue
		}
		pathKnown = true
		if method == strings.ToUpper(operation.Method) {
			return operation.OperationID, true
		}
	}
	return "", pathKnown
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}
