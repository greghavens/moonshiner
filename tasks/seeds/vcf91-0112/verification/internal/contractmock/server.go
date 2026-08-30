// Package contractmock provides the protected loopback vCenter fixture.
package contractmock

import (
	"bufio"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
)

type contract struct {
	BasePath   string      `json:"base_path"`
	Operations []operation `json:"operations"`
}

type operation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

// FirstBehavior controls deterministic response behavior.
type FirstBehavior int

const (
	Succeed FirstBehavior = iota
	DropAfterCommit
	DropEveryResponse
	Return400
	Return503
	Return307
	Truncate201AfterCommit
	Malformed201
)

// Scenario configures one isolated loopback server.
type Scenario struct {
	First               FirstBehavior
	ExpectedSession     string
	ExpectedClientToken string
	LibraryID           string
}

// RequestRecord is the complete filesystem assertion surface.
type RequestRecord struct {
	Sequence      int                 `json:"sequence"`
	Method        string              `json:"method"`
	Target        string              `json:"target"`
	SessionToken  []string            `json:"session_token"`
	ClientToken   []string            `json:"client_token"`
	Authorization []string            `json:"authorization"`
	Accept        []string            `json:"accept"`
	ContentType   []string            `json:"content_type"`
	ContentLength int64               `json:"content_length"`
	Transfer      []string            `json:"transfer_encoding"`
	BodyBase64    string              `json:"body_base64"`
	Headers       map[string][]string `json:"headers"`
}

type state struct {
	mu          sync.Mutex
	sequence    int
	effects     int
	firstServed bool
	logPath     string
	byToken     map[string][]byte
	identifiers map[string]string
}

// Server is an ephemeral loopback-only contract fixture.
type Server struct {
	URL    string
	Client *http.Client
	close  func()
	state  *state
}

// Close stops the server.
func (s *Server) Close() {
	s.close()
}

// EffectCount returns the number of distinct library creations.
func (s *Server) EffectCount() int {
	s.state.mu.Lock()
	defer s.state.mu.Unlock()
	return s.state.effects
}

// Start reads the contract and serves only its named operation.
func Start(
	t testing.TB,
	contractPath string,
	logPath string,
	scenario Scenario,
) *Server {
	t.Helper()

	data, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var c contract
	if err := json.Unmarshal(data, &c); err != nil {
		t.Fatalf("decode contract: %v", err)
	}
	if len(c.Operations) != 1 {
		t.Fatalf("contract operation count = %d, want 1", len(c.Operations))
	}
	op := c.Operations[0]
	if op.OperationID != "Content.LocalLibrary_create" ||
		op.Method != http.MethodPost ||
		op.Path != "/content/local-library" ||
		c.BasePath != "/api" {
		t.Fatalf("unexpected protected operation: base=%q op=%#v", c.BasePath, op)
	}
	if scenario.LibraryID == "" {
		scenario.LibraryID = "library-1"
	}
	if err := os.WriteFile(logPath, nil, 0o600); err != nil {
		t.Fatalf("create request log: %v", err)
	}

	route := c.BasePath + op.Path
	st := &state{
		logPath:     logPath,
		byToken:     make(map[string][]byte),
		identifiers: make(map[string]string),
	}

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, readErr := io.ReadAll(r.Body)
		if readErr != nil {
			http.Error(w, "request body could not be read", http.StatusBadRequest)
			return
		}

		st.mu.Lock()
		st.sequence++
		record := RequestRecord{
			Sequence:      st.sequence,
			Method:        r.Method,
			Target:        r.RequestURI,
			SessionToken:  append([]string(nil), r.Header.Values("vmware-api-session-id")...),
			ClientToken:   append([]string(nil), r.Header.Values("Client-Token")...),
			Authorization: append([]string(nil), r.Header.Values("Authorization")...),
			Accept:        append([]string(nil), r.Header.Values("Accept")...),
			ContentType:   append([]string(nil), r.Header.Values("Content-Type")...),
			ContentLength: r.ContentLength,
			Transfer:      append([]string(nil), r.TransferEncoding...),
			BodyBase64:    base64.StdEncoding.EncodeToString(body),
			Headers:       cloneHeaders(r.Header),
		}
		logErr := appendRecord(st.logPath, record)

		matches := logErr == nil &&
			r.Method == op.Method &&
			r.URL.EscapedPath() == route &&
			r.URL.RawQuery == "" &&
			len(record.SessionToken) == 1 &&
			record.SessionToken[0] == scenario.ExpectedSession &&
			len(record.ClientToken) == 1 &&
			record.ClientToken[0] == scenario.ExpectedClientToken
		if !matches {
			st.mu.Unlock()
			writeError(w, http.StatusNotFound)
			return
		}

		first := !st.firstServed
		if first {
			st.firstServed = true
		}
		if first && scenario.First == Return307 {
			st.mu.Unlock()
			w.Header().Set("Location", "/api/uncontracted")
			w.WriteHeader(http.StatusTemporaryRedirect)
			return
		}
		if first && scenario.First == Return400 {
			st.mu.Unlock()
			writeError(w, http.StatusBadRequest)
			return
		}
		if first && scenario.First == Return503 {
			st.mu.Unlock()
			writeError(w, http.StatusServiceUnavailable)
			return
		}

		identifier, ok := apply(st, record.ClientToken[0], body, scenario.LibraryID)
		if !ok {
			st.mu.Unlock()
			writeError(w, http.StatusBadRequest)
			return
		}
		shouldDrop := scenario.First == DropEveryResponse ||
			(first && scenario.First == DropAfterCommit)
		st.mu.Unlock()

		if shouldDrop {
			dropConnection(w)
			return
		}
		if first && scenario.First == Truncate201AfterCommit {
			writeTruncated201(w)
			return
		}
		if first && scenario.First == Malformed201 {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusCreated)
			_, _ = io.WriteString(w, "{}")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(identifier)
	})

	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen on loopback: %v", err)
	}
	ts := httptest.NewUnstartedServer(handler)
	ts.Listener = listener
	ts.Start()
	return &Server{
		URL:    ts.URL,
		Client: ts.Client(),
		close:  ts.Close,
		state:  st,
	}
}

