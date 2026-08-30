package contractmock

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"
)

// Host is the small response projection used by the loopback fixture.
type Host struct {
	ID     string `json:"id"`
	FQDN   string `json:"fqdn"`
	Status string `json:"status"`
}

// Request is an immutable snapshot of one request observed by the mock.
type Request struct {
	Method           string
	Path             string
	RawQuery         string
	RequestURI       string
	Host             string
	Header           http.Header
	Body             string
	ContentLength    int64
	TransferEncoding []string
}

type operation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

type contract struct {
	Operations []operation `json:"operations"`
}

// Server is a race-safe loopback SDDC Manager mock. Its sole route is loaded
// from the checked-in contract instead of being independently duplicated. A
// standard HTTP transport writes requests to an in-memory net.Pipe, so no
// listener or external network access is required.
type Server struct {
	operation operation
	pages     [][]Host
	client    *http.Client
	transport *http.Transport

	mu       sync.Mutex
	requests []Request
}

// New starts a loopback mock pinned to contractPath. The contract must name
// exactly getHosts; no fallback or catch-all API operation is served.
func New(t testing.TB, contractPath string, pages [][]Host) *Server {
	t.Helper()

	data, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read contract for loopback mock: %v", err)
	}
	var document contract
	if err := json.Unmarshal(data, &document); err != nil {
		t.Fatalf("decode contract for loopback mock: %v", err)
	}
	if len(document.Operations) != 1 {
		t.Fatalf("loopback mock requires exactly one contract operation, got %d", len(document.Operations))
	}
	op := document.Operations[0]
	if op.OperationID != "getHosts" || op.Method != http.MethodGet || op.Path != "/v1/hosts" {
		t.Fatalf("loopback mock does not implement contract operation %#v", op)
	}
	if len(pages) == 0 {
		t.Fatal("loopback mock requires at least one page")
	}

	s := &Server{operation: op, pages: clonePages(pages)}
	s.transport = &http.Transport{}
	s.transport.DialContext = func(_ context.Context, _, _ string) (net.Conn, error) {
		clientConnection, serverConnection := net.Pipe()
		go s.serveConnection(serverConnection)
		return clientConnection, nil
	}
	s.client = &http.Client{Transport: s.transport}
	t.Cleanup(s.transport.CloseIdleConnections)
	return s
}

// URL returns the loopback base URL.
func (s *Server) URL() string {
	return "http://127.0.0.1"
}

// Client returns an HTTP client configured for this loopback server.
func (s *Server) Client() *http.Client {
	return s.client
}

// Requests returns deep copies of all requests observed so far.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	out := make([]Request, len(s.requests))
	for i, request := range s.requests {
		out[i] = request
		out[i].Header = request.Header.Clone()
		out[i].TransferEncoding = append([]string(nil), request.TransferEncoding...)
	}
	return out
}

func (s *Server) serveConnection(connection net.Conn) {
	defer connection.Close()
	reader := bufio.NewReader(connection)
	for {
		request, err := http.ReadRequest(reader)
		if err != nil {
			return
		}
		response := s.responseFor(request)
		if err := response.Write(connection); err != nil {
			return
		}
		if request.Close || response.Close {
			return
		}
	}
}

func (s *Server) responseFor(r *http.Request) *http.Response {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		return textResponse(http.StatusBadRequest, "cannot read request\n")
	}
	r.Body.Close()
	s.record(r, string(body))

	if r.Method != s.operation.Method || r.URL.Path != s.operation.Path {
		return textResponse(http.StatusNotFound, "404 page not found\n")
	}

	values := r.URL.Query()
	pageText, ok := values["pageNumber"]
	if !ok || len(pageText) != 1 || pageText[0] == "" {
		return textResponse(http.StatusBadRequest, "pageNumber is required by this fixture\n")
	}
	pageNumber, err := strconv.Atoi(pageText[0])
	if err != nil || pageNumber < 0 || pageNumber >= len(s.pages) {
		return textResponse(http.StatusBadRequest, "pageNumber is out of range\n")
	}

	totalElements := 0
	for _, page := range s.pages {
		totalElements += len(page)
	}
	response := struct {
		Elements     []Host `json:"elements"`
		PageMetadata struct {
			PageNumber    int `json:"pageNumber"`
			PageSize      int `json:"pageSize"`
			TotalElements int `json:"totalElements"`
			TotalPages    int `json:"totalPages"`
		} `json:"pageMetadata"`
	}{
		Elements: append([]Host(nil), s.pages[pageNumber]...),
	}
	response.PageMetadata.PageNumber = pageNumber
	response.PageMetadata.PageSize = len(response.Elements)
	response.PageMetadata.TotalElements = totalElements
	response.PageMetadata.TotalPages = len(s.pages)

	var encoded bytes.Buffer
	if err := json.NewEncoder(&encoded).Encode(response); err != nil {
		panic(fmt.Sprintf("encode loopback response: %v", err))
	}
	return &http.Response{
		StatusCode:    http.StatusOK,
		ProtoMajor:    1,
		ProtoMinor:    1,
		Header:        http.Header{"Content-Type": []string{"application/json"}},
		Body:          io.NopCloser(bytes.NewReader(encoded.Bytes())),
		ContentLength: int64(encoded.Len()),
	}
}

func (s *Server) record(r *http.Request, body string) {
	s.mu.Lock()
	defer s.mu.Unlock()

	s.requests = append(s.requests, Request{
		Method:           r.Method,
		Path:             r.URL.Path,
		RawQuery:         r.URL.RawQuery,
		RequestURI:       r.RequestURI,
		Host:             r.Host,
		Header:           r.Header.Clone(),
		Body:             body,
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
	})
}

func textResponse(status int, body string) *http.Response {
	return &http.Response{
		StatusCode:    status,
		ProtoMajor:    1,
		ProtoMinor:    1,
		Header:        http.Header{"Content-Type": []string{"text/plain; charset=utf-8"}},
		Body:          io.NopCloser(strings.NewReader(body)),
		ContentLength: int64(len(body)),
	}
}

func clonePages(pages [][]Host) [][]Host {
	out := make([][]Host, len(pages))
	for i, page := range pages {
		out[i] = append([]Host(nil), page...)
	}
	return out
}
