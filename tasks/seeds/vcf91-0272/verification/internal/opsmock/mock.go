// Package opsmock implements an in-memory HTTP double for the VMware Cloud
// Foundation Operations 9.1 API.
//
// The double is pinned to the wire contract recorded in docs/contract.json: it
// serves only the three operations that contract names and answers 404 for
// every other path. Each received request is appended to a log that tests read
// back with Requests, and each adapter instance the server actually creates is
// added to an inventory exposed by Instances, so a test can assert that a
// failed precheck left the server unchanged.
//
// The double uses a net/http RoundTripper without opening a network socket.
// Requests still pass through http.Client and the same HTTP handler used by a
// server, while verification remains usable in network-restricted runners.
package opsmock

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
)

// BasePath is the server base path declared by the specification.
const BasePath = "/suite-api"

// TokenScheme is the authorization scheme carried in the Authorization header.
const TokenScheme = "OpsToken"

// Operation describes one operation the double serves.
type Operation struct {
	// OperationID is the operationId from the specification.
	OperationID string
	// Method is the HTTP method.
	Method string
	// Path is the full request path, including BasePath.
	Path string
	// RequiresAuth reports whether the operation demands an Authorization header.
	RequiresAuth bool
}

// Operations lists every operation the double serves. Requests for anything
// else are recorded with an empty OperationID and answered with 404.
var Operations = []Operation{
	{OperationID: "acquireToken", Method: http.MethodPost, Path: BasePath + "/api/auth/token/acquire", RequiresAuth: false},
	{OperationID: "testConnection", Method: http.MethodPost, Path: BasePath + "/api/adapters/testConnection", RequiresAuth: true},
	{OperationID: "createAdapterInstance", Method: http.MethodPost, Path: BasePath + "/api/adapters", RequiresAuth: true},
}

// RequestRecord is one entry of the request log.
type RequestRecord struct {
	// OperationID is the matched operation, or "" when the path is not served.
	OperationID string
	Method      string
	Path        string
	RawQuery    string
	Query       url.Values
	Header      http.Header
	Body        []byte
	// Status is the status code the double answered with.
	Status int
}

// Instance is one adapter instance the double actually created.
type Instance struct {
	ID             string
	Name           string
	AdapterKindKey string
	// Body is the raw request body that created the instance.
	Body []byte
}

// Config configures the double. The zero value is usable: Start fills in
// defaults for every unset field.
type Config struct {
	// Username and Password are the credentials acquireToken accepts.
	Username string
	Password string
	// AuthSource is the auth source acquireToken expects. When empty the
	// request body must omit authSource entirely.
	AuthSource string
	// Token is the token acquireToken hands out and the value the
	// Authorization header must carry as "OpsToken <token>".
	Token string

	// PrecheckStatus is the status testConnection answers with. Defaults to
	// 201, which means the precheck passed.
	PrecheckStatus int
	// PrecheckMessage is the error message returned when PrecheckStatus is
	// not 201.
	PrecheckMessage string

	// CreateStatus is the status createAdapterInstance answers with.
	// Defaults to 201.
	CreateStatus int
	// CreateMessage is the error message returned when CreateStatus is not 201.
	CreateMessage string

	// InstanceID is the id reported for a created adapter instance.
	InstanceID string
	// ResourceKindKey is reported in the resourceKey of adapter-instance
	// responses.
	ResourceKindKey string
}

func (c *Config) applyDefaults() {
	if c.Username == "" {
		c.Username = "svc-vcfops"
	}
	if c.Password == "" {
		c.Password = "correct-horse-battery-staple"
	}
	if c.Token == "" {
		c.Token = "3f6a1f4e-0c2b-4d38-8e7a-9b1c5d2e4f60::9a2f"
	}
	if c.PrecheckStatus == 0 {
		c.PrecheckStatus = http.StatusCreated
	}
	if c.PrecheckMessage == "" {
		c.PrecheckMessage = "Unable to establish a connection to the endpoint."
	}
	if c.CreateStatus == 0 {
		c.CreateStatus = http.StatusCreated
	}
	if c.CreateMessage == "" {
		c.CreateMessage = "Adapter instance could not be created."
	}
	if c.InstanceID == "" {
		c.InstanceID = "c7e0b4a2-53d1-4a8f-9b16-2ac4f0d9e781"
	}
	if c.ResourceKindKey == "" {
		c.ResourceKindKey = "ADAPTER_INSTANCE"
	}
}

