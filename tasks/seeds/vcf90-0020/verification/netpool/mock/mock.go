// Package mock serves a loopback double of the VMware Cloud Foundation 9.0
// SDDC Manager network pool API.
//
// It is pinned to docs/contract.json: it routes exactly the two operations the
// contract names (getNetworkPool and createNetworkPool) and answers every other
// method or path off-contract. Each request is appended to a log the tests read
// back, together with the contract violations the double noticed in it.
//
// Nothing here talks to a VMware deployment. The listener is bound to 127.0.0.1.
package mock

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"sync"
)

// DuplicateNameErrorCode is the minor error code the double returns when a
// createNetworkPool request names a pool that already exists.
const DuplicateNameErrorCode = "NETWORK_POOL_NAME_DUPLICATE"

// Operation identifiers from the 9.0.0.0 specification.
const (
	OpGetNetworkPool    = "getNetworkPool"
	OpCreateNetworkPool = "createNetworkPool"
)

// Contract mirrors the request-encoding section of docs/contract.json. The test
// suite cross-checks these values against the document itself, so the double and
// the contract cannot drift apart.
var Contract = struct {
	OperationIDs []string

	PoolRequiredKeys  []string
	PoolAllowedKeys   []string
	PoolForbiddenKeys []string

	NetworkRequiredKeys  []string
	NetworkAllowedKeys   []string
	NetworkOptionalKeys  []string
	NetworkForbiddenKeys []string

	IPPoolRequiredKeys []string
	IPPoolAllowedKeys  []string

	// KeysAbsentAt90 exist on Network in the 9.1.0.0 revision of the same file
	// but not at 9.0.0.0. Seeing one on the wire means the wrong revision was used.
	KeysAbsentAt90 []string
}{
	OperationIDs: []string{OpCreateNetworkPool, OpGetNetworkPool},

	PoolRequiredKeys:  []string{"name", "networks"},
	PoolAllowedKeys:   []string{"name", "networks"},
	PoolForbiddenKeys: []string{"hostsCount", "id"},

	NetworkRequiredKeys:  []string{"gateway", "mask", "mtu", "subnet", "type", "vlanId"},
	NetworkAllowedKeys:   []string{"gateway", "ipPools", "mask", "mtu", "subnet", "type", "vlanId"},
	NetworkOptionalKeys:  []string{"ipPools"},
	NetworkForbiddenKeys: []string{"freeIps", "id", "usedIps"},

	IPPoolRequiredKeys: []string{"end", "start"},
	IPPoolAllowedKeys:  []string{"end", "start"},

	KeysAbsentAt90: []string{"freeIpCount", "ipAddressAssignmentMode", "ipAddressVersion", "usedIpCount"},
}

// Fault scripts one createNetworkPool request. Faults are consumed in order:
// the first entry applies to the first create the double receives, and so on.
// Once the list is exhausted, creates behave normally.
type Fault string

const (
	// FaultNone handles the create normally.
	FaultNone Fault = ""
	// FaultApplyThenUnavailable stores the pool, then answers 503. The caller
	// cannot tell from the response that the create landed.
	FaultApplyThenUnavailable Fault = "apply-then-503"
	// FaultApplyThenHangUp stores the pool, then closes the connection without
	// writing any response at all.
	FaultApplyThenHangUp Fault = "apply-then-hangup"
	// FaultRejectUnapplied answers 500 without storing anything.
	FaultRejectUnapplied Fault = "reject-500"
	// FaultLoseRaceThenDuplicate stores a pool of the requested name as though a
	// peer had won the race, then answers 400 with DuplicateNameErrorCode.
	FaultLoseRaceThenDuplicate Fault = "race-then-400"
)

// IPRange is the IpPool schema.
type IPRange struct {
	Start string `json:"start"`
	End   string `json:"end"`
}

// Network is the Network schema, as the double stores and returns it.
type Network struct {
	ID      string    `json:"id"`
	Type    string    `json:"type"`
	VLANID  int32     `json:"vlanId"`
	MTU     int32     `json:"mtu"`
	Subnet  string    `json:"subnet"`
	Mask    string    `json:"mask"`
	Gateway string    `json:"gateway"`
	IPPools []IPRange `json:"ipPools,omitempty"`
	FreeIPs []string  `json:"freeIps,omitempty"`
	UsedIPs []string  `json:"usedIps,omitempty"`
}

