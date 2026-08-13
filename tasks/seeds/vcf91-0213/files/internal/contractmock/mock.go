// Package contractmock provides the loopback-only VCF Installer fixture.
package contractmock

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"sync"
	"testing"
)

const (
	pinnedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	pinnedPath   = "specifications/vcf-installer/vcf-installer-openapi.json"
)

type contractDocument struct {
	Source struct {
		RepositoryCommitSHA string `json:"repositoryCommitSha"`
		SpecPath            string `json:"specPath"`
		InfoVersion         string `json:"infoVersion"`
	} `json:"source"`
	Operations []operation `json:"operations"`
}

type operation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
	Responses   map[string]struct {
		Description string `json:"description"`
	} `json:"responses"`
}

// Scenario selects one operation and one specification-declared failure
// status. Empty FailOperation means every operation is accepted.
type Scenario struct {
	FailOperation string
	FailStatus    int
}

// Request is a server-side record used for exact wire verification.
type Request struct {
	OperationID      string
	Method           string
	RawTarget        string
	Header           http.Header
	Body             []byte
	ContentLength    int64
	TransferEncoding []string
}

// Server is a contract-pinned, loopback-only mock with a synchronized log.
type Server struct {
	httpServer *httptest.Server
	routes     map[string]operation
	scenario   Scenario

	mu       sync.Mutex
	requests []Request
}

// Start loads the focused contract and starts an ephemeral IPv4 loopback
// server exposing only the three projected operations.
func Start(t testing.TB, contractPath string, scenario Scenario) *Server {
	t.Helper()
	data, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read focused contract: %v", err)
	}
	var document contractDocument
	if err := json.Unmarshal(data, &document); err != nil {
		t.Fatalf("decode focused contract: %v", err)
	}
	if document.Source.RepositoryCommitSHA != pinnedCommit || document.Source.SpecPath != pinnedPath || document.Source.InfoVersion != "9.1.0.0" {
		t.Fatal("focused contract is not pinned to the VCF Installer 9.1 source")
	}
	want := map[string]operation{
		"updateProxyConfiguration": {
			OperationID: "updateProxyConfiguration",
			Method:      http.MethodPatch,
			Path:        "/v1/system/proxy-configuration",
		},
		"updateDepotSettings": {
			OperationID: "updateDepotSettings",
			Method:      http.MethodPut,
			Path:        "/v1/system/settings/depot",
		},
		"syncDepotMetadata": {
			OperationID: "syncDepotMetadata",
			Method:      http.MethodPatch,
			Path:        "/v1/system/settings/depot/depot-sync-info",
		},
	}
	if len(document.Operations) != len(want) {
		t.Fatalf("focused operation count = %d, want %d", len(document.Operations), len(want))
	}
	routes := make(map[string]operation, len(want))
	byID := make(map[string]operation, len(want))
	for _, got := range document.Operations {
		expected, ok := want[got.OperationID]
		if !ok || got.Method != expected.Method || got.Path != expected.Path {
			t.Fatalf("operation outside focused contract: %+v", got)
		}
		key := got.Method + " " + got.Path
		if _, duplicate := routes[key]; duplicate {
			t.Fatalf("duplicate contract route %s", key)
		}
		routes[key] = got
		byID[got.OperationID] = got
	}
	if scenario.FailOperation != "" {
		op, ok := byID[scenario.FailOperation]
		if !ok {
			t.Fatalf("failure operation %q is outside focused contract", scenario.FailOperation)
		}
		if _, ok := op.Responses[fmt.Sprint(scenario.FailStatus)]; !ok || scenario.FailStatus == http.StatusAccepted {
			t.Fatalf("failure status %d is not a declared failure for %s", scenario.FailStatus, scenario.FailOperation)
		}
	}

	s := &Server{routes: routes, scenario: scenario}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen on loopback: %v", err)
	}
	s.httpServer = &httptest.Server{
		Listener: listener,
		Config:   &http.Server{Handler: http.HandlerFunc(s.serveHTTP)},
	}
	s.httpServer.Start()
	parsed, err := url.Parse(s.httpServer.URL)
	if err != nil {
		s.httpServer.Close()
		t.Fatalf("parse mock URL: %v", err)
	}
	host, _, err := net.SplitHostPort(parsed.Host)
	if err != nil || host != "127.0.0.1" || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		s.httpServer.Close()
		t.Fatalf("mock did not bind to loopback: %q", parsed.Host)
	}
	t.Cleanup(s.Close)
	return s
}

