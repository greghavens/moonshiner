// Package contractmock provides the protected contract-pinned loopback service
// used by the acceptance tests.
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
	"regexp"
	"strconv"
	"strings"
	"sync"
)

const (
	OperationNamespaceList = "vcenter.namespace.listAuthorized"
	OperationClusterList   = "kubernetes.cluster.list"
)

// Resource is one Kubernetes Cluster resource emitted by a Page.
type Resource struct {
	Name            string
	UID             string
	ResourceVersion string
}

// Page is one response in the Kubernetes continuation sequence.
type Page struct {
	Items    []Resource
	Continue string
}

// Scenario contains runtime-created fixture values. The server supplies its
// own loopback authority as master_host.
type Scenario struct {
	Namespace               string
	SessionID               string
	PageLimit               int64
	Pages                   []Page
	MasterHostOverride      string
	CorruptUnrelatedSummary bool
	DuplicateNamespace      bool
	FailOperation           string
	FailStatus              int
}

// Request is a deep-copied request-log entry.
type Request struct {
	Sequence      int
	Operation     string
	Method        string
	RawTarget     string
	Host          string
	Header        http.Header
	Body          []byte
	ContentLength int64
}

type route struct {
	name       string
	method     string
	template   string
	pattern    *regexp.Regexp
	parameters []string
}

// Server owns a loopback listener, contract-derived allow-list, scenario state,
// and synchronized request log.
type Server struct {
	http      *httptest.Server
	client    *http.Client
	url       string
	authority string
	routes    []route
	scenario  Scenario

	mu       sync.Mutex
	requests []Request
	page     int
}

type contractDocument struct {
	OpenAPIProjection struct {
		Operations []contractOperation `json:"operations"`
	} `json:"openapiProjection"`
	KubernetesAPI struct {
		Operations []contractOperation `json:"operations"`
	} `json:"kubernetesApi"`
}

type contractOperation struct {
	Name   string `json:"name"`
	Method string `json:"method"`
	Path   string `json:"path"`
}

// New reads contractPath, derives the only allowed routes, and starts a real
// HTTP server on an ephemeral 127.0.0.1 port.
func New(contractPath string, scenario Scenario) (*Server, error) {
	if scenario.Namespace == "" || scenario.SessionID == "" || scenario.PageLimit <= 0 || len(scenario.Pages) == 0 {
		return nil, errors.New("contractmock: incomplete scenario")
	}
	if scenario.FailOperation != "" && scenario.FailStatus < 400 {
		return nil, errors.New("contractmock: failure status must be at least 400")
	}

	file, err := os.Open(contractPath)
	if err != nil {
		return nil, fmt.Errorf("contractmock: open contract: %w", err)
	}
	defer file.Close()
	raw, err := io.ReadAll(file)
	if err != nil {
		return nil, fmt.Errorf("contractmock: read contract: %w", err)
	}
	var document contractDocument
	if err := json.Unmarshal(raw, &document); err != nil {
		return nil, fmt.Errorf("contractmock: decode contract: %w", err)
	}

	operations := append(
		append([]contractOperation(nil), document.OpenAPIProjection.Operations...),
		document.KubernetesAPI.Operations...,
	)
	if len(operations) != 2 {
		return nil, fmt.Errorf("contractmock: contract operation count is %d, want 2", len(operations))
	}

	s := &Server{scenario: cloneScenario(scenario)}
	seen := make(map[string]bool, len(operations))
	for _, operation := range operations {
		if operation.Name == "" || operation.Method == "" || operation.Path == "" || seen[operation.Name] {
			return nil, errors.New("contractmock: invalid named operation")
		}
		seen[operation.Name] = true
		compiled, compileErr := compileRoute(operation)
		if compileErr != nil {
			return nil, compileErr
		}
		s.routes = append(s.routes, compiled)
	}
	if !seen[OperationNamespaceList] || !seen[OperationClusterList] {
		return nil, errors.New("contractmock: required named operations are absent")
	}

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		// Some authoring sandboxes prohibit loopback sockets. Keep the exact
		// net/http request boundary available there through a handler-backed
		// transport; verifier environments with loopback support use the real
		// ephemeral listener above.
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

// URL is the vCenter origin for a test client.
func (s *Server) URL() string {
	return s.url
}

// Client is configured for the loopback server.
func (s *Server) Client() *http.Client {
	return s.client
}

// Close stops the loopback service.
func (s *Server) Close() {
	if s.http != nil {
		s.http.Close()
	}
}

// Requests returns a deep copy of the flushed in-memory request log.
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

// ServeHTTP records every request, then dispatches only a route derived from
// the named operations in docs/contract.json.
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
		Host:          r.Host,
		Header:        r.Header.Clone(),
		Body:          append([]byte(nil), body...),
		ContentLength: r.ContentLength,
	})
	s.mu.Unlock()

	switch operation {
	case OperationNamespaceList:
		s.serveNamespaces(w, r, body)
	case OperationClusterList:
		s.serveClusters(w, r, body, parameters)
	default:
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "operation_not_in_contract"})
	}
}

