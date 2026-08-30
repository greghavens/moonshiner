// Package contractmock provides the protected loopback SDDC Manager fixture.
package contractmock

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"sync"
)

const DeleteDepotSettings = "deleteDepotSettings"

// Plan selects a contract-valid failure scenario.
type Plan struct {
	FailFirstAfterApply bool
	RejectStatus        int
}

// Request is one request observed by the loopback server.
type Request struct {
	OperationID      string
	Method           string
	RequestURI       string
	Path             string
	RawQuery         string
	Host             string
	Header           http.Header
	ContentLength    int64
	TransferEncoding []string
	Body             []byte
	ResponseStatus   int
}

// Server is a contract-scoped loopback SDDC Manager.
type Server struct {
	httpServer *httptest.Server
	plan       Plan
	token      string
	method     string
	path       string
	queryName  string

	mu           sync.Mutex
	requests     []Request
	requestCount int
	configured   bool
	effectCount  int
}

type contractFile struct {
	Operations []struct {
		OperationID     string `json:"operationId"`
		Method          string `json:"method"`
		Path            string `json:"path"`
		QueryParameters []struct {
			Name     string `json:"name"`
			In       string `json:"in"`
			Required bool   `json:"required"`
		} `json:"query_parameters"`
	} `json:"operations"`
}

// New starts a loopback server on an ephemeral IPv4 address.
func New(plan Plan) *Server {
	method, path, queryName := loadPinnedRoute()
	server := &Server{
		plan:       plan,
		token:      randomValue("access"),
		method:     method,
		path:       path,
		queryName:  queryName,
		configured: true,
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		panic("cannot start loopback contract mock")
	}
	server.httpServer = &httptest.Server{
		Listener: listener,
		Config:   &http.Server{Handler: http.HandlerFunc(server.serveHTTP)},
	}
	server.httpServer.Start()
	return server
}

// Close stops the loopback server.
func (s *Server) Close() {
	s.httpServer.Close()
}

// URL returns the loopback origin.
func (s *Server) URL() string {
	return s.httpServer.URL
}

// Client returns the loopback server's HTTP client.
func (s *Server) Client() *http.Client {
	return s.httpServer.Client()
}

// Token returns the per-server bearer token generated at runtime.
func (s *Server) Token() string {
	return s.token
}

// Requests returns a deep copy of the race-safe request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	out := make([]Request, len(s.requests))
	for index, request := range s.requests {
		out[index] = request
		out[index].Header = request.Header.Clone()
		out[index].TransferEncoding = append([]string(nil), request.TransferEncoding...)
		out[index].Body = append([]byte(nil), request.Body...)
	}
	return out
}

// EffectCount reports actual configured-to-absent state transitions.
func (s *Server) EffectCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.effectCount
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	operationID := ""
	if r.Method == s.method && r.URL.Path == s.path {
		operationID = DeleteDepotSettings
	}

	status := s.handle(operationID, r, body)
	s.record(Request{
		OperationID:      operationID,
		Method:           r.Method,
		RequestURI:       r.RequestURI,
		Path:             r.URL.Path,
		RawQuery:         r.URL.RawQuery,
		Host:             r.Host,
		Header:           r.Header.Clone(),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
		Body:             append([]byte(nil), body...),
		ResponseStatus:   status,
	})

	switch status {
	case http.StatusNoContent:
		w.WriteHeader(status)
	case http.StatusNotFound:
		writeError(w, status, "NOT_IN_CONTRACT")
	default:
		writeError(w, status, statusCode(status))
	}
}

func (s *Server) handle(operationID string, r *http.Request, body []byte) int {
	if operationID == "" {
		return http.StatusNotFound
	}
	if r.Header.Get("Authorization") != "Bearer "+s.token ||
		r.Header.Get("Accept") != "application/json" ||
		r.Header.Get("Content-Type") != "" ||
		len(body) != 0 {
		return http.StatusBadRequest
	}
	if !validQuery(r.URL.Query(), s.queryName) {
		return http.StatusBadRequest
	}
	if s.plan.RejectStatus != 0 {
		return s.plan.RejectStatus
	}

	s.mu.Lock()
	s.requestCount++
	requestNumber := s.requestCount
	if s.configured {
		s.configured = false
		s.effectCount++
	}
	s.mu.Unlock()

	if s.plan.FailFirstAfterApply && requestNumber == 1 {
		return http.StatusInternalServerError
	}
	return http.StatusNoContent
}

func validQuery(query url.Values, name string) bool {
	if len(query) == 0 {
		return true
	}
	values, ok := query[name]
	return ok && len(query) == 1 && len(values) == 1 && values[0] != ""
}

func (s *Server) record(request Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, request)
}

func loadPinnedRoute() (string, string, string) {
	_, sourceFile, _, ok := runtime.Caller(0)
	if !ok {
		panic("cannot locate contract mock source")
	}
	contractPath := filepath.Join(
		filepath.Dir(sourceFile),
		"..",
		"..",
		"docs",
		"contract.json",
	)
	content, err := os.ReadFile(contractPath)
	if err != nil {
		panic("cannot read protected contract")
	}
	var contract contractFile
	if err := json.Unmarshal(content, &contract); err != nil {
		panic("cannot decode protected contract")
	}
	if len(contract.Operations) != 1 {
		panic("contract mock requires exactly one operation")
	}
	operation := contract.Operations[0]
	if operation.OperationID != DeleteDepotSettings ||
		operation.Method != http.MethodDelete ||
		operation.Path != "/v1/system/settings/depot" ||
		len(operation.QueryParameters) != 1 ||
		operation.QueryParameters[0].Name != "depotType" ||
		operation.QueryParameters[0].In != "query" ||
		operation.QueryParameters[0].Required {
		panic("protected contract does not match the loopback route")
	}
	return operation.Method, operation.Path, operation.QueryParameters[0].Name
}

func writeError(w http.ResponseWriter, status int, code string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"errorCode":          code,
		"message":            "the depot settings request did not complete",
		"remediationMessage": "retry only when the outcome is safe",
		"referenceToken":     "loopback-reference",
	})
}

func statusCode(status int) string {
	switch status {
	case http.StatusBadRequest:
		return "BAD_REQUEST"
	case http.StatusInternalServerError:
		return "INTERNAL_SERVER_ERROR"
	default:
		return "UNEXPECTED_STATUS"
	}
}

func randomValue(prefix string) string {
	var bytes [16]byte
	if _, err := rand.Read(bytes[:]); err != nil {
		panic("cannot create loopback fixture token")
	}
	return prefix + "-" + hex.EncodeToString(bytes[:])
}