// URL returns the loopback service root.
func (s *Server) URL() string { return s.httpServer.URL }

// Close stops the mock.
func (s *Server) Close() { s.httpServer.Close() }

// Requests returns a deep copy of the synchronized request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	result := make([]Request, len(s.requests))
	for index, request := range s.requests {
		result[index] = request
		result[index].Header = request.Header.Clone()
		result[index].Body = append([]byte(nil), request.Body...)
		result[index].TransferEncoding = append([]string(nil), request.TransferEncoding...)
	}
	return result
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	key := r.Method + " " + r.URL.EscapedPath()
	op, ok := s.routes[key]
	operationID := ""
	if ok {
		operationID = op.OperationID
	}
	record := Request{
		OperationID:      operationID,
		Method:           r.Method,
		RawTarget:        r.RequestURI,
		Header:           r.Header.Clone(),
		Body:             append([]byte(nil), body...),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
	}
	s.mu.Lock()
	s.requests = append(s.requests, record)
	s.mu.Unlock()

	if !ok {
		s.writeJSON(w, http.StatusNotFound, map[string]string{
			"errorCode": "NOT_IN_FOCUSED_CONTRACT",
			"message":   "operation is outside the focused contract",
		})
		return
	}
	if s.scenario.FailOperation == operationID {
		s.writeJSON(w, s.scenario.FailStatus, failureDocument(operationID))
		return
	}
	switch operationID {
	case "updateProxyConfiguration":
		s.writeJSON(w, http.StatusAccepted, map[string]any{
			"id":                "task-proxy-0213",
			"name":              "Update Proxy Configuration",
			"status":            "IN_PROGRESS",
			"creationTimestamp": "2026-05-13T12:00:00Z",
		})
	case "updateDepotSettings":
		s.writeJSON(w, http.StatusAccepted, map[string]any{
			"vmwareAccount": map[string]string{
				"status":  "DEPOT_CONNECTION_SUCCESSFUL",
				"message": "Credentials accepted",
			},
			"depotConfiguration": map[string]bool{"isOfflineDepot": false},
		})
	case "syncDepotMetadata":
		s.writeJSON(w, http.StatusAccepted, map[string]string{"syncStatus": "IN_PROGRESS"})
	default:
		panic("unhandled focused operation " + operationID)
	}
}

func failureDocument(operationID string) map[string]string {
	switch operationID {
	case "updateProxyConfiguration":
		return map[string]string{
			"errorCode":          "VCF_PROXY_REJECTED",
			"errorType":          "INVALID_CONFIGURATION",
			"message":            "Proxy configuration was rejected",
			"remediationMessage": "Correct the proxy endpoint.",
			"referenceToken":     "ref-proxy-0213",
		}
	case "updateDepotSettings":
		return map[string]string{
			"errorCode":          "VCF_DEPOT_SETTINGS_FAILED",
			"errorType":          "INTERNAL_SERVER_ERROR",
			"message":            "Depot credentials could not be saved",
			"remediationMessage": "Verify the activation code.",
			"referenceToken":     "ref-settings-0213",
		}
	case "syncDepotMetadata":
		return map[string]string{
			"errorCode":          "VCF_DEPOT_SYNC_FAILED",
			"errorType":          "INTERNAL_SERVER_ERROR",
			"message":            "Depot metadata index could not be refreshed",
			"remediationMessage": "Retry after depot connectivity is restored.",
			"referenceToken":     "ref-sync-0213",
		}
	default:
		panic("unhandled focused operation " + operationID)
	}
}

func (s *Server) writeJSON(w http.ResponseWriter, status int, document any) {
	encoded, err := json.Marshal(document)
	if err != nil {
		panic(err)
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Length", fmt.Sprint(len(encoded)))
	w.WriteHeader(status)
	_, _ = w.Write(encoded)
}

// String makes unexpected route records compact in failures.
func (r Request) String() string {
	return fmt.Sprintf("%s %s (%s)", r.Method, r.RawTarget, r.OperationID)
}
