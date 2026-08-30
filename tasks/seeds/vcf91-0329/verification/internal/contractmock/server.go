// Package contractmock provides the loopback-only, contract-pinned verifier
// service used by the credrotate tests. Its entire route allow-list, its path
// templates and its query-parameter rules are loaded from docs/contract.json,
// so it can serve nothing that the contract does not name. It is test
// infrastructure, not an implementation of a VMware endpoint, and it never
// reaches a network beyond 127.0.0.1.
package contractmock

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"sort"
	"strings"
	"sync"
	"syscall"
)

const maxRequestBody = 1 << 20

// Contract operationIds. They are slugs local to docs/contract.json; VMware
// publishes no operationId values for these reference-documented operations.
const (
	OpRetrieveAuthToken       = "retrieveAuthToken"
	OpUpdateCloudAccountAsync = "updateCloudAccountAsync"
	OpGetRequestTracker       = "getRequestTracker"
	OpGetCloudAccount         = "getCloudAccount"
)

type parameter struct {
	Name     string `json:"name"`
	In       string `json:"in"`
	Required bool   `json:"required"`
}

type operation struct {
	OperationID string      `json:"operationId"`
	Parameters  []parameter `json:"parameters"`
	Security    *[]struct{} `json:"security"`
}

type contractDocument struct {
	Paths map[string]map[string]json.RawMessage `json:"paths"`
}

type route struct {
	Method        string
	Template      string
	Segments      []string
	OperationID   string
	QueryAllowed  map[string]bool
	QueryRequired []string
	Unauthchecked bool
}

// TrackerState is one scripted getRequestTracker reply. The last entry of a
// script repeats for any further poll.
type TrackerState struct {
	Status   string
	Progress int
	Message  string
}

// Options configures one isolated mock instance.
type Options struct {
	// ContractPath is the docs/contract.json that pins the route allow-list.
	ContractPath string
	// RefreshToken is the only value retrieveAuthToken accepts.
	RefreshToken string
	// APIVersion, when set, is the only accepted apiVersion query value.
	APIVersion string
	// CloudAccountID is the only account id the mock knows.
	CloudAccountID string
	// CloudAccountName is reported by getCloudAccount.
	CloudAccountName string
	// TrackerID is the request id issued by updateCloudAccountAsync. It is
	// deliberately distinct from CloudAccountID.
	TrackerID string
	// TrackerScript drives successive getRequestTracker replies.
	TrackerScript []TrackerState
	// RevokeAfterAuthorized revokes the live bearer token immediately after
	// that many authorized non-login requests have been served. Zero disables
	// revocation. Every later request carrying a revoked token gets 401.
	RevokeAfterAuthorized int
}

// RequestRecord is one flushed JSONL request-log entry. Requests that match no
// contract route are logged with an empty OperationID.
type RequestRecord struct {
	OperationID   string              `json:"operationId"`
	Method        string              `json:"method"`
	Path          string              `json:"path"`
	RawQuery      string              `json:"rawQuery"`
	Query         map[string][]string `json:"query"`
	Headers       http.Header         `json:"headers"`
	Body          string              `json:"body"`
	ContentLength int64               `json:"contentLength"`
	Status        int                 `json:"status"`
}

// Authorization returns the single Authorization header value, or "" when the
// header is absent, empty or repeated.
func (r RequestRecord) Authorization() string {
	values := r.Headers.Values("Authorization")
	if len(values) != 1 {
		return ""
	}
	return values[0]
}

// Server is an ephemeral 127.0.0.1 mock pinned to docs/contract.json.
type Server struct {
	routes []route
	opts   Options

	mu           sync.Mutex
	issued       int
	liveToken    string
	authServed   int
	effects      int
	lastPatch    string
	trackerPolls int
	logFile      *os.File
	logPath      string
	logErr       error

	httpServer *httptest.Server
	baseURL    string
	client     *http.Client
}