// Pool is the NetworkPool schema.
type Pool struct {
	ID         string    `json:"id"`
	Name       string    `json:"name"`
	Networks   []Network `json:"networks"`
	HostsCount int32     `json:"hostsCount"`
}

// Request is one entry of the double's request log.
type Request struct {
	Seq int
	// OperationID is the contract operation the request matched, or "" when the
	// request fell outside the contract.
	OperationID string
	Method      string
	Path        string
	RawQuery    string
	Header      http.Header
	Body        []byte
	// Status is the response code, or 0 when the double hung up without one.
	Status int
	// Violations lists every way the request departed from docs/contract.json.
	Violations []string
	// Applied reports whether a createNetworkPool request changed stored state,
	// regardless of what the caller was told.
	Applied bool
}

// OnContract reports whether the request matched a contract operation.
func (r Request) OnContract() bool { return r.OperationID != "" }

// Options configures a double.
type Options struct {
	// Token is the bearer token the double requires. Defaults to "test-token".
	Token string
	// Pools seeds stored state.
	Pools []Pool
	// CreateFaults scripts consecutive createNetworkPool requests.
	CreateFaults []Fault
}

// Server is a running loopback double.
type Server struct {
	// URL is the base URL, for example http://127.0.0.1:38211.
	URL string

	http *httptest.Server

	mu         sync.Mutex
	token      string
	pools      []Pool
	log        []Request
	faults     []Fault
	createSeen int
	nextID     int
}

// Start binds a double to 127.0.0.1 and returns it. Call Close when done.
func Start(opts Options) *Server {
	token := opts.Token
	if token == "" {
		token = "test-token"
	}
	s := &Server{token: token, faults: append([]Fault(nil), opts.CreateFaults...)}
	for _, p := range opts.Pools {
		s.pools = append(s.pools, s.materialize(p))
	}

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		panic("mock: cannot bind loopback listener: " + err.Error())
	}
	s.http = &httptest.Server{
		Listener: listener,
		Config:   &http.Server{Handler: http.HandlerFunc(s.route)},
	}
	s.http.Start()
	s.URL = s.http.URL
	return s
}

// Close shuts the double down.
func (s *Server) Close() { s.http.Close() }

// Requests returns a copy of the request log in arrival order.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Request(nil), s.log...)
}

// RequestsFor returns the logged requests that matched one contract operation.
func (s *Server) RequestsFor(operationID string) []Request {
	var out []Request
	for _, r := range s.Requests() {
		if r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

// OffContractRequests returns the logged requests that matched no contract operation.
func (s *Server) OffContractRequests() []Request {
	var out []Request
	for _, r := range s.Requests() {
		if !r.OnContract() {
			out = append(out, r)
		}
	}
	return out
}

// Violations returns every contract violation the double recorded, prefixed with
// the request that carried it.
func (s *Server) Violations() []string {
	var out []string
	for _, r := range s.Requests() {
		for _, v := range r.Violations {
			out = append(out, fmt.Sprintf("request #%d %s %s: %s", r.Seq, r.Method, r.Path, v))
		}
	}
	return out
}

// Pools returns stored state in insertion order.
func (s *Server) Pools() []Pool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Pool(nil), s.pools...)
}

// PoolsNamed returns every stored pool carrying the given name. More than one
// means a retry duplicated its effect.
func (s *Server) PoolsNamed(name string) []Pool {
	var out []Pool
	for _, p := range s.Pools() {
		if p.Name == name {
			out = append(out, p)
		}
	}
	return out
}

// AppliedCreates counts the createNetworkPool requests that changed stored state.
func (s *Server) AppliedCreates() int {
	n := 0
	for _, r := range s.Requests() {
		if r.OperationID == OpCreateNetworkPool && r.Applied {
			n++
		}
	}
	return n
}

