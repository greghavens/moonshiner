// Package contractmock provides the loopback-only, contract-pinned verifier
// service. It is test infrastructure, not a VMware endpoint implementation.
package contractmock

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync"
	"syscall"
)

const maxRequestBody = 1 << 20

type route struct {
	Method       string
	PathTemplate string
	OperationID  string
}

type operation struct {
	OperationID string `json:"operationId"`
}

type contractDocument struct {
	Paths map[string]map[string]json.RawMessage `json:"paths"`
}

// Options controls dynamic failure behavior for one isolated mock.
type Options struct {
	ContractPath        string
	ExpectedToken       string
	LoseResponses       int
	ResponseStatus      int
	ResponseBody        []byte
	ResponseContentType string
	RedirectLocation    string
}

// RequestRecord is one flushed JSONL request-log entry.
type RequestRecord struct {
	OperationID string      `json:"operationId"`
	Method      string      `json:"method"`
	RequestURI  string      `json:"requestURI"`
	Headers     http.Header `json:"headers"`
	Body        string      `json:"body"`
}

// Server is an ephemeral loopback mock whose route comes from contract.json.
type Server struct {
	route         route
	expectedToken string

	mu             sync.Mutex
	loseResponses  int
	responseStatus int
	responseBody   []byte
	responseType   string
	redirectTo     string
	resourceKey    string
	effects        int
	logFile        *os.File
	logPath        string
	logErr         error

	httpServer *httptest.Server
	baseURL    string
	client     *http.Client
}

// Start loads the focused contract and starts an IPv4 loopback listener.
func Start(opts Options) (*Server, error) {
	r, err := loadOnlyRoute(opts.ContractPath)
	if err != nil {
		return nil, err
	}
	logFile, err := os.CreateTemp("", "vcf91-0184-requests-*.jsonl")
	if err != nil {
		return nil, err
	}
	s := &Server{
		route:          r,
		expectedToken:  opts.ExpectedToken,
		loseResponses:  opts.LoseResponses,
		responseStatus: opts.ResponseStatus,
		responseBody:   append([]byte(nil), opts.ResponseBody...),
		responseType:   opts.ResponseContentType,
		redirectTo:     opts.RedirectLocation,
		logFile:        logFile,
		logPath:        logFile.Name(),
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		if !errors.Is(err, syscall.EPERM) &&
			!errors.Is(err, syscall.EACCES) &&
			!errors.Is(err, syscall.EAFNOSUPPORT) {
			logFile.Close()
			os.Remove(logFile.Name())
			return nil, err
		}
		s.baseURL = "http://127.0.0.1"
		s.client = &http.Client{Transport: inProcessTransport{handler: s}}
		return s, nil
	}
	ts := httptest.NewUnstartedServer(s)
	ts.Listener = listener
	ts.Start()
	s.httpServer = ts
	s.baseURL = ts.URL
	s.client = ts.Client()
	return s, nil
}

func loadOnlyRoute(path string) (route, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return route{}, err
	}
	var doc contractDocument
	if err := json.Unmarshal(raw, &doc); err != nil {
		return route{}, err
	}
	var routes []route
	for pathTemplate, pathItem := range doc.Paths {
		for method, rawOperation := range pathItem {
			var op operation
			if err := json.Unmarshal(rawOperation, &op); err != nil || op.OperationID == "" {
				continue
			}
			routes = append(routes, route{
				Method:       strings.ToUpper(method),
				PathTemplate: pathTemplate,
				OperationID:  op.OperationID,
			})
		}
	}
	if len(routes) != 1 {
		return route{}, fmt.Errorf("focused contract contains %d operations, want 1", len(routes))
	}
	r := routes[0]
	if r.Method != http.MethodPut ||
		r.PathTemplate != "/api/v2/logs/forwarders/{id}" ||
		r.OperationID != "updateLogForwarder" {
		return route{}, errors.New("focused contract operation changed")
	}
	return r, nil
}

// URL returns the mock's HTTP origin.
func (s *Server) URL() string {
	return s.baseURL
}

// Client returns an HTTP client connected either to the loopback listener or,
// when sockets are prohibited, to the same handler at request level.
func (s *Server) Client() *http.Client {
	return s.client
}

// Close shuts down the server and removes its request-log file.
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
	s.mu.Unlock()
	if path != "" {
		_ = os.Remove(path)
	}
}

