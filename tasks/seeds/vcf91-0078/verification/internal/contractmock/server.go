package contractmock

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strings"
	"sync"
	"time"
)

const ListOperation = "ListAllInfraSegments"

type contract struct {
	BasePath   string               `json:"basePath"`
	Operations map[string]operation `json:"operations"`
}

type operation struct {
	OperationID     string           `json:"operationId"`
	Method          string           `json:"method"`
	Path            string           `json:"path"`
	QueryParameters []queryParameter `json:"query_parameters"`
}

type queryParameter struct {
	Name string `json:"name"`
	Type string `json:"type"`
}

// Scenario controls the access-token transition. All collection response data
// remains a deterministic property of this protected loopback fixture.
type Scenario struct {
	ExpiredToken string
	FreshToken   string
	ExpireOnce   bool
}

// LoggedRequest is the synchronized assertion surface used by the verifier.
type LoggedRequest struct {
	OperationID      string   `json:"operation_id"`
	Method           string   `json:"method"`
	RequestURI       string   `json:"request_uri"`
	RawQuery         string   `json:"raw_query"`
	Authorization    string   `json:"authorization"`
	Accept           string   `json:"accept"`
	ContentType      string   `json:"content_type"`
	ContentLength    int64    `json:"content_length"`
	TransferEncoding []string `json:"transfer_encoding"`
	Body             string   `json:"body"`
	StatusCode       int      `json:"status_code"`
}

type page struct {
	Results []segment
	Cursor  string
}

type segment struct {
	ID          string `json:"id"`
	DisplayName string `json:"display_name"`
	Path        string `json:"path"`
}

// Server is a contract-pinned, IPv4-loopback NSX Policy fixture.
type Server struct {
	URL string

	contract contract
	scenario Scenario
	http     *http.Server
	listener net.Listener

	mu                  sync.Mutex
	requests            []LoggedRequest
	tokenExpired        bool
	successfulResponses int
}

// New loads its only route from contractPath and listens on an ephemeral IPv4
// loopback port. Contracts naming any additional operation are rejected.
func New(contractPath string, scenario Scenario) (*Server, error) {
	raw, err := os.ReadFile(contractPath)
	if err != nil {
		return nil, fmt.Errorf("read contract: %w", err)
	}
	var c contract
	if err := json.Unmarshal(raw, &c); err != nil {
		return nil, fmt.Errorf("decode contract: %w", err)
	}
	if c.BasePath != "/policy/api/v1" || len(c.Operations) != 1 {
		return nil, fmt.Errorf("unexpected focused contract")
	}
	op, ok := c.Operations[ListOperation]
	if !ok || op.OperationID != ListOperation || op.Method != http.MethodGet ||
		op.Path != "/infra/segments" {
		return nil, fmt.Errorf("unexpected contract operation")
	}
	gotParameters := make([]string, 0, len(op.QueryParameters))
	for _, parameter := range op.QueryParameters {
		gotParameters = append(gotParameters, parameter.Name+":"+parameter.Type)
	}
	sort.Strings(gotParameters)
	wantParameters := []string{
		"cursor:string",
		"include_mark_for_delete_objects:boolean",
		"included_fields:string",
		"page_size:integer",
		"segment_type:string",
		"sort_ascending:boolean",
		"sort_by:string",
	}
	if !equalStrings(gotParameters, wantParameters) {
		return nil, fmt.Errorf("unexpected contract query parameters")
	}
	if !validFixtureToken(scenario.ExpiredToken) ||
		!validFixtureToken(scenario.FreshToken) ||
		scenario.ExpiredToken == scenario.FreshToken {
		return nil, fmt.Errorf("scenario requires two distinct safe tokens")
	}

	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("listen: %w", err)
	}
	s := &Server{
		URL:      "http://" + listener.Addr().String(),
		contract: c,
		scenario: scenario,
		listener: listener,
	}
	s.http = &http.Server{
		Handler:           http.HandlerFunc(s.serveHTTP),
		ReadHeaderTimeout: 2 * time.Second,
	}
	go func() {
		_ = s.http.Serve(listener)
	}()
	return s, nil
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		http.Error(w, "read failed", http.StatusBadRequest)
		return
	}
	_ = r.Body.Close()

	op := s.contract.Operations[ListOperation]
	opID := ""
	if r.Method == op.Method &&
		r.URL.EscapedPath() == s.contract.BasePath+op.Path &&
		declaredQueryOnly(r.URL, op.QueryParameters) &&
		!(r.URL.RawQuery == "" && strings.HasSuffix(r.RequestURI, "?")) {
		opID = ListOperation
	}

	status, response := s.response(opID, r)
	s.appendLog(LoggedRequest{
		OperationID:      opID,
		Method:           r.Method,
		RequestURI:       r.RequestURI,
		RawQuery:         r.URL.RawQuery,
		Authorization:    r.Header.Get("Authorization"),
		Accept:           r.Header.Get("Accept"),
		ContentType:      r.Header.Get("Content-Type"),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
		Body:             string(body),
		StatusCode:       status,
	})

	w.Header().Set("Content-Type", "application/json")
	if status == http.StatusUnauthorized {
		w.Header().Set("WWW-Authenticate", `Bearer error="invalid_token"`)
	}
	w.WriteHeader(status)
	_, _ = w.Write(response)
}

