// Package nimock runs a loopback HTTP appliance that impersonates VCF
// Operations for Networks 9.1.
//
// The server is pinned to docs/contract.json: it builds its routing table from
// the operations that contract names and serves nothing else, so a request to
// any other endpoint of the real API surface is answered with 404. Every
// request it receives is appended to a log the tests read back.
package nimock

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
)

// Request is one recorded inbound HTTP request.
type Request struct {
	Method   string
	Path     string
	RawQuery string
	Header   http.Header
	Body     []byte
	// OperationID is the contract operation the request was routed to, or ""
	// when the request did not match any contract operation.
	OperationID string
}

// AppResult is one per-application entry of a task progress report.
type AppResult struct {
	EntityID     string
	Name         string
	ResponseCode string
	ErrorMessage string
}

// ProgressStep is one task progress report. Successive polls of the task walk
// the scenario's steps in order; the final step repeats forever.
type ProgressStep struct {
	Status   string
	Progress float64
	Apps     []AppResult
}

// Scenario is the appliance state the server starts with.
type Scenario struct {
	Username  string
	Password  string
	Token     string
	Expiry    int64
	RequestID string
	TaskName  string
	StartTime int64
	Steps     []ProgressStep
}

type route struct {
	operationID   string
	method        string
	segments      []string
	authenticated bool
}

type contractFile struct {
	BasePath string `json:"base_path"`
	Auth     struct {
		Header      string `json:"header"`
		ValueFormat string `json:"value_format"`
	} `json:"auth"`
	Operations map[string]struct {
		Method        string `json:"method"`
		Path          string `json:"path"`
		Authenticated bool   `json:"authenticated"`
	} `json:"operations"`
}

// Server is a running loopback appliance.
type Server struct {
	scenario  Scenario
	basePath  string
	authPfx   string
	routes    []route
	hs        *httptest.Server
	mu        sync.Mutex
	requests  []Request
	pollIndex int
	tokenLive bool
}

// New reads the contract at contractPath, builds the routing table from it and
// starts a loopback server.
func New(contractPath string, sc Scenario) (*Server, error) {
	raw, err := os.ReadFile(contractPath)
	if err != nil {
		return nil, fmt.Errorf("read contract: %w", err)
	}
	var cf contractFile
	if err := json.Unmarshal(raw, &cf); err != nil {
		return nil, fmt.Errorf("parse contract: %w", err)
	}
	if cf.BasePath == "" || len(cf.Operations) == 0 {
		return nil, fmt.Errorf("contract %s names no base_path or no operations", contractPath)
	}
	prefix, _, ok := strings.Cut(cf.Auth.ValueFormat, "{")
	if !ok {
		return nil, fmt.Errorf("contract auth.value_format %q has no {token} placeholder", cf.Auth.ValueFormat)
	}

	s := &Server{
		scenario:  sc,
		basePath:  strings.TrimSuffix(cf.BasePath, "/"),
		authPfx:   prefix,
		tokenLive: true,
	}
	for id, op := range cf.Operations {
		s.routes = append(s.routes, route{
			operationID:   id,
			method:        strings.ToUpper(op.Method),
			segments:      splitPath(op.Path),
			authenticated: op.Authenticated,
		})
	}
	s.hs = httptest.NewServer(http.HandlerFunc(s.serve))
	return s, nil
}

// URL is the appliance root, without the contract base path.
func (s *Server) URL() string { return s.hs.URL }

// Close shuts the server down.
func (s *Server) Close() { s.hs.Close() }

// Requests returns a copy of the request log, oldest first.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.requests))
	copy(out, s.requests)
	return out
}