// Effects returns the number of distinct logical replacements.
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
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) == 1 && lines[0] == "" {
		return []RequestRecord{}, nil
	}
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

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	segment, ok := s.match(r)
	if !ok {
		writeJSON(w, http.StatusNotFound, []byte(`{"errorCode":"API_ERROR","errorMessage":"operation is not in focused contract"}`))
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, maxRequestBody+1))
	if err != nil || len(body) > maxRequestBody {
		writeJSON(w, http.StatusBadRequest, []byte(`{"errorCode":"JSON_FORMAT_ERROR","errorMessage":"invalid request body"}`))
		return
	}
	record := RequestRecord{
		OperationID: s.route.OperationID,
		Method:      r.Method,
		RequestURI:  r.RequestURI,
		Headers:     r.Header.Clone(),
		Body:        string(body),
	}
	if err := s.appendRecord(record); err != nil {
		writeJSON(w, http.StatusInternalServerError, []byte(`{"errorCode":"INTERNAL_SERVER_ERROR"}`))
		return
	}

	var object map[string]json.RawMessage
	if err := json.Unmarshal(body, &object); err != nil || object == nil {
		writeJSON(w, http.StatusBadRequest, []byte(`{"errorCode":"JSON_FORMAT_ERROR","errorMessage":"JSON object required"}`))
		return
	}
	if _, exists := object["id"]; exists {
		writeJSON(w, http.StatusBadRequest, []byte(`{"errorCode":"VALIDATION_ERROR","errorMessage":"id is read-only"}`))
		return
	}
	if !oneHeader(r.Header, "X-JWT-Token", s.expectedToken) {
		writeJSON(w, http.StatusForbidden, []byte(`{"errorCode":"SECURITY_ERROR","errorMessage":"authorization required"}`))
		return
	}
	if !oneHeader(r.Header, "Accept", "application/json") ||
		!oneHeader(r.Header, "Content-Type", "application/json") {
		writeJSON(w, http.StatusBadRequest, []byte(`{"errorCode":"JSON_FORMAT_ERROR","errorMessage":"JSON media headers required"}`))
		return
	}

	s.mu.Lock()
	status := s.responseStatus
	redirectTo := s.redirectTo
	s.mu.Unlock()
	if status != 0 && status != http.StatusOK {
		if redirectTo != "" {
			w.Header().Set("Location", redirectTo)
		}
		writeJSONStatus(w, status, []byte(`{"errorCode":"API_ERROR","errorMessage":"configured HTTP failure"}`), "application/json")
		return
	}

	decodedID, err := url.PathUnescape(segment)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, []byte(`{"errorCode":"VALIDATION_ERROR"}`))
		return
	}
	key := decodedID + "\x00" + string(body)
	s.mu.Lock()
	if s.resourceKey != key {
		s.resourceKey = key
		s.effects++
	}
	lose := s.loseResponses > 0
	if lose {
		s.loseResponses--
	}
	responseBody := append([]byte(nil), s.responseBody...)
	responseType := s.responseType
	s.mu.Unlock()

	if lose {
		hijacker, ok := w.(http.Hijacker)
		if !ok {
			panic("loopback verifier does not support connection hijacking")
		}
		connection, _, err := hijacker.Hijack()
		if err == nil {
			_ = connection.Close()
		}
		return
	}

	if responseBody == nil {
		idJSON, _ := json.Marshal(decodedID)
		object["id"] = idJSON
		responseBody, _ = json.Marshal(object)
	}
	if responseType == "" {
		responseType = "application/json"
	}
	writeJSONStatus(w, http.StatusOK, responseBody, responseType)
}

func (s *Server) match(r *http.Request) (string, bool) {
	if r.Method != s.route.Method || r.URL.RawQuery != "" || r.URL.ForceQuery {
		return "", false
	}
	prefix := strings.TrimSuffix(s.route.PathTemplate, "{id}")
	escapedPath := r.URL.EscapedPath()
	if !strings.HasPrefix(escapedPath, prefix) {
		return "", false
	}
	segment := strings.TrimPrefix(escapedPath, prefix)
	if segment == "" || strings.Contains(segment, "/") {
		return "", false
	}
	return segment, true
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
	writeJSONStatus(w, status, body, "application/json")
}

func writeJSONStatus(w http.ResponseWriter, status int, body []byte, contentType string) {
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Content-Length", fmt.Sprint(len(body)))
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

type inProcessTransport struct {
	handler http.Handler
}

func (transport inProcessTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	serverRequest := request.Clone(request.Context())
	serverRequest.RequestURI = request.URL.RequestURI()
	recorder := &responseCapture{ResponseRecorder: httptest.NewRecorder()}
	transport.handler.ServeHTTP(recorder, serverRequest)
	if recorder.lost {
		return nil, io.ErrUnexpectedEOF
	}
	response := recorder.Result()
	response.Request = request
	return response, nil
}

type responseCapture struct {
	*httptest.ResponseRecorder
	lost bool
}

func (capture *responseCapture) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	capture.lost = true
	left, right := net.Pipe()
	_ = right.Close()
	return left, bufio.NewReadWriter(bufio.NewReader(left), bufio.NewWriter(left)), nil
}
