// Package mock serves a loopback SDDC Manager pinned to docs/contract.json.
//
// Only the operations the contract names are routed; every request is logged,
// whatever the outcome, so a test can read back the exact wire shape a client
// put on the connection. Nothing here talks to a VMware endpoint.
package mock

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
)

// Config configures a mock SDDC Manager.
type Config struct {
	ContractPath string // docs/contract.json, the contract the server is pinned to
	FixturePath  string // the JSON array of hosts the server pages over
	Username     string // the only username createToken accepts
	Password     string // the only password createToken accepts
	AccessToken  string // the access token createToken hands out

	// RepeatBoundary makes the inventory page the way SDDC Manager does when
	// the server side has no stable ordering: every page after the first
	// starts with the last element of the page before it.
	RepeatBoundary bool
}

// Request is one logged request.
type Request struct {
	Index       int         // position in the log
	OperationID string      // the contract operation the request matched, "" when it matched none
	Method      string      //
	Path        string      //
	RawQuery    string      //
	Header      http.Header //
	Body        []byte      //
	Status      int         // the status the server answered with
}

// host is one fixture record: the bytes served back, plus the fields the
// contract's filters match on.
type host struct {
	raw    json.RawMessage
	fields hostFields
}

type hostFields struct {
	ID                    string `json:"id"`
	FQDN                  string `json:"fqdn"`
	Status                string `json:"status"`
	CompatibleStorageType string `json:"compatibleStorageType"`
	BundleRepoDatastore   string `json:"bundleRepoDatastore"`
	Domain                *struct {
		ID string `json:"id"`
	} `json:"domain"`
	Cluster *struct {
		ID string `json:"id"`
	} `json:"cluster"`
	Networkpool *struct {
		ID string `json:"id"`
	} `json:"networkpool"`
}

// Server is a running mock SDDC Manager.
type Server struct {
	cfg      Config
	contract *contract
	hosts    []host

	listener net.Listener
	http     *http.Server

	mu  sync.Mutex
	log []Request
}

// New reads the contract and the fixture and starts the server on an
// ephemeral loopback port.
func New(cfg Config) (*Server, error) {
	switch {
	case cfg.ContractPath == "":
		return nil, errors.New("mock: ContractPath is required")
	case cfg.FixturePath == "":
		return nil, errors.New("mock: FixturePath is required")
	case cfg.Username == "":
		return nil, errors.New("mock: Username is required")
	case cfg.Password == "":
		return nil, errors.New("mock: Password is required")
	case cfg.AccessToken == "":
		return nil, errors.New("mock: AccessToken is required")
	}

	c, err := loadContract(cfg.ContractPath)
	if err != nil {
		return nil, fmt.Errorf("mock: %w", err)
	}
	hosts, err := loadHosts(cfg.FixturePath)
	if err != nil {
		return nil, fmt.Errorf("mock: %w", err)
	}

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("mock: listen: %w", err)
	}

	s := &Server{cfg: cfg, contract: c, hosts: hosts, listener: ln}
	s.http = &http.Server{Handler: http.HandlerFunc(s.serve)}
	go func() { _ = s.http.Serve(ln) }()
	return s, nil
}

func loadHosts(path string) ([]host, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read fixture: %w", err)
	}
	var records []json.RawMessage
	if err := json.Unmarshal(raw, &records); err != nil {
		return nil, fmt.Errorf("decode fixture %s: %w", path, err)
	}
	hosts := make([]host, 0, len(records))
	for i, r := range records {
		var f hostFields
		if err := json.Unmarshal(r, &f); err != nil {
			return nil, fmt.Errorf("decode fixture %s element %d: %w", path, i, err)
		}
		hosts = append(hosts, host{raw: append(json.RawMessage(nil), r...), fields: f})
	}
	return hosts, nil
}

// URL is the base url the server answers on.
func (s *Server) URL() string {
	return "http://" + s.listener.Addr().String()
}

// Close stops the server.
func (s *Server) Close() {
	_ = s.http.Close()
}

