// Package mockvc is a loopback double of the vCenter Automation API, pinned to
// docs/contract.json. It listens on 127.0.0.1 and serves only the four
// operations the contract names:
//
//	Cis.Session_create              POST   /api/session
//	Cis.Session_delete              DELETE /api/session
//	Vcenter.Tagging.Categories_list GET    /api/vcenter/tagging/categories
//	Vcenter.Tagging.Tags_list       GET    /api/vcenter/tagging/tags
//
// Anything else answers 404 with a Vapi.Std.Errors.Error body, including
// operations that exist in the specification but are outside the contract.
//
// Every request that reaches the server is appended to a log that tests read
// back with Requests. The double enforces the contract's query serialization
// rules, so a client that sends an unset optional field as an empty value
// (marker=, page_size=, names=) is answered 400 rather than quietly tolerated.
//
// This file is part of the protected harness. Do not modify it.
package mockvc

import (
	"crypto/subtle"
	_ "embed"
	"encoding/base64"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"sync"
)

//go:embed inventory.json
var inventoryJSON []byte

// CategoryInfo mirrors Vcenter.Tagging.Categories.Info.
type CategoryInfo struct {
	Name            string   `json:"name"`
	Description     string   `json:"description"`
	Cardinality     string   `json:"cardinality"`
	AssociableTypes []string `json:"associable_types"`
	UsedBy          []string `json:"used_by"`
}

// CategoryRecord mirrors Vcenter.Tagging.Categories.ListItem.
type CategoryRecord struct {
	CategoryID string       `json:"category_id"`
	Info       CategoryInfo `json:"info"`
}

// TagInfo mirrors Vcenter.Tagging.Tags.Info.
type TagInfo struct {
	Name        string   `json:"name"`
	Category    string   `json:"category"`
	Description string   `json:"description"`
	UsedBy      []string `json:"used_by"`
}

// TagRecord mirrors Vcenter.Tagging.Tags.ListItem.
type TagRecord struct {
	Tag  string  `json:"tag"`
	Info TagInfo `json:"info"`
}

// Dataset is the tagging inventory the double serves.
type Dataset struct {
	Categories []CategoryRecord `json:"categories"`
	Tags       []TagRecord      `json:"tags"`
}

// Clone returns a deep enough copy that callers cannot mutate the server's view.
func (d Dataset) Clone() Dataset {
	out := Dataset{
		Categories: append([]CategoryRecord(nil), d.Categories...),
		Tags:       append([]TagRecord(nil), d.Tags...),
	}
	return out
}

// Inventory returns the canned tagging inventory shipped with the harness.
func Inventory() Dataset {
	var d Dataset
	if err := json.Unmarshal(inventoryJSON, &d); err != nil {
		panic("mockvc: embedded inventory.json is invalid: " + err.Error())
	}
	return d
}

// Default credentials and session token used when Options leaves them empty.
const (
	DefaultUsername  = "administrator@vsphere.local"
	DefaultPassword  = "Sup3rS3cret!"
	DefaultSessionID = "0dc25e01f5b1c9d4a0f5a1e2b3c4d5e6"
)

// Options configures a Server.
type Options struct {
	// Username and Password are the basic credentials Cis.Session_create accepts.
	Username string
	Password string
	// SessionID is the token Cis.Session_create hands out.
	SessionID string
	// DefaultPageSize is the page size used when a request omits page_size.
	DefaultPageSize int
	// Dataset overrides the canned inventory when non nil.
	Dataset *Dataset
	// RepeatMarker makes every tags page hand back the very first marker, so a
	// client that trusts the server to make progress iterates forever.
	RepeatMarker bool
	// TagsUnavailable makes Vcenter.Tagging.Tags_list answer 503.
	TagsUnavailable bool
}

func (o Options) withDefaults() Options {
	if o.Username == "" {
		o.Username = DefaultUsername
	}
	if o.Password == "" {
		o.Password = DefaultPassword
	}
	if o.SessionID == "" {
		o.SessionID = DefaultSessionID
	}
	if o.DefaultPageSize <= 0 {
		o.DefaultPageSize = 20
	}
	return o
}

