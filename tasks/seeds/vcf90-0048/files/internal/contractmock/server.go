// Package contractmock provides the protected contract-pinned loopback vCenter
// used by the acceptance tests. It serves exactly the operations named by
// docs/contract.json and nothing else.
package contractmock

import (
	"crypto/rand"
	"encoding/base64"
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

// Operation identifiers pinned by the focused contract.
const (
	SessionCreate = "Cis.Session_create"
	VMList        = "Vcenter.VM_list"
	CPUGet        = "Vcenter.Vm.Hardware.Cpu_get"
	CPUUpdate     = "Vcenter.Vm.Hardware.Cpu_update"
)

// SessionHeader is the api_key_auth header named by the contract.
const SessionHeader = "vmware-api-session-id"

// CPUInfo is the focused Vcenter.Vm.Hardware.Cpu.Info shape.
type CPUInfo struct {
	Count            int64 `json:"count"`
	CoresPerSocket   int64 `json:"cores_per_socket"`
	HotAddEnabled    bool  `json:"hot_add_enabled"`
	HotRemoveEnabled bool  `json:"hot_remove_enabled"`
}

// VM is one virtual machine held by the fixture inventory.
type VM struct {
	ID         string
	Name       string
	PowerState string
	CPU        CPUInfo
}

// LocalizableMessage is the focused Vapi.Std.LocalizableMessage shape.
type LocalizableMessage struct {
	ID             string   `json:"id"`
	DefaultMessage string   `json:"default_message"`
	Args           []string `json:"args"`
}

// APIError is the focused Vapi.Std.Errors.Error shape, including the
// Vapi.Std.Errors.Unauthenticated challenge member.
type APIError struct {
	ErrorType string               `json:"error_type"`
	Messages  []LocalizableMessage `json:"messages"`
	Challenge string               `json:"challenge,omitempty"`
}

// Plan controls the fixture responses. Tests build it only after receiving the
// independently generated runtime values.
type Plan struct {
	// VMs is the fixture inventory, in the order Vcenter.VM_list returns it.
	VMs []VM
	// TokenBudgets bounds how many api_key_auth requests each successively
	// issued session token accepts before it is permanently expired. Entry i
	// applies to the i-th issued token; an entry of 0 expires that token on
	// first use. Tokens beyond the slice are unlimited.
	TokenBudgets []int
	// SessionStatuses overrides the HTTP status of successive session creates.
	// A zero or missing entry means 201.
	SessionStatuses []int
	// SessionBody replaces the 201 session-create body when nonempty.
	SessionBody string
	// SessionContentType replaces the media type of a custom SessionBody.
	SessionContentType string
	// SessionRedirectLocation is emitted with a 3xx session response.
	SessionRedirectLocation string
	// ListStatus overrides the Vcenter.VM_list status. Zero means 200.
	ListStatus int
	// ListBody replaces the 200 Vcenter.VM_list body when nonempty.
	ListBody string
	// ListContentType replaces the media type of a custom ListBody.
	ListContentType string
	// CPUGetStatus overrides the per-VM Cpu_get status. Zero means 200.
	CPUGetStatus map[string]int
	// CPUGetBody replaces the per-VM 200 Cpu_get body when nonempty.
	CPUGetBody map[string]string
	// CPUGetContentType replaces the media type of a custom per-VM Cpu_get body.
	CPUGetContentType map[string]string
	// CPUUpdateStatus overrides the per-VM Cpu_update status. Zero means 204.
	CPUUpdateStatus map[string]int
}

// Request is one request captured by the race-safe request log.
type Request struct {
	OperationID      string
	Method           string
	Path             string
	EscapedPath      string
	RawQuery         string
	ForceQuery       bool
	Header           http.Header
	ContentLength    int64
	TransferEncoding []string
	Body             []byte
	Status           int
}

// Applied is one Cpu_update that the fixture actually committed to inventory.
type Applied struct {
	VM    string
	Body  []byte
	Token string
}

// RuntimeValues are generated independently for every server.
type RuntimeValues struct {
	Username string
	Password string
	// Tokens are the session tokens handed out by successive session creates.
	Tokens []string
	// VM identifiers and names for the default inventory.
	AlphaID   string
	BravoID   string
	CharlieID string
	DeltaID   string
	AlphaName string
	BravoName string
	DeltaName string
	// FilterName contains characters that must be percent-encoded in a query.
	FilterName string
}

type contractOperation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

// Server is an IPv4 loopback-only fixture serving exactly the focused
// contract's operation set.
type Server struct {
	httpServer *httptest.Server
	plan       Plan
	runtime    RuntimeValues
	allowed    map[string]contractOperation
	basePath   string

	mu           sync.Mutex
	requests     []Request
	applied      []Applied
	issued       []string
	tokenIndex   map[string]int
	tokenUse     map[string]int
	tokenExpired map[string]bool
	inventory    map[string]*VM
	order        []string
}

// New loads and pins the contract, generates runtime values, and starts the
// loopback server.
func New(contractPath string, planFactory func(RuntimeValues) Plan) (*Server, error) {
	allowed, basePath, err := loadContract(contractPath)
	if err != nil {
		return nil, err
	}
	suffix := randomValue("fixture")
	runtime := RuntimeValues{
		Username: "svc-" + randomValue("user") + "@vsphere.local",
		Password: randomValue("secret"),
		Tokens: []string{
			randomValue("session"),
			randomValue("session"),
			randomValue("session"),
			randomValue("session"),
		},
		AlphaID: "vm-" + randomValue("id"),
		BravoID: "vm-" + randomValue("id"),
		// The specification types the vm path parameter as an opaque string
		// with no character restriction, so one fixture identifier requires
		// percent-encoding as a single path segment.
		CharlieID:  "vm-" + randomValue("id") + "/legacy inventory",
		DeltaID:    "vm-" + randomValue("id"),
		AlphaName:  "alpha-" + suffix,
		BravoName:  "bravo-" + suffix,
		DeltaName:  "delta-" + suffix,
		FilterName: "edge node/" + suffix,
	}
	plan := Plan{}
	if planFactory != nil {
		plan = planFactory(runtime)
	}

	server := &Server{
		plan:         plan,
		runtime:      runtime,
		allowed:      allowed,
		basePath:     basePath,
		tokenIndex:   map[string]int{},
		tokenUse:     map[string]int{},
		tokenExpired: map[string]bool{},
		inventory:    map[string]*VM{},
	}
	for _, machine := range plan.VMs {
		if _, exists := server.inventory[machine.ID]; exists {
			return nil, errors.New("fixture inventory contains a duplicate identifier")
		}
		copied := machine
		server.inventory[machine.ID] = &copied
		server.order = append(server.order, machine.ID)
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

func loadContract(path string) (map[string]contractOperation, string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, "", errors.New("cannot read focused contract")
	}
	var contract struct {
		Server struct {
			BasePath string `json:"base_path"`
		} `json:"server"`
		Operations []contractOperation `json:"operations"`
	}
	if json.Unmarshal(data, &contract) != nil {
		return nil, "", errors.New("cannot decode focused contract")
	}
	if contract.Server.BasePath != "/api" {
		return nil, "", errors.New("focused contract does not pin the /api server base path")
	}
	allowed := make(map[string]contractOperation, len(contract.Operations))
	for _, operation := range contract.Operations {
		if operation.OperationID == "" || operation.Method == "" || operation.Path == "" {
			return nil, "", errors.New("focused contract contains an incomplete operation")
		}
		if _, exists := allowed[operation.OperationID]; exists {
			return nil, "", errors.New("focused contract contains a duplicate operationId")
		}
		allowed[operation.OperationID] = operation
	}
	required := map[string]contractOperation{
		SessionCreate: {
			OperationID: SessionCreate,
			Method:      http.MethodPost,
			Path:        "/session",
		},
		VMList: {
			OperationID: VMList,
			Method:      http.MethodGet,
			Path:        "/vcenter/vm",
		},
		CPUGet: {
			OperationID: CPUGet,
			Method:      http.MethodGet,
			Path:        "/vcenter/vm/{vm}/hardware/cpu",
		},
		CPUUpdate: {
			OperationID: CPUUpdate,
			Method:      http.MethodPatch,
			Path:        "/vcenter/vm/{vm}/hardware/cpu",
		},
	}
	if len(allowed) != len(required) {
		return nil, "", errors.New("focused contract operation set is not pinned")
	}
	for operationID, want := range required {
		if got, ok := allowed[operationID]; !ok || got != want {
			return nil, "", errors.New("focused contract operation does not match pinned route")
		}
	}
	return allowed, contract.Server.BasePath, nil
}

// Close stops the fixture.
func (s *Server) Close() {
	s.httpServer.Close()
}

// URL returns the loopback origin.
func (s *Server) URL() string {
	return s.httpServer.URL
}

// Client returns the fixture's HTTP client.
func (s *Server) Client() *http.Client {
	return s.httpServer.Client()
}

// Runtime returns the generated runtime values.
func (s *Server) Runtime() RuntimeValues {
	return s.runtime
}

// Requests returns a deep copy of the request log.
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

// Applied returns a deep copy of the committed Cpu_update log.
func (s *Server) Applied() []Applied {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Applied, len(s.applied))
	for index, update := range s.applied {
		out[index] = update
		out[index].Body = append([]byte(nil), update.Body...)
	}
	return out
}

// Inventory returns the current fixture CPU state keyed by VM identifier.
func (s *Server) Inventory() map[string]CPUInfo {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make(map[string]CPUInfo, len(s.inventory))
	for id, machine := range s.inventory {
		out[id] = machine.CPU
	}
	return out
}

// IssuedTokens returns the session tokens handed out so far.
func (s *Server) IssuedTokens() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]string(nil), s.issued...)
}

