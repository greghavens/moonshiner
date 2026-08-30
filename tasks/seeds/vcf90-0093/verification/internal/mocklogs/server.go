// Package mocklogs provides the hermetic VCF Operations for Logs fixture.
package mocklogs

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"sort"
	"strconv"
	"strings"
	"sync"
)

// OperationID is the only operation served by this fixture.
const OperationID = "GET_events-+path"

// Request is the wire information retained for contract assertions.
type Request struct {
	Method     string
	RequestURI string
	Header     http.Header
	Body       string
}

type field struct {
	Name    string `json:"name,omitempty"`
	Content string `json:"content,omitempty"`
}

type event struct {
	Text            string  `json:"text,omitempty"`
	Timestamp       int64   `json:"timestamp,omitempty"`
	TimestampString string  `json:"timestampString,omitempty"`
	Fields          []field `json:"fields,omitempty"`
}

var fixtureEvents = []event{
	{Text: "gamma", Timestamp: 1700000000300, TimestampString: "2023-11-14T22:13:20.300Z"},
	{Text: "alpha", Timestamp: 1700000000100, TimestampString: "2023-11-14T22:13:20.100Z", Fields: []field{{Name: "source", Content: "esx-01"}}},
	{Text: "echo", Timestamp: 1700000000500, TimestampString: "2023-11-14T22:13:20.500Z"},
	{Text: "bravo", Timestamp: 1700000000200, TimestampString: "2023-11-14T22:13:20.200Z"},
	{Text: "delta", Timestamp: 1700000000400, TimestampString: "2023-11-14T22:13:20.400Z"},
}

// Server is a loopback-only server with a concurrency-safe request log.
type Server struct {
	httpServer *httptest.Server
	httpClient *http.Client
	mu         sync.Mutex
	requests   []Request
}

// New starts the loopback fixture.
func New() *Server {
	s := &Server{}
	handler := http.HandlerFunc(s.serveHTTP)
	if loopback := startLoopback(handler); loopback != nil {
		s.httpServer = loopback
		s.httpClient = loopback.Client()
		return s
	}

	listener := newPipeListener()
	s.httpServer = &httptest.Server{
		Listener: listener,
		Config:   &http.Server{Handler: handler},
	}
	s.httpServer.Start()
	s.httpClient = &http.Client{Transport: &http.Transport{DialContext: listener.dialContext}}
	return s
}

func startLoopback(handler http.Handler) (server *httptest.Server) {
	defer func() {
		if recover() != nil {
			server = nil
		}
	}()
	return httptest.NewServer(handler)
}

// URL returns the loopback base URL.
func (s *Server) URL() string { return s.httpServer.URL }

// Client returns the HTTP client configured for this loopback server.
func (s *Server) Client() *http.Client { return s.httpClient }

// Close stops the server.
func (s *Server) Close() {
	s.httpClient.CloseIdleConnections()
	s.httpServer.Close()
}

// Requests returns a detached copy of the request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.requests))
	copy(out, s.requests)
	for i := range out {
		out[i].Header = out[i].Header.Clone()
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	s.mu.Lock()
	s.requests = append(s.requests, Request{
		Method: r.Method, RequestURI: r.RequestURI, Header: r.Header.Clone(), Body: string(body),
	})
	s.mu.Unlock()

	const prefix = "/api/v2/events/timestamp/GT "
	if r.Method != http.MethodGet || !strings.HasPrefix(r.URL.Path, prefix) {
		http.NotFound(w, r)
		return
	}
	if r.Header.Get("Authorization") != "Bearer fixture-session" || r.Header.Get("Accept") != "application/json" {
		http.Error(w, "contract headers required", http.StatusBadRequest)
		return
	}
	if len(body) != 0 {
		http.Error(w, "GET body is not allowed", http.StatusBadRequest)
		return
	}

	lower, err := strconv.ParseInt(strings.TrimPrefix(r.URL.Path, prefix), 10, 64)
	if err != nil {
		http.Error(w, "bad timestamp constraint", http.StatusBadRequest)
		return
	}
	q := r.URL.Query()
	limit, err := strconv.Atoi(q.Get("limit"))
	if err != nil || limit <= 0 || q.Get("order-by-direction") != "ASC" {
		http.Error(w, "bad paging query", http.StatusBadRequest)
		return
	}

	allowed := map[string]bool{"limit": true, "order-by-direction": true}
	for name := range q {
		if !allowed[name] {
			http.Error(w, "unexpected optional query field", http.StatusBadRequest)
			return
		}
	}

	items := append([]event(nil), fixtureEvents...)
	sort.Slice(items, func(i, j int) bool {
		if items[i].Timestamp == items[j].Timestamp {
			return items[i].Text < items[j].Text
		}
		return items[i].Timestamp < items[j].Timestamp
	})
	page := make([]event, 0, limit)
	for _, item := range items {
		if item.Timestamp > lower {
			page = append(page, item)
			if len(page) == limit {
				break
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(struct {
		Complete bool    `json:"complete"`
		Duration float64 `json:"duration"`
		Events   []event `json:"events"`
	}{Complete: true, Duration: 1, Events: page})
}

// pipeListener keeps the mock hermetic in sandboxes that prohibit opening even
// loopback ports. httptest still serves and parses real HTTP over net.Conn.
type pipeListener struct {
	connections chan net.Conn
	done        chan struct{}
	closeOnce   sync.Once
}

func newPipeListener() *pipeListener {
	return &pipeListener{connections: make(chan net.Conn), done: make(chan struct{})}
}

func (l *pipeListener) Accept() (net.Conn, error) {
	select {
	case connection := <-l.connections:
		return connection, nil
	case <-l.done:
		return nil, net.ErrClosed
	}
}

func (l *pipeListener) Close() error {
	l.closeOnce.Do(func() { close(l.done) })
	return nil
}

func (l *pipeListener) Addr() net.Addr {
	return mockAddr("127.0.0.1:0")
}

func (l *pipeListener) dialContext(ctx context.Context, _, _ string) (net.Conn, error) {
	client, server := net.Pipe()
	select {
	case l.connections <- server:
		return client, nil
	case <-ctx.Done():
		_ = client.Close()
		_ = server.Close()
		return nil, ctx.Err()
	case <-l.done:
		_ = client.Close()
		_ = server.Close()
		return nil, errors.New("mock server is closed")
	}
}

type mockAddr string

func (a mockAddr) Network() string { return "tcp" }
func (a mockAddr) String() string  { return string(a) }
