// Package vcfmock provides the loopback VCF Automation service used by tests.
package vcfmock

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sync"
	"testing"
)

// Operation identifies one operation served by the mock.
type Operation struct {
	Method string
	Path   string
}

// Operations is the complete operation set pinned by docs/contract.json.
func Operations() []Operation {
	return []Operation{
		{Method: http.MethodPost, Path: "/catalog/api/items/{id}/request"},
		{Method: http.MethodGet, Path: "/deployment/api/deployments/{deploymentId}"},
		{Method: http.MethodPost, Path: "/oidc/oauth2/token"},
	}
}

// Request is one byte-preserving entry from the mock's request log.
type Request struct {
	Method     string
	Path       string
	RequestURI string
	Query      string
	Header     http.Header
	Body       string
}

// Fixture supplies fake values while leaving the operation contract fixed.
type Fixture struct {
	ItemID               string
	DeploymentID         string
	DeploymentName       string
	ClientID             string
	ClientSecret         string
	AccessToken          string
	RefreshToken         string
	RefreshedAccessToken string
}

// Server is a contract-pinned, loopback-only VCF Automation fixture.
type Server struct {
	httpServer *httptest.Server
	mu         sync.Mutex
	requests   []Request
	fixture    Fixture
}

// New starts a loopback server. Call Close when the test is done.
func New(t testing.TB, fixture Fixture) *Server {
	t.Helper()
	if fixture.ItemID == "" || fixture.DeploymentID == "" || fixture.DeploymentName == "" ||
		fixture.ClientID == "" || fixture.ClientSecret == "" || fixture.AccessToken == "" ||
		fixture.RefreshToken == "" || fixture.RefreshedAccessToken == "" {
		t.Fatal("vcfmock: every fixture value is required")
	}
	s := &Server{fixture: fixture}
	s.httpServer = httptest.NewServer(http.HandlerFunc(s.serveHTTP))
	t.Cleanup(s.Close)
	return s
}

// URL returns the loopback base URL.
func (s *Server) URL() string { return s.httpServer.URL }

// Client returns the transport configured for this loopback server.
func (s *Server) Client() *http.Client { return s.httpServer.Client() }

// Close stops the loopback server.
func (s *Server) Close() { s.httpServer.Close() }

// Log returns an isolated copy of the request log.
func (s *Server) Log() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	result := make([]Request, len(s.requests))
	for i, entry := range s.requests {
		result[i] = entry
		result[i].Header = entry.Header.Clone()
	}
	return result
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "cannot read request", http.StatusBadRequest)
		return
	}
	s.mu.Lock()
	s.requests = append(s.requests, Request{
		Method:     r.Method,
		Path:       r.URL.Path,
		RequestURI: r.RequestURI,
		Query:      r.URL.RawQuery,
		Header:     r.Header.Clone(),
		Body:       string(body),
	})
	s.mu.Unlock()

	createPath := "/catalog/api/items/" + s.fixture.ItemID + "/request"
	deploymentPath := "/deployment/api/deployments/" + s.fixture.DeploymentID
	switch {
	case r.Method == http.MethodPost && r.URL.Path == createPath:
		if r.Header.Get("Authorization") != "Bearer "+s.fixture.AccessToken {
			writeJSON(w, http.StatusUnauthorized, `{"message":"unauthorized"}`)
			return
		}
		writeJSONValue(w, http.StatusOK, []map[string]string{{
			"deploymentId":   s.fixture.DeploymentID,
			"deploymentName": s.fixture.DeploymentName,
		}})
	case r.Method == http.MethodGet && r.URL.Path == deploymentPath:
		switch r.Header.Get("Authorization") {
		case "Bearer " + s.fixture.AccessToken:
			writeJSON(w, http.StatusUnauthorized, `{"message":"access token expired"}`)
		case "Bearer " + s.fixture.RefreshedAccessToken:
			writeJSONValue(w, http.StatusOK, map[string]string{
				"id": s.fixture.DeploymentID, "name": s.fixture.DeploymentName, "status": "CREATE_SUCCESSFUL",
			})
		default:
			writeJSON(w, http.StatusUnauthorized, `{"message":"unauthorized"}`)
		}
	case r.Method == http.MethodPost && r.URL.Path == "/oidc/oauth2/token":
		username, password, ok := r.BasicAuth()
		expectedForm := url.Values{"grant_type": {"refresh_token"}, "refresh_token": {s.fixture.RefreshToken}}.Encode()
		if !ok || username != s.fixture.ClientID || password != s.fixture.ClientSecret || string(body) != expectedForm {
			writeJSON(w, http.StatusBadRequest, `{"message":"bad refresh request"}`)
			return
		}
		writeJSONValue(w, http.StatusOK, map[string]any{
			"access_token": s.fixture.RefreshedAccessToken,
			"expires_in":   3600,
			"token_type":   "Bearer",
		})
	default:
		writeJSON(w, http.StatusNotFound, `{"message":"operation is outside the pinned contract"}`)
	}
}

func writeJSONValue(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeJSON(w http.ResponseWriter, status int, body string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = io.WriteString(w, body)
}