// Start loads the focused contract and starts an ephemeral loopback service.
func Start(opts Options) (*Server, error) {
	routes, err := loadRoutes(opts.ContractPath)
	if err != nil {
		return nil, err
	}
	if opts.TrackerID == "" {
		opts.TrackerID = "req-tracker-default"
	}
	if opts.CloudAccountID == "" {
		opts.CloudAccountID = "cloud-account-default"
	}
	if len(opts.TrackerScript) == 0 {
		opts.TrackerScript = []TrackerState{{Status: "FINISHED", Progress: 100, Message: "Completed"}}
	}
	logFile, err := os.CreateTemp("", "vcf91-0329-requests-*.jsonl")
	if err != nil {
		return nil, err
	}
	s := &Server{
		routes:  routes,
		opts:    opts,
		logFile: logFile,
		logPath: logFile.Name(),
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

func loadRoutes(path string) ([]route, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var doc contractDocument
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, err
	}
	var routes []route
	for template, pathItem := range doc.Paths {
		for method, rawOperation := range pathItem {
			var op operation
			if err := json.Unmarshal(rawOperation, &op); err != nil || op.OperationID == "" {
				continue
			}
			r := route{
				Method:        strings.ToUpper(method),
				Template:      template,
				Segments:      strings.Split(strings.TrimPrefix(template, "/"), "/"),
				OperationID:   op.OperationID,
				QueryAllowed:  map[string]bool{},
				Unauthchecked: op.Security != nil && len(*op.Security) == 0,
			}
			for _, p := range op.Parameters {
				switch p.In {
				case "path":
					if !strings.Contains(template, "{"+p.Name+"}") {
						return nil, fmt.Errorf("%s declares path parameter %q absent from %s",
							op.OperationID, p.Name, template)
					}
				case "query":
					r.QueryAllowed[p.Name] = true
					if p.Required {
						r.QueryRequired = append(r.QueryRequired, p.Name)
					}
				}
			}
			routes = append(routes, r)
		}
	}
	expected := map[string]string{
		"POST /iaas/api/login":                OpRetrieveAuthToken,
		"PATCH /iaas/api/cloud-accounts/{id}": OpUpdateCloudAccountAsync,
		"GET /iaas/api/cloud-accounts/{id}":   OpGetCloudAccount,
		"GET /iaas/api/request-tracker/{id}":  OpGetRequestTracker,
	}
	if len(routes) != len(expected) {
		return nil, fmt.Errorf("focused contract declares %d operations, want %d", len(routes), len(expected))
	}
	for _, r := range routes {
		if expected[r.Method+" "+r.Template] != r.OperationID {
			return nil, fmt.Errorf("focused contract operation changed: %s %s -> %s", r.Method, r.Template, r.OperationID)
		}
	}
	sort.Slice(routes, func(i, j int) bool { return routes[i].OperationID < routes[j].OperationID })
	return routes, nil
}

// URL returns the mock's loopback HTTP origin.
func (s *Server) URL() string { return s.baseURL }

// Client returns the HTTP client bound to the loopback service.
func (s *Server) Client() *http.Client { return s.client }

// Close shuts the service down and removes its request-log file.
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

// Effects returns the number of accepted updateCloudAccountAsync mutations.
func (s *Server) Effects() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.effects
}

// LastPatchBody returns the body of the most recently accepted mutation.
func (s *Server) LastPatchBody() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.lastPatch
}

// OperationIDs returns the sorted operationIds the contract allows.
func (s *Server) OperationIDs() []string {
	ids := make([]string, 0, len(s.routes))
	for _, r := range s.routes {
		ids = append(ids, r.OperationID)
	}
	sort.Strings(ids)
	return ids
}

// ReadLog reads the synchronized, flushed request log.
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

// RecordsFor returns the logged records for one operationId, in arrival order.
func (s *Server) RecordsFor(operationID string) ([]RequestRecord, error) {
	all, err := s.ReadLog()
	if err != nil {
		return nil, err
	}
	var out []RequestRecord
	for _, record := range all {
		if record.OperationID == operationID {
			out = append(out, record)
		}
	}
	return out, nil
}

