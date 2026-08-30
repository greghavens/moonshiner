// Package mockvc is a loopback stand-in for a VCF 9.0 vCenter, pinned to
// docs/contract.json.
//
// It builds its routing table from the contract and refuses to start unless the
// contract describes the operations this project depends on. It serves those
// operations and nothing else. Every request it receives is retained and readable
// through Requests, so tests can assert on the exact bytes that went over the wire.
//
// It uses an in-process HTTP transport and never opens a network connection.
package mockvc

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"sort"
	"strings"
	"sync"
	"testing"
)

// ---------------------------------------------------------------------------
// Contract
// ---------------------------------------------------------------------------

// Contract is docs/contract.json.
type Contract struct {
	API            string            `json:"api"`
	SpecVersion    string            `json:"spec_version"`
	ServerBasePath string            `json:"server_base_path"`
	Auth           Auth              `json:"auth"`
	Operations     []Operation       `json:"operations"`
	Schemas        map[string]Schema `json:"schemas"`
}

// Auth is the security scheme the operations carry.
type Auth struct {
	Scheme string `json:"scheme"`
	In     string `json:"in"`
	Name   string `json:"name"`
}

// Operation is one contracted API operation.
type Operation struct {
	OperationID  string            `json:"operation_id"`
	Method       string            `json:"method"`
	Path         string            `json:"path"`
	Query        map[string]string `json:"query"`
	SpecPathKey  string            `json:"spec_path_key"`
	PathParams   []string          `json:"path_params"`
	RequestBody  *string           `json:"request_body"`
	SuccessCode  int               `json:"success_status"`
	ResponseBody *string           `json:"response_body"`
}

// Schema is one contracted schema.
type Schema struct {
	Properties map[string]Property `json:"properties"`
}

// Property is one contracted schema property.
type Property struct {
	Type     string   `json:"type"`
	Required bool     `json:"required"`
	Format   string   `json:"format,omitempty"`
	Enum     []string `json:"enum,omitempty"`
	Ref      string   `json:"ref,omitempty"`
	Items    *Items   `json:"items,omitempty"`
}

// Items describes an array property's element.
type Items struct {
	Type string `json:"type,omitempty"`
	Ref  string `json:"ref,omitempty"`
}

// LoadContract reads and structurally validates a contract document.
func LoadContract(path string) (*Contract, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var c Contract
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&c); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	if err := c.validate(); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return &c, nil
}

// requiredOperations are the operations this project depends on. The mock will not
// start unless the contract names exactly these, and it serves nothing else.
var requiredOperations = []string{
	"Vcenter.Vm.Guest.Customization_check",
	"Vcenter.Vm.Guest.Customization_set",
}

func (c *Contract) validate() error {
	if c.ServerBasePath == "" || !strings.HasPrefix(c.ServerBasePath, "/") {
		return fmt.Errorf("server_base_path %q is not an absolute path", c.ServerBasePath)
	}
	if c.Auth.In != "header" || c.Auth.Name == "" {
		return fmt.Errorf("auth must be carried in a named header, got in=%q name=%q", c.Auth.In, c.Auth.Name)
	}
	var got []string
	for _, op := range c.Operations {
		got = append(got, op.OperationID)
	}
	sort.Strings(got)
	want := append([]string(nil), requiredOperations...)
	sort.Strings(want)
	if strings.Join(got, ",") != strings.Join(want, ",") {
		return fmt.Errorf("contract names operations %v, this project needs exactly %v", got, want)
	}
	for _, op := range c.Operations {
		if op.Method == "" || !strings.HasPrefix(op.Path, "/") {
			return fmt.Errorf("%s: method %q path %q", op.OperationID, op.Method, op.Path)
		}
		if op.Method != strings.ToUpper(op.Method) {
			return fmt.Errorf("%s: method %q is not uppercase", op.OperationID, op.Method)
		}
		if op.SuccessCode < 200 || op.SuccessCode > 299 {
			return fmt.Errorf("%s: success_status %d is not a success code", op.OperationID, op.SuccessCode)
		}
		for _, name := range []*string{op.RequestBody, op.ResponseBody} {
			if name != nil {
				if _, ok := c.Schemas[*name]; !ok {
					return fmt.Errorf("%s: references unknown schema %q", op.OperationID, *name)
				}
			}
		}
		for _, p := range op.PathParams {
			if !strings.Contains(op.Path, "{"+p+"}") {
				return fmt.Errorf("%s: path %q has no {%s} placeholder", op.OperationID, op.Path, p)
			}
		}
	}
	for name, s := range c.Schemas {
		for pname, p := range s.Properties {
			switch p.Type {
			case "string", "integer", "boolean":
				if p.Ref != "" || p.Items != nil {
					return fmt.Errorf("%s.%s: scalar property carries ref/items", name, pname)
				}
			case "object":
				if p.Ref == "" {
					return fmt.Errorf("%s.%s: object property has no ref", name, pname)
				}
				if _, ok := c.Schemas[p.Ref]; !ok {
					return fmt.Errorf("%s.%s: ref %q is not a contracted schema", name, pname, p.Ref)
				}
			case "array":
				if p.Items == nil {
					return fmt.Errorf("%s.%s: array property has no items", name, pname)
				}
				if p.Items.Ref != "" {
					if _, ok := c.Schemas[p.Items.Ref]; !ok {
						return fmt.Errorf("%s.%s: items ref %q is not a contracted schema", name, pname, p.Items.Ref)
					}
				} else if p.Items.Type == "" {
					return fmt.Errorf("%s.%s: array items have neither type nor ref", name, pname)
				}
			default:
				return fmt.Errorf("%s.%s: unsupported type %q", name, pname, p.Type)
			}
		}
	}
	return nil
}

