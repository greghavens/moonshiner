// Package contractmock provides the loopback-only getDomains fixture pinned to
// docs/contract.json. It is test infrastructure, not an SDDC Manager emulator.
package contractmock

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
)

// Mode selects a deterministic response behavior.
type Mode string

const (
	ModeOK                 Mode = "ok"
	ModeEmpty              Mode = "empty"
	ModeAPIError           Mode = "api-error"
	ModeMalformed          Mode = "malformed"
	ModeWrongMediaType     Mode = "wrong-media-type"
	ModeTrailingJSON       Mode = "trailing-json"
	ModeOversized          Mode = "oversized"
	ModeBadPageNumber      Mode = "bad-page-number"
	ModeBadPageSize        Mode = "bad-page-size"
	ModeInconsistentTotals Mode = "inconsistent-totals"
	ModeCountMismatch      Mode = "count-mismatch"
	ModeNegativeMetadata   Mode = "negative-metadata"
	ModeRedirect           Mode = "redirect"
)

// Domain is one fixture domain.
type Domain struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Status string `json:"status,omitempty"`
	Type   string `json:"type,omitempty"`
}

// Runtime contains values generated when the fixture starts.
type Runtime struct {
	AccessToken       string
	Domains           []Domain
	ErrorCode         string
	ErrorMessage      string
	Remediation       string
	ReferenceToken    string
	TransportPageSize int
}

// Request records one received HTTP request. Header and Body are copied.
type Request struct {
	Method           string
	Path             string
	RawQuery         string
	Header           http.Header
	Body             []byte
	TransferEncoding []string
	Reversed         bool
}

// Server is a loopback-only contract fixture with a race-safe request log.
type Server struct {
	mode       Mode
	runtime    Runtime
	listener   net.Listener
	httpServer *http.Server

	mu             sync.Mutex
	requests       []Request
	responseSerial int
}

type contractFile struct {
	Operations []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	} `json:"operations"`
}

// New loads the protected contract and starts an ephemeral IPv4 loopback
// server. The fixture exposes only the operation named by that contract.
func New(mode Mode) (*Server, error) {
	if err := validateContract("docs/contract.json"); err != nil {
		return nil, err
	}
	if mode == "" {
		mode = ModeOK
	}

	nonce, err := randomHex(12)
	if err != nil {
		return nil, fmt.Errorf("generate fixture values: %w", err)
	}
	runtime := Runtime{
		AccessToken:       "access-" + nonce,
		ErrorCode:         "ERR-" + nonce,
		ErrorMessage:      "server-message-" + nonce,
		Remediation:       "server-remediation-" + nonce,
		ReferenceToken:    "reference-" + nonce,
		TransportPageSize: 2,
		Domains: []Domain{
			{ID: nonce + "-z", Name: "zulu", Status: "ACTIVE", Type: "VI"},
			{ID: nonce + "-b", Name: "alpha", Status: "ACTIVE", Type: "VI"},
			{ID: nonce + "-c", Name: "charlie", Status: "UPGRADING", Type: "VI"},
			{ID: nonce + "-a", Name: "alpha", Status: "ACTIVE", Type: "MANAGEMENT"},
			{ID: nonce + "-d", Name: "delta", Status: "CREATING", Type: "VI"},
			{ID: nonce + "-r", Name: "bravo", Status: "ACTIVE", Type: "VI"},
		},
	}

	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("listen on loopback: %w", err)
	}
	server := &Server{
		mode:     mode,
		runtime:  runtime,
		listener: listener,
	}
	server.httpServer = &http.Server{Handler: server}
	go func() {
		_ = server.httpServer.Serve(listener)
	}()
	return server, nil
}

// URL returns the fixture's loopback origin.
func (s *Server) URL() string {
	if s == nil || s.listener == nil {
		return ""
	}
	return "http://" + s.listener.Addr().String()
}

// Client returns an HTTP client suitable for the fixture.
func (s *Server) Client() *http.Client {
	return &http.Client{}
}

// Runtime returns a copy of all runtime-generated fixture values.
func (s *Server) Runtime() Runtime {
	if s == nil {
		return Runtime{}
	}
	out := s.runtime
	out.Domains = append([]Domain(nil), s.runtime.Domains...)
	return out
}

// Requests returns a deep copy of the race-safe request log.
func (s *Server) Requests() []Request {
	if s == nil {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.requests))
	for index, request := range s.requests {
		out[index] = request
		out[index].Header = request.Header.Clone()
		out[index].Body = append([]byte(nil), request.Body...)
		out[index].TransferEncoding = append(
			[]string(nil),
			request.TransferEncoding...,
		)
	}
	return out
}

// Close stops the fixture.
func (s *Server) Close() {
	if s == nil || s.httpServer == nil {
		return
	}
	_ = s.httpServer.Close()
}

