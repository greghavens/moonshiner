// Package contractmock provides the protected contract-pinned loopback service
// used by the acceptance tests.
package contractmock

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"regexp"
	"strings"
	"sync"
)

const (
	OperationNamespaceGet = "getSupervisorNamespace"
	OperationClusterPatch = "patchVksClusterVersion"
)

// Scenario contains runtime-created values used by one isolated mock.
type Scenario struct {
	Namespace          string
	Supervisor         string
	ReportedSupervisor string
	ClusterName        string
	OldVersion         string
	TargetVersion      string
	VCenterSessionID   string
	KubernetesToken    string
	ConfigStatus       string
	FailOperation      string
	FailStatus         int
}

// Request is one deep-copied entry in the synchronized request log.
type Request struct {
	Sequence      int
	Operation     string
	Method        string
	RawTarget     string
	Protocol      string
	Host          string
	Header        http.Header
	Body          []byte
	ContentLength int64
}

// State is a synchronized snapshot of the simulated Cluster and call counts.
type State struct {
	ClusterVersion   string
	PrecheckRequests int
	PatchAttempts    int
}

type route struct {
	name       string
	method     string
	template   string
	pattern    *regexp.Regexp
	parameters []string
}

type contractDocument struct {
	Operations []contractOperation `json:"operations"`
}

type contractOperation struct {
	ContractName string `json:"contractName"`
	OperationID  string `json:"operationId"`
	OperationKey string `json:"operationKey"`
	Method       string `json:"method"`
	PathTemplate string `json:"pathTemplate"`
}

// Server owns the loopback listener, contract-derived allow-list, mutable
// fixture state, and synchronized request log.
type Server struct {
	http      *httptest.Server
	client    *http.Client
	url       string
	authority string
	routes    []route
	scenario  Scenario

	mu                   sync.Mutex
	requests             []Request
	clusterVersion       string
	precheckRequests     int
	clusterPatchAttempts int
}

// New reads contractPath, derives the only allowed routes, and starts a real
// HTTP service on an ephemeral 127.0.0.1 port.
func New(contractPath string, scenario Scenario) (*Server, error) {
	if scenario.Namespace == "" ||
		scenario.Supervisor == "" ||
		scenario.ClusterName == "" ||
		scenario.OldVersion == "" ||
		scenario.TargetVersion == "" ||
		scenario.VCenterSessionID == "" ||
		scenario.KubernetesToken == "" ||
		scenario.ConfigStatus == "" {
		return nil, errors.New("contractmock: incomplete scenario")
	}
	if scenario.FailOperation != "" && scenario.FailStatus < 300 {
		return nil, errors.New("contractmock: failure status must be at least 300")
	}

	raw, err := os.ReadFile(contractPath)
	if err != nil {
		return nil, fmt.Errorf("contractmock: read contract: %w", err)
	}
	var document contractDocument
	if err := json.Unmarshal(raw, &document); err != nil {
		return nil, fmt.Errorf("contractmock: decode contract: %w", err)
	}
	if len(document.Operations) != 2 {
		return nil, fmt.Errorf(
			"contractmock: contract operation count is %d, want 2",
			len(document.Operations),
		)
	}

	expected := map[string]struct {
		method       string
		operationID  string
		operationKey string
	}{
		OperationNamespaceGet: {
			method:      http.MethodGet,
			operationID: "Vcenter.Namespaces.Instances_getV2",
		},
		OperationClusterPatch: {
			method:       http.MethodPatch,
			operationKey: "cluster.x-k8s.io/v1beta2:namespaced-clusters:patch",
		},
	}
	s := &Server{
		scenario:       scenario,
		clusterVersion: scenario.OldVersion,
	}
	seen := make(map[string]bool, len(document.Operations))
	for _, operation := range document.Operations {
		want, ok := expected[operation.ContractName]
		if !ok ||
			seen[operation.ContractName] ||
			operation.Method != want.method ||
			operation.OperationID != want.operationID ||
			operation.OperationKey != want.operationKey ||
			operation.PathTemplate == "" {
			return nil, fmt.Errorf(
				"contractmock: invalid operation identity %q",
				operation.ContractName,
			)
		}
		compiled, compileErr := compileRoute(operation)
		if compileErr != nil {
			return nil, compileErr
		}
		seen[operation.ContractName] = true
		s.routes = append(s.routes, compiled)
	}
	if len(seen) != len(expected) {
		return nil, errors.New("contractmock: required operations are absent")
	}

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		// Some authoring sandboxes prohibit even loopback sockets. Preserve the
		// net/http request boundary with a handler-backed loopback transport in
		// that environment. Normal verifier environments use the real listener.
		s.authority = "127.0.0.1"
		s.url = "http://" + s.authority
		s.client = &http.Client{Transport: handlerTransport{handler: s}}
		return s, nil
	}
	s.authority = listener.Addr().String()
	s.url = "http://" + s.authority
	s.http = &httptest.Server{
		Listener: listener,
		Config:   &http.Server{Handler: s},
	}
	s.http.Start()
	s.client = s.http.Client()
	return s, nil
}

