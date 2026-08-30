// Package mockapi serves a loopback stand-in for the VCF Automation deployment
// API.
//
// The server is pinned to docs/contract.json: it routes only the operations the
// contract names, and it records every request it receives so a test can assert
// the exact wire shape the client produced.
package mockapi

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
)

const maxBody = 4 << 20

// Action is one entry of the action list served by the precheck operation.
type Action struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	DisplayName string `json:"displayName"`
	ActionType  string `json:"actionType"`
	Valid       bool   `json:"valid"`
}

// Deployment is the seeded state of one deployment.
type Deployment struct {
	// Actions is what the precheck operation returns for this deployment.
	Actions []Action
}

// Options configures a Server.
type Options struct {
	// ContractPath is the path to docs/contract.json. Required.
	ContractPath string

	// Token is the bearer token the server requires. Required.
	Token string

	// Deployments is the seeded state, keyed by deployment id.
	Deployments map[string]Deployment

	// ActionsStatus, when non-zero, makes the precheck operation respond with
	// this status code instead of its normal response.
	ActionsStatus int

	// RequestStatus, when non-zero, makes the mutating operation respond with
	// this status code instead of its normal response.
	RequestStatus int
}

// Recorded is one request the server received, in the order it arrived.
type Recorded struct {
	Method   string
	Path     string
	RawQuery string
	Header   http.Header
	Body     []byte
}

// handler serves one contract operation.
type handler func(w http.ResponseWriter, r *http.Request, params map[string]string, body []byte)

type route struct {
	id      string
	method  string
	segs    []string
	handler handler
}

// Server is a running loopback API.
type Server struct {
	url    string
	routes []route
	token  string

	deployments   map[string]Deployment
	actionsStatus int
	requestStatus int

	httpSrv  *http.Server
	listener net.Listener

	mu         sync.Mutex
	log        []Recorded
	requestSeq int
}

// contractDoc is the slice of docs/contract.json the mock is pinned to.
type contractDoc struct {
	Operations []struct {
		ID     string `json:"id"`
		Method string `json:"method"`
		Path   string `json:"path"`
	} `json:"operations"`
}

// Start reads the contract, registers a route for each operation it names and
// starts listening on loopback.
func Start(opts Options) (*Server, error) {
	if opts.ContractPath == "" {
		return nil, errors.New("mockapi: ContractPath is required")
	}
	if opts.Token == "" {
		return nil, errors.New("mockapi: Token is required")
	}

	raw, err := os.ReadFile(opts.ContractPath)
	if err != nil {
		return nil, fmt.Errorf("mockapi: read contract: %w", err)
	}
	var doc contractDoc
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, fmt.Errorf("mockapi: parse contract: %w", err)
	}
	if len(doc.Operations) == 0 {
		return nil, errors.New("mockapi: contract names no operations")
	}

	s := &Server{
		token:         opts.Token,
		deployments:   make(map[string]Deployment, len(opts.Deployments)),
		actionsStatus: opts.ActionsStatus,
		requestStatus: opts.RequestStatus,
	}
	for id, dep := range opts.Deployments {
		s.deployments[id] = dep
	}

	// Only the operations the contract names get a route. Anything else the
	// real API offers stays unserved.
	for _, op := range doc.Operations {
		var h handler
		switch op.ID {
		case "getDeploymentActions":
			h = s.handleGetDeploymentActions
		case "submitDeploymentActionRequest":
			h = s.handleSubmitDeploymentActionRequest
		default:
			return nil, fmt.Errorf("mockapi: contract names operation %q, which this mock does not serve", op.ID)
		}
		if op.Method == "" || op.Path == "" {
			return nil, fmt.Errorf("mockapi: operation %q has no method or path", op.ID)
		}
		s.routes = append(s.routes, route{
			id:      op.ID,
			method:  op.Method,
			segs:    splitPath(op.Path),
			handler: h,
		})
	}

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("mockapi: listen: %w", err)
	}
	s.listener = ln
	s.url = "http://" + ln.Addr().String()
	s.httpSrv = &http.Server{Handler: http.HandlerFunc(s.serveHTTP)}
	go func() { _ = s.httpSrv.Serve(ln) }()

	return s, nil
}

// URL is the scheme://host:port root the server is listening on.
func (s *Server) URL() string { return s.url }

// Close shuts the server down.
func (s *Server) Close() {
	if s.httpSrv != nil {
		_ = s.httpSrv.Close()
	}
}