func (s *Server) response(opID string, r *http.Request) (int, []byte) {
	if opID == "" {
		return http.StatusNotFound, []byte(`{"error_message":"undeclared route"}`)
	}

	token, bearer := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	if !bearer || token == "" {
		return http.StatusUnauthorized, []byte(`{"error_code":40101,"error_message":"access token required","module_name":"common-services"}`)
	}

	cursor := r.URL.Query().Get("cursor")
	currentPage, ok := fixturePages()[cursor]
	if !ok {
		return http.StatusBadRequest, []byte(`{"error_code":40001,"error_message":"unknown cursor","module_name":"common-services"}`)
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	if token != s.scenario.ExpiredToken && token != s.scenario.FreshToken {
		return http.StatusForbidden, []byte(`{"error_code":40301,"error_message":"token not recognized","module_name":"common-services","details":"credential rejected"}`)
	}
	if s.tokenExpired && token == s.scenario.ExpiredToken {
		return http.StatusUnauthorized, []byte(`{"error_code":40102,"error_message":"access token expired","module_name":"common-services"}`)
	}
	if s.scenario.ExpireOnce && cursor == "cursor-two" &&
		token == s.scenario.ExpiredToken {
		s.tokenExpired = true
		return http.StatusUnauthorized, []byte(`{"error_code":40102,"error_message":"access token expired","module_name":"common-services"}`)
	}

	s.successfulResponses++
	results := append([]segment(nil), currentPage.Results...)
	// Odd responses are reversed and even responses use fixture order. Because
	// each traversal has three pages, the next traversal sees the opposite
	// element order for every page.
	if s.successfulResponses%2 == 1 {
		for left, right := 0, len(results)-1; left < right; left, right = left+1, right-1 {
			results[left], results[right] = results[right], results[left]
		}
	}
	payload := struct {
		Results     []segment `json:"results"`
		Cursor      string    `json:"cursor,omitempty"`
		ResultCount int64     `json:"result_count"`
	}{
		Results:     results,
		Cursor:      currentPage.Cursor,
		ResultCount: int64(len(results)),
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		panic(err)
	}
	return http.StatusOK, encoded
}

func fixturePages() map[string]page {
	return map[string]page{
		"": {
			Results: []segment{
				{ID: "segment-z", DisplayName: "Zulu", Path: "/infra/segments/zulu"},
				{ID: "segment-a", DisplayName: "Alpha", Path: "/infra/segments/alpha"},
			},
			Cursor: "cursor-two",
		},
		"cursor-two": {
			Results: []segment{
				{ID: "segment-y", DisplayName: "Yankee", Path: "/infra/segments/yankee"},
				{ID: "segment-b", DisplayName: "Bravo", Path: "/infra/segments/bravo"},
			},
			Cursor: "cursor-three",
		},
		"cursor-three": {
			Results: []segment{
				{ID: "segment-x", DisplayName: "Xray", Path: "/infra/segments/xray"},
				{ID: "segment-c", DisplayName: "Charlie", Path: "/infra/segments/charlie"},
			},
		},
	}
}

func declaredQueryOnly(target *url.URL, parameters []queryParameter) bool {
	values, err := url.ParseQuery(target.RawQuery)
	if err != nil {
		return false
	}
	declared := make(map[string]struct{}, len(parameters))
	for _, parameter := range parameters {
		declared[parameter.Name] = struct{}{}
	}
	for name, entries := range values {
		if _, ok := declared[name]; !ok || len(entries) != 1 {
			return false
		}
	}
	return true
}

func validFixtureToken(token string) bool {
	return token != "" && token == strings.TrimSpace(token) &&
		!strings.ContainsAny(token, "\r\n")
}

func (s *Server) appendLog(entry LoggedRequest) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, entry)
}

// Snapshot returns a synchronized copy of the complete request log.
func (s *Server) Snapshot() []LoggedRequest {
	s.mu.Lock()
	defer s.mu.Unlock()
	result := make([]LoggedRequest, len(s.requests))
	copy(result, s.requests)
	for i := range result {
		result[i].TransferEncoding = append([]string(nil), result[i].TransferEncoding...)
	}
	return result
}

// Close stops the loopback service.
func (s *Server) Close() error {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	err := s.http.Shutdown(ctx)
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
