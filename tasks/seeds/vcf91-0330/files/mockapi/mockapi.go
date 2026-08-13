// Package mockapi serves a loopback stand-in for the VCF Automation deployment
// API.
//
// The server is pinned to docs/contract.json: it routes only the operations the
// contract names, and it records every request it receives so a test can assert
// the exact wire shape the client produced.
package mockapi

import (
	"errors"
	"net/http"
)

// Event is one event emitted by a deployment request.
type Event struct {
	ID           string
	Name         string
	Details      string
	Timestamp    string
	ResourceName string
	ResourceType string

	// HasLogs reports whether the event has logs to retrieve. An event with
	// HasLogs false has no log resource: asking for its logs is a 404.
	HasLogs bool

	// UserEvent marks an event raised by a user rather than the engine.
	UserEvent bool

	// Logs are the log line messages, in row order. Row numbers start at 1.
	Logs []string
}

// Request is one deployment request.
type Request struct {
	ID        string
	Name      string
	Status    string
	Details   string
	CreatedAt string
	UpdatedAt string

	Events []Event
}

// Deployment is the seeded state of one deployment.
type Deployment struct {
	// Requests are served in the order they are seeded here. The server does
	// not sort them.
	Requests []Request
}

// Options configures a Server.
type Options struct {
	// ContractPath is the path to docs/contract.json. Required.
	ContractPath string

	// Token is the bearer token the server requires. Required.
	Token string

	// Deployments is the seeded state, keyed by deployment id.
	Deployments map[string]Deployment

	// LogPageSize is the number of log rows served per response. When zero or
	// negative every remaining row is served in one response.
	LogPageSize int

	// Each of these, when non-zero, makes the matching operation respond with
	// that status code instead of its normal response.
	RequestsStatus int // getDeploymentRequests
	RequestStatus  int // getRequest
	EventsStatus   int // getRequestEvents
	LogsStatus     int // getEventLogs
}

// Recorded is one request the server received, in the order it arrived.
type Recorded struct {
	Method   string
	Path     string
	RawQuery string
	Header   http.Header
	Body     []byte
}

// Server is a running loopback API.
type Server struct {
	url string
}

// Start reads the contract, registers a route for each operation it names and
// starts listening on loopback.
func Start(opts Options) (*Server, error) {
	return nil, errors.New("mockapi: Start not implemented")
}

// URL is the scheme://host:port root the server is listening on.
func (s *Server) URL() string { return s.url }

// Close shuts the server down.
func (s *Server) Close() {}

// Requests returns a copy of the request log, oldest first.
func (s *Server) Requests() []Recorded { return nil }
