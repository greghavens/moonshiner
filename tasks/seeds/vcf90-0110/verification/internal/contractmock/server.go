package contractmock

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
)

const (
	ValidationID = "123e4567-e89b-42d3-a456-556642440000"
	TaskID       = "223e4567-e89b-42d3-a456-556642440000"
)

// Scenario selects one deterministic service outcome for verification.
type Scenario int

const (
	SuccessfulRefresh Scenario = iota
	ValidationRejected
	ValidationUnsuccessful
	ValidationPollRejected
	RefreshRejected
	DeploymentRejected
	ConcurrentRefresh
)

var expectedOperations = map[string]struct {
	Method string
	Path   string
}{
	"validateSddcSpec":      {Method: http.MethodPost, Path: "/v1/sddcs/validations"},
	"getSddcSpecValidation": {Method: http.MethodGet, Path: "/v1/sddcs/validations/{id}"},
	"refreshAccessToken":    {Method: http.MethodPatch, Path: "/v1/tokens/access-token/refresh"},
	"deploySddc":            {Method: http.MethodPost, Path: "/v1/sddcs"},
}

type contractDocument struct {
	APIVersion string `json:"apiVersion"`
	Source     struct {
		Tag       string `json:"tag"`
		CommitSHA string `json:"commitSha"`
		Path      string `json:"path"`
	} `json:"source"`
	Operations []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	} `json:"operations"`
}

type Request struct {
	Method        string
	Path          string
	RawQuery      string
	Authorization string
	Accept        string
	ContentType   string
	Body          []byte
}

type Server struct {
	server   *httptest.Server
	routes   map[string]string
	scenario Scenario

	mu                      sync.Mutex
	requests                []Request
	successfulNewTokenPolls int
}

func New(contractJSON []byte) (*Server, error) {
	return NewForScenario(contractJSON, SuccessfulRefresh)
}

func NewForScenario(contractJSON []byte, scenario Scenario) (*Server, error) {
	var contract contractDocument
	if err := json.Unmarshal(contractJSON, &contract); err != nil {
		return nil, fmt.Errorf("decode contract: %w", err)
	}
	if contract.APIVersion != "9.0.0.0" || contract.Source.Tag != "9.0.0.0" ||
		contract.Source.CommitSHA != "85151f6b1bb58f13b6ac0304bfec53904bea085f" ||
		contract.Source.Path != "specifications/vcf-installer/vcf-installer-openapi.json" {
		return nil, errors.New("contract is not pinned to the VCF Installer 9.0.0.0 specification")
	}
	if len(contract.Operations) != len(expectedOperations) {
		return nil, fmt.Errorf("contract names %d operations, want %d", len(contract.Operations), len(expectedOperations))
	}

	routes := make(map[string]string, len(contract.Operations))
	for _, operation := range contract.Operations {
		expected, ok := expectedOperations[operation.OperationID]
		if !ok || operation.Method != expected.Method || operation.Path != expected.Path {
			return nil, fmt.Errorf("unexpected contract operation %q", operation.OperationID)
		}
		routes[operation.Method+" "+operation.Path] = operation.OperationID
	}

	s := &Server{routes: routes, scenario: scenario}
	s.server = httptest.NewServer(http.HandlerFunc(s.serveHTTP))
	return s, nil
}

func (s *Server) URL() string { return s.server.URL }

func (s *Server) Client() *http.Client { return s.server.Client() }

func (s *Server) Close() { s.server.Close() }

