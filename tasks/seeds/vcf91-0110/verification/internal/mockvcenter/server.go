// Package mockvcenter provides a loopback fixture pinned to docs/contract.json.
package mockvcenter

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"slices"
	"strings"
	"sync"
)

const (
	SessionCreateOperationID  = "Cis.Session_create"
	DatacenterListOperationID = "Vcenter.Datacenter_list"
	VMListOperationID         = "Vcenter.VM_list"
)

var (
	datacenterParameters = map[string]bool{
		"datacenters": true,
		"names":       true,
		"folders":     true,
	}
	vmParameters = map[string]bool{
		"vms":            true,
		"names":          true,
		"folders":        true,
		"datacenters":    true,
		"hosts":          true,
		"clusters":       true,
		"resource_pools": true,
		"power_states":   true,
	}
)

// Datacenter is a fixture Vcenter.Datacenter.Summary.
type Datacenter struct {
	Datacenter string `json:"datacenter"`
	Name       string `json:"name"`
}

// VM is a fixture Vcenter.VM.Summary.
type VM struct {
	VM            string `json:"vm"`
	Name          string `json:"name"`
	PowerState    string `json:"power_state"`
	CPUCount      *int64 `json:"cpu_count,omitempty"`
	MemorySizeMiB *int64 `json:"memory_size_mib,omitempty"`
}

// Scenario configures a deterministic mock. When ExpireFirstToken is true, the
// first token expires after ExpireAfter successful list requests.
type Scenario struct {
	Username          string
	Password          string
	Tokens            []string
	Datacenters       []Datacenter
	VMs               []VM
	ExpireFirstToken  bool
	ExpireAfter       int
	RejectReplacement bool
	ErrorSecret       string
}

// Request is an immutable snapshot of one incoming HTTP request.
type Request struct {
	Method     string
	RequestURI string
	Path       string
	RawQuery   string
	Header     http.Header
	Body       []byte
}

// Server is a loopback-only vCenter mock with a race-safe request log.
type Server struct {
	httpServer *httptest.Server
	httpClient *http.Client
	origin     string
	scenario   Scenario

	mu                  sync.Mutex
	requests            []Request
	issuedTokens        int
	firstTokenSuccesses int
	datacenterCalls     int
	vmCalls             int
}

// New starts an IPv4 loopback server.
func New(scenario Scenario) (*Server, error) {
	if strings.TrimSpace(scenario.Username) == "" ||
		strings.TrimSpace(scenario.Password) == "" {
		return nil, errors.New("mockvcenter: credentials are required")
	}
	if len(scenario.Tokens) < 2 ||
		strings.TrimSpace(scenario.Tokens[0]) == "" ||
		strings.TrimSpace(scenario.Tokens[1]) == "" ||
		scenario.Tokens[0] == scenario.Tokens[1] {
		return nil, errors.New("mockvcenter: two distinct tokens are required")
	}
	if scenario.ExpireAfter < 0 {
		return nil, errors.New("mockvcenter: ExpireAfter must be non-negative")
	}
	for _, datacenter := range scenario.Datacenters {
		if datacenter.Datacenter == "" || datacenter.Name == "" {
			return nil, errors.New("mockvcenter: invalid datacenter")
		}
	}
	for _, vm := range scenario.VMs {
		if vm.VM == "" || vm.Name == "" || !validPowerState(vm.PowerState) {
			return nil, errors.New("mockvcenter: invalid VM")
		}
	}

	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		server := &Server{
			origin:   "http://127.0.0.1",
			scenario: scenario,
		}
		server.httpClient = &http.Client{
			Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
				recorder := httptest.NewRecorder()
				server.serveHTTP(recorder, request)
				return recorder.Result(), nil
			}),
		}
		return server, nil
	}
	server := &Server{scenario: scenario}
	server.httpServer = httptest.NewUnstartedServer(http.HandlerFunc(server.serveHTTP))
	server.httpServer.Listener = listener
	server.httpServer.Start()
	server.origin = server.httpServer.URL
	server.httpClient = server.httpServer.Client()
	return server, nil
}

// URL returns the loopback origin without the contract's /api base path.
func (s *Server) URL() string {
	return s.origin
}

// Client returns an HTTP client connected to the mock.
func (s *Server) Client() *http.Client {
	return s.httpClient
}

// Close stops the loopback server.
func (s *Server) Close() {
	if s.httpServer != nil {
		s.httpServer.Close()
	}
}

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	result := make([]Request, len(s.requests))
	for index, request := range s.requests {
		result[index] = request
		result[index].Header = request.Header.Clone()
		result[index].Body = slices.Clone(request.Body)
	}
	return result
}

// OperationIDs returns the only operations served by the fixture.
func OperationIDs() []string {
	return []string{
		SessionCreateOperationID,
		DatacenterListOperationID,
		VMListOperationID,
	}
}