type recorder struct {
	http.ResponseWriter
	status int
}

func (r *recorder) WriteHeader(status int) {
	if r.status == 0 {
		r.status = status
	}
	r.ResponseWriter.WriteHeader(status)
}

func (s *Server) serveHTTP(w http.ResponseWriter, request *http.Request) {
	body, _ := io.ReadAll(request.Body)
	operationID, vmID := s.operationFor(request)
	index := s.record(Request{
		OperationID:      operationID,
		Method:           request.Method,
		Path:             request.URL.Path,
		EscapedPath:      request.URL.EscapedPath(),
		RawQuery:         request.URL.RawQuery,
		ForceQuery:       request.URL.ForceQuery,
		Header:           request.Header.Clone(),
		ContentLength:    request.ContentLength,
		TransferEncoding: append([]string(nil), request.TransferEncoding...),
		Body:             append([]byte(nil), body...),
	})
	writer := &recorder{ResponseWriter: w}
	defer func() { s.finish(index, writer.status) }()

	if operationID == "" {
		writeJSON(writer, http.StatusNotFound, newError(
			"NOT_FOUND",
			"the focused contract does not serve this operation",
		))
		return
	}

	if operationID == SessionCreate {
		s.sessionCreate(writer, request)
		return
	}

	token := request.Header.Get(SessionHeader)
	if !s.acceptToken(token) {
		writeJSON(writer, http.StatusUnauthorized, unauthenticated())
		return
	}

	switch operationID {
	case VMList:
		s.listVMs(writer, request)
	case CPUGet:
		s.getCPU(writer, request, vmID)
	case CPUUpdate:
		s.updateCPU(writer, request, vmID, body, token)
	}
}