func (s *Server) route(w http.ResponseWriter, r *http.Request) {
	body := readBody(r)
	entry := Request{
		Method:   r.Method,
		Path:     r.URL.Path,
		RawQuery: r.URL.RawQuery,
		Header:   r.Header.Clone(),
		Body:     body,
	}

	switch {
	case r.URL.Path == "/v1/network-pools" && r.Method == http.MethodGet:
		entry.OperationID = OpGetNetworkPool
		s.handleList(w, &entry)
	case r.URL.Path == "/v1/network-pools" && r.Method == http.MethodPost:
		entry.OperationID = OpCreateNetworkPool
		s.handleCreate(w, &entry)
	default:
		// Deliberately not served: the contract names two operations and the
		// double offers exactly those.
		entry.Violations = append(entry.Violations,
			fmt.Sprintf("off-contract request %s %s; the contract names only %s",
				r.Method, r.URL.Path, strings.Join(Contract.OperationIDs, " and ")))
		status := http.StatusNotFound
		if r.URL.Path == "/v1/network-pools" {
			status = http.StatusMethodNotAllowed
		}
		s.finish(w, &entry, status, errorBody("OFF_CONTRACT_OPERATION",
			"This double serves only the operations named in docs/contract.json."))
	}
}

func (s *Server) handleList(w http.ResponseWriter, entry *Request) {
	s.checkCommonHeaders(entry, false)
	if entry.RawQuery != "" {
		entry.Violations = append(entry.Violations,
			fmt.Sprintf("getNetworkPool declares no parameters at 9.0.0.0 but the request carried the query %q", entry.RawQuery))
	}
	if len(entry.Body) > 0 {
		entry.Violations = append(entry.Violations, "getNetworkPool declares no request body but the request carried one")
	}
	if !s.authorized(entry) {
		s.finish(w, entry, http.StatusUnauthorized, errorBody("UNAUTHORIZED", "Missing or invalid bearer token."))
		return
	}

	pools := s.Pools()
	if pools == nil {
		pools = []Pool{}
	}
	s.finish(w, entry, http.StatusOK, map[string]any{
		"elements": pools,
		"pageMetadata": map[string]any{
			"pageNumber":    0,
			"pageSize":      len(pools),
			"totalElements": len(pools),
			"totalPages":    1,
		},
	})
}

func (s *Server) handleCreate(w http.ResponseWriter, entry *Request) {
	s.checkCommonHeaders(entry, true)
	if !s.authorized(entry) {
		s.finish(w, entry, http.StatusUnauthorized, errorBody("UNAUTHORIZED", "Missing or invalid bearer token."))
		return
	}

	violations, wanted, hard := inspectCreateBody(entry.Body)
	entry.Violations = append(entry.Violations, violations...)
	if hard != "" {
		// A real SDDC Manager rejects a body it cannot bind. Extra or empty
		// properties are tolerated and merely recorded above.
		s.finish(w, entry, http.StatusBadRequest, errorBody("NETWORK_POOL_SPEC_INVALID", hard))
		return
	}

	s.mu.Lock()
	fault := FaultNone
	if s.createSeen < len(s.faults) {
		fault = s.faults[s.createSeen]
	}
	s.createSeen++
	s.mu.Unlock()

	switch fault {
	case FaultRejectUnapplied:
		s.finish(w, entry, http.StatusInternalServerError,
			errorBody("INTERNAL_SERVER_ERROR", "The network pool could not be created."))
		return
	case FaultLoseRaceThenDuplicate:
		// A peer wins the race: the pool appears, and this caller is told the
		// name is taken.
		_, entry.Applied = s.insertIfAbsent(wanted)
		s.finish(w, entry, http.StatusBadRequest,
			errorBody(DuplicateNameErrorCode, fmt.Sprintf("Network pool with name %s already exists.", wanted.Name)))
		return
	}

	created, inserted := s.insertIfAbsent(wanted)
	if !inserted {
		s.finish(w, entry, http.StatusBadRequest,
			errorBody(DuplicateNameErrorCode, fmt.Sprintf("Network pool with name %s already exists.", wanted.Name)))
		return
	}
	entry.Applied = true

	switch fault {
	case FaultApplyThenUnavailable:
		s.finish(w, entry, http.StatusServiceUnavailable,
			errorBody("SERVICE_UNAVAILABLE", "The service is temporarily unavailable."))
	case FaultApplyThenHangUp:
		s.hangUp(w, entry)
	default:
		s.finish(w, entry, http.StatusCreated, created)
	}
}