// Server is a running in-memory double.
type Server struct {
	// URL is the base URL of the running server, without a trailing slash.
	// It does not include BasePath.
	URL string

	cfg  Config
	http *http.Client

	mu        sync.Mutex
	requests  []RequestRecord
	instances []Instance
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) {
	return f(r)
}

// Start starts an in-memory double. The caller must Close it.
func Start(cfg Config) *Server {
	cfg.applyDefaults()
	s := &Server{cfg: cfg}
	handler := http.HandlerFunc(s.serve)
	s.http = &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, r)
		return recorder.Result(), nil
	})}
	s.URL = "http://opsmock.local"
	return s
}

// HTTPClient returns the client that routes requests to this double.
func (s *Server) HTTPClient() *http.Client { return s.http }

// Close releases any idle resources held by the double's HTTP client.
func (s *Server) Close() { s.http.CloseIdleConnections() }

// Config returns the effective configuration, with defaults applied.
func (s *Server) Config() Config { return s.cfg }

// Requests returns a copy of the request log in arrival order.
func (s *Server) Requests() []RequestRecord {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]RequestRecord, len(s.requests))
	copy(out, s.requests)
	return out
}

// Instances returns a copy of the adapter instances the double created.
func (s *Server) Instances() []Instance {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Instance, len(s.instances))
	copy(out, s.instances)
	return out
}

// Reset clears the request log and the created-instance inventory.
func (s *Server) Reset() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = nil
	s.instances = nil
}

type errorBody struct {
	Message        string `json:"message"`
	HTTPStatusCode int    `json:"httpStatusCode"`
}

type resourceKey struct {
	Name            string `json:"name"`
	AdapterKindKey  string `json:"adapterKindKey"`
	ResourceKindKey string `json:"resourceKindKey"`
}

type adapterInstance struct {
	ID          string      `json:"id,omitempty"`
	ResourceKey resourceKey `json:"resourceKey"`
	Description string      `json:"description,omitempty"`
}

type authToken struct {
	Token     string   `json:"token"`
	Validity  int64    `json:"validity"`
	ExpiresAt string   `json:"expiresAt"`
	Roles     []string `json:"roles"`
}

func lookup(path string) (Operation, bool) {
	for _, op := range Operations {
		if op.Path == path {
			return op, true
		}
	}
	return Operation{}, false
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	rec := RequestRecord{
		Method:   r.Method,
		Path:     r.URL.Path,
		RawQuery: r.URL.RawQuery,
		Query:    r.URL.Query(),
		Header:   r.Header.Clone(),
		Body:     body,
	}

	op, known := lookup(r.URL.Path)
	if known {
		rec.OperationID = op.OperationID
	}

	status, payload := s.dispatch(op, known, r, body)
	rec.Status = status

	s.mu.Lock()
	s.requests = append(s.requests, rec)
	s.mu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if payload != nil {
		_ = json.NewEncoder(w).Encode(payload)
	}
}

func (s *Server) dispatch(op Operation, known bool, r *http.Request, body []byte) (int, any) {
	if !known {
		return http.StatusNotFound, errorBody{Message: "No handler found for " + r.URL.Path, HTTPStatusCode: http.StatusNotFound}
	}
	if r.Method != op.Method {
		return http.StatusMethodNotAllowed, errorBody{Message: "Method not allowed", HTTPStatusCode: http.StatusMethodNotAllowed}
	}
	if ct := r.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		return http.StatusUnsupportedMediaType, errorBody{Message: "Content-Type must be application/json", HTTPStatusCode: http.StatusUnsupportedMediaType}
	}
	if op.RequiresAuth && r.Header.Get("Authorization") != TokenScheme+" "+s.cfg.Token {
		return http.StatusUnauthorized, errorBody{Message: "Invalid or missing authorization token", HTTPStatusCode: http.StatusUnauthorized}
	}

	switch op.OperationID {
	case "acquireToken":
		return s.acquireToken(body)
	case "testConnection":
		return s.testConnection(body)
	case "createAdapterInstance":
		return s.createAdapterInstance(body)
	}
	return http.StatusNotFound, errorBody{Message: "unreachable", HTTPStatusCode: http.StatusNotFound}
}