func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	requests := make([]Request, len(s.requests))
	for i, request := range s.requests {
		requests[i] = request
		requests[i].Body = append([]byte(nil), request.Body...)
	}
	return requests
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "read request", http.StatusBadRequest)
		return
	}

	s.mu.Lock()
	s.requests = append(s.requests, Request{
		Method:        r.Method,
		Path:          r.URL.Path,
		RawQuery:      r.URL.RawQuery,
		Authorization: r.Header.Get("Authorization"),
		Accept:        r.Header.Get("Accept"),
		ContentType:   r.Header.Get("Content-Type"),
		Body:          append([]byte(nil), body...),
	})
	s.mu.Unlock()

	contractPath := r.URL.Path
	if strings.HasPrefix(contractPath, "/v1/sddcs/validations/") {
		contractPath = "/v1/sddcs/validations/{id}"
	}
	operationID, ok := s.routes[r.Method+" "+contractPath]
	if !ok {
		http.NotFound(w, r)
		return
	}

	switch operationID {
	case "validateSddcSpec":
		validAuthorization := r.Header.Get("Authorization") == "Bearer access-old" ||
			(s.scenario == ConcurrentRefresh && r.Header.Get("Authorization") == "Bearer access-new")
		if !validJSONContentType(r) || !validAuthorization || len(body) == 0 {
			writeJSON(w, http.StatusBadRequest, map[string]string{"message": "invalid validation request"})
			return
		}
		if s.scenario == ValidationRejected {
			writeJSON(w, http.StatusUnprocessableEntity, map[string]string{"message": "specification was rejected"})
			return
		}
		writeJSON(w, http.StatusAccepted, map[string]string{
			"id": ValidationID, "description": "fixture validation", "executionStatus": "IN_PROGRESS", "resultStatus": "UNKNOWN",
		})
	case "getSddcSpecValidation":
		if r.URL.Path != "/v1/sddcs/validations/"+ValidationID {
			http.NotFound(w, r)
			return
		}
		if s.scenario == ValidationPollRejected {
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"message": "validation lookup unavailable"})
			return
		}
		switch r.Header.Get("Authorization") {
		case "Bearer access-old":
			writeJSON(w, http.StatusUnauthorized, map[string]string{"message": "access token expired"})
		case "Bearer access-new":
			if s.scenario == ValidationUnsuccessful {
				writeJSON(w, http.StatusOK, map[string]string{
					"id": ValidationID, "description": "fixture validation", "executionStatus": "COMPLETED", "resultStatus": "FAILED",
				})
				return
			}
			if s.scenario == ConcurrentRefresh {
				writeJSON(w, http.StatusOK, map[string]string{
					"id": ValidationID, "description": "fixture validation", "executionStatus": "COMPLETED", "resultStatus": "SUCCEEDED",
				})
				return
			}
			s.mu.Lock()
			s.successfulNewTokenPolls++
			poll := s.successfulNewTokenPolls
			s.mu.Unlock()
			if poll == 1 {
				writeJSON(w, http.StatusOK, map[string]string{
					"id": ValidationID, "description": "fixture validation", "executionStatus": "IN_PROGRESS", "resultStatus": "UNKNOWN",
				})
				return
			}
			writeJSON(w, http.StatusOK, map[string]string{
				"id": ValidationID, "description": "fixture validation", "executionStatus": "COMPLETED", "resultStatus": "SUCCEEDED",
			})
		default:
			writeJSON(w, http.StatusUnauthorized, map[string]string{"message": "invalid access token"})
		}
	case "refreshAccessToken":
		if !validJSONContentType(r) || r.Header.Get("Authorization") != "" || string(body) != `"refresh-fixture"` {
			writeJSON(w, http.StatusBadRequest, map[string]string{"message": "invalid refresh request"})
			return
		}
		if s.scenario == RefreshRejected {
			writeJSON(w, http.StatusForbidden, map[string]string{"message": "refresh token rejected"})
			return
		}
		writeJSON(w, http.StatusOK, "access-new")
	case "deploySddc":
		if !validJSONContentType(r) || r.Header.Get("Authorization") != "Bearer access-new" || len(body) == 0 {
			writeJSON(w, http.StatusBadRequest, map[string]string{"message": "invalid deployment request"})
			return
		}
		if s.scenario == DeploymentRejected {
			writeJSON(w, http.StatusConflict, map[string]string{"message": "deployment conflict"})
			return
		}
		writeJSON(w, http.StatusAccepted, map[string]string{
			"id": TaskID, "name": "VCF installation", "status": "IN_PROGRESS", "creationTimestamp": "2025-06-17T12:00:00Z",
		})
	}
}

func validJSONContentType(r *http.Request) bool {
	return r.Header.Get("Content-Type") == "application/json"
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