// insertIfAbsent stores a pool unless one of that name is already present. The
// check and the store happen under one lock, so concurrent creates of the same
// name cannot both land.
func (s *Server) insertIfAbsent(p Pool) (Pool, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, existing := range s.pools {
		if existing.Name == p.Name {
			return existing, false
		}
	}
	stored := s.materialize(p)
	s.pools = append(s.pools, stored)
	return stored, true
}

// materialize assigns deterministic identifiers. Callers hold s.mu, except Start.
func (s *Server) materialize(p Pool) Pool {
	s.nextID++
	poolSeq := s.nextID
	out := Pool{
		ID:         fmt.Sprintf("np-%04d", poolSeq),
		Name:       p.Name,
		HostsCount: p.HostsCount,
		Networks:   make([]Network, 0, len(p.Networks)),
	}
	for i, n := range p.Networks {
		if n.ID == "" {
			n.ID = fmt.Sprintf("np-%04d-net-%d", poolSeq, i+1)
		}
		out.Networks = append(out.Networks, n)
	}
	return out
}

func (s *Server) authorized(entry *Request) bool {
	return entry.Header.Get("Authorization") == "Bearer "+s.token
}

func (s *Server) checkCommonHeaders(entry *Request, hasBody bool) {
	if got := entry.Header.Get("Accept"); !mediaTypeIsJSON(got) {
		entry.Violations = append(entry.Violations,
			fmt.Sprintf("Accept must be application/json, got %q", got))
	}
	if hasBody {
		if got := entry.Header.Get("Content-Type"); !mediaTypeIsJSON(got) {
			entry.Violations = append(entry.Violations,
				fmt.Sprintf("Content-Type must be application/json, got %q", got))
		}
	} else if got := entry.Header.Get("Content-Type"); got != "" {
		entry.Violations = append(entry.Violations,
			fmt.Sprintf("a bodyless request must not declare Content-Type, got %q", got))
	}
	if entry.Header.Get("Authorization") == "" {
		entry.Violations = append(entry.Violations, "Authorization header is absent")
	}
}

func (s *Server) finish(w http.ResponseWriter, entry *Request, status int, payload any) {
	entry.Status = status
	s.record(entry)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if payload != nil {
		_ = json.NewEncoder(w).Encode(payload)
	}
}

// hangUp drops the connection without writing a response, which is what a caller
// sees when a mutation lands but its answer is lost.
func (s *Server) hangUp(w http.ResponseWriter, entry *Request) {
	entry.Status = 0
	s.record(entry)
	hijacker, ok := w.(http.Hijacker)
	if !ok {
		panic("mock: response writer does not support hijacking")
	}
	conn, _, err := hijacker.Hijack()
	if err != nil {
		panic("mock: hijack failed: " + err.Error())
	}
	_ = conn.Close()
}

func (s *Server) record(entry *Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	entry.Seq = len(s.log) + 1
	s.log = append(s.log, *entry)
}