// URL returns the mock origin.
func (s *Server) URL() string {
	return s.url
}

// Client returns an HTTP client configured for this loopback server.
func (s *Server) Client() *http.Client {
	return s.client
}

// Close stops the loopback service.
func (s *Server) Close() {
	if s.http != nil {
		s.http.Close()
	}
}

// Requests returns a deep copy of the complete request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	out := make([]Request, len(s.requests))
	for i, request := range s.requests {
		out[i] = request
		out[i].Header = request.Header.Clone()
		out[i].Body = append([]byte(nil), request.Body...)
	}
	return out
}

// Snapshot returns the current fixture state.
func (s *Server) Snapshot() State {
	s.mu.Lock()
	defer s.mu.Unlock()
	return State{
		ClusterVersion:   s.clusterVersion,
		PrecheckRequests: s.precheckRequests,
		PatchAttempts:    s.clusterPatchAttempts,
	}
}

// ServeHTTP logs every request and dispatches only a route derived from one of
// the two named operations in docs/contract.json.
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	var body []byte
	if r.Body != nil {
		body, _ = io.ReadAll(r.Body)
	}
	operation, parameters := s.match(r.Method, r.URL.EscapedPath())

	s.mu.Lock()
	s.requests = append(s.requests, Request{
		Sequence:      len(s.requests),
		Operation:     operation,
		Method:        r.Method,
		RawTarget:     r.RequestURI,
		Protocol:      r.Proto,
		Host:          r.Host,
		Header:        r.Header.Clone(),
		Body:          append([]byte(nil), body...),
		ContentLength: r.ContentLength,
	})
	switch operation {
	case OperationNamespaceGet:
		s.precheckRequests++
	case OperationClusterPatch:
		s.clusterPatchAttempts++
	}
	s.mu.Unlock()

	switch operation {
	case OperationNamespaceGet:
		s.serveNamespace(w, r, body, parameters)
	case OperationClusterPatch:
		s.serveClusterPatch(w, r, body, parameters)
	default:
		writeJSON(w, http.StatusNotFound, map[string]any{
			"error": "operation_not_in_contract",
		})
	}
}

func (s *Server) serveNamespace(
	w http.ResponseWriter,
	r *http.Request,
	body []byte,
	parameters map[string]string,
) {
	expectedTarget := strings.ReplaceAll(
		s.template(OperationNamespaceGet),
		"{namespace}",
		url.PathEscape(s.scenario.Namespace),
	)
	if r.RequestURI != expectedTarget ||
		parameters["namespace"] != s.scenario.Namespace ||
		len(body) != 0 ||
		r.ContentLength != 0 ||
		!exactHeader(r.Header, "Accept", "application/json") ||
		!exactHeader(
			r.Header,
			"vmware-api-session-id",
			s.scenario.VCenterSessionID,
		) ||
		r.Header.Values("Authorization") != nil ||
		r.Header.Values("Content-Type") != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"error": "namespace_request_wire_mismatch",
		})
		return
	}
	if s.scenario.FailOperation == OperationNamespaceGet {
		writeFailure(
			w,
			s.scenario.FailStatus,
			s.scenario.VCenterSessionID,
		)
		return
	}

	supervisor := s.scenario.ReportedSupervisor
	if supervisor == "" {
		supervisor = s.scenario.Supervisor
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"access_list":   []any{},
		"config_status": s.scenario.ConfigStatus,
		"description":   "runtime fixture",
		"messages":      []any{},
		"stats":         map[string]any{},
		"storage_specs": []any{},
		"supervisor":    supervisor,
	})
}