func (s *Server) match(r *http.Request) (route, map[string]string, bool) {
	got := strings.Split(strings.TrimPrefix(r.URL.EscapedPath(), "/"), "/")
	for _, candidate := range s.routes {
		if candidate.Method != r.Method || len(candidate.Segments) != len(got) {
			continue
		}
		params := map[string]string{}
		ok := true
		for i, want := range candidate.Segments {
			if strings.HasPrefix(want, "{") && strings.HasSuffix(want, "}") {
				if got[i] == "" {
					ok = false
					break
				}
				name := want[1 : len(want)-1]
				value, err := url.PathUnescape(got[i])
				if err != nil {
					ok = false
					break
				}
				params[name] = value
				continue
			}
			if want != got[i] {
				ok = false
				break
			}
		}
		if ok {
			return candidate, params, true
		}
	}
	return route{}, nil, false
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, readErr := io.ReadAll(io.LimitReader(r.Body, maxRequestBody+1))
	matched, params, routed := s.match(r)
	record := RequestRecord{
		OperationID:   matched.OperationID,
		Method:        r.Method,
		Path:          r.URL.EscapedPath(),
		RawQuery:      r.URL.RawQuery,
		Query:         r.URL.Query(),
		Headers:       r.Header.Clone(),
		Body:          string(body),
		ContentLength: r.ContentLength,
	}

	status, payload := s.dispatch(r, matched, params, routed, body, readErr)
	record.Status = status
	if err := s.appendRecord(record); err != nil {
		writeJSON(w, http.StatusInternalServerError, serviceError(http.StatusInternalServerError, "request log unavailable"))
		return
	}
	writeJSON(w, status, payload)
}

func (s *Server) dispatch(r *http.Request, matched route, params map[string]string, routed bool, body []byte, readErr error) (int, []byte) {
	if !routed {
		return http.StatusNotFound, serviceError(http.StatusNotFound, "no operation in the focused contract serves "+r.Method+" "+r.URL.EscapedPath())
	}
	if readErr != nil || len(body) > maxRequestBody {
		return http.StatusBadRequest, serviceError(http.StatusBadRequest, "unreadable request body")
	}
	if status, payload, bad := s.checkQuery(matched, r.URL.Query()); bad {
		return status, payload
	}
	if status, payload, bad := checkHeaders(matched, r, body); bad {
		return status, payload
	}
	if matched.OperationID == OpRetrieveAuthToken {
		return s.handleLogin(body)
	}
	token, ok := bearer(r.Header)
	if !ok {
		return http.StatusUnauthorized, serviceError(http.StatusUnauthorized, "missing or malformed Authorization header")
	}
	if status, payload, bad := s.authorize(token); bad {
		return status, payload
	}
	switch matched.OperationID {
	case OpUpdateCloudAccountAsync:
		return s.handlePatch(params["id"], body)
	case OpGetRequestTracker:
		return s.handleTracker(params["id"])
	case OpGetCloudAccount:
		return s.handleGetAccount(params["id"])
	}
	return http.StatusNotFound, serviceError(http.StatusNotFound, "unreachable")
}

func (s *Server) checkQuery(matched route, query url.Values) (int, []byte, bool) {
	for name := range query {
		if !matched.QueryAllowed[name] {
			return http.StatusBadRequest, serviceError(http.StatusBadRequest,
				matched.OperationID+" does not declare query parameter "+name), true
		}
		if len(query[name]) != 1 {
			return http.StatusBadRequest, serviceError(http.StatusBadRequest,
				"query parameter "+name+" was repeated"), true
		}
	}
	for _, name := range matched.QueryRequired {
		if query.Get(name) == "" {
			return http.StatusBadRequest, serviceError(http.StatusBadRequest,
				matched.OperationID+" requires query parameter "+name), true
		}
	}
	if version := query.Get("apiVersion"); version != "" && s.opts.APIVersion != "" && version != s.opts.APIVersion {
		return http.StatusBadRequest, serviceError(http.StatusBadRequest, "unsupported apiVersion "+version), true
	}
	return 0, nil, false
}