// RequestsFor returns the logged requests routed to one contract operation.
func (s *Server) RequestsFor(operationID string) []Request {
	var out []Request
	for _, r := range s.Requests() {
		if r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

func splitPath(p string) []string {
	var out []string
	for _, seg := range strings.Split(strings.Trim(p, "/"), "/") {
		if seg != "" {
			out = append(out, seg)
		}
	}
	return out
}

func (s *Server) match(r *http.Request) (route, map[string]string, bool, bool) {
	if !strings.HasPrefix(r.URL.Path, s.basePath+"/") {
		return route{}, nil, false, false
	}
	got := splitPath(strings.TrimPrefix(r.URL.Path, s.basePath))
	pathKnown := false
	for _, rt := range s.routes {
		if len(rt.segments) != len(got) {
			continue
		}
		params := map[string]string{}
		ok := true
		for i, seg := range rt.segments {
			if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
				if got[i] == "" {
					ok = false
					break
				}
				params[strings.Trim(seg, "{}")] = got[i]
				continue
			}
			if seg != got[i] {
				ok = false
				break
			}
		}
		if !ok {
			continue
		}
		pathKnown = true
		if rt.method == r.Method {
			return rt, params, true, true
		}
	}
	return route{}, nil, false, pathKnown
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	_ = r.Body.Close()

	rt, params, matched, pathKnown := s.match(r)

	s.mu.Lock()
	s.requests = append(s.requests, Request{
		Method:      r.Method,
		Path:        r.URL.Path,
		RawQuery:    r.URL.RawQuery,
		Header:      r.Header.Clone(),
		Body:        body,
		OperationID: rt.operationID,
	})
	s.mu.Unlock()

	if !matched {
		if pathKnown {
			writeErr(w, http.StatusMethodNotAllowed, "method not allowed for this resource")
			return
		}
		writeErr(w, http.StatusNotFound, "no such operation in the pinned contract: "+r.Method+" "+r.URL.Path)
		return
	}

	if rt.authenticated {
		s.mu.Lock()
		live := s.tokenLive
		s.mu.Unlock()
		if !live || r.Header.Get("Authorization") != s.authPfx+s.scenario.Token {
			writeErr(w, http.StatusUnauthorized, "invalid or expired auth token")
			return
		}
	}

	switch rt.operationID {
	case "create":
		s.create(w, body)
	case "delete":
		s.mu.Lock()
		s.tokenLive = false
		s.mu.Unlock()
		w.WriteHeader(http.StatusNoContent)
	case "saveDiscoveredApplications":
		s.save(w, body)
	case "getBulkApplicationTaskProgress":
		s.progress(w, params["requestId"])
	default:
		writeErr(w, http.StatusNotFound, "unhandled operation "+rt.operationID)
	}
}

type domainBody struct {
	DomainType *string `json:"domain_type"`
	Value      *string `json:"value"`
}

type credentialBody struct {
	Username *string     `json:"username"`
	Password *string     `json:"password"`
	Domain   *domainBody `json:"domain"`
}

func (s *Server) create(w http.ResponseWriter, body []byte) {
	var in credentialBody
	if err := strictDecode(body, &in); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if in.Username == nil || in.Password == nil {
		writeErr(w, http.StatusBadRequest, "username and password are required")
		return
	}
	if in.Domain != nil && in.Domain.DomainType != nil && *in.Domain.DomainType != "LOCAL" && *in.Domain.DomainType != "LDAP" {
		writeErr(w, http.StatusBadRequest, "domain_type must be LDAP or LOCAL")
		return
	}
	if *in.Username != s.scenario.Username || *in.Password != s.scenario.Password {
		writeErr(w, http.StatusUnauthorized, "bad credentials")
		return
	}
	s.mu.Lock()
	s.tokenLive = true
	s.mu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{
		"token":  s.scenario.Token,
		"expiry": s.scenario.Expiry,
	})
}

type discoveredAppBody struct {
	SourceEntityID *string `json:"source_entity_id"`
}

type saveBody struct {
	DiscoveredApps []discoveredAppBody `json:"discovered_apps"`
	DiscoveryType  *string             `json:"discovery_type"`
	EnableIntent   *bool               `json:"enable_intent"`
}

func (s *Server) save(w http.ResponseWriter, body []byte) {
	var in saveBody
	if err := strictDecode(body, &in); err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	if len(in.DiscoveredApps) == 0 {
		writeErr(w, http.StatusBadRequest, "discovered_apps must not be empty")
		return
	}
	for _, a := range in.DiscoveredApps {
		if a.SourceEntityID == nil || *a.SourceEntityID == "" {
			writeErr(w, http.StatusBadRequest, "every discovered app needs a source_entity_id")
			return
		}
	}
	if in.DiscoveryType != nil {
		switch *in.DiscoveryType {
		case "MANUAL", "PATTERN_BASED", "SERVICE_NOW", "FLOW_BASED_DISCOVERY":
		default:
			writeErr(w, http.StatusBadRequest, "unknown discovery_type "+*in.DiscoveryType)
			return
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"request_id":   s.scenario.RequestID,
		"callback_API": "api/ni/groups/task/progress/" + s.scenario.RequestID + "/",
	})
}

func (s *Server) progress(w http.ResponseWriter, requestID string) {
	if requestID != s.scenario.RequestID {
		writeErr(w, http.StatusNotFound, "no task for request id "+requestID)
		return
	}
	s.mu.Lock()
	idx := s.pollIndex
	if idx >= len(s.scenario.Steps) {
		idx = len(s.scenario.Steps) - 1
	}
	s.pollIndex++
	step := s.scenario.Steps[idx]
	s.mu.Unlock()

	apps := make([]map[string]any, 0, len(step.Apps))
	for _, a := range step.Apps {
		entry := map[string]any{
			"entity_id":     a.EntityID,
			"name":          a.Name,
			"response_code": a.ResponseCode,
		}
		if a.ErrorMessage != "" {
			entry["error_message"] = a.ErrorMessage
		}
		apps = append(apps, entry)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"request_id":        s.scenario.RequestID,
		"task_name":         s.scenario.TaskName,
		"status":            step.Status,
		"progress":          step.Progress,
		"start_time":        s.scenario.StartTime,
		"app_save_response": apps,
	})
}

func strictDecode(body []byte, into any) error {
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.DisallowUnknownFields()
	if err := dec.Decode(into); err != nil {
		return fmt.Errorf("request body does not match the pinned contract: %v", err)
	}
	return nil
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func writeErr(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]any{"code": status, "message": msg})
}
