package vsandp

import (
	"errors"
	"net/http"
	"net/http/httptest"
)

// MockScenario configures the two contract operations exposed by MockServer.
type MockScenario struct {
	ClusterID         string
	ProtectionGroupID string
	TaskID            string
	Statuses          []TaskStatus
}

// RequestRecord is an immutable snapshot of one request received by MockServer.
type RequestRecord struct {
	Method        string
	RequestURI    string
	Host          string
	Header        http.Header
	Body          []byte
	ContentLength int64
}

// MockServer is a loopback-only vSAN Data Protection contract fixture.
type MockServer struct {
	server *httptest.Server
}

// NewMockServer starts the contract-pinned loopback fixture.
func NewMockServer(scenario MockScenario) (*MockServer, error) {
	return nil, errors.New("NewMockServer is not implemented")
}

// URL returns the mock API base URL, including /api.
func (m *MockServer) URL() string {
	if m == nil || m.server == nil {
		return ""
	}
	return m.server.URL + "/api"
}

// Client returns an HTTP client configured for this mock server.
func (m *MockServer) Client() *http.Client {
	if m == nil || m.server == nil {
		return http.DefaultClient
	}
	return m.server.Client()
}

// Close stops the mock server.
func (m *MockServer) Close() {
	if m != nil && m.server != nil {
		m.server.Close()
	}
}

// Requests returns a deep copy of the request log.
func (m *MockServer) Requests() []RequestRecord {
	return nil
}