// operationFor resolves the request against the pinned contract routes and
// returns the matching operationId plus the decoded {vm} path parameter.
func (s *Server) operationFor(request *http.Request) (string, string) {
	escaped := request.URL.EscapedPath()
	for _, operationID := range []string{SessionCreate, VMList} {
		operation, ok := s.allowed[operationID]
		if ok &&
			request.Method == operation.Method &&
			escaped == s.basePath+operation.Path {
			return operation.OperationID, ""
		}
	}
	for _, operationID := range []string{CPUGet, CPUUpdate} {
		operation, ok := s.allowed[operationID]
		if !ok || request.Method != operation.Method {
			continue
		}
		template := s.basePath + operation.Path
		prefix, suffix, found := strings.Cut(template, "{vm}")
		if !found {
			continue
		}
		if !strings.HasPrefix(escaped, prefix) || !strings.HasSuffix(escaped, suffix) {
			continue
		}
		segment := escaped[len(prefix) : len(escaped)-len(suffix)]
		if segment == "" || strings.Contains(segment, "/") {
			continue
		}
		decoded, err := url.PathUnescape(segment)
		if err != nil {
			continue
		}
		return operation.OperationID, decoded
	}
	return "", ""
}

func (s *Server) record(request Request) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, request)
	return len(s.requests) - 1
}

