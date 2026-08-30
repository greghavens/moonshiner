package contractmock

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const (
	ReadOperation  = "ReadInfraSegment"
	PatchOperation = "PatchInfraSegment"
)

type contract struct {
	BasePath   string               `json:"basePath"`
	Operations map[string]operation `json:"operations"`
}

type operation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

// Scenario is supplied at test runtime. ReadBody and PatchBody are response
// documents, not an initial-state fixture.
type Scenario struct {
	SegmentID   string
	ReadStatus  int
	ReadBody    []byte
	PatchStatus int
	PatchBody   []byte
}

// LoggedRequest is the stable JSONL assertion surface used by the verifier.
type LoggedRequest struct {
	OperationID      string   `json:"operation_id"`
	Method           string   `json:"method"`
	RequestURI       string   `json:"request_uri"`
	Authorization    string   `json:"authorization"`
	Accept           string   `json:"accept"`
	ContentType      string   `json:"content_type"`
	ContentLength    int64    `json:"content_length"`
	TransferEncoding []string `json:"transfer_encoding"`
	Body             string   `json:"body"`
}

// Server is a contract-pinned loopback NSX Policy fixture.
type Server struct {
	URL     string
	LogPath string

	contract contract
	scenario Scenario
	http     *http.Server
	listener net.Listener

	mu      sync.Mutex
	effects int
}

// New loads the route table from contractPath and listens on an ephemeral IPv4
// loopback port. It refuses contracts containing any operation beyond the two
// operations named by this task.
func New(contractPath, logPath string, scenario Scenario) (*Server, error) {
	raw, err := os.ReadFile(contractPath)
	if err != nil {
		return nil, fmt.Errorf("read contract: %w", err)
	}
	var c contract
	if err := json.Unmarshal(raw, &c); err != nil {
		return nil, fmt.Errorf("decode contract: %w", err)
	}
	if c.BasePath != "/policy/api/v1" {
		return nil, fmt.Errorf("unexpected contract base path")
	}
	if len(c.Operations) != 2 {
		return nil, fmt.Errorf("contract must name exactly two operations")
	}
	for id, method := range map[string]string{
		ReadOperation:  http.MethodGet,
		PatchOperation: http.MethodPatch,
	} {
		op, ok := c.Operations[id]
		if !ok || op.OperationID != id || op.Method != method ||
			op.Path != "/infra/segments/{segment-id}" {
			return nil, fmt.Errorf("unexpected contract operation %s", id)
		}
	}
	if strings.TrimSpace(scenario.SegmentID) == "" {
		return nil, fmt.Errorf("scenario segment id is blank")
	}
	if scenario.ReadStatus == 0 {
		scenario.ReadStatus = http.StatusOK
	}
	if scenario.PatchStatus == 0 {
		scenario.PatchStatus = http.StatusOK
	}
	if err := os.MkdirAll(filepath.Dir(logPath), 0o755); err != nil {
		return nil, fmt.Errorf("create log directory: %w", err)
	}
	if err := os.WriteFile(logPath, nil, 0o600); err != nil {
		return nil, fmt.Errorf("create log: %w", err)
	}

	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("listen: %w", err)
	}
	s := &Server{
		URL:      "http://" + listener.Addr().String(),
		LogPath:  logPath,
		contract: c,
		scenario: scenario,
		listener: listener,
	}
	s.http = &http.Server{
		Handler:           http.HandlerFunc(s.serveHTTP),
		ReadHeaderTimeout: 2 * time.Second,
	}
	go func() {
		_ = s.http.Serve(listener)
	}()
	return s, nil
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		http.Error(w, "read failed", http.StatusBadRequest)
		return
	}
	_ = r.Body.Close()

	opID := ""
	expectedEscapedPath := s.contract.BasePath + strings.ReplaceAll(
		s.contract.Operations[ReadOperation].Path,
		"{segment-id}",
		url.PathEscape(s.scenario.SegmentID),
	)
	if r.URL.EscapedPath() == expectedEscapedPath && r.URL.RawQuery == "" {
		switch r.Method {
		case s.contract.Operations[ReadOperation].Method:
			opID = ReadOperation
		case s.contract.Operations[PatchOperation].Method:
			opID = PatchOperation
		}
	}

	entry := LoggedRequest{
		OperationID:      opID,
		Method:           r.Method,
		RequestURI:       r.RequestURI,
		Authorization:    r.Header.Get("Authorization"),
		Accept:           r.Header.Get("Accept"),
		ContentType:      r.Header.Get("Content-Type"),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
		Body:             string(body),
	}
	if err := s.appendLog(entry); err != nil {
		http.Error(w, "log failed", http.StatusInternalServerError)
		return
	}

	switch opID {
	case ReadOperation:
		s.writeResponse(w, s.scenario.ReadStatus, s.scenario.ReadBody)
	case PatchOperation:
		if s.scenario.PatchStatus == http.StatusOK {
			s.mu.Lock()
			s.effects++
			s.mu.Unlock()
		}
		s.writeResponse(w, s.scenario.PatchStatus, s.scenario.PatchBody)
	default:
		http.NotFound(w, r)
	}
}

func (s *Server) appendLog(entry LoggedRequest) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	f, err := os.OpenFile(s.LogPath, os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return err
	}
	defer f.Close()
	encoded, err := json.Marshal(entry)
	if err != nil {
		return err
	}
	encoded = append(encoded, '\n')
	_, err = f.Write(encoded)
	return err
}

func (s *Server) writeResponse(w http.ResponseWriter, status int, body []byte) {
	if len(body) == 0 && status != http.StatusOK {
		body = []byte(`{"error_code":93001,"error_message":"fixture rejection","module_name":"Policy","details":"runtime scenario"}`)
	}
	if len(body) > 0 {
		w.Header().Set("Content-Type", "application/json")
	}
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

// Effects returns the number of successful mutating requests accepted by the
// fixture.
func (s *Server) Effects() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.effects
}

// Close stops the loopback service.
func (s *Server) Close() error {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	err := s.http.Shutdown(ctx)
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

// ReadLog reads the complete filesystem JSONL request log.
func ReadLog(path string) ([]LoggedRequest, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	var entries []LoggedRequest
	scanner := bufio.NewScanner(f)
	scanner.Buffer(make([]byte, 1024), 1<<20)
	for scanner.Scan() {
		var entry LoggedRequest
		if err := json.Unmarshal(scanner.Bytes(), &entry); err != nil {
			return nil, err
		}
		entries = append(entries, entry)
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	return entries, nil
}
