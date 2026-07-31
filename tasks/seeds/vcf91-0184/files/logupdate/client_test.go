package logupdate_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"moonshiner.local/vcf91/logupdate/internal/contractmock"
	"moonshiner.local/vcf91/logupdate/logupdate"
)

const (
	operationID = "updateLogForwarder"
	commitSHA   = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	specPath    = "specifications/vcf-operations/log-management-openapi.json"
)

func pointer[T any](value T) *T {
	return &value
}

func dynamicToken(t *testing.T) string {
	return "runtime-token-" + strings.NewReplacer("/", "-", " ", "-").Replace(t.Name())
}

func startMock(t *testing.T, opts contractmock.Options) *contractmock.Server {
	t.Helper()
	opts.ContractPath = filepath.Join("..", "docs", "contract.json")
	server, err := contractmock.Start(opts)
	if err != nil {
		t.Fatalf("start contract mock: %v", err)
	}
	t.Cleanup(server.Close)
	return server
}

func newClient(t *testing.T, server *contractmock.Server, token string, attempts int) *logupdate.Client {
	t.Helper()
	client, err := logupdate.NewClient(logupdate.Config{
		BaseURL:     server.URL(),
		Token:       token,
		HTTPClient:  server.Client(),
		MaxAttempts: attempts,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if server.Client().CheckRedirect != nil {
		t.Fatal("NewClient mutated the caller-owned HTTP client")
	}
	return client
}

func TestUpdateLogForwarderWireTable(t *testing.T) {
	t.Parallel()

	emptyObject := json.RawMessage(`{}`)
	emptyTags := map[string]string{}
	tests := []struct {
		name         string
		id           string
		update       logupdate.LogForwarderUpdate
		lose         int
		attempts     int
		wantBody     string
		wantTarget   string
		wantRequests int
	}{
		{
			name: "response loss replays exact PUT and preserves explicit falsy values",
			id:   "edge /?雪",
			update: logupdate.LogForwarderUpdate{
				Certificate:                pointer(""),
				ConnectionRefreshInterval:  pointer(int32(0)),
				Constraints:                &emptyObject,
				Enabled:                    pointer(false),
				ForwardComplementaryFields: pointer(false),
				Host:                       pointer("relay<&雪.example"),
				Name:                       pointer(""),
				Port:                       pointer(int32(0)),
				Protocol:                   pointer(""),
				SSLEnabled:                 pointer(false),
				Tags:                       &emptyTags,
				TransportProtocol:          pointer(""),
				WorkerCount:                pointer(int32(0)),
			},
			lose:         1,
			attempts:     2,
			wantBody:     `{"certificate":"","connectionRefreshInterval":0,"constraints":{},"enabled":false,"forwardComplementaryFields":false,"host":"relay<&雪.example","name":"","port":0,"protocol":"","sslEnabled":false,"tags":{},"transportProtocol":"","workerCount":0}`,
			wantTarget:   "/api/v2/logs/forwarders/edge%20%2F%3F%E9%9B%AA",
			wantRequests: 2,
		},
		{
			name:         "every unset optional is omitted",
			id:           "plain-id",
			update:       logupdate.LogForwarderUpdate{},
			lose:         0,
			attempts:     1,
			wantBody:     `{}`,
			wantTarget:   "/api/v2/logs/forwarders/plain-id",
			wantRequests: 1,
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			token := dynamicToken(t)
			server := startMock(t, contractmock.Options{
				ExpectedToken: token,
				LoseResponses: test.lose,
			})
			client := newClient(t, server, token, test.attempts)

			got, err := client.UpdateLogForwarder(context.Background(), test.id, test.update)
			if err != nil {
				t.Fatalf("UpdateLogForwarder: %v", err)
			}
			if got.ID != test.id {
				t.Fatalf("response ID = %q, want %q", got.ID, test.id)
			}
			if effects := server.Effects(); effects != 1 {
				t.Fatalf("logical effects = %d, want 1", effects)
			}

			records, err := server.ReadLog()
			if err != nil {
				t.Fatalf("read request log: %v", err)
			}
			if len(records) != test.wantRequests {
				t.Fatalf("request count = %d, want %d", len(records), test.wantRequests)
			}
			for index, record := range records {
				if record.OperationID != operationID {
					t.Errorf("request %d operationId = %q", index, record.OperationID)
				}
				if record.Method != http.MethodPut {
					t.Errorf("request %d method = %q, want PUT", index, record.Method)
				}
				if record.RequestURI != test.wantTarget {
					t.Errorf("request %d target = %q, want %q", index, record.RequestURI, test.wantTarget)
				}
				assertOneHeader(t, index, record.Headers, "Accept", "application/json")
				assertOneHeader(t, index, record.Headers, "Content-Type", "application/json")
				assertOneHeader(t, index, record.Headers, "X-JWT-Token", token)
				if record.Body != test.wantBody {
					t.Errorf("request %d body\n got: %s\nwant: %s", index, record.Body, test.wantBody)
				}
				var body map[string]json.RawMessage
				if err := json.Unmarshal([]byte(record.Body), &body); err != nil {
					t.Fatalf("request %d body is not JSON: %v", index, err)
				}
				if _, sent := body["id"]; sent {
					t.Errorf("request %d sent read-only id", index)
				}
			}
			for index := 1; index < len(records); index++ {
				if records[index].Method != records[0].Method ||
					records[index].RequestURI != records[0].RequestURI ||
					records[index].Body != records[0].Body {
					t.Errorf("attempt %d is not wire-identical to attempt 0", index)
				}
			}
		})
	}
}

func TestHTTPResponsesAreNeverRetried(t *testing.T) {
	t.Parallel()
	for _, status := range []int{302, 400, 403, 404, 500, 502} {
		status := status
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			t.Parallel()
			token := dynamicToken(t)
			opts := contractmock.Options{
				ExpectedToken:  token,
				ResponseStatus: status,
			}
			if status == http.StatusFound {
				opts.RedirectLocation = "/api/v2/logs/forwarders"
			}
			server := startMock(t, opts)
			client := newClient(t, server, token, 4)

			_, err := client.UpdateLogForwarder(
				context.Background(),
				"http-failure",
				logupdate.LogForwarderUpdate{},
			)
			var apiErr *logupdate.Error
			if !errors.As(err, &apiErr) {
				t.Fatalf("error type = %T, want *logupdate.Error", err)
			}
			if apiErr.OperationID != operationID || apiErr.Kind != logupdate.KindHTTP ||
				apiErr.StatusCode != status || apiErr.Attempts != 1 {
				t.Fatalf("HTTP error = %#v", apiErr)
			}
			if rendered := err.Error(); strings.Contains(rendered, token) ||
				strings.Contains(rendered, "configured HTTP failure") {
				t.Fatalf("HTTP error exposed protected data: %q", rendered)
			}
			records, readErr := server.ReadLog()
			if readErr != nil {
				t.Fatal(readErr)
			}
			if len(records) != 1 {
				t.Fatalf("HTTP %d request count = %d, want 1", status, len(records))
			}
			if server.Effects() != 0 {
				t.Fatalf("HTTP %d changed mock state", status)
			}
		})
	}
}