func (s *Server) serveClusterPatch(
	w http.ResponseWriter,
	r *http.Request,
	body []byte,
	parameters map[string]string,
) {
	expectedTarget := strings.NewReplacer(
		"{namespace}", url.PathEscape(s.scenario.Namespace),
		"{clusterName}", url.PathEscape(s.scenario.ClusterName),
	).Replace(s.template(OperationClusterPatch))
	expectedBody, err := versionPatch(s.scenario.TargetVersion)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{
			"error": "fixture_json_error",
		})
		return
	}
	if r.RequestURI != expectedTarget ||
		parameters["namespace"] != s.scenario.Namespace ||
		parameters["clusterName"] != s.scenario.ClusterName ||
		r.ContentLength != int64(len(expectedBody)) ||
		!bytes.Equal(body, expectedBody) ||
		!exactHeader(r.Header, "Accept", "application/json") ||
		!exactHeader(
			r.Header,
			"Authorization",
			"Bearer "+s.scenario.KubernetesToken,
		) ||
		!exactHeader(
			r.Header,
			"Content-Type",
			"application/merge-patch+json",
		) ||
		r.Header.Values("vmware-api-session-id") != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{
			"error": "cluster_patch_wire_mismatch",
		})
		return
	}
	if s.scenario.ConfigStatus != "RUNNING" {
		writeJSON(w, http.StatusConflict, map[string]any{
			"error": "precheck_gate_bypassed",
		})
		return
	}
	if s.scenario.FailOperation == OperationClusterPatch {
		writeFailure(
			w,
			s.scenario.FailStatus,
			s.scenario.KubernetesToken,
		)
		return
	}

	s.mu.Lock()
	s.clusterVersion = s.scenario.TargetVersion
	s.mu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{
		"apiVersion": "cluster.x-k8s.io/v1beta2",
		"kind":       "Cluster",
		"metadata": map[string]any{
			"name":      s.scenario.ClusterName,
			"namespace": s.scenario.Namespace,
		},
		"spec": map[string]any{
			"topology": map[string]any{
				"version": s.scenario.TargetVersion,
			},
		},
	})
}

func (s *Server) match(
	method string,
	escapedPath string,
) (string, map[string]string) {
	for _, candidate := range s.routes {
		if method != candidate.method {
			continue
		}
		matches := candidate.pattern.FindStringSubmatch(escapedPath)
		if matches == nil {
			continue
		}
		parameters := make(map[string]string, len(candidate.parameters))
		for i, name := range candidate.parameters {
			value, err := url.PathUnescape(matches[i+1])
			if err != nil {
				return "", nil
			}
			parameters[name] = value
		}
		return candidate.name, parameters
	}
	return "", nil
}

func (s *Server) template(name string) string {
	for _, candidate := range s.routes {
		if candidate.name == name {
			return candidate.template
		}
	}
	return ""
}

func compileRoute(operation contractOperation) (route, error) {
	placeholder := regexp.MustCompile(`\{([A-Za-z][A-Za-z0-9_]*)\}`)
	var expression strings.Builder
	parameters := make([]string, 0)
	cursor := 0
	for _, match := range placeholder.FindAllStringSubmatchIndex(
		operation.PathTemplate,
		-1,
	) {
		expression.WriteString(regexp.QuoteMeta(
			operation.PathTemplate[cursor:match[0]],
		))
		expression.WriteString(`([^/]+)`)
		parameters = append(
			parameters,
			operation.PathTemplate[match[2]:match[3]],
		)
		cursor = match[1]
	}
	expression.WriteString(regexp.QuoteMeta(
		operation.PathTemplate[cursor:],
	))
	pattern, err := regexp.Compile("^" + expression.String() + "$")
	if err != nil {
		return route{}, fmt.Errorf(
			"contractmock: compile route %q: %w",
			operation.ContractName,
			err,
		)
	}
	return route{
		name:       operation.ContractName,
		method:     operation.Method,
		template:   operation.PathTemplate,
		pattern:    pattern,
		parameters: parameters,
	}, nil
}

func exactHeader(header http.Header, name string, value string) bool {
	values := header.Values(name)
	return len(values) == 1 && values[0] == value
}

func versionPatch(version string) ([]byte, error) {
	value := struct {
		Spec struct {
			Topology struct {
				Version string `json:"version"`
			} `json:"topology"`
		} `json:"spec"`
	}{}
	value.Spec.Topology.Version = version

	var output bytes.Buffer
	encoder := json.NewEncoder(&output)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(output.Bytes(), []byte{'\n'}), nil
}

func writeFailure(w http.ResponseWriter, status int, secret string) {
	if status >= 300 && status < 400 {
		w.Header().Set("Location", "/operation-not-in-contract")
	}
	writeJSON(w, status, map[string]any{
		"error":  "runtime_failure",
		"secret": secret,
	})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

type handlerTransport struct {
	handler http.Handler
}

func (transport handlerTransport) RoundTrip(
	request *http.Request,
) (*http.Response, error) {
	select {
	case <-request.Context().Done():
		return nil, request.Context().Err()
	default:
	}
	clone := request.Clone(request.Context())
	clone.RequestURI = request.URL.RequestURI()
	clone.Host = request.URL.Host
	clone.Proto = "HTTP/1.1"
	clone.ProtoMajor = 1
	clone.ProtoMinor = 1
	if clone.Header.Get("User-Agent") == "" {
		clone.Header.Set("User-Agent", "Go-http-client/1.1")
	}
	if clone.Header.Get("Accept-Encoding") == "" {
		clone.Header.Set("Accept-Encoding", "gzip")
	}
	recorder := httptest.NewRecorder()
	transport.handler.ServeHTTP(recorder, clone)
	response := recorder.Result()
	response.Request = request
	return response, nil
}