func (s *Server) acquireToken(body []byte) (int, any) {
	var req map[string]any
	if err := json.Unmarshal(body, &req); err != nil {
		return http.StatusBadRequest, errorBody{Message: "Malformed request body", HTTPStatusCode: http.StatusBadRequest}
	}
	unauthorized := errorBody{Message: "Invalid credentials", HTTPStatusCode: http.StatusUnauthorized}
	if req["username"] != s.cfg.Username || req["password"] != s.cfg.Password {
		return http.StatusUnauthorized, unauthorized
	}
	got, present := req["authSource"]
	if s.cfg.AuthSource == "" {
		if present {
			return http.StatusUnauthorized, unauthorized
		}
	} else if !present || got != s.cfg.AuthSource {
		return http.StatusUnauthorized, unauthorized
	}
	return http.StatusOK, authToken{
		Token:     s.cfg.Token,
		Validity:  1778929200000,
		ExpiresAt: "Wednesday, May 13, 2026 08:20:00 AM UTC",
		Roles:     []string{"ContentAdmin"},
	}
}

// decodeCreate validates the two members the specification marks required on
// the create-adapter-instance schema.
func decodeCreate(body []byte) (name, adapterKindKey string, err *errorBody) {
	var req map[string]any
	if e := json.Unmarshal(body, &req); e != nil {
		return "", "", &errorBody{Message: "Malformed request body", HTTPStatusCode: http.StatusBadRequest}
	}
	name, _ = req["name"].(string)
	adapterKindKey, _ = req["adapterKindKey"].(string)
	if name == "" || adapterKindKey == "" {
		return "", "", &errorBody{Message: "name and adapterKindKey are required", HTTPStatusCode: http.StatusBadRequest}
	}
	return name, adapterKindKey, nil
}

func (s *Server) testConnection(body []byte) (int, any) {
	name, kind, bad := decodeCreate(body)
	if bad != nil {
		return bad.HTTPStatusCode, *bad
	}
	if s.cfg.PrecheckStatus != http.StatusCreated {
		return s.cfg.PrecheckStatus, errorBody{Message: s.cfg.PrecheckMessage, HTTPStatusCode: s.cfg.PrecheckStatus}
	}
	// A passing precheck creates nothing, so the response carries no id.
	return http.StatusCreated, adapterInstance{
		ResourceKey: resourceKey{Name: name, AdapterKindKey: kind, ResourceKindKey: s.cfg.ResourceKindKey},
	}
}

func (s *Server) createAdapterInstance(body []byte) (int, any) {
	name, kind, bad := decodeCreate(body)
	if bad != nil {
		return bad.HTTPStatusCode, *bad
	}
	if s.cfg.CreateStatus != http.StatusCreated {
		return s.cfg.CreateStatus, errorBody{Message: s.cfg.CreateMessage, HTTPStatusCode: s.cfg.CreateStatus}
	}

	stored := make([]byte, len(body))
	copy(stored, body)

	s.mu.Lock()
	s.instances = append(s.instances, Instance{
		ID:             s.cfg.InstanceID,
		Name:           name,
		AdapterKindKey: kind,
		Body:           stored,
	})
	s.mu.Unlock()

	return http.StatusCreated, adapterInstance{
		ID:          s.cfg.InstanceID,
		ResourceKey: resourceKey{Name: name, AdapterKindKey: kind, ResourceKindKey: s.cfg.ResourceKindKey},
	}
}
