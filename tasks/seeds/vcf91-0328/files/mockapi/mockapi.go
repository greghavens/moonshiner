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

// Action is one entry of the action list served by the precheck operation.
type Action struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	DisplayName string `json:"displayName"`
	ActionType  string `json:"actionType"`
	Valid       bool   `json:"valid"`
}

// Deployment is the seeded state of one deployment.
type Deployment struct {
	// Actions is what the precheck operation returns for this deployment.
	Actions []Action
}

// Options configures a Server.
type Options struct {
	// ContractPath is the path to docs/contract.json. Required.
	ContractPath string

	// Token is the bearer token the server requires. Required.
	Token string

	// Deployments is the seeded state, keyed by deployment id.
	Deployments map[string]Deployment

	// ActionsStatus, when non-zero, makes the precheck operation respond with
	// this status code instead of its normal response.
	ActionsStatus int

	// RequestStatus, when non-zero, makes the mutating operation respond with
	// this status code instead of its normal response.
	RequestStatus int
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
