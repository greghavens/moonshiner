// Package contractmock serves a loopback stand-in for a VMware Cloud Foundation
// 9.0 SDDC Manager appliance.
//
// The route table is loaded from docs/contract.json at startup, so the mock can
// only ever answer the four operations that contract names. Anything else is
// refused and recorded as a contract violation. Every request is appended to a
// mutex-guarded log that a test can read with Requests.
//
// Nothing here contacts a live VMware endpoint. The listener is bound to an
// ephemeral 127.0.0.1 port.
package contractmock

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"testing"
)

// Fixture identifiers handed back by the mock. Tests assert against these, and
// the client under test must thread them through instead of inventing its own.
const (
	AccessToken = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.sddc-manager-9-0-fixture"

	// The identifiers deliberately contain reserved path characters. The client
	// must carry each value as one percent-escaped path segment while retaining
	// the original value in request bodies and reports.
	NetworkPoolID    = "pool/3f2a?west"
	VMotionNetworkID = "d1c4b8e2-7f36-4a90-b2d8-5e6f7a8b9c01"
	VsanNetworkID    = "network/9c4e#vsan"
	TaskID           = "task/7b2c?run"

	HostA = "esx-a07.vcf.local"
	HostB = "esx-a08.vcf.local"

	HostBErrorCode    = "ESX_SSL_THUMBPRINT_MISMATCH"
	HostBErrorMessage = "The SSL thumbprint presented by esx-a08.vcf.local does not match the thumbprint in the commission spec."

	TaskFailureErrorCode = "HOST_COMMISSION_PARTIAL_FAILURE"
	TaskFailureMessage   = "Commissioning failed for 1 of 2 hosts. Successfully commissioned hosts remain in the inventory."

	IPPoolRejectedErrorCode      = "NETWORK_POOL_IP_RANGE_OVERLAP"
	IPPoolRejectedMessage        = "The IP range 172.20.32.20-172.20.32.60 overlaps an existing range in the selected network."
	NetworkPoolRejectedErrorCode = "NETWORK_POOL_NAME_ALREADY_EXISTS"
	NetworkPoolRejectedMessage   = "A network pool named np-ops-a01 already exists."
)

// Scenario selects which step of the onboarding sequence fails.
type Scenario int

const (
	// ScenarioAllSucceed commissions both hosts and finishes SUCCESSFUL.
	ScenarioAllSucceed Scenario = iota
	// ScenarioCommissionTaskFails accepts the commission with HTTP 202 and then
	// settles the task as FAILED, with one host commissioned and one not.
	ScenarioCommissionTaskFails
	// ScenarioIPPoolRejected rejects addIpPoolToNetworkOfNetworkPool with 400
	// after the network pool has already been created.
	ScenarioIPPoolRejected
	// ScenarioNetworkPoolRejected rejects the very first step with 400.
	ScenarioNetworkPoolRejected
)

// Request is one logged inbound HTTP request.
type Request struct {
	// OperationID is the contract operationId this request matched, or "" when
	// the request did not match any operation the contract names.
	OperationID string
	Method      string
	// RawTarget is r.RequestURI verbatim, so a stray query string or a bare "?"
	// is visible to the test.
	RawTarget string
	Path      string
	Query     string
	Header    http.Header
	Body      []byte
	// Violation is set when the mock refused the request: an unknown route, a
	// bad method, a missing bearer token, or a body the contract forbids.
	Violation string
}

// Server is a running loopback SDDC Manager stand-in.
type Server struct {
	// URL is the service root, e.g. http://127.0.0.1:39481.
	URL string

	t        *testing.T
	scenario Scenario
	http     *httptest.Server
	client   *http.Client
	routes   []route
	schemas  map[string]schema

	mu       sync.Mutex
	log      []Request
	taskGets int
}

type route struct {
	operationID string
	method      string
	pattern     *regexp.Regexp
}

type schema struct {
	Required                 []string          `json:"required"`
	ReadOnly                 []string          `json:"readOnly"`
	Members                  map[string]member `json:"members"`
	NotMembersInThisRevision []string          `json:"notMembersInThisRevision"`
}

type member struct {
	Type  string `json:"type"`
	Items string `json:"items"`
}

type contractFile struct {
	Operations []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	} `json:"operations"`
	Schemas map[string]schema `json:"schemas"`
}

