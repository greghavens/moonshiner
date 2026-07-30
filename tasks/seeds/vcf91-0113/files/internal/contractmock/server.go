// Package contractmock provides the protected, contract-pinned loopback
// vCenter used by the acceptance tests.
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
	CPUUpdate    = "Vcenter.Vm.Hardware.Cpu_update"
	MemoryUpdate = "Vcenter.Vm.Hardware.Memory_update"
	PowerStart   = "Vcenter.Vm.Power_start"
)

var operationOrder = []string{CPUUpdate, MemoryUpdate, PowerStart}

// LocalizableMessage is the focused vAPI message shape.
type LocalizableMessage struct {
	Args           []string `json:"args"`
	DefaultMessage string   `json:"default_message"`
	ID             string   `json:"id"`
}

// VAPIError is the focused standard vAPI error envelope.
type VAPIError struct {
	ErrorType string               `json:"error_type"`
	Messages  []LocalizableMessage `json:"messages"`
}

// Reply overrides the default response for one named operation.
type Reply struct {
	Status      int
	Body        any
	RawBody     []byte
	ContentType string
}

// Plan controls operation responses. Missing entries use the workflow defaults:
// both updates return 204 and power start returns a standard vAPI 503.
type Plan struct {
	Replies map[string]Reply
}

// Request is one request observed by the loopback mock.
type Request struct {
	OperationID      string
	Method           string
	RequestURI       string
	Path             string
	RawQuery         string
	Header           http.Header
	ContentLength    int64
	TransferEncoding []string
	Body             []byte
	Status           int
}

// RuntimeValues contains values generated independently for each mock.
type RuntimeValues struct {
	SessionToken   string
	VM             string
	CPUCount       int64
	MemoryMiB      int64
	FailureMessage string
}

type contractOperation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

type renderedOperation struct {
	contractOperation
	RequestURI string
	PathOnly   string
	RawQuery   string
	Index      int
}

// Server is a loopback-only server scoped to the operations in contract.json.
type Server struct {
	httpServer *httptest.Server
	runtime    RuntimeValues
	operations []renderedOperation
	replies    map[string]Reply

	mu       sync.Mutex
	requests []Request
	next     int
}

