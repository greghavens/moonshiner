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

// Options controls one isolated mock's responses.
type Options struct {
	ContractPath      string
	ExpectedToken     string
	CreateStatus      int
	CreateBody        []byte
	CreateContentType string
	DeleteStatus      int
	DeleteBody        []byte
	DeleteContentType string
	RedirectLocation  string
}

// RequestRecord is one flushed JSONL request-log entry.
type RequestRecord struct {
	OperationID string      `json:"operationId"`
	Method      string      `json:"method"`
	RequestURI  string      `json:"requestURI"`
	Headers     http.Header `json:"headers"`
	Body        string      `json:"body"`
}

// Server is an ephemeral loopback mock whose allow-list comes from the
// protected focused contract.
type Server struct {
	routes        []route
	expectedToken string
	options       Options

	mu      sync.Mutex
	logFile *os.File
	logPath string
	logErr  error

	httpServer *httptest.Server
	baseURL    string
	client     *http.Client
}

// Start loads the focused contract and starts an IPv4 loopback listener.
func Start(options Options) (*Server, error) {
	routes, err := loadRoutes(options.ContractPath)
	if err != nil {
		return nil, err
	}
	logFile, err := os.CreateTemp("", "vcf91-0185-requests-*.jsonl")
	if err != nil {
		return nil, err
	}
	server := &Server{
		routes:        routes,
		expectedToken: options.ExpectedToken,
		options:       options,
		logFile:       logFile,
		logPath:       logFile.Name(),
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		if !errors.Is(err, syscall.EPERM) &&
			!errors.Is(err, syscall.EACCES) &&
			!errors.Is(err, syscall.EAFNOSUPPORT) {
			_ = logFile.Close()
			_ = os.Remove(logFile.Name())
			return nil, err
		}
		server.baseURL = "http://127.0.0.1"
		server.client = &http.Client{Transport: inProcessTransport{handler: server}}
		return server, nil
	}
	testServer := httptest.NewUnstartedServer(server)
	testServer.Listener = listener
	testServer.Start()
	server.httpServer = testServer
	server.baseURL = testServer.URL
	server.client = testServer.Client()
	return server, nil
}

func loadRoutes(path string) ([]route, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var document contractDocument
	if err := json.Unmarshal(raw, &document); err != nil {
		return nil, err
	}
	var routes []route
	for pathTemplate, pathItem := range document.Paths {
		for method, rawOperation := range pathItem {
			var operation operation
			if err := json.Unmarshal(rawOperation, &operation); err != nil || operation.OperationID == "" {
				continue
			}
			routes = append(routes, route{
				Method:       strings.ToUpper(method),
				PathTemplate: pathTemplate,
				OperationID:  operation.OperationID,
			})
		}
	}
	sort.Slice(routes, func(i, j int) bool {
		return routes[i].OperationID < routes[j].OperationID
	})
	if len(routes) != 2 {
		return nil, fmt.Errorf("focused contract contains %d operations, want 2", len(routes))
	}
	expected := []route{
		{Method: http.MethodPost, PathTemplate: "/api/v2/logs/forwarders", OperationID: "createLogForwarder"},
		{Method: http.MethodDelete, PathTemplate: "/api/v2/logs/forwarders/{id}", OperationID: "deleteLogForwarder"},
	}
	for index := range expected {
		if routes[index] != expected[index] {
			return nil, errors.New("focused contract operations changed")
		}
	}
	return routes, nil
}

// URL returns the mock's HTTP origin.
func (server *Server) URL() string {
	return server.baseURL
}

// Client returns a client connected either to the loopback listener or, when
// sockets are prohibited, to the same handler at request level.
func (server *Server) Client() *http.Client {
	return server.client
}

// Close shuts down the server and removes its request-log file.
func (server *Server) Close() {
	if server.httpServer != nil {
		server.httpServer.Close()
	}
	server.mu.Lock()
	if server.logFile != nil {
		_ = server.logFile.Close()
		server.logFile = nil
	}
	path := server.logPath
	server.mu.Unlock()
	if path != "" {
		_ = os.Remove(path)
	}
}

// ReadLog reads the synchronized, flushed, fsynced JSONL request log.
func (server *Server) ReadLog() ([]RequestRecord, error) {
	server.mu.Lock()
	if server.logFile != nil {
		if err := server.logFile.Sync(); err != nil && server.logErr == nil {
			server.logErr = err
		}
	}
	path := server.logPath
	logErr := server.logErr
	server.mu.Unlock()
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

func (server *Server) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	matched, ok := server.match(request)
	if !ok {
		writeJSON(writer, http.StatusNotFound, []byte(`{"errorCode":"API_ERROR","errorMessage":"operation is not in focused contract"}`))
		return
	}
	var body []byte
	if request.Body != nil {
		var err error
		body, err = io.ReadAll(io.LimitReader(request.Body, maxRequestBody+1))
		if err != nil || len(body) > maxRequestBody {
			writeJSON(writer, http.StatusBadRequest, []byte(`{"errorCode":"JSON_FORMAT_ERROR","errorMessage":"invalid request body"}`))
			return
		}
	}
	record := RequestRecord{
		OperationID: matched.OperationID,
		Method:      request.Method,
		RequestURI:  request.RequestURI,
		Headers:     request.Header.Clone(),
		Body:        string(body),
	}
	if err := server.appendRecord(record); err != nil {
		writeJSON(writer, http.StatusInternalServerError, []byte(`{"errorCode":"INTERNAL_SERVER_ERROR"}`))
		return
	}
	if !oneHeader(request.Header, "Accept", "application/json") ||
		!oneHeader(request.Header, "X-JWT-Token", server.expectedToken) {
		writeJSON(writer, http.StatusForbidden, []byte(`{"errorCode":"SECURITY_ERROR","errorMessage":"authorization required"}`))
		return
	}

	switch matched.OperationID {
	case "createLogForwarder":
		server.serveCreate(writer, request, body)
	case "deleteLogForwarder":
		server.serveDelete(writer, request, body)
	default:
		panic("route allow-list and handler diverged")
	}
}