// Start boots the mock on an ephemeral loopback port when the sandbox permits
// sockets, otherwise with an in-process transport, and registers cleanup.
func Start(t *testing.T, scenario Scenario) *Server {
	t.Helper()

	c := loadContract(t)
	s := &Server{t: t, scenario: scenario, schemas: c.Schemas}

	for _, op := range c.Operations {
		s.routes = append(s.routes, route{
			operationID: op.OperationID,
			method:      op.Method,
			pattern:     pathPattern(op.Path),
		})
		if !handled[op.OperationID] {
			t.Fatalf("contractmock: docs/contract.json names operation %q, which the mock cannot serve", op.OperationID)
		}
	}
	if len(s.routes) == 0 {
		t.Fatal("contractmock: docs/contract.json names no operations")
	}

	handler := http.HandlerFunc(s.serve)
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err == nil {
		s.http = httptest.NewUnstartedServer(handler)
		s.http.Listener = listener
		s.http.Start()
		t.Cleanup(s.http.Close)
		s.URL = s.http.URL
		s.client = s.http.Client()
		return s
	}
	if !errors.Is(err, syscall.EPERM) && !errors.Is(err, syscall.EACCES) {
		if errors.Is(err, syscall.EAFNOSUPPORT) || errors.Is(err, syscall.EADDRNOTAVAIL) {
			return s.startInProcess(handler)
		}
		t.Fatalf("contractmock: bind 127.0.0.1:0: %v", err)
	}
	return s.startInProcess(handler)
}

func (s *Server) startInProcess(handler http.Handler) *Server {
	// Some verification sandboxes forbid AF_INET even for loopback. Keep the
	// identical handler and request/response path in process in that case. The
	// implementation under test still performs genuine http.Client.Do calls;
	// only the transport boundary changes.
	s.URL = "http://127.0.0.1"
	s.client = &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
		select {
		case <-req.Context().Done():
			return nil, req.Context().Err()
		default:
		}
		copy := req.Clone(req.Context())
		copy.RequestURI = req.URL.RequestURI()
		if copy.Body == nil {
			copy.Body = http.NoBody
		}
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, copy)
		response := recorder.Result()
		response.Request = req
		return response, nil
	})}
	return s
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

// Client returns the transport that reaches this mock in the current sandbox.
func (s *Server) Client() *http.Client { return s.client }

var handled = map[string]bool{
	"createNetworkPool":               true,
	"addIpPoolToNetworkOfNetworkPool": true,
	"commissionHosts":                 true,
	"getTask":                         true,
}

func loadContract(t *testing.T) contractFile {
	t.Helper()
	_, self, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("contractmock: cannot locate package source")
	}
	path := filepath.Join(filepath.Dir(self), "..", "..", "docs", "contract.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("contractmock: read %s: %v", path, err)
	}
	var c contractFile
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatalf("contractmock: parse %s: %v", path, err)
	}
	return c
}

// pathPattern turns "/v1/network-pools/{id}/networks/{networkId}/ip-pools"
// into an anchored regexp with one capture per template parameter.
func pathPattern(template string) *regexp.Regexp {
	var b strings.Builder
	b.WriteString("^")
	for _, seg := range strings.Split(strings.TrimPrefix(template, "/"), "/") {
		b.WriteString("/")
		if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
			b.WriteString("([^/]+)")
			continue
		}
		b.WriteString(regexp.QuoteMeta(seg))
	}
	b.WriteString("$")
	return regexp.MustCompile(b.String())
}

