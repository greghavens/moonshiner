// Package contractmock provides the contract-pinned loopback vCenter fixture.
//
// The fixture serves only the three operations named by docs/contract.json and
// refuses to start when that projection drifts from the pinned vSphere
// Automation API specification revision. Every request is recorded in a
// synchronized in-process log that records both arrival order and completion
// order, so a verifier can prove that a session was retired only after the
// requests still using it had finished.
package contractmock

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path"
	"strings"
	"sync"
	"testing"
)

const (
	pinnedCommit     = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
	pinnedPath       = "specifications/vsphere/openapi/automation/vcenter.yaml"
	pinnedAPIVersion = "9.0.0.0"
	pinnedBasePath   = "/api"

	// SessionHeader is the api_key_auth header named by the specification.
	SessionHeader = "vmware-api-session-id"
)

// Mode selects a protected rotation scenario.
type Mode int

const (
	// RotateWhileBusy accepts both credentials and both session lifecycles.
	RotateWhileBusy Mode = iota
	// RejectNextCredential fails Cis.Session_create for the replacement secret.
	RejectNextCredential
	// DeleteUnavailable fails Cis.Session_delete with HTTP 503.
	DeleteUnavailable
	// LegacySessionEnvelope returns the pre-9.0 {"value": ...} token wrapper.
	LegacySessionEnvelope
)

type contractDocument struct {
	Source struct {
		Repository          string `json:"repository"`
		RepositoryCommitSHA string `json:"repositoryCommitSha"`
		SpecPath            string `json:"specPath"`
		APIVersion          string `json:"apiVersion"`
		ServerBasePath      string `json:"serverBasePath"`
	} `json:"source"`
	SecuritySchemes struct {
		APIKeyAuth struct {
			Name string `json:"name"`
			In   string `json:"in"`
		} `json:"api_key_auth"`
	} `json:"securitySchemes"`
	Operations           []operation `json:"operations"`
	FocusedFilterProfile struct {
		DeclaredQueryOrder []string `json:"declaredQueryOrder"`
		UnsetBehavior      string   `json:"unsetBehavior"`
	} `json:"focusedFilterProfile"`
}

type operation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

// Credential is one vCenter secret accepted by Cis.Session_create.
type Credential struct {
	Username string
	Password string
}

// VM is a mock response record shaped by the Vcenter.VM.Summary projection.
type VM struct {
	VM            string `json:"vm"`
	Name          string `json:"name"`
	PowerState    string `json:"power_state"`
	CPUCount      *int64 `json:"cpu_count,omitempty"`
	MemorySizeMiB *int64 `json:"memory_size_mib,omitempty"`
}

// Request is a lossless-enough server-side record for wire verification.
//
// Sequence is the one-based arrival order and CompletionOrder is the one-based
// order in which responses were produced. They differ whenever a request is
// held open while later requests are served.
type Request struct {
	Sequence         int
	CompletionOrder  int
	OperationID      string
	Method           string
	RawTarget        string
	Header           http.Header
	Body             []byte
	ContentLength    int64
	TransferEncoding []string
	SessionToken     string
	ResponseStatus   int
	Stranded         bool
}

// String makes unexpected request records compact in test failures.
func (r Request) String() string {
	return fmt.Sprintf("#%d/c%d %s %s (%s => %d)", r.Sequence, r.CompletionOrder, r.Method, r.RawTarget, r.OperationID, r.ResponseStatus)
}

// Server is a focused loopback-only vCenter mock.
type Server struct {
	httpServer *httptest.Server
	routes     map[string]operation
	mode       Mode
	first      Credential
	next       Credential
	vms        []VM
	marker     string

	closeOnce   sync.Once
	closed      chan struct{}
	releaseOnce sync.Once
	gateArrived chan struct{}
	gateRelease chan struct{}

	mu        sync.Mutex
	requests  []Request
	completed int
	sessions  map[string]bool
	issued    []string
	gateArmed bool
}

