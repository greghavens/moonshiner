// Package mock provides a loopback VCF Operations for Logs server for tests.
package mock

import (
	"errors"
	"net/http"
)

// RequestRecord is one request as observed on the wire by the loopback mock.
type RequestRecord struct {
	Method        string
	RequestURI    string
	ContentType   string
	Accept        string
	Authorization string
	Body          []byte
}

// Server is a contract-pinned loopback server.
type Server struct{}

func New() (*Server, error) {
	return nil, errors.New("mock server is not implemented")
}

func (s *Server) URL() string { return "" }

func (s *Server) HTTPClient() *http.Client { return nil }

func (s *Server) Close() {}

func (s *Server) Requests() []RequestRecord { return nil }

func (s *Server) EffectCount() int { return 0 }
