// Package contractmock provides the loopback-only, contract-pinned verifier
// service. It is test infrastructure, not a VMware endpoint implementation.
package contractmock

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"sort"
	"strings"
	"sync"
	"syscall"
)

const maxRequestBody = 1 << 20

type routeKey struct {
	Method string
	Path   string
}

type route struct {
	Method      string
	Path        string
	OperationID string
}

type operation struct {
	OperationID string `json:"operationId"`
}

type contractDocument struct {
	Paths map[string]map[string]json.RawMessage `json:"paths"`
}

// Options controls responses for one isolated mock instance.
type Options struct {
	ContractPath  string
	ExpectedToken string

	PrecheckStatus       int
	PrecheckResponseBody []byte
	PrecheckContentType  string

	CreateStatus       int
	CreateResponseBody []byte
	CreateContentType  string
	CreatedID          string
}

// RequestRecord is one flushed JSONL request-log entry.
type RequestRecord struct {
	OperationID      string      `json:"operationId"`
	Method           string      `json:"method"`
	RequestURI       string      `json:"requestURI"`
	Headers          http.Header `json:"headers"`
	Body             string      `json:"body"`
	ContentLength    int64       `json:"contentLength"`
	TransferEncoding []string    `json:"transferEncoding"`
}

// Server is an ephemeral IPv4 loopback mock whose complete route allow-list is
// loaded from docs/contract.json.
type Server struct {
	routes        map[routeKey]route
	expectedToken string

	precheckStatus int
	precheckBody   []byte
	precheckType   string
	createStatus   int
	createBody     []byte
	createType     string
	createdID      string

	mu      sync.Mutex
	effects int
	logFile *os.File
	logPath string
	logErr  error

	httpServer *httptest.Server
	baseURL    string
	client     *http.Client
}

// Start loads the focused contract and starts an ephemeral 127.0.0.1 service.
func Start(opts Options) (*Server, error) {
	routes, err := loadRoutes(opts.ContractPath)
	if err != nil {
		return nil, err
	}
	logFile, err := os.CreateTemp("", "vcf91-0186-requests-*.jsonl")
	if err != nil {
		return nil, err
	}
	s := &Server{
		routes:         routes,
		expectedToken:  opts.ExpectedToken,
		precheckStatus: opts.PrecheckStatus,
		precheckBody:   append([]byte(nil), opts.PrecheckResponseBody...),
		precheckType:   opts.PrecheckContentType,
		createStatus:   opts.CreateStatus,
		createBody:     append([]byte(nil), opts.CreateResponseBody...),
		createType:     opts.CreateContentType,
		createdID:      opts.CreatedID,
		logFile:        logFile,
		logPath:        logFile.Name(),
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		if errors.Is(err, syscall.EPERM) ||
			errors.Is(err, syscall.EACCES) ||
			errors.Is(err, syscall.EAFNOSUPPORT) {
			s.baseURL = "http://127.0.0.1"
			s.client = &http.Client{Transport: inProcessTransport{handler: s}}
			return s, nil
		}
		_ = logFile.Close()
		_ = os.Remove(logFile.Name())
		return nil, fmt.Errorf("start loopback listener: %w", err)
	}
	ts := httptest.NewUnstartedServer(s)
	ts.Listener = listener
	ts.Start()
	s.httpServer = ts
	s.baseURL = ts.URL
	s.client = ts.Client()
	return s, nil
}

func loadRoutes(path string) (map[routeKey]route, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var doc contractDocument
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, err
	}
	routes := make(map[routeKey]route)
	for path, pathItem := range doc.Paths {
		for method, rawOperation := range pathItem {
			var op operation
			if err := json.Unmarshal(rawOperation, &op); err != nil || op.OperationID == "" {
				continue
			}
			key := routeKey{Method: strings.ToUpper(method), Path: path}
			routes[key] = route{
				Method:      key.Method,
				Path:        path,
				OperationID: op.OperationID,
			}
		}
	}
	expected := map[routeKey]string{
		{Method: http.MethodPost, Path: "/api/v2/logs/forwarders/test"}: "testLogForwarderConnection",
		{Method: http.MethodPost, Path: "/api/v2/logs/forwarders"}:      "createLogForwarder",
	}
	if len(routes) != len(expected) {
		return nil, fmt.Errorf("focused contract contains %d operations, want %d", len(routes), len(expected))
	}
	for key, operationID := range expected {
		if actual, ok := routes[key]; !ok || actual.OperationID != operationID {
			return nil, errors.New("focused contract operation changed")
		}
	}
	return routes, nil
}

// URL returns the mock's loopback HTTP origin.
func (s *Server) URL() string {
	return s.baseURL
}

// Client returns the HTTP client associated with the loopback service.
func (s *Server) Client() *http.Client {
	return s.client
}

// Close shuts down the service and removes its request-log file.
func (s *Server) Close() {
	if s.httpServer != nil {
		s.httpServer.Close()
	}
	s.mu.Lock()
	if s.logFile != nil {
		_ = s.logFile.Close()
		s.logFile = nil
	}
	path := s.logPath
	s.logPath = ""
	s.mu.Unlock()
	if path != "" {
		_ = os.Remove(path)
	}
}

// Effects returns the number of accepted createLogForwarder mutations.
func (s *Server) Effects() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.effects
}