func checkHeaders(matched route, r *http.Request, body []byte) (int, []byte, bool) {
	if !oneHeader(r.Header, "Accept", "application/json") {
		return http.StatusBadRequest, serviceError(http.StatusBadRequest, "Accept must be exactly application/json"), true
	}
	if matched.Unauthchecked && len(r.Header.Values("Authorization")) != 0 {
		return http.StatusBadRequest, serviceError(http.StatusBadRequest,
			matched.OperationID+" is unauthenticated and must not carry an Authorization header"), true
	}
	switch r.Method {
	case http.MethodGet:
		if len(body) != 0 || r.ContentLength > 0 {
			return http.StatusBadRequest, serviceError(http.StatusBadRequest, "GET must not carry a body"), true
		}
	default:
		if !oneHeader(r.Header, "Content-Type", "application/json") {
			return http.StatusBadRequest, serviceError(http.StatusBadRequest, "Content-Type must be exactly application/json"), true
		}
		if r.ContentLength != int64(len(body)) || len(r.TransferEncoding) != 0 {
			return http.StatusBadRequest, serviceError(http.StatusBadRequest, "body must be sent with an exact Content-Length"), true
		}
	}
	return 0, nil, false
}

func (s *Server) handleLogin(body []byte) (int, []byte) {
	var object map[string]json.RawMessage
	if err := json.Unmarshal(body, &object); err != nil || object == nil {
		return http.StatusBadRequest, serviceError(http.StatusBadRequest, "body must be a JSON object")
	}
	if len(object) != 1 {
		return http.StatusBadRequest, serviceError(http.StatusBadRequest,
			"CspLoginSpecification declares only refreshToken")
	}
	var refreshToken string
	if err := json.Unmarshal(object["refreshToken"], &refreshToken); err != nil || refreshToken == "" {
		return http.StatusBadRequest, serviceError(http.StatusBadRequest, "refreshToken is required")
	}
	if refreshToken != s.opts.RefreshToken {
		return http.StatusForbidden, serviceError(http.StatusForbidden, "refresh token rejected")
	}
	s.mu.Lock()
	s.issued++
	s.liveToken = fmt.Sprintf("tok-%d", s.issued)
	token := s.liveToken
	s.mu.Unlock()
	payload, _ := json.Marshal(map[string]string{"tokenType": "Bearer", "token": token})
	return http.StatusOK, payload
}

// authorize accepts only the live token and, when the revocation budget is
// exhausted by this request, retires that token so every later holder of it
// receives 401 and must re-authenticate.
func (s *Server) authorize(token string) (int, []byte, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.liveToken == "" || token != s.liveToken {
		return http.StatusUnauthorized, serviceError(http.StatusUnauthorized, "bearer token is expired or revoked"), true
	}
	s.authServed++
	if s.opts.RevokeAfterAuthorized > 0 && s.authServed == s.opts.RevokeAfterAuthorized {
		s.liveToken = ""
	}
	return 0, nil, false
}