func (s *Server) serveNamespaces(w http.ResponseWriter, r *http.Request, body []byte) {
	target := s.template(OperationNamespaceList)
	if r.RequestURI != target ||
		len(body) != 0 ||
		r.ContentLength != 0 ||
		!exactHeader(r.Header, "Accept", "application/json") ||
		!exactHeader(r.Header, "vmware-api-session-id", s.scenario.SessionID) ||
		r.Header.Values("Authorization") != nil ||
		r.Header.Values("Content-Type") != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "namespace_request_wire_mismatch"})
		return
	}
	if s.scenario.FailOperation == OperationNamespaceList {
		writeJSON(w, s.scenario.FailStatus, map[string]any{
			"error_type": "FIXTURE_FAILURE",
			"secret":     s.scenario.SessionID,
		})
		return
	}

	masterHost := s.authority
	if s.scenario.MasterHostOverride != "" {
		masterHost = s.scenario.MasterHostOverride
	}
	summaries := []map[string]string{
		{
			"namespace":   "unrelated-" + s.scenario.Namespace,
			"master_host": s.authority,
		},
		{
			"namespace":   s.scenario.Namespace,
			"master_host": masterHost,
		},
	}
	if s.scenario.CorruptUnrelatedSummary {
		delete(summaries[0], "master_host")
	}
	if s.scenario.DuplicateNamespace {
		summaries = append(summaries, map[string]string{
			"namespace":   s.scenario.Namespace,
			"master_host": masterHost,
		})
	}
	writeJSON(w, http.StatusOK, summaries)
}

func (s *Server) serveClusters(w http.ResponseWriter, r *http.Request, body []byte, parameters map[string]string) {
	s.mu.Lock()
	pageIndex := s.page
	s.mu.Unlock()
	if pageIndex >= len(s.scenario.Pages) {
		writeJSON(w, http.StatusConflict, map[string]any{"error": "unexpected_extra_page"})
		return
	}

	continueToken := ""
	if pageIndex > 0 {
		continueToken = s.scenario.Pages[pageIndex-1].Continue
	}
	expectedTarget := strings.ReplaceAll(
		s.template(OperationClusterList),
		"{namespace}",
		url.PathEscape(s.scenario.Namespace),
	)
	query := url.Values{
		"limit": {strconv.FormatInt(s.scenario.PageLimit, 10)},
	}
	if continueToken != "" {
		query.Set("continue", continueToken)
	}
	expectedTarget += "?" + query.Encode()

	if r.RequestURI != expectedTarget ||
		parameters["namespace"] != s.scenario.Namespace ||
		len(body) != 0 ||
		r.ContentLength != 0 ||
		!exactHeader(r.Header, "Accept", "application/json") ||
		!exactHeader(r.Header, "Authorization", "Bearer "+s.scenario.SessionID) ||
		r.Header.Values("vmware-api-session-id") != nil ||
		r.Header.Values("Content-Type") != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "cluster_request_wire_mismatch"})
		return
	}
	if s.scenario.FailOperation == OperationClusterList {
		writeJSON(w, s.scenario.FailStatus, map[string]any{
			"kind":    "Status",
			"message": s.scenario.SessionID,
		})
		return
	}

	page := s.scenario.Pages[pageIndex]
	s.mu.Lock()
	s.page++
	s.mu.Unlock()

	items := make([]map[string]any, len(page.Items))
	for i, item := range page.Items {
		items[i] = map[string]any{
			"apiVersion": "cluster.x-k8s.io/v1beta2",
			"kind":       "Cluster",
			"metadata": map[string]any{
				"name":            item.Name,
				"namespace":       s.scenario.Namespace,
				"uid":             item.UID,
				"resourceVersion": item.ResourceVersion,
			},
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"apiVersion": "cluster.x-k8s.io/v1beta2",
		"kind":       "ClusterList",
		"metadata": map[string]any{
			"continue": page.Continue,
		},
		"items": items,
	})
}

func (s *Server) match(method, escapedPath string) (string, map[string]string) {
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
	for _, match := range placeholder.FindAllStringSubmatchIndex(operation.Path, -1) {
		expression.WriteString(regexp.QuoteMeta(operation.Path[cursor:match[0]]))
		expression.WriteString(`([^/]+)`)
		parameters = append(parameters, operation.Path[match[2]:match[3]])
		cursor = match[1]
	}
	expression.WriteString(regexp.QuoteMeta(operation.Path[cursor:]))
	pattern, err := regexp.Compile("^" + expression.String() + "$")
	if err != nil {
		return route{}, fmt.Errorf("contractmock: compile route %q: %w", operation.Name, err)
	}
	return route{
		name:       operation.Name,
		method:     operation.Method,
		template:   operation.Path,
		pattern:    pattern,
		parameters: parameters,
	}, nil
}

func exactHeader(header http.Header, name, value string) bool {
	values := header.Values(name)
	return len(values) == 1 && values[0] == value
}

func cloneScenario(in Scenario) Scenario {
	out := in
	out.Pages = make([]Page, len(in.Pages))
	for i, page := range in.Pages {
		out.Pages[i] = page
		out.Pages[i].Items = append([]Resource(nil), page.Items...)
	}
	return out
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

type handlerTransport struct {
	handler http.Handler
}

func (transport handlerTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	select {
	case <-request.Context().Done():
		return nil, request.Context().Err()
	default:
	}
	clone := request.Clone(request.Context())
	clone.RequestURI = request.URL.RequestURI()
	clone.Host = request.URL.Host
	recorder := httptest.NewRecorder()
	transport.handler.ServeHTTP(recorder, clone)
	response := recorder.Result()
	response.Request = request
	return response, nil
}
