// Package mockvcf provides a deterministic loopback VCF Operations server for
// the acquireToken and getAlerts operations recorded in docs/contract.json.
package mockvcf

import (
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"sync"
)

const (
	AcquireTokenOperationID = "acquireToken"
	GetAlertsOperationID    = "getAlerts"
	acquireTokenPath        = "/suite-api/api/auth/token/acquire"
	getAlertsPath           = "/suite-api/api/alerts"
)

// Request is the wire information retained for assertions.
type Request struct {
	Method   string
	Path     string
	RawQuery string
	Header   http.Header
	Body     []byte
}

// RequestLog is safe to read while the server is running.
type RequestLog struct {
	mu      sync.Mutex
	entries []Request
}

func (l *RequestLog) append(r Request) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.entries = append(l.entries, r)
}

// Entries returns a deep copy of all requests in arrival order.
func (l *RequestLog) Entries() []Request {
	l.mu.Lock()
	defer l.mu.Unlock()
	out := make([]Request, len(l.entries))
	for i, entry := range l.entries {
		out[i] = entry
		out[i].Header = entry.Header.Clone()
		out[i].Body = append([]byte(nil), entry.Body...)
	}
	return out
}

// Server exposes only acquireToken and getAlerts. Its first token expires when
// page 1 is requested; a refreshed token can retrieve that page.
type Server struct {
	Log *RequestLog

	mu           sync.Mutex
	acquireCount int
	httpServer   *httptest.Server
	httpClient   *http.Client
	url          string
}

// NewServer starts the contract-pinned loopback server.
func NewServer() *Server {
	s := &Server{Log: &RequestLog{}}
	handler := http.HandlerFunc(s.serveHTTP)
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err == nil {
		s.httpServer = httptest.NewUnstartedServer(handler)
		s.httpServer.Listener = listener
		s.httpServer.Start()
		s.httpClient = s.httpServer.Client()
		s.url = s.httpServer.URL
		return s
	}

	// Some restricted test sandboxes disallow even loopback sockets. Keep the
	// same net/http wire behavior by routing requests directly to the handler.
	s.url = "http://127.0.0.1"
	s.httpClient = &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, req)
		return recorder.Result(), nil
	})}
	return s
}

// URL returns the loopback server origin.
func (s *Server) URL() string { return s.url }

// Client returns an HTTP client connected only to this mock.
func (s *Server) Client() *http.Client { return s.httpClient }

// Close stops the loopback server.
func (s *Server) Close() {
	if s.httpServer != nil {
		s.httpServer.Close()
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	var body []byte
	if r.Body != nil {
		body, _ = io.ReadAll(r.Body)
	}
	s.Log.append(Request{
		Method:   r.Method,
		Path:     r.URL.Path,
		RawQuery: r.URL.RawQuery,
		Header:   r.Header.Clone(),
		Body:     body,
	})

	switch {
	case r.Method == http.MethodPost && r.URL.Path == acquireTokenPath:
		s.serveAcquireToken(w)
	case r.Method == http.MethodGet && r.URL.Path == getAlertsPath:
		s.serveGetAlerts(w, r)
	default:
		http.NotFound(w, r)
	}
}

func (s *Server) serveAcquireToken(w http.ResponseWriter) {
	s.mu.Lock()
	s.acquireCount++
	token := "token-" + strconv.Itoa(s.acquireCount)
	s.mu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"token":    token,
		"validity": int64(4102444800000),
	})
}

func (s *Server) serveGetAlerts(w http.ResponseWriter, r *http.Request) {
	page, err := strconv.Atoi(r.URL.Query().Get("page"))
	if err != nil {
		http.Error(w, "invalid page", http.StatusBadRequest)
		return
	}
	token := r.Header.Get("Authorization")
	if token != "token-1" && token != "token-2" {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	if page == 1 && token == "token-1" {
		http.Error(w, "expired", http.StatusUnauthorized)
		return
	}

	alerts := []map[string]any{}
	switch page {
	case 0:
		alerts = []map[string]any{
			{"alertId": "11111111-1111-4111-8111-111111111111", "resourceId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "alertLevel": "WARNING", "startTimeUTC": int64(100), "updateTimeUTC": int64(110)},
			{"alertId": "22222222-2222-4222-8222-222222222222", "resourceId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "alertLevel": "CRITICAL", "startTimeUTC": int64(200), "updateTimeUTC": int64(210)},
		}
	case 1:
		alerts = []map[string]any{
			{"alertId": "33333333-3333-4333-8333-333333333333", "resourceId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "alertLevel": "INFORMATION", "startTimeUTC": int64(300), "updateTimeUTC": int64(310)},
		}
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"pageInfo": map[string]any{"totalCount": 3, "page": page, "pageSize": 2},
		"alerts":   alerts,
	})
}
