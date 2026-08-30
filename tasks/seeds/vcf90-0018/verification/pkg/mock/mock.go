// Package mock serves a loopback stand-in for the SDDC Manager REST API.
//
// The server is pinned to docs/contract.json: it routes only the operations
// the contract names, accepts only the query parameters the contract lists,
// and rejects anything else. Every request it sees is appended to a log the
// caller can read back.
package mock

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
)

// Config configures a mock server.
type Config struct {
	// ContractPath is the docs/contract.json the server is pinned to.
	ContractPath string
	// FixturePath is the JSON array of credentials the server pages over.
	FixturePath string
	// Username and Password are the only credentials createToken accepts.
	Username string
	Password string
	// AccessToken is the access token createToken hands out.
	AccessToken string
	// RefreshedToken is the access token refreshAccessToken hands out.
	RefreshedToken string
	// RefreshTokenID is the refresh token id createToken hands out and
	// refreshAccessToken expects back.
	RefreshTokenID string
	// ExpireAfter is how many getCredentials requests are answered 200 while
	// bearing AccessToken. Every later request bearing AccessToken is answered
	// 401.
	ExpireAfter int
}

// Request is one logged request.
type Request struct {
	// Index is the position of this request in the log.
	Index int
	// OperationID is the contract operation the request matched, or "" when it
	// matched none.
	OperationID string
	Method      string
	Path        string
	RawQuery    string
	Header      http.Header
	Body        []byte
	// Status is the status code the server answered with.
	Status int
}

func (r Request) clone() Request {
	out := r
	out.Header = r.Header.Clone()
	out.Body = append([]byte(nil), r.Body...)
	return out
}

// Server is a running mock.
type Server struct {
	cfg      Config
	routes   map[string]string // "METHOD PATH" -> operationId
	allowed  map[string]map[string]bool
	fixture  []credential
	listener net.Listener
	srv      *http.Server
	url      string

	mu       sync.Mutex
	log      []Request
	served   int  // getCredentials requests answered 200 with the initial token
	refresh  bool // a refresh has happened
	closed   bool
	closeErr error
}

type credential struct {
	ID                    string   `json:"id"`
	CredentialType        string   `json:"credentialType"`
	AccountType           string   `json:"accountType"`
	Username              string   `json:"username"`
	CreationTimestamp     string   `json:"creationTimestamp"`
	ModificationTimestamp string   `json:"modificationTimestamp"`
	Resource              resource `json:"resource"`
}

type resource struct {
	ResourceID   string   `json:"resourceId"`
	ResourceName string   `json:"resourceName"`
	ResourceType string   `json:"resourceType"`
	DomainNames  []string `json:"domainNames"`
}

type contractDoc struct {
	Operations []struct {
		OperationID     string `json:"operationId"`
		Method          string `json:"method"`
		Path            string `json:"path"`
		QueryParameters []struct {
			Name string `json:"name"`
		} `json:"queryParameters"`
	} `json:"operations"`
}

// New starts a mock server on the loopback interface.
func New(cfg Config) (*Server, error) {
	if cfg.ContractPath == "" {
		return nil, errors.New("mock: ContractPath is required")
	}
	if cfg.FixturePath == "" {
		return nil, errors.New("mock: FixturePath is required")
	}
	if cfg.AccessToken == "" || cfg.RefreshedToken == "" || cfg.RefreshTokenID == "" {
		return nil, errors.New("mock: AccessToken, RefreshedToken and RefreshTokenID are required")
	}

	routes, allowed, err := loadContract(cfg.ContractPath)
	if err != nil {
		return nil, err
	}
	fixture, err := loadFixture(cfg.FixturePath)
	if err != nil {
		return nil, err
	}

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("mock: listen: %w", err)
	}

	s := &Server{
		cfg:      cfg,
		routes:   routes,
		allowed:  allowed,
		fixture:  fixture,
		listener: ln,
		url:      "http://" + ln.Addr().String(),
	}
	s.srv = &http.Server{Handler: http.HandlerFunc(s.serve)}
	go func() { _ = s.srv.Serve(ln) }()
	return s, nil
}

func loadContract(path string) (map[string]string, map[string]map[string]bool, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, nil, fmt.Errorf("mock: read contract: %w", err)
	}
	var doc contractDoc
	if err := json.Unmarshal(raw, &doc); err != nil {
		return nil, nil, fmt.Errorf("mock: parse contract: %w", err)
	}
	if len(doc.Operations) == 0 {
		return nil, nil, errors.New("mock: contract names no operations")
	}
	routes := make(map[string]string, len(doc.Operations))
	allowed := make(map[string]map[string]bool, len(doc.Operations))
	for _, op := range doc.Operations {
		if op.OperationID == "" || op.Method == "" || op.Path == "" {
			return nil, nil, errors.New("mock: contract operation is missing operationId, method or path")
		}
		routes[strings.ToUpper(op.Method)+" "+op.Path] = op.OperationID
		names := make(map[string]bool, len(op.QueryParameters))
		for _, p := range op.QueryParameters {
			names[p.Name] = true
		}
		allowed[op.OperationID] = names
	}
	return routes, allowed, nil
}

