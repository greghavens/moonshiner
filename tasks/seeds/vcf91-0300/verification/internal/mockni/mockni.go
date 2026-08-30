// Package mockni is a loopback double for the VCF Operations for Networks API.
//
// It serves exactly the two operations named in docs/contract.json —
// validateVCenter and addVcenterDatasource — at the paths and with the status
// codes and body shapes that contract records. Every other route answers 404
// with an ApiError body, so a client that drifts onto an endpoint the contract
// does not name fails loudly instead of silently passing.
//
// The server listens on 127.0.0.1 only. It never reaches the network, and no
// live VMware endpoint is involved anywhere in this repository.
//
// Every request that arrives is appended to a request log that tests read back
// through Requests, RequestsFor and Created. The log records the raw body bytes
// exactly as they arrived, which is what lets a test assert the wire shape of a
// request rather than the shape of the struct that produced it.
//
// This file is part of the protected verification harness. Do not edit it.
package mockni

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"sync"
	"testing"
)

// Operation IDs, spelled exactly as the OpenAPI document spells them.
const (
	OpValidateVCenter      = "validateVCenter"
	OpAddVcenterDatasource = "addVcenterDatasource"
)

// Paths, including the /api/ni server base path from the spec.
const (
	PathValidateVCenter      = "/api/ni/data-sources/vcenters/validate"
	PathAddVcenterDatasource = "/api/ni/data-sources/vcenters"
)

// AuthPrefix is the ApiKeyAuth value prefix: "NetworkInsight {token}".
const AuthPrefix = "NetworkInsight "

// LoggedRequest is one request as it arrived on the wire.
type LoggedRequest struct {
	// OperationID is the contract operation this request landed on, or "" if
	// the request did not match any operation the contract names.
	OperationID string
	Method      string
	Path        string
	Header      http.Header
	Body        []byte
	Status      int
}

// JSONBody decodes the logged body into a map of raw members. Decoding into raw
// members rather than a typed struct is deliberate: it preserves exactly which
// keys were present, which is the property the wire-shape assertions check.
func (r LoggedRequest) JSONBody() (map[string]json.RawMessage, error) {
	var m map[string]json.RawMessage
	if err := json.Unmarshal(r.Body, &m); err != nil {
		return nil, fmt.Errorf("body is not a JSON object: %w (raw: %s)", err, r.Body)
	}
	return m, nil
}

// ValidateOutcome is the reply the mock gives to validateVCenter.
type ValidateOutcome struct {
	// HTTPStatus is the HTTP status line. The spec allows a failed validation
	// to arrive either as HTTP 200 with a non-200 body code, or as an HTTP
	// error status with an ApiError body.
	HTTPStatus int
	// Code is the body-level `code` member.
	Code int
	// Message is the body-level `message` member.
	Message string
}

// ValidationSucceeds is the spec's documented success reply for validateVCenter.
func ValidationSucceeds() ValidateOutcome {
	return ValidateOutcome{HTTPStatus: 200, Code: 200, Message: "Validation successful."}
}

// ValidationFailsInBody models the case the spec's response schema allows and
// that a naive client misses: HTTP 200, but the body verdict is a failure.
func ValidationFailsInBody(code int, message string) ValidateOutcome {
	return ValidateOutcome{HTTPStatus: 200, Code: code, Message: message}
}

// ValidationFailsWithStatus models a failure signalled by the HTTP status, with
// an ApiError body.
func ValidationFailsWithStatus(status int, message string) ValidateOutcome {
	return ValidateOutcome{HTTPStatus: status, Code: status, Message: message}
}

// CreatedDataSource is a data source the mock actually created, i.e. the effect
// of a successful addVcenterDatasource call. A test asserts that this stays
// empty when the precheck fails: that is what "nothing was changed" means here.
type CreatedDataSource struct {
	EntityID string
	Body     []byte
}

// Options configures a mock server.
type Options struct {
	// Token is the API key the server accepts. Requests whose Authorization
	// header is not exactly AuthPrefix+Token are rejected with 401 and are
	// logged with OperationID set, so a test can see the client did reach the
	// right route with the wrong credential.
	Token string
	// Validate is the reply validateVCenter gives. The zero value means
	// ValidationSucceeds.
	Validate ValidateOutcome
}

// Server is a running loopback mock.
type Server struct {
	url      string
	listener net.Listener
	srv      *http.Server

	mu       sync.Mutex
	log      []LoggedRequest
	created  []CreatedDataSource
	nextID   int
	validate ValidateOutcome
	token    string
}

