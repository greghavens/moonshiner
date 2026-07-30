// Package contractmock provides the verifier's contract-pinned loopback server.
// It derives its complete route allow-list from docs/contract.json and records
// the requests observed on the wire.
package contractmock

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync"
)

type Auth struct {
	VCenterSessionID      string
	KubernetesBearerToken string
}

type Cluster struct {
	Name            string
	UID             string
	ResourceVersion string
}

type Fixture struct {
	Namespace      string
	Supervisor     string
	Clusters       []Cluster
	OldAuth        Auth
	NewAuth        Auth
	BlockOperation string
	ForcedStatus   map[string]int
}

type Request struct {
	Operation        string
	Method           string
	RawTarget        string
	Header           http.Header
	Body             []byte
	ContentLength    int64
	TransferEncoding []string
}

type contractOperation struct {
	ContractName string `json:"contractName"`
	SourceKind   string `json:"sourceKind"`
	Method       string `json:"method"`
	PathTemplate string `json:"pathTemplate"`
	RawQuery     string `json:"rawQuery"`
}

type contractDocument struct {
	Operations []contractOperation `json:"operations"`
}

type route struct {
	name       string
	sourceKind string
}

type Server struct {
	fixture Fixture
	routes  map[string]route
	server  *httptest.Server
	testURL string
	client  *http.Client

	logMu sync.Mutex
	log   []Request

	stateMu     sync.Mutex
	blockUsed   bool
	rotated     bool
	blocked     chan struct{}
	release     chan struct{}
	releaseOnce sync.Once
}

func New(contractPath string, fixture Fixture) (*Server, error) {
	data, err := os.ReadFile(contractPath)
	if err != nil {
		return nil, fmt.Errorf("read contract: %w", err)
	}
	var document contractDocument
	if err := json.Unmarshal(data, &document); err != nil {
		return nil, fmt.Errorf("decode contract: %w", err)
	}
	if len(document.Operations) != 2 {
		return nil, fmt.Errorf("contract operation count = %d, want 2", len(document.Operations))
	}

	s := &Server{
		fixture: fixture,
		routes:  make(map[string]route, len(document.Operations)),
		blocked: make(chan struct{}),
		release: make(chan struct{}),
	}
	for _, operation := range document.Operations {
		if operation.ContractName == "" || operation.Method == "" || operation.PathTemplate == "" {
			return nil, errors.New("contract contains an incomplete operation")
		}
		path := strings.ReplaceAll(
			operation.PathTemplate,
			"{namespace}",
			url.PathEscape(fixture.Namespace),
		)
		key := operation.Method + " " + path
		if _, exists := s.routes[key]; exists {
			return nil, fmt.Errorf("contract contains duplicate route %q", key)
		}
		s.routes[key] = route{
			name:       operation.ContractName,
			sourceKind: operation.SourceKind,
		}
		if operation.RawQuery != "" {
			return nil, fmt.Errorf("operation %s requires an unexpected query", operation.ContractName)
		}
	}

	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		// The selected coding harness may deny AF_INET sockets. Keep the same
		// loopback-addressed HTTP boundary and exact handler in that environment.
		s.testURL = "http://127.0.0.1"
		s.client = &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
			wireRequest := request.Clone(request.Context())
			wireRequest.Header = request.Header.Clone()
			for name, values := range wireRequest.Header {
				if len(values) == 0 {
					wireRequest.Header.Del(name)
				}
			}
			recorder := httptest.NewRecorder()
			s.serveHTTP(recorder, wireRequest)
			return recorder.Result(), nil
		})}
		return s, nil
	}
	s.server = httptest.NewUnstartedServer(http.HandlerFunc(s.serveHTTP))
	s.server.Listener = listener
	s.server.Start()
	s.testURL = s.server.URL
	s.client = s.server.Client()
	return s, nil
}

func (s *Server) URL() string {
	return s.testURL
}

func (s *Server) Client() *http.Client {
	return s.client
}

func (s *Server) Requests() []Request {
	s.logMu.Lock()
	defer s.logMu.Unlock()

	copied := make([]Request, len(s.log))
	for i, request := range s.log {
		copied[i] = request
		copied[i].Header = request.Header.Clone()
		copied[i].Body = append([]byte(nil), request.Body...)
	}
	return copied
}