func TestTransportExhaustionUsesExactAttempts(t *testing.T) {
	t.Parallel()
	token := dynamicToken(t)
	server := startMock(t, contractmock.Options{
		ExpectedToken: token,
		LoseResponses: 3,
	})
	client := newClient(t, server, token, 3)

	_, err := client.UpdateLogForwarder(
		context.Background(),
		"ambiguous",
		logupdate.LogForwarderUpdate{Enabled: pointer(false)},
	)
	var transportErr *logupdate.Error
	if !errors.As(err, &transportErr) {
		t.Fatalf("error type = %T, want *logupdate.Error", err)
	}
	if transportErr.Kind != logupdate.KindTransport || transportErr.Attempts != 3 {
		t.Fatalf("transport error = %#v", transportErr)
	}
	if rendered := err.Error(); strings.Contains(rendered, token) ||
		strings.Contains(strings.ToLower(rendered), "unexpected eof") {
		t.Fatalf("transport error exposed protected data: %q", rendered)
	}
	records, readErr := server.ReadLog()
	if readErr != nil {
		t.Fatal(readErr)
	}
	if len(records) != 3 {
		t.Fatalf("request count = %d, want 3", len(records))
	}
	for index := 1; index < len(records); index++ {
		if records[index].RequestURI != records[0].RequestURI ||
			records[index].Body != records[0].Body {
			t.Fatalf("transport retry %d differs from first request", index)
		}
	}
	if effects := server.Effects(); effects != 1 {
		t.Fatalf("logical effects = %d, want 1", effects)
	}
}

