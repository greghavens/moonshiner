package logtask

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"moonshiner/vcf91-0181/internal/contractmock"
)

func TestWaitForOperationPollsToTerminalAndMatchesWire(t *testing.T) {
	upper := int64(1700000000999)
	tests := []struct {
		name      string
		states    []string
		start     int64
		end       *int64
		wantPolls int
		wantRange string
	}{
		{
			name:      "queued and running before success with unset upper bound",
			states:    []string{"QUEUED", "RUNNING", "SUCCEEDED"},
			start:     1700000000000,
			wantPolls: 3,
			wantRange: `{"gte":"1700000000000"}`,
		},
		{
			name:      "no event then blocked before success with explicit upper bound",
			states:    []string{"", "BLOCKED", "SUCCEEDED"},
			start:     0,
			end:       &upper,
			wantPolls: 3,
			wantRange: `{"gte":"0","lte":"1700000000999"}`,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			fixture := contractmock.New(t, contractPath(), contractmock.Options{
				States: test.states,
			})
			token := "token-wire"
			client := mustClient(t, fixture, token, test.wantPolls+1)
			operationID := "op-<green>&-Ω"

			result, err := client.WaitForOperation(context.Background(), WaitRequest{
				OperationID: operationID,
				StartTimeMS: test.start,
				EndTimeMS:   test.end,
			})
			if err != nil {
				t.Fatalf("WaitForOperation() error = %v", err)
			}
			if result.OperationID != operationID ||
				result.State != StateSucceeded ||
				result.Polls != test.wantPolls ||
				result.ObservedAt == 0 ||
				result.Message != "structured operation progress event" {
				t.Fatalf("WaitForOperation() result = %+v", result)
			}

			wantBody := fmt.Sprintf(
				`{"query":{"bool":{"filter":[{"match_phrase":{"operation_id":"%s"}},{"match_phrase":{"event_type":"%s"}},{"range":{"timestamp":%s}}]}},"size":1,"sort":[{"timestamp":{"order":"desc"}}],"trackTotalHits":false}`,
				operationID,
				StateEventType,
				test.wantRange,
			)
			log := fixture.Log()
			if len(log) != test.wantPolls {
				t.Fatalf("request count = %d, want %d", len(log), test.wantPolls)
			}
			for index, record := range log {
				if record.Operation != "searchOperationStateEvents" {
					t.Errorf("request %d operation = %q", index, record.Operation)
				}
				if record.Method != http.MethodPost {
					t.Errorf("request %d method = %q", index, record.Method)
				}
				if record.RequestURI != "/api/v2/logs/search" {
					t.Errorf("request %d target = %q", index, record.RequestURI)
				}
				assertOneHeader(t, record.Header, "Accept", "application/json")
				assertOneHeader(t, record.Header, "Content-Type", "application/json")
				assertOneHeader(t, record.Header, "X-JWT-Token", token)
				if got := record.Header.Values("Authorization"); len(got) != 0 {
					t.Errorf("request %d Authorization = %q", index, got)
				}
				if string(record.Body) != wantBody {
					t.Errorf("request %d body\n got: %s\nwant: %s", index, record.Body, wantBody)
				}
				if record.ContentLength != int64(len(wantBody)) {
					t.Errorf("request %d ContentLength = %d, want %d", index, record.ContentLength, len(wantBody))
				}
				if len(record.TransferEncoding) != 0 {
					t.Errorf("request %d Transfer-Encoding = %q", index, record.TransferEncoding)
				}
				for _, unset := range []string{
					`"aggregations"`,
					`"from"`,
					`"indices"`,
					`"scroll"`,
					`"scrollSize"`,
				} {
					if strings.Contains(string(record.Body), unset) {
						t.Errorf("request %d contains unset optional %s", index, unset)
					}
				}
				if test.end == nil && strings.Contains(string(record.Body), `"lte"`) {
					t.Errorf("request %d serialized unset lte", index)
				}
				if !strings.Contains(string(record.Body), `"trackTotalHits":false`) {
					t.Errorf("request %d omitted explicit false", index)
				}
				if strings.Contains(string(record.Body), `\u003c`) ||
					strings.Contains(string(record.Body), `\u0026`) ||
					strings.HasSuffix(string(record.Body), "\n") {
					t.Errorf("request %d is not direct compact UTF-8 JSON: %q", index, record.Body)
				}
			}
		})
	}
}

