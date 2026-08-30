// Package mockvc is a loopback stand-in for the two vSphere Automation API
// operations named in docs/contract.json:
//
//	Vcenter.Ovf.LibraryItem_deploy   POST /api/vcenter/ovf/library-item/{ovfLibraryItemId}?action=deploy
//	Vcenter.VM_list                  GET  /api/vcenter/vm
//
// Nothing else is served. Any other method, path or action is answered with the
// vAPI NotFound envelope and recorded in the request log, so a client that
// wanders off the pinned contract fails loudly instead of silently passing.
//
// The mock is pinned to the contract on the request side too: it rejects
// unknown body properties, camelCase spellings, null values and empty-string
// values for optional properties, exactly as the 9.0.0.0 schemas require.
//
// It listens on 127.0.0.1 via httptest and contacts nothing.
package mockvc

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"testing"
)

// Operation ids this mock serves, spelled exactly as in the 9.0.0.0 spec.
const (
	OpDeploy  = "Vcenter.Ovf.LibraryItem_deploy"
	OpListVMs = "Vcenter.VM_list"
)

const (
	basePath      = "/api"
	deployPrefix  = basePath + "/vcenter/ovf/library-item/"
	listVMsPath   = basePath + "/vcenter/vm"
	sessionHeader = "vmware-api-session-id"
	tokenHeader   = "Client-Token"
)

// uuidRe is the lexical form the spec demands of a Client-Token: "if the
// clientToken does not conform to the UUID format" -> 400 InvalidArgument.
var uuidRe = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

// Fault is a condition injected into one non-replayed deploy attempt.
type Fault string

const (
	// FaultNone deploys normally and answers 200.
	FaultNone Fault = ""
	// FaultDropAfterCommit performs the deployment, records it against the
	// Client-Token, and then closes the connection without writing a
	// response. This is the exact situation the Client-Token header exists
	// for: the effect happened, the client never saw the answer.
	FaultDropAfterCommit Fault = "drop_after_commit"
	// FaultServiceUnavailable answers 503 ServiceUnavailable and deploys nothing.
	FaultServiceUnavailable Fault = "service_unavailable"
	// FaultInvalidArgument answers 400 InvalidArgument and deploys nothing.
	FaultInvalidArgument Fault = "invalid_argument"
	// FaultUnauthorized answers 403 Unauthorized and deploys nothing.
	FaultUnauthorized Fault = "unauthorized"
	// FaultNotFound answers 404 NotFound and deploys nothing.
	FaultNotFound Fault = "not_found"
	// FaultResourceInaccessible answers 500 ResourceInaccessible and deploys nothing.
	FaultResourceInaccessible Fault = "resource_inaccessible"
	// FaultDeployFailed answers 200 with succeeded=false and an OVF error.
	// The deploy ran and failed; no virtual machine is created.
	FaultDeployFailed Fault = "deploy_failed"
)

// Config configures a mock vCenter.
type Config struct {
	// SessionID is the value the vmware-api-session-id header must carry.
	SessionID string
	// LibraryItemID is the only content library item that can be deployed.
	LibraryItemID string
	// DefaultVMName names the deployed VM when deployment_spec.name is
	// omitted, standing in for the name inside the OVF package.
	DefaultVMName string
	// DeployFaults are applied in order to deploy attempts that are not
	// answered from the token ledger. Attempts past the end of the slice
	// behave as FaultNone.
	DeployFaults []Fault
}

// Request is one recorded HTTP request.
type Request struct {
	// Op is the operation id the request routed to, or "" when the request
	// did not match any operation this mock serves.
	Op       string
	Method   string
	Path     string
	RawQuery string
	Header   http.Header
	Body     []byte
	// Token is the Client-Token the request carried ("" when absent).
	Token string
	// Status is the HTTP status written, or 0 when the connection was
	// dropped without a response.
	Status int
	// Outcome is a short label: "ok", "replayed", "dropped", "failed",
	// "rejected" or "not_served".
	Outcome string
}

// VM is a virtual machine in the mock inventory.
type VM struct {
	ID   string
	Name string
}

type commit struct {
	body []byte
}

// Server is a running mock vCenter.
type Server struct {
	cfg Config
	hs  *httptest.Server

	mu       sync.Mutex
	log      []Request
	vms      []VM
	ledger   map[string]commit
	attempts int
	tokenSeq int
}

