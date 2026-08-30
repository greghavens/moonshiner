// Package contractmock provides the protected loopback NSX Policy fixture.
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
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
)

type contract struct {
	BasePath   string      `json:"basePath"`
	Operations []operation `json:"operations"`
}

type operation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

// FirstBehavior controls the first matching request only.
type FirstBehavior int

const (
	Succeed FirstBehavior = iota
	DropAfterApply
	Return503
	Return504
	Return400
	Return403
	Return404
	Return412
	Return500
)

// Scenario controls deterministic response behavior.
type Scenario struct {
	IPBlockID    string
	First        FirstBehavior
	RepeatFirst  bool
	ExpectedUser string
	ExpectedPass string
}

// RequestRecord is the complete filesystem assertion surface.
type RequestRecord struct {
	Sequence      int                 `json:"sequence"`
	Method        string              `json:"method"`
	Target        string              `json:"target"`
	Authorization string              `json:"authorization"`
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
	resource    []byte
	firstServed bool
	logPath     string
}

// Server is an ephemeral loopback-only contract fixture.
type Server struct {
	URL    string
	Client *http.Client
	close  func()
	state  *state
}

// Close stops the loopback server.
func (s *Server) Close() {
	s.close()
}

// EffectCount returns the number of distinct resource state changes.
func (s *Server) EffectCount() int {
	s.state.mu.Lock()
	defer s.state.mu.Unlock()
	return s.state.effects
}

// Start reads the protected contract and serves only its named operation.
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
	if op.OperationID != "CreateOrPatchIpAddressBlock" ||
		op.Method != http.MethodPatch {
		t.Fatalf("unexpected protected operation: %#v", op)
	}
	if !strings.Contains(op.Path, "{ip-block-id}") {
		t.Fatalf("operation path lacks ip-block placeholder: %q", op.Path)
	}
	if err := os.WriteFile(logPath, nil, 0o600); err != nil {
		t.Fatalf("create request log: %v", err)
	}

	route := c.BasePath + strings.ReplaceAll(
		op.Path,
		"{ip-block-id}",
		url.PathEscape(scenario.IPBlockID),
	)
	st := &state{logPath: logPath}

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
			Authorization: r.Header.Get("Authorization"),
			Accept:        append([]string(nil), r.Header.Values("Accept")...),
			ContentType:   append([]string(nil), r.Header.Values("Content-Type")...),
			ContentLength: r.ContentLength,
			Transfer:      append([]string(nil), r.TransferEncoding...),
			BodyBase64:    base64.StdEncoding.EncodeToString(body),
			Headers:       cloneHeaders(r.Header),
		}
		logErr := appendRecord(st.logPath, record)

		user, pass, basicOK := r.BasicAuth()
		matches := logErr == nil &&
			r.Method == op.Method &&
			r.URL.EscapedPath() == route &&
			r.URL.RawQuery == "" &&
			basicOK &&
			user == scenario.ExpectedUser &&
			pass == scenario.ExpectedPass
		if !matches {
			st.mu.Unlock()
			writeError(w, http.StatusNotFound)
			return
		}

		first := !st.firstServed
		if first {
			st.firstServed = true
		}

		if first && scenario.First == DropAfterApply {
			apply(st, body)
			st.mu.Unlock()
			dropConnection(w)
			return
		}
		if first || scenario.RepeatFirst {
			if status := behaviorStatus(scenario.First); status != 0 {
				st.mu.Unlock()
				writeError(w, status)
				return
			}
		}

		apply(st, body)
		st.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, "{}")
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
	defer f.Close()
	if _, err := f.Write(append(line, '\n')); err != nil {
		return err
	}
	return nil
}

func apply(st *state, body []byte) {
	if string(st.resource) == string(body) {
		return
	}
	st.resource = append(st.resource[:0], body...)
	st.effects++
}

func behaviorStatus(behavior FirstBehavior) int {
	switch behavior {
	case Return503:
		return http.StatusServiceUnavailable
	case Return504:
		return http.StatusGatewayTimeout
	case Return400:
		return http.StatusBadRequest
	case Return403:
		return http.StatusForbidden
	case Return404:
		return http.StatusNotFound
	case Return412:
		return http.StatusPreconditionFailed
	case Return500:
		return http.StatusInternalServerError
	default:
		return 0
	}
}

func writeError(w http.ResponseWriter, status int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"error_code":    int64(97001),
		"error_message": "fixture failure payload must remain private",
		"module_name":   "contractmock",
		"details":       "projected detail",
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