func (s *Server) handlePatch(id string, body []byte) (int, []byte) {
	if id != s.opts.CloudAccountID {
		return http.StatusNotFound, serviceError(http.StatusNotFound, "cloud account "+id+" not found")
	}
	var object map[string]json.RawMessage
	if err := json.Unmarshal(body, &object); err != nil || object == nil {
		return http.StatusBadRequest, serviceError(http.StatusBadRequest, "body must be a JSON object")
	}
	for _, required := range []string{"name", "cloudAccountProperties", "regions"} {
		if _, ok := object[required]; !ok {
			return http.StatusBadRequest, serviceError(http.StatusBadRequest,
				"UpdateCloudAccountSpecification requires "+required)
		}
	}
	allowed := map[string]bool{
		"name": true, "description": true, "privateKeyId": true, "privateKey": true,
		"associatedCloudAccountIds": true, "associatedMobilityCloudAccountIds": true,
		"cloudAccountProperties": true, "customProperties": true, "regions": true,
		"createDefaultZones": true, "tags": true, "certificateInfo": true,
	}
	for name := range object {
		if !allowed[name] {
			return http.StatusBadRequest, serviceError(http.StatusBadRequest,
				"UpdateCloudAccountSpecification does not declare "+name)
		}
	}
	s.mu.Lock()
	s.effects++
	s.lastPatch = string(body)
	s.mu.Unlock()
	return http.StatusAccepted, s.trackerPayload(TrackerState{Status: "INPROGRESS", Progress: 0, Message: "Accepted"})
}

func (s *Server) handleTracker(id string) (int, []byte) {
	if id != s.opts.TrackerID {
		return http.StatusNotFound, serviceError(http.StatusNotFound, "request "+id+" not found")
	}
	s.mu.Lock()
	index := s.trackerPolls
	s.trackerPolls++
	s.mu.Unlock()
	if index >= len(s.opts.TrackerScript) {
		index = len(s.opts.TrackerScript) - 1
	}
	return http.StatusOK, s.trackerPayload(s.opts.TrackerScript[index])
}

func (s *Server) trackerPayload(state TrackerState) []byte {
	payload := map[string]any{
		"progress": state.Progress,
		"status":   state.Status,
		"id":       s.opts.TrackerID,
		"selfLink": "/iaas/api/request-tracker/" + s.opts.TrackerID,
		"name":     "Update Cloud Account",
	}
	if state.Message != "" {
		payload["message"] = state.Message
	}
	raw, _ := json.Marshal(payload)
	return raw
}

func (s *Server) handleGetAccount(id string) (int, []byte) {
	if id != s.opts.CloudAccountID {
		return http.StatusNotFound, serviceError(http.StatusNotFound, "cloud account "+id+" not found")
	}
	name := s.opts.CloudAccountName
	if name == "" {
		name = "loopback-cloud-account"
	}
	raw, _ := json.Marshal(map[string]any{
		"id":                     s.opts.CloudAccountID,
		"name":                   name,
		"cloudAccountType":       "vsphere",
		"cloudAccountProperties": map[string]string{"hostName": "vc.loopback.test"},
		"healthy":                true,
		"_links": map[string]any{
			"self": map[string]string{"href": "/iaas/api/cloud-accounts/" + s.opts.CloudAccountID},
		},
	})
	return http.StatusOK, raw
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

func bearer(header http.Header) (string, bool) {
	values := header.Values("Authorization")
	if len(values) != 1 {
		return "", false
	}
	const prefix = "Bearer "
	if !strings.HasPrefix(values[0], prefix) {
		return "", false
	}
	token := strings.TrimPrefix(values[0], prefix)
	if token == "" || strings.TrimSpace(token) != token {
		return "", false
	}
	return token, true
}

func oneHeader(header http.Header, name, value string) bool {
	values := header.Values(name)
	return len(values) == 1 && values[0] == value
}

func serviceError(status int, message string) []byte {
	raw, _ := json.Marshal(map[string]any{
		"message":    message,
		"statusCode": status,
		"messageId":  "com.vmware.vcf.automation.loopback",
	})
	return raw
}

func writeJSON(w http.ResponseWriter, status int, body []byte) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Length", fmt.Sprint(len(body)))
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

// inProcessTransport keeps the loopback URL and runs the identical contract
// handler only when the execution sandbox forbids creating an IPv4 socket.
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
	if serverRequest.Body == nil {
		serverRequest.Body = http.NoBody
	}
	recorder := httptest.NewRecorder()
	transport.handler.ServeHTTP(recorder, serverRequest)
	response := recorder.Result()
	response.Request = request
	return response, nil
}