func TestNewClientValidationAndIsolation(t *testing.T) {
	valid := func() Config {
		return Config{
			BaseURL:      "http://127.0.0.1:8443",
			Token:        "token",
			PollInterval: 0,
			MaxPolls:     1,
		}
	}
	tests := []struct {
		name   string
		change func(*Config)
	}{
		{"blank origin", func(c *Config) { c.BaseURL = "" }},
		{"relative origin", func(c *Config) { c.BaseURL = "localhost:8443" }},
		{"unsupported scheme", func(c *Config) { c.BaseURL = "ftp://host" }},
		{"credentials", func(c *Config) { c.BaseURL = "https://user@host" }},
		{"non-root path", func(c *Config) { c.BaseURL = "https://host/api" }},
		{"query", func(c *Config) { c.BaseURL = "https://host/?x=1" }},
		{"bare query", func(c *Config) { c.BaseURL = "https://host/?" }},
		{"fragment", func(c *Config) { c.BaseURL = "https://host/#x" }},
		{"bad port", func(c *Config) { c.BaseURL = "https://host:70000" }},
		{"blank token", func(c *Config) { c.Token = "" }},
		{"surrounding token whitespace", func(c *Config) { c.Token = " token" }},
		{"header injection", func(c *Config) { c.Token = "token\r\nX-Evil: yes" }},
		{"negative interval", func(c *Config) { c.PollInterval = -time.Nanosecond }},
		{"zero max polls", func(c *Config) { c.MaxPolls = 0 }},
		{"negative max polls", func(c *Config) { c.MaxPolls = -1 }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			config := valid()
			test.change(&config)
			if _, err := NewClient(config); err == nil {
				t.Fatal("NewClient() error = nil")
			}
		})
	}

	redirectCalls := 0
	original := &http.Client{
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			redirectCalls++
			return nil
		},
	}
	config := valid()
	config.BaseURL = "https://example.com/"
	config.HTTPClient = original
	client, err := NewClient(config)
	if err != nil {
		t.Fatalf("NewClient(valid) error = %v", err)
	}
	if client.httpClient == original {
		t.Fatal("NewClient retained caller-owned *http.Client")
	}
	request, _ := http.NewRequest(http.MethodGet, "https://example.com", nil)
	if err := client.httpClient.CheckRedirect(request, nil); !errors.Is(err, http.ErrUseLastResponse) {
		t.Fatalf("copied CheckRedirect error = %v", err)
	}
	if redirectCalls != 0 || original.CheckRedirect == nil {
		t.Fatal("NewClient mutated or invoked the caller's redirect policy")
	}
}

func TestWaitRequestValidationFinishesBeforeTraffic(t *testing.T) {
	endEqual := int64(10)
	endEarlier := int64(9)
	fixture := contractmock.New(t, contractPath(), contractmock.Options{})
	client := mustClient(t, fixture, "token-validation", 2)
	tests := []struct {
		name    string
		ctx     context.Context
		request WaitRequest
	}{
		{"nil context", nil, WaitRequest{OperationID: "op", StartTimeMS: 0}},
		{"blank operation", context.Background(), WaitRequest{OperationID: "", StartTimeMS: 0}},
		{"whitespace operation", context.Background(), WaitRequest{OperationID: " op", StartTimeMS: 0}},
		{"header controls", context.Background(), WaitRequest{OperationID: "op\nx", StartTimeMS: 0}},
		{"negative start", context.Background(), WaitRequest{OperationID: "op", StartTimeMS: -1}},
		{"equal end", context.Background(), WaitRequest{OperationID: "op", StartTimeMS: 10, EndTimeMS: &endEqual}},
		{"earlier end", context.Background(), WaitRequest{OperationID: "op", StartTimeMS: 10, EndTimeMS: &endEarlier}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := client.WaitForOperation(test.ctx, test.request); err == nil {
				t.Fatal("WaitForOperation() error = nil")
			}
		})
	}
	if got := len(fixture.Log()); got != 0 {
		t.Fatalf("invalid inputs made %d requests", got)
	}
}

func TestTerminalAndTimeoutOutcomes(t *testing.T) {
	tests := []struct {
		name        string
		states      []string
		maxPolls    int
		wantFailed  bool
		wantTimeout bool
		wantProto   bool
		wantPolls   int
	}{
		{"failed", []string{"RUNNING", "FAILED"}, 4, true, false, false, 2},
		{"cancelled", []string{"CANCELLED"}, 4, true, false, false, 1},
		{"exhausted while nonterminal", []string{"QUEUED", "RUNNING"}, 2, false, true, false, 2},
		{"unknown state", []string{"DONE"}, 4, false, false, true, 1},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			fixture := contractmock.New(t, contractPath(), contractmock.Options{
				States: test.states,
			})
			client := mustClient(t, fixture, "token-outcome", test.maxPolls)
			_, err := client.WaitForOperation(context.Background(), WaitRequest{
				OperationID: "operation-outcome",
				StartTimeMS: 1,
			})
			if err == nil {
				t.Fatal("WaitForOperation() error = nil")
			}
			var failed *OperationFailedError
			var timeout *PollTimeoutError
			var protocol *ProtocolError
			if errors.As(err, &failed) != test.wantFailed ||
				errors.As(err, &timeout) != test.wantTimeout ||
				errors.As(err, &protocol) != test.wantProto {
				t.Fatalf("WaitForOperation() error type = %T (%v)", err, err)
			}
			if got := len(fixture.Log()); got != test.wantPolls {
				t.Fatalf("request count = %d, want %d", got, test.wantPolls)
			}
			if failed != nil && (failed.Polls != test.wantPolls || failed.State == StateSucceeded) {
				t.Fatalf("OperationFailedError = %+v", failed)
			}
			if timeout != nil && timeout.Polls != test.wantPolls {
				t.Fatalf("PollTimeoutError = %+v", timeout)
			}
		})
	}
}

