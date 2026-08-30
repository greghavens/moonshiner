// Package contractmock provides the protected, contract-pinned loopback fixture.
package contractmock

import (
	"bufio"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"sync"
)

// Request is one request persisted in the mock's JSONL request log.
type Request struct {
	OperationID      string      `json:"operation_id"`
	Method           string      `json:"method"`
	Path             string      `json:"path"`
	RawQuery         string      `json:"raw_query"`
	RequestURI       string      `json:"request_uri"`
	Host             string      `json:"host"`
	Header           http.Header `json:"header"`
	ContentLength    int64       `json:"content_length"`
	TransferEncoding []string    `json:"transfer_encoding"`
	Body             string      `json:"body"`
}

// Secrets contains per-server values generated at runtime.
type Secrets struct {
	SessionID string
	Markers   []string
}

// Plan controls contract-valid response data and selected failure cases.
type Plan struct {
	Categories       []map[string]any
	PageWidth        int
	StatusCode       int
	FailContinuation bool
	RepeatMarker     bool
	MutatePage       func(pageIndex int, payload map[string]any)
}

type routeContract struct {
	operationID   string
	method        string
	path          string
	sessionHeader string
	queryNames    map[string]bool
}

// Server is a contract-scoped loopback vCenter.
type Server struct {
	httpServer *httptest.Server
	route      routeContract
	plan       Plan
	secrets    Secrets
	logPath    string

	mu             sync.Mutex
	logFile        *os.File
	traversalCount int
	reverseItems   bool
}

// New loads the focused contract and starts a loopback server on an ephemeral port.
func New(contractPath, logPath string, plan Plan) (*Server, error) {
	route, err := loadRoute(contractPath)
	if err != nil {
		return nil, err
	}
	if logPath == "" {
		return nil, errors.New("request log path is required")
	}
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, errors.New("cannot create request log")
	}
	if plan.PageWidth < 1 {
		plan.PageWidth = 2
	}
	plan.Categories = cloneObjects(plan.Categories)

	pageCount := 0
	if len(plan.Categories) > 0 {
		pageCount = (len(plan.Categories) + plan.PageWidth - 1) / plan.PageWidth
	}
	markers := make([]string, 0, max(0, pageCount-1))
	for index := 0; index+1 < pageCount; index++ {
		markers = append(markers, "cursor/"+strconv.Itoa(index+1)+"+"+randomValue())
	}

	server := &Server{
		route:   route,
		plan:    plan,
		secrets: Secrets{SessionID: "session-" + randomValue(), Markers: markers},
		logPath: logPath,
		logFile: logFile,
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		_ = logFile.Close()
		return nil, errors.New("cannot start loopback contract mock")
	}
	server.httpServer = &httptest.Server{
		Listener: listener,
		Config:   &http.Server{Handler: http.HandlerFunc(server.serveHTTP)},
	}
	server.httpServer.Start()
	return server, nil
}

// Close stops the server and closes its request log.
func (s *Server) Close() {
	s.httpServer.Close()
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.logFile != nil {
		_ = s.logFile.Close()
		s.logFile = nil
	}
}

// URL returns the loopback server origin.
func (s *Server) URL() string { return s.httpServer.URL }

// Client returns the loopback server's HTTP client.
func (s *Server) Client() *http.Client { return s.httpServer.Client() }

// Secrets returns a copy of the runtime-generated session and markers.
func (s *Server) Secrets() Secrets {
	return Secrets{
		SessionID: s.secrets.SessionID,
		Markers:   append([]string(nil), s.secrets.Markers...),
	}
}

