// Package contractmock provides the verifier's contract-pinned loopback
// server. Its complete route allow-list comes from docs/contract.json, and its
// request log lets tests inspect what the client put on the wire.
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
	"strings"
	"sync"
)

type Auth struct {
	VCenterSessionID      string
	KubernetesBearerToken string
}

type Event struct {
	Name              string
	Type              string
	Reason            string
	Message           string
	InvolvedKind      string
	InvolvedNamespace string
	InvolvedName      string
}

type Fixture struct {
	Namespace    string
	Pod          string
	Supervisor   string
	ConfigStatus string
	Events       []Event
	Log          string
	Auth         Auth
	ForcedStatus map[string]int
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

	mu  sync.Mutex
	log []Request
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
	if len(document.Operations) != 3 {
		return nil, fmt.Errorf("contract operation count = %d, want 3", len(document.Operations))
	}

	server := &Server{
		fixture: fixture,
		routes:  make(map[string]route, len(document.Operations)),
	}
	for _, operation := range document.Operations {
		if operation.ContractName == "" || operation.SourceKind == "" ||
			operation.Method == "" || operation.PathTemplate == "" {
			return nil, errors.New("contract contains an incomplete operation")
		}
		path := strings.NewReplacer(
			"{namespace}", url.PathEscape(fixture.Namespace),
			"{pod}", url.PathEscape(fixture.Pod),
		).Replace(operation.PathTemplate)
		key := operation.Method + " " + path
		if _, exists := server.routes[key]; exists {
			return nil, fmt.Errorf("contract contains duplicate route %q", key)
		}
		server.routes[key] = route{
			name:       operation.ContractName,
			sourceKind: operation.SourceKind,
		}
	}

	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		// Some coding sandboxes deny AF_INET sockets. Keep the same
		// loopback-addressed HTTP boundary and exact handler there.
		server.testURL = "http://127.0.0.1"
		server.client = &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
			wireRequest := request.Clone(request.Context())
			wireRequest.Header = request.Header.Clone()
			recorder := httptest.NewRecorder()
			server.serveHTTP(recorder, wireRequest)
			return recorder.Result(), nil
		})}
		return server, nil
	}

	server.server = httptest.NewUnstartedServer(http.HandlerFunc(server.serveHTTP))
	server.server.Listener = listener
	server.server.Start()
	server.testURL = server.server.URL
	server.client = server.server.Client()
	return server, nil
}

func (s *Server) URL() string {
	return s.testURL
}

func (s *Server) Client() *http.Client {
	return s.client
}

func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	copied := make([]Request, len(s.log))
	for index, request := range s.log {
		copied[index] = request
		copied[index].Header = request.Header.Clone()
		copied[index].Body = append([]byte(nil), request.Body...)
		copied[index].TransferEncoding = append([]string(nil), request.TransferEncoding...)
	}
	return copied
}

func (s *Server) Close() {
	if s.server != nil {
		s.server.Close()
	}
}

func (s *Server) serveHTTP(response http.ResponseWriter, request *http.Request) {
	var body []byte
	if request.Body != nil {
		var err error
		body, err = io.ReadAll(request.Body)
		if err != nil {
			http.Error(response, "request body could not be read", http.StatusBadRequest)
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
	s.mu.Lock()
	s.log = append(s.log, logged)
	s.mu.Unlock()

	if !known {
		http.Error(response, "operation is not named by the contract", http.StatusNotFound)
		return
	}
	if len(body) != 0 {
		http.Error(response, "GET body must be absent", http.StatusBadRequest)
		return
	}
	if err := s.validateQuery(matched.name, request.URL); err != nil {
		http.Error(response, err.Error(), http.StatusBadRequest)
		return
	}
	if !s.authAccepted(matched, request.Header) {
		writeJSON(response, http.StatusUnauthorized, map[string]string{"status": "credential rejected"})
		return
	}
	if status := s.fixture.ForcedStatus[matched.name]; status != 0 {
		if status >= 300 && status < 400 {
			response.Header().Set("Location", "/operation-not-named-by-contract")
		}
		writeJSON(response, status, map[string]string{"status": "forced failure"})
		return
	}

	switch matched.name {
	case "getSupervisorNamespace":
		status := s.fixture.ConfigStatus
		if status == "" {
			status = "RUNNING"
		}
		writeJSON(response, http.StatusOK, map[string]any{
			"supervisor":    s.fixture.Supervisor,
			"config_status": status,
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
	case "listPodEvents":
		items := make([]map[string]any, len(s.fixture.Events))
		for index, event := range s.fixture.Events {
			items[index] = map[string]any{
				"apiVersion": "v1",
				"kind":       "Event",
				"metadata": map[string]string{
					"name":      event.Name,
					"namespace": s.fixture.Namespace,
				},
				"involvedObject": map[string]string{
					"kind":      event.InvolvedKind,
					"namespace": event.InvolvedNamespace,
					"name":      event.InvolvedName,
				},
				"type":    event.Type,
				"reason":  event.Reason,
				"message": event.Message,
			}
		}
		writeJSON(response, http.StatusOK, map[string]any{
			"apiVersion": "v1",
			"kind":       "EventList",
			"items":      items,
		})
	case "readPodLog":
		response.Header().Set("Content-Type", "text/plain")
		response.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(response, s.fixture.Log)
	default:
		http.Error(response, "contract operation has no fixture", http.StatusInternalServerError)
	}
}

func (s *Server) validateQuery(operation string, requestURL *url.URL) error {
	query := requestURL.Query()
	switch operation {
	case "getSupervisorNamespace":
		if requestURL.RawQuery != "" || requestURL.ForceQuery {
			return errors.New("vCenter query inputs must be omitted")
		}
	case "listPodEvents":
		if requestURL.ForceQuery || len(query) != 1 || len(query["fieldSelector"]) != 1 {
			return errors.New("event query must contain only one fieldSelector")
		}
		want := "involvedObject.kind=Pod,involvedObject.namespace=" +
			s.fixture.Namespace + ",involvedObject.name=" + s.fixture.Pod
		if query.Get("fieldSelector") != want {
			return errors.New("event fieldSelector does not select the fixture pod")
		}
	case "readPodLog":
		if requestURL.ForceQuery {
			return errors.New("bare query marker is forbidden")
		}
		for name, values := range query {
			if (name != "container" && name != "previous") || len(values) != 1 || values[0] == "" {
				return errors.New("pod log query contains an unset or unknown input")
			}
			if name == "previous" && values[0] != "true" && values[0] != "false" {
				return errors.New("previous must use a JSON boolean spelling")
			}
		}
	}
	return nil
}

func (s *Server) authAccepted(operation route, header http.Header) bool {
	accept := header.Values("Accept")
	if len(accept) != 1 || accept[0] != "application/json" ||
		len(header.Values("Content-Type")) != 0 {
		return false
	}
	switch operation.sourceKind {
	case "openapi":
		return equalOne(header.Values("vmware-api-session-id"), s.fixture.Auth.VCenterSessionID) &&
			len(header.Values("Authorization")) == 0
	case "kubernetes-resource":
		return equalOne(header.Values("Authorization"), "Bearer "+s.fixture.Auth.KubernetesBearerToken) &&
			len(header.Values("vmware-api-session-id")) == 0
	default:
		return false
	}
}

func equalOne(values []string, want string) bool {
	return len(values) == 1 && values[0] == want
}

func writeJSON(response http.ResponseWriter, status int, value any) {
	response.Header().Set("Content-Type", "application/json")
	response.WriteHeader(status)
	_ = json.NewEncoder(response).Encode(value)
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}
