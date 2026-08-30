// Package vcmock is a loopback stand-in for a VMware Cloud Foundation 9.0
// vCenter appliance.
//
// It listens on 127.0.0.1, builds its routing table from docs/contract.json and
// serves only the operations that contract names. Anything else is answered
// with the specification's NotFound error and still recorded, so a test can
// prove a client never wandered off the contract. Every request that arrives is
// appended to a log the test can read back with Requests.
//
// No live VMware endpoint is contacted.
package vcmock

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"

	"vcf.local/vcenterchange/internal/contract"
)

// Power states, from the contract's powerState vocabulary.
const (
	PoweredOn  = "POWERED_ON"
	PoweredOff = "POWERED_OFF"
	Suspended  = "SUSPENDED"
)

// Recorded is one request the appliance received.
type Recorded struct {
	// Seq is the 1-based arrival order across the whole server.
	Seq int
	// OperationID is the contract operation that served the request, or "" if
	// the request matched no operation the contract names.
	OperationID string
	Method      string
	Path        string
	RawQuery    string
	Query       url.Values
	Header      http.Header
	// Body is the raw request body exactly as it arrived on the wire.
	Body []byte
	// PathParams holds the path parameters the contract bound, such as vm.
	PathParams map[string]string
	// Status is the status code the appliance answered with.
	Status int
}

// JSONBody decodes the recorded body into a generic JSON object.
func (r Recorded) JSONBody() (map[string]any, error) {
	var obj map[string]any
	if err := json.Unmarshal(r.Body, &obj); err != nil {
		return nil, fmt.Errorf("vcmock: request %d (%s) body is not a JSON object: %w", r.Seq, r.OperationID, err)
	}
	return obj, nil
}

// Failure injects an error response for one occurrence of an operation.
type Failure struct {
	// Operation is the operationId to fail.
	Operation string
	// Occurrence is the 1-based call number to fail. Zero fails every call.
	Occurrence int
	// Status is the HTTP status to answer with.
	Status int
	// ErrorType is the vAPI error_type discriminator, which must be a value
	// from the contract's errorType vocabulary.
	ErrorType string
	// Message becomes messages[0].default_message.
	Message string
}

// Config configures one appliance instance.
type Config struct {
	// SessionID is the value the appliance requires in the
	// vmware-api-session-id header. Empty means a generated identifier.
	SessionID string
	// InitialPowerState is the power state of the virtual machine before the
	// change set runs. Empty means POWERED_ON.
	InitialPowerState string
	// PowerStopAlreadyOff models a virtual machine that was powered off between
	// the caller's Vcenter.Vm.Power_get and its Vcenter.Vm.Power_stop. The first
	// Vcenter.Vm.Power_get answers POWERED_ON, the machine is in fact already
	// off, Vcenter.Vm.Power_stop answers 400 ALREADY_IN_DESIRED_STATE, and every
	// later Vcenter.Vm.Power_get answers POWERED_OFF.
	PowerStopAlreadyOff bool
	// DiskIDs are handed out in order by Vcenter.Vm.Hardware.Disk_create. Empty
	// means 2000, 2001, 2002 and so on.
	DiskIDs []string
	// Failures are injected error responses.
	Failures []Failure
}

// Server is a running loopback appliance.
type Server struct {
	// URL is the appliance root, with no path. The contract's /api base path
	// hangs off it.
	URL string
	// SessionID is the value the appliance requires in the
	// vmware-api-session-id header.
	SessionID string

	t        testing.TB
	contract *contract.Contract
	httptest *httptest.Server

	mu         sync.Mutex
	requests   []Recorded
	calls      map[string]int
	powerState string
	powerGets  int
	staleOn    bool
	diskIDs    []string
	diskNext   int
	failures   []Failure
}

