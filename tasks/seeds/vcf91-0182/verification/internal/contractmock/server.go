package contractmock

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"syscall"
)

type Scenario struct {
	OldToken       string
	NewToken       string
	ExpireOnName   string
	Existing       []map[string]any
	OldPostArrived chan<- struct{}
	ReleaseOldPost <-chan struct{}
}

type Request struct {
	OperationID      string
	Method           string
	RequestURI       string
	Header           http.Header
	Body             []byte
	ContentLength    int64
	TransferEncoding []string
	Status           int
}

type operation struct {
	OperationID   string `json:"operationId"`
	Method        string `json:"method"`
	PathTemplate  string `json:"pathTemplate"`
	SuccessStatus int    `json:"successStatus"`
}

type projection struct {
	Operations []operation `json:"operations"`
}

type Server struct {
	testServer *httptest.Server
	client     *http.Client
	baseURL    string
	allowed    map[string]operation
	scenario   Scenario
	idPrefix   string

	mu       sync.Mutex
	expired  bool
	created  int
	state    []map[string]any
	requests []Request
}

func Start(contractPath string, scenario Scenario) (*Server, error) {
	raw, err := os.ReadFile(contractPath)
	if err != nil {
		return nil, fmt.Errorf("read contract: %w", err)
	}

	var contract projection
	if err := json.Unmarshal(raw, &contract); err != nil {
		return nil, fmt.Errorf("decode contract: %w", err)
	}
	if len(contract.Operations) == 0 {
		return nil, errors.New("contract contains no operations")
	}

	allowed := make(map[string]operation, len(contract.Operations))
	for _, op := range contract.Operations {
		if op.OperationID == "" || op.Method == "" || op.PathTemplate == "" || op.SuccessStatus == 0 {
			return nil, errors.New("contract contains an incomplete operation")
		}
		key := strings.ToUpper(op.Method) + " " + op.PathTemplate
		if _, exists := allowed[key]; exists {
			return nil, fmt.Errorf("contract contains duplicate route %s", key)
		}
		allowed[key] = op
	}

	state, err := cloneObjects(scenario.Existing)
	if err != nil {
		return nil, fmt.Errorf("copy scenario: %w", err)
	}
	if state == nil {
		state = make([]map[string]any, 0)
	}
	prefix, err := randomID()
	if err != nil {
		return nil, fmt.Errorf("create runtime id: %w", err)
	}

	server := &Server{
		allowed:  allowed,
		scenario: scenario,
		idPrefix: prefix,
		state:    state,
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		if !errors.Is(err, syscall.EPERM) &&
			!errors.Is(err, syscall.EACCES) &&
			!errors.Is(err, syscall.EAFNOSUPPORT) {
			return nil, fmt.Errorf("listen on loopback: %w", err)
		}
		server.baseURL = "http://127.0.0.1"
		server.client = &http.Client{
			Transport: inProcessTransport{handler: server},
		}
		return server, nil
	}
	testServer := httptest.NewUnstartedServer(server)
	testServer.Listener = listener
	testServer.Start()
	server.testServer = testServer
	server.client = testServer.Client()
	server.baseURL = testServer.URL
	return server, nil
}

func (s *Server) URL() string {
	return s.baseURL
}

func (s *Server) Client() *http.Client {
	return s.client
}

func (s *Server) Close() {
	if s.testServer != nil {
		s.testServer.Close()
	}
}

func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	out := make([]Request, len(s.requests))
	for i, request := range s.requests {
		out[i] = Request{
			OperationID:      request.OperationID,
			Method:           request.Method,
			RequestURI:       request.RequestURI,
			Header:           request.Header.Clone(),
			Body:             append([]byte(nil), request.Body...),
			ContentLength:    request.ContentLength,
			TransferEncoding: append([]string(nil), request.TransferEncoding...),
			Status:           request.Status,
		}
	}
	return out
}

func (s *Server) State() []map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	state, _ := cloneObjects(s.state)
	return state
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	var body []byte
	var readErr error
	if r.Body != nil {
		body, readErr = io.ReadAll(io.LimitReader(r.Body, 1<<20))
		_ = r.Body.Close()
	}

	key := r.Method + " " + r.URL.EscapedPath()
	op, allowed := s.allowed[key]
	if r.URL.RawQuery != "" || r.URL.ForceQuery {
		allowed = false
		op = operation{}
	}
	status := http.StatusNotFound
	var response any = map[string]any{"error": "route not in focused contract"}

	if allowed &&
		op.Method == http.MethodPost &&
		r.Header.Get("X-JWT-Token") == s.scenario.OldToken &&
		s.scenario.OldPostArrived != nil &&
		s.scenario.ReleaseOldPost != nil {
		s.scenario.OldPostArrived <- struct{}{}
		<-s.scenario.ReleaseOldPost
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	if readErr != nil {
		status = http.StatusBadRequest
		response = map[string]any{"error": "unreadable request"}
	} else if allowed {
		status, response = s.handleAllowed(op, r, body)
	}

	s.requests = append(s.requests, Request{
		OperationID:      op.OperationID,
		Method:           r.Method,
		RequestURI:       r.RequestURI,
		Header:           r.Header.Clone(),
		Body:             append([]byte(nil), body...),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
		Status:           status,
	})

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(response)
}

func (s *Server) handleAllowed(op operation, r *http.Request, body []byte) (int, any) {
	token := r.Header.Get("X-JWT-Token")
	if token != s.scenario.OldToken && token != s.scenario.NewToken {
		return http.StatusForbidden, map[string]any{"error": "authentication required"}
	}

	if op.Method == http.MethodGet {
		if token == s.scenario.OldToken && s.expired {
			return http.StatusForbidden, map[string]any{"error": "authentication required"}
		}
		state, _ := cloneObjects(s.state)
		return op.SuccessStatus, state
	}

	var desired map[string]any
	if err := json.Unmarshal(body, &desired); err != nil {
		return http.StatusBadRequest, map[string]any{"error": "invalid request"}
	}
	name, _ := desired["name"].(string)
	if token == s.scenario.OldToken {
		if s.expired || name == s.scenario.ExpireOnName {
			s.expired = true
			return http.StatusForbidden, map[string]any{"error": "authentication required"}
		}
	}

	s.created++
	created, _ := cloneObject(desired)
	created["id"] = fmt.Sprintf("%s-%d", s.idPrefix, s.created)
	s.state = append(s.state, created)
	response, _ := cloneObject(created)
	return op.SuccessStatus, response
}

func cloneObjects(in []map[string]any) ([]map[string]any, error) {
	if in == nil {
		return nil, nil
	}
	out := make([]map[string]any, len(in))
	for i, item := range in {
		cloned, err := cloneObject(item)
		if err != nil {
			return nil, err
		}
		out[i] = cloned
	}
	return out, nil
}

func cloneObject(in map[string]any) (map[string]any, error) {
	raw, err := json.Marshal(in)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return out, nil
}

func randomID() (string, error) {
	var value [8]byte
	if _, err := rand.Read(value[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(value[:]), nil
}

type inProcessTransport struct {
	handler http.Handler
}

func (t inProcessTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	serverRequest := request.Clone(request.Context())
	serverRequest.RequestURI = request.URL.RequestURI()
	recorder := httptest.NewRecorder()
	t.handler.ServeHTTP(recorder, serverRequest)
	response := recorder.Result()
	response.Request = request
	return response, nil
}