// inspectCreateBody records every departure from the contract's request encoding
// and returns the pool the request asked for. A non-empty hard message means the
// body cannot be bound at all and must be answered 400.
func inspectCreateBody(raw []byte) (violations []string, wanted Pool, hard string) {
	var top map[string]json.RawMessage
	if err := json.Unmarshal(raw, &top); err != nil {
		return nil, Pool{}, "Request body is not a JSON object."
	}

	violations = append(violations, checkKeys("body", top,
		Contract.PoolRequiredKeys, Contract.PoolAllowedKeys, Contract.PoolForbiddenKeys, nil)...)

	if _, ok := top["name"]; !ok {
		return violations, Pool{}, "Property name is required."
	}
	if err := json.Unmarshal(top["name"], &wanted.Name); err != nil || wanted.Name == "" {
		return violations, Pool{}, "Property name must be a non-empty string."
	}

	rawNetworks, ok := top["networks"]
	if !ok {
		return violations, Pool{}, "Property networks is required."
	}
	var networks []map[string]json.RawMessage
	if err := json.Unmarshal(rawNetworks, &networks); err != nil {
		return violations, Pool{}, "Property networks must be an array."
	}
	if len(networks) == 0 {
		return violations, Pool{}, "Property networks must not be empty."
	}

	for i, n := range networks {
		where := fmt.Sprintf("body.networks[%d]", i)
		violations = append(violations, checkKeys(where, n,
			Contract.NetworkRequiredKeys, Contract.NetworkAllowedKeys, Contract.NetworkForbiddenKeys,
			Contract.KeysAbsentAt90)...)

		var decoded Network
		if err := json.Unmarshal(rawObject(n), &decoded); err != nil {
			return violations, Pool{}, where + " could not be bound: " + err.Error()
		}
		for _, key := range Contract.NetworkRequiredKeys {
			if _, present := n[key]; !present {
				return violations, Pool{}, fmt.Sprintf("Property %s.%s is required at 9.0.0.0.", where, key)
			}
		}
		if rawPools, present := n["ipPools"]; present {
			var pools []map[string]json.RawMessage
			if err := json.Unmarshal(rawPools, &pools); err != nil {
				return violations, Pool{}, where + ".ipPools must be an array."
			}
			for j, p := range pools {
				violations = append(violations, checkKeys(fmt.Sprintf("%s.ipPools[%d]", where, j), p,
					Contract.IPPoolRequiredKeys, Contract.IPPoolAllowedKeys, nil, nil)...)
			}
		}
		decoded.ID = ""
		decoded.FreeIPs = nil
		decoded.UsedIPs = nil
		wanted.Networks = append(wanted.Networks, decoded)
	}
	return violations, wanted, ""
}

func checkKeys(where string, obj map[string]json.RawMessage, required, allowed, forbidden, absentAt90 []string) []string {
	var out []string
	for _, key := range sortedKeys(obj) {
		switch {
		case contains(forbidden, key):
			out = append(out, fmt.Sprintf("%s carries %q, which is readOnly at 9.0.0.0 and must never be sent", where, key))
		case contains(absentAt90, key):
			out = append(out, fmt.Sprintf("%s carries %q, which does not exist on this schema at 9.0.0.0; that property was introduced at 9.1.0.0", where, key))
		case !contains(allowed, key):
			out = append(out, fmt.Sprintf("%s carries unknown property %q", where, key))
		}
		if contains(allowed, key) && !contains(required, key) && isEmptyJSON(obj[key]) {
			out = append(out, fmt.Sprintf("%s sends optional property %q as %s; an unset optional property is omitted, not sent empty",
				where, key, strings.TrimSpace(string(obj[key]))))
		}
	}
	for _, key := range required {
		if _, ok := obj[key]; !ok {
			out = append(out, fmt.Sprintf("%s is missing required property %q", where, key))
		}
	}
	return out
}

// isEmptyJSON reports whether a value carries no information: null, an empty
// string, or an empty array.
func isEmptyJSON(raw json.RawMessage) bool {
	switch strings.TrimSpace(string(raw)) {
	case "null", `""`, "[]", "{}":
		return true
	}
	return false
}

func rawObject(obj map[string]json.RawMessage) []byte {
	encoded, err := json.Marshal(obj)
	if err != nil {
		panic("mock: cannot re-encode object: " + err.Error())
	}
	return encoded
}

func sortedKeys(obj map[string]json.RawMessage) []string {
	keys := make([]string, 0, len(obj))
	for k := range obj {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func contains(haystack []string, needle string) bool {
	for _, s := range haystack {
		if s == needle {
			return true
		}
	}
	return false
}

func mediaTypeIsJSON(value string) bool {
	base, _, _ := strings.Cut(value, ";")
	return strings.EqualFold(strings.TrimSpace(base), "application/json")
}

func errorBody(code, message string) map[string]any {
	return map[string]any{"errorCode": code, "message": message}
}

func readBody(r *http.Request) []byte {
	if r.Body == nil {
		return nil
	}
	defer r.Body.Close()
	raw, err := io.ReadAll(r.Body)
	if err != nil || len(raw) == 0 {
		return nil
	}
	return raw
}
