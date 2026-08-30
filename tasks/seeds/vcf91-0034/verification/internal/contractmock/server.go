// Package contractmock provides the protected, contract-pinned loopback
// SDDC Manager used by the acceptance tests.
package contractmock

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"sync"
)

const (
	GetDomains         = "getDomains"
	GetTasks           = "getTasks"
	RefreshAccessToken = "refreshAccessToken"
)

// Domain is one fixture getDomains element.
type Domain struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Status string `json:"status,omitempty"`
	Type   string `json:"type,omitempty"`
}

// Task is one fixture getTasks element.
type Task struct {
	ID                string `json:"id"`
	Name              string `json:"name"`
	Type              string `json:"type,omitempty"`
	Status            string `json:"status"`
	CreationTimestamp string `json:"creationTimestamp"`
}

// Plan selects contract-valid and selected failure responses.
type Plan struct {
	DomainStatus         int
	TaskStatus           int
	RefreshStatus        int
	RejectRefreshedToken bool
	RefreshTokenValue    *string
	MutateDomains        func(map[string]any)
	MutateTasks          func(map[string]any)
}

// Request is one request captured by the race-safe log.
type Request struct {
	OperationID      string
	Method           string
	Path             string
	EscapedPath      string
	RawQuery         string
	ForceQuery       bool
	Host             string
	Header           http.Header
	ContentLength    int64
	TransferEncoding []string
	Body             []byte
}

// RuntimeValues contains values generated independently for each server.
type RuntimeValues struct {
	AccessToken    string
	NewAccessToken string
	RefreshTokenID string
	Domains        []Domain
	Tasks          []Task
}

type contractOperation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

// Server is an IPv4-loopback-only mock scoped to contract.json.
type Server struct {
	httpServer *httptest.Server
	plan       Plan
	runtime    RuntimeValues
	allowed    map[string]contractOperation

	mu            sync.Mutex
	requests      []Request
	oldExpired    bool
	refreshed     bool
	domainReplies int
	taskReplies   int
}