// New loads the focused contract and starts an IPv4 loopback server.
func New(contractPath string, plan Plan) (*Server, error) {
	operations, err := loadOperations(contractPath)
	if err != nil {
		return nil, err
	}
	random := randomBytes()
	runtime := RuntimeValues{
		SessionToken:   "session-" + hex.EncodeToString(random[:12]),
		VM:             "vm /prod?#%✓-" + hex.EncodeToString(random[12:18]),
		CPUCount:       int64(2 + random[18]%15),
		MemoryMiB:      int64(4+random[19]%13) * 1024,
		FailureMessage: "power service unavailable " + hex.EncodeToString(random[20:]),
	}
	rendered, err := renderOperations(operations, runtime.VM)
	if err != nil {
		return nil, err
	}
	replies := make(map[string]Reply, len(plan.Replies))
	for operationID, reply := range plan.Replies {
		if !containsOperation(operationID) {
			return nil, errors.New("response plan names an operation outside the focused contract")
		}
		reply.RawBody = append([]byte(nil), reply.RawBody...)
		replies[operationID] = reply
	}
	server := &Server{
		runtime:    runtime,
		operations: rendered,
		replies:    replies,
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

func loadOperations(path string) ([]contractOperation, error) {
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
	required := []contractOperation{
		{OperationID: CPUUpdate, Method: http.MethodPatch, Path: "/api/vcenter/vm/{vm}/hardware/cpu"},
		{OperationID: MemoryUpdate, Method: http.MethodPatch, Path: "/api/vcenter/vm/{vm}/hardware/memory"},
		{OperationID: PowerStart, Method: http.MethodPost, Path: "/api/vcenter/vm/{vm}/power?action=start"},
	}
	if len(contract.Operations) != len(required) {
		return nil, errors.New("focused contract operation set is not pinned")
	}
	for index, want := range required {
		if contract.Operations[index] != want {
			return nil, errors.New("focused contract operation does not match pinned route")
		}
	}
	return contract.Operations, nil
}

func renderOperations(operations []contractOperation, vm string) ([]renderedOperation, error) {
	rendered := make([]renderedOperation, 0, len(operations))
	for index, operation := range operations {
		requestURI := strings.Replace(operation.Path, "{vm}", url.PathEscape(vm), 1)
		if strings.Contains(requestURI, "{vm}") {
			return nil, errors.New("focused contract path template is invalid")
		}
		parsed, err := url.ParseRequestURI(requestURI)
		if err != nil {
			return nil, errors.New("focused contract path cannot be rendered")
		}
		rendered = append(rendered, renderedOperation{
			contractOperation: operation,
			RequestURI:        requestURI,
			PathOnly:          parsed.Path,
			RawQuery:          parsed.RawQuery,
			Index:             index,
		})
	}
	return rendered, nil
}

func containsOperation(operationID string) bool {
	for _, candidate := range operationOrder {
		if candidate == operationID {
			return true
		}
	}
	return false
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

// Runtime returns the per-server generated scenario values.
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

func (s *Server) serveHTTP(w http.ResponseWriter, request *http.Request) {
	body, _ := io.ReadAll(request.Body)
	operation := s.operationFor(request.Method, request.RequestURI)

	s.mu.Lock()
	sequenceValid := operation != nil && operation.Index == s.next
	status, responseBody, contentType := s.reply(operation, sequenceValid)
	if sequenceValid && status != http.StatusConflict && status != http.StatusNotFound {
		s.next++
	}
	operationID := ""
	if operation != nil {
		operationID = operation.OperationID
	}
	s.requests = append(s.requests, Request{
		OperationID:      operationID,
		Method:           request.Method,
		RequestURI:       request.RequestURI,
		Path:             request.URL.Path,
		RawQuery:         request.URL.RawQuery,
		Header:           request.Header.Clone(),
		ContentLength:    request.ContentLength,
		TransferEncoding: append([]string(nil), request.TransferEncoding...),
		Body:             append([]byte(nil), body...),
		Status:           status,
	})
	s.mu.Unlock()

	if contentType != "" {
		w.Header().Set("Content-Type", contentType)
	}
	w.WriteHeader(status)
	if len(responseBody) != 0 {
		_, _ = w.Write(responseBody)
	}
}

func (s *Server) operationFor(method, requestURI string) *renderedOperation {
	for index := range s.operations {
		operation := &s.operations[index]
		if method == operation.Method && requestURI == operation.RequestURI {
			return operation
		}
	}
	return nil
}

func (s *Server) reply(
	operation *renderedOperation,
	sequenceValid bool,
) (int, []byte, string) {
	if operation == nil {
		return encodeReply(http.StatusNotFound, VAPIError{
			ErrorType: "NOT_FOUND",
			Messages: []LocalizableMessage{{
				Args:           []string{},
				DefaultMessage: "the focused contract does not serve this operation",
				ID:             "contract.not_found",
			}},
		}, nil, "application/json")
	}
	if !sequenceValid {
		return encodeReply(http.StatusConflict, VAPIError{
			ErrorType: "NOT_ALLOWED_IN_CURRENT_STATE",
			Messages: []LocalizableMessage{{
				Args:           []string{},
				DefaultMessage: "operation was invoked out of contract order",
				ID:             "contract.order",
			}},
		}, nil, "application/json")
	}
	if override, ok := s.replies[operation.OperationID]; ok {
		status := override.Status
		if status == 0 {
			status = http.StatusNoContent
		}
		contentType := override.ContentType
		if contentType == "" && status != http.StatusNoContent {
			contentType = "application/json"
		}
		return encodeReply(status, override.Body, override.RawBody, contentType)
	}
	if operation.OperationID != PowerStart {
		return http.StatusNoContent, nil, ""
	}
	return encodeReply(http.StatusServiceUnavailable, VAPIError{
		ErrorType: "SERVICE_UNAVAILABLE",
		Messages: []LocalizableMessage{{
			Args:           []string{},
			DefaultMessage: s.runtime.FailureMessage,
			ID:             "com.vmware.vcenter.power.unavailable",
		}},
	}, nil, "application/json")
}

func encodeReply(status int, body any, raw []byte, contentType string) (int, []byte, string) {
	if raw != nil {
		return status, append([]byte(nil), raw...), contentType
	}
	if body == nil {
		return status, nil, contentType
	}
	encoded, err := json.Marshal(body)
	if err != nil {
		panic("contract mock reply is not JSON encodable")
	}
	return status, encoded, contentType
}

func randomBytes() [32]byte {
	var value [32]byte
	if _, err := rand.Read(value[:]); err != nil {
		panic("cannot generate loopback fixture values")
	}
	return value
}
