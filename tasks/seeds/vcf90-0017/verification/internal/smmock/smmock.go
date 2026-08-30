// Package smmock is a loopback stand-in for an SDDC Manager appliance.
//
// It builds its routing table from docs/contract.json and serves only the five
// operations that contract names; every other request is recorded as unmatched
// and answered 404. Each request it receives - method, path, resolved path
// parameters, query string, headers and raw body bytes - is appended to a
// request log the tests can read.
//
// The mock is deliberately strict about the things the specification is strict
// about (authorization on the operations that require it, required schema
// properties, unknown properties, the declared JSON shape of a request body)
// and deliberately permissive about the thing the verifier exists to check: an
// optional property sent as an empty string is accepted here and rejected
// there.
//
// The server listens on 127.0.0.1. No live VMware endpoint is contacted.
//
// Do not edit this package. It is replaced wholesale during grading.
package smmock

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"sync"

	"vcf.local/sddchosts/internal/contract"
)

// Default identifiers and timestamps. They are constants so that every run
// produces byte-identical responses.
const (
	DefaultAccessToken    = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.sddc-manager-9-0.mock"
	DefaultRefreshTokenID = "0f4d1e2c-6b7a-4c8d-9e01-2f3a4b5c6d7e"
	DefaultValidationID   = "b1d9f3a4-52c7-4c1e-8a6f-90b2d7c41e35"
	DefaultTaskID         = "7c2a5e18-93bf-4d60-b1c4-2e8f05a7d913"
	DefaultCreationTime   = "2025-06-11T09:14:27.481Z"
	DefaultCompletionTime = "2025-06-11T09:41:03.902Z"
)

// Request is one recorded HTTP request.
type Request struct {
	Index       int
	OperationID string // empty when the request matched no contract operation
	Method      string
	Path        string
	RawQuery    string
	Header      http.Header
	Body        []byte
	PathParams  map[string]string
}

// HasBody reports whether the request carried any body bytes.
func (r Request) HasBody() bool { return len(r.Body) > 0 }

// DecodeObject decodes the request body as a JSON object.
func (r Request) DecodeObject() (map[string]any, error) {
	var out map[string]any
	if err := json.Unmarshal(r.Body, &out); err != nil {
		return nil, fmt.Errorf("request %d (%s): body is not a JSON object: %w", r.Index, r.OperationID, err)
	}
	if out == nil {
		return nil, fmt.Errorf("request %d (%s): body is JSON null, not an object", r.Index, r.OperationID)
	}
	return out, nil
}

// DecodeArray decodes the request body as a JSON array of objects.
func (r Request) DecodeArray() ([]map[string]any, error) {
	var out []map[string]any
	if err := json.Unmarshal(r.Body, &out); err != nil {
		return nil, fmt.Errorf("request %d (%s): body is not a JSON array of objects: %w", r.Index, r.OperationID, err)
	}
	if out == nil {
		return nil, fmt.Errorf("request %d (%s): body is JSON null, not an array", r.Index, r.OperationID)
	}
	return out, nil
}

// ObjectKeys returns the sorted top-level keys of an object body.
func (r Request) ObjectKeys() ([]string, error) {
	obj, err := r.DecodeObject()
	if err != nil {
		return nil, err
	}
	return sortedKeys(obj), nil
}

// ItemKeys returns the sorted keys of each element of an array body.
func (r Request) ItemKeys() ([][]string, error) {
	items, err := r.DecodeArray()
	if err != nil {
		return nil, err
	}
	out := make([][]string, 0, len(items))
	for _, item := range items {
		out = append(out, sortedKeys(item))
	}
	return out, nil
}

// ValidationState is one Validation payload the mock will serve.
type ValidationState struct {
	ExecutionStatus string
	ResultStatus    string
	Description     string
	Checks          []ValidationCheck
}

// ValidationCheck is one entry of Validation.validationChecks.
type ValidationCheck struct {
	Description  string
	Severity     string
	ResultStatus string
}

// TaskState is one Task payload the mock will serve.
type TaskState struct {
	Status        string
	ErrorMessages []string
}

// Failure injects a non-2xx response for an operation.
type Failure struct {
	StatusCode int
	ErrorCode  string
	Message    string
	// Occurrences limits the injection to the first N calls of the operation.
	// Zero means every call.
	Occurrences int
}