// New starts a mock vCenter on the loopback interface and registers its
// shutdown with t.
func New(t *testing.T, cfg Config) *Server {
	t.Helper()
	if cfg.DefaultVMName == "" {
		cfg.DefaultVMName = "ovf-default-name"
	}
	s := &Server{cfg: cfg, ledger: map[string]commit{}}
	s.hs = httptest.NewServer(http.HandlerFunc(s.handle))
	// Every attempt gets a fresh connection, so net/http never silently
	// replays a request of its own accord on a reused, half-dead one.
	s.hs.Config.SetKeepAlivesEnabled(false)
	t.Cleanup(s.hs.Close)
	return s
}

// URL is the base URL of the mock, without the /api path prefix.
func (s *Server) URL() string { return s.hs.URL }

// Log returns a copy of every request the mock has received, in order.
func (s *Server) Log() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.log))
	copy(out, s.log)
	return out
}

// Requests returns a copy of the recorded requests that routed to op.
func (s *Server) Requests(op string) []Request {
	var out []Request
	for _, r := range s.Log() {
		if r.Op == op {
			out = append(out, r)
		}
	}
	return out
}

// VMs returns a copy of the mock inventory in creation order.
func (s *Server) VMs() []VM {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]VM, len(s.vms))
	copy(out, s.vms)
	return out
}

func (s *Server) handle(w http.ResponseWriter, r *http.Request) {
	body := readAll(r)
	rec := &Request{
		Method:   r.Method,
		Path:     r.URL.Path,
		RawQuery: r.URL.RawQuery,
		Header:   r.Header.Clone(),
		Body:     body,
		Token:    r.Header.Get(tokenHeader),
	}
	defer func() {
		s.mu.Lock()
		s.log = append(s.log, *rec)
		s.mu.Unlock()
	}()

	switch {
	case r.Method == http.MethodPost &&
		strings.HasPrefix(r.URL.Path, deployPrefix) &&
		r.URL.Query().Get("action") == "deploy":
		rec.Op = OpDeploy
		s.deploy(w, r, rec)
	case r.Method == http.MethodGet && r.URL.Path == listVMsPath:
		rec.Op = OpListVMs
		s.listVMs(w, r, rec)
	default:
		rec.Outcome = "not_served"
		writeError(w, rec, http.StatusNotFound, "NOT_FOUND",
			fmt.Sprintf("this mock serves only %s and %s; %s %s?%s matches neither",
				OpDeploy, OpListVMs, r.Method, r.URL.Path, r.URL.RawQuery))
	}
}