var wantOperations = []operation{
	{OperationID: "Cis.Session_create", Method: http.MethodPost, Path: "/session"},
	{OperationID: "Cis.Session_delete", Method: http.MethodDelete, Path: "/session"},
	{OperationID: "Vcenter.VM_list", Method: http.MethodGet, Path: "/vcenter/vm"},
}

// FilterQueryMembers returns the declared Vcenter.VM_list query order.
func FilterQueryMembers() []string {
	return []string{"vms", "names", "folders", "datacenters", "hosts", "clusters", "resource_pools", "power_states"}
}

// Start loads exactly the contract routes and starts an ephemeral loopback server.
func Start(t testing.TB, contractPath string, mode Mode) *Server {
	t.Helper()
	data, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read focused contract: %v", err)
	}
	var document contractDocument
	if err := json.Unmarshal(data, &document); err != nil {
		t.Fatalf("decode focused contract: %v", err)
	}
	if document.Source.Repository != "vmware/vcf-api-specs" ||
		document.Source.RepositoryCommitSHA != pinnedCommit ||
		document.Source.SpecPath != pinnedPath ||
		document.Source.APIVersion != pinnedAPIVersion ||
		document.Source.ServerBasePath != pinnedBasePath {
		t.Fatalf("focused contract is not pinned to vcenter.yaml at %s: %+v", pinnedCommit, document.Source)
	}
	if document.SecuritySchemes.APIKeyAuth.Name != SessionHeader || document.SecuritySchemes.APIKeyAuth.In != "header" {
		t.Fatalf("focused contract lost the api_key_auth header: %+v", document.SecuritySchemes.APIKeyAuth)
	}
	if document.FocusedFilterProfile.UnsetBehavior != "omit" ||
		strings.Join(document.FocusedFilterProfile.DeclaredQueryOrder, ",") != strings.Join(FilterQueryMembers(), ",") {
		t.Fatalf("focused filter profile changed: %+v", document.FocusedFilterProfile)
	}
	if len(document.Operations) != len(wantOperations) {
		t.Fatalf("focused contract has %d operations, want %d", len(document.Operations), len(wantOperations))
	}
	routes := make(map[string]operation, len(wantOperations))
	for index, expected := range wantOperations {
		if document.Operations[index] != expected {
			t.Fatalf("focused operation %d = %+v, want %+v", index, document.Operations[index], expected)
		}
		routes[expected.OperationID] = operation{
			OperationID: expected.OperationID,
			Method:      expected.Method,
			Path:        path.Join(pinnedBasePath, expected.Path),
		}
	}
	if mode < RotateWhileBusy || mode > LegacySessionEnvelope {
		t.Fatalf("unsupported mock mode %d", mode)
	}

	digest := sha256.Sum256([]byte(t.Name()))
	marker := hex.EncodeToString(digest[:6])
	cpu := func(value int64) *int64 { return &value }
	s := &Server{
		routes:      routes,
		mode:        mode,
		marker:      marker,
		first:       Credential{Username: "svc-rotation-" + marker + "@vsphere.local", Password: "first-secret-" + marker},
		next:        Credential{Username: "svc-rotation-" + marker + "@vsphere.local", Password: "next-secret-" + marker},
		closed:      make(chan struct{}),
		gateArrived: make(chan struct{}),
		gateRelease: make(chan struct{}),
		sessions:    map[string]bool{},
		vms: []VM{
			{VM: "vm-101", Name: "app-tier-01", PowerState: "POWERED_ON", CPUCount: cpu(4), MemorySizeMiB: cpu(8192)},
			{VM: "vm-102", Name: "app-tier-02", PowerState: "POWERED_OFF", CPUCount: cpu(2), MemorySizeMiB: cpu(4096)},
			{VM: "vm-103", Name: "db-tier-01", PowerState: "POWERED_ON"},
			{VM: "vm-104", Name: "web tier/01", PowerState: "SUSPENDED", CPUCount: cpu(8), MemorySizeMiB: cpu(16384)},
		},
	}

	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("start loopback mock: %v", err)
	}
	s.httpServer = httptest.NewUnstartedServer(http.HandlerFunc(s.serveHTTP))
	s.httpServer.Listener = listener
	s.httpServer.Start()
	parsed, err := url.Parse(s.httpServer.URL)
	if err != nil {
		s.Close()
		t.Fatalf("parse mock URL: %v", err)
	}
	host, _, err := net.SplitHostPort(parsed.Host)
	if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		s.Close()
		t.Fatalf("mock did not bind to loopback: %q", parsed.Host)
	}
	t.Cleanup(s.Close)
	return s
}

