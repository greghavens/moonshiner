// Package contractmock provides the protected loopback NSX Policy fixture.
package contractmock

import (
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

// WireGroup is one mock Group response.
type WireGroup struct {
	ID           string `json:"id"`
	DisplayName  string `json:"display_name"`
	Path         string `json:"path"`
	ResourceType string `json:"resource_type"`
}

// Scenario controls the deterministic pagination and expiry behavior.
type Scenario struct {
	DomainID        string
	OldToken        string
	NewToken        string
	Cursor          string
	FirstPage       []WireGroup
	SecondPage      []WireGroup
	ExpireOldCursor bool
}

// RequestRecord is the complete filesystem assertion surface.
type RequestRecord struct {
	Sequence      int    `json:"sequence"`
	Method        string `json:"method"`
	Target        string `json:"target"`
	Authorization string `json:"authorization"`
	Accept        string `json:"accept"`
	ContentType   string `json:"content_type"`
	ContentLength int64  `json:"content_length"`
	BodyBase64    string `json:"body_base64"`
}

// Server is an ephemeral loopback-only contract fixture.
type Server struct {
	URL    string
	Client *http.Client
	close  func()
}

// Close stops the loopback server.
func (s *Server) Close() {
	s.close()
}

// Start reads the protected contract and serves only its named operation.
func Start(t testing.TB, contractPath, logPath string, scenario Scenario) *Server {
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
	if op.OperationID != "ListGroupForDomain" || op.Method != http.MethodGet {
		t.Fatalf("unexpected protected operation: %#v", op)
	}
	if !strings.Contains(op.Path, "{domain-id}") {
		t.Fatalf("operation path lacks domain placeholder: %q", op.Path)
	}
	if err := os.WriteFile(logPath, nil, 0o600); err != nil {
		t.Fatalf("create request log: %v", err)
	}

	route := c.BasePath + strings.ReplaceAll(
		op.Path,
		"{domain-id}",
		url.PathEscape(scenario.DomainID),
	)
	var mu sync.Mutex
	sequence := 0
	expired := false

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, readErr := io.ReadAll(r.Body)
		if readErr != nil {
			http.Error(w, "read request", http.StatusBadRequest)
			return
		}

		mu.Lock()
		sequence++
		record := RequestRecord{
			Sequence:      sequence,
			Method:        r.Method,
			Target:        r.RequestURI,
			Authorization: r.Header.Get("Authorization"),
			Accept:        r.Header.Get("Accept"),
			ContentType:   r.Header.Get("Content-Type"),
			ContentLength: r.ContentLength,
			BodyBase64:    base64.StdEncoding.EncodeToString(body),
		}
		line, marshalErr := json.Marshal(record)
		if marshalErr == nil {
			f, openErr := os.OpenFile(logPath, os.O_APPEND|os.O_WRONLY, 0o600)
			if openErr == nil {
				_, _ = f.Write(append(line, '\n'))
				_ = f.Close()
			} else {
				marshalErr = openErr
			}
		}

		cursor := r.URL.Query().Get("cursor")
		auth := r.Header.Get("Authorization")
		status := http.StatusNotFound
		var response any = map[string]any{
			"error_code":    int64(40401),
			"error_message": "operation is not served by the protected contract mock",
			"module_name":   "contractmock",
		}

		if marshalErr == nil && r.Method == op.Method && r.URL.EscapedPath() == route {
			switch {
			case cursor == "" &&
				(auth == "Bearer "+scenario.OldToken ||
					(scenario.ExpireOldCursor &&
						auth == "Bearer "+scenario.NewToken)):
				status = http.StatusOK
				response = map[string]any{
					"results": scenario.FirstPage,
					"cursor":  scenario.Cursor,
				}
			case cursor == scenario.Cursor &&
				auth == "Bearer "+scenario.OldToken &&
				scenario.ExpireOldCursor && !expired:
				expired = true
				status = http.StatusUnauthorized
				response = map[string]any{
					"error_code":    int64(403),
					"error_message": "access token expired",
					"module_name":   "authentication",
				}
			case cursor == scenario.Cursor &&
				auth == "Bearer "+scenario.OldToken &&
				!scenario.ExpireOldCursor:
				status = http.StatusOK
				response = map[string]any{"results": scenario.SecondPage}
			case cursor == scenario.Cursor &&
				auth == "Bearer "+scenario.NewToken &&
				scenario.ExpireOldCursor && expired:
				status = http.StatusOK
				response = map[string]any{"results": scenario.SecondPage}
			default:
				status = http.StatusUnauthorized
				response = map[string]any{
					"error_code":    int64(403),
					"error_message": "invalid access token or cursor",
					"module_name":   "authentication",
				}
			}
		}
		mu.Unlock()

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		if err := json.NewEncoder(w).Encode(response); err != nil {
			t.Errorf("encode mock response: %v", err)
		}
	})

	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen on loopback: %v", err)
	}
	ts := httptest.NewUnstartedServer(handler)
	ts.Listener = listener
	ts.Start()
	return &Server{URL: ts.URL, Client: ts.Client(), close: ts.Close}
}

// ReadLog reads the complete JSONL request log after a scenario finishes.
func ReadLog(path string) ([]RequestRecord, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(lines) == 1 && lines[0] == "" {
		return nil, nil
	}
	records := make([]RequestRecord, 0, len(lines))
	for i, line := range lines {
		var record RequestRecord
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			return nil, fmt.Errorf("decode request log line %d: %w", i+1, err)
		}
		records = append(records, record)
	}
	return records, nil
}
