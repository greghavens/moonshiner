// Package contractmock provides the protected loopback SDDC Manager fixture.
package contractmock

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"sync"
)

const (
	CreateToken        = "createToken"
	RefreshAccessToken = "refreshAccessToken"
	GetDomains         = "getDomains"
)

// Request is one request observed by the loopback server.
type Request struct {
	OperationID      string
	Method           string
	Path             string
	RawQuery         string
	Host             string
	Header           http.Header
	ContentLength    int64
	TransferEncoding []string
	Body             []byte
}

// Secrets contains per-server values generated at runtime.
type Secrets struct {
	Username       string
	Password       string
	AccessToken    string
	NewAccessToken string
	RefreshTokenID string
}

// Plan controls contract-valid response data and selected failure cases.
type Plan struct {
	Domains              []map[string]any
	CreateStatus         int
	RefreshStatus        int
	DomainStatus         int
	RejectRefreshedToken bool
	MutatePage           func(pageNumber int, payload map[string]any)
}

// Server is a contract-scoped loopback SDDC Manager.
type Server struct {
	httpServer *httptest.Server
	plan       Plan
	secrets    Secrets

	mu        sync.Mutex
	requests  []Request
	refreshed bool
}

// New starts a loopback server on an ephemeral address.
func New(plan Plan) *Server {
	server := &Server{
		plan: plan,
		secrets: Secrets{
			Username:       randomValue("user"),
			Password:       randomValue("password"),
			AccessToken:    randomValue("access-old"),
			NewAccessToken: randomValue("access-new"),
			RefreshTokenID: randomValue("refresh"),
		},
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		panic("cannot start loopback contract mock")
	}
	server.httpServer = &httptest.Server{
		Listener: listener,
		Config:   &http.Server{Handler: http.HandlerFunc(server.serveHTTP)},
	}
	server.httpServer.Start()
	return server
}

// Close stops the loopback server.
func (s *Server) Close() {
	s.httpServer.Close()
}

// URL returns the server origin.
func (s *Server) URL() string {
	return s.httpServer.URL
}

// Client returns the loopback server's HTTP client.
func (s *Server) Client() *http.Client {
	return s.httpServer.Client()
}

// Secrets returns the runtime-generated credential and token values.
func (s *Server) Secrets() Secrets {
	return s.secrets
}

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	out := make([]Request, len(s.requests))
	for i, request := range s.requests {
		out[i] = request
		out[i].Header = request.Header.Clone()
		out[i].TransferEncoding = append([]string(nil), request.TransferEncoding...)
		out[i].Body = append([]byte(nil), request.Body...)
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	operationID := operationFor(r.Method, r.URL.Path)
	s.record(Request{
		OperationID:      operationID,
		Method:           r.Method,
		Path:             r.URL.Path,
		RawQuery:         r.URL.RawQuery,
		Host:             r.Host,
		Header:           r.Header.Clone(),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
		Body:             append([]byte(nil), body...),
	})

	switch operationID {
	case CreateToken:
		s.createToken(w, r)
	case RefreshAccessToken:
		s.refreshAccessToken(w, r)
	case GetDomains:
		s.getDomains(w, r)
	default:
		writeJSON(w, http.StatusNotFound, map[string]any{
			"errorCode": "NOT_IN_CONTRACT",
			"message":   "the contract mock does not serve this operation",
		})
	}
}

func operationFor(method, path string) string {
	switch {
	case method == http.MethodPost && path == "/v1/tokens":
		return CreateToken
	case method == http.MethodPatch && path == "/v1/tokens/access-token/refresh":
		return RefreshAccessToken
	case method == http.MethodGet && path == "/v1/domains":
		return GetDomains
	default:
		return ""
	}
}