// ServeHTTP serves only getDomains from the pinned contract.
func (s *Server) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	body, _ := io.ReadAll(request.Body)
	reversed := s.nextReversal()
	s.record(request, body, reversed)

	if request.Method != http.MethodGet || request.URL.Path != "/v1/domains" {
		http.NotFound(writer, request)
		return
	}
	if request.Header.Get("Authorization") !=
		"Bearer "+s.runtime.AccessToken ||
		request.Header.Get("Accept") != "application/json" ||
		request.Header.Get("Content-Type") != "" ||
		len(body) != 0 ||
		len(request.TransferEncoding) != 0 {
		s.writeAPIError(writer, http.StatusBadRequest)
		return
	}

	pageNumber, ok := parsePageQuery(
		request.URL.Query(),
		s.runtime.TransportPageSize,
	)
	if !ok {
		s.writeAPIError(writer, http.StatusBadRequest)
		return
	}

	switch s.mode {
	case ModeAPIError:
		s.writeAPIError(writer, http.StatusInternalServerError)
		return
	case ModeMalformed:
		writer.Header().Set("Content-Type", "application/json")
		writer.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(writer, `{"elements":[`)
		return
	case ModeWrongMediaType:
		writer.Header().Set("Content-Type", "text/plain")
	case ModeOversized:
		writer.Header().Set("Content-Type", "application/json")
		writer.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(writer, strings.Repeat("x", (1<<20)+1))
		return
	case ModeRedirect:
		writer.Header().Set(
			"Location",
			"/v1/domains?pageNumber=0&pageSize=2",
		)
		writer.WriteHeader(http.StatusFound)
		return
	default:
		writer.Header().Set("Content-Type", "application/json; charset=utf-8")
	}

	page, ok := s.page(pageNumber, reversed)
	if !ok {
		s.writeAPIError(writer, http.StatusBadRequest)
		return
	}
	if s.mode == ModeBadPageNumber {
		page.PageMetadata.PageNumber++
	}
	if s.mode == ModeBadPageSize {
		page.PageMetadata.PageSize++
	}
	if s.mode == ModeInconsistentTotals && pageNumber == 1 {
		page.PageMetadata.TotalElements--
	}
	if s.mode == ModeCountMismatch {
		page.PageMetadata.TotalElements++
	}
	if s.mode == ModeNegativeMetadata {
		page.PageMetadata.TotalPages = -1
	}

	writer.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(writer).Encode(page)
	if s.mode == ModeTrailingJSON {
		_, _ = io.WriteString(writer, "{}\n")
	}
}

type pageMetadata struct {
	PageNumber    int `json:"pageNumber"`
	PageSize      int `json:"pageSize"`
	TotalElements int `json:"totalElements"`
	TotalPages    int `json:"totalPages"`
}

type domainPage struct {
	Elements     []Domain     `json:"elements"`
	PageMetadata pageMetadata `json:"pageMetadata"`
}

func (s *Server) page(pageNumber int, reversed bool) (domainPage, bool) {
	if s.mode == ModeEmpty {
		if pageNumber != 0 {
			return domainPage{}, false
		}
		return domainPage{
			Elements: []Domain{},
			PageMetadata: pageMetadata{
				PageNumber:    0,
				PageSize:      0,
				TotalElements: 0,
				TotalPages:    0,
			},
		}, true
	}
	if pageNumber < 0 || pageNumber >= 3 {
		return domainPage{}, false
	}
	start := pageNumber * s.runtime.TransportPageSize
	end := start + s.runtime.TransportPageSize
	elements := append([]Domain(nil), s.runtime.Domains[start:end]...)
	if reversed {
		for left, right := 0, len(elements)-1; left < right; left, right =
			left+1, right-1 {
			elements[left], elements[right] = elements[right], elements[left]
		}
	}
	return domainPage{
		Elements: elements,
		PageMetadata: pageMetadata{
			PageNumber:    pageNumber,
			PageSize:      len(elements),
			TotalElements: len(s.runtime.Domains),
			TotalPages:    3,
		},
	}, true
}

func (s *Server) nextReversal() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	reversed := s.responseSerial%2 == 0
	s.responseSerial++
	return reversed
}

func (s *Server) record(
	request *http.Request,
	body []byte,
	reversed bool,
) {
	entry := Request{
		Method:           request.Method,
		Path:             request.URL.Path,
		RawQuery:         request.URL.RawQuery,
		Header:           request.Header.Clone(),
		Body:             append([]byte(nil), body...),
		TransferEncoding: append([]string(nil), request.TransferEncoding...),
		Reversed:         reversed,
	}
	s.mu.Lock()
	s.requests = append(s.requests, entry)
	s.mu.Unlock()
}

func (s *Server) writeAPIError(writer http.ResponseWriter, status int) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(map[string]string{
		"errorCode":          s.runtime.ErrorCode,
		"message":            s.runtime.ErrorMessage,
		"remediationMessage": s.runtime.Remediation,
		"referenceToken":     s.runtime.ReferenceToken,
	})
}

func parsePageQuery(values url.Values, pageSize int) (int, bool) {
	if len(values) != 2 ||
		len(values["pageNumber"]) != 1 ||
		len(values["pageSize"]) != 1 ||
		values.Get("pageSize") != strconv.Itoa(pageSize) {
		return 0, false
	}
	pageNumber, err := strconv.Atoi(values.Get("pageNumber"))
	return pageNumber, err == nil && pageNumber >= 0
}

func validateContract(path string) error {
	content, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read contract: %w", err)
	}
	var contract contractFile
	if err := json.Unmarshal(content, &contract); err != nil {
		return fmt.Errorf("decode contract: %w", err)
	}
	if len(contract.Operations) != 1 {
		return errors.New("contract fixture requires exactly one operation")
	}
	operation := contract.Operations[0]
	if operation.OperationID != "getDomains" ||
		operation.Method != http.MethodGet ||
		operation.Path != "/v1/domains" {
		return errors.New("contract fixture is not pinned to getDomains")
	}
	return nil
}

func randomHex(bytesCount int) (string, error) {
	buffer := make([]byte, bytesCount)
	if _, err := rand.Read(buffer); err != nil {
		return "", err
	}
	return hex.EncodeToString(buffer), nil
}
