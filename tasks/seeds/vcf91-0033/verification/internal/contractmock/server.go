// Package contractmock provides the protected contract-pinned loopback SDDC
// Manager used by the acceptance tests.
package contractmock

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync"
)

const (
	UpdateHosts = "updateHosts"
	GetTask     = "getTask"
	GetHosts    = "getHosts"
)

// VCFError is the focused contract error shape.
type VCFError struct {
	ErrorCode          string `json:"errorCode,omitempty"`
	Message            string `json:"message,omitempty"`
	RemediationMessage string `json:"remediationMessage,omitempty"`
	ReferenceToken     string `json:"referenceToken,omitempty"`
}

// Task is the focused asynchronous Task shape.
type Task struct {
	ID                string     `json:"id"`
	Name              string     `json:"name"`
	Type              string     `json:"type,omitempty"`
	Status            string     `json:"status"`
	CreationTimestamp string     `json:"creationTimestamp"`
	Errors            []VCFError `json:"errors,omitempty"`
}

// Host is the focused getHosts element shape.
type Host struct {
	ID     string `json:"id"`
	FQDN   string `json:"fqdn"`
	Status string `json:"status"`
}

// TaskReply controls one getTask response.
type TaskReply struct {
	HTTPStatus int
	Task       Task
	APIError   VCFError
}

// Plan controls the fixture responses. Tests build it only after receiving the
// independently generated runtime values.
type Plan struct {
	UpdateHTTPStatus int
	UpdateTask       Task
	UpdateAPIError   VCFError
	TaskPolls        []TaskReply
	HostsHTTPStatus  int
	Hosts            []Host
	HostsAPIError    VCFError
}

// Request is one request captured by the race-safe request log.
type Request struct {
	OperationID      string
	Method           string
	Path             string
	EscapedPath      string
	RawQuery         string
	ForceQuery       bool
	Header           http.Header
	ContentLength    int64
	TransferEncoding []string
	Body             []byte
}

// RuntimeValues are generated independently for every server.
type RuntimeValues struct {
	AccessToken    string
	TaskID         string
	HostAlphaID    string
	HostCharlieID  string
	HostZuluID     string
	AlphaFQDN      string
	ZuluFQDN       string
	ReferenceToken string
}

type contractOperation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

// Server is an IPv4 loopback-only fixture serving exactly the focused
// contract's operation set.
type Server struct {
	httpServer *httptest.Server
	plan       Plan
	runtime    RuntimeValues
	allowed    map[string]contractOperation

	mu            sync.Mutex
	requests      []Request
	taskPollIndex int
	hostResponses int
}

// New loads and pins the contract, generates runtime values, and starts the
// loopback server.
func New(
	contractPath string,
	planFactory func(RuntimeValues) Plan,
) (*Server, error) {
	allowed, err := loadOperations(contractPath)
	if err != nil {
		return nil, err
	}
	suffix := randomValue("fixture")
	runtime := RuntimeValues{
		AccessToken:    randomValue("access"),
		TaskID:         "task id/" + randomValue("task"),
		HostAlphaID:    "host-a-" + randomValue("id"),
		HostCharlieID:  "host-c-" + randomValue("id"),
		HostZuluID:     "host-z-" + randomValue("id"),
		AlphaFQDN:      "node-a-" + suffix + ".example.test",
		ZuluFQDN:       "node-z-" + suffix + ".example.test",
		ReferenceToken: randomValue("reference"),
	}
	plan := Plan{}
	if planFactory != nil {
		plan = planFactory(runtime)
	}
	server := &Server{
		plan:    plan,
		runtime: runtime,
		allowed: allowed,
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, errors.New("cannot start loopback contract mock")
	}
	server.httpServer = &httptest.Server{
		Listener: listener,
		Config:   &http.Server{Handler: http.HandlerFunc(server.serveHTTP)},
	}
	server.httpServer.Start()
	return server, nil
}

func loadOperations(path string) (map[string]contractOperation, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, errors.New("cannot read focused contract")
	}
	var contract struct {
		Operations []contractOperation `json:"operations"`
	}
	if json.Unmarshal(data, &contract) != nil {
		return nil, errors.New("cannot decode focused contract")
	}
	allowed := make(map[string]contractOperation, len(contract.Operations))
	for _, operation := range contract.Operations {
		if operation.OperationID == "" ||
			operation.Method == "" ||
			operation.Path == "" {
			return nil, errors.New("focused contract contains an incomplete operation")
		}
		if _, exists := allowed[operation.OperationID]; exists {
			return nil, errors.New("focused contract contains a duplicate operationId")
		}
		allowed[operation.OperationID] = operation
	}
	required := map[string]contractOperation{
		UpdateHosts: {
			OperationID: UpdateHosts,
			Method:      http.MethodPatch,
			Path:        "/v1/hosts",
		},
		GetTask: {
			OperationID: GetTask,
			Method:      http.MethodGet,
			Path:        "/v1/tasks/{id}",
		},
		GetHosts: {
			OperationID: GetHosts,
			Method:      http.MethodGet,
			Path:        "/v1/hosts",
		},
	}
	if len(allowed) != len(required) {
		return nil, errors.New("focused contract operation set is not pinned")
	}
	for operationID, want := range required {
		if got, ok := allowed[operationID]; !ok || got != want {
			return nil, errors.New("focused contract operation does not match pinned route")
		}
	}
	return allowed, nil
}

// Close stops the fixture.
func (s *Server) Close() {
	s.httpServer.Close()
}

// URL returns the loopback origin.
func (s *Server) URL() string {
	return s.httpServer.URL
}

// Client returns the fixture's HTTP client.
func (s *Server) Client() *http.Client {
	return s.httpServer.Client()
}