// Request is one entry of the server's request log.
type Request struct {
	Method        string
	Path          string
	RawQuery      string
	Header        http.Header
	Body          string
	Status        int
	Authorization string
	SessionHeader string
	Accept        string
	ContentType   string
}

// Query parses RawQuery back into its key/value pairs.
func (r Request) Query() url.Values {
	v, err := url.ParseQuery(r.RawQuery)
	if err != nil {
		return url.Values{}
	}
	return v
}

// Server is a running loopback double.
type Server struct {
	ts   *httptest.Server
	opts Options
	data Dataset

	mu           sync.Mutex
	log          []Request
	sessionAlive bool
	pagesServed  map[string]int
}

// maxPagesPerCollection bounds a client that never stops iterating, so a
// runaway pagination loop fails fast instead of hanging the test binary.
const maxPagesPerCollection = 256

// Start brings up the double on 127.0.0.1. Close it when the test finishes.
func Start(opts Options) *Server {
	o := opts.withDefaults()
	data := Inventory()
	if o.Dataset != nil {
		data = o.Dataset.Clone()
	}
	s := &Server{opts: o, data: data, pagesServed: map[string]int{}}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		panic("mockvc: failed to listen on 127.0.0.1: " + err.Error())
	}
	s.ts = httptest.NewUnstartedServer(http.HandlerFunc(s.serve))
	s.ts.Listener = listener
	s.ts.Start()
	return s
}

// URL is the base URL of the double, without the /api prefix.
func (s *Server) URL() string { return s.ts.URL }

// IssuedSessionID is the token Cis.Session_create hands out.
func (s *Server) IssuedSessionID() string { return s.opts.SessionID }

// Close shuts the double down.
func (s *Server) Close() { s.ts.Close() }

// Requests returns a copy of the request log in arrival order.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.log))
	copy(out, s.log)
	return out
}

type localizableMessage struct {
	ID             string   `json:"id"`
	DefaultMessage string   `json:"default_message"`
	Args           []string `json:"args"`
}

type vapiError struct {
	ErrorType string               `json:"error_type"`
	Messages  []localizableMessage `json:"messages"`
}

type apiFault struct {
	status int
	body   vapiError
}

func fault(status int, errorType, id, msg string) *apiFault {
	return &apiFault{
		status: status,
		body: vapiError{
			ErrorType: errorType,
			Messages:  []localizableMessage{{ID: id, DefaultMessage: msg, Args: []string{}}},
		},
	}
}

func notFound(msg string) *apiFault {
	return fault(http.StatusNotFound, "NOT_FOUND", "com.vmware.vapi.rest.notfound", msg)
}

func invalidArgument(msg string) *apiFault {
	return fault(http.StatusBadRequest, "INVALID_ARGUMENT", "com.vmware.vapi.std.errors.invalid_argument", msg)
}

func unauthenticated(msg string) *apiFault {
	return fault(http.StatusUnauthorized, "UNAUTHENTICATED", "com.vmware.vapi.std.errors.unauthenticated", msg)
}

func serviceUnavailable(msg string) *apiFault {
	return fault(http.StatusServiceUnavailable, "SERVICE_UNAVAILABLE", "com.vmware.vapi.std.errors.service_unavailable", msg)
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body := readBody(r)
	idx := s.record(r, body)

	status := s.dispatch(w, r)
	s.mu.Lock()
	s.log[idx].Status = status
	s.mu.Unlock()
}

func readBody(r *http.Request) string {
	if r.Body == nil {
		return ""
	}
	defer r.Body.Close()
	raw, err := io.ReadAll(r.Body)
	if err != nil {
		return string(raw)
	}
	return string(raw)
}

func (s *Server) record(r *http.Request, body string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log = append(s.log, Request{
		Method:        r.Method,
		Path:          r.URL.Path,
		RawQuery:      r.URL.RawQuery,
		Header:        r.Header.Clone(),
		Body:          body,
		Authorization: r.Header.Get("Authorization"),
		SessionHeader: r.Header.Get("vmware-api-session-id"),
		Accept:        r.Header.Get("Accept"),
		ContentType:   r.Header.Get("Content-Type"),
	})
	return len(s.log) - 1
}