// Requests returns the log in arrival order. The caller gets a snapshot:
// mutating it does not reach the server's copy.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.log))
	for i, r := range s.log {
		out[i] = r
		out[i].Header = r.Header.Clone()
		out[i].Body = append([]byte(nil), r.Body...)
	}
	return out
}

func (s *Server) record(r Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	r.Index = len(s.log)
	s.log = append(s.log, r)
}

// apiError is the specification's Error schema.
type apiError struct {
	ErrorCode string `json:"errorCode"`
	ErrorType string `json:"errorType"`
	Message   string `json:"message"`
}

type response struct {
	status int
	body   any
}

func fail(status int, code, message string) response {
	kind := "USER_INPUT_ERROR"
	if status >= 500 {
		kind = "INTERNAL_ERROR"
	}
	return response{status: status, body: apiError{ErrorCode: code, ErrorType: kind, Message: message}}
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	_ = r.Body.Close()

	logged := Request{
		Method:   r.Method,
		Path:     r.URL.Path,
		RawQuery: r.URL.RawQuery,
		Header:   r.Header.Clone(),
		Body:     body,
	}

	var resp response
	op, ok := s.contract.route(r.Method, r.URL.Path)
	if !ok {
		resp = fail(http.StatusNotFound, "NOT_FOUND",
			fmt.Sprintf("%s %s is not one of the operations this server is pinned to", r.Method, r.URL.Path))
	} else {
		logged.OperationID = op.OperationID
		switch op.OperationID {
		case "createToken":
			resp = s.createToken(op, r, body)
		case "getHosts":
			resp = s.getHosts(op, r)
		default:
			resp = fail(http.StatusNotFound, "NOT_FOUND", op.OperationID+" is not implemented by this server")
		}
	}

	logged.Status = resp.status
	s.record(logged)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.status)
	if resp.body != nil {
		_ = json.NewEncoder(w).Encode(resp.body)
	}
}

// tokenPair is the specification's TokenPair schema.
type tokenPair struct {
	AccessToken  string       `json:"accessToken"`
	RefreshToken refreshToken `json:"refreshToken"`
}

type refreshToken struct {
	ID string `json:"id"`
}

func (s *Server) createToken(op operation, r *http.Request, body []byte) response {
	want := "application/json"
	if op.RequestBody != nil && op.RequestBody.ContentType != "" {
		want = op.RequestBody.ContentType
	}
	mediaType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || mediaType != want {
		return fail(http.StatusBadRequest, "INVALID_CONTENT_TYPE",
			fmt.Sprintf("Content-Type %q, want %q", r.Header.Get("Content-Type"), want))
	}

	var fields map[string]json.RawMessage
	if err := json.Unmarshal(body, &fields); err != nil {
		return fail(http.StatusBadRequest, "INVALID_BODY", "request body is not a JSON object")
	}
	for name := range fields {
		if !op.allowsProperty(name) {
			return fail(http.StatusBadRequest, "INVALID_BODY",
				fmt.Sprintf("%q is not a property of the %s schema", name, op.RequestBody.Schema))
		}
	}

	var creds struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if err := json.Unmarshal(body, &creds); err != nil {
		return fail(http.StatusBadRequest, "INVALID_BODY", "request body is not a JSON object")
	}
	if creds.Username != s.cfg.Username || creds.Password != s.cfg.Password {
		return fail(http.StatusBadRequest, "INVALID_CREDENTIALS", "the credentials are not valid")
	}

	return response{
		status: op.SuccessStatus,
		body: tokenPair{
			AccessToken:  s.cfg.AccessToken,
			RefreshToken: refreshToken{ID: "9d0f4a1c-6b23-4f8e-9e0a-7c5d3b2a1f00"},
		},
	}
}

// pageMetadata is the specification's PageMetadata schema.
type pageMetadata struct {
	PageNumber    int `json:"pageNumber"`
	PageSize      int `json:"pageSize"`
	TotalElements int `json:"totalElements"`
	TotalPages    int `json:"totalPages"`
}

// pageOfHost is the specification's PageOfHost schema.
type pageOfHost struct {
	Elements     []json.RawMessage `json:"elements"`
	PageMetadata pageMetadata      `json:"pageMetadata"`
}