// URL returns the loopback service root, without the /api base path.
func (s *Server) URL() string { return s.httpServer.URL }

// FirstCredential returns the secret in use before rotation.
func (s *Server) FirstCredential() Credential { return s.first }

// NextCredential returns the replacement secret.
func (s *Server) NextCredential() Credential { return s.next }

// VMs returns the unfiltered fixture inventory.
func (s *Server) VMs() []VM { return append([]VM(nil), s.vms...) }

// IssuedTokens returns the session tokens minted so far, in mint order.
func (s *Server) IssuedTokens() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]string(nil), s.issued...)
}

// Requests returns a deep copy of the synchronized request log in arrival order.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	result := make([]Request, len(s.requests))
	for index, request := range s.requests {
		result[index] = request
		result[index].Header = request.Header.Clone()
		result[index].Body = append([]byte(nil), request.Body...)
		result[index].TransferEncoding = append([]string(nil), request.TransferEncoding...)
	}
	return result
}

// GateVMList arms a one-shot hold on the next Vcenter.VM_list request. The
// returned channel is closed once that request has been recorded, and the
// returned function lets the held request produce its response. The hold makes
// a request verifiably in flight while other operations are served.
func (s *Server) GateVMList() (arrived <-chan struct{}, release func()) {
	s.mu.Lock()
	s.gateArmed = true
	s.mu.Unlock()
	return s.gateArrived, func() { s.releaseOnce.Do(func() { close(s.gateRelease) }) }
}