func (s *Server) dispatch(w http.ResponseWriter, r *http.Request) int {
	switch {
	case r.Method == http.MethodPost && r.URL.Path == "/api/session":
		return s.sessionCreate(w, r)
	case r.Method == http.MethodDelete && r.URL.Path == "/api/session":
		return s.sessionDelete(w, r)
	case r.Method == http.MethodGet && r.URL.Path == "/api/vcenter/tagging/categories":
		return s.categoriesList(w, r)
	case r.Method == http.MethodGet && r.URL.Path == "/api/vcenter/tagging/tags":
		return s.tagsList(w, r)
	default:
		return writeFault(w, notFound("this loopback double serves only the operations named by docs/contract.json"))
	}
}

func writeFault(w http.ResponseWriter, f *apiFault) int {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(f.status)
	_ = json.NewEncoder(w).Encode(f.body)
	return f.status
}

func writeJSON(w http.ResponseWriter, status int, v any) int {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
	return status
}

func (s *Server) sessionCreate(w http.ResponseWriter, r *http.Request) int {
	user, pass, ok := r.BasicAuth()
	if !ok {
		return writeFault(w, unauthenticated("Cis.Session_create requires HTTP basic credentials"))
	}
	userOK := subtle.ConstantTimeCompare([]byte(user), []byte(s.opts.Username)) == 1
	passOK := subtle.ConstantTimeCompare([]byte(pass), []byte(s.opts.Password)) == 1
	if !userOK || !passOK {
		return writeFault(w, unauthenticated("the supplied credentials were rejected"))
	}
	s.mu.Lock()
	s.sessionAlive = true
	s.mu.Unlock()
	return writeJSON(w, http.StatusCreated, s.opts.SessionID)
}

func (s *Server) sessionDelete(w http.ResponseWriter, r *http.Request) int {
	if f := s.requireSession(r); f != nil {
		return writeFault(w, f)
	}
	s.mu.Lock()
	s.sessionAlive = false
	s.mu.Unlock()
	w.WriteHeader(http.StatusNoContent)
	return http.StatusNoContent
}

func (s *Server) requireSession(r *http.Request) *apiFault {
	got := r.Header.Get("vmware-api-session-id")
	if got == "" {
		return unauthenticated("the vmware-api-session-id header is missing")
	}
	s.mu.Lock()
	alive := s.sessionAlive
	s.mu.Unlock()
	if !alive || subtle.ConstantTimeCompare([]byte(got), []byte(s.opts.SessionID)) != 1 {
		return unauthenticated("the session id is not known to this server")
	}
	return nil
}

// iterationRequest is the parsed, validated query of a list operation.
type iterationRequest struct {
	names    []string
	marker   string
	offset   int
	pageSize int
}

// parseIteration enforces the contract's query serialization rules.
func (s *Server) parseIteration(kind string, r *http.Request, total int) (iterationRequest, *apiFault) {
	var out iterationRequest
	s.mu.Lock()
	s.pagesServed[kind]++
	served := s.pagesServed[kind]
	s.mu.Unlock()
	if served > maxPagesPerCollection {
		return out, invalidArgument("the iterator is not making progress; this double serves at most " +
			strconv.Itoa(maxPagesPerCollection) + " pages of one collection")
	}

	raw, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		return out, invalidArgument("the query string could not be parsed")
	}
	for key, vals := range raw {
		switch key {
		case "names", "marker", "page_size":
			for _, v := range vals {
				if v == "" {
					return out, invalidArgument("query parameter " + key + " was sent with an empty value; an unset optional field must be omitted entirely")
				}
			}
		default:
			return out, invalidArgument("unexpected query parameter " + key)
		}
	}

	out.names = raw["names"]
	seen := map[string]bool{}
	for _, n := range out.names {
		if seen[n] {
			return out, invalidArgument("names is a set and must not repeat a value")
		}
		seen[n] = true
	}

	if markers := raw["marker"]; len(markers) > 0 {
		if len(markers) > 1 {
			return out, invalidArgument("marker must appear at most once")
		}
		if len(out.names) > 0 {
			return out, invalidArgument("a filter and a marker cannot be supplied together")
		}
		out.marker = markers[0]
		off, ok := decodeMarker(kind, out.marker, total)
		if !ok {
			return out, notFound("the supplied marker is not a marker returned by an earlier invocation of this operation")
		}
		out.offset = off
	}

	out.pageSize = s.opts.DefaultPageSize
	if sizes := raw["page_size"]; len(sizes) > 0 {
		if len(sizes) > 1 {
			return out, invalidArgument("page_size must appear at most once")
		}
		n, err := strconv.Atoi(sizes[0])
		if err != nil || n < 1 {
			return out, invalidArgument("page_size must be a positive integer")
		}
		out.pageSize = n
	}
	return out, nil
}

