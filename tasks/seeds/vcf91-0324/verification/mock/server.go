// Package mock is a loopback stand-in for a VCF Automation appliance, pinned
// to docs/contract.json.
//
// The mock is not a general HTTP fixture. It is driven from the contract and
// serves *only* what the contract names: a request whose method and path match
// no contract operation is a 404, a query parameter the operation does not
// declare is a 400, and a body field the operation does not declare is a 400.
// That is what makes it useful — a client that drifts from the documented wire
// shape fails against the mock instead of failing in production.
//
// It also keeps a log of every request it received, decoded, so that tests can
// assert on the exact bytes the client put on the wire. Every request is
// logged, including the ones it rejects.
//
// # Request handling order
//
// The server applies these steps in this order, and replies as soon as one of
// them fails:
//
//  1. Route. Match the request method and path against the contract's
//     operations, treating {placeholder} segments as wildcards. No match is
//     404 — including a known path reached with a method the contract does not
//     document for it, and including the token operation addressed to a tenant
//     other than Options.Tenant.
//  2. Validate. Reject a query parameter the matched operation does not
//     declare, or a body field it does not declare, with 400.
//  3. Authenticate. Every operation except the token operation requires
//     Authorization: Bearer <access token>. A missing, unrecognised or expired
//     token is 401.
//  4. Serve.
//
// Validating before authenticating is deliberate: it lets a test confirm that
// the contract is being enforced without first having to hold a token.
package mock

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"

	"vcfauto/contract"
)

// Options configures a Server.
type Options struct {
	// Contract is the loaded docs/contract.json. The server refuses to
	// serve anything it does not name.
	Contract *contract.Contract

	// Tenant is the organization the token operation is scoped to. A token
	// request for any other tenant is a 404.
	Tenant string

	// APIToken is the long-lived API token the server accepts in exchange
	// for an access token.
	APIToken string

	// Deployments and CatalogItems are the corpora the list and get
	// operations page over and look up. See Fixtures.
	Deployments  []map[string]any
	CatalogItems []map[string]any

	// ExpireAccessTokenAfter makes an issued access token stop being
	// accepted once it has authorized this many requests; the next request
	// bearing it gets a 401. Exchanging the API token again issues a fresh
	// access token and resets the count. Zero means tokens never expire.
	//
	// The count is deliberately request-based rather than clock-based so
	// that expiry lands in exactly the same place on every run.
	ExpireAccessTokenAfter int

	// AccessTokenTTLSeconds is reported as the token response's expires_in.
	// Defaults to 3600. It does not by itself cause expiry; see
	// ExpireAccessTokenAfter.
	AccessTokenTTLSeconds int
}

// route is one contract operation, pre-split for matching.
type route struct {
	op   *contract.Operation
	segs []string // path segments; a placeholder is stored as ""
	name []string // placeholder names, in the order they appear
}

// Server is a running loopback mock.
type Server struct {
	opts   Options
	routes []route
	http   *http.Server
	listen net.Listener
	url    string

	mu     sync.Mutex
	log    []*loggedRequest
	tokens map[string]int // access token -> requests it has authorized
	issued int
}

// loggedRequest reserves a request's position in the log as soon as its
// handler starts. Requests waits for done before copying record, so callers
// never observe a partially populated entry.
type loggedRequest struct {
	record RecordedRequest
	done   chan struct{}
}