// ReadLog reads a race-safe snapshot of the fsynced JSONL request log.
func (s *Server) ReadLog() ([]Request, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.logFile != nil {
		if err := s.logFile.Sync(); err != nil {
			return nil, errors.New("cannot sync request log")
		}
	}
	file, err := os.Open(s.logPath)
	if err != nil {
		return nil, errors.New("cannot open request log")
	}
	defer file.Close()

	var requests []Request
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 4096), 1<<20)
	for scanner.Scan() {
		var request Request
		if err := json.Unmarshal(scanner.Bytes(), &request); err != nil {
			return nil, errors.New("request log contains invalid JSON")
		}
		requests = append(requests, request)
	}
	if err := scanner.Err(); err != nil {
		return nil, errors.New("cannot read request log")
	}
	return requests, nil
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	operationID := ""
	if r.Method == s.route.method && r.URL.Path == s.route.path {
		operationID = s.route.operationID
	}
	s.record(Request{
		OperationID:      operationID,
		Method:           r.Method,
		Path:             r.URL.Path,
		RawQuery:         r.URL.RawQuery,
		RequestURI:       r.RequestURI,
		Host:             r.Host,
		Header:           r.Header.Clone(),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
		Body:             string(body),
	})

	if operationID == "" {
		writeJSON(w, http.StatusNotFound, errorEnvelope("NOT_IN_CONTRACT"))
		return
	}
	s.listCategories(w, r)
}

func (s *Server) listCategories(w http.ResponseWriter, r *http.Request) {
	if !s.validQuery(r) {
		writeJSON(w, http.StatusBadRequest, errorEnvelope("INVALID_QUERY"))
		return
	}
	query := r.URL.Query()
	marker := query.Get("marker")
	if s.plan.StatusCode != 0 &&
		s.plan.StatusCode != http.StatusOK &&
		(!s.plan.FailContinuation || marker != "") {
		writeJSON(w, s.plan.StatusCode, map[string]any{
			"messages": []map[string]any{{
				"id":              "mock.failure",
				"default_message": "server text contains " + s.secrets.SessionID,
				"args":            []string{},
			}},
			"error_type": "MOCK_FAILURE",
		})
		return
	}

	pageIndex := 0
	if marker == "" {
		s.mu.Lock()
		s.traversalCount++
		s.reverseItems = s.traversalCount%2 == 0
		s.mu.Unlock()
	} else {
		found := false
		for index, candidate := range s.secrets.Markers {
			if marker == candidate {
				pageIndex = index + 1
				found = true
				break
			}
		}
		if !found {
			writeJSON(w, http.StatusNotFound, errorEnvelope("INVALID_MARKER"))
			return
		}
	}

	start := pageIndex * s.plan.PageWidth
	if start > len(s.plan.Categories) {
		start = len(s.plan.Categories)
	}
	end := min(start+s.plan.PageWidth, len(s.plan.Categories))
	items := cloneObjects(s.plan.Categories[start:end])

	s.mu.Lock()
	reverse := s.reverseItems
	s.mu.Unlock()
	if reverse {
		for left, right := 0, len(items)-1; left < right; left, right = left+1, right-1 {
			items[left], items[right] = items[right], items[left]
		}
	}

	payload := map[string]any{"items": items}
	if pageIndex < len(s.secrets.Markers) {
		next := s.secrets.Markers[pageIndex]
		if s.plan.RepeatMarker && pageIndex > 0 {
			next = s.secrets.Markers[pageIndex-1]
		}
		payload["marker"] = next
	}
	if s.plan.MutatePage != nil {
		s.plan.MutatePage(pageIndex, payload)
	}
	writeJSON(w, http.StatusOK, payload)
}

func (s *Server) validQuery(r *http.Request) bool {
	sessionValues := r.Header.Values(s.route.sessionHeader)
	if len(sessionValues) != 1 || sessionValues[0] != s.secrets.SessionID {
		return false
	}
	query := r.URL.Query()
	for name, values := range query {
		if !s.route.queryNames[name] || len(values) == 0 {
			return false
		}
		for _, value := range values {
			if value == "" {
				return false
			}
		}
	}
	if values, present := query["marker"]; present {
		if len(values) != 1 || len(query["names"]) != 0 {
			return false
		}
	}
	if values, present := query["page_size"]; present {
		if len(values) != 1 {
			return false
		}
		size, err := strconv.ParseInt(values[0], 10, 64)
		if err != nil || size < 1 {
			return false
		}
	}
	if values, present := query["names"]; present && len(values) == 0 {
		return false
	}
	return true
}