// Requests returns a copy of the request log, oldest first.
func (s *Server) Requests() []Recorded {
	s.mu.Lock()
	defer s.mu.Unlock()

	out := make([]Recorded, len(s.log))
	for i, rec := range s.log {
		cp := rec
		cp.Header = rec.Header.Clone()
		cp.Body = append([]byte(nil), rec.Body...)
		out[i] = cp
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	recordIndex := s.record(r)
	body, _ := io.ReadAll(io.LimitReader(r.Body, maxBody))
	s.recordBody(recordIndex, body)

	got := splitPath(r.URL.Path)
	pathKnown := false
	for _, rt := range s.routes {
		params, ok := matchPath(rt.segs, got)
		if !ok {
			continue
		}
		pathKnown = true
		if rt.method != r.Method {
			continue
		}
		if r.Header.Get("Authorization") != "Bearer "+s.token {
			writeError(w, http.StatusUnauthorized, "invalid or missing bearer token")
			return
		}
		rt.handler(w, r, params, body)
		return
	}

	if pathKnown {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed for this operation")
		return
	}
	writeError(w, http.StatusNotFound, "path is not an operation named by the pinned contract")
}

func (s *Server) record(r *http.Request) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log = append(s.log, Recorded{
		Method:   r.Method,
		Path:     r.URL.Path,
		RawQuery: r.URL.RawQuery,
		Header:   r.Header.Clone(),
	})
	return len(s.log) - 1
}

func (s *Server) recordBody(index int, body []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log[index].Body = append([]byte(nil), body...)
}

func (s *Server) handleGetDeploymentActions(w http.ResponseWriter, _ *http.Request, params map[string]string, _ []byte) {
	if s.actionsStatus != 0 {
		writeError(w, s.actionsStatus, "injected precheck failure")
		return
	}
	dep, ok := s.deployments[params["deploymentId"]]
	if !ok {
		writeError(w, http.StatusNotFound, "deployment not found")
		return
	}
	actions := dep.Actions
	if actions == nil {
		actions = []Action{}
	}
	writeJSON(w, http.StatusOK, actions)
}

// handleSubmitDeploymentActionRequest deliberately does not re-check whether the
// action is available or valid. The gate lives in the client, and the mock has
// to stay willing so a test can tell a client that skipped the gate apart from
// one that honoured it.
func (s *Server) handleSubmitDeploymentActionRequest(w http.ResponseWriter, r *http.Request, params map[string]string, body []byte) {
	if s.requestStatus != 0 {
		writeError(w, s.requestStatus, "injected failure")
		return
	}
	depID := params["deploymentId"]
	if _, ok := s.deployments[depID]; !ok {
		writeError(w, http.StatusNotFound, "deployment not found")
		return
	}
	if ct := r.Header.Get("Content-Type"); ct != "application/json" {
		writeError(w, http.StatusUnsupportedMediaType, "Content-Type must be application/json, got "+ct)
		return
	}

	var in struct {
		ActionID string         `json:"actionId"`
		Inputs   map[string]any `json:"inputs"`
		Reason   string         `json:"reason"`
	}
	if err := json.Unmarshal(body, &in); err != nil {
		writeError(w, http.StatusBadRequest, "body is not a ResourceActionRequest")
		return
	}
	if in.ActionID == "" {
		writeError(w, http.StatusBadRequest, "actionId is required")
		return
	}

	s.mu.Lock()
	s.requestSeq++
	seq := s.requestSeq
	s.mu.Unlock()

	writeJSON(w, http.StatusOK, map[string]any{
		"id":           fmt.Sprintf("req-%d", seq),
		"actionId":     in.ActionID,
		"deploymentId": depID,
		"status":       "PENDING",
		"inputs":       in.Inputs,
		"details":      in.Reason,
	})
}

// splitPath turns "/deployment/api/deployments/{deploymentId}/actions" into its
// segments.
func splitPath(p string) []string {
	return strings.Split(strings.Trim(p, "/"), "/")
}

// matchPath matches a request's segments against a route template and returns
// the path parameters it filled.
func matchPath(tmpl, got []string) (map[string]string, bool) {
	if len(tmpl) != len(got) {
		return nil, false
	}
	params := map[string]string{}
	for i, seg := range tmpl {
		if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
			if got[i] == "" {
				return nil, false
			}
			params[seg[1:len(seg)-1]] = got[i]
			continue
		}
		if seg != got[i] {
			return nil, false
		}
	}
	return params, true
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]any{
		"status":  status,
		"message": message,
	})
}
