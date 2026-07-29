// Package contractmock provides the protected, contract-pinned loopback
// SDDC Manager used by the acceptance tests.
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
	"os"
	"strings"
	"sync"
)

const (
	UpdateProxyConfiguration = "updateProxyConfiguration"
	SetCeipStatus            = "setCeipStatus"
	GetTask                  = "getTask"
)

// VCFError is the focused contract error shape.
type VCFError struct {
	ErrorCode          string `json:"errorCode,omitempty"`
	Message            string `json:"message,omitempty"`
	RemediationMessage string `json:"remediationMessage,omitempty"`
	ReferenceToken     string `json:"referenceToken,omitempty"`
}

// PollReply controls one getTask response.
type PollReply struct {
	HTTPStatus int
	TaskStatus string
	Errors     []VCFError
	APIError   VCFError
}

// Plan controls responses without placing scenario state in the mock itself.
type Plan struct {
	ProxySubmitStatus int
	ProxySubmitError  VCFError
	ProxyPolls        []PollReply
	CeipSubmitStatus  int
	CeipSubmitError   VCFError
	CeipPolls         []PollReply
}

// Request is one request observed by the loopback mock.
type Request struct {
	OperationID      string
	Method           string
	Path             string
	RawQuery         string
	Header           http.Header
	ContentLength    int64
	TransferEncoding []string
	Body             []byte
}

// RuntimeValues contains values generated independently for each mock.
type RuntimeValues struct {
	AccessToken string
	ProxyTaskID string
	CeipTaskID  string
}

type contractOperation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

// Server is a loopback-only server scoped to the operations in contract.json.
type Server struct {
	httpServer *httptest.Server
	plan       Plan
	runtime    RuntimeValues
	allowed    map[string]contractOperation

	mu         sync.Mutex
	requests   []Request
	proxyPolls int
	ceipPolls  int
}

// New loads the focused contract and starts an IPv4 loopback server.
func New(contractPath string, plan Plan) (*Server, error) {
	allowed, err := loadOperations(contractPath)
	if err != nil {
		return nil, err
	}
	server := &Server{
		plan:    plan,
		allowed: allowed,
		runtime: RuntimeValues{
			AccessToken: randomValue("access"),
			ProxyTaskID: randomValue("proxy-task"),
			CeipTaskID:  randomValue("ceip-task"),
		},
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
		if operation.OperationID == "" || operation.Method == "" || operation.Path == "" {
			return nil, errors.New("focused contract contains an incomplete operation")
		}
		if _, exists := allowed[operation.OperationID]; exists {
			return nil, errors.New("focused contract contains a duplicate operationId")
		}
		allowed[operation.OperationID] = operation
	}
	required := map[string]contractOperation{
		UpdateProxyConfiguration: {
			OperationID: UpdateProxyConfiguration,
			Method:      http.MethodPatch,
			Path:        "/v1/system/proxy-configuration",
		},
		SetCeipStatus: {
			OperationID: SetCeipStatus,
			Method:      http.MethodPatch,
			Path:        "/v1/system/ceip",
		},
		GetTask: {
			OperationID: GetTask,
			Method:      http.MethodGet,
			Path:        "/v1/tasks/{id}",
		},
	}
	if len(allowed) != len(required) {
		return nil, errors.New("focused contract operation set is not pinned")
	}
	for id, want := range required {
		if got, ok := allowed[id]; !ok || got != want {
			return nil, errors.New("focused contract operation does not match pinned route")
		}
	}
	return allowed, nil
}

// Close stops the loopback server.
func (s *Server) Close() {
	s.httpServer.Close()
}

// URL returns the loopback origin.
func (s *Server) URL() string {
	return s.httpServer.URL
}

// Client returns a client configured for this loopback server.
func (s *Server) Client() *http.Client {
	return s.httpServer.Client()
}

// Runtime returns the per-server generated token and task IDs.
func (s *Server) Runtime() RuntimeValues {
	return s.runtime
}

// Requests returns a deep copy of the race-safe request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.requests))
	for index, request := range s.requests {
		out[index] = request
		out[index].Header = request.Header.Clone()
		out[index].TransferEncoding = append([]string(nil), request.TransferEncoding...)
		out[index].Body = append([]byte(nil), request.Body...)
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	operationID := s.operationFor(r.Method, r.URL.Path)
	s.record(Request{
		OperationID:      operationID,
		Method:           r.Method,
		Path:             r.URL.Path,
		RawQuery:         r.URL.RawQuery,
		Header:           r.Header.Clone(),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
		Body:             append([]byte(nil), body...),
	})

	if operationID == "" {
		writeJSON(w, http.StatusNotFound, VCFError{
			ErrorCode: "NOT_IN_CONTRACT",
			Message:   "the focused contract does not serve this operation",
		})
		return
	}
	if r.URL.RawQuery != "" {
		writeJSON(w, http.StatusBadRequest, VCFError{
			ErrorCode: "QUERY_NOT_IN_CONTRACT",
			Message:   "the selected operation has no query parameters",
		})
		return
	}

	switch operationID {
	case UpdateProxyConfiguration:
		s.submitProxy(w)
	case SetCeipStatus:
		s.submitCeip(w)
	case GetTask:
		s.getTask(w, r.URL.Path)
	}
}