// Operation returns the contracted operation with the given id.
func (c *Contract) Operation(id string) (Operation, bool) {
	for _, op := range c.Operations {
		if op.OperationID == id {
			return op, true
		}
	}
	return Operation{}, false
}

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------

// Recorded is one request the server received, including requests it rejected.
type Recorded struct {
	OperationID string // "" when the request matched no contracted operation
	Method      string
	Path        string // full request path, base path included
	RawQuery    string
	Header      http.Header
	Body        []byte
	Status      int // status the server responded with
}

// Server is a loopback vCenter.
type Server struct {
	tb       testing.TB
	contract *Contract
	url      string
	hc       *http.Client

	mu        sync.Mutex
	requests  []Recorded
	checkResp canned
	setResp   *canned
}

type canned struct {
	status int
	body   []byte
}

type roundTripper struct {
	server *Server
}

func (rt roundTripper) RoundTrip(req *http.Request) (*http.Response, error) {
	recorder := httptest.NewRecorder()
	rt.server.serve(recorder, req)
	return recorder.Result(), nil
}

// New creates an in-process loopback vCenter pinned to the contract at
// contractPath. It fails tb if the contract cannot be loaded or does not describe
// the operations this project depends on.
func New(tb testing.TB, contractPath string) *Server {
	tb.Helper()
	c, err := LoadContract(contractPath)
	if err != nil {
		tb.Fatalf("mockvc: refusing to start: %v", err)
	}
	s := &Server{tb: tb, contract: c, url: "http://127.0.0.1"}
	s.checkResp = canned{status: 200, body: mustJSON(CheckInfo("SUPPORTED", boolp(true), boolp(true)))}
	s.hc = &http.Client{Transport: roundTripper{server: s}}
	return s
}

// URL is the base URL a client should be pointed at. It carries no path: the client
// is responsible for the contract's server_base_path.
func (s *Server) URL() string { return s.url }

// Client returns an http.Client wired to this server's listener.
func (s *Server) Client() *http.Client { return s.hc }

// Contract is the contract the server was pinned to.
func (s *Server) Contract() *Contract { return s.contract }

// SetCheckInfo sets what the precheck operation responds with. body is marshalled
// as JSON; a nil body sends no payload.
func (s *Server) SetCheckInfo(status int, body any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.checkResp = canned{status: status, body: mustJSON(body)}
}

// SetSetResponse overrides what the mutating operation responds with. Without it,
// the mutating operation validates the request body against the contract and
// answers with the contracted success status.
func (s *Server) SetSetResponse(status int, body any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.setResp = &canned{status: status, body: mustJSON(body)}
}

// Requests returns every request the server has received, oldest first.
func (s *Server) Requests() []Recorded {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Recorded, len(s.requests))
	copy(out, s.requests)
	return out
}

