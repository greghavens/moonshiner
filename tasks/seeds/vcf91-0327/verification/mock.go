package vcfauto

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"regexp"
	"sort"
	"strings"
	"sync"
)

// RecordedRequest is one request the mock received.
type RecordedRequest struct {
	// OperationID is the contract operation the request was routed to, or ""
	// if the mock did not recognise it.
	OperationID string
	Method      string
	Path        string
	RawQuery    string
	Header      http.Header
	Body        []byte
}

// MockConfig seeds the mock. The caller supplies the state; nothing is baked
// into the mock itself.
type MockConfig struct {
	DeploymentID string
	ResourceID   string

	// ActionResult maps an actionId to the terminal request status the mock
	// settles on ("SUCCESSFUL" or "FAILED"). Unlisted actions succeed.
	ActionResult map[string]string
	// ActionDetails maps an actionId to the detail text reported on failure.
	ActionDetails map[string]string
	// PollsBeforeTerminal is how many GetRequest polls report INPROGRESS
	// before the terminal status is reported.
	PollsBeforeTerminal int
}

type mockRoute struct {
	op    Operation
	re    *regexp.Regexp
	names []string
}

type mockRequestState struct {
	id       string
	actionID string
	polls    int
}

// Mock is a loopback HTTP server pinned to a contract. It serves only the
// operations the contract names; anything else is 404 or 405.
type Mock struct {
	cfg    MockConfig
	routes []mockRoute
	srv    *http.Server
	listen net.Listener
	url    string

	mu       sync.Mutex
	log      []RecordedRequest
	requests map[string]*mockRequestState
	nextID   int
	name     string
	descr    string
	iconID   string
}

// NewMock starts a loopback server that serves the given contract.
func NewMock(c *Contract, cfg MockConfig) (*Mock, error) {
	if c == nil {
		return nil, fmt.Errorf("nil contract")
	}
	if cfg.DeploymentID == "" {
		return nil, fmt.Errorf("MockConfig.DeploymentID is required")
	}
	m := &Mock{
		cfg:      cfg,
		requests: map[string]*mockRequestState{},
		name:     cfg.DeploymentID,
	}
	for _, op := range c.Operations {
		re, names, err := compileTemplate(op.PathTemplate)
		if err != nil {
			return nil, fmt.Errorf("operation %s: %w", op.ID, err)
		}
		m.routes = append(m.routes, mockRoute{op: op, re: re, names: names})
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("start loopback listener: %w", err)
	}
	m.listen = listener
	m.url = "http://" + listener.Addr().String()
	m.srv = &http.Server{Handler: http.HandlerFunc(m.serve)}
	go func() {
		_ = m.srv.Serve(listener)
	}()
	return m, nil
}

func compileTemplate(tmpl string) (*regexp.Regexp, []string, error) {
	var names []string
	var b strings.Builder
	b.WriteString("^")
	last := 0
	for _, loc := range placeholderRE.FindAllStringSubmatchIndex(tmpl, -1) {
		b.WriteString(regexp.QuoteMeta(tmpl[last:loc[0]]))
		b.WriteString(`([^/]+)`)
		names = append(names, tmpl[loc[2]:loc[3]])
		last = loc[1]
	}
	b.WriteString(regexp.QuoteMeta(tmpl[last:]))
	b.WriteString("$")
	re, err := regexp.Compile(b.String())
	if err != nil {
		return nil, nil, err
	}
	return re, names, nil
}

// URL is the base URL of the loopback server.
func (m *Mock) URL() string { return m.url }

// Close shuts the server down.
func (m *Mock) Close() {
	_ = m.listen.Close()
	_ = m.srv.Close()
}

// Requests returns a copy of the request log, in arrival order.
func (m *Mock) Requests() []RecordedRequest {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]RecordedRequest, len(m.log))
	copy(out, m.log)
	return out
}

func (m *Mock) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	_ = r.Body.Close()

	var matched *mockRoute
	var params map[string]string
	pathKnown := false

	for i := range m.routes {
		rt := &m.routes[i]
		sub := rt.re.FindStringSubmatch(r.URL.Path)
		if sub == nil {
			continue
		}
		pathKnown = true
		if rt.op.Method != r.Method {
			continue
		}
		params = map[string]string{}
		for j, n := range rt.names {
			params[n] = sub[j+1]
		}
		matched = rt
		break
	}

	opID := ""
	if matched != nil {
		opID = matched.op.ID
	}
	m.mu.Lock()
	m.log = append(m.log, RecordedRequest{
		OperationID: opID,
		Method:      r.Method,
		Path:        r.URL.Path,
		RawQuery:    r.URL.RawQuery,
		Header:      r.Header.Clone(),
		Body:        body,
	})
	m.mu.Unlock()

	switch {
	case matched != nil:
		m.dispatch(w, matched.op, params, body)
	case pathKnown:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed for this path")
	default:
		writeErr(w, http.StatusNotFound, "no such operation in contract")
	}
}