func (s *Server) operationFor(method, path string) string {
	if operation, ok := s.allowed[UpdateProxyConfiguration]; ok &&
		method == operation.Method && path == operation.Path {
		return operation.OperationID
	}
	if operation, ok := s.allowed[SetCeipStatus]; ok &&
		method == operation.Method && path == operation.Path {
		return operation.OperationID
	}
	if operation, ok := s.allowed[GetTask]; ok &&
		method == operation.Method &&
		strings.HasPrefix(path, strings.TrimSuffix(operation.Path, "{id}")) &&
		path != strings.TrimSuffix(operation.Path, "{id}") {
		return operation.OperationID
	}
	return ""
}

func (s *Server) submitProxy(w http.ResponseWriter) {
	status := s.plan.ProxySubmitStatus
	if status == 0 {
		status = http.StatusAccepted
	}
	if status != http.StatusAccepted {
		writeJSON(w, status, defaultError(s.plan.ProxySubmitError, "PROXY_REJECTED"))
		return
	}
	writeJSON(w, status, task(s.runtime.ProxyTaskID, "Update proxy configuration", "PENDING", nil))
}

func (s *Server) submitCeip(w http.ResponseWriter) {
	status := s.plan.CeipSubmitStatus
	if status == 0 {
		status = http.StatusAccepted
	}
	if status != http.StatusAccepted {
		writeJSON(w, status, defaultError(s.plan.CeipSubmitError, "CEIP_REJECTED"))
		return
	}
	writeJSON(w, status, task(s.runtime.CeipTaskID, "Set CEIP status", "PENDING", nil))
}

func (s *Server) getTask(w http.ResponseWriter, path string) {
	switch path {
	case "/v1/tasks/" + s.runtime.ProxyTaskID:
		reply := s.nextPoll(true)
		s.writePoll(w, s.runtime.ProxyTaskID, "Update proxy configuration", reply)
	case "/v1/tasks/" + s.runtime.CeipTaskID:
		reply := s.nextPoll(false)
		s.writePoll(w, s.runtime.CeipTaskID, "Set CEIP status", reply)
	default:
		writeJSON(w, http.StatusNotFound, VCFError{
			ErrorCode: "TASK_NOT_FOUND",
			Message:   "the requested task does not exist",
		})
	}
}

func (s *Server) nextPoll(proxy bool) PollReply {
	s.mu.Lock()
	defer s.mu.Unlock()
	polls := s.plan.CeipPolls
	index := s.ceipPolls
	if proxy {
		polls = s.plan.ProxyPolls
		index = s.proxyPolls
	}
	if len(polls) == 0 {
		return PollReply{TaskStatus: "SUCCESSFUL"}
	}
	if index >= len(polls) {
		index = len(polls) - 1
	} else if proxy {
		s.proxyPolls++
	} else {
		s.ceipPolls++
	}
	return polls[index]
}

func (s *Server) writePoll(w http.ResponseWriter, id, name string, reply PollReply) {
	status := reply.HTTPStatus
	if status == 0 {
		status = http.StatusOK
	}
	if status != http.StatusOK {
		writeJSON(w, status, defaultError(reply.APIError, "TASK_READ_FAILED"))
		return
	}
	taskStatus := reply.TaskStatus
	if taskStatus == "" {
		taskStatus = "SUCCESSFUL"
	}
	writeJSON(w, status, task(id, name, taskStatus, reply.Errors))
}

func (s *Server) record(request Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, request)
}

func task(id, name, status string, taskErrors []VCFError) map[string]any {
	value := map[string]any{
		"id":                id,
		"name":              name,
		"type":              "SYSTEM_CONFIGURATION_UPDATE",
		"status":            status,
		"creationTimestamp": "2026-07-28T12:00:00Z",
	}
	if taskErrors != nil {
		value["errors"] = taskErrors
	}
	return value
}

func defaultError(value VCFError, code string) VCFError {
	if value.ErrorCode == "" {
		value.ErrorCode = code
	}
	if value.Message == "" {
		value.Message = "the simulated SDDC Manager operation failed"
	}
	return value
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func randomValue(prefix string) string {
	var value [16]byte
	if _, err := rand.Read(value[:]); err != nil {
		panic("cannot generate loopback fixture value")
	}
	return prefix + "-" + hex.EncodeToString(value[:])
}