// New loads the focused contract and starts an ephemeral loopback server.
func New(contractPath string, plan Plan) (*Server, error) {
	allowed, err := loadOperations(contractPath)
	if err != nil {
		return nil, err
	}
	server := &Server{
		plan:    plan,
		allowed: allowed,
		runtime: RuntimeValues{
			AccessToken:    randomValue("access-old"),
			NewAccessToken: randomValue("access-new"),
			RefreshTokenID: randomValue("refresh"),
			Domains: []Domain{
				{
					ID: randomValue("domain-atlas"), Name: "Atlas",
					Status: "ACTIVE", Type: "MANAGEMENT",
				},
				{
					ID: randomValue("domain-mistral"), Name: "Mistral",
					Status: "ACTIVE", Type: "VI",
				},
				{
					ID: randomValue("domain-zephyr"), Name: "Zephyr",
					Status: "ACTIVE", Type: "VI",
				},
			},
			Tasks: []Task{
				{
					ID: randomValue("task-atlas"), Name: "Atlas audit",
					Type: "AUDIT", Status: "SUCCESSFUL",
					CreationTimestamp: "2026-07-28T10:00:00Z",
				},
				{
					ID: randomValue("task-mistral"), Name: "Mistral inventory",
					Type: "INVENTORY", Status: "SUCCESSFUL",
					CreationTimestamp: "2026-07-28T10:01:00Z",
				},
				{
					ID: randomValue("task-zephyr"), Name: "Zephyr health",
					Type: "HEALTH", Status: "IN_PROGRESS",
					CreationTimestamp: "2026-07-28T10:02:00Z",
				},
			},
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
		GetDomains: {
			OperationID: GetDomains,
			Method:      http.MethodGet,
			Path:        "/v1/domains",
		},
		GetTasks: {
			OperationID: GetTasks,
			Method:      http.MethodGet,
			Path:        "/v1/tasks",
		},
		RefreshAccessToken: {
			OperationID: RefreshAccessToken,
			Method:      http.MethodPatch,
			Path:        "/v1/tokens/access-token/refresh",
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

// Client returns the server's HTTP client.
func (s *Server) Client() *http.Client {
	return s.httpServer.Client()
}

// Runtime returns a deep copy of per-server generated data.
func (s *Server) Runtime() RuntimeValues {
	out := s.runtime
	out.Domains = append([]Domain(nil), s.runtime.Domains...)
	out.Tasks = append([]Task(nil), s.runtime.Tasks...)
	return out
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

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	operationID := s.operationFor(r.Method, r.URL.Path)
	s.record(Request{
		OperationID:      operationID,
		Method:           r.Method,
		Path:             r.URL.Path,
		EscapedPath:      r.URL.EscapedPath(),
		RawQuery:         r.URL.RawQuery,
		ForceQuery:       r.URL.ForceQuery,
		Host:             r.Host,
		Header:           r.Header.Clone(),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
		Body:             append([]byte(nil), body...),
	})

	switch operationID {
	case GetDomains:
		s.getDomains(w, r)
	case GetTasks:
		s.getTasks(w, r)
	case RefreshAccessToken:
		s.refreshAccessToken(w, r, body)
	default:
		writeJSON(w, http.StatusNotFound, s.errorEnvelope("NOT_IN_CONTRACT"))
	}
}

func (s *Server) operationFor(method, path string) string {
	for _, id := range []string{GetDomains, GetTasks, RefreshAccessToken} {
		operation := s.allowed[id]
		if method == operation.Method && path == operation.Path {
			return id
		}
	}
	return ""
}

func (s *Server) getDomains(w http.ResponseWriter, r *http.Request) {
	if r.URL.RawQuery != "pageNumber=0&pageSize=100" || r.URL.ForceQuery {
		writeJSON(w, http.StatusBadRequest, s.errorEnvelope("DOMAIN_QUERY"))
		return
	}
	if !s.authorizeCollection(w, r) {
		return
	}

	status := s.plan.DomainStatus
	if status == 0 {
		status = http.StatusOK
	}
	if status != http.StatusOK {
		writeJSON(w, status, s.errorEnvelope("DOMAIN_FAILED"))
		return
	}
	elements := append([]Domain(nil), s.runtime.Domains...)
	s.mu.Lock()
	if s.domainReplies%2 == 0 {
		reverse(elements)
	}
	s.domainReplies++
	if r.Header.Get("Authorization") == "Bearer "+s.runtime.AccessToken {
		s.oldExpired = true
	}
	s.mu.Unlock()

	payload := pagePayload(elements)
	if s.plan.MutateDomains != nil {
		s.plan.MutateDomains(payload)
	}
	writeJSON(w, status, payload)
}

func (s *Server) getTasks(w http.ResponseWriter, r *http.Request) {
	if r.URL.RawQuery != "pageNumber=0&pageSize=100" || r.URL.ForceQuery {
		writeJSON(w, http.StatusBadRequest, s.errorEnvelope("TASK_QUERY"))
		return
	}
	if !s.authorizeCollection(w, r) {
		return
	}

	status := s.plan.TaskStatus
	if status == 0 {
		status = http.StatusOK
	}
	if status != http.StatusOK {
		writeJSON(w, status, s.errorEnvelope("TASK_FAILED"))
		return
	}
	elements := append([]Task(nil), s.runtime.Tasks...)
	s.mu.Lock()
	if s.taskReplies%2 == 0 {
		reverse(elements)
	}
	s.taskReplies++
	s.mu.Unlock()

	payload := pagePayload(elements)
	if s.plan.MutateTasks != nil {
		s.plan.MutateTasks(payload)
	}
	writeJSON(w, status, payload)
}

func (s *Server) authorizeCollection(w http.ResponseWriter, r *http.Request) bool {
	authorization := r.Header.Get("Authorization")
	s.mu.Lock()
	oldExpired := s.oldExpired
	refreshed := s.refreshed
	s.mu.Unlock()

	oldAccepted := authorization == "Bearer "+s.runtime.AccessToken && !oldExpired
	newAccepted := authorization == "Bearer "+s.runtime.NewAccessToken &&
		refreshed &&
		!s.plan.RejectRefreshedToken
	if oldAccepted || newAccepted {
		return true
	}
	writeJSON(w, http.StatusUnauthorized, s.errorEnvelope("ACCESS_TOKEN_EXPIRED"))
	return false
}

func (s *Server) refreshAccessToken(w http.ResponseWriter, r *http.Request, body []byte) {
	if r.URL.RawQuery != "" || r.URL.ForceQuery {
		writeJSON(w, http.StatusBadRequest, s.errorEnvelope("REFRESH_QUERY"))
		return
	}
	wantBody, _ := json.Marshal(s.runtime.RefreshTokenID)
	if !bytes.Equal(body, wantBody) {
		writeJSON(w, http.StatusBadRequest, s.errorEnvelope("REFRESH_BODY"))
		return
	}

	status := s.plan.RefreshStatus
	if status == 0 {
		status = http.StatusOK
	}
	if status != http.StatusOK {
		writeJSON(w, status, s.errorEnvelope("REFRESH_FAILED"))
		return
	}
	s.mu.Lock()
	s.refreshed = true
	s.mu.Unlock()
	value := s.runtime.NewAccessToken
	if s.plan.RefreshTokenValue != nil {
		value = *s.plan.RefreshTokenValue
	}
	writeJSON(w, status, value)
}

func (s *Server) errorEnvelope(code string) map[string]any {
	return map[string]any{
		"errorCode": code,
		"message": "request failed with " +
			s.runtime.AccessToken + " " +
			s.runtime.NewAccessToken + " " +
			s.runtime.RefreshTokenID,
		"remediationMessage": "replace the secret credential",
		"referenceToken":     randomValue("reference"),
	}
}

func (s *Server) record(request Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, request)
}

func pagePayload[T any](elements []T) map[string]any {
	totalPages := 0
	if len(elements) > 0 {
		totalPages = 1
	}
	return map[string]any{
		"elements": elements,
		"pageMetadata": map[string]any{
			"pageNumber":    0,
			"pageSize":      len(elements),
			"totalElements": len(elements),
			"totalPages":    totalPages,
		},
	}
}

func reverse[T any](values []T) {
	for left, right := 0, len(values)-1; left < right; left, right = left+1, right-1 {
		values[left], values[right] = values[right], values[left]
	}
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func randomValue(prefix string) string {
	var value [16]byte
	if _, err := rand.Read(value[:]); err != nil {
		panic("cannot create loopback fixture value")
	}
	return prefix + "-" + hex.EncodeToString(value[:])
}