func (m *Mock) dispatch(w http.ResponseWriter, op Operation, params map[string]string, body []byte) {
	switch op.ID {
	case "GetDeployment":
		if params["deploymentId"] != m.cfg.DeploymentID {
			writeErr(w, http.StatusNotFound, "deployment not found")
			return
		}
		writeJSON(w, http.StatusOK, m.deployment())

	case "PatchDeployment":
		if params["deploymentId"] != m.cfg.DeploymentID {
			writeErr(w, http.StatusNotFound, "deployment not found")
			return
		}
		var upd struct {
			Name        *string `json:"name"`
			Description *string `json:"description"`
			IconID      *string `json:"iconId"`
		}
		if err := json.Unmarshal(body, &upd); err != nil {
			writeErr(w, http.StatusBadRequest, "malformed body")
			return
		}
		m.mu.Lock()
		if upd.Name != nil {
			m.name = *upd.Name
		}
		if upd.Description != nil {
			m.descr = *upd.Description
		}
		if upd.IconID != nil {
			m.iconID = *upd.IconID
		}
		m.mu.Unlock()
		writeJSON(w, http.StatusOK, m.deployment())

	case "GetDeploymentActions":
		if params["deploymentId"] != m.cfg.DeploymentID {
			writeErr(w, http.StatusNotFound, "deployment not found")
			return
		}
		writeJSON(w, http.StatusOK, m.actions())

	case "GetDeploymentResources":
		if params["deploymentId"] != m.cfg.DeploymentID {
			writeErr(w, http.StatusNotFound, "deployment not found")
			return
		}
		writeJSON(w, http.StatusOK, m.resourcePage())

	case "SubmitDeploymentActionRequest", "SubmitResourceActionRequest":
		if params["deploymentId"] != m.cfg.DeploymentID {
			writeErr(w, http.StatusNotFound, "deployment not found")
			return
		}
		if op.ID == "SubmitResourceActionRequest" && m.cfg.ResourceID != "" && params["resourceId"] != m.cfg.ResourceID {
			writeErr(w, http.StatusNotFound, "resource not found")
			return
		}
		var req struct {
			ActionID string `json:"actionId"`
		}
		if err := json.Unmarshal(body, &req); err != nil {
			writeErr(w, http.StatusBadRequest, "malformed body")
			return
		}
		if req.ActionID == "" {
			writeErr(w, http.StatusBadRequest, "actionId is required to select an action")
			return
		}
		m.mu.Lock()
		m.nextID++
		st := &mockRequestState{id: fmt.Sprintf("req-%d", m.nextID), actionID: req.ActionID}
		m.requests[st.id] = st
		m.mu.Unlock()
		writeJSON(w, http.StatusOK, m.requestBody(st, "INPROGRESS", ""))

	case "GetRequest":
		m.mu.Lock()
		st, ok := m.requests[params["requestId"]]
		if !ok {
			m.mu.Unlock()
			writeErr(w, http.StatusNotFound, "request not found")
			return
		}
		st.polls++
		polls := st.polls
		action := st.actionID
		m.mu.Unlock()

		if polls <= m.cfg.PollsBeforeTerminal {
			writeJSON(w, http.StatusOK, m.requestBody(st, "INPROGRESS", ""))
			return
		}
		status := "SUCCESSFUL"
		if s, ok := m.cfg.ActionResult[action]; ok && s != "" {
			status = s
		}
		detail := ""
		if status != "SUCCESSFUL" {
			detail = m.cfg.ActionDetails[action]
			if detail == "" {
				detail = "request did not complete successfully"
			}
		}
		writeJSON(w, http.StatusOK, m.requestBody(st, status, detail))

	default:
		writeErr(w, http.StatusNotImplemented, "operation not implemented by mock")
	}
}

func (m *Mock) deployment() map[string]any {
	m.mu.Lock()
	defer m.mu.Unlock()
	d := map[string]any{
		"id":          m.cfg.DeploymentID,
		"name":        m.name,
		"description": m.descr,
	}
	if m.iconID != "" {
		d["iconId"] = m.iconID
	}
	return d
}

func (m *Mock) actions() []map[string]any {
	ids := map[string]bool{}
	for id := range m.cfg.ActionResult {
		ids[id] = true
	}
	for id := range m.cfg.ActionDetails {
		ids[id] = true
	}
	ordered := make([]string, 0, len(ids))
	for id := range ids {
		ordered = append(ordered, id)
	}
	sort.Strings(ordered)
	actions := make([]map[string]any, 0, len(ordered))
	for _, id := range ordered {
		actions = append(actions, map[string]any{"id": id})
	}
	return actions
}

func (m *Mock) resourcePage() map[string]any {
	res := []map[string]any{}
	if m.cfg.ResourceID != "" {
		res = append(res, map[string]any{
			"id":   m.cfg.ResourceID,
			"name": m.cfg.ResourceID,
		})
	}
	return map[string]any{
		"content":          res,
		"empty":            len(res) == 0,
		"first":            true,
		"last":             true,
		"number":           0,
		"numberOfElements": len(res),
		"size":             20,
		"totalElements":    len(res),
		"totalPages":       1,
	}
}

func (m *Mock) requestBody(st *mockRequestState, status, detail string) map[string]any {
	return map[string]any{
		"id":           st.id,
		"actionId":     st.actionID,
		"deploymentId": m.cfg.DeploymentID,
		"status":       status,
		"details":      detail,
	}
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, map[string]any{"message": msg, "statusCode": code})
}