// New starts an appliance on 127.0.0.1 and registers its shutdown with t.
func New(t testing.TB, cfg Config) *Server {
	t.Helper()
	c, err := contract.LoadDefault()
	if err != nil {
		t.Fatalf("vcmock: %v", err)
	}

	sessionID := cfg.SessionID
	if sessionID == "" {
		sessionID = "vcmock-session-0f3a1c7e"
	}
	state := cfg.InitialPowerState
	if state == "" {
		state = PoweredOn
	}
	if v, ok := c.Vocabulary("powerState"); ok && !v.Has(state) {
		t.Fatalf("vcmock: InitialPowerState %q is not in the contract powerState vocabulary %v", state, v.Values)
	}
	diskIDs := cfg.DiskIDs
	if len(diskIDs) == 0 {
		diskIDs = []string{"2000", "2001", "2002", "2003"}
	}
	if v, ok := c.Vocabulary("errorType"); ok {
		for _, f := range cfg.Failures {
			if !v.Has(f.ErrorType) {
				t.Fatalf("vcmock: Failure.ErrorType %q is not in the contract errorType vocabulary", f.ErrorType)
			}
			if _, found := c.Operation(f.Operation); !found {
				t.Fatalf("vcmock: Failure.Operation %q is not named by the contract", f.Operation)
			}
		}
	}

	s := &Server{
		SessionID:  sessionID,
		t:          t,
		contract:   c,
		calls:      map[string]int{},
		powerState: state,
		diskIDs:    diskIDs,
		failures:   append([]Failure(nil), cfg.Failures...),
	}
	if cfg.PowerStopAlreadyOff {
		s.powerState = PoweredOff
		s.staleOn = true
	}

	s.httptest = httptest.NewServer(http.HandlerFunc(s.serve))
	s.URL = s.httptest.URL
	t.Cleanup(s.httptest.Close)
	return s
}

// Close shuts the appliance down. New already registers this with t.Cleanup.
func (s *Server) Close() { s.httptest.Close() }

// Requests returns a copy of the request log in arrival order.
func (s *Server) Requests() []Recorded {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Recorded(nil), s.requests...)
}

// OperationIDs returns the operationId of each logged request in arrival order.
// A request that matched no contract operation contributes "".
func (s *Server) OperationIDs() []string {
	out := []string{}
	for _, r := range s.Requests() {
		out = append(out, r.OperationID)
	}
	return out
}