func TestMalformedAndUnsafeResponses(t *testing.T) {
	tests := []struct {
		name      string
		variant   string
		wantAPI   bool
		wantProto bool
	}{
		{"duplicate hit", contractmock.VariantDuplicateHit, false, true},
		{"duplicate required field", contractmock.VariantDuplicateField, false, true},
		{"mismatched operation", contractmock.VariantMismatch, false, true},
		{"missing timestamp", contractmock.VariantMissingTime, false, true},
		{"non-string state", contractmock.VariantNonStringState, false, true},
		{"query timeout", contractmock.VariantTimedOut, false, true},
		{"query failure", contractmock.VariantFailureReason, false, true},
		{"invalid JSON", contractmock.VariantInvalidJSON, false, true},
		{"JSON array success", contractmock.VariantJSONArray, false, true},
		{"HTTP error", contractmock.VariantHTTPError, true, false},
		{"wrong media type", contractmock.VariantWrongMedia, false, true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			fixture := contractmock.New(t, contractPath(), contractmock.Options{
				States:  []string{"SUCCEEDED"},
				Variant: test.variant,
			})
			token := "sensitive-token"
			client := mustClient(t, fixture, token, 2)
			_, err := client.WaitForOperation(context.Background(), WaitRequest{
				OperationID: "malformed-operation",
				StartTimeMS: 0,
			})
			if err == nil {
				t.Fatal("WaitForOperation() error = nil")
			}
			var api *APIError
			var protocol *ProtocolError
			if errors.As(err, &api) != test.wantAPI ||
				errors.As(err, &protocol) != test.wantProto {
				t.Fatalf("WaitForOperation() error type = %T (%v)", err, err)
			}
			for _, forbidden := range []string{
				token,
				"fixture detail",
				"errorMessage",
				"structured operation progress event",
			} {
				if strings.Contains(err.Error(), forbidden) {
					t.Fatalf("error exposes %q: %v", forbidden, err)
				}
			}
			if got := len(fixture.Log()); got != 1 {
				t.Fatalf("request count = %d, want 1", got)
			}
		})
	}
}

func TestPollingWaitHonorsContext(t *testing.T) {
	fixture := contractmock.New(t, contractPath(), contractmock.Options{
		States: []string{"RUNNING"},
	})
	config := Config{
		BaseURL:      fixture.URL(),
		Token:        "token-context",
		HTTPClient:   fixture.Client(),
		PollInterval: time.Hour,
		MaxPolls:     3,
	}
	client, err := NewClient(config)
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	_, err = client.WaitForOperation(ctx, WaitRequest{
		OperationID: "context-operation",
		StartTimeMS: 0,
	})
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("WaitForOperation() error = %v, want context deadline", err)
	}
	if got := len(fixture.Log()); got != 1 {
		t.Fatalf("request count = %d, want 1", got)
	}
}

func TestClientConcurrentUseIsRaceSafe(t *testing.T) {
	fixture := contractmock.New(t, contractPath(), contractmock.Options{
		States: []string{"QUEUED", "RUNNING", "SUCCEEDED"},
	})
	client := mustClient(t, fixture, "token-concurrent", 4)

	const workers = 8
	var wait sync.WaitGroup
	errs := make(chan error, workers)
	for index := 0; index < workers; index++ {
		wait.Add(1)
		go func(index int) {
			defer wait.Done()
			operationID := fmt.Sprintf("concurrent-%d", index)
			result, err := client.WaitForOperation(context.Background(), WaitRequest{
				OperationID: operationID,
				StartTimeMS: int64(index),
			})
			if err != nil {
				errs <- err
				return
			}
			if result.OperationID != operationID ||
				result.State != StateSucceeded ||
				result.Polls != 3 {
				errs <- fmt.Errorf("unexpected result: %+v", result)
			}
		}(index)
	}
	wait.Wait()
	close(errs)
	for err := range errs {
		t.Error(err)
	}
	if got := len(fixture.Log()); got != workers*3 {
		t.Fatalf("request count = %d, want %d", got, workers*3)
	}
}

func mustClient(t *testing.T, fixture *contractmock.Server, token string, maxPolls int) *Client {
	t.Helper()
	client, err := NewClient(Config{
		BaseURL:      fixture.URL(),
		Token:        token,
		HTTPClient:   fixture.Client(),
		PollInterval: 0,
		MaxPolls:     maxPolls,
	})
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}
	return client
}

func contractPath() string {
	return filepath.Join("..", "docs", "contract.json")
}

func assertOneHeader(t *testing.T, header http.Header, name, want string) {
	t.Helper()
	values := header.Values(name)
	if len(values) != 1 || values[0] != want {
		t.Errorf("%s = %q, want exactly [%q]", name, values, want)
	}
}
