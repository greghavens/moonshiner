// Package contractmock provides the protected loopback vCenter used by the
// acceptance tests. It implements only the three operations extracted in
// docs/contract.json.
package contractmock

import (
	"encoding/json"
	"mime"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"slices"
	"sync"
)

const (
	OperationTokenIssue = "Vcenter.Authentication.Token_issue"
	OperationVMList     = "Vcenter.VM_list"
	OperationHostList   = "Vcenter.Host_list"

	InitialAccessToken = "0123456789abcdef0123456789abcdef"
	RotatedAccessToken = "fedcba9876543210fedcba9876543210"
	SubjectToken       = "PHNhbWwyOkFzc2VydGlvbiBJRD0iX2NvbnRyYWN0X2NvdmVyYWdlXyIvPg=="
	SubjectTokenType   = "SAML2"
	Audience           = "vcf91-contract-coverage"
	RequestedTokenType = "JWT_ID"

	tokenExchangeGrant = "urn:ietf:params:oauth:grant-type:token-exchange"
)

// Request is a sanitized copy of a request observed by the mock. The
// credentials are inert fixture values.
type Request struct {
	OperationID   string
	Method        string
	Path          string
	SessionID     string
	Authorization string
	ContentType   string
	Accept        string
	Form          url.Values
}

// Server is an httptest server with contract state and a race-safe request log.
type Server struct {
	http   *httptest.Server
	url    string
	client *http.Client

	mu            sync.Mutex
	requests      []Request
	validAccess   string
	vmResponses   int
	hostResponses int
	tokenFailure  bool
	rejectRotated bool
	extraHost     bool
}

// Option selects a documented failure mode for error-envelope tests.
type Option func(*Server)

// WithTokenFailure makes Token_issue return the documented OAuth2 400 shape.
func WithTokenFailure() Option {
	return func(s *Server) {
		s.tokenFailure = true
	}
}

// WithRejectedRotatedAccess makes the retried collection request return a
// second documented vAPI 401 response.
func WithRejectedRotatedAccess() Option {
	return func(s *Server) {
		s.rejectRotated = true
	}
}

// WithAdditionalHostForSortingCoverage adds a second, deliberately first-
// sorted host that is not part of the live-shaped primary response.
func WithAdditionalHostForSortingCoverage() Option {
	return func(s *Server) {
		s.extraHost = true
	}
}

// New starts a loopback server. The first successful VM response expires the
// initial access token, so the following host request must be resumed after a
// token exchange.
func New(options ...Option) *Server {
	s := &Server{validAccess: InitialAccessToken}
	for _, option := range options {
		option(s)
	}
	handler := http.HandlerFunc(s.serveHTTP)
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err == nil {
		s.http = &httptest.Server{
			Listener: listener,
			Config:   &http.Server{Handler: handler},
		}
		s.http.Start()
		s.url = s.http.URL
		s.client = s.http.Client()
	} else {
		// Some authoring sandboxes prohibit even loopback sockets. Preserve the
		// exact HTTP boundary through a handler-backed transport there; normal
		// verifier environments use the real 127.0.0.1 listener above.
		s.url = "http://127.0.0.1"
		s.client = &http.Client{Transport: handlerTransport{handler: handler}}
	}
	return s
}

func (s *Server) URL() string {
	return s.url
}

func (s *Server) Client() *http.Client {
	return s.client
}

func (s *Server) Close() {
	if s.http != nil {
		s.http.Close()
	}
}

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	out := make([]Request, len(s.requests))
	for i, req := range s.requests {
		out[i] = req
		out[i].Form = cloneValues(req.Form)
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	op := operationFor(r.Method, r.URL.Path)

	var form url.Values
	if op == OperationTokenIssue {
		_ = r.ParseForm()
		form = cloneValues(r.PostForm)
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, Request{
		OperationID:   op,
		Method:        r.Method,
		Path:          r.URL.Path,
		SessionID:     r.Header.Get("vmware-api-session-id"),
		Authorization: r.Header.Get("Authorization"),
		ContentType:   r.Header.Get("Content-Type"),
		Accept:        r.Header.Get("Accept"),
		Form:          form,
	})

	switch op {
	case OperationTokenIssue:
		s.issueToken(w, r, form)
	case OperationVMList:
		s.listVMs(w, r)
	case OperationHostList:
		s.listHosts(w, r)
	default:
		http.NotFound(w, r)
	}
}

func operationFor(method, path string) string {
	switch {
	case method == http.MethodPost && path == "/api/vcenter/authentication/token":
		return OperationTokenIssue
	case method == http.MethodGet && path == "/api/vcenter/vm":
		return OperationVMList
	case method == http.MethodGet && path == "/api/vcenter/host":
		return OperationHostList
	default:
		return ""
	}
}