// RequestsFor returns the logged requests served by one operation, in order.
func (s *Server) RequestsFor(operationID string) []Recorded {
	out := []Recorded{}
	for _, r := range s.Requests() {
		if r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

// PowerState returns the current power state of the virtual machine.
func (s *Server) PowerState() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.powerState
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	_ = r.Body.Close()

	basePath := s.contract.API.BasePath
	routePath := r.URL.Path
	baseOK := strings.HasPrefix(routePath, basePath)
	if baseOK {
		routePath = strings.TrimPrefix(routePath, basePath)
		if routePath == "" {
			routePath = "/"
		}
	}

	var (
		op     contract.Operation
		params map[string]string
		found  bool
	)
	if baseOK {
		op, params, found = s.contract.Match(r.Method, routePath, r.URL.Query())
	}

	rec := Recorded{
		OperationID: op.OperationID,
		Method:      r.Method,
		Path:        r.URL.Path,
		RawQuery:    r.URL.RawQuery,
		Query:       r.URL.Query(),
		Header:      r.Header.Clone(),
		Body:        body,
		PathParams:  params,
	}
	if !found {
		rec.OperationID = ""
	}

	status, payload := s.dispatch(op, found, rec)
	rec.Status = status

	s.mu.Lock()
	rec.Seq = len(s.requests) + 1
	s.requests = append(s.requests, rec)
	s.mu.Unlock()

	if payload == nil {
		w.WriteHeader(status)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

// dispatch decides the answer for one request without touching the log.
func (s *Server) dispatch(op contract.Operation, found bool, rec Recorded) (int, any) {
	if !found {
		return http.StatusNotFound, vapiError("NOT_FOUND",
			fmt.Sprintf("This appliance serves only the operations docs/contract.json names; %s %s is not one of them.", rec.Method, rec.Path))
	}
	if got := rec.Header.Get(s.contract.Authorization.HeaderName); got != s.SessionID {
		return http.StatusUnauthorized, vapiError("UNAUTHENTICATED",
			fmt.Sprintf("The %s header is missing or does not identify an active session.", s.contract.Authorization.HeaderName))
	}
	if accept := rec.Header.Get("Accept"); accept != "" && !strings.Contains(accept, "application/json") && !strings.Contains(accept, "*/*") {
		return http.StatusNotAcceptable, vapiError("UNSUPPORTED",
			fmt.Sprintf("Accept %q cannot be satisfied; this operation produces application/json.", accept))
	}

	if op.HasBody() {
		if ct := rec.Header.Get("Content-Type"); !strings.HasPrefix(ct, op.RequestContentType) {
			return http.StatusBadRequest, vapiError("INVALID_ARGUMENT",
				fmt.Sprintf("%s requires Content-Type %s, got %q.", op.OperationID, op.RequestContentType, ct))
		}
		var obj map[string]any
		if err := json.Unmarshal(rec.Body, &obj); err != nil {
			return http.StatusBadRequest, vapiError("INVALID_ARGUMENT",
				fmt.Sprintf("%s request body is not a JSON object.", op.OperationID))
		}
		if msg := s.validate(op.RequestSchema, obj, op.RequestSchema); msg != "" {
			return http.StatusBadRequest, vapiError("INVALID_ARGUMENT", msg)
		}
	} else {
		if len(rec.Body) > 0 {
			return http.StatusBadRequest, vapiError("INVALID_ARGUMENT",
				fmt.Sprintf("%s takes no request body.", op.OperationID))
		}
		if rec.Header.Get("Content-Type") != "" {
			return http.StatusBadRequest, vapiError("INVALID_ARGUMENT",
				fmt.Sprintf("%s carries no body and must not send Content-Type.", op.OperationID))
		}
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	s.calls[op.OperationID]++
	n := s.calls[op.OperationID]
	for _, f := range s.failures {
		if f.Operation == op.OperationID && (f.Occurrence == 0 || f.Occurrence == n) {
			return f.Status, vapiError(f.ErrorType, f.Message)
		}
	}

	switch op.OperationID {
	case "Vcenter.Vm.Power_get":
		s.powerGets++
		state := s.powerState
		if s.staleOn && s.powerGets == 1 {
			state = PoweredOn
		}
		return op.SuccessStatus, map[string]any{"state": state}

	case "Vcenter.Vm.Power_stop":
		if s.powerState == PoweredOff {
			return http.StatusBadRequest, vapiError("ALREADY_IN_DESIRED_STATE",
				"The virtual machine is already powered off.")
		}
		s.powerState = PoweredOff
		return op.SuccessStatus, nil

	case "Vcenter.Vm.Power_start":
		if s.powerState == PoweredOn {
			return http.StatusBadRequest, vapiError("ALREADY_IN_DESIRED_STATE",
				"The virtual machine is already powered on.")
		}
		s.powerState = PoweredOn
		return op.SuccessStatus, nil

	case "Vcenter.Vm.Hardware.Memory_update", "Vcenter.Vm.Hardware.Cpu_update":
		if s.powerState != PoweredOff {
			return http.StatusBadRequest, vapiError("NOT_ALLOWED_IN_CURRENT_STATE",
				"This setting may only be modified while the virtual machine is powered off.")
		}
		return op.SuccessStatus, nil

	case "Vcenter.Vm.Hardware.Disk_create":
		if s.diskNext >= len(s.diskIDs) {
			return http.StatusBadRequest, vapiError("UNABLE_TO_ALLOCATE_RESOURCE",
				"No further virtual disk device slots are available on this virtual machine.")
		}
		id := s.diskIDs[s.diskNext]
		s.diskNext++
		return op.SuccessStatus, id

	default:
		return http.StatusNotFound, vapiError("OPERATION_NOT_FOUND",
			fmt.Sprintf("%s is named by the contract but not implemented by this appliance.", op.OperationID))
	}
}

// validate checks a decoded request body against a contract schema, rejecting a
// property the schema does not declare and a required property that is absent.
// It recurses into nested schemas so an scsi or new_vmdk object is checked too.
func (s *Server) validate(schemaName string, obj map[string]any, path string) string {
	schema, ok := s.contract.Schema(schemaName)
	if !ok {
		return fmt.Sprintf("contract names no schema %s", schemaName)
	}
	for key, value := range obj {
		if !schema.Allows(key) {
			return fmt.Sprintf("%s.%s is not a property of %s; allowed: %s",
				path, key, schemaName, strings.Join(schema.AllowedProperties, ", "))
		}
		prop := schema.Properties[key]
		if prop.SchemaName == "" {
			continue
		}
		nested, isObj := value.(map[string]any)
		if !isObj {
			return fmt.Sprintf("%s.%s must be a %s object", path, key, prop.SchemaName)
		}
		if msg := s.validate(prop.SchemaName, nested, path+"."+key); msg != "" {
			return msg
		}
	}
	for _, required := range schema.RequiredProperties {
		if _, present := obj[required]; !present {
			return fmt.Sprintf("%s.%s is required by %s and is missing", path, required, schemaName)
		}
	}
	return ""
}

// vapiError builds a Vapi.Std.Errors.Error payload.
func vapiError(errorType, message string) map[string]any {
	return map[string]any{
		"error_type": errorType,
		"messages": []map[string]any{{
			"id":              "vcmock." + strings.ToLower(errorType),
			"default_message": message,
			"args":            []string{},
		}},
	}
}
