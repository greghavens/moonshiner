// Package contractmock provides a loopback-only server whose routes are loaded
// from the protected focused contract.
package contractmock

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"regexp"
	"strings"
	"sync"
)

const (
	powerGetOperation  = "Vcenter.Vm.Power_get"
	cpuUpdateOperation = "Vcenter.Vm.Hardware.Cpu_update"
)

// Scenario controls contract-valid and intentionally malformed responses.
type Scenario struct {
	PowerStatus      int
	PowerState       string
	PowerBody        []byte
	CPUStatus        int
	CPUBody          []byte
	RedirectLocation string
}

// Record is one complete request written to the fsynced JSONL log.
type Record struct {
	OperationID   string              `json:"operation_id"`
	Method        string              `json:"method"`
	RequestURI    string              `json:"request_uri"`
	Headers       map[string][]string `json:"headers"`
	ContentLength int64               `json:"content_length"`
	Body          string              `json:"body"`
}

type operation struct {
	ID      string
	Method  string
	Path    string
	Pattern *regexp.Regexp
}

// Server is an ephemeral IPv4 loopback server.
type Server struct {
	httpServer *httptest.Server
	logPath    string
	logFile    *os.File
	operations []operation
	scenario   Scenario

	mu      sync.Mutex
	effects int
}

// New starts a mock after loading and validating its complete route allow-list.
func New(contractPath, logPath string, scenario Scenario) (*Server, error) {
	operations, err := loadOperations(contractPath)
	if err != nil {
		return nil, err
	}
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, fmt.Errorf("create request log: %w", err)
	}
	server := &Server{
		logPath:    logPath,
		logFile:    logFile,
		operations: operations,
		scenario:   scenario,
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		_ = logFile.Close()
		return nil, fmt.Errorf("listen on loopback: %w", err)
	}
	server.httpServer = httptest.NewUnstartedServer(http.HandlerFunc(server.serveHTTP))
	server.httpServer.Listener = listener
	server.httpServer.Start()
	return server, nil
}

// URL returns the ephemeral loopback origin.
func (s *Server) URL() string {
	return s.httpServer.URL
}

// LogPath returns the filesystem JSONL request log.
func (s *Server) LogPath() string {
	return s.logPath
}

// EffectCount returns the number of accepted CPU mutations.
func (s *Server) EffectCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.effects
}

// Close stops the server and closes the request log.
func (s *Server) Close() error {
	s.httpServer.Close()
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.logFile.Close()
}

func (s *Server) serveHTTP(writer http.ResponseWriter, request *http.Request) {
	body, err := io.ReadAll(request.Body)
	if err != nil {
		http.Error(writer, "request body read failed", http.StatusBadRequest)
		return
	}
	_ = request.Body.Close()

	operationID := ""
	for _, candidate := range s.operations {
		if request.Method == candidate.Method &&
			candidate.Pattern.MatchString(request.URL.EscapedPath()) {
			operationID = candidate.ID
			break
		}
	}
	record := Record{
		OperationID:   operationID,
		Method:        request.Method,
		RequestURI:    request.RequestURI,
		Headers:       cloneHeaders(request.Header),
		ContentLength: request.ContentLength,
		Body:          string(body),
	}
	if err := s.appendRecord(record); err != nil {
		http.Error(writer, "request log failed", http.StatusInternalServerError)
		return
	}
	if operationID == "" {
		http.Error(writer, "operation is outside focused contract", http.StatusNotFound)
		return
	}
	if request.URL.RawQuery != "" {
		http.Error(writer, "query is outside focused contract", http.StatusBadRequest)
		return
	}

	switch operationID {
	case powerGetOperation:
		if len(body) != 0 {
			http.Error(writer, "power precheck is bodyless", http.StatusBadRequest)
			return
		}
		s.servePower(writer)
	case cpuUpdateOperation:
		s.serveCPU(writer)
	default:
		http.Error(writer, "operation is outside focused contract", http.StatusNotFound)
	}
}