func (s *Server) finish(index int, status int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if status == 0 {
		status = http.StatusOK
	}
	s.requests[index].Status = status
}

func (s *Server) sessionCreate(w http.ResponseWriter, request *http.Request) {
	if request.URL.RawQuery != "" || request.URL.ForceQuery {
		writeJSON(w, http.StatusBadRequest, newError(
			"INVALID_ARGUMENT",
			"Cis.Session_create declares no query parameters",
		))
		return
	}
	if request.Header.Get(SessionHeader) != "" {
		writeJSON(w, http.StatusBadRequest, newError(
			"INVALID_ARGUMENT",
			"Cis.Session_create is authenticated with basic_auth, not api_key_auth",
		))
		return
	}
	username, password, ok := request.BasicAuth()
	if !ok || username != s.runtime.Username || password != s.runtime.Password {
		writeJSON(w, http.StatusUnauthorized, unauthenticated())
		return
	}

	s.mu.Lock()
	attempt := len(s.issued)
	status := http.StatusCreated
	if attempt < len(s.plan.SessionStatuses) && s.plan.SessionStatuses[attempt] != 0 {
		status = s.plan.SessionStatuses[attempt]
	}
	var token string
	if status == http.StatusCreated {
		if attempt >= len(s.runtime.Tokens) {
			s.mu.Unlock()
			writeJSON(w, http.StatusServiceUnavailable, newError(
				"SERVICE_UNAVAILABLE",
				"the fixture exhausted its generated session tokens",
			))
			return
		}
		token = s.runtime.Tokens[attempt]
		s.tokenIndex[token] = len(s.issued)
		s.issued = append(s.issued, token)
	}
	s.mu.Unlock()

	if status != http.StatusCreated {
		if status >= 300 && status < 400 && s.plan.SessionRedirectLocation != "" {
			w.Header().Set("Location", s.plan.SessionRedirectLocation)
		}
		writeJSON(w, status, unauthenticated())
		return
	}
	if s.plan.SessionBody != "" {
		writeRaw(
			w,
			status,
			s.plan.SessionContentType,
			s.plan.SessionBody,
		)
		return
	}
	writeJSON(w, status, token)
}

