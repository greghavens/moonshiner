// Package contractmock provides the protected, contract-pinned loopback SDDC
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
	"os"
	"strings"
	"sync"
)

const (
	UpdateOrRotatePasswords = "updateOrRotatePasswords"
	GetCredentialsTask      = "getCredentialsTask"
)

// VCFError is the focused contract error shape.
type VCFError struct {
	ErrorCode          string `json:"errorCode,omitempty"`
	Message            string `json:"message,omitempty"`
	RemediationMessage string `json:"remediationMessage,omitempty"`
	ReferenceToken     string `json:"referenceToken,omitempty"`
}

// PollReply controls one getCredentialsTask response.
type PollReply struct {
	HTTPStatus               int
	TaskStatus               string
	TaskID                   string
	OmitNewPassword          bool
	DuplicateMatchingSubTask bool
	APIError                 VCFError
	TaskErrors               []VCFError
	SubTaskErrors            []VCFError
}

// Plan controls responses without hard-coding acceptance values in the mock.
type Plan struct {
	SubmitStatus     int
	SubmitTaskStatus string
	SubmitLocation   string
	OmitSubmitTaskID bool
	SubmitError      VCFError
	Polls            []PollReply
}

// Request is one request observed by the loopback mock.
type Request struct {
	OperationID      string
	Method           string
	Path             string
	RawQuery         string
	RequestURI       string
	Header           http.Header
	ContentLength    int64
	TransferEncoding []string
	Body             []byte
}

// RuntimeValues contains values independently generated for each mock.
type RuntimeValues struct {
	AccessToken     string
	CurrentPassword string
	NewPassword     string
	Username        string
	TaskID          string
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

	mu       sync.Mutex
	requests []Request
	polls    int
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
			AccessToken:     randomValue("access"),
			CurrentPassword: randomValue("old-password"),
			NewPassword:     randomValue("new-password"),
			Username:        randomValue("svc-user"),
			TaskID:          randomValue("credential task"),
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
		UpdateOrRotatePasswords: {
			OperationID: UpdateOrRotatePasswords,
			Method:      http.MethodPatch,
			Path:        "/v1/credentials",
		},
		GetCredentialsTask: {
			OperationID: GetCredentialsTask,
			Method:      http.MethodGet,
			Path:        "/v1/credentials/tasks/{id}",
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

// Runtime returns the generated values for this server.
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
		out[index].TransferEncoding = append(
			[]string(nil),
			request.TransferEncoding...,
		)
		out[index].Body = append([]byte(nil), request.Body...)
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	operationID := s.operationFor(r.Method, r.URL.Path)
	s.record(Request{
		OperationID:      operationID,
		Method:           r.Method,
		Path:             r.URL.Path,
		RawQuery:         r.URL.RawQuery,
		RequestURI:       r.RequestURI,
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
			Message:   "the selected operations have no query parameters",
		})
		return
	}

	switch operationID {
	case UpdateOrRotatePasswords:
		s.submit(w)
	case GetCredentialsTask:
		s.poll(w)
	}
}

func (s *Server) operationFor(method, path string) string {
	if operation, ok := s.allowed[UpdateOrRotatePasswords]; ok &&
		method == operation.Method &&
		path == operation.Path {
		return operation.OperationID
	}
	if operation, ok := s.allowed[GetCredentialsTask]; ok &&
		method == operation.Method {
		prefix := strings.TrimSuffix(operation.Path, "{id}")
		id := strings.TrimPrefix(path, prefix)
		if strings.HasPrefix(path, prefix) &&
			id != "" &&
			!strings.Contains(id, "/") {
			return operation.OperationID
		}
	}
	return ""
}

func (s *Server) submit(w http.ResponseWriter) {
	status := s.plan.SubmitStatus
	if status == 0 {
		status = http.StatusAccepted
	}
	if s.plan.SubmitLocation != "" {
		w.Header().Set("Location", s.plan.SubmitLocation)
	}
	if status != http.StatusAccepted {
		writeJSON(w, status, s.plan.SubmitError)
		return
	}
	taskStatus := s.plan.SubmitTaskStatus
	if taskStatus == "" {
		taskStatus = "SUCCESSFUL"
	}
	taskID := s.runtime.TaskID
	if s.plan.OmitSubmitTaskID {
		taskID = ""
	}
	writeJSON(w, status, map[string]any{
		"id":                taskID,
		"name":              "Rotate credentials",
		"type":              "ROTATE",
		"status":            taskStatus,
		"creationTimestamp": "2026-01-02T03:04:05Z",
	})
}

func (s *Server) poll(w http.ResponseWriter) {
	reply := s.nextPoll()
	status := reply.HTTPStatus
	if status == 0 {
		status = http.StatusOK
	}
	if status != http.StatusOK {
		writeJSON(w, status, reply.APIError)
		return
	}
	taskStatus := reply.TaskStatus
	if taskStatus == "" {
		taskStatus = "SUCCESSFUL"
	}
	taskID := reply.TaskID
	if taskID == "" {
		taskID = s.runtime.TaskID
	}
	newPassword := s.runtime.NewPassword
	if reply.OmitNewPassword {
		newPassword = ""
	}
	subTasks := []map[string]any{
		s.subTask(taskStatus, newPassword, reply.SubTaskErrors),
	}
	if reply.DuplicateMatchingSubTask {
		subTasks = append(
			subTasks,
			s.subTask(taskStatus, newPassword, reply.SubTaskErrors),
		)
	}
	writeJSON(w, status, map[string]any{
		"id":                  taskID,
		"name":                "Rotate credentials",
		"type":                "ROTATE",
		"creationTimestamp":   "2026-01-02T03:04:05Z",
		"completionTimestamp": "2026-01-02T03:04:06Z",
		"status":              taskStatus,
		"subTasks":            subTasks,
		"errors":              reply.TaskErrors,
	})
}

func (s *Server) subTask(
	status string,
	newPassword string,
	taskErrors []VCFError,
) map[string]any {
	return map[string]any{
		"id":                  randomValue("subtask"),
		"name":                "Rotate resource credential",
		"description":         "Rotate a resource account password",
		"creationTimestamp":   "2026-01-02T03:04:05Z",
		"completionTimestamp": "2026-01-02T03:04:06Z",
		"status":              status,
		"username":            s.runtime.Username,
		"newPassword":         newPassword,
		"errors":              taskErrors,
	}
}

func (s *Server) nextPoll() PollReply {
	s.mu.Lock()
	defer s.mu.Unlock()
	index := s.polls
	s.polls++
	if len(s.plan.Polls) == 0 {
		return PollReply{}
	}
	if index >= len(s.plan.Polls) {
		return s.plan.Polls[len(s.plan.Polls)-1]
	}
	return s.plan.Polls[index]
}

func (s *Server) record(request Request) {
	s.mu.Lock()
	s.requests = append(s.requests, request)
	s.mu.Unlock()
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func randomValue(prefix string) string {
	var value [12]byte
	if _, err := rand.Read(value[:]); err != nil {
		panic("cannot generate isolated mock value")
	}
	return prefix + "-" + hex.EncodeToString(value[:])
}