func (s *Server) deploy(w http.ResponseWriter, r *http.Request, rec *Request) {
	if !s.authorized(w, r, rec) {
		return
	}
	itemID := strings.TrimPrefix(r.URL.Path, deployPrefix)
	if itemID == "" || strings.Contains(itemID, "/") || itemID != s.cfg.LibraryItemID {
		reject(w, rec, http.StatusNotFound, "NOT_FOUND",
			fmt.Sprintf("library item %q does not exist", itemID))
		return
	}
	if got := r.URL.Query()["action"]; len(got) != 1 || r.URL.Query().Encode() != "action=deploy" {
		reject(w, rec, http.StatusBadRequest, "INVALID_ARGUMENT",
			fmt.Sprintf("unexpected query string %q; expected exactly action=deploy", r.URL.RawQuery))
		return
	}
	if ct := r.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		reject(w, rec, http.StatusBadRequest, "INVALID_ARGUMENT",
			fmt.Sprintf("unsupported Content-Type %q", ct))
		return
	}

	token := rec.Token
	if token == "" {
		s.mu.Lock()
		s.tokenSeq++
		token = fmt.Sprintf("00000000-0000-4000-8000-%012d", s.tokenSeq)
		s.mu.Unlock()
	} else if !uuidRe.MatchString(token) {
		reject(w, rec, http.StatusBadRequest, "INVALID_ARGUMENT",
			fmt.Sprintf("the clientToken %q does not conform to the UUID format", token))
		return
	}

	s.mu.Lock()
	if c, ok := s.ledger[token]; ok {
		s.mu.Unlock()
		rec.Outcome = "replayed"
		writeJSON(w, rec, http.StatusOK, c.body)
		return
	}
	s.mu.Unlock()

	name, msg := parseDeployBody(rec.Body, s.cfg.DefaultVMName)
	if msg != "" {
		reject(w, rec, http.StatusBadRequest, "INVALID_ARGUMENT", msg)
		return
	}

	s.mu.Lock()
	fault := FaultNone
	if s.attempts < len(s.cfg.DeployFaults) {
		fault = s.cfg.DeployFaults[s.attempts]
	}
	s.attempts++
	s.mu.Unlock()

	switch fault {
	case FaultServiceUnavailable:
		reject(w, rec, http.StatusServiceUnavailable, "SERVICE_UNAVAILABLE",
			"the service is not available; the server is too busy")
		return
	case FaultInvalidArgument:
		reject(w, rec, http.StatusBadRequest, "INVALID_ARGUMENT",
			"deploymentSpec contains invalid arguments")
		return
	case FaultUnauthorized:
		reject(w, rec, http.StatusForbidden, "UNAUTHORIZED",
			"the resource pool requires VApp.Import")
		return
	case FaultNotFound:
		reject(w, rec, http.StatusNotFound, "NOT_FOUND",
			"the resource pool referenced by target does not exist")
		return
	case FaultResourceInaccessible:
		reject(w, rec, http.StatusInternalServerError, "RESOURCE_INACCESSIBLE",
			"the content library item is inaccessible")
		return
	case FaultDeployFailed:
		body := mustJSON(map[string]any{
			"succeeded": false,
			"error": map[string]any{
				"errors": []any{map[string]any{
					"category": "INPUT",
					"message": map[string]any{
						"id":              "com.vmware.vcenter.ovf.input_error",
						"default_message": "the OVF package requires a network mapping for section net-0",
						"args":            []any{},
					},
				}},
				"warnings":    []any{},
				"information": []any{},
			},
		})
		s.mu.Lock()
		s.ledger[token] = commit{body: body}
		s.mu.Unlock()
		rec.Outcome = "failed"
		writeJSON(w, rec, http.StatusOK, body)
		return
	}

	// FaultNone and FaultDropAfterCommit both commit the deployment.
	s.mu.Lock()
	vm := VM{ID: fmt.Sprintf("vm-%d", 1001+len(s.vms)), Name: name}
	s.vms = append(s.vms, vm)
	body := mustJSON(map[string]any{
		"succeeded": true,
		"resource_id": map[string]any{
			"type": "VirtualMachine",
			"id":   vm.ID,
		},
	})
	s.ledger[token] = commit{body: body}
	s.mu.Unlock()

	if fault == FaultDropAfterCommit {
		rec.Outcome = "dropped"
		hijackAndClose(w)
		return
	}
	rec.Outcome = "ok"
	writeJSON(w, rec, http.StatusOK, body)
}

func (s *Server) listVMs(w http.ResponseWriter, r *http.Request, rec *Request) {
	if !s.authorized(w, r, rec) {
		return
	}
	q, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		reject(w, rec, http.StatusBadRequest, "INVALID_ARGUMENT", "malformed query string")
		return
	}
	for k := range q {
		if k != "names" {
			reject(w, rec, http.StatusBadRequest, "INVALID_ARGUMENT",
				fmt.Sprintf("unsupported query parameter %q; this mock serves only the names filter", k))
			return
		}
	}
	want := map[string]bool{}
	for _, n := range q["names"] {
		want[n] = true
	}

	out := []any{}
	for _, vm := range s.VMs() {
		if len(want) > 0 && !want[vm.Name] {
			continue
		}
		out = append(out, map[string]any{
			"vm":              vm.ID,
			"name":            vm.Name,
			"power_state":     "POWERED_OFF",
			"cpu_count":       2,
			"memory_size_mib": 4096,
		})
	}
	rec.Outcome = "ok"
	writeJSON(w, rec, http.StatusOK, mustJSON(out))
}

func (s *Server) authorized(w http.ResponseWriter, r *http.Request, rec *Request) bool {
	if r.Header.Get(sessionHeader) != s.cfg.SessionID {
		reject(w, rec, http.StatusUnauthorized, "UNAUTHENTICATED",
			fmt.Sprintf("the %s header is missing or does not identify a session", sessionHeader))
		return false
	}
	return true
}

// deploy9_0Target and deploy9_0Spec are the property sets of the 9.0.0.0
// schemas. Anything outside them is rejected the way vAPI rejects an unknown
// property, which is what catches a body built from the 9.1.0.0 revision.
var (
	deploy9_0Target = map[string]bool{
		"resource_pool_id": true, "host_id": true, "folder_id": true,
	}
	deploy9_0TargetRequired = []string{"resource_pool_id"}

	deploy9_0Spec = map[string]bool{
		"name": true, "annotation": true, "accept_all_eula": true,
		"network_mappings": true, "storage_mappings": true,
		"storage_provisioning": true, "storage_profile_id": true,
		"locale": true, "flags": true, "additional_parameters": true,
		"default_datastore_id": true, "vm_config_spec": true,
	}
	deploy9_0SpecRequired = []string{"accept_all_eula"}
)