func TestSuccessfulResponseValidationTable(t *testing.T) {
	t.Parallel()
	oversized := bytes.Repeat([]byte(" "), (1<<20)+1)
	tests := []struct {
		name        string
		body        []byte
		contentType string
	}{
		{name: "null", body: []byte(`null`), contentType: "application/json"},
		{name: "array", body: []byte(`[]`), contentType: "application/json"},
		{name: "scalar", body: []byte(`1`), contentType: "application/json"},
		{name: "malformed", body: []byte(`{`), contentType: "application/json"},
		{name: "trailing data", body: []byte(`{} {}`), contentType: "application/json"},
		{name: "wrong media type", body: []byte(`{}`), contentType: "text/plain"},
		{name: "oversized", body: oversized, contentType: "application/json"},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			token := dynamicToken(t)
			server := startMock(t, contractmock.Options{
				ExpectedToken:       token,
				ResponseBody:        test.body,
				ResponseContentType: test.contentType,
			})
			client := newClient(t, server, token, 3)
			_, err := client.UpdateLogForwarder(
				context.Background(),
				"response-check",
				logupdate.LogForwarderUpdate{},
			)
			var protocolErr *logupdate.Error
			if !errors.As(err, &protocolErr) || protocolErr.Kind != logupdate.KindProtocol {
				t.Fatalf("error = %#v, want KindProtocol", err)
			}
			rendered := err.Error()
			if strings.Contains(rendered, token) {
				t.Fatalf("protocol error exposed token: %q", rendered)
			}
			trimmedBody := strings.TrimSpace(string(test.body))
			if trimmedBody != "" && len(trimmedBody) < 64 && strings.Contains(rendered, trimmedBody) {
				t.Fatalf("protocol error exposed response body: %q", rendered)
			}
			records, readErr := server.ReadLog()
			if readErr != nil {
				t.Fatal(readErr)
			}
			if len(records) != 1 {
				t.Fatalf("response validation retried: %d requests", len(records))
			}
		})
	}
}

func TestValidationHappensBeforeTraffic(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name string
		cfg  logupdate.Config
	}{
		{name: "missing origin", cfg: logupdate.Config{Token: "token", MaxAttempts: 1}},
		{name: "non HTTP scheme", cfg: logupdate.Config{BaseURL: "ftp://example.com", Token: "token", MaxAttempts: 1}},
		{name: "credentials", cfg: logupdate.Config{BaseURL: "https://user@example.com", Token: "token", MaxAttempts: 1}},
		{name: "path", cfg: logupdate.Config{BaseURL: "https://example.com/base", Token: "token", MaxAttempts: 1}},
		{name: "query", cfg: logupdate.Config{BaseURL: "https://example.com?x=1", Token: "token", MaxAttempts: 1}},
		{name: "forced query", cfg: logupdate.Config{BaseURL: "https://example.com?", Token: "token", MaxAttempts: 1}},
		{name: "fragment", cfg: logupdate.Config{BaseURL: "https://example.com/#frag", Token: "token", MaxAttempts: 1}},
		{name: "blank token", cfg: logupdate.Config{BaseURL: "https://example.com", Token: " \t", MaxAttempts: 1}},
		{name: "token whitespace", cfg: logupdate.Config{BaseURL: "https://example.com", Token: " token", MaxAttempts: 1}},
		{name: "token newline", cfg: logupdate.Config{BaseURL: "https://example.com", Token: "a\nb", MaxAttempts: 1}},
		{name: "zero attempts", cfg: logupdate.Config{BaseURL: "https://example.com", Token: "token"}},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			transport := &countingTransport{}
			cfg := test.cfg
			cfg.HTTPClient = &http.Client{Transport: transport}
			if _, err := logupdate.NewClient(cfg); err == nil {
				t.Fatal("NewClient unexpectedly succeeded")
			}
			if calls := transport.calls.Load(); calls != 0 {
				t.Fatalf("NewClient made %d request(s) during validation", calls)
			}
		})
	}

	token := dynamicToken(t)
	server := startMock(t, contractmock.Options{ExpectedToken: token})
	client := newClient(t, server, token, 1)
	invalidCalls := []struct {
		name   string
		ctx    context.Context
		id     string
		update logupdate.LogForwarderUpdate
	}{
		{name: "nil context", ctx: nil, id: "id"},
		{name: "empty id", ctx: context.Background(), id: ""},
		{
			name: "invalid constraints JSON",
			ctx:  context.Background(),
			id:   "id",
			update: logupdate.LogForwarderUpdate{
				Constraints: pointer(json.RawMessage(`{`)),
			},
		},
	}
	for _, call := range invalidCalls {
		if _, err := client.UpdateLogForwarder(call.ctx, call.id, call.update); err == nil {
			t.Errorf("%s unexpectedly succeeded", call.name)
		}
	}
	records, err := server.ReadLog()
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 0 {
		t.Fatalf("validation made %d request(s)", len(records))
	}
}