// RequestsFor returns the received requests that matched the given operation id.
func (s *Server) RequestsFor(operationID string) []Recorded {
	var out []Recorded
	for _, r := range s.Requests() {
		if r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

// Reset clears the request log and the canned mutating response.
func (s *Server) Reset() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = nil
	s.setResp = nil
}

// CheckInfo builds a Vcenter.Vm.Guest.Customization.CheckInfo payload. Nil pointers
// leave the corresponding optional property out, as the API does.
func CheckInfo(status string, guestOS, powerState *bool) map[string]any {
	m := map[string]any{"check_status": status}
	if guestOS != nil {
		m["supported_guest_os"] = *guestOS
	}
	if powerState != nil {
		m["supported_power_state"] = *powerState
	}
	return m
}

func boolp(b bool) *bool { return &b }

func mustJSON(v any) []byte {
	if v == nil {
		return nil
	}
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return b
}

// ---------------------------------------------------------------------------
// Routing
// ---------------------------------------------------------------------------

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	var body []byte
	if r.Body != nil {
		body, _ = io.ReadAll(r.Body)
	}
	rec := Recorded{
		Method:   r.Method,
		Path:     r.URL.Path,
		RawQuery: r.URL.RawQuery,
		Header:   r.Header.Clone(),
		Body:     body,
	}

	status, payload := s.dispatch(&rec)
	rec.Status = status

	s.mu.Lock()
	s.requests = append(s.requests, rec)
	s.mu.Unlock()

	if len(payload) > 0 {
		w.Header().Set("Content-Type", "application/json")
	}
	w.WriteHeader(status)
	if len(payload) > 0 {
		_, _ = w.Write(payload)
	}
}

func (s *Server) dispatch(rec *Recorded) (int, []byte) {
	op, ok := s.match(rec)
	if !ok {
		// Not an operation this contract names.
		if s.pathMatchesAnyOperation(rec) {
			return 405, apiError("METHOD_NOT_ALLOWED", "operation not served")
		}
		return 404, apiError("NOT_FOUND", "no contracted operation matches this request")
	}
	rec.OperationID = op.OperationID

	if rec.Header.Get(s.contract.Auth.Name) == "" {
		return 401, apiError("UNAUTHENTICATED", "missing "+s.contract.Auth.Name)
	}

	if op.RequestBody == nil {
		if len(rec.Body) > 0 {
			return 400, apiError("INVALID_ARGUMENT", op.OperationID+" takes no request body")
		}
	} else {
		ct := rec.Header.Get("Content-Type")
		if ct != "application/json" {
			return 400, apiError("INVALID_ARGUMENT", "Content-Type must be application/json, got "+describeContentType(ct))
		}
		var doc any
		dec := json.NewDecoder(strings.NewReader(string(rec.Body)))
		dec.UseNumber()
		if err := dec.Decode(&doc); err != nil {
			return 400, apiError("INVALID_ARGUMENT", "request body is not JSON: "+err.Error())
		}
		if err := s.validateValue(doc, Property{Type: "object", Ref: *op.RequestBody, Required: true}, *op.RequestBody); err != nil {
			return 400, apiError("INVALID_ARGUMENT", err.Error())
		}
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	switch op.OperationID {
	case "Vcenter.Vm.Guest.Customization_check":
		return s.checkResp.status, s.checkResp.body
	default:
		if s.setResp != nil {
			return s.setResp.status, s.setResp.body
		}
		return op.SuccessCode, nil
	}
}

func describeContentType(s string) string {
	if s == "" {
		return "no Content-Type"
	}
	return "\"" + s + "\""
}

func (s *Server) match(rec *Recorded) (Operation, bool) {
	for _, op := range s.contract.Operations {
		if !strings.EqualFold(op.Method, rec.Method) {
			continue
		}
		if !pathMatches(s.contract.ServerBasePath+op.Path, rec.Path) {
			continue
		}
		if !queryMatches(op.Query, rec.RawQuery) {
			continue
		}
		return op, true
	}
	return Operation{}, false
}

func (s *Server) pathMatchesAnyOperation(rec *Recorded) bool {
	for _, op := range s.contract.Operations {
		if pathMatches(s.contract.ServerBasePath+op.Path, rec.Path) {
			return true
		}
	}
	return false
}

func pathMatches(tmpl, got string) bool {
	tp := strings.Split(strings.Trim(tmpl, "/"), "/")
	gp := strings.Split(strings.Trim(got, "/"), "/")
	if len(tp) != len(gp) {
		return false
	}
	for i, seg := range tp {
		if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
			if gp[i] == "" {
				return false
			}
			continue
		}
		if seg != gp[i] {
			return false
		}
	}
	return true
}