func (s *Server) WaitBlocked(ctx context.Context) error {
	select {
	case <-s.blocked:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// PublishReplacement changes which fixture credential generation is accepted,
// then releases the deliberately paused old-generation request.
func (s *Server) PublishReplacement() {
	s.stateMu.Lock()
	s.rotated = true
	s.stateMu.Unlock()
	s.releaseOnce.Do(func() { close(s.release) })
}

func (s *Server) Close() {
	s.releaseOnce.Do(func() { close(s.release) })
	if s.server != nil {
		s.server.Close()
	}
}

func (s *Server) serveHTTP(w http.ResponseWriter, request *http.Request) {
	var body []byte
	if request.Body != nil {
		var err error
		body, err = io.ReadAll(request.Body)
		if err != nil {
			http.Error(w, "request body could not be read", http.StatusBadRequest)
			return
		}
	}

	key := request.Method + " " + request.URL.EscapedPath()
	matched, known := s.routes[key]
	logged := Request{
		Method:           request.Method,
		RawTarget:        request.URL.RequestURI(),
		Header:           request.Header.Clone(),
		Body:             append([]byte(nil), body...),
		ContentLength:    request.ContentLength,
		TransferEncoding: append([]string(nil), request.TransferEncoding...),
	}
	if known {
		logged.Operation = matched.name
	}
	s.logMu.Lock()
	s.log = append(s.log, logged)
	s.logMu.Unlock()

	if !known {
		http.Error(w, "operation is not named by the contract", http.StatusNotFound)
		return
	}
	if request.URL.RawQuery != "" || request.URL.ForceQuery {
		http.Error(w, "query inputs must be omitted", http.StatusBadRequest)
		return
	}
	if len(body) != 0 {
		http.Error(w, "GET body must be absent", http.StatusBadRequest)
		return
	}

	if s.shouldBlock(matched, request.Header) {
		close(s.blocked)
		<-s.release
		writeJSON(w, http.StatusUnauthorized, map[string]string{"status": "retired credential"})
		return
	}
	if status := s.fixture.ForcedStatus[matched.name]; status != 0 {
		writeJSON(w, status, map[string]string{"status": "forced failure"})
		return
	}
	if !s.authAccepted(matched, request.Header) {
		writeJSON(w, http.StatusUnauthorized, map[string]string{"status": "credential rejected"})
		return
	}

	switch matched.name {
	case "getSupervisorNamespace":
		writeJSON(w, http.StatusOK, map[string]any{
			"supervisor":    s.fixture.Supervisor,
			"config_status": "RUNNING",
			"description":   "",
			"messages":      []any{},
			"stats": map[string]int64{
				"cpu_used":     0,
				"memory_used":  0,
				"storage_used": 0,
			},
			"access_list":   []any{},
			"storage_specs": []any{},
		})
	case "listVksClusters":
		items := make([]map[string]any, len(s.fixture.Clusters))
		for i, cluster := range s.fixture.Clusters {
			items[i] = map[string]any{
				"apiVersion": "cluster.x-k8s.io/v1beta2",
				"kind":       "Cluster",
				"metadata": map[string]string{
					"name":            cluster.Name,
					"namespace":       s.fixture.Namespace,
					"uid":             cluster.UID,
					"resourceVersion": cluster.ResourceVersion,
				},
			}
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"apiVersion": "cluster.x-k8s.io/v1beta2",
			"kind":       "ClusterList",
			"metadata":   map[string]string{"resourceVersion": "list-rv"},
			"items":      items,
		})
	default:
		http.Error(w, "named operation has no fixture implementation", http.StatusNotFound)
	}
}

func (s *Server) shouldBlock(matched route, header http.Header) bool {
	s.stateMu.Lock()
	defer s.stateMu.Unlock()

	if s.blockUsed || matched.name != s.fixture.BlockOperation || s.rotated {
		return false
	}
	if !authEquals(matched, header, s.fixture.OldAuth) {
		return false
	}
	s.blockUsed = true
	return true
}

func (s *Server) authAccepted(matched route, header http.Header) bool {
	s.stateMu.Lock()
	rotated := s.rotated
	s.stateMu.Unlock()
	if rotated {
		return authEquals(matched, header, s.fixture.NewAuth)
	}
	return authEquals(matched, header, s.fixture.OldAuth)
}

func authEquals(matched route, header http.Header, auth Auth) bool {
	switch matched.sourceKind {
	case "openapi":
		return len(header.Values("vmware-api-session-id")) == 1 &&
			header.Get("vmware-api-session-id") == auth.VCenterSessionID &&
			len(header.Values("Authorization")) == 0
	case "kubernetes-resource":
		return len(header.Values("Authorization")) == 1 &&
			header.Get("Authorization") == "Bearer "+auth.KubernetesBearerToken &&
			len(header.Values("vmware-api-session-id")) == 0
	default:
		return false
	}
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	encoded, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(encoded)
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}