func loadFixture(path string) ([]credential, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("mock: read fixture: %w", err)
	}
	var creds []credential
	if err := json.Unmarshal(raw, &creds); err != nil {
		return nil, fmt.Errorf("mock: parse fixture: %w", err)
	}
	return creds, nil
}

// URL is the base URL the server listens on.
func (s *Server) URL() string { return s.url }

// Requests returns a snapshot of the request log, in the order the requests
// arrived. Mutating the result does not affect the server.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.log))
	for i, r := range s.log {
		out[i] = r.clone()
	}
	return out
}

// Close shuts the server down.
func (s *Server) Close() {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return
	}
	s.closed = true
	s.mu.Unlock()
	_ = s.srv.Close()
}

// apiError is the Error schema of the specification, reduced to the fields the
// mock fills in.
type apiError struct {
	ErrorCode string `json:"errorCode"`
	ErrorType string `json:"errorType"`
	Message   string `json:"message"`
}

type pageMetadata struct {
	PageNumber    int `json:"pageNumber"`
	PageSize      int `json:"pageSize"`
	TotalElements int `json:"totalElements"`
	TotalPages    int `json:"totalPages"`
}

type pageOfCredential struct {
	Elements     []credential `json:"elements"`
	PageMetadata pageMetadata `json:"pageMetadata"`
}

type tokenPair struct {
	AccessToken  string       `json:"accessToken"`
	RefreshToken refreshToken `json:"refreshToken"`
}

type refreshToken struct {
	ID string `json:"id"`
}

// reply is what a handler decided to send back.
type reply struct {
	status int
	body   any
}

func errorReply(status int, code, message string) reply {
	return reply{status: status, body: apiError{
		ErrorCode: code,
		ErrorType: http.StatusText(status),
		Message:   message,
	}}
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		body = nil
	}
	_ = r.Body.Close()

	operationID := s.routes[r.Method+" "+r.URL.Path]

	var res reply
	switch operationID {
	case "":
		res = errorReply(http.StatusNotFound, "NOT_FOUND",
			fmt.Sprintf("%s %s is not part of the pinned contract", r.Method, r.URL.Path))
	case "createToken":
		res = s.createToken(body, r)
	case "refreshAccessToken":
		res = s.refreshAccessToken(body, r)
	case "getCredentials":
		res = s.getCredentials(r)
	default:
		res = errorReply(http.StatusNotImplemented, "NOT_IMPLEMENTED",
			fmt.Sprintf("operation %s is named by the contract but not served", operationID))
	}

	s.mu.Lock()
	s.log = append(s.log, Request{
		Index:       len(s.log),
		OperationID: operationID,
		Method:      r.Method,
		Path:        r.URL.Path,
		RawQuery:    r.URL.RawQuery,
		Header:      r.Header.Clone(),
		Body:        append([]byte(nil), body...),
		Status:      res.status,
	})
	s.mu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(res.status)
	if res.body != nil {
		_ = json.NewEncoder(w).Encode(res.body)
	}
}

func requireJSON(r *http.Request) *reply {
	if ct := r.Header.Get("Content-Type"); ct != "application/json" {
		res := errorReply(http.StatusBadRequest, "BAD_REQUEST",
			fmt.Sprintf("Content-Type %q, want application/json", ct))
		return &res
	}
	return nil
}

