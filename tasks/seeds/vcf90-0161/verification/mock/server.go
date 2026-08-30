package mock

import (
	"encoding/json"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"

	vcfautomation "example.com/vcfautomation"
)

type Request struct {
	Method        string
	RequestURI    string
	Authorization string
	ContentType   string
	Accept        string
	Body          []byte
}

type Server struct {
	httpServer *httptest.Server
	operation  vcfautomation.Operation

	mu        sync.RWMutex
	requests  []Request
	state     vcfautomation.Deployment
	mutations int
}

func New(initial vcfautomation.Deployment) (*Server, error) {
	contract, err := vcfautomation.Contract()
	if err != nil {
		return nil, err
	}
	if len(contract.Operations) != 1 {
		return nil, fmt.Errorf("mock: contract must name exactly one operation, got %d", len(contract.Operations))
	}

	server := &Server{
		operation: contract.Operations[0],
		state:     initial,
	}
	server.httpServer = httptest.NewServer(http.HandlerFunc(server.serveHTTP))
	return server, nil
}

func (s *Server) URL() string {
	return s.httpServer.URL
}

func (s *Server) Client() *http.Client {
	return s.httpServer.Client()
}

func (s *Server) Close() {
	s.httpServer.Close()
}

func (s *Server) Requests() []Request {
	s.mu.RLock()
	defer s.mu.RUnlock()

	requests := make([]Request, len(s.requests))
	for i, request := range s.requests {
		requests[i] = request
		requests[i].Body = append([]byte(nil), request.Body...)
	}
	return requests
}

func (s *Server) State() vcfautomation.Deployment {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.state
}

func (s *Server) Mutations() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.mutations
}

func (s *Server) serveHTTP(response http.ResponseWriter, request *http.Request) {
	body, err := io.ReadAll(request.Body)
	if err != nil {
		http.Error(response, "read body", http.StatusBadRequest)
		return
	}
	s.record(request, body)

	deploymentID, matches := matchOperation(s.operation, request)
	if !matches {
		http.NotFound(response, request)
		return
	}
	if request.Header.Get("Authorization") == "" || request.Header.Get("Authorization") == "Bearer" {
		http.Error(response, "unauthorized", http.StatusUnauthorized)
		return
	}
	mediaType, _, err := mime.ParseMediaType(request.Header.Get("Content-Type"))
	if err != nil || mediaType != s.operation.Request.ContentType {
		http.Error(response, "unsupported media type", http.StatusUnsupportedMediaType)
		return
	}

	update, err := decodeUpdate(body, s.operation.Request.Body.Properties)
	if err != nil {
		http.Error(response, err.Error(), http.StatusBadRequest)
		return
	}

	s.mu.Lock()
	if deploymentID != s.state.ID {
		s.mu.Unlock()
		http.NotFound(response, request)
		return
	}
	changed := applyUpdate(&s.state, update)
	if changed {
		s.mutations++
	}
	state := s.state
	s.mu.Unlock()

	response.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(response).Encode(state); err != nil {
		panic(err)
	}
}

func (s *Server) record(request *http.Request, body []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, Request{
		Method:        request.Method,
		RequestURI:    request.RequestURI,
		Authorization: request.Header.Get("Authorization"),
		ContentType:   request.Header.Get("Content-Type"),
		Accept:        request.Header.Get("Accept"),
		Body:          append([]byte(nil), body...),
	})
}

func matchOperation(operation vcfautomation.Operation, request *http.Request) (string, bool) {
	if request.Method != operation.Method || request.URL.RawQuery != "" {
		return "", false
	}
	const placeholder = "{deploymentId}"
	parts := strings.Split(operation.Path, placeholder)
	if len(parts) != 2 {
		return "", false
	}
	escapedPath := request.URL.EscapedPath()
	if !strings.HasPrefix(escapedPath, parts[0]) || !strings.HasSuffix(escapedPath, parts[1]) {
		return "", false
	}
	escapedID := strings.TrimSuffix(strings.TrimPrefix(escapedPath, parts[0]), parts[1])
	if escapedID == "" || strings.Contains(escapedID, "/") {
		return "", false
	}
	deploymentID, err := url.PathUnescape(escapedID)
	return deploymentID, err == nil
}

func decodeUpdate(body []byte, properties map[string]vcfautomation.PropertySchema) (vcfautomation.DeploymentUpdate, error) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(body, &fields); err != nil {
		return vcfautomation.DeploymentUpdate{}, fmt.Errorf("invalid JSON: %w", err)
	}
	update := vcfautomation.DeploymentUpdate{}
	for name, raw := range fields {
		property, ok := properties[name]
		if !ok || property.Type != "string" {
			return vcfautomation.DeploymentUpdate{}, fmt.Errorf("field %q is not in the contract", name)
		}
		var value string
		if err := json.Unmarshal(raw, &value); err != nil {
			return vcfautomation.DeploymentUpdate{}, fmt.Errorf("field %q must be a string", name)
		}
		switch name {
		case "description":
			update.Description = &value
		case "iconId":
			update.IconID = &value
		case "name":
			update.Name = &value
		}
	}
	return update, nil
}

func applyUpdate(state *vcfautomation.Deployment, update vcfautomation.DeploymentUpdate) bool {
	changed := false
	if update.Description != nil && state.Description != *update.Description {
		state.Description = *update.Description
		changed = true
	}
	if update.IconID != nil && state.IconID != *update.IconID {
		state.IconID = *update.IconID
		changed = true
	}
	if update.Name != nil && state.Name != *update.Name {
		state.Name = *update.Name
		changed = true
	}
	return changed
}