// Options configures the appliance the mock impersonates.
type Options struct {
	AccessToken    string
	RefreshTokenID string
	ValidationID   string
	TaskID         string

	// PostValidation is the body of the 202 answer to
	// validateHostCommissionSpec. Validations is the sequence of bodies served
	// by successive getHostCommissionValidationByID calls; the last entry
	// repeats once the sequence is exhausted.
	PostValidation ValidationState
	Validations    []ValidationState

	// PostTask is the body of the 202 answer to commissionHosts. Tasks is the
	// sequence of bodies served by successive getTask calls; the last entry
	// repeats once the sequence is exhausted.
	PostTask TaskState
	Tasks    []TaskState

	TaskName string
	TaskType string

	// Failures maps an operationId to an injected non-2xx response.
	Failures map[string]Failure
}

func (o *Options) applyDefaults() {
	if o.AccessToken == "" {
		o.AccessToken = DefaultAccessToken
	}
	if o.RefreshTokenID == "" {
		o.RefreshTokenID = DefaultRefreshTokenID
	}
	if o.ValidationID == "" {
		o.ValidationID = DefaultValidationID
	}
	if o.TaskID == "" {
		o.TaskID = DefaultTaskID
	}
	if o.TaskName == "" {
		o.TaskName = "Commissioning host(s) to VMware Cloud Foundation"
	}
	if o.TaskType == "" {
		o.TaskType = "HOST_COMMISSION"
	}
	if o.PostValidation.ExecutionStatus == "" {
		o.PostValidation = ValidationState{ExecutionStatus: "IN_PROGRESS", ResultStatus: "UNKNOWN"}
	}
	if len(o.Validations) == 0 {
		o.Validations = []ValidationState{{ExecutionStatus: "COMPLETED", ResultStatus: "SUCCEEDED"}}
	}
	if o.PostTask.Status == "" {
		o.PostTask = TaskState{Status: "IN_PROGRESS"}
	}
	if len(o.Tasks) == 0 {
		o.Tasks = []TaskState{{Status: "SUCCESSFUL"}}
	}
}

type route struct {
	op       contract.Operation
	segments []string
}

// Server is a running loopback SDDC Manager mock.
type Server struct {
	contract *contract.Contract
	opts     Options
	routes   []route
	http     *httptest.Server

	mu       sync.Mutex
	log      []Request
	calls    map[string]int
	injected map[string]int
}

// New starts a mock pinned to c. Call Close when finished.
func New(c *contract.Contract, opts Options) (*Server, error) {
	if c == nil {
		return nil, fmt.Errorf("smmock: nil contract")
	}
	opts.applyDefaults()
	for id := range opts.Failures {
		if _, err := c.Operation(id); err != nil {
			return nil, fmt.Errorf("smmock: failure injected for unknown operation: %w", err)
		}
	}
	s := &Server{
		contract: c,
		opts:     opts,
		calls:    map[string]int{},
		injected: map[string]int{},
	}
	for _, id := range c.OperationIDs() {
		op := c.Operations[id]
		s.routes = append(s.routes, route{op: op, segments: contract.Segments(op.Path)})
	}
	s.http = httptest.NewServer(http.HandlerFunc(s.serve))
	return s, nil
}

// URL is the appliance root the client under test should be pointed at.
func (s *Server) URL() string { return s.http.URL }

// Close shuts the server down.
func (s *Server) Close() { s.http.Close() }

// AccessToken is the token createToken hands out.
func (s *Server) AccessToken() string { return s.opts.AccessToken }

// ValidationID is the id validateHostCommissionSpec hands out.
func (s *Server) ValidationID() string { return s.opts.ValidationID }

// TaskID is the id commissionHosts hands out.
func (s *Server) TaskID() string { return s.opts.TaskID }

// Requests returns a copy of the request log in arrival order.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.log))
	copy(out, s.log)
	return out
}