func (s *Server) serveHTTP(response http.ResponseWriter, request *http.Request) {
	var (
		body    []byte
		readErr error
	)
	if request.Body != nil {
		body, readErr = io.ReadAll(io.LimitReader(request.Body, 1<<20))
	}
	s.record(request, body)
	if readErr != nil {
		writeError(response, http.StatusBadRequest, "request body read failed")
		return
	}

	switch {
	case request.Method == http.MethodPost &&
		request.URL.Path == "/api/session" &&
		request.URL.RawQuery == "":
		s.createSession(response, request, body)
	case request.Method == http.MethodGet &&
		request.URL.Path == "/api/vcenter/datacenter":
		s.listDatacenters(response, request, body)
	case request.Method == http.MethodGet &&
		request.URL.Path == "/api/vcenter/vm":
		s.listVMs(response, request, body)
	default:
		writeError(response, http.StatusNotFound, "outside focused contract")
	}
}

func (s *Server) record(request *http.Request, body []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, Request{
		Method:     request.Method,
		RequestURI: request.URL.RequestURI(),
		Path:       request.URL.Path,
		RawQuery:   request.URL.RawQuery,
		Header:     request.Header.Clone(),
		Body:       slices.Clone(body),
	})
}

func (s *Server) createSession(
	response http.ResponseWriter,
	request *http.Request,
	body []byte,
) {
	expected := "Basic " + base64.StdEncoding.EncodeToString(
		[]byte(s.scenario.Username+":"+s.scenario.Password),
	)
	if request.Header.Get("Authorization") != expected ||
		request.Header.Get("vmware-api-session-id") != "" ||
		len(body) != 0 {
		writeError(response, http.StatusUnauthorized, s.errorMessage())
		return
	}

	s.mu.Lock()
	index := s.issuedTokens
	if index < len(s.scenario.Tokens) {
		s.issuedTokens++
	}
	s.mu.Unlock()
	if index >= len(s.scenario.Tokens) {
		writeError(response, http.StatusServiceUnavailable, s.errorMessage())
		return
	}
	writeJSON(response, http.StatusCreated, s.scenario.Tokens[index])
}

func (s *Server) listDatacenters(
	response http.ResponseWriter,
	request *http.Request,
	body []byte,
) {
	if !validListRequest(request, body, datacenterParameters) {
		writeError(response, http.StatusBadRequest, "invalid datacenter request")
		return
	}
	if !s.authorizeList(request.Header.Get("vmware-api-session-id")) {
		writeError(response, http.StatusUnauthorized, s.errorMessage())
		return
	}

	s.mu.Lock()
	s.datacenterCalls++
	reverse := s.datacenterCalls%2 == 1
	values := slices.Clone(s.scenario.Datacenters)
	s.mu.Unlock()
	if reverse {
		slices.Reverse(values)
	}
	writeJSON(response, http.StatusOK, values)
}

func (s *Server) listVMs(
	response http.ResponseWriter,
	request *http.Request,
	body []byte,
) {
	if !validListRequest(request, body, vmParameters) {
		writeError(response, http.StatusBadRequest, "invalid VM request")
		return
	}
	if !s.authorizeList(request.Header.Get("vmware-api-session-id")) {
		writeError(response, http.StatusUnauthorized, s.errorMessage())
		return
	}

	s.mu.Lock()
	s.vmCalls++
	reverse := s.vmCalls%2 == 1
	values := slices.Clone(s.scenario.VMs)
	s.mu.Unlock()
	if reverse {
		slices.Reverse(values)
	}
	writeJSON(response, http.StatusOK, values)
}

func (s *Server) authorizeList(token string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()

	if token == s.scenario.Tokens[0] {
		if s.scenario.ExpireFirstToken &&
			s.firstTokenSuccesses >= s.scenario.ExpireAfter {
			return false
		}
		s.firstTokenSuccesses++
		return true
	}
	if token == s.scenario.Tokens[1] && s.issuedTokens >= 2 {
		return !s.scenario.RejectReplacement
	}
	return false
}

func (s *Server) errorMessage() string {
	if s.scenario.ErrorSecret != "" {
		return s.scenario.ErrorSecret
	}
	return "mock authentication failure"
}

func validListRequest(
	request *http.Request,
	body []byte,
	allowed map[string]bool,
) bool {
	if len(body) != 0 ||
		request.Header.Get("Authorization") != "" ||
		request.Header.Get("Content-Type") != "" ||
		request.Header.Get("vmware-api-session-id") == "" {
		return false
	}
	query, err := url.ParseQuery(request.URL.RawQuery)
	if err != nil {
		return false
	}
	for name, values := range query {
		if !allowed[name] || len(values) == 0 {
			return false
		}
		for _, value := range values {
			if value == "" {
				return false
			}
			if name == "power_states" && !validPowerState(value) {
				return false
			}
		}
	}
	return true
}

func validPowerState(value string) bool {
	switch value {
	case "POWERED_OFF", "POWERED_ON", "SUSPENDED":
		return true
	default:
		return false
	}
}

func writeJSON(response http.ResponseWriter, status int, value any) {
	body, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	response.Header().Set("Content-Type", "application/json")
	response.Header().Set("Content-Length", fmt.Sprint(len(body)))
	response.WriteHeader(status)
	_, _ = response.Write(body)
}

func writeError(response http.ResponseWriter, status int, message string) {
	writeJSON(response, status, map[string]any{
		"error_type": strings.ToUpper(strings.ReplaceAll(http.StatusText(status), " ", "_")),
		"messages": []map[string]any{{
			"id":              "mock.error",
			"default_message": message,
			"args":            []string{},
		}},
	})
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}