func cloneHeaders(source http.Header) map[string][]string {
	out := make(map[string][]string, len(source))
	for key, values := range source {
		out[key] = append([]string(nil), values...)
	}
	return out
}

func appendRecord(path string, record RequestRecord) error {
	line, err := json.Marshal(record)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	if _, err := f.Write(append(line, '\n')); err != nil {
		_ = f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		_ = f.Close()
		return err
	}
	return f.Close()
}

func apply(
	st *state,
	token string,
	body []byte,
	libraryID string,
) (string, bool) {
	if previous, exists := st.byToken[token]; exists {
		if string(previous) != string(body) {
			return "", false
		}
		return st.identifiers[token], true
	}
	st.byToken[token] = append([]byte(nil), body...)
	st.identifiers[token] = libraryID
	st.effects++
	return libraryID, true
}

func writeError(w http.ResponseWriter, status int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"error_type": "INVALID_ARGUMENT",
		"messages": []map[string]any{
			{
				"id":              "contractmock.private",
				"default_message": "fixture response body must remain private",
				"args":            []string{},
			},
		},
	})
}

func dropConnection(w http.ResponseWriter) {
	hijacker, ok := w.(http.Hijacker)
	if !ok {
		panic("loopback response writer cannot hijack HTTP/1 connection")
	}
	conn, rw, err := hijacker.Hijack()
	if err != nil {
		panic(fmt.Sprintf("hijack loopback connection: %v", err))
	}
	if rw != nil {
		_ = rw.Flush()
	}
	_ = conn.Close()
}

func writeTruncated201(w http.ResponseWriter) {
	hijacker, ok := w.(http.Hijacker)
	if !ok {
		panic("loopback response writer cannot hijack HTTP/1 connection")
	}
	conn, rw, err := hijacker.Hijack()
	if err != nil {
		panic(fmt.Sprintf("hijack loopback connection: %v", err))
	}
	_, _ = rw.WriteString(
		"HTTP/1.1 201 Created\r\n" +
			"Content-Type: application/json\r\n" +
			"Content-Length: 40\r\n" +
			"Connection: close\r\n\r\n" +
			"\"partial",
	)
	_ = rw.Flush()
	_ = conn.Close()
}

// ReadLog reads the complete JSONL request log after a scenario finishes.
func ReadLog(path string) ([]RequestRecord, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var records []RequestRecord
	scanner := bufio.NewScanner(f)
	for line := 1; scanner.Scan(); line++ {
		var record RequestRecord
		if err := json.Unmarshal(scanner.Bytes(), &record); err != nil {
			return nil, fmt.Errorf("decode request log line %d: %w", line, err)
		}
		records = append(records, record)
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	return records, nil
}

// ContractOperations returns operation IDs read from a focused contract.
func ContractOperations(path string) ([]string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var c contract
	if err := json.Unmarshal(data, &c); err != nil {
		return nil, err
	}
	ids := make([]string, 0, len(c.Operations))
	for _, op := range c.Operations {
		ids = append(ids, strings.TrimSpace(op.OperationID))
	}
	return ids, nil
}