// Start brings up a mock on 127.0.0.1 and registers cleanup with t.
func Start(t *testing.T, opts Options) *Server {
	t.Helper()

	if opts.Validate.HTTPStatus == 0 {
		opts.Validate = ValidationSucceeds()
	}

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("mockni: listen on loopback: %v", err)
	}

	s := &Server{
		url:      "http://" + ln.Addr().String(),
		listener: ln,
		validate: opts.Validate,
		token:    opts.Token,
		nextID:   1,
	}
	s.srv = &http.Server{Handler: http.HandlerFunc(s.route)}

	go func() { _ = s.srv.Serve(ln) }()
	t.Cleanup(func() { _ = s.srv.Close() })

	return s
}

// URL is the base URL of the running mock, e.g. http://127.0.0.1:38211. It has
// no path suffix: the /api/ni server base path belongs to the client.
func (s *Server) URL() string { return s.url }

// Requests returns a copy of the request log, in arrival order.
func (s *Server) Requests() []LoggedRequest {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]LoggedRequest, len(s.log))
	copy(out, s.log)
	return out
}

// RequestsFor returns the logged requests that landed on one operation.
func (s *Server) RequestsFor(operationID string) []LoggedRequest {
	var out []LoggedRequest
	for _, r := range s.Requests() {
		if r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

// OperationOrder returns the operation IDs in the order they were called.
// Requests that matched no contract operation appear as "<unrouted>".
func (s *Server) OperationOrder() []string {
	reqs := s.Requests()
	out := make([]string, 0, len(reqs))
	for _, r := range reqs {
		if r.OperationID == "" {
			out = append(out, "<unrouted>")
			continue
		}
		out = append(out, r.OperationID)
	}
	return out
}

// Created returns the data sources that were actually created.
func (s *Server) Created() []CreatedDataSource {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]CreatedDataSource, len(s.created))
	copy(out, s.created)
	return out
}

func (s *Server) route(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	_ = r.Body.Close()

	operationID := ""
	switch {
	case r.Method == http.MethodPost && r.URL.Path == PathValidateVCenter:
		operationID = OpValidateVCenter
	case r.Method == http.MethodPost && r.URL.Path == PathAddVcenterDatasource:
		operationID = OpAddVcenterDatasource
	}

	entry := LoggedRequest{
		OperationID: operationID,
		Method:      r.Method,
		Path:        r.URL.Path,
		Header:      r.Header.Clone(),
		Body:        body,
	}

	status, reply := s.handle(operationID, r, body)
	entry.Status = status

	s.mu.Lock()
	s.log = append(s.log, entry)
	s.mu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(reply)
}

// handle produces the status and reply body, and applies the one side effect
// this API has: creating a data source.
func (s *Server) handle(operationID string, r *http.Request, body []byte) (int, any) {
	if operationID == "" {
		return http.StatusNotFound, apiError(404, fmt.Sprintf(
			"no such operation: %s %s (this mock serves only %s and %s)",
			r.Method, r.URL.Path, OpValidateVCenter, OpAddVcenterDatasource))
	}

	if got, want := r.Header.Get("Authorization"), AuthPrefix+s.token; got != want {
		return http.StatusUnauthorized, apiError(401, fmt.Sprintf(
			"unauthorized: Authorization header was %q, want %q", got, want))
	}

	var decoded map[string]json.RawMessage
	if err := json.Unmarshal(body, &decoded); err != nil {
		return http.StatusBadRequest, apiError(400, "request body is not a JSON object")
	}

	if operationID == OpValidateVCenter {
		s.mu.Lock()
		out := s.validate
		s.mu.Unlock()
		if out.HTTPStatus == http.StatusOK {
			return out.HTTPStatus, map[string]any{"code": out.Code, "message": out.Message}
		}
		return out.HTTPStatus, apiError(out.Code, out.Message)
	}

	// addVcenterDatasource: the mutating call.
	s.mu.Lock()
	id := fmt.Sprintf("18230:902:%d", 993642895+s.nextID)
	s.nextID++
	s.created = append(s.created, CreatedDataSource{EntityID: id, Body: body})
	s.mu.Unlock()

	reply := map[string]any{"entity_id": id, "entity_type": "VCenterDataSource"}
	for _, k := range []string{"ip", "fqdn", "proxy_id", "nickname", "enabled", "notes"} {
		if v, ok := decoded[k]; ok {
			reply[k] = v
		}
	}
	return http.StatusCreated, reply
}

func apiError(code int, message string) map[string]any {
	return map[string]any{"code": code, "message": message}
}