// RequestsFor returns the recorded requests for one operation.
func (s *Server) RequestsFor(operationID string) []Request {
	var out []Request
	for _, r := range s.Requests() {
		if r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

// OperationSequence returns the operationIds in arrival order. An unmatched
// request appears as "<unmatched METHOD /path>".
func (s *Server) OperationSequence() []string {
	reqs := s.Requests()
	out := make([]string, 0, len(reqs))
	for _, r := range reqs {
		if r.OperationID == "" {
			out = append(out, fmt.Sprintf("<unmatched %s %s>", r.Method, r.Path))
			continue
		}
		out = append(out, r.OperationID)
	}
	return out
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	_ = r.Body.Close()

	op, params, matched := s.match(r.Method, r.URL.Path)
	rec := Request{
		Method:     r.Method,
		Path:       r.URL.Path,
		RawQuery:   r.URL.RawQuery,
		Header:     r.Header.Clone(),
		Body:       body,
		PathParams: params,
	}
	if matched {
		rec.OperationID = op.OperationID
	}

	s.mu.Lock()
	rec.Index = len(s.log)
	s.log = append(s.log, rec)
	if matched {
		s.calls[op.OperationID]++
	}
	call := 0
	if matched {
		call = s.calls[op.OperationID]
	}
	s.mu.Unlock()

	if !matched {
		s.writeError(w, http.StatusNotFound, "NOT_FOUND",
			fmt.Sprintf("no operation is served at %s %s; this mock serves only %s",
				r.Method, r.URL.Path, strings.Join(s.contract.OperationIDs(), ", ")))
		return
	}

	if s.contract.Authorization.RequiresAuthorization(op.OperationID) {
		want := s.contract.Authorization.HeaderValue(s.opts.AccessToken)
		if got := r.Header.Get(s.contract.Authorization.HeaderName); got != want {
			s.writeError(w, http.StatusUnauthorized, "UNAUTHORIZED",
				fmt.Sprintf("%s requires a valid %s header", op.OperationID,
					s.contract.Authorization.HeaderName))
			return
		}
	}

	if f, ok := s.takeFailure(op.OperationID); ok {
		s.writeError(w, f.StatusCode, f.ErrorCode, f.Message)
		return
	}

	switch op.OperationID {
	case "createToken":
		s.handleCreateToken(w, rec, op)
	case "validateHostCommissionSpec":
		s.handleValidateHosts(w, rec, op)
	case "getHostCommissionValidationByID":
		s.handleGetValidation(w, rec, call)
	case "commissionHosts":
		s.handleCommissionHosts(w, rec, op)
	case "getTask":
		s.handleGetTask(w, rec, call)
	default:
		s.writeError(w, http.StatusNotImplemented, "NOT_IMPLEMENTED",
			fmt.Sprintf("the mock has no handler for %s", op.OperationID))
	}
}

func (s *Server) match(method, path string) (contract.Operation, map[string]string, bool) {
	got := contract.Segments(path)
	for _, rt := range s.routes {
		if !strings.EqualFold(rt.op.Method, method) || len(rt.segments) != len(got) {
			continue
		}
		params := map[string]string{}
		ok := true
		for i, want := range rt.segments {
			if name, isParam := contract.IsParamSegment(want); isParam {
				if got[i] == "" {
					ok = false
					break
				}
				params[name] = got[i]
				continue
			}
			if want != got[i] {
				ok = false
				break
			}
		}
		if ok {
			return rt.op, params, true
		}
	}
	return contract.Operation{}, nil, false
}

func (s *Server) takeFailure(operationID string) (Failure, bool) {
	f, ok := s.opts.Failures[operationID]
	if !ok {
		return Failure{}, false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if f.Occurrences > 0 && s.injected[operationID] >= f.Occurrences {
		return Failure{}, false
	}
	s.injected[operationID]++
	if f.StatusCode == 0 {
		f.StatusCode = http.StatusInternalServerError
	}
	return f, true
}

func (s *Server) handleCreateToken(w http.ResponseWriter, rec Request, op contract.Operation) {
	obj, err := rec.DecodeObject()
	if err != nil {
		s.writeError(w, http.StatusBadRequest, "BAD_REQUEST", err.Error())
		return
	}
	if err := checkProperties(op, sortedKeys(obj)); err != nil {
		s.writeError(w, http.StatusBadRequest, "BAD_REQUEST", err.Error())
		return
	}
	if obj["username"] == nil || obj["password"] == nil {
		s.writeError(w, http.StatusBadRequest, "BAD_REQUEST",
			"createToken needs username and password for this scenario")
		return
	}
	s.writeJSON(w, http.StatusCreated, map[string]any{
		"accessToken":  s.opts.AccessToken,
		"refreshToken": map[string]any{"id": s.opts.RefreshTokenID},
	})
}

func (s *Server) hostSpecArray(w http.ResponseWriter, rec Request, op contract.Operation) bool {
	items, err := rec.DecodeArray()
	if err != nil {
		s.writeError(w, http.StatusBadRequest, "BAD_REQUEST", err.Error())
		return false
	}
	if len(items) == 0 {
		s.writeError(w, http.StatusBadRequest, "BAD_REQUEST",
			op.OperationID+" needs at least one HostCommissionSpec")
		return false
	}
	for i, item := range items {
		if err := checkProperties(op, sortedKeys(item)); err != nil {
			s.writeError(w, http.StatusBadRequest, "BAD_REQUEST",
				fmt.Sprintf("HostCommissionSpec[%d]: %v", i, err))
			return false
		}
	}
	return true
}

func (s *Server) handleValidateHosts(w http.ResponseWriter, rec Request, op contract.Operation) {
	if !s.hostSpecArray(w, rec, op) {
		return
	}
	s.writeJSON(w, http.StatusAccepted, s.validationPayload(s.opts.PostValidation))
}

func (s *Server) handleGetValidation(w http.ResponseWriter, rec Request, call int) {
	if id := rec.PathParams["id"]; id != s.opts.ValidationID {
		s.writeError(w, http.StatusBadRequest, "BAD_REQUEST",
			fmt.Sprintf("no host commission validation with id %q", id))
		return
	}
	s.writeJSON(w, http.StatusAccepted, s.validationPayload(pick(s.opts.Validations, call)))
}

func (s *Server) handleCommissionHosts(w http.ResponseWriter, rec Request, op contract.Operation) {
	if !s.hostSpecArray(w, rec, op) {
		return
	}
	s.writeJSON(w, http.StatusAccepted, s.taskPayload(s.opts.PostTask))
}

func (s *Server) handleGetTask(w http.ResponseWriter, rec Request, call int) {
	if id := rec.PathParams["id"]; id != s.opts.TaskID {
		s.writeError(w, http.StatusNotFound, "NOT_FOUND",
			fmt.Sprintf("no task with id %q", id))
		return
	}
	s.writeJSON(w, http.StatusOK, s.taskPayload(pick(s.opts.Tasks, call)))
}

func (s *Server) validationPayload(v ValidationState) map[string]any {
	description := v.Description
	if description == "" {
		description = "Validation of the host commission specification"
	}
	out := map[string]any{
		"id":              s.opts.ValidationID,
		"description":     description,
		"executionStatus": v.ExecutionStatus,
		"resultStatus":    v.ResultStatus,
	}
	if len(v.Checks) > 0 {
		checks := make([]any, 0, len(v.Checks))
		for _, c := range v.Checks {
			checks = append(checks, map[string]any{
				"description":  c.Description,
				"severity":     c.Severity,
				"resultStatus": c.ResultStatus,
			})
		}
		out["validationChecks"] = checks
	}
	return out
}

func (s *Server) taskPayload(t TaskState) map[string]any {
	out := map[string]any{
		"id":                s.opts.TaskID,
		"name":              s.opts.TaskName,
		"type":              s.opts.TaskType,
		"status":            t.Status,
		"creationTimestamp": DefaultCreationTime,
	}
	if contract.Canonical(t.Status) != "PENDING" && contract.Canonical(t.Status) != "IN_PROGRESS" {
		out["completionTimestamp"] = DefaultCompletionTime
	}
	if len(t.ErrorMessages) > 0 {
		errs := make([]any, 0, len(t.ErrorMessages))
		for _, m := range t.ErrorMessages {
			errs = append(errs, map[string]any{"errorCode": "HOST_COMMISSION_FAILED", "message": m})
		}
		out["errors"] = errs
	}
	return out
}

func (s *Server) writeJSON(w http.ResponseWriter, code int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(payload)
}

func (s *Server) writeError(w http.ResponseWriter, code int, errorCode, message string) {
	s.writeJSON(w, code, map[string]any{
		"errorCode":      errorCode,
		"errorType":      "MOCK",
		"message":        message,
		"referenceToken": "smmock",
	})
}

// checkProperties enforces the contract's required and allowed property sets
// for an operation's request body. Empty-string values are accepted here on
// purpose: rejecting them is the verifier's job, not the appliance's.
func checkProperties(op contract.Operation, keys []string) error {
	if op.RequestBody == nil {
		return nil
	}
	allowed := map[string]bool{}
	for _, k := range op.RequestBody.AllowedProperties {
		allowed[k] = true
	}
	for _, k := range keys {
		if !allowed[k] {
			return fmt.Errorf("property %q is not part of the request schema (allowed: %s)",
				k, strings.Join(op.RequestBody.AllowedProperties, ", "))
		}
	}
	present := map[string]bool{}
	for _, k := range keys {
		present[k] = true
	}
	var missing []string
	for _, k := range op.RequestBody.RequiredProperties {
		if !present[k] {
			missing = append(missing, k)
		}
	}
	if len(missing) > 0 {
		return fmt.Errorf("required properties missing: %s", strings.Join(missing, ", "))
	}
	return nil
}

func pick[T any](states []T, call int) T {
	if call < 1 {
		call = 1
	}
	if call > len(states) {
		call = len(states)
	}
	return states[call-1]
}

func sortedKeys(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