// Requests returns a copy of the request log in arrival order.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.log))
	copy(out, s.log)
	return out
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	var body []byte
	if r.Body != nil {
		body, _ = io.ReadAll(r.Body)
		_ = r.Body.Close()
	}

	entry := Request{
		Method:    r.Method,
		RawTarget: r.RequestURI,
		Path:      r.URL.Path,
		Query:     r.URL.RawQuery,
		Header:    r.Header.Clone(),
		Body:      body,
	}

	// Match the escaped path so an encoded slash remains inside one template
	// segment. Captures are unescaped before handlers compare them with ids.
	opID, params, pathKnown := s.match(r.Method, r.URL.EscapedPath())
	entry.OperationID = opID

	switch {
	case opID == "" && pathKnown:
		s.refuse(w, &entry, http.StatusMethodNotAllowed, "CONTRACT_METHOD_NOT_ALLOWED",
			fmt.Sprintf("%s is not the method the contract names for %s", r.Method, r.URL.Path))
		return
	case opID == "":
		s.refuse(w, &entry, http.StatusNotFound, "CONTRACT_UNKNOWN_ROUTE",
			fmt.Sprintf("%s %s is not an operation named by docs/contract.json", r.Method, r.URL.Path))
		return
	}

	if got := r.Header.Values("Authorization"); len(got) != 1 || got[0] != "Bearer "+AccessToken {
		s.refuse(w, &entry, http.StatusUnauthorized, "CONTRACT_BAD_AUTHORIZATION",
			"expected exactly one Authorization: Bearer header carrying the fixture access token")
		return
	}

	if code, msg, ok := s.checkBody(opID, body); !ok {
		s.refuse(w, &entry, http.StatusBadRequest, code, msg)
		return
	}

	s.record(entry)

	switch opID {
	case "createNetworkPool":
		s.createNetworkPool(w)
	case "addIpPoolToNetworkOfNetworkPool":
		s.addIPPool(w, params)
	case "commissionHosts":
		s.commissionHosts(w)
	case "getTask":
		s.getTask(w, params)
	}
}

func (s *Server) match(method, path string) (opID string, params []string, pathKnown bool) {
	for _, rt := range s.routes {
		m := rt.pattern.FindStringSubmatch(path)
		if m == nil {
			continue
		}
		pathKnown = true
		if rt.method == method {
			params = make([]string, 0, len(m)-1)
			for _, encoded := range m[1:] {
				value, err := url.PathUnescape(encoded)
				if err != nil {
					return "", nil, false
				}
				params = append(params, value)
			}
			return rt.operationID, params, true
		}
	}
	return "", nil, pathKnown
}

func (s *Server) record(entry Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log = append(s.log, entry)
}

func (s *Server) refuse(w http.ResponseWriter, entry *Request, status int, code, message string) {
	entry.Violation = code + ": " + message
	s.record(*entry)
	writeError(w, status, code, message)
}

// checkBody validates a request body against the contract schema projection.
// It is what pins the mock to docs/contract.json rather than to a hand-written
// idea of the wire format.
func (s *Server) checkBody(opID string, body []byte) (code, message string, ok bool) {
	switch opID {
	case "getTask":
		if len(body) != 0 {
			return "CONTRACT_UNEXPECTED_BODY", "getTask is a bodyless GET", false
		}
		return "", "", true

	case "commissionHosts":
		// The 9.0.0.0 request body is a bare array, not an object wrapping one.
		var specs []map[string]any
		if err := json.Unmarshal(body, &specs); err != nil {
			return "CONTRACT_BODY_NOT_ARRAY",
				"commissionHosts takes a bare JSON array of HostCommissionSpec as its top-level body", false
		}
		if len(specs) == 0 {
			return "CONTRACT_EMPTY_ARRAY", "commissionHosts requires at least one HostCommissionSpec", false
		}
		for i, spec := range specs {
			if c, m, good := s.checkObject("HostCommissionSpec", fmt.Sprintf("HostCommissionSpec[%d]", i), spec); !good {
				return c, m, false
			}
		}
		return "", "", true

	case "createNetworkPool":
		return s.checkTopLevelObject("NetworkPool", body)

	case "addIpPoolToNetworkOfNetworkPool":
		return s.checkTopLevelObject("IpPool", body)
	}
	return "", "", true
}

func (s *Server) checkTopLevelObject(schemaName string, body []byte) (string, string, bool) {
	var obj map[string]any
	if err := json.Unmarshal(body, &obj); err != nil {
		return "CONTRACT_BODY_NOT_OBJECT",
			fmt.Sprintf("%s takes a JSON object as its top-level body", schemaName), false
	}
	return s.checkObject(schemaName, schemaName, obj)
}

