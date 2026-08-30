// Package contractmock provides the protected loopback vCenter fixture.
package contractmock

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"
)

const (
	sessionCreateOperation = "Cis.Session_create"
	vmListOperation        = "Vcenter.VM_list"
	sessionDeleteOperation = "Cis.Session_delete"
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

// VM is a fixture Vcenter.VM.Summary.
type VM struct {
	VM            string `json:"vm"`
	Name          string `json:"name"`
	PowerState    string `json:"power_state"`
	CPUCount      *int64 `json:"cpu_count,omitempty"`
	MemorySizeMiB *int64 `json:"memory_size_mib,omitempty"`
}

// Scenario configures one deterministic mock.
type Scenario struct {
	Username    string
	OldPassword string
	NewPassword string
	OldToken    string
	NewToken    string
	OldVMs      []VM
	NewVMs      []VM

	HoldOldTarget       string
	InitialCreateStatus int
	NewCreateStatus     int
	ListStatus          int
	ListBody            []byte
	DeleteStatus        int
	ErrorSecret         string
}

// RequestRecord is the complete filesystem assertion surface.
type RequestRecord struct {
	Sequence               int                 `json:"sequence"`
	OperationID            string              `json:"operation_id"`
	Method                 string              `json:"method"`
	Target                 string              `json:"target"`
	Authorization          []string            `json:"authorization"`
	SessionToken           []string            `json:"session_token"`
	Accept                 []string            `json:"accept"`
	ContentType            []string            `json:"content_type"`
	ContentLength          int64               `json:"content_length"`
	TransferEncoding       []string            `json:"transfer_encoding"`
	BodyBase64             string              `json:"body_base64"`
	Headers                map[string][]string `json:"headers"`
	SlowCompletedAtArrival bool                `json:"slow_completed_at_arrival"`
}

type state struct {
	mu             sync.Mutex
	sequence       int
	oldActive      bool
	newActive      bool
	slowCompleted  bool
	slowArrived    chan struct{}
	slowArrivedOne sync.Once
	releaseSlow    chan struct{}
	releaseOne     sync.Once
	logPath        string
}

// Server is an ephemeral loopback-only contract fixture.
type Server struct {
	URL    string
	Client *http.Client

	close func()
	state *state
}

// Close stops the server.
func (s *Server) Close() {
	s.close()
}

// WaitForSlow waits until the held old-generation list request is logged.
func (s *Server) WaitForSlow(timeout time.Duration) bool {
	select {
	case <-s.state.slowArrived:
		return true
	case <-time.After(timeout):
		return false
	}
}

// ReleaseSlow lets the held response complete.
func (s *Server) ReleaseSlow() {
	s.state.releaseOne.Do(func() {
		close(s.state.releaseSlow)
	})
}

// Start reads the protected contract and serves only its named operations.
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
	var focused contract
	if err := json.Unmarshal(data, &focused); err != nil {
		t.Fatalf("decode contract: %v", err)
	}
	routes := contractRoutes(t, focused)
	validateScenario(t, scenario)

	if scenario.InitialCreateStatus == 0 {
		scenario.InitialCreateStatus = http.StatusCreated
	}
	if scenario.NewCreateStatus == 0 {
		scenario.NewCreateStatus = http.StatusCreated
	}
	if scenario.ListStatus == 0 {
		scenario.ListStatus = http.StatusOK
	}
	if scenario.DeleteStatus == 0 {
		scenario.DeleteStatus = http.StatusNoContent
	}
	if scenario.ErrorSecret == "" {
		scenario.ErrorSecret = "contract fixture private response"
	}
	if err := os.WriteFile(logPath, nil, 0o600); err != nil {
		t.Fatalf("create request log: %v", err)
	}

	st := &state{
		slowArrived: make(chan struct{}),
		releaseSlow: make(chan struct{}),
		logPath:     logPath,
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var (
			body    []byte
			readErr error
		)
		if r.Body != nil {
			body, readErr = io.ReadAll(io.LimitReader(r.Body, 1<<20))
		}
		if readErr != nil {
			writeError(w, http.StatusBadRequest, scenario.ErrorSecret)
			return
		}

		operationID := routes[r.Method+" "+r.URL.Path]
		if r.URL.RawQuery != "" && operationID != vmListOperation {
			operationID = ""
		}

		st.mu.Lock()
		st.sequence++
		record := RequestRecord{
			Sequence:               st.sequence,
			OperationID:            operationID,
			Method:                 r.Method,
			Target:                 r.URL.RequestURI(),
			Authorization:          cloneStrings(r.Header.Values("Authorization")),
			SessionToken:           cloneStrings(r.Header.Values("vmware-api-session-id")),
			Accept:                 cloneStrings(r.Header.Values("Accept")),
			ContentType:            cloneStrings(r.Header.Values("Content-Type")),
			ContentLength:          r.ContentLength,
			TransferEncoding:       cloneStrings(r.TransferEncoding),
			BodyBase64:             base64.StdEncoding.EncodeToString(body),
			Headers:                cloneHeaders(r.Header),
			SlowCompletedAtArrival: st.slowCompleted,
		}
		logErr := appendRecord(st.logPath, record)
		st.mu.Unlock()
		if logErr != nil {
			writeError(w, http.StatusInternalServerError, scenario.ErrorSecret)
			return
		}

		switch operationID {
		case sessionCreateOperation:
			serveCreate(w, r, body, st, scenario)
		case vmListOperation:
			serveList(w, r, body, st, scenario)
		case sessionDeleteOperation:
			serveDelete(w, r, body, st, scenario)
		default:
			writeError(w, http.StatusNotFound, scenario.ErrorSecret)
		}
	})

	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return &Server{
			URL: "http://127.0.0.1",
			Client: &http.Client{
				Transport: roundTripFunc(func(
					request *http.Request,
				) (*http.Response, error) {
					recorder := httptest.NewRecorder()
					handler.ServeHTTP(recorder, request)
					return recorder.Result(), nil
				}),
			},
			close: func() {},
			state: st,
		}
	}
	httpServer := httptest.NewUnstartedServer(handler)
	httpServer.Listener = listener
	httpServer.Start()
	return &Server{
		URL:    httpServer.URL,
		Client: httpServer.Client(),
		close:  httpServer.Close,
		state:  st,
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(
	request *http.Request,
) (*http.Response, error) {
	return function(request)
}

func contractRoutes(t testing.TB, focused contract) map[string]string {
	t.Helper()
	expected := []operation{
		{
			OperationID: sessionCreateOperation,
			Method:      http.MethodPost,
			Path:        "/session",
		},
		{
			OperationID: vmListOperation,
			Method:      http.MethodGet,
			Path:        "/vcenter/vm",
		},
		{
			OperationID: sessionDeleteOperation,
			Method:      http.MethodDelete,
			Path:        "/session",
		},
	}
	if focused.BasePath != "/api" || len(focused.Operations) != len(expected) {
		t.Fatalf(
			"unexpected focused contract: base=%q operations=%d",
			focused.BasePath,
			len(focused.Operations),
		)
	}
	routes := make(map[string]string, len(expected))
	for index, want := range expected {
		got := focused.Operations[index]
		if got != want {
			t.Fatalf("operation %d = %#v, want %#v", index, got, want)
		}
		routes[got.Method+" "+focused.BasePath+got.Path] = got.OperationID
	}
	return routes
}

func validateScenario(t testing.TB, scenario Scenario) {
	t.Helper()
	for name, value := range map[string]string{
		"Username":    scenario.Username,
		"OldPassword": scenario.OldPassword,
		"NewPassword": scenario.NewPassword,
		"OldToken":    scenario.OldToken,
		"NewToken":    scenario.NewToken,
	} {
		if strings.TrimSpace(value) == "" {
			t.Fatalf("scenario %s is blank", name)
		}
	}
	if scenario.OldPassword == scenario.NewPassword ||
		scenario.OldToken == scenario.NewToken {
		t.Fatal("scenario generations must use distinct credentials and tokens")
	}
}

func serveCreate(
	w http.ResponseWriter,
	r *http.Request,
	body []byte,
	st *state,
	scenario Scenario,
) {
	if !commonBodyless(r, body) ||
		len(r.Header.Values("Authorization")) != 1 ||
		len(r.Header.Values("vmware-api-session-id")) != 0 {
		writeError(w, http.StatusUnauthorized, scenario.ErrorSecret)
		return
	}
	oldAuthorization := basic(scenario.Username, scenario.OldPassword)
	newAuthorization := basic(scenario.Username, scenario.NewPassword)
	switch r.Header.Get("Authorization") {
	case oldAuthorization:
		if scenario.InitialCreateStatus != http.StatusCreated {
			writeConfiguredStatus(w, scenario.InitialCreateStatus, scenario.ErrorSecret)
			return
		}
		st.mu.Lock()
		st.oldActive = true
		st.mu.Unlock()
		writeJSON(w, http.StatusCreated, scenario.OldToken)
	case newAuthorization:
		if scenario.NewCreateStatus != http.StatusCreated {
			writeConfiguredStatus(w, scenario.NewCreateStatus, scenario.ErrorSecret)
			return
		}
		st.mu.Lock()
		st.newActive = true
		st.mu.Unlock()
		writeJSON(w, http.StatusCreated, scenario.NewToken)
	default:
		writeError(w, http.StatusUnauthorized, scenario.ErrorSecret)
	}
}

func serveList(
	w http.ResponseWriter,
	r *http.Request,
	body []byte,
	st *state,
	scenario Scenario,
) {
	if !commonBodyless(r, body) ||
		len(r.Header.Values("Authorization")) != 0 ||
		len(r.Header.Values("vmware-api-session-id")) != 1 {
		writeError(w, http.StatusBadRequest, scenario.ErrorSecret)
		return
	}
	token := r.Header.Get("vmware-api-session-id")
	st.mu.Lock()
	active := token == scenario.OldToken && st.oldActive ||
		token == scenario.NewToken && st.newActive
	st.mu.Unlock()
	if !active {
		writeError(w, http.StatusUnauthorized, scenario.ErrorSecret)
		return
	}
	if scenario.ListStatus != http.StatusOK {
		writeConfiguredStatus(w, scenario.ListStatus, scenario.ErrorSecret)
		return
	}

	if token == scenario.OldToken &&
		scenario.HoldOldTarget != "" &&
		r.URL.RequestURI() == scenario.HoldOldTarget {
		st.slowArrivedOne.Do(func() {
			close(st.slowArrived)
		})
		<-st.releaseSlow
		st.mu.Lock()
		st.slowCompleted = true
		st.mu.Unlock()
	}
	if scenario.ListBody != nil {
		writeRawJSON(w, http.StatusOK, scenario.ListBody)
		return
	}
	if token == scenario.OldToken {
		writeJSON(w, http.StatusOK, scenario.OldVMs)
		return
	}
	writeJSON(w, http.StatusOK, scenario.NewVMs)
}

func serveDelete(
	w http.ResponseWriter,
	r *http.Request,
	body []byte,
	st *state,
	scenario Scenario,
) {
	if !commonBodyless(r, body) ||
		len(r.Header.Values("Authorization")) != 0 ||
		len(r.Header.Values("vmware-api-session-id")) != 1 {
		writeError(w, http.StatusBadRequest, scenario.ErrorSecret)
		return
	}
	token := r.Header.Get("vmware-api-session-id")
	st.mu.Lock()
	active := token == scenario.OldToken && st.oldActive ||
		token == scenario.NewToken && st.newActive
	if active && scenario.DeleteStatus == http.StatusNoContent {
		if token == scenario.OldToken {
			st.oldActive = false
		} else {
			st.newActive = false
		}
	}
	st.mu.Unlock()
	if !active {
		writeError(w, http.StatusUnauthorized, scenario.ErrorSecret)
		return
	}
	if scenario.DeleteStatus != http.StatusNoContent {
		writeConfiguredStatus(w, scenario.DeleteStatus, scenario.ErrorSecret)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func commonBodyless(r *http.Request, body []byte) bool {
	return len(r.Header.Values("Accept")) == 1 &&
		r.Header.Get("Accept") == "application/json" &&
		len(r.Header.Values("Content-Type")) == 0 &&
		len(r.TransferEncoding) == 0 &&
		len(body) == 0
}

func basic(username, password string) string {
	value := base64.StdEncoding.EncodeToString(
		[]byte(username + ":" + password),
	)
	return "Basic " + value
}

func cloneStrings(values []string) []string {
	return append([]string(nil), values...)
}

func cloneHeaders(source http.Header) map[string][]string {
	result := make(map[string][]string, len(source))
	for key, values := range source {
		result[key] = cloneStrings(values)
	}
	return result
}

func appendRecord(path string, record RequestRecord) error {
	line, err := json.Marshal(record)
	if err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	if _, err := file.Write(append(line, '\n')); err != nil {
		_ = file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		_ = file.Close()
		return err
	}
	return file.Close()
}

func writeConfiguredStatus(w http.ResponseWriter, status int, secret string) {
	if status >= 300 && status <= 399 {
		w.Header().Set("Location", "/api/outside-focused-contract")
		w.WriteHeader(status)
		return
	}
	writeError(w, status, secret)
}

func writeError(w http.ResponseWriter, status int, secret string) {
	writeJSON(w, status, map[string]any{
		"error_type": "CONTRACT_FIXTURE_ERROR",
		"messages": []map[string]any{
			{
				"id":              "contractmock.private",
				"default_message": secret,
				"args":            []string{},
			},
		},
	})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	body, err := json.Marshal(value)
	if err != nil {
		panic(fmt.Sprintf("contractmock marshal: %v", err))
	}
	writeRawJSON(w, status, body)
}

func writeRawJSON(w http.ResponseWriter, status int, body []byte) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Length", fmt.Sprintf("%d", len(body)))
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

// ReadLog decodes the fsynced JSONL request log.
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
	for index, line := range lines {
		var record RequestRecord
		if err := json.Unmarshal([]byte(line), &record); err != nil {
			return nil, fmt.Errorf("request log line %d: %w", index+1, err)
		}
		records = append(records, record)
	}
	return records, nil
}

// AwaitLog waits for a minimum number of fsynced request records.
func AwaitLog(
	path string,
	minimum int,
	timeout time.Duration,
) ([]RequestRecord, error) {
	deadline := time.Now().Add(timeout)
	for {
		records, err := ReadLog(path)
		if err == nil && len(records) >= minimum {
			return records, nil
		}
		if err != nil && !errors.Is(err, os.ErrNotExist) {
			return nil, err
		}
		if time.Now().After(deadline) {
			return nil, fmt.Errorf("request log did not reach %d records", minimum)
		}
		time.Sleep(time.Millisecond)
	}
}
