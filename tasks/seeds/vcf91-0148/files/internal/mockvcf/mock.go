package mockvcf

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"regexp"
	"strings"
	"sync"
)

type Config struct {
	Supervisor            string
	Namespace             string
	Cluster               string
	SessionID             string
	ExpiredToken          string
	FreshToken            string
	NamespaceExists       bool
	ClusterExists         bool
	ExpireOnClusterCreate bool
	RejectFreshToken      bool
}

type Request struct {
	Method string
	Target string
	Header http.Header
	Body   string
}

type operation struct {
	Name   string
	Method string
	Path   string
	Status int
	re     *regexp.Regexp
}

type contractFile struct {
	Server struct {
		APIRoot string `json:"api_root"`
	} `json:"server"`
	Operations []struct {
		OperationID   string `json:"operation_id"`
		Method        string `json:"method"`
		Path          string `json:"path"`
		SuccessStatus int    `json:"success_status"`
	} `json:"operations"`
	Kubernetes struct {
		Operations []struct {
			Name          string `json:"name"`
			Method        string `json:"method"`
			Path          string `json:"path"`
			SuccessStatus int    `json:"success_status"`
		} `json:"operations"`
	} `json:"kubernetes_api"`
}

type Server struct {
	URL string

	cfg        Config
	httpServer *httptest.Server
	httpClient *http.Client
	operations []operation

	mu              sync.Mutex
	requests        []Request
	namespaceExists bool
	clusterExists   bool
	expiredOnce     bool
}

func Start(contractPath string, cfg Config) (*Server, error) {
	raw, err := os.ReadFile(contractPath)
	if err != nil {
		return nil, fmt.Errorf("read contract: %w", err)
	}
	var doc contractFile
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, fmt.Errorf("decode contract: %w", err)
	}

	s := &Server{
		cfg:             cfg,
		namespaceExists: cfg.NamespaceExists,
		clusterExists:   cfg.ClusterExists,
	}
	for _, item := range doc.Operations {
		s.operations = append(s.operations, compile(item.OperationID, item.Method, doc.Server.APIRoot+item.Path, item.SuccessStatus))
	}
	for _, item := range doc.Kubernetes.Operations {
		s.operations = append(s.operations, compile(item.Name, item.Method, item.Path, item.SuccessStatus))
	}
	if len(s.operations) != 4 {
		return nil, fmt.Errorf("contract exposes %d operations, want 4", len(s.operations))
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err == nil {
		s.httpServer = httptest.NewUnstartedServer(http.HandlerFunc(s.serveHTTP))
		s.httpServer.Listener = listener
		s.httpServer.Start()
		s.URL = s.httpServer.URL
		s.httpClient = s.httpServer.Client()
	} else {
		// Some build sandboxes prohibit all sockets, including loopback. Keep the
		// same HTTP boundary available there so the protected verifier can run.
		s.URL = "http://127.0.0.1"
		s.httpClient = &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
			r.Header.Del("User-Agent")
			recorder := httptest.NewRecorder()
			s.serveHTTP(recorder, r)
			return recorder.Result(), nil
		})}
	}
	return s, nil
}

func compile(name, method, path string, status int) operation {
	quoted := regexp.QuoteMeta(path)
	placeholder := regexp.MustCompile(`\\\{[^}]+\\\}`)
	pattern := "^" + placeholder.ReplaceAllString(quoted, `([^/]+)`) + "$"
	return operation{Name: name, Method: method, Path: path, Status: status, re: regexp.MustCompile(pattern)}
}

func (s *Server) Close() {
	if s != nil && s.httpServer != nil {
		s.httpServer.Close()
	}
}

func (s *Server) HTTPClient() *http.Client {
	return s.httpClient
}

