package nsxmock

import (
	"errors"
	"net/http"
)

var ErrNotImplemented = errors.New("nsxmock: not implemented")

type Failure struct {
	OperationID string
	Path        string
	Status      int
	ErrorCode   int
	Message     string
	ModuleName  string
}

type Request struct {
	OperationID string
	Method      string
	RequestURI  string
	Header      http.Header
	Body        []byte
}

type Server struct{}

// New loads the derived contract, starts a loopback-only server, and optionally
// configures one operation/path to fail.
func New(contractPath string, failure *Failure) (*Server, error) {
	return nil, ErrNotImplemented
}

func (s *Server) URL() string {
	return ""
}

func (s *Server) Client() *http.Client {
	return nil
}

func (s *Server) Close() {}

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []Request {
	return nil
}
