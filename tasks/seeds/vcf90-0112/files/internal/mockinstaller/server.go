// Package mockinstaller provides the loopback VCF Installer fixture used by
// the protected verifier. It implements only updateDepotSettings from the
// contract pinned in docs/contract.json.
package mockinstaller

import (
	"bytes"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
)

const (
	UpdateDepotSettingsOperationID = "updateDepotSettings"
	UpdateDepotSettingsPath        = "/v1/system/settings/depot"
)

// Request is an exact record of one named contract operation received by the
// mock. Body is copied before it is returned to callers.
type Request struct {
	OperationID string
	Method      string
	Path        string
	RawQuery    string
	ContentType string
	Body        []byte
}

// Server is a race-safe, loopback-only replacement-state mock. Applying the
// same representation again is logged as another request but not another
// distinct effect.
type Server struct {
	httpServer *httptest.Server

	mu             sync.Mutex
	requests       []Request
	representation []byte
	effects        int
	responseStatus int
	responseBody   []byte
}

// New starts a loopback mock with no preloaded depot representation.
func New() *Server {
	s := &Server{}
	s.httpServer = httptest.NewServer(http.HandlerFunc(s.serveHTTP))
	return s
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != UpdateDepotSettingsPath {
		http.NotFound(w, r)
		return
	}
	if r.Method != http.MethodPut {
		w.Header().Set("Allow", http.MethodPut)
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}

	body, err := io.ReadAll(r.Body)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		return
	}

	s.mu.Lock()
	s.requests = append(s.requests, Request{
		OperationID: UpdateDepotSettingsOperationID,
		Method:      r.Method,
		Path:        r.URL.Path,
		RawQuery:    r.URL.RawQuery,
		ContentType: r.Header.Get("Content-Type"),
		Body:        append([]byte(nil), body...),
	})
	status := http.StatusAccepted
	responseBody := body
	if s.responseStatus != 0 {
		status = s.responseStatus
		responseBody = append([]byte(nil), s.responseBody...)
	}
	if status == http.StatusAccepted && !bytes.Equal(s.representation, body) {
		s.representation = append(s.representation[:0], body...)
		s.effects++
	}
	s.mu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(responseBody)
}

// URL returns the loopback base URL.
func (s *Server) URL() string { return s.httpServer.URL }

// Client returns the transport configured for this loopback server.
func (s *Server) Client() *http.Client { return s.httpServer.Client() }

// Close stops the loopback server.
func (s *Server) Close() { s.httpServer.Close() }

// Requests returns a deep copy of the operation request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.requests))
	copy(out, s.requests)
	for i := range out {
		out[i].Body = append([]byte(nil), out[i].Body...)
	}
	return out
}

// EffectCount returns the number of distinct representations applied.
func (s *Server) EffectCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.effects
}

// SetResponse replaces the default 202-and-echo response for subsequent
// named-operation requests. It does not add another served operation.
func (s *Server) SetResponse(status int, body []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.responseStatus = status
	s.responseBody = append([]byte(nil), body...)
}