func (s *Server) record(request Request) {
	encoded, _ := json.Marshal(request)
	encoded = append(encoded, '\n')

	s.mu.Lock()
	defer s.mu.Unlock()
	if s.logFile == nil {
		return
	}
	_, _ = s.logFile.Write(encoded)
	_ = s.logFile.Sync()
}

type focusedContract struct {
	Servers []struct {
		BasePath string `json:"base_path"`
	} `json:"servers"`
	SecuritySchemes map[string]struct {
		Type string `json:"type"`
		Name string `json:"name"`
		In   string `json:"in"`
	} `json:"security_schemes"`
	Operations []struct {
		OperationID string   `json:"operationId"`
		Method      string   `json:"method"`
		Path        string   `json:"path"`
		Security    []string `json:"security"`
		Parameters  []struct {
			Name      string `json:"name"`
			Style     string `json:"style"`
			Explode   bool   `json:"explode"`
			SchemaRef string `json:"schema_ref"`
			Schema    struct {
				Type string `json:"type"`
			} `json:"schema"`
		} `json:"query_parameters"`
	} `json:"operations"`
	Schemas map[string]struct {
		Properties map[string]json.RawMessage `json:"properties"`
	} `json:"schemas"`
}

func loadRoute(contractPath string) (routeContract, error) {
	data, err := os.ReadFile(contractPath)
	if err != nil {
		return routeContract{}, errors.New("cannot read focused contract")
	}
	var contract focusedContract
	if err := json.Unmarshal(data, &contract); err != nil {
		return routeContract{}, errors.New("focused contract is not valid JSON")
	}
	if len(contract.Servers) != 1 || len(contract.Operations) != 1 ||
		len(contract.Operations[0].Security) != 1 {
		return routeContract{}, errors.New("focused contract must name one operation")
	}
	operation := contract.Operations[0]
	scheme, ok := contract.SecuritySchemes[operation.Security[0]]
	if !ok || scheme.Type != "apiKey" || scheme.In != "header" || scheme.Name == "" {
		return routeContract{}, errors.New("focused contract has invalid security")
	}
	allowed := make(map[string]bool)
	for _, parameter := range operation.Parameters {
		if parameter.Style != "form" || !parameter.Explode {
			return routeContract{}, errors.New("focused contract has unsupported query serialization")
		}
		if parameter.Schema.Type != "" {
			allowed[parameter.Name] = true
			continue
		}
		const prefix = "#/components/schemas/"
		if !strings.HasPrefix(parameter.SchemaRef, prefix) {
			return routeContract{}, errors.New("focused contract has invalid schema reference")
		}
		schema, ok := contract.Schemas[strings.TrimPrefix(parameter.SchemaRef, prefix)]
		if !ok {
			return routeContract{}, errors.New("focused contract schema reference is missing")
		}
		for name := range schema.Properties {
			allowed[name] = true
		}
	}
	return routeContract{
		operationID:   operation.OperationID,
		method:        operation.Method,
		path:          strings.TrimSuffix(contract.Servers[0].BasePath, "/") + operation.Path,
		sessionHeader: scheme.Name,
		queryNames:    allowed,
	}, nil
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func errorEnvelope(code string) map[string]any {
	return map[string]any{
		"messages": []map[string]any{{
			"id":              strings.ToLower(code),
			"default_message": "request did not match the focused operation contract",
			"args":            []string{},
		}},
		"error_type": code,
	}
}

func cloneObjects(input []map[string]any) []map[string]any {
	output := make([]map[string]any, len(input))
	for index, object := range input {
		encoded, _ := json.Marshal(object)
		_ = json.Unmarshal(encoded, &output[index])
	}
	return output
}

func randomValue() string {
	var value [12]byte
	if _, err := rand.Read(value[:]); err != nil {
		panic("cannot generate fixture value")
	}
	return hex.EncodeToString(value[:])
}
