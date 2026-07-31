// Package contractmock provides a contract-pinned, loopback-only Log
// Management fixture and a concurrency-safe request recorder.
package contractmock

import (
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"sync"
	"testing"
)

const (
	VariantNormal         = ""
	VariantDuplicateHit   = "duplicate-hit"
	VariantDuplicateField = "duplicate-field"
	VariantMismatch       = "mismatch"
	VariantMissingTime    = "missing-time"
	VariantNonStringState = "non-string-state"
	VariantTimedOut       = "timed-out"
	VariantFailureReason  = "failure-reason"
	VariantInvalidJSON    = "invalid-json"
	VariantJSONArray      = "json-array"
	VariantHTTPError      = "http-error"
	VariantWrongMedia     = "wrong-media"
)

// Options selects a response script or one malformed-response variant.
type Options struct {
	States  []string
	Variant string
}

// RequestRecord is a deep request snapshot captured by the handler.
type RequestRecord struct {
	Operation        string
	Method           string
	RequestURI       string
	Header           http.Header
	Body             []byte
	ContentLength    int64
	TransferEncoding []string
}

type operation struct {
	ContractName string `json:"contractName"`
	OperationID  string `json:"operationId"`
	Method       string `json:"method"`
	Path         string `json:"path"`
}

type contractDocument struct {
	Operations []operation `json:"operations"`
}

// Server serves exactly the operation route loaded from docs/contract.json.
type Server struct {
	t              testing.TB
	operation      operation
	http           *httptest.Server
	origin         string
	fallbackClient *http.Client
	states         []string
	variant        string

	mu    sync.Mutex
	log   []RequestRecord
	reads map[string]int
}

// New loads the route allow-list from contractPath and starts an ephemeral
// IPv4 loopback server. If the sandbox denies listener creation, Client uses
// the exact same handler through an in-memory RoundTripper.
func New(t testing.TB, contractPath string, options Options) *Server {
	t.Helper()

	data, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var document contractDocument
	if err := json.Unmarshal(data, &document); err != nil {
		t.Fatalf("decode contract: %v", err)
	}
	if len(document.Operations) != 1 {
		t.Fatalf("contract operation count = %d, want 1", len(document.Operations))
	}
	op := document.Operations[0]
	if op.ContractName != "searchOperationStateEvents" ||
		op.OperationID != "executeLogSearchQuery_1" ||
		op.Method != http.MethodPost ||
		op.Path != "/api/v2/logs/search" {
		t.Fatalf("unexpected contract operation: %+v", op)
	}

	states := append([]string(nil), options.States...)
	if len(states) == 0 {
		states = []string{"QUEUED", "RUNNING", "BLOCKED", "SUCCEEDED"}
	}
	server := &Server{
		t:         t,
		operation: op,
		states:    states,
		variant:   options.Variant,
		reads:     make(map[string]int),
	}

	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err == nil {
		host, _, splitErr := net.SplitHostPort(listener.Addr().String())
		if splitErr != nil || !net.ParseIP(host).IsLoopback() {
			_ = listener.Close()
			t.Fatalf("fixture listener is not loopback: %q", listener.Addr())
		}
		server.http = &httptest.Server{
			Listener: listener,
			Config:   &http.Server{Handler: http.HandlerFunc(server.serveHTTP)},
		}
		server.http.Start()
		server.origin = server.http.URL
		t.Cleanup(server.http.Close)
		return server
	}

	server.origin = "http://127.0.0.1"
	server.fallbackClient = &http.Client{
		Transport: handlerTransport{handler: http.HandlerFunc(server.serveHTTP)},
	}
	return server
}

// URL returns the loopback origin.
func (s *Server) URL() string {
	return s.origin
}

// Client returns a fresh client that reaches this fixture.
func (s *Server) Client() *http.Client {
	if s.fallbackClient == nil {
		return &http.Client{}
	}
	copied := *s.fallbackClient
	return &copied
}