// acceptToken reports whether the token is a live session token, consuming one
// unit of its budget. An expired token stays expired.
func (s *Server) acceptToken(token string) bool {
	if token == "" {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	index, known := s.tokenIndex[token]
	if !known || s.tokenExpired[token] {
		return false
	}
	budget := -1
	if index < len(s.plan.TokenBudgets) {
		budget = s.plan.TokenBudgets[index]
	}
	if budget >= 0 && s.tokenUse[token] >= budget {
		s.tokenExpired[token] = true
		return false
	}
	s.tokenUse[token]++
	return true
}

func (s *Server) listVMs(w http.ResponseWriter, request *http.Request) {
	values, err := url.ParseQuery(request.URL.RawQuery)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, newError(
			"INVALID_ARGUMENT",
			"the query string is malformed",
		))
		return
	}
	for name := range values {
		if name != "names" && name != "power_states" {
			writeJSON(w, http.StatusBadRequest, newError(
				"INVALID_ARGUMENT",
				"the focused workflow uses only the names and power_states filters",
			))
			return
		}
	}
	for _, state := range values["power_states"] {
		switch state {
		case "POWERED_OFF", "POWERED_ON", "SUSPENDED":
		default:
			writeJSON(w, http.StatusBadRequest, newError(
				"INVALID_ARGUMENT",
				"power_states contains a value that is not supported by the server",
			))
			return
		}
	}

	status := s.plan.ListStatus
	if status == 0 {
		status = http.StatusOK
	}
	if status != http.StatusOK {
		writeJSON(w, status, newError("ERROR", "the fixture rejected Vcenter.VM_list"))
		return
	}
	if s.plan.ListBody != "" {
		writeRaw(w, status, s.plan.ListContentType, s.plan.ListBody)
		return
	}

	type summary struct {
		VM            string `json:"vm"`
		Name          string `json:"name"`
		PowerState    string `json:"power_state"`
		CPUCount      int64  `json:"cpu_count"`
		MemorySizeMiB int64  `json:"memory_size_mib"`
	}
	s.mu.Lock()
	elements := []summary{}
	for _, id := range s.order {
		machine := s.inventory[id]
		if len(values["names"]) != 0 && !contains(values["names"], machine.Name) {
			continue
		}
		if len(values["power_states"]) != 0 &&
			!contains(values["power_states"], machine.PowerState) {
			continue
		}
		elements = append(elements, summary{
			VM:            machine.ID,
			Name:          machine.Name,
			PowerState:    machine.PowerState,
			CPUCount:      machine.CPU.Count,
			MemorySizeMiB: 4096,
		})
	}
	s.mu.Unlock()
	writeJSON(w, http.StatusOK, elements)
}

func (s *Server) getCPU(w http.ResponseWriter, request *http.Request, vmID string) {
	if request.URL.RawQuery != "" || request.URL.ForceQuery {
		writeJSON(w, http.StatusBadRequest, newError(
			"INVALID_ARGUMENT",
			"Vcenter.Vm.Hardware.Cpu_get declares no query parameters",
		))
		return
	}
	s.mu.Lock()
	machine, ok := s.inventory[vmID]
	var info CPUInfo
	if ok {
		info = machine.CPU
	}
	s.mu.Unlock()
	if !ok {
		writeJSON(w, http.StatusNotFound, newError(
			"NOT_FOUND",
			"the virtual machine is not found",
		))
		return
	}
	if status, found := s.plan.CPUGetStatus[vmID]; found && status != 0 &&
		status != http.StatusOK {
		writeJSON(w, status, newError("ERROR", "the fixture rejected Cpu_get"))
		return
	}
	if body, found := s.plan.CPUGetBody[vmID]; found && body != "" {
		writeRaw(
			w,
			http.StatusOK,
			s.plan.CPUGetContentType[vmID],
			body,
		)
		return
	}
	writeJSON(w, http.StatusOK, info)
}