// Close stops the loopback server and frees any held request.
func (s *Server) Close() {
	s.closeOnce.Do(func() {
		close(s.closed)
		s.httpServer.Close()
	})
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	operationID := s.match(r.Method, r.URL.EscapedPath())
	token := ""
	if values := r.Header.Values(SessionHeader); len(values) == 1 {
		token = values[0]
	}

	s.mu.Lock()
	index := len(s.requests)
	s.requests = append(s.requests, Request{
		Sequence:         index + 1,
		OperationID:      operationID,
		Method:           r.Method,
		RawTarget:        r.RequestURI,
		Header:           r.Header.Clone(),
		Body:             append([]byte(nil), body...),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
		SessionToken:     token,
	})
	liveAtArrival := token != "" && s.sessions[token]
	held := false
	if operationID == "Vcenter.VM_list" && s.gateArmed {
		s.gateArmed = false
		held = true
	}
	s.mu.Unlock()

	if held {
		close(s.gateArrived)
		select {
		case <-s.gateRelease:
		case <-s.closed:
		}
	}

	s.mu.Lock()
	liveNow := token != "" && s.sessions[token]
	status, payload := s.dispatchLocked(operationID, r, body, token)
	s.completed++
	s.requests[index].ResponseStatus = status
	s.requests[index].CompletionOrder = s.completed
	s.requests[index].Stranded = operationID == "Vcenter.VM_list" && liveAtArrival && !liveNow
	s.mu.Unlock()

	if status == http.StatusNoContent {
		w.WriteHeader(status)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func (s *Server) match(method, escapedPath string) string {
	for _, expected := range wantOperations {
		route := s.routes[expected.OperationID]
		if method == route.Method && escapedPath == route.Path {
			return route.OperationID
		}
	}
	return ""
}

func (s *Server) dispatchLocked(operationID string, r *http.Request, body []byte, token string) (int, any) {
	switch operationID {
	case "Cis.Session_create":
		return s.createSessionLocked(r, body)
	case "Cis.Session_delete":
		return s.deleteSessionLocked(r, token)
	case "Vcenter.VM_list":
		return s.listVMsLocked(r, token)
	default:
		return http.StatusNotFound, errorBody("NOT_FOUND", "operation is outside the focused contract")
	}
}

func (s *Server) createSessionLocked(r *http.Request, body []byte) (int, any) {
	if len(body) != 0 || r.URL.RawQuery != "" || len(r.Header.Values(SessionHeader)) != 0 {
		return http.StatusBadRequest, errorBody("INVALID_ARGUMENT", "Cis.Session_create takes no body, query, or session header")
	}
	username, password, ok := r.BasicAuth()
	if !ok {
		return http.StatusUnauthorized, errorBody("UNAUTHENTICATED", "basic_auth credentials are required")
	}
	presented := Credential{Username: username, Password: password}
	switch {
	case presented == s.first:
	case presented == s.next:
		if s.mode == RejectNextCredential {
			return http.StatusUnauthorized, errorBody("UNAUTHENTICATED", "replacement secret is not active yet")
		}
	default:
		return http.StatusUnauthorized, errorBody("UNAUTHENTICATED", "unknown credentials")
	}

	token := fmt.Sprintf("session-%s-%d", s.marker, len(s.issued)+1)
	s.issued = append(s.issued, token)
	s.sessions[token] = true
	if s.mode == LegacySessionEnvelope {
		return http.StatusCreated, map[string]any{"value": token}
	}
	return http.StatusCreated, token
}

func (s *Server) deleteSessionLocked(r *http.Request, token string) (int, any) {
	if r.URL.RawQuery != "" || len(r.Header.Values("Authorization")) != 0 {
		return http.StatusBadRequest, errorBody("INVALID_ARGUMENT", "Cis.Session_delete takes no query and no basic_auth")
	}
	if token == "" || !s.sessions[token] {
		return http.StatusUnauthorized, errorBody("UNAUTHENTICATED", "session token is missing or already invalid")
	}
	if s.mode == DeleteUnavailable {
		return http.StatusServiceUnavailable, errorBody("SERVICE_UNAVAILABLE", "session store is unreachable")
	}
	s.sessions[token] = false
	return http.StatusNoContent, nil
}

func (s *Server) listVMsLocked(r *http.Request, token string) (int, any) {
	if len(r.Header.Values("Authorization")) != 0 {
		return http.StatusBadRequest, errorBody("INVALID_ARGUMENT", "Vcenter.VM_list uses api_key_auth, not basic_auth")
	}
	if token == "" || !s.sessions[token] {
		return http.StatusUnauthorized, errorBody("UNAUTHENTICATED", "session token is missing or no longer valid")
	}
	query := r.URL.Query()
	matches := make([]VM, 0, len(s.vms))
	for _, vm := range s.vms {
		if selects(query, "vms", vm.VM) && selects(query, "names", vm.Name) && selects(query, "power_states", vm.PowerState) {
			matches = append(matches, vm)
		}
	}
	return http.StatusOK, matches
}

// selects reports whether an inventory value satisfies one filter member. An
// absent member matches everything, mirroring the specification's "if missing
// or null or empty" filter semantics.
func selects(query url.Values, member, value string) bool {
	values, present := query[member]
	if !present || len(values) == 0 {
		return true
	}
	for _, candidate := range values {
		if candidate == value {
			return true
		}
	}
	return false
}

func errorBody(code, message string) map[string]any {
	return map[string]any{
		"error_type": code,
		"messages": []any{map[string]any{
			"id":              "com.vmware.vapi.rest." + strings.ToLower(code),
			"default_message": message,
			"args":            []any{},
		}},
	}
}