// Runtime returns the generated runtime values.
func (s *Server) Runtime() RuntimeValues {
	return s.runtime
}

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.requests))
	for index, request := range s.requests {
		out[index] = request
		out[index].Header = request.Header.Clone()
		out[index].TransferEncoding = append(
			[]string(nil),
			request.TransferEncoding...,
		)
		out[index].Body = append([]byte(nil), request.Body...)
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, request *http.Request) {
	body, _ := io.ReadAll(request.Body)
	operationID := s.operationFor(request)
	s.record(Request{
		OperationID:      operationID,
		Method:           request.Method,
		Path:             request.URL.Path,
		EscapedPath:      request.URL.EscapedPath(),
		RawQuery:         request.URL.RawQuery,
		ForceQuery:       request.URL.ForceQuery,
		Header:           request.Header.Clone(),
		ContentLength:    request.ContentLength,
		TransferEncoding: append([]string(nil), request.TransferEncoding...),
		Body:             append([]byte(nil), body...),
	})

	if operationID == "" {
		writeJSON(w, http.StatusNotFound, VCFError{
			ErrorCode: "NOT_IN_CONTRACT",
			Message:   "the focused contract does not serve this operation",
		})
		return
	}
	if request.URL.RawQuery != "" || request.URL.ForceQuery {
		writeJSON(w, http.StatusBadRequest, VCFError{
			ErrorCode: "QUERY_NOT_IN_WORKFLOW",
			Message:   "the focused workflow does not use optional query parameters",
		})
		return
	}
	if request.Header.Get("Authorization") != "Bearer "+s.runtime.AccessToken {
		writeJSON(w, http.StatusUnauthorized, VCFError{
			ErrorCode: "UNAUTHORIZED",
			Message:   "the generated bearer token is required",
		})
		return
	}

	switch operationID {
	case UpdateHosts:
		s.updateHosts(w)
	case GetTask:
		s.getTask(w)
	case GetHosts:
		s.getHosts(w)
	}
}

func (s *Server) operationFor(request *http.Request) string {
	if operation, ok := s.allowed[UpdateHosts]; ok &&
		request.Method == operation.Method &&
		request.URL.Path == operation.Path {
		return operation.OperationID
	}
	if operation, ok := s.allowed[GetHosts]; ok &&
		request.Method == operation.Method &&
		request.URL.Path == operation.Path {
		return operation.OperationID
	}
	if operation, ok := s.allowed[GetTask]; ok &&
		request.Method == operation.Method {
		want := strings.ReplaceAll(
			operation.Path,
			"{id}",
			url.PathEscape(s.runtime.TaskID),
		)
		if request.URL.EscapedPath() == want {
			return operation.OperationID
		}
	}
	return ""
}

func (s *Server) record(request Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, request)
}

func (s *Server) updateHosts(w http.ResponseWriter) {
	s.mu.Lock()
	s.taskPollIndex = 0
	s.mu.Unlock()

	status := s.plan.UpdateHTTPStatus
	if status == 0 {
		status = http.StatusAccepted
	}
	if status != http.StatusAccepted {
		writeJSON(w, status, s.plan.UpdateAPIError)
		return
	}
	writeJSON(w, status, s.plan.UpdateTask)
}

func (s *Server) getTask(w http.ResponseWriter) {
	s.mu.Lock()
	index := s.taskPollIndex
	s.taskPollIndex++
	s.mu.Unlock()

	reply := TaskReply{
		HTTPStatus: http.StatusOK,
		Task:       s.plan.UpdateTask,
	}
	if len(s.plan.TaskPolls) != 0 {
		if index >= len(s.plan.TaskPolls) {
			index = len(s.plan.TaskPolls) - 1
		}
		reply = s.plan.TaskPolls[index]
		if reply.HTTPStatus == 0 {
			reply.HTTPStatus = http.StatusOK
		}
	}
	if reply.HTTPStatus != http.StatusOK {
		writeJSON(w, reply.HTTPStatus, reply.APIError)
		return
	}
	writeJSON(w, reply.HTTPStatus, reply.Task)
}

func (s *Server) getHosts(w http.ResponseWriter) {
	status := s.plan.HostsHTTPStatus
	if status == 0 {
		status = http.StatusOK
	}
	if status != http.StatusOK {
		writeJSON(w, status, s.plan.HostsAPIError)
		return
	}

	s.mu.Lock()
	responseIndex := s.hostResponses
	s.hostResponses++
	s.mu.Unlock()

	elements := append([]Host(nil), s.plan.Hosts...)
	if responseIndex%2 == 1 {
		for left, right := 0, len(elements)-1; left < right; left, right = left+1, right-1 {
			elements[left], elements[right] = elements[right], elements[left]
		}
	}
	writeJSON(w, http.StatusOK, struct {
		Elements     []Host `json:"elements"`
		PageMetadata struct {
			PageNumber    int `json:"pageNumber"`
			PageSize      int `json:"pageSize"`
			TotalElements int `json:"totalElements"`
			TotalPages    int `json:"totalPages"`
		} `json:"pageMetadata"`
	}{
		Elements: elements,
		PageMetadata: struct {
			PageNumber    int `json:"pageNumber"`
			PageSize      int `json:"pageSize"`
			TotalElements int `json:"totalElements"`
			TotalPages    int `json:"totalPages"`
		}{
			PageNumber:    0,
			PageSize:      len(elements),
			TotalElements: len(elements),
			TotalPages:    1,
		},
	})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func randomValue(prefix string) string {
	var data [12]byte
	if _, err := rand.Read(data[:]); err != nil {
		panic("cannot generate protected fixture value")
	}
	return prefix + "-" + hex.EncodeToString(data[:])
}