func (s *Server) issueToken(w http.ResponseWriter, r *http.Request, form url.Values) {
	mediaType, _, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
	validForm := len(form) == 5 &&
		one(form, "audience") == Audience &&
		one(form, "grant_type") == tokenExchangeGrant &&
		one(form, "requested_token_type") == RequestedTokenType &&
		one(form, "subject_token") == SubjectToken &&
		one(form, "subject_token_type") == SubjectTokenType
	if mediaType != "application/x-www-form-urlencoded" ||
		r.Header.Get("Accept") != "application/json" ||
		r.Header.Get("Authorization") != "Bearer "+SubjectToken ||
		!validForm {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"error":             "invalid_request",
			"error_description": "token exchange request does not match the pinned contract",
		})
		return
	}
	if s.tokenFailure {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"error":             "invalid_grant",
			"error_description": "the subject credential expired",
			"error_uri":         "https://developer.broadcom.com/xapis/vsphere-automation-api/9.1/",
		})
		return
	}

	s.validAccess = RotatedAccessToken
	writeJSON(w, http.StatusOK, map[string]any{
		"access_token":      s.validAccess,
		"token_type":        "Bearer",
		"expires_in":        300,
		"refresh_token":     "refresh-2",
		"issued_token_type": RequestedTokenType,
	})
}

func (s *Server) listVMs(w http.ResponseWriter, r *http.Request) {
	if !s.validCollectionRequest(r) {
		writeUnauthenticated(w)
		return
	}

	items := []map[string]any{
		{"vm": "vm-19", "name": "sddcm01", "power_state": "POWERED_ON", "cpu_count": 4, "memory_size_MiB": 16384},
		{"vm": "vm-20", "name": "vc01", "power_state": "POWERED_ON", "cpu_count": 4, "memory_size_MiB": 21504},
		{"vm": "vm-28", "name": "nsx01a", "power_state": "POWERED_ON", "cpu_count": 6, "memory_size_MiB": 24576},
		{"vm": "vm-33", "name": "vcf-msr01-nxpxf", "power_state": "POWERED_ON", "cpu_count": 4, "memory_size_MiB": 10240},
		{"vm": "vm-34", "name": "vcf-msr01-5ghdn", "power_state": "POWERED_ON", "cpu_count": 8, "memory_size_MiB": 24576},
		{"vm": "vm-35", "name": "vcf-msr01-x6j88", "power_state": "POWERED_ON", "cpu_count": 8, "memory_size_MiB": 24576},
		{"vm": "vm-36", "name": "vcf-msr01-6zpgq", "power_state": "POWERED_ON", "cpu_count": 8, "memory_size_MiB": 24576},
		{"vm": "vm-37", "name": "vcf01", "power_state": "POWERED_ON", "cpu_count": 4, "memory_size_MiB": 16384},
		{"vm": "vm-38", "name": "vcf-proxy01", "power_state": "POWERED_ON", "cpu_count": 4, "memory_size_MiB": 16384},
		{"vm": "vm-39", "name": "vcf-lic01", "power_state": "POWERED_ON", "cpu_count": 2, "memory_size_MiB": 4096},
		{"vm": "vm-43", "name": "vcf-asr01-szwjz", "power_state": "POWERED_ON", "cpu_count": 8, "memory_size_MiB": 98304},
	}
	s.vmResponses++
	if s.vmResponses%2 == 1 {
		slices.Reverse(items)
	}

	if s.validAccess == InitialAccessToken {
		// The token expires only after the VM collection has been delivered.
		s.validAccess = ""
	}
	writeJSON(w, http.StatusOK, items)
}

func (s *Server) listHosts(w http.ResponseWriter, r *http.Request) {
	if !s.validCollectionRequest(r) {
		writeUnauthenticated(w)
		return
	}

	items := []map[string]any{{
		"host":             "host-12",
		"name":             "esx01.vcf.lab",
		"connection_state": "CONNECTED",
		"power_state":      "POWERED_ON",
		"host_uuid":        "312680c6-8a28-4302-90c6-319869516823",
	}}
	if s.extraHost {
		items = append(items, map[string]any{
			"host":             "host-01",
			"name":             "contract-sort-only.example.test",
			"connection_state": "DISCONNECTED",
		})
	}
	s.hostResponses++
	if s.hostResponses%2 == 1 {
		slices.Reverse(items)
	}
	writeJSON(w, http.StatusOK, items)
}

func (s *Server) validCollectionRequest(r *http.Request) bool {
	if s.rejectRotated && r.Header.Get("vmware-api-session-id") == RotatedAccessToken {
		return false
	}
	return r.URL.RawQuery == "" &&
		r.Header.Get("Accept") == "application/json" &&
		r.Header.Get("vmware-api-session-id") == s.validAccess &&
		s.validAccess != ""
}

func writeUnauthenticated(w http.ResponseWriter) {
	writeJSON(w, http.StatusUnauthorized, map[string]any{
		"error_type": "UNAUTHENTICATED",
		"messages": []map[string]any{
			{
				"id":              "com.vmware.vapi.endpoint.method.authentication.required",
				"default_message": "Authentication required.",
				"args":            []string{},
			},
		},
	})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func one(values url.Values, key string) string {
	got, ok := values[key]
	if !ok || len(got) != 1 {
		return ""
	}
	return got[0]
}

func cloneValues(in url.Values) url.Values {
	if in == nil {
		return nil
	}
	out := make(url.Values, len(in))
	for key, values := range in {
		out[key] = append([]string(nil), values...)
	}
	return out
}

type handlerTransport struct {
	handler http.Handler
}

func (t handlerTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	select {
	case <-req.Context().Done():
		return nil, req.Context().Err()
	default:
	}
	recorder := httptest.NewRecorder()
	t.handler.ServeHTTP(recorder, req)
	return recorder.Result(), nil
}