// parseDeployBody validates the request body against the 9.0.0.0 schemas and
// returns the name of the VM to create. A non-empty second result is the
// InvalidArgument message to answer with.
func parseDeployBody(body []byte, defaultName string) (string, string) {
	var top map[string]json.RawMessage
	if err := json.Unmarshal(body, &top); err != nil || top == nil {
		return "", fmt.Sprintf("request body is not a JSON object: %v", err)
	}
	for k := range top {
		if k != "target" && k != "deployment_spec" {
			return "", fmt.Sprintf("unknown property %q in the request body; the 9.0.0.0 body has exactly target and deployment_spec", k)
		}
	}
	for _, k := range []string{"target", "deployment_spec"} {
		if _, ok := top[k]; !ok {
			return "", fmt.Sprintf("required property %q is missing from the request body", k)
		}
	}

	target, msg := objectOf(top["target"], "target", deploy9_0Target, deploy9_0TargetRequired)
	if msg != "" {
		return "", msg
	}
	if v, ok := target["resource_pool_id"].(string); !ok || v == "" {
		return "", "target.resource_pool_id must be a non-empty resource pool identifier"
	}

	spec, msg := objectOf(top["deployment_spec"], "deployment_spec", deploy9_0Spec, deploy9_0SpecRequired)
	if msg != "" {
		return "", msg
	}
	if _, ok := spec["accept_all_eula"].(bool); !ok {
		return "", "deployment_spec.accept_all_eula must be a boolean"
	}

	name := defaultName
	if v, ok := spec["name"]; ok {
		s, ok := v.(string)
		if !ok {
			return "", "deployment_spec.name must be a string"
		}
		name = s
	}
	return name, ""
}

func objectOf(raw json.RawMessage, path string, allowed map[string]bool, required []string) (map[string]any, string) {
	var obj map[string]any
	if err := json.Unmarshal(raw, &obj); err != nil || obj == nil {
		return nil, fmt.Sprintf("%s must be a JSON object", path)
	}
	for k, v := range obj {
		if !allowed[k] {
			return nil, fmt.Sprintf("unknown property %q in %s; it is not part of the 9.0.0.0 schema", k, path)
		}
		if v == nil {
			return nil, fmt.Sprintf("%s.%s is null; an unset optional property is omitted, not sent as null", path, k)
		}
		if s, ok := v.(string); ok && s == "" {
			return nil, fmt.Sprintf("%s.%s is an empty string; an unset optional property is omitted, not sent empty", path, k)
		}
		if m, ok := v.(map[string]any); ok && len(m) == 0 {
			return nil, fmt.Sprintf("%s.%s is an empty object; an unset optional property is omitted", path, k)
		}
		if a, ok := v.([]any); ok && len(a) == 0 {
			return nil, fmt.Sprintf("%s.%s is an empty array; an unset optional property is omitted", path, k)
		}
	}
	for _, k := range required {
		if _, ok := obj[k]; !ok {
			return nil, fmt.Sprintf("required property %q is missing from %s", k, path)
		}
	}
	return obj, ""
}

func reject(w http.ResponseWriter, rec *Request, status int, errorType, message string) {
	if rec.Outcome == "" {
		rec.Outcome = "rejected"
	}
	writeError(w, rec, status, errorType, message)
}

func writeError(w http.ResponseWriter, rec *Request, status int, errorType, message string) {
	writeJSON(w, rec, status, mustJSON(map[string]any{
		"error_type": errorType,
		"messages": []any{map[string]any{
			"id":              "com.vmware.vapi.std.errors." + strings.ToLower(errorType),
			"default_message": message,
			"args":            []any{},
		}},
	}))
}

func writeJSON(w http.ResponseWriter, rec *Request, status int, body []byte) {
	rec.Status = status
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

func hijackAndClose(w http.ResponseWriter) {
	h, ok := w.(http.Hijacker)
	if !ok {
		panic("mockvc: ResponseWriter does not support hijacking")
	}
	conn, _, err := h.Hijack()
	if err != nil {
		panic("mockvc: hijack failed: " + err.Error())
	}
	_ = conn.Close()
}

func readAll(r *http.Request) []byte {
	if r.Body == nil {
		return nil
	}
	b, err := io.ReadAll(r.Body)
	if err != nil {
		return b
	}
	return b
}

func mustJSON(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		panic("mockvc: " + err.Error())
	}
	return b
}