func (server *Server) serveCreate(writer http.ResponseWriter, request *http.Request, body []byte) {
	if !oneHeader(request.Header, "Content-Type", "application/json") {
		writeJSON(writer, http.StatusBadRequest, []byte(`{"errorCode":"JSON_FORMAT_ERROR","errorMessage":"JSON media headers required"}`))
		return
	}
	var object map[string]json.RawMessage
	if err := json.Unmarshal(body, &object); err != nil || object == nil {
		writeJSON(writer, http.StatusBadRequest, []byte(`{"errorCode":"JSON_FORMAT_ERROR","errorMessage":"JSON object required"}`))
		return
	}
	if _, exists := object["id"]; exists {
		writeJSON(writer, http.StatusBadRequest, []byte(`{"errorCode":"VALIDATION_ERROR","errorMessage":"id is read-only"}`))
		return
	}
	status := server.options.CreateStatus
	if status == 0 {
		status = http.StatusCreated
	}
	responseBody := append([]byte(nil), server.options.CreateBody...)
	responseType := server.options.CreateContentType
	if status != http.StatusCreated && responseBody == nil {
		responseBody = []byte(`{"errorCode":"API_ERROR","errorMessage":"configured create failure"}`)
	}
	if status == http.StatusCreated && responseBody == nil {
		responseBody = []byte(`{"id":"new-forwarder"}`)
	}
	if responseType == "" {
		responseType = "application/json"
	}
	if server.options.RedirectLocation != "" {
		writer.Header().Set("Location", server.options.RedirectLocation)
	}
	writeStatus(writer, status, responseBody, responseType)
}

func (server *Server) serveDelete(writer http.ResponseWriter, request *http.Request, body []byte) {
	if len(request.Header.Values("Content-Type")) != 0 || len(body) != 0 {
		writeJSON(writer, http.StatusBadRequest, []byte(`{"errorCode":"JSON_FORMAT_ERROR","errorMessage":"DELETE must have no body or content type"}`))
		return
	}
	status := server.options.DeleteStatus
	if status == 0 {
		status = http.StatusNoContent
	}
	responseBody := append([]byte(nil), server.options.DeleteBody...)
	responseType := server.options.DeleteContentType
	if status != http.StatusNoContent && responseBody == nil {
		responseBody = []byte(`{"errorCode":"API_ERROR","errorMessage":"configured delete failure"}`)
	}
	if responseType == "" && len(responseBody) != 0 {
		responseType = "application/json"
	}
	if server.options.RedirectLocation != "" {
		writer.Header().Set("Location", server.options.RedirectLocation)
	}
	writeStatus(writer, status, responseBody, responseType)
}

func (server *Server) match(request *http.Request) (route, bool) {
	if request.URL.RawQuery != "" || request.URL.ForceQuery {
		return route{}, false
	}
	escapedPath := request.URL.EscapedPath()
	for _, candidate := range server.routes {
		if request.Method != candidate.Method {
			continue
		}
		if !strings.Contains(candidate.PathTemplate, "{id}") {
			if escapedPath == candidate.PathTemplate {
				return candidate, true
			}
			continue
		}
		prefix := strings.TrimSuffix(candidate.PathTemplate, "{id}")
		if !strings.HasPrefix(escapedPath, prefix) {
			continue
		}
		segment := strings.TrimPrefix(escapedPath, prefix)
		if segment != "" && !strings.Contains(segment, "/") {
			return candidate, true
		}
	}
	return route{}, false
}

func (server *Server) appendRecord(record RequestRecord) error {
	server.mu.Lock()
	defer server.mu.Unlock()
	if server.logErr != nil {
		return server.logErr
	}
	raw, err := json.Marshal(record)
	if err == nil {
		_, err = server.logFile.Write(append(raw, '\n'))
	}
	if err == nil {
		err = server.logFile.Sync()
	}
	if err != nil {
		server.logErr = err
	}
	return err
}

func oneHeader(header http.Header, name, value string) bool {
	var values []string
	for key, candidates := range header {
		if strings.EqualFold(key, name) {
			values = append(values, candidates...)
		}
	}
	return len(values) == 1 && values[0] == value
}

func writeJSON(writer http.ResponseWriter, status int, body []byte) {
	writeStatus(writer, status, body, "application/json")
}

func writeStatus(writer http.ResponseWriter, status int, body []byte, contentType string) {
	if contentType != "" {
		writer.Header().Set("Content-Type", contentType)
	}
	writer.Header().Set("Content-Length", fmt.Sprint(len(body)))
	writer.WriteHeader(status)
	_, _ = writer.Write(body)
}

type inProcessTransport struct {
	handler http.Handler
}

func (transport inProcessTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	serverRequest := request.Clone(request.Context())
	serverRequest.RequestURI = request.URL.RequestURI()
	recorder := httptest.NewRecorder()
	transport.handler.ServeHTTP(recorder, serverRequest)
	response := recorder.Result()
	response.Request = request
	return response, nil
}