// Start validates opts and starts a server listening on 127.0.0.1 on an
// ephemeral port. Call Close when done.
//
// What the operations serve:
//
//   - The token operation takes a form-encoded body. The grant type and the
//     presented token must be the ones the contract names, and the token must
//     equal Options.APIToken; otherwise 400. On success it issues a fresh
//     access token and replies with the response fields the contract declares,
//     reporting AccessTokenTTLSeconds as the lifetime.
//   - The list operations page over their corpus using the page and size
//     parameters the contract declares, falling back to the defaults the
//     contract records when the client omits them, and reply with the page
//     envelope the contract declares.
//   - The deployment get operation looks its ID up in the corpus, 404 if absent.
//   - The catalog request operation replies with one entry per requested
//     instance, echoing the requested deployment name.
func Start(opts Options) (*Server, error) {
	if opts.Contract == nil {
		return nil, errors.New("mock: Contract is required")
	}
	if strings.TrimSpace(opts.Tenant) == "" {
		return nil, errors.New("mock: Tenant is required")
	}
	if strings.TrimSpace(opts.APIToken) == "" {
		return nil, errors.New("mock: APIToken is required")
	}
	if opts.AccessTokenTTLSeconds == 0 {
		opts.AccessTokenTTLSeconds = 3600
	}
	for _, id := range contract.RequiredOperations {
		if _, err := opts.Contract.Operation(id); err != nil {
			return nil, fmt.Errorf("mock: %w", err)
		}
	}

	s := &Server{opts: opts, tokens: map[string]int{}}
	for i := range opts.Contract.Operations {
		op := &opts.Contract.Operations[i]
		r := route{op: op}
		for _, seg := range strings.Split(strings.TrimPrefix(op.Path, "/"), "/") {
			if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
				r.segs = append(r.segs, "")
				r.name = append(r.name, strings.Trim(seg, "{}"))
				continue
			}
			r.segs = append(r.segs, seg)
		}
		s.routes = append(s.routes, r)
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("mock: listen on loopback: %w", err)
	}
	s.listen = listener
	s.url = "http://" + listener.Addr().String()
	s.http = &http.Server{Handler: http.HandlerFunc(s.serve)}
	go func() {
		_ = s.http.Serve(listener)
	}()
	return s, nil
}

// URL is the server's base URL, with no trailing slash.
func (s *Server) URL() string {
	if s == nil || s.http == nil {
		return ""
	}
	return strings.TrimRight(s.url, "/")
}

// Close shuts the server down.
func (s *Server) Close() {
	if s != nil && s.http != nil {
		_ = s.http.Close()
	}
	if s != nil && s.listen != nil {
		_ = s.listen.Close()
	}
}

// RecordedRequest is one request as the server received it.
type RecordedRequest struct {
	// Seq is the 0-based order in which the server received the request.
	Seq int

	// Operation is the contract operation ID this request matched, or ""
	// if it matched none.
	Operation string

	Method string

	// Path is the request path, with path parameters still substituted —
	// i.e. what the client actually sent, not the template.
	Path string

	// Query is the parsed query string, exactly as sent. A parameter the
	// client omitted is absent from this map; a parameter the client sent
	// empty is present with an empty value. The difference is the point.
	Query url.Values

	// Header is the request header.
	Header http.Header

	// Body is the raw request body, unmodified.
	Body []byte

	// Status is the status code the server replied with.
	Status int
}

// JSONBody decodes the recorded body as a JSON object.
func (r RecordedRequest) JSONBody() (map[string]any, error) {
	var m map[string]any
	if err := json.Unmarshal(r.Body, &m); err != nil {
		return nil, err
	}
	return m, nil
}

// FormBody decodes the recorded body as form-encoded values.
func (r RecordedRequest) FormBody() (url.Values, error) {
	return url.ParseQuery(string(r.Body))
}

// Requests returns a copy of the request log, in the order received. It is
// safe to call while the server is serving.
func (s *Server) Requests() []RecordedRequest {
	s.mu.Lock()
	entries := append([]*loggedRequest(nil), s.log...)
	s.mu.Unlock()

	out := make([]RecordedRequest, len(entries))
	for i, entry := range entries {
		<-entry.done
		out[i] = entry.record
	}
	return out
}

// RequestsFor returns the logged requests that matched the given operation ID.
func (s *Server) RequestsFor(operation string) []RecordedRequest {
	var out []RecordedRequest
	for _, r := range s.Requests() {
		if r.Operation == operation {
			out = append(out, r)
		}
	}
	return out
}

// --- serving ----------------------------------------------------------------

// reply is what a handler decided to send.
type reply struct {
	status int
	body   any
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	entry := &loggedRequest{done: make(chan struct{})}
	s.mu.Lock()
	seq := len(s.log)
	s.log = append(s.log, entry)
	s.mu.Unlock()
	defer close(entry.done)

	raw, _ := io.ReadAll(r.Body)
	r.Body.Close()

	rec := RecordedRequest{
		Seq:       seq,
		Operation: "",
		Method:    r.Method,
		Path:      r.URL.Path,
		Query:     r.URL.Query(),
		Header:    r.Header.Clone(),
		Body:      raw,
	}

	op, pathParams, ok := s.match(r.Method, r.URL.Path)
	if ok {
		rec.Operation = op.ID
	}
	res := s.handle(op, ok, pathParams, r, raw)
	rec.Status = res.status
	entry.record = rec

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(res.status)
	if res.body != nil {
		_ = json.NewEncoder(w).Encode(res.body)
	}
}