// ReadLog reads the synchronized, flushed, fsynced JSONL request log.
func (s *Server) ReadLog() ([]RequestRecord, error) {
	s.mu.Lock()
	if s.logFile != nil {
		if err := s.logFile.Sync(); err != nil && s.logErr == nil {
			s.logErr = err
		}
	}
	path := s.logPath
	logErr := s.logErr
	s.mu.Unlock()
	if logErr != nil {
		return nil, logErr
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	trimmed := strings.TrimSpace(string(raw))
	if trimmed == "" {
		return []RequestRecord{}, nil
	}
	lines := strings.Split(trimmed, "\n")
	records := make([]RequestRecord, 0, len(lines))
	for _, line := range lines {
		var record RequestRecord
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			return nil, err
		}
		records = append(records, record)
	}
	return records, nil
}

// OperationIDs returns the sorted operationIds that the contract allowed.
func (s *Server) OperationIDs() []string {
	ids := make([]string, 0, len(s.routes))
	for _, r := range s.routes {
		ids = append(ids, r.OperationID)
	}
	sort.Strings(ids)
	return ids
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.RawQuery != "" || r.URL.ForceQuery {
		writeJSON(w, http.StatusNotFound, []byte(`{"errorCode":"API_ERROR"}`))
		return
	}
	key := routeKey{Method: r.Method, Path: r.URL.EscapedPath()}
	matched, ok := s.routes[key]
	if !ok {
		writeJSON(w, http.StatusNotFound, []byte(`{"errorCode":"API_ERROR"}`))
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, maxRequestBody+1))
	if err != nil || len(body) > maxRequestBody {
		writeJSON(w, http.StatusBadRequest, []byte(`{"errorCode":"JSON_FORMAT_ERROR"}`))
		return
	}
	record := RequestRecord{
		OperationID:      matched.OperationID,
		Method:           r.Method,
		RequestURI:       r.RequestURI,
		Headers:          r.Header.Clone(),
		Body:             string(body),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
	}
	if err := s.appendRecord(record); err != nil {
		writeJSON(w, http.StatusInternalServerError, []byte(`{"errorCode":"INTERNAL_SERVER_ERROR"}`))
		return
	}
	if !validRequest(r, body, s.expectedToken) {
		writeJSON(w, http.StatusBadRequest, []byte(`{"errorCode":"VALIDATION_ERROR"}`))
		return
	}

	switch matched.OperationID {
	case "testLogForwarderConnection":
		s.handlePrecheck(w)
	case "createLogForwarder":
		s.handleCreate(w, body)
	default:
		writeJSON(w, http.StatusNotFound, []byte(`{"errorCode":"API_ERROR"}`))
	}
}

func validRequest(r *http.Request, body []byte, expectedToken string) bool {
	if !oneHeader(r.Header, "Accept", "application/json") ||
		!oneHeader(r.Header, "Content-Type", "application/json") ||
		!oneHeader(r.Header, "X-JWT-Token", expectedToken) ||
		r.ContentLength != int64(len(body)) ||
		len(r.TransferEncoding) != 0 {
		return false
	}
	var object map[string]json.RawMessage
	if err := json.Unmarshal(body, &object); err != nil || object == nil {
		return false
	}
	_, hasReadOnlyID := object["id"]
	return !hasReadOnlyID
}

func (s *Server) handlePrecheck(w http.ResponseWriter) {
	status := s.precheckStatus
	if status == 0 {
		status = http.StatusOK
	}
	if status != http.StatusOK {
		writeJSON(w, status, []byte(`{"errorCode":"TEST_ERROR"}`))
		return
	}
	if s.precheckBody != nil {
		contentType := s.precheckType
		if contentType == "" {
			contentType = "application/json"
		}
		writeBody(w, status, s.precheckBody, contentType)
		return
	}
	w.WriteHeader(status)
}

func (s *Server) handleCreate(w http.ResponseWriter, body []byte) {
	status := s.createStatus
	if status == 0 {
		status = http.StatusCreated
	}
	if status != http.StatusCreated {
		writeJSON(w, status, []byte(`{"errorCode":"API_ERROR"}`))
		return
	}

	s.mu.Lock()
	s.effects++
	s.mu.Unlock()

	responseBody := append([]byte(nil), s.createBody...)
	if s.createBody == nil {
		var object map[string]json.RawMessage
		_ = json.Unmarshal(body, &object)
		id := s.createdID
		if id == "" {
			id = "created-by-loopback"
		}
		idJSON, _ := json.Marshal(id)
		object["id"] = idJSON
		responseBody, _ = json.Marshal(object)
	}
	contentType := s.createType
	if contentType == "" {
		contentType = "application/json"
	}
	writeBody(w, status, responseBody, contentType)
}

func (s *Server) appendRecord(record RequestRecord) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.logErr != nil {
		return s.logErr
	}
	raw, err := json.Marshal(record)
	if err == nil {
		_, err = s.logFile.Write(append(raw, '\n'))
	}
	if err == nil {
		err = s.logFile.Sync()
	}
	if err != nil {
		s.logErr = err
	}
	return err
}

func oneHeader(header http.Header, name, value string) bool {
	values := header.Values(name)
	return len(values) == 1 && values[0] == value
}

func writeJSON(w http.ResponseWriter, status int, body []byte) {
	writeBody(w, status, body, "application/json")
}

func writeBody(w http.ResponseWriter, status int, body []byte, contentType string) {
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Content-Length", fmt.Sprint(len(body)))
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

// inProcessTransport retains the loopback URL and runs the same contract
// handler only when the execution sandbox prohibits creating an IPv4 socket.
type inProcessTransport struct {
	handler http.Handler
}

func (transport inProcessTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	select {
	case <-request.Context().Done():
		return nil, request.Context().Err()
	default:
	}
	serverRequest := request.Clone(request.Context())
	serverRequest.RequestURI = request.URL.RequestURI()
	recorder := httptest.NewRecorder()
	transport.handler.ServeHTTP(recorder, serverRequest)
	response := recorder.Result()
	response.Request = request
	return response, nil
}