func TestContextCancellationIsDiscoverable(t *testing.T) {
	t.Parallel()
	token := dynamicToken(t)
	server := startMock(t, contractmock.Options{ExpectedToken: token})
	client := newClient(t, server, token, 2)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	_, err := client.UpdateLogForwarder(ctx, "cancelled", logupdate.LogForwarderUpdate{})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("errors.Is(%v, context.Canceled) = false", err)
	}
	records, readErr := server.ReadLog()
	if readErr != nil {
		t.Fatal(readErr)
	}
	if len(records) != 0 {
		t.Fatalf("cancelled call made %d request(s)", len(records))
	}
}

func TestMockRejectsOperationsOutsideFocusedContract(t *testing.T) {
	t.Parallel()
	token := dynamicToken(t)
	server := startMock(t, contractmock.Options{ExpectedToken: token})
	request, err := http.NewRequest(http.MethodGet, server.URL()+"/api/v2/logs/forwarders", nil)
	if err != nil {
		t.Fatal(err)
	}
	response, err := server.Client().Do(request)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = io.Copy(io.Discard, response.Body)
	_ = response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("unnamed operation status = %d, want 404", response.StatusCode)
	}
	records, readErr := server.ReadLog()
	if readErr != nil {
		t.Fatal(readErr)
	}
	if len(records) != 0 {
		t.Fatalf("unnamed operation entered focused log: %#v", records)
	}
}

func TestOfficialSourceAndContractProvenance(t *testing.T) {
	t.Parallel()
	type officialSources struct {
		Repository   string   `json:"repository"`
		License      string   `json:"license"`
		CommitSHA    string   `json:"commit_sha"`
		SpecBlobSHA  string   `json:"spec_blob_sha"`
		SpecPath     string   `json:"spec_path"`
		SourceURL    string   `json:"source_url"`
		OperationIDs []string `json:"operationIds"`
	}
	raw, err := os.ReadFile(filepath.Join("..", "docs", "official_sources.json"))
	if err != nil {
		t.Fatal(err)
	}
	var sources officialSources
	if err := json.Unmarshal(raw, &sources); err != nil {
		t.Fatal(err)
	}
	if sources.Repository != "https://github.com/vmware/vcf-api-specs" ||
		sources.License != "Apache-2.0" ||
		sources.CommitSHA != commitSHA ||
		sources.SpecBlobSHA != "4ada16fa39ec345674de4126174de94ea70d23a0" ||
		sources.SpecPath != specPath ||
		!strings.Contains(sources.SourceURL, commitSHA+"/"+specPath) ||
		!reflect.DeepEqual(sources.OperationIDs, []string{operationID}) {
		t.Fatalf("official source metadata changed: %#v", sources)
	}

	contractRaw, err := os.ReadFile(filepath.Join("..", "docs", "contract.json"))
	if err != nil {
		t.Fatal(err)
	}
	var contract struct {
		OpenAPI string `json:"openapi"`
		Source  struct {
			CommitSHA string `json:"commit_sha"`
			SpecPath  string `json:"spec_path"`
		} `json:"x-official-source"`
		Paths map[string]map[string]struct {
			OperationID string `json:"operationId"`
		} `json:"paths"`
	}
	if err := json.Unmarshal(contractRaw, &contract); err != nil {
		t.Fatal(err)
	}
	if contract.OpenAPI != "3.0.1" ||
		contract.Source.CommitSHA != commitSHA ||
		contract.Source.SpecPath != specPath {
		t.Fatalf("contract provenance changed: %#v", contract)
	}
	pathItem := contract.Paths["/api/v2/logs/forwarders/{id}"]
	if len(contract.Paths) != 1 || len(pathItem) != 1 ||
		pathItem["put"].OperationID != operationID {
		t.Fatalf("contract operation set changed: %#v", contract.Paths)
	}
}

func assertOneHeader(t *testing.T, requestIndex int, header http.Header, name, want string) {
	t.Helper()
	values := header.Values(name)
	if !reflect.DeepEqual(values, []string{want}) {
		t.Errorf("request %d %s = %#v, want exactly [%q]", requestIndex, name, values, want)
	}
}

type countingTransport struct {
	calls atomic.Int32
}

func (transport *countingTransport) RoundTrip(_ *http.Request) (*http.Response, error) {
	transport.calls.Add(1)
	return nil, errors.New("unexpected request")
}
