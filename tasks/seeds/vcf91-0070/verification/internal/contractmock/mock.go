// Package contractmock provides the protected NSX Policy fixture.
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

type WireGroup struct {
	ID           string `json:"id"`
	DisplayName  string `json:"display_name"`
	Path         string `json:"path"`
	ResourceType string `json:"resource_type"`
}

type Scenario struct {
	DomainID   string
	Username   string
	Password   string
	Cursor     string
	FirstPage  []WireGroup
	SecondPage []WireGroup
}

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

type Server struct {
	URL    string
	Client *http.Client
	close  func()
}

func (s *Server) Close() { s.close() }

func Start(t testing.TB, contractPath, logPath string, scenario Scenario) *Server {
	t.Helper()
	data, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var document contract
	if err := json.Unmarshal(data, &document); err != nil {
		t.Fatalf("decode contract: %v", err)
	}
	if len(document.Operations) != 1 {
		t.Fatalf("contract operation count = %d, want 1", len(document.Operations))
	}
	op := document.Operations[0]
	if op.OperationID != "ListGroupForDomain" || op.Method != http.MethodGet {
		t.Fatalf("unexpected operation: %#v", op)
	}
	if err := os.WriteFile(logPath, nil, 0o600); err != nil {
		t.Fatalf("create request log: %v", err)
	}
	route := document.BasePath + strings.ReplaceAll(op.Path, "{domain-id}", url.PathEscape(scenario.DomainID))
	expectedAuth := "Basic " + base64.StdEncoding.EncodeToString([]byte(scenario.Username+":"+scenario.Password))
	var mu sync.Mutex
	sequence := 0
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		mu.Lock()
		defer mu.Unlock()
		sequence++
		record := RequestRecord{Sequence: sequence, Method: r.Method, Target: r.RequestURI,
			Authorization: r.Header.Get("Authorization"), Accept: r.Header.Get("Accept"),
			ContentType: r.Header.Get("Content-Type"), ContentLength: r.ContentLength,
			BodyBase64: base64.StdEncoding.EncodeToString(body)}
		line, _ := json.Marshal(record)
		file, openErr := os.OpenFile(logPath, os.O_APPEND|os.O_WRONLY, 0o600)
		if openErr == nil {
			_, _ = file.Write(append(line, '\n'))
			_ = file.Close()
		}
		status := http.StatusNotFound
		var response any = map[string]any{"error_code": 40401, "error_message": "operation not served", "module_name": "contractmock"}
		if r.Method == op.Method && r.URL.EscapedPath() == route {
			if r.Header.Get("Authorization") != expectedAuth {
				status = http.StatusForbidden
				response = map[string]any{"error_code": 403, "error_message": "Credentials are incorrect or the account is locked", "module_name": "common-services"}
			} else {
				switch r.URL.Query().Get("cursor") {
				case "":
					status = http.StatusOK
					response = map[string]any{"results": scenario.FirstPage, "cursor": scenario.Cursor}
				case scenario.Cursor:
					status = http.StatusOK
					response = map[string]any{"results": scenario.SecondPage}
				default:
					status = http.StatusBadRequest
					response = map[string]any{"error_code": 2057, "error_message": "invalid cursor", "module_name": "internal-framework"}
				}
			}
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(response)
	})
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	server := httptest.NewUnstartedServer(handler)
	server.Listener = listener
	server.Start()
	return &Server{URL: server.URL, Client: server.Client(), close: server.Close}
}

func ReadLog(path string) ([]RequestRecord, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	text := strings.TrimSpace(string(data))
	if text == "" {
		return nil, nil
	}
	lines := strings.Split(text, "\n")
	records := make([]RequestRecord, 0, len(lines))
	for index, line := range lines {
		var record RequestRecord
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			return nil, fmt.Errorf("decode request log line %d: %w", index+1, err)
		}
		records = append(records, record)
	}
	return records, nil
}
