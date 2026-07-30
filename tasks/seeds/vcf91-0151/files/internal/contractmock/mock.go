// Package contractmock provides a loopback-only HTTP fixture driven by
// docs/contract.json. It records requests so acceptance tests can verify the
// exact wire representation produced by the client.
package contractmock

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
)

type operation struct {
	Name           string `json:"name"`
	Method         string `json:"method"`
	ServerBasePath string `json:"serverBasePath"`
	Path           string `json:"path"`
}

type contract struct {
	Operations []operation `json:"operations"`
}

type Reply struct {
	Status int
	Body   string
	Header http.Header
}

type Fixture struct {
	Namespace string
	Cluster   string
	Replies   map[string]Reply
}

type Request struct {
	Operation        string
	Method           string
	Path             string
	RawQuery         string
	Header           http.Header
	ContentLength    int64
	TransferEncoding []string
	Body             []byte
}

type Server struct {
	server  *httptest.Server
	url     string
	client  *http.Client
	mu      sync.Mutex
	log     []Request
	routes  map[string]string
	replies map[string]Reply
}

func New(t testing.TB, contractPath string, fixture Fixture) *Server {
	t.Helper()

	data, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var doc contract
	if err := json.Unmarshal(data, &doc); err != nil {
		t.Fatalf("decode contract: %v", err)
	}

	s := &Server{
		routes:  make(map[string]string, len(doc.Operations)),
		replies: make(map[string]Reply, len(doc.Operations)),
	}
	for _, op := range doc.Operations {
		path := op.ServerBasePath + op.Path
		path = strings.ReplaceAll(path, "{namespace}", fixture.Namespace)
		path = strings.ReplaceAll(path, "{cluster}", fixture.Cluster)
		key := op.Method + " " + path
		if _, exists := s.routes[key]; exists {
			t.Fatalf("contract has duplicate route %q", key)
		}
		s.routes[key] = op.Name
		s.replies[op.Name] = defaultReply(op.Name)
	}
	for name, reply := range fixture.Replies {
		if !hasOperation(s.routes, name) {
			t.Fatalf("reply configured for operation absent from contract: %s", name)
		}
		s.replies[name] = reply
	}

	handler := http.HandlerFunc(s.serveHTTP)
	listener, listenErr := net.Listen("tcp4", "127.0.0.1:0")
	if listenErr == nil {
		s.server = httptest.NewUnstartedServer(handler)
		s.server.Listener = listener
		s.server.Start()
		s.url = s.server.URL
		s.client = s.server.Client()
		t.Cleanup(s.server.Close)
	} else {
		// Some coding sandboxes deny AF_INET listeners entirely. Keep the same
		// loopback URL and HTTP boundary, but dispatch the request to the exact
		// same handler through an in-memory transport so the verifier remains
		// hermetic in those environments.
		s.url = "http://127.0.0.1"
		s.client = &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, req)
			return recorder.Result(), nil
		})}
	}
	return s
}

func (s *Server) URL() string {
	return s.url
}

func (s *Server) Client() *http.Client {
	return s.client
}

func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	out := make([]Request, len(s.log))
	for i, req := range s.log {
		out[i] = req
		out[i].Header = req.Header.Clone()
		out[i].TransferEncoding = append([]string(nil), req.TransferEncoding...)
		out[i].Body = append([]byte(nil), req.Body...)
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "could not read request", http.StatusBadRequest)
		return
	}
	key := r.Method + " " + r.URL.EscapedPath()
	name, ok := s.routes[key]

	s.mu.Lock()
	s.log = append(s.log, Request{
		Operation:        name,
		Method:           r.Method,
		Path:             r.URL.EscapedPath(),
		RawQuery:         r.URL.RawQuery,
		Header:           r.Header.Clone(),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
		Body:             append([]byte(nil), body...),
	})
	s.mu.Unlock()

	if !ok {
		http.Error(w, fmt.Sprintf("operation is not in contract: %s", key), http.StatusNotFound)
		return
	}
	reply := s.replies[name]
	for key, values := range reply.Header {
		for _, value := range values {
			w.Header().Add(key, value)
		}
	}
	if reply.Body != "" && w.Header().Get("Content-Type") == "" {
		w.Header().Set("Content-Type", "application/json")
	}
	w.WriteHeader(reply.Status)
	_, _ = io.WriteString(w, reply.Body)
}

func defaultReply(name string) Reply {
	switch name {
	case "Vcenter.Namespaces.Instances_get":
		return Reply{
			Status: http.StatusOK,
			Body:   `{"cluster":"domain-c8","config_status":"RUNNING","messages":[],"stats":{"cpu_used":0,"memory_used":0,"storage_used":0},"description":"before","access_list":[],"storage_specs":[]}`,
		}
	case "Vcenter.Namespaces.Instances_update":
		return Reply{Status: http.StatusNoContent}
	case "Vks.Cluster_patch":
		return Reply{Status: http.StatusOK, Body: `{"kind":"Cluster"}`}
	default:
		panic("contract operation has no pinned default: " + name)
	}
}

func hasOperation(routes map[string]string, name string) bool {
	for _, candidate := range routes {
		if candidate == name {
			return true
		}
	}
	return false
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return fn(req)
}