func encodeMarker(kind string, offset int) string {
	return base64.RawURLEncoding.EncodeToString([]byte(kind + ":" + strconv.Itoa(offset)))
}

func decodeMarker(kind, marker string, total int) (int, bool) {
	raw, err := base64.RawURLEncoding.DecodeString(marker)
	if err != nil {
		return 0, false
	}
	prefix := kind + ":"
	text := string(raw)
	if !strings.HasPrefix(text, prefix) {
		return 0, false
	}
	off, err := strconv.Atoi(strings.TrimPrefix(text, prefix))
	if err != nil || off <= 0 || off > total {
		return 0, false
	}
	return off, true
}

type categoriesListResult struct {
	Items  []CategoryRecord `json:"items"`
	Marker *string          `json:"marker,omitempty"`
}

type tagsListResult struct {
	Items  []TagRecord `json:"items"`
	Marker *string     `json:"marker,omitempty"`
}

func (s *Server) categoriesList(w http.ResponseWriter, r *http.Request) int {
	if f := s.requireSession(r); f != nil {
		return writeFault(w, f)
	}
	matched := append([]CategoryRecord(nil), s.data.Categories...)
	it, f := s.parseIteration("categories", r, len(matched))
	if f != nil {
		return writeFault(w, f)
	}
	if len(it.names) > 0 {
		filtered := matched[:0:0]
		for _, c := range matched {
			for _, n := range it.names {
				if c.Info.Name == n {
					filtered = append(filtered, c)
					break
				}
			}
		}
		matched = filtered
	}

	page, next := slicePage(len(matched), it.offset, it.pageSize)
	res := categoriesListResult{Items: matched[page[0]:page[1]]}
	if res.Items == nil {
		res.Items = []CategoryRecord{}
	}
	if next > 0 {
		m := encodeMarker("categories", next)
		res.Marker = &m
	}
	return writeJSON(w, http.StatusOK, res)
}

func (s *Server) tagsList(w http.ResponseWriter, r *http.Request) int {
	if f := s.requireSession(r); f != nil {
		return writeFault(w, f)
	}
	if s.opts.TagsUnavailable {
		return writeFault(w, serviceUnavailable("the tagging service is not reachable from this vCenter Server"))
	}
	matched := append([]TagRecord(nil), s.data.Tags...)
	it, f := s.parseIteration("tags", r, len(matched))
	if f != nil {
		return writeFault(w, f)
	}
	if len(it.names) > 0 {
		filtered := matched[:0:0]
		for _, t := range matched {
			for _, n := range it.names {
				if t.Info.Name == n {
					filtered = append(filtered, t)
					break
				}
			}
		}
		matched = filtered
	}

	page, next := slicePage(len(matched), it.offset, it.pageSize)
	res := tagsListResult{Items: matched[page[0]:page[1]]}
	if res.Items == nil {
		res.Items = []TagRecord{}
	}
	if next > 0 {
		off := next
		if s.opts.RepeatMarker {
			off = it.pageSize
		}
		m := encodeMarker("tags", off)
		res.Marker = &m
	}
	return writeJSON(w, http.StatusOK, res)
}

// slicePage returns the [start,end) bounds of the requested page and the offset
// of the next page, or 0 when the page just returned is the last one.
func slicePage(total, offset, size int) ([2]int, int) {
	start := offset
	if start > total {
		start = total
	}
	end := start + size
	if end > total {
		end = total
	}
	next := 0
	if end < total {
		next = end
	}
	return [2]int{start, end}, next
}