func (s *Server) getHosts(op operation, r *http.Request) response {
	token, ok := bearer(r.Header.Get("Authorization"))
	if !ok || token != s.cfg.AccessToken {
		return fail(http.StatusUnauthorized, "UNAUTHORIZED", "a valid Bearer access token is required")
	}

	values, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		return fail(http.StatusBadRequest, "INVALID_QUERY", "the query string is malformed")
	}
	for name, v := range values {
		if !op.hasQueryParameter(name) {
			return fail(http.StatusBadRequest, "INVALID_QUERY",
				fmt.Sprintf("%q is not a query parameter of %s in this revision of the API", name, op.OperationID))
		}
		if len(v) != 1 {
			return fail(http.StatusBadRequest, "INVALID_QUERY", fmt.Sprintf("%q is repeated", name))
		}
		if v[0] == "" {
			return fail(http.StatusBadRequest, "INVALID_QUERY",
				fmt.Sprintf("%q is present with an empty value; an unset optional parameter must be left out", name))
		}
	}

	page, err := nonNegative(values, s.contract.Pagination.PageParameter)
	if err != nil {
		return fail(http.StatusBadRequest, "INVALID_QUERY", err.Error())
	}
	size, err := nonNegative(values, s.contract.Pagination.SizeParameter)
	if err != nil {
		return fail(http.StatusBadRequest, "INVALID_QUERY", err.Error())
	}

	matched := s.filter(values)
	elements, meta := s.paginate(matched, page, size)
	return response{status: op.SuccessStatus, body: pageOfHost{Elements: elements, PageMetadata: meta}}
}

func bearer(header string) (string, bool) {
	const prefix = "Bearer "
	if !strings.HasPrefix(header, prefix) {
		return "", false
	}
	token := strings.TrimSpace(strings.TrimPrefix(header, prefix))
	return token, token != ""
}

func nonNegative(values url.Values, name string) (int, error) {
	raw := values.Get(name)
	if raw == "" {
		return 0, nil
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n < 0 {
		return 0, fmt.Errorf("%q must be a non-negative integer, got %q", name, raw)
	}
	return n, nil
}

func (s *Server) filter(values url.Values) []host {
	out := make([]host, 0, len(s.hosts))
	for _, h := range s.hosts {
		if !matches(h.fields, values) {
			continue
		}
		out = append(out, h)
	}
	return out
}

func matches(f hostFields, values url.Values) bool {
	reference := func(v *struct {
		ID string `json:"id"`
	}) string {
		if v == nil {
			return ""
		}
		return v.ID
	}
	for name, want := range map[string]string{
		"fqdn":          f.FQDN,
		"status":        f.Status,
		"storageType":   f.CompatibleStorageType,
		"datastoreName": f.BundleRepoDatastore,
		"domainId":      reference(f.Domain),
		"clusterId":     reference(f.Cluster),
		"networkpoolId": reference(f.Networkpool),
	} {
		if got := values.Get(name); got != "" && got != want {
			return false
		}
	}
	return true
}

// paginate cuts the requested window out of the matched hosts. An absent or
// zero size is a single page holding everything.
func (s *Server) paginate(matched []host, page, size int) ([]json.RawMessage, pageMetadata) {
	total := len(matched)

	window := matched
	totalPages := 0
	switch {
	case size <= 0:
		if total > 0 {
			totalPages = 1
		}
		if page > 0 {
			window = nil
		}
	default:
		totalPages = (total + size - 1) / size
		start := page * size
		if start >= total {
			window = nil
			break
		}
		end := start + size
		if end > total {
			end = total
		}
		if s.cfg.RepeatBoundary && start > 0 {
			start--
		}
		window = matched[start:end]
	}

	elements := make([]json.RawMessage, 0, len(window))
	for _, h := range window {
		elements = append(elements, h.raw)
	}
	return elements, pageMetadata{
		PageNumber:    page,
		PageSize:      len(elements),
		TotalElements: total,
		TotalPages:    totalPages,
	}
}