func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.requests))
	for i, req := range s.requests {
		out[i] = Request{Method: req.Method, Target: req.Target, Header: req.Header.Clone(), Body: req.Body}
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	_ = r.Body.Close()

	s.mu.Lock()
	s.requests = append(s.requests, Request{
		Method: r.Method,
		Target: r.URL.RequestURI(),
		Header: r.Header.Clone(),
		Body:   string(body),
	})
	s.mu.Unlock()

	var matched *operation
	for i := range s.operations {
		op := &s.operations[i]
		if r.Method == op.Method && op.re.MatchString(r.URL.EscapedPath()) {
			matched = op
			break
		}
	}
	if matched == nil {
		http.NotFound(w, r)
		return
	}

	switch matched.Name {
	case "Vcenter.Namespaces.Instances_getV2":
		s.getNamespace(w, r)
	case "Vcenter.Namespaces.Instances_createV2":
		s.createNamespace(w, r, body)
	case "Kubernetes.Cluster.get":
		s.getCluster(w, r)
	case "Kubernetes.Cluster.create":
		s.createCluster(w, r, body)
	default:
		http.NotFound(w, r)
	}
}

func (s *Server) validSession(r *http.Request) bool {
	return r.Header.Get("vmware-api-session-id") == s.cfg.SessionID
}

func (s *Server) getNamespace(w http.ResponseWriter, r *http.Request) {
	if !s.validSession(r) {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	s.mu.Lock()
	exists := s.namespaceExists
	s.mu.Unlock()
	if !strings.HasSuffix(r.URL.Path, "/"+s.cfg.Namespace) || !exists {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = io.WriteString(w, fmt.Sprintf(
		`{"supervisor":%q,"config_status":"RUNNING","messages":[],"stats":{"cpu_used":0,"memory_used":0,"storage_used":0},"description":"","access_list":[],"storage_specs":[]}`,
		s.cfg.Supervisor,
	))
}

func (s *Server) createNamespace(w http.ResponseWriter, r *http.Request, body []byte) {
	if !s.validSession(r) {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	var got struct {
		Namespace  string `json:"namespace"`
		Supervisor string `json:"supervisor"`
	}
	if json.Unmarshal(body, &got) != nil || got.Namespace != s.cfg.Namespace || got.Supervisor != s.cfg.Supervisor {
		w.WriteHeader(http.StatusBadRequest)
		return
	}
	s.mu.Lock()
	s.namespaceExists = true
	s.mu.Unlock()
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) authorized(r *http.Request) (token string, ok bool) {
	value := r.Header.Get("Authorization")
	if !strings.HasPrefix(value, "Bearer ") {
		return "", false
	}
	token = strings.TrimPrefix(value, "Bearer ")
	return token, token == s.cfg.ExpiredToken || token == s.cfg.FreshToken
}

func (s *Server) getCluster(w http.ResponseWriter, r *http.Request) {
	_, ok := s.authorized(r)
	if !ok {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	s.mu.Lock()
	exists := s.clusterExists
	s.mu.Unlock()
	if !strings.HasSuffix(r.URL.Path, "/"+s.cfg.Cluster) || !exists {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = io.WriteString(w, fmt.Sprintf(`{"apiVersion":"cluster.x-k8s.io/v1beta2","kind":"Cluster","metadata":{"name":%q}}`, s.cfg.Cluster))
}

func (s *Server) createCluster(w http.ResponseWriter, r *http.Request, body []byte) {
	token, ok := s.authorized(r)
	if !ok {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	if token == s.cfg.FreshToken && s.cfg.RejectFreshToken {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	if token == s.cfg.ExpiredToken && s.cfg.ExpireOnClusterCreate {
		s.expiredOnce = true
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	var got struct {
		APIVersion string `json:"apiVersion"`
		Kind       string `json:"kind"`
		Metadata   struct {
			Name string `json:"name"`
		} `json:"metadata"`
	}
	if json.Unmarshal(body, &got) != nil ||
		got.APIVersion != "cluster.x-k8s.io/v1beta2" ||
		got.Kind != "Cluster" ||
		got.Metadata.Name != s.cfg.Cluster {
		w.WriteHeader(http.StatusBadRequest)
		return
	}
	s.clusterExists = true
	w.WriteHeader(http.StatusCreated)
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}