func (s *Server) updateCPU(
	w http.ResponseWriter,
	request *http.Request,
	vmID string,
	body []byte,
	token string,
) {
	if request.URL.RawQuery != "" || request.URL.ForceQuery {
		writeJSON(w, http.StatusBadRequest, newError(
			"INVALID_ARGUMENT",
			"Vcenter.Vm.Hardware.Cpu_update declares no query parameters",
		))
		return
	}
	if !strings.HasPrefix(request.Header.Get("Content-Type"), "application/json") {
		writeJSON(w, http.StatusBadRequest, newError(
			"INVALID_ARGUMENT",
			"Vcenter.Vm.Hardware.Cpu_update accepts application/json",
		))
		return
	}

	var spec struct {
		Count            *int64 `json:"count"`
		CoresPerSocket   *int64 `json:"cores_per_socket"`
		HotAddEnabled    *bool  `json:"hot_add_enabled"`
		HotRemoveEnabled *bool  `json:"hot_remove_enabled"`
	}
	decoder := json.NewDecoder(strings.NewReader(string(body)))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&spec) != nil {
		writeJSON(w, http.StatusBadRequest, newError(
			"INVALID_ARGUMENT",
			"the request body is not a Vcenter.Vm.Hardware.Cpu.UpdateSpec",
		))
		return
	}

	s.mu.Lock()
	machine, ok := s.inventory[vmID]
	s.mu.Unlock()
	if !ok {
		writeJSON(w, http.StatusNotFound, newError(
			"NOT_FOUND",
			"the virtual machine is not found",
		))
		return
	}

	// The specification permits the hot-plug members to be modified only while
	// the virtual machine is powered off.
	if (spec.HotAddEnabled != nil || spec.HotRemoveEnabled != nil) &&
		machine.PowerState != "POWERED_OFF" {
		writeJSON(w, http.StatusBadRequest, newError(
			"NOT_ALLOWED_IN_CURRENT_STATE",
			"hot_add_enabled and hot_remove_enabled require a powered off virtual machine",
		))
		return
	}
	if (spec.Count != nil && *spec.Count < 1) ||
		(spec.CoresPerSocket != nil && *spec.CoresPerSocket < 1) {
		writeJSON(w, http.StatusBadRequest, newError(
			"INVALID_ARGUMENT",
			"count and cores_per_socket must be positive",
		))
		return
	}
	if status, found := s.plan.CPUUpdateStatus[vmID]; found && status != 0 &&
		status != http.StatusNoContent {
		writeJSON(w, status, newError("ERROR", "the fixture rejected Cpu_update"))
		return
	}

	s.mu.Lock()
	if spec.Count != nil {
		machine.CPU.Count = *spec.Count
	}
	if spec.CoresPerSocket != nil {
		machine.CPU.CoresPerSocket = *spec.CoresPerSocket
	}
	if spec.HotAddEnabled != nil {
		machine.CPU.HotAddEnabled = *spec.HotAddEnabled
	}
	if spec.HotRemoveEnabled != nil {
		machine.CPU.HotRemoveEnabled = *spec.HotRemoveEnabled
	}
	s.applied = append(s.applied, Applied{
		VM:    vmID,
		Body:  append([]byte(nil), body...),
		Token: token,
	})
	s.mu.Unlock()

	w.WriteHeader(http.StatusNoContent)
}

func contains(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}

func newError(errorType string, message string) APIError {
	return APIError{
		ErrorType: errorType,
		Messages: []LocalizableMessage{{
			ID:             "com.vmware.vapi.fixture." + strings.ToLower(errorType),
			DefaultMessage: message,
			Args:           []string{},
		}},
	}
}

func unauthenticated() APIError {
	failure := newError("UNAUTHENTICATED", "the session token is not valid")
	failure.Challenge = `Basic realm="vCenter"`
	return failure
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeRaw(w http.ResponseWriter, status int, contentType string, body string) {
	if contentType == "" {
		contentType = "application/json"
	}
	w.Header().Set("Content-Type", contentType)
	w.WriteHeader(status)
	_, _ = io.WriteString(w, body)
}

// BasicAuthorization builds the basic_auth header value for the generated
// fixture credentials.
func (s *Server) BasicAuthorization() string {
	raw := s.runtime.Username + ":" + s.runtime.Password
	return "Basic " + base64.StdEncoding.EncodeToString([]byte(raw))
}

func randomValue(prefix string) string {
	var data [12]byte
	if _, err := rand.Read(data[:]); err != nil {
		panic("cannot generate protected fixture value")
	}
	return prefix + "-" + hex.EncodeToString(data[:])
}
