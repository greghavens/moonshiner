// Package contractmock provides the loopback-only fixture for the focused
// createMaintenanceSchedules contract. It is test infrastructure, not a VCF
// Operations emulator.
package contractmock

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
)

const operationID = "createMaintenanceSchedules"

// Config controls the one failure used by the partial-change scenario.
// FailAt is a zero-based request index; a negative value means no failure.
type Config struct {
	FailAt int
}

// Schedule is the fixture's focused request/response projection.
type Schedule struct {
	Hour            int32    `json:"hour"`
	MinuteOfTheHour int32    `json:"minuteOfTheHour"`
	Duration        int32    `json:"duration"`
	ScheduleType    string   `json:"scheduleType"`
	Recurrence      *int32   `json:"recurrence,omitempty"`
	DayOfTheMonth   *int32   `json:"dayOfTheMonth,omitempty"`
	DaysOfTheMonth  []string `json:"daysOfTheMonth,omitempty"`
	WeeksOfTheMonth []string `json:"weeksOfTheMonth,omitempty"`
	DaysOfTheWeek   []string `json:"daysOfTheWeek,omitempty"`
	Month           *int32   `json:"month,omitempty"`
	Months          []int32  `json:"months,omitempty"`
	StartDate       *string  `json:"startDate,omitempty"`
	ExpirationDate  *string  `json:"expirationDate,omitempty"`
	TimeZone        *string  `json:"timeZone,omitempty"`
	ExpireRuns      *int32   `json:"expireRuns,omitempty"`
}

// MaintenanceSchedule is the fixture's focused model.
type MaintenanceSchedule struct {
	ID       string   `json:"id,omitempty"`
	Key      string   `json:"key"`
	Schedule Schedule `json:"schedule"`
}

// Request is one copied request-log entry.
type Request struct {
	Method           string
	Path             string
	RawQuery         string
	Header           http.Header
	Body             []byte
	TransferEncoding []string
}

// Runtime exposes values generated after the fixture starts.
type Runtime struct {
	AccessToken string
	KeyPrefix   string
}

// Server is an ephemeral IPv4-loopback fixture with a race-safe request log.
type Server struct {
	config     Config
	runtime    Runtime
	listener   net.Listener
	httpServer *http.Server
	transport  *http.Transport

	mu       sync.Mutex
	requests []Request
}

type contractFile struct {
	Servers []struct {
		URL string `json:"url"`
	} `json:"servers"`
	Operations []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	} `json:"operations"`
}

// New validates docs/contract.json and starts a loopback fixture exposing
// only the named operation.
func New(config Config) (*Server, error) {
	if err := validateContract("docs/contract.json"); err != nil {
		return nil, err
	}
	nonceBytes := make([]byte, 12)
	if _, err := rand.Read(nonceBytes); err != nil {
		return nil, fmt.Errorf("generate fixture nonce: %w", err)
	}
	nonce := hex.EncodeToString(nonceBytes)
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("listen on loopback: %w", err)
	}
	transport := &http.Transport{Proxy: nil}
	server := &Server{
		config: config,
		runtime: Runtime{
			AccessToken: "ops-token-" + nonce,
			KeyPrefix:   "schedule-" + nonce,
		},
		listener:  listener,
		transport: transport,
	}
	server.httpServer = &http.Server{Handler: server}
	go func() {
		_ = server.httpServer.Serve(listener)
	}()
	return server, nil
}

// URL returns the loopback origin, without the contract's /suite-api base.
func (s *Server) URL() string {
	return "http://" + s.listener.Addr().String()
}

// Client returns an HTTP client that never consults proxy environment state.
func (s *Server) Client() *http.Client {
	return &http.Client{Transport: s.transport}
}

// Runtime returns the immutable runtime values.
func (s *Server) Runtime() Runtime {
	return s.runtime
}

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	result := make([]Request, len(s.requests))
	for index, request := range s.requests {
		result[index] = Request{
			Method:           request.Method,
			Path:             request.Path,
			RawQuery:         request.RawQuery,
			Header:           request.Header.Clone(),
			Body:             append([]byte(nil), request.Body...),
			TransferEncoding: append([]string(nil), request.TransferEncoding...),
		}
	}
	return result
}

// Close stops the fixture and releases idle client connections.
func (s *Server) Close() error {
	if s == nil {
		return nil
	}
	if s.transport != nil {
		s.transport.CloseIdleConnections()
	}
	if s.httpServer == nil {
		return nil
	}
	return s.httpServer.Shutdown(context.Background())
}

func (s *Server) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost || request.URL.Path != "/suite-api/api/maintenanceschedules" {
		http.NotFound(writer, request)
		return
	}
	body, err := io.ReadAll(request.Body)
	if err != nil {
		http.Error(writer, "request read failed", http.StatusBadRequest)
		return
	}
	entry := Request{
		Method:           request.Method,
		Path:             request.URL.Path,
		RawQuery:         request.URL.RawQuery,
		Header:           request.Header.Clone(),
		Body:             append([]byte(nil), body...),
		TransferEncoding: append([]string(nil), request.TransferEncoding...),
	}
	s.mu.Lock()
	requestIndex := len(s.requests)
	s.requests = append(s.requests, entry)
	s.mu.Unlock()

	if request.Header.Get("Authorization") != s.runtime.AccessToken {
		writer.WriteHeader(http.StatusUnauthorized)
		return
	}
	if requestIndex == s.config.FailAt {
		writer.WriteHeader(http.StatusUnprocessableEntity)
		return
	}
	var input MaintenanceSchedule
	if err := json.Unmarshal(body, &input); err != nil || input.Key == "" {
		writer.WriteHeader(http.StatusBadRequest)
		return
	}
	input.ID = fmt.Sprintf("00000000-0000-4000-8000-%012x", requestIndex+1)
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(writer).Encode(input)
}

func validateContract(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read focused contract: %w", err)
	}
	var contract contractFile
	if err := json.Unmarshal(data, &contract); err != nil {
		return fmt.Errorf("decode focused contract: %w", err)
	}
	if len(contract.Servers) != 1 || contract.Servers[0].URL != "/suite-api" {
		return fmt.Errorf("focused contract must name only /suite-api as its server")
	}
	if len(contract.Operations) != 1 {
		return fmt.Errorf("focused contract must contain exactly one operation")
	}
	operation := contract.Operations[0]
	if operation.OperationID != operationID ||
		!strings.EqualFold(operation.Method, http.MethodPost) ||
		operation.Path != "/api/maintenanceschedules" {
		return fmt.Errorf("focused contract operation does not match %s", operationID)
	}
	return nil
}