// queryMatches requires the request's query string to be exactly the contracted one.
func queryMatches(want map[string]string, rawQuery string) bool {
	got := map[string]string{}
	if rawQuery != "" {
		for _, kv := range strings.Split(rawQuery, "&") {
			k, v, _ := strings.Cut(kv, "=")
			if _, dup := got[k]; dup {
				return false
			}
			got[k] = v
		}
	}
	if len(got) != len(want) {
		return false
	}
	for k, v := range want {
		if got[k] != v {
			return false
		}
	}
	return true
}

func apiError(kind, msg string) []byte {
	return mustJSON(map[string]any{
		"error_type": kind,
		"messages":   []any{map[string]any{"default_message": msg, "id": "mockvc." + kind}},
	})
}

// ---------------------------------------------------------------------------
// Body validation, against the contract only
// ---------------------------------------------------------------------------

func (s *Server) validateValue(v any, p Property, path string) error {
	if v == nil {
		return fmt.Errorf("%s: null is not a permitted value; an unset optional property is omitted, not nulled", path)
	}
	switch p.Type {
	case "object":
		obj, ok := v.(map[string]any)
		if !ok {
			return fmt.Errorf("%s: expected an object, got %s", path, kindOf(v))
		}
		schema, ok := s.contract.Schemas[p.Ref]
		if !ok {
			return fmt.Errorf("%s: schema %q is not contracted", path, p.Ref)
		}
		for name := range obj {
			if _, ok := schema.Properties[name]; !ok {
				return fmt.Errorf("%s: %q is not a property of %s", path, name, p.Ref)
			}
		}
		names := make([]string, 0, len(schema.Properties))
		for name := range schema.Properties {
			names = append(names, name)
		}
		sort.Strings(names)
		for _, name := range names {
			sub := schema.Properties[name]
			got, present := obj[name]
			if !present {
				if sub.Required {
					return fmt.Errorf("%s: required property %q of %s is missing", path, name, p.Ref)
				}
				continue
			}
			if err := s.validateValue(got, sub, path+"."+name); err != nil {
				return err
			}
		}
		return nil
	case "array":
		arr, ok := v.([]any)
		if !ok {
			return fmt.Errorf("%s: expected an array, got %s", path, kindOf(v))
		}
		elem := Property{Required: true}
		if p.Items.Ref != "" {
			elem.Type, elem.Ref = "object", p.Items.Ref
		} else {
			elem.Type = p.Items.Type
		}
		for i, e := range arr {
			if err := s.validateValue(e, elem, fmt.Sprintf("%s[%d]", path, i)); err != nil {
				return err
			}
		}
		return nil
	case "string":
		str, ok := v.(string)
		if !ok {
			return fmt.Errorf("%s: expected a string, got %s", path, kindOf(v))
		}
		if len(p.Enum) > 0 {
			for _, want := range p.Enum {
				if str == want {
					return nil
				}
			}
			return fmt.Errorf("%s: %q is not one of %v", path, str, p.Enum)
		}
		return nil
	case "integer":
		n, ok := v.(json.Number)
		if !ok {
			return fmt.Errorf("%s: expected an integer, got %s", path, kindOf(v))
		}
		if _, err := n.Int64(); err != nil {
			return fmt.Errorf("%s: %s is not an integer", path, n.String())
		}
		return nil
	case "boolean":
		if _, ok := v.(bool); !ok {
			return fmt.Errorf("%s: expected a boolean, got %s", path, kindOf(v))
		}
		return nil
	}
	return fmt.Errorf("%s: unsupported contracted type %q", path, p.Type)
}

func kindOf(v any) string {
	switch t := v.(type) {
	case map[string]any:
		return "an object"
	case []any:
		return "an array"
	case string:
		return "a string"
	case bool:
		return "a boolean"
	case json.Number:
		return "the number " + t.String()
	case nil:
		return "null"
	}
	return fmt.Sprintf("%T", v)
}
