// Package contractmock provides the protected loopback SDDC Manager fixture.
package contractmock

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"sync"
)

const (
	ValidateALBControllerClusterCreationSpec = "validateALBControllerClusterCreationSpec"
	DeployALBCluster                         = "deployALBCluster"
)

// Plan selects contract-valid precheck and mutation outcomes.
type Plan struct {
	PrecheckExecutionStatus string
	PrecheckResultStatus    string
	PrecheckStatus          int
	DeployStatus            int
}

// Request is one request observed by the loopback server.
type Request struct {
	OperationID      string
	Method           string
	RequestURI       string
	Path             string
	RawQuery         string
	Host             string
	Header           http.Header
	ContentLength    int64
	TransferEncoding []string
	Body             []byte
	ResponseStatus   int
}

// Server is a contract-scoped loopback SDDC Manager.
type Server struct {
	httpServer *httptest.Server
	plan       Plan
	token      string
	routes     map[string]string

	mu           sync.Mutex
	requests     []Request
	precheckBody []byte
	precheckPass bool
	effectCount  int
}

type contractFile struct {
	Operations []struct {
		OperationID     string `json:"operationId"`
		Method          string `json:"method"`
		Path            string `json:"path"`
		QueryParameters []struct {
			Name     string `json:"name"`
			In       string `json:"in"`
			Required bool   `json:"required"`
			Schema   struct {
				Type    string `json:"type"`
				Default bool   `json:"default"`
			} `json:"schema"`
		} `json:"query_parameters"`
		RequestBody struct {
			Required  bool   `json:"required"`
			MediaType string `json:"media_type"`
			SchemaRef string `json:"schema_ref"`
		} `json:"request_body"`
		Responses map[string]struct {
			SchemaRef string `json:"schema_ref"`
		} `json:"responses"`
	} `json:"operations"`
}

// New starts a loopback server on an ephemeral IPv4 address.
func New(plan Plan) *Server {
	routes := loadPinnedRoutes()
	if plan.PrecheckExecutionStatus == "" {
		plan.PrecheckExecutionStatus = "COMPLETED"
	}
	if plan.PrecheckResultStatus == "" {
		plan.PrecheckResultStatus = "SUCCEEDED"
	}
	if plan.PrecheckStatus == 0 {
		plan.PrecheckStatus = http.StatusOK
	}
	if plan.DeployStatus == 0 {
		plan.DeployStatus = http.StatusAccepted
	}
	server := &Server{
		plan:   plan,
		token:  randomValue("access"),
		routes: routes,
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

// URL returns the loopback origin.
func (s *Server) URL() string {
	return s.httpServer.URL
}

// Client returns the loopback server's HTTP client.
func (s *Server) Client() *http.Client {
	return s.httpServer.Client()
}

// Token returns the per-server bearer token generated at runtime.
func (s *Server) Token() string {
	return s.token
}

// Requests returns a deep copy of the race-safe request log.
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

// EffectCount reports accepted deployment mutations.
func (s *Server) EffectCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.effectCount
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	operationID := s.routes[r.Method+" "+r.URL.Path]
	status := s.handle(operationID, r, body)
	s.record(Request{
		OperationID:      operationID,
		Method:           r.Method,
		RequestURI:       r.RequestURI,
		Path:             r.URL.Path,
		RawQuery:         r.URL.RawQuery,
		Host:             r.Host,
		Header:           r.Header.Clone(),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
		Body:             append([]byte(nil), body...),
		ResponseStatus:   status,
	})

	switch {
	case operationID == ValidateALBControllerClusterCreationSpec &&
		status == http.StatusOK:
		writeJSON(w, status, map[string]string{
			"id":              randomValue("validation"),
			"description":     "ALB deployment precheck",
			"executionStatus": s.plan.PrecheckExecutionStatus,
			"resultStatus":    s.plan.PrecheckResultStatus,
		})
	case operationID == DeployALBCluster && status == http.StatusAccepted:
		writeJSON(w, status, map[string]string{
			"id":                randomValue("task"),
			"name":              "Deploy ALB cluster",
			"status":            "IN_PROGRESS",
			"creationTimestamp": "2026-01-15T12:00:00Z",
		})
	default:
		writeError(w, status)
	}
}

func (s *Server) handle(operationID string, r *http.Request, body []byte) int {
	if operationID == "" {
		return http.StatusNotFound
	}
	if r.Header.Get("Authorization") != "Bearer "+s.token ||
		r.Header.Get("Accept") != "application/json" ||
		r.Header.Get("Content-Type") != "application/json" ||
		!validQuery(r.URL.RawQuery) ||
		!validJSONObject(body) {
		return http.StatusBadRequest
	}

	switch operationID {
	case ValidateALBControllerClusterCreationSpec:
		s.mu.Lock()
		s.precheckBody = append([]byte(nil), body...)
		s.precheckPass = s.plan.PrecheckStatus == http.StatusOK &&
			s.plan.PrecheckExecutionStatus == "COMPLETED" &&
			s.plan.PrecheckResultStatus == "SUCCEEDED"
		s.mu.Unlock()
		return s.plan.PrecheckStatus
	case DeployALBCluster:
		s.mu.Lock()
		defer s.mu.Unlock()
		if !s.precheckPass || !bytes.Equal(s.precheckBody, body) {
			return http.StatusConflict
		}
		if s.plan.DeployStatus == http.StatusAccepted {
			s.effectCount++
		}
		return s.plan.DeployStatus
	default:
		return http.StatusNotFound
	}
}

func validQuery(rawQuery string) bool {
	return rawQuery == "" ||
		rawQuery == "skipCompatibilityCheck=false" ||
		rawQuery == "skipCompatibilityCheck=true"
}

func validJSONObject(body []byte) bool {
	var value map[string]any
	decoder := json.NewDecoder(bytes.NewReader(body))
	if err := decoder.Decode(&value); err != nil || value == nil {
		return false
	}
	var extra any
	return decoder.Decode(&extra) == io.EOF
}

func (s *Server) record(request Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, request)
}