func (s *Server) servePower(writer http.ResponseWriter) {
	status := s.scenario.PowerStatus
	if status == 0 {
		status = http.StatusOK
	}
	if s.scenario.RedirectLocation != "" {
		writer.Header().Set("Location", s.scenario.RedirectLocation)
	}
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	if len(s.scenario.PowerBody) != 0 {
		_, _ = writer.Write(s.scenario.PowerBody)
		return
	}
	if status == http.StatusOK {
		state := s.scenario.PowerState
		if state == "" {
			state = "POWERED_OFF"
		}
		_ = json.NewEncoder(writer).Encode(map[string]string{"state": state})
		return
	}
	_, _ = io.WriteString(
		writer,
		`{"error_type":"SERVICE_UNAVAILABLE","messages":[{"id":"mock.failure","default_message":"generated mock failure"}]}`,
	)
}

func (s *Server) serveCPU(writer http.ResponseWriter) {
	status := s.scenario.CPUStatus
	if status == 0 {
		status = http.StatusNoContent
	}
	if status == http.StatusNoContent {
		s.mu.Lock()
		s.effects++
		s.mu.Unlock()
		writer.WriteHeader(status)
		return
	}
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	if len(s.scenario.CPUBody) != 0 {
		_, _ = writer.Write(s.scenario.CPUBody)
		return
	}
	_, _ = io.WriteString(
		writer,
		`{"error_type":"SERVICE_UNAVAILABLE","messages":[{"id":"mock.failure","default_message":"generated mock failure"}]}`,
	)
}

func (s *Server) appendRecord(record Record) error {
	line, err := json.Marshal(record)
	if err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, err := s.logFile.Write(append(line, '\n')); err != nil {
		return err
	}
	return s.logFile.Sync()
}

func cloneHeaders(headers http.Header) map[string][]string {
	copy := make(map[string][]string, len(headers))
	for name, values := range headers {
		copy[name] = append([]string(nil), values...)
	}
	return copy
}

// ReadLog reads complete records from a request log.
func ReadLog(path string) ([]Record, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	var records []Record
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 4096), 1<<20)
	for scanner.Scan() {
		var record Record
		if err := json.Unmarshal(scanner.Bytes(), &record); err != nil {
			return nil, fmt.Errorf("decode request log: %w", err)
		}
		records = append(records, record)
	}
	if err := scanner.Err(); err != nil {
		return nil, fmt.Errorf("read request log: %w", err)
	}
	return records, nil
}

func loadOperations(path string) ([]operation, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read focused contract: %w", err)
	}
	var contract struct {
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(data, &contract); err != nil {
		return nil, fmt.Errorf("decode focused contract: %w", err)
	}
	if len(contract.Operations) != 2 {
		return nil, errors.New("focused contract must name exactly two operations")
	}
	expected := []string{powerGetOperation, cpuUpdateOperation}
	operations := make([]operation, 0, len(contract.Operations))
	for index, entry := range contract.Operations {
		if entry.OperationID != expected[index] {
			return nil, errors.New("focused contract operation order or identity changed")
		}
		if entry.Method == "" || entry.Path == "" {
			return nil, errors.New("focused contract operation is incomplete")
		}
		pattern, err := compilePath(entry.Path)
		if err != nil {
			return nil, err
		}
		operations = append(operations, operation{
			ID:      entry.OperationID,
			Method:  entry.Method,
			Path:    entry.Path,
			Pattern: pattern,
		})
	}
	return operations, nil
}

func compilePath(path string) (*regexp.Regexp, error) {
	placeholder := regexp.MustCompile(`\{[^{}]+\}`)
	indexes := placeholder.FindAllStringIndex(path, -1)
	if len(indexes) != 1 {
		return nil, errors.New("focused contract path must contain one path parameter")
	}
	var pattern strings.Builder
	pattern.WriteString("^")
	cursor := 0
	for _, index := range indexes {
		pattern.WriteString(regexp.QuoteMeta(path[cursor:index[0]]))
		pattern.WriteString(`[^/]+`)
		cursor = index[1]
	}
	pattern.WriteString(regexp.QuoteMeta(path[cursor:]))
	pattern.WriteString("$")
	compiled, err := regexp.Compile(pattern.String())
	if err != nil {
		return nil, fmt.Errorf("compile contract route: %w", err)
	}
	return compiled, nil
}