func (s *Server) createToken(w http.ResponseWriter, r *http.Request) {
	status := s.plan.CreateStatus
	if status == 0 {
		status = http.StatusCreated
	}
	if r.URL.RawQuery != "" {
		writeJSON(w, http.StatusBadRequest, errorEnvelope("TOKEN_QUERY"))
		return
	}
	writeJSON(w, status, map[string]any{
		"accessToken": s.secrets.AccessToken,
		"refreshToken": map[string]any{
			"id": s.secrets.RefreshTokenID,
		},
	})
}

func (s *Server) refreshAccessToken(w http.ResponseWriter, r *http.Request) {
	status := s.plan.RefreshStatus
	if status == 0 {
		status = http.StatusOK
	}
	if r.URL.RawQuery != "" {
		writeJSON(w, http.StatusBadRequest, errorEnvelope("REFRESH_QUERY"))
		return
	}
	if status == http.StatusOK {
		s.mu.Lock()
		s.refreshed = true
		s.mu.Unlock()
		writeJSON(w, status, s.secrets.NewAccessToken)
		return
	}
	writeJSON(w, status, map[string]any{
		"errorCode": "REFRESH_FAILED",
		"message":   "refresh failed for " + s.secrets.RefreshTokenID,
	})
}

func (s *Server) getDomains(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query()
	if len(query) != 2 || len(query["pageNumber"]) != 1 || len(query["pageSize"]) != 1 {
		writeJSON(w, http.StatusBadRequest, errorEnvelope("DOMAIN_QUERY"))
		return
	}
	pageNumber, pageErr := strconv.Atoi(query.Get("pageNumber"))
	pageSize, sizeErr := strconv.Atoi(query.Get("pageSize"))
	if pageErr != nil || sizeErr != nil || pageNumber < 0 || pageSize < 1 {
		writeJSON(w, http.StatusBadRequest, errorEnvelope("DOMAIN_PAGE"))
		return
	}

	authorization := r.Header.Get("Authorization")
	oldAuthorization := "Bearer " + s.secrets.AccessToken
	newAuthorization := "Bearer " + s.secrets.NewAccessToken

	s.mu.Lock()
	refreshed := s.refreshed
	s.mu.Unlock()

	switch {
	case authorization == oldAuthorization && pageNumber == 0 && !refreshed:
		// Page zero succeeds before the initial access token expires.
	case authorization == newAuthorization && refreshed && !s.plan.RejectRefreshedToken:
		// The interrupted and remaining pages use the refreshed access token.
	default:
		writeJSON(w, http.StatusUnauthorized, map[string]any{
			"errorCode": "ACCESS_TOKEN_EXPIRED",
			"message": "expired credential " + s.secrets.Password + " " +
				s.secrets.AccessToken + " " + s.secrets.NewAccessToken,
		})
		return
	}

	totalElements := len(s.plan.Domains)
	totalPages := 0
	if totalElements > 0 {
		totalPages = (totalElements + pageSize - 1) / pageSize
	}
	start := pageNumber * pageSize
	if start > totalElements {
		start = totalElements
	}
	end := start + pageSize
	if end > totalElements {
		end = totalElements
	}
	elements := make([]map[string]any, end-start)
	copy(elements, s.plan.Domains[start:end])
	payload := map[string]any{
		"elements": elements,
		"pageMetadata": map[string]any{
			"pageNumber":    pageNumber,
			"pageSize":      len(elements),
			"totalElements": totalElements,
			"totalPages":    totalPages,
		},
	}
	if s.plan.MutatePage != nil {
		s.plan.MutatePage(pageNumber, payload)
	}
	status := s.plan.DomainStatus
	if status == 0 {
		status = http.StatusOK
	}
	writeJSON(w, status, payload)
}

func (s *Server) record(request Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, request)
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func errorEnvelope(code string) map[string]any {
	return map[string]any{
		"errorCode": code,
		"message":   "request did not match the operation contract",
	}
}

func randomValue(prefix string) string {
	var bytes [16]byte
	if _, err := rand.Read(bytes[:]); err != nil {
		panic("cannot create loopback fixture secret")
	}
	return prefix + "-" + hex.EncodeToString(bytes[:])
}