// checkObject validates obj against the named contract schema. path is the
// dotted location of obj in the request body, used only for diagnostics.
func (s *Server) checkObject(schemaName, path string, obj map[string]any) (string, string, bool) {
	sc, known := s.schemas[schemaName]
	if !known {
		return "", "", true
	}
	at := func(name string) string { return path + "." + name }

	for _, name := range sc.NotMembersInThisRevision {
		if _, present := obj[name]; present {
			return "CONTRACT_WRONG_SPEC_REVISION", fmt.Sprintf(
				"%s exists only in the 9.1.0.0 revision of the specification; this contract is pinned to 9.0.0.0",
				at(name)), false
		}
	}
	for _, name := range sc.ReadOnly {
		if _, present := obj[name]; present {
			return "CONTRACT_READONLY_MEMBER", fmt.Sprintf("%s is readOnly and must never be sent in a request", at(name)), false
		}
	}
	for _, name := range sc.Required {
		v, present := obj[name]
		if !present {
			return "CONTRACT_MISSING_MEMBER", fmt.Sprintf("%s is required", at(name)), false
		}
		if str, isStr := v.(string); isStr && strings.TrimSpace(str) == "" {
			return "CONTRACT_BLANK_MEMBER", fmt.Sprintf("%s is required and must not be blank", at(name)), false
		}
	}

	for name, value := range obj {
		m, known := sc.Members[name]
		if !known {
			return "CONTRACT_UNKNOWN_MEMBER", fmt.Sprintf("%s is not a member of %s in this revision", at(name), schemaName), false
		}
		if value == nil {
			return "CONTRACT_NULL_MEMBER", fmt.Sprintf(
				"%s is unset, so it must be absent from the encoded object rather than sent as null", at(name)), false
		}
		if str, isStr := value.(string); isStr && str == "" {
			return "CONTRACT_EMPTY_MEMBER", fmt.Sprintf(
				"%s is unset, so it must be absent from the encoded object rather than sent as an empty string", at(name)), false
		}
		if m.Type == "array" && m.Items != "" {
			items, isArr := value.([]any)
			if !isArr {
				return "CONTRACT_NOT_ARRAY", fmt.Sprintf("%s must be an array", at(name)), false
			}
			for i, item := range items {
				child, isObj := item.(map[string]any)
				if !isObj {
					continue
				}
				if c, msg, good := s.checkObject(m.Items, fmt.Sprintf("%s.%s[%d]", path, name, i), child); !good {
					return c, msg, false
				}
			}
		}
	}
	return "", "", true
}

func (s *Server) createNetworkPool(w http.ResponseWriter) {
	if s.scenario == ScenarioNetworkPoolRejected {
		writeError(w, http.StatusBadRequest, NetworkPoolRejectedErrorCode, NetworkPoolRejectedMessage)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{
		"id":         NetworkPoolID,
		"name":       "np-ops-a01",
		"hostsCount": 0,
		"networks": []any{
			map[string]any{
				"id": VsanNetworkID, "type": "VSAN", "vlanId": 1632, "mtu": 9000,
				"subnet": "172.20.32.0", "mask": "255.255.255.0", "gateway": "172.20.32.1",
				"freeIps": []any{}, "usedIps": []any{},
			},
			map[string]any{
				"id": VMotionNetworkID, "type": "VMOTION", "vlanId": 1631, "mtu": 9000,
				"subnet": "172.20.31.0", "mask": "255.255.255.0", "gateway": "172.20.31.1",
				"freeIps": []any{}, "usedIps": []any{},
			},
		},
	})
}

func (s *Server) addIPPool(w http.ResponseWriter, params []string) {
	if s.scenario == ScenarioIPPoolRejected {
		writeError(w, http.StatusBadRequest, IPPoolRejectedErrorCode, IPPoolRejectedMessage)
		return
	}
	if len(params) != 2 || params[0] != NetworkPoolID {
		writeError(w, http.StatusNotFound, "NETWORK_POOL_NOT_FOUND", "unknown network pool id")
		return
	}
	if params[1] != VsanNetworkID {
		writeError(w, http.StatusNotFound, "NETWORK_NOT_FOUND",
			fmt.Sprintf("network %s is not the VSAN network of pool %s", params[1], params[0]))
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"id": VsanNetworkID, "type": "VSAN", "vlanId": 1632, "mtu": 9000,
		"subnet": "172.20.32.0", "mask": "255.255.255.0", "gateway": "172.20.32.1",
		"ipPools": []any{map[string]any{"start": "172.20.32.20", "end": "172.20.32.60"}},
		"freeIps": []any{"172.20.32.20"}, "usedIps": []any{},
	})
}