func (s *Server) createToken(body []byte, r *http.Request) reply {
	if bad := requireJSON(r); bad != nil {
		return *bad
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(body, &fields); err != nil {
		return errorReply(http.StatusBadRequest, "BAD_REQUEST", "body is not a TokenCreationSpec")
	}
	for name := range fields {
		switch name {
		case "username", "password", "apiKey", "idToken":
		default:
			return errorReply(http.StatusBadRequest, "BAD_REQUEST",
				fmt.Sprintf("TokenCreationSpec has no field %q", name))
		}
	}
	var spec struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if err := json.Unmarshal(body, &spec); err != nil {
		return errorReply(http.StatusBadRequest, "BAD_REQUEST", "body is not a TokenCreationSpec")
	}
	if spec.Username != s.cfg.Username || spec.Password != s.cfg.Password {
		return errorReply(http.StatusBadRequest, "BAD_CREDENTIALS", "username or password is incorrect")
	}
	return reply{status: http.StatusCreated, body: tokenPair{
		AccessToken:  s.cfg.AccessToken,
		RefreshToken: refreshToken{ID: s.cfg.RefreshTokenID},
	}}
}

func (s *Server) refreshAccessToken(body []byte, r *http.Request) reply {
	if bad := requireJSON(r); bad != nil {
		return *bad
	}
	var id string
	if err := json.Unmarshal(body, &id); err != nil {
		return errorReply(http.StatusBadRequest, "BAD_REQUEST",
			"body must be a JSON string holding the refresh token id")
	}
	if id != s.cfg.RefreshTokenID {
		return errorReply(http.StatusNotFound, "REFRESH_TOKEN_NOT_FOUND", "refresh token id is not known")
	}
	s.mu.Lock()
	s.refresh = true
	s.mu.Unlock()
	return reply{status: http.StatusOK, body: s.cfg.RefreshedToken}
}

func (s *Server) getCredentials(r *http.Request) reply {
	if bad := s.authorize(r); bad != nil {
		return *bad
	}
	values, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		return errorReply(http.StatusBadRequest, "BAD_REQUEST", "query string is malformed")
	}
	allowed := s.allowed["getCredentials"]
	for name, vals := range values {
		if !allowed[name] {
			return errorReply(http.StatusBadRequest, "BAD_REQUEST",
				fmt.Sprintf("getCredentials has no query parameter %q", name))
		}
		if len(vals) > 1 {
			return errorReply(http.StatusBadRequest, "BAD_REQUEST",
				fmt.Sprintf("query parameter %q is repeated", name))
		}
		if vals[0] == "" {
			return errorReply(http.StatusBadRequest, "BAD_REQUEST",
				fmt.Sprintf("query parameter %q was sent empty; omit it instead", name))
		}
	}

	pageNumber, bad := positiveInt(values, "pageNumber")
	if bad != nil {
		return *bad
	}
	pageSize, bad := positiveInt(values, "pageSize")
	if bad != nil {
		return *bad
	}

	matched := make([]credential, 0, len(s.fixture))
	for _, c := range s.fixture {
		if v := values.Get("resourceType"); v != "" && c.Resource.ResourceType != v {
			continue
		}
		if v := values.Get("resourceName"); v != "" && c.Resource.ResourceName != v {
			continue
		}
		if v := values.Get("accountType"); v != "" && c.AccountType != v {
			continue
		}
		if v := values.Get("domainName"); v != "" && !contains(c.Resource.DomainNames, v) {
			continue
		}
		matched = append(matched, c)
	}

	total := len(matched)
	totalPages := 1
	if pageSize > 0 {
		totalPages = (total + pageSize - 1) / pageSize
	}
	if total == 0 {
		totalPages = 0
	}

	elements := matched
	if pageSize > 0 {
		start := pageNumber * pageSize
		if start > total {
			start = total
		}
		end := start + pageSize
		if end > total {
			end = total
		}
		elements = matched[start:end]
	} else if pageNumber > 0 {
		elements = nil
	}

	return reply{status: http.StatusOK, body: pageOfCredential{
		Elements: elements,
		PageMetadata: pageMetadata{
			PageNumber:    pageNumber,
			PageSize:      len(elements),
			TotalElements: total,
			TotalPages:    totalPages,
		},
	}}
}

func (s *Server) authorize(r *http.Request) *reply {
	header := r.Header.Get("Authorization")
	token, ok := strings.CutPrefix(header, "Bearer ")
	if !ok || token == "" {
		res := errorReply(http.StatusUnauthorized, "UNAUTHENTICATED",
			"a Bearer access token is required")
		return &res
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	switch token {
	case s.cfg.RefreshedToken:
		return nil
	case s.cfg.AccessToken:
		if s.served >= s.cfg.ExpireAfter {
			res := errorReply(http.StatusUnauthorized, "TOKEN_EXPIRED", "the access token has expired")
			return &res
		}
		s.served++
		return nil
	default:
		res := errorReply(http.StatusUnauthorized, "UNAUTHENTICATED", "the access token is not recognised")
		return &res
	}
}

func positiveInt(values url.Values, name string) (int, *reply) {
	raw := values.Get(name)
	if raw == "" {
		return 0, nil
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n < 0 {
		res := errorReply(http.StatusBadRequest, "BAD_REQUEST",
			fmt.Sprintf("query parameter %q must be a non-negative number, got %q", name, raw))
		return 0, &res
	}
	return n, nil
}

func contains(haystack []string, needle string) bool {
	for _, s := range haystack {
		if s == needle {
			return true
		}
	}
	return false
}