func errBody(msg string) map[string]any {
	return map[string]any{"message": msg}
}

// match finds the operation a method and path address.
func (s *Server) match(method, path string) (*contract.Operation, map[string]string, bool) {
	got := strings.Split(strings.TrimPrefix(path, "/"), "/")
	for _, r := range s.routes {
		if r.op.Method != method || len(r.segs) != len(got) {
			continue
		}
		params := map[string]string{}
		n := 0
		matched := true
		for i, want := range r.segs {
			if want == "" {
				if got[i] == "" {
					matched = false
					break
				}
				params[r.name[n]] = got[i]
				n++
				continue
			}
			if want != got[i] {
				matched = false
				break
			}
		}
		if !matched {
			continue
		}
		// The token operation is scoped to one organization; a request
		// for any other one addresses a tenant this appliance does not
		// serve.
		if r.op.ID == contract.OpAuthToken {
			if len(r.name) != 1 || params[r.name[0]] != s.opts.Tenant {
				continue
			}
		}
		return r.op, params, true
	}
	return nil, nil, false
}

func (s *Server) handle(op *contract.Operation, routed bool, pathParams map[string]string, r *http.Request, raw []byte) reply {
	if !routed {
		return reply{http.StatusNotFound, errBody("no such operation")}
	}

	// 2. Validate against the contract.
	for name := range r.URL.Query() {
		if _, ok := op.QueryField(name); !ok {
			return reply{http.StatusBadRequest,
				errBody(fmt.Sprintf("operation %s declares no query parameter %q", op.ID, name))}
		}
	}
	var jsonBody map[string]any
	var formBody url.Values
	if op.RequestBody != nil {
		switch op.RequestBody.ContentType {
		case contract.ContentTypeJSON:
			if len(raw) > 0 {
				if err := json.Unmarshal(raw, &jsonBody); err != nil {
					return reply{http.StatusBadRequest, errBody("body is not a JSON object")}
				}
				if jsonBody == nil {
					return reply{http.StatusBadRequest, errBody("body is not a JSON object")}
				}
			}
			for name := range jsonBody {
				if _, ok := op.BodyField(name); !ok {
					return reply{http.StatusBadRequest,
						errBody(fmt.Sprintf("operation %s declares no body field %q", op.ID, name))}
				}
			}
		case contract.ContentTypeForm:
			var err error
			formBody, err = url.ParseQuery(string(raw))
			if err != nil {
				return reply{http.StatusBadRequest, errBody("body is not form-encoded")}
			}
			for name := range formBody {
				if _, ok := op.BodyField(name); !ok {
					return reply{http.StatusBadRequest,
						errBody(fmt.Sprintf("operation %s declares no body field %q", op.ID, name))}
				}
			}
		}
	}

	// 3. Authenticate, except for the operation that hands out tokens.
	if op.ID != contract.OpAuthToken {
		if res, ok := s.authorize(r); !ok {
			return res
		}
	}

	// 4. Serve.
	switch op.ID {
	case contract.OpAuthToken:
		return s.serveToken(op, formBody)
	case contract.OpDeploymentsList:
		return s.servePage(op, r.URL.Query(), s.opts.Deployments)
	case contract.OpCatalogItemsList:
		return s.servePage(op, r.URL.Query(), s.opts.CatalogItems)
	case contract.OpDeploymentsGet:
		return s.serveDeployment(pathParams)
	case contract.OpCatalogItemsRequest:
		return s.serveCatalogRequest(pathParams, jsonBody)
	}
	return reply{http.StatusNotImplemented, errBody("operation not served by this mock")}
}

// authorize checks the bearer token and, on success, spends one of its
// remaining authorizations.
func (s *Server) authorize(r *http.Request) (reply, bool) {
	auth := r.Header.Get("Authorization")
	if !strings.HasPrefix(auth, "Bearer ") {
		return reply{http.StatusUnauthorized, errBody("missing bearer token")}, false
	}
	tok := strings.TrimPrefix(auth, "Bearer ")

	s.mu.Lock()
	defer s.mu.Unlock()
	used, known := s.tokens[tok]
	if !known {
		return reply{http.StatusUnauthorized, errBody("unknown access token")}, false
	}
	if s.opts.ExpireAccessTokenAfter > 0 && used >= s.opts.ExpireAccessTokenAfter {
		return reply{http.StatusUnauthorized, errBody("access token has expired")}, false
	}
	s.tokens[tok] = used + 1
	return reply{}, true
}