// Log returns a deep copy of the recorded request snapshots.
func (s *Server) Log() []RequestRecord {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]RequestRecord, len(s.log))
	for index, record := range s.log {
		out[index] = record
		out[index].Header = record.Header.Clone()
		out[index].Body = append([]byte(nil), record.Body...)
		out[index].TransferEncoding = append([]string(nil), record.TransferEncoding...)
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, request *http.Request) {
	body, _ := io.ReadAll(io.LimitReader(request.Body, 1<<20))
	operationName := ""
	if request.Method == s.operation.Method && request.URL.Path == s.operation.Path {
		operationName = s.operation.ContractName
	}
	s.record(request, operationName, body)

	if operationName == "" {
		http.NotFound(w, request)
		return
	}

	operationID := operationIDFromQuery(body)
	if operationID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"errorCode":    "QUERY_ERROR",
			"errorMessage": "operation_id filter is required",
		})
		return
	}

	switch s.variant {
	case VariantHTTPError:
		writeJSON(w, http.StatusForbidden, map[string]any{
			"errorCode":    "SECURITY_ERROR",
			"errorMessage": "sensitive-token must not escape",
		})
		return
	case VariantInvalidJSON:
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, "{")
		return
	case VariantJSONArray:
		writeJSON(w, http.StatusOK, []any{})
		return
	case VariantWrongMedia:
		w.Header().Set("Content-Type", "text/plain")
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, `{"events":{"hits":[],"total":0}}`)
		return
	case VariantTimedOut:
		writeJSON(w, http.StatusOK, map[string]any{
			"events":   map[string]any{"hits": []any{}, "total": 0},
			"timedOut": true,
		})
		return
	case VariantFailureReason:
		writeJSON(w, http.StatusOK, map[string]any{
			"events":         map[string]any{"hits": []any{}, "total": 0},
			"failureMessage": "fixture detail must not be exposed",
			"failureReason":  "SYSTEM",
		})
		return
	}

	state, poll := s.nextState(operationID)
	if state == "" {
		writeJSON(w, http.StatusOK, map[string]any{
			"events":          map[string]any{"hits": []any{}, "total": 0},
			"timeTakenMillis": 1,
			"timedOut":        false,
		})
		return
	}

	hitOperationID := operationID
	if s.variant == VariantMismatch {
		hitOperationID = "different-operation"
	}
	stateValue := any(state)
	if s.variant == VariantNonStringState {
		stateValue = 7
	}
	fields := []map[string]any{
		{"internalName": "operation_id", "value": hitOperationID, "valueType": "STRING"},
		{"internalName": "event_type", "value": "VCF_ASYNC_OPERATION_STATE", "valueType": "STRING"},
		{"internalName": "operation_state", "value": stateValue, "valueType": "STRING"},
	}
	if s.variant == VariantDuplicateField {
		fields = append(fields, map[string]any{
			"internalName": "operation_state",
			"value":        state,
			"valueType":    "STRING",
		})
	}
	content := map[string]any{
		"fields":       fields,
		"originalText": "structured operation progress event",
	}
	if s.variant != VariantMissingTime {
		content["logTimestamp"] = int64(1700000000000 + poll)
	}
	hit := map[string]any{"msgContent": content}
	hits := []any{hit}
	if s.variant == VariantDuplicateHit {
		hits = append(hits, hit)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"events":          map[string]any{"hits": hits, "total": len(hits)},
		"timeTakenMillis": 1,
		"timedOut":        false,
	})
}

func (s *Server) record(request *http.Request, operation string, body []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log = append(s.log, RequestRecord{
		Operation:        operation,
		Method:           request.Method,
		RequestURI:       request.URL.RequestURI(),
		Header:           request.Header.Clone(),
		Body:             append([]byte(nil), body...),
		ContentLength:    request.ContentLength,
		TransferEncoding: append([]string(nil), request.TransferEncoding...),
	})
}

func (s *Server) nextState(operationID string) (string, int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	index := s.reads[operationID]
	s.reads[operationID] = index + 1
	stateIndex := index
	if stateIndex >= len(s.states) {
		stateIndex = len(s.states) - 1
	}
	return s.states[stateIndex], index + 1
}

func operationIDFromQuery(body []byte) string {
	var request struct {
		Query struct {
			Bool struct {
				Filter []struct {
					MatchPhrase map[string]string `json:"match_phrase"`
				} `json:"filter"`
			} `json:"bool"`
		} `json:"query"`
	}
	if json.Unmarshal(body, &request) != nil {
		return ""
	}
	for _, filter := range request.Query.Bool.Filter {
		if operationID := filter.MatchPhrase["operation_id"]; operationID != "" {
			return operationID
		}
	}
	return ""
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

type handlerTransport struct {
	handler http.Handler
}

func (transport handlerTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	recorder := httptest.NewRecorder()
	transport.handler.ServeHTTP(recorder, request)
	response := recorder.Result()
	response.Request = request
	return response, nil
}