func loadPinnedRoutes() map[string]string {
	_, sourceFile, _, ok := runtime.Caller(0)
	if !ok {
		panic("cannot locate contract mock source")
	}
	contractPath := filepath.Join(
		filepath.Dir(sourceFile),
		"..",
		"..",
		"docs",
		"contract.json",
	)
	content, err := os.ReadFile(contractPath)
	if err != nil {
		panic("cannot read protected contract")
	}
	var contract contractFile
	if err := json.Unmarshal(content, &contract); err != nil {
		panic("cannot decode protected contract")
	}
	if len(contract.Operations) != 2 {
		panic("contract mock requires exactly two operations")
	}

	want := map[string]string{
		ValidateALBControllerClusterCreationSpec: "/v1/alb-clusters/validations",
		DeployALBCluster:                         "/v1/alb-clusters",
	}
	routes := make(map[string]string, len(want))
	for _, operation := range contract.Operations {
		path, exists := want[operation.OperationID]
		successStatus := "200"
		successSchema := "#/components/schemas/Validation"
		if operation.OperationID == DeployALBCluster {
			successStatus = "202"
			successSchema = "#/components/schemas/Task"
		}
		if !exists ||
			operation.Method != http.MethodPost ||
			operation.Path != path ||
			len(operation.QueryParameters) != 1 ||
			operation.QueryParameters[0].Name != "skipCompatibilityCheck" ||
			operation.QueryParameters[0].In != "query" ||
			operation.QueryParameters[0].Required ||
			operation.QueryParameters[0].Schema.Type != "boolean" ||
			!operation.RequestBody.Required ||
			operation.RequestBody.MediaType != "application/json" ||
			operation.RequestBody.SchemaRef !=
				"#/components/schemas/AlbControllerClusterSpec" ||
			len(operation.Responses) != 3 ||
			operation.Responses[successStatus].SchemaRef != successSchema ||
			operation.Responses["400"].SchemaRef !=
				"#/components/schemas/Error" ||
			operation.Responses["500"].SchemaRef !=
				"#/components/schemas/Error" {
			panic("protected contract does not match the loopback routes")
		}
		routes[operation.Method+" "+operation.Path] = operation.OperationID
		delete(want, operation.OperationID)
	}
	if len(want) != 0 {
		panic("protected contract is missing a required operation")
	}
	return routes
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int) {
	writeJSON(w, status, map[string]string{
		"errorCode":          statusCode(status),
		"message":            "the ALB request did not complete",
		"remediationMessage": "correct the request or precheck result",
		"referenceToken":     "loopback-reference",
	})
}

func statusCode(status int) string {
	switch status {
	case http.StatusBadRequest:
		return "BAD_REQUEST"
	case http.StatusConflict:
		return "PRECHECK_REQUIRED"
	case http.StatusInternalServerError:
		return "INTERNAL_SERVER_ERROR"
	case http.StatusNotFound:
		return "NOT_IN_CONTRACT"
	default:
		return "UNEXPECTED_STATUS"
	}
}

func randomValue(prefix string) string {
	var bytes [16]byte
	if _, err := rand.Read(bytes[:]); err != nil {
		panic("cannot create loopback fixture value")
	}
	return prefix + "-" + hex.EncodeToString(bytes[:])
}