func (s *Server) serveToken(op *contract.Operation, form url.Values) reply {
	grant := "refresh_token"
	if f, ok := op.BodyField("grant_type"); ok && f.Default != "" {
		grant = f.Default
	}
	if form.Get("grant_type") != grant {
		return reply{http.StatusBadRequest, errBody("unsupported grant_type")}
	}
	if form.Get("refresh_token") != s.opts.APIToken {
		return reply{http.StatusBadRequest, errBody("invalid API token")}
	}

	s.mu.Lock()
	s.issued++
	tok := fmt.Sprintf("access-token-%d", s.issued)
	s.tokens[tok] = 0
	s.mu.Unlock()

	return reply{http.StatusOK, map[string]any{
		"access_token": tok,
		"token_type":   "Bearer",
		"expires_in":   s.opts.AccessTokenTTLSeconds,
	}}
}

// intParam reads an integer query parameter, falling back to the default the
// contract records for it.
func intParam(op *contract.Operation, q url.Values, name string) (int, error) {
	values, present := q[name]
	raw := ""
	if present && len(values) > 0 {
		raw = values[0]
	}
	if !present {
		if f, ok := op.QueryField(name); ok && f.Default != "" {
			raw = f.Default
		}
	}
	if raw == "" {
		if present {
			return 0, fmt.Errorf("%s must not be empty", name)
		}
		return 0, nil
	}
	return strconv.Atoi(raw)
}

func (s *Server) servePage(op *contract.Operation, q url.Values, corpus []map[string]any) reply {
	page, err := intParam(op, q, "page")
	if err != nil || page < 0 {
		return reply{http.StatusBadRequest, errBody("page must be a non-negative integer")}
	}
	size, err := intParam(op, q, "size")
	if err != nil || size <= 0 {
		return reply{http.StatusBadRequest, errBody("size must be a positive integer")}
	}

	total := len(corpus)
	totalPages := (total + size - 1) / size
	start := page * size
	if start > total {
		start = total
	}
	end := start + size
	if end > total {
		end = total
	}
	content := corpus[start:end]

	return reply{http.StatusOK, map[string]any{
		"content":          content,
		"number":           page,
		"size":             size,
		"numberOfElements": len(content),
		"totalElements":    total,
		"totalPages":       totalPages,
		"first":            page == 0,
		"last":             page >= totalPages-1,
		"empty":            len(content) == 0,
	}}
}

func (s *Server) serveDeployment(pathParams map[string]string) reply {
	id := singleParam(pathParams)
	for _, d := range s.opts.Deployments {
		if d["id"] == id {
			return reply{http.StatusOK, d}
		}
	}
	return reply{http.StatusNotFound, errBody("no such deployment")}
}

func (s *Server) serveCatalogRequest(pathParams map[string]string, body map[string]any) reply {
	id := singleParam(pathParams)
	var item map[string]any
	for _, ci := range s.opts.CatalogItems {
		if ci["id"] == id {
			item = ci
			break
		}
	}
	if item == nil {
		return reply{http.StatusNotFound, errBody("no such catalog item")}
	}

	count := 1
	if v, ok := body["bulkRequestCount"]; ok {
		if n, ok := v.(float64); ok && int(n) > 0 {
			count = int(n)
		}
	}
	name, _ := body["deploymentName"].(string)
	if name == "" {
		name, _ = item["name"].(string)
	}

	out := make([]map[string]any, 0, count)
	for i := 0; i < count; i++ {
		suffix := ""
		if count > 1 {
			suffix = fmt.Sprintf("-%d", i)
		}
		out = append(out, map[string]any{
			"deploymentId":   fmt.Sprintf("%s-req-%d", id, i),
			"deploymentName": name + suffix,
		})
	}
	return reply{http.StatusOK, out}
}

func singleParam(params map[string]string) string {
	for _, v := range params {
		return v
	}
	return ""
}