func (s *Server) commissionHosts(w http.ResponseWriter) {
	writeJSON(w, http.StatusAccepted, map[string]any{
		"id":                TaskID,
		"name":              "Commissioning Hosts",
		"type":              "HOST_COMMISSION",
		"status":            "  in   progress  ",
		"creationTimestamp": "2025-06-17T09:14:02.331Z",
	})
}

// getTask reports IN_PROGRESS once and settles on the second and later reads,
// so a client that treats the HTTP 202 as the outcome is visibly wrong.
func (s *Server) getTask(w http.ResponseWriter, params []string) {
	if len(params) != 1 || params[0] != TaskID {
		writeError(w, http.StatusNotFound, "TASK_NOT_FOUND", "unknown task id")
		return
	}

	s.mu.Lock()
	s.taskGets++
	n := s.taskGets
	s.mu.Unlock()

	if n < 2 {
		writeJSON(w, http.StatusOK, map[string]any{
			"id":                TaskID,
			"name":              "Commissioning Hosts",
			"type":              "HOST_COMMISSION",
			"status":            "\tqueued  ",
			"creationTimestamp": "2025-06-17T09:14:02.331Z",
		})
		return
	}

	hostASubTask := map[string]any{
		"name":                "Commission host " + HostA,
		"description":         "Add " + HostA + " to the SDDC Manager inventory",
		"status":              " successful ",
		"creationTimestamp":   "2025-06-17T09:14:03.002Z",
		"completionTimestamp": "2025-06-17T09:16:41.884Z",
		"resources": []any{map[string]any{
			"resourceId": "b8f1d2a3-4c56-4789-9abc-0d1e2f3a4b5c", "type": "ESXI", "fqdn": HostA, "name": HostA,
		}},
	}

	if s.scenario == ScenarioAllSucceed {
		hostBSubTask := map[string]any{
			"name":                "Commission host " + HostB,
			"description":         "Add " + HostB + " to the SDDC Manager inventory",
			"status":              "SUCCESSFUL",
			"creationTimestamp":   "2025-06-17T09:14:03.010Z",
			"completionTimestamp": "2025-06-17T09:16:52.117Z",
			"resources": []any{map[string]any{
				"resourceId": "c9a2e3b4-5d67-489a-8bcd-1e2f3a4b5c6d", "type": "ESXI", "fqdn": HostB, "name": HostB,
			}},
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"id":                  TaskID,
			"name":                "Commissioning Hosts",
			"type":                "HOST_COMMISSION",
			"status":              "SUCCESSFUL",
			"creationTimestamp":   "2025-06-17T09:14:02.331Z",
			"completionTimestamp": "2025-06-17T09:16:53.006Z",
			"subTasks":            []any{hostASubTask, hostBSubTask},
		})
		return
	}

	hostBSubTask := map[string]any{
		"name":                "Commission host " + HostB,
		"description":         "Add " + HostB + " to the SDDC Manager inventory",
		"status":              " failed ",
		"creationTimestamp":   "2025-06-17T09:14:03.010Z",
		"completionTimestamp": "2025-06-17T09:15:08.442Z",
		"errors": []any{map[string]any{
			"errorCode": HostBErrorCode,
			"errorType": "VALIDATION_FAILED",
			"message":   HostBErrorMessage,
		}},
		"resources": []any{map[string]any{
			"resourceId": "c9a2e3b4-5d67-489a-8bcd-1e2f3a4b5c6d", "type": "ESXI", "fqdn": HostB, "name": HostB,
		}},
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"id":                  TaskID,
		"name":                "Commissioning Hosts",
		"type":                "HOST_COMMISSION",
		"status":              "FAILED",
		"creationTimestamp":   "2025-06-17T09:14:02.331Z",
		"completionTimestamp": "2025-06-17T09:15:09.771Z",
		"subTasks":            []any{hostASubTask, hostBSubTask},
		"errors": []any{map[string]any{
			"errorCode": TaskFailureErrorCode,
			"errorType": "LOGICAL",
			"message":   TaskFailureMessage,
		}},
	})
}

func writeJSON(w http.ResponseWriter, status int, body map[string]any) {
	raw, err := json.Marshal(body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(raw)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]any{
		"errorCode": code,
		"errorType": "VALIDATION_FAILED",
		"message":   message,
	})
}
