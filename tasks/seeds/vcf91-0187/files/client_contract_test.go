package vcfopslogs_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	vcfopslogs "example.com/vcfopslogs"
	"example.com/vcfopslogs/internal/mockvcf"
)

func pointer[T any](value T) *T { return &value }

func TestPinnedContractAndOfficialSources(t *testing.T) {
	t.Parallel()

	var contract struct {
		OpenAPI string `json:"openapi"`
		Info    struct {
			Version string `json:"version"`
		} `json:"info"`
		Paths map[string]map[string]struct {
			OperationID string `json:"operationId"`
		} `json:"paths"`
		Components struct {
			Schemas map[string]struct {
				Required   []string                  `json:"required"`
				Properties map[string]map[string]any `json:"properties"`
			} `json:"schemas"`
			SecuritySchemes map[string]struct {
				In   string `json:"in"`
				Name string `json:"name"`
				Type string `json:"type"`
			} `json:"securitySchemes"`
		} `json:"components"`
	}
	readJSONFile(t, "docs/contract.json", &contract)
	if contract.OpenAPI != "3.0.1" || contract.Info.Version != "9.1.0.0" {
		t.Fatalf("unexpected contract identity: openapi=%q version=%q", contract.OpenAPI, contract.Info.Version)
	}

	wantOperations := mockvcf.Operations()
	if len(contract.Paths) != len(wantOperations) {
		t.Fatalf("contract paths = %d, want exactly %d", len(contract.Paths), len(wantOperations))
	}
	for _, operation := range wantOperations {
		methods, ok := contract.Paths[operation.Path]
		if !ok || len(methods) != 1 {
			t.Fatalf("contract route %s methods = %#v", operation.Path, methods)
		}
		entry, ok := methods[strings.ToLower(operation.Method)]
		if !ok || entry.OperationID != operation.OperationID {
			t.Fatalf("contract route %s %s operationId = %q, want %q", operation.Method, operation.Path, entry.OperationID, operation.OperationID)
		}
	}

	authRequest := contract.Components.Schemas["AgentAuthenticationRequest"]
	if !reflect.DeepEqual(authRequest.Required, []string{"secret"}) {
		t.Fatalf("AgentAuthenticationRequest.required = %#v", authRequest.Required)
	}
	if _, ok := authRequest.Properties["ttl"]; !ok {
		t.Fatal("AgentAuthenticationRequest must retain optional ttl")
	}
	createRequest := contract.Components.Schemas["AgentSecretCreateRequest"]
	if len(createRequest.Required) != 0 {
		t.Fatalf("AgentSecretCreateRequest.required = %#v; name is optional in the source spec", createRequest.Required)
	}
	security := contract.Components.SecuritySchemes["OPSTokenAuthorization"]
	if security.In != "header" || security.Name != "X-JWT-Token" || security.Type != "apiKey" {
		t.Fatalf("unexpected security scheme: %#v", security)
	}

	var sources struct {
		Repository   string `json:"repository"`
		License      string `json:"license"`
		CommitSHA    string `json:"commit_sha"`
		SpecPath     string `json:"spec_path"`
		OperationIDs []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operationIds"`
	}
	readJSONFile(t, "docs/official_sources.json", &sources)
	if sources.Repository != "https://github.com/vmware/vcf-api-specs" ||
		sources.License != "Apache-2.0" ||
		sources.CommitSHA != "3949fc33339fc5ea1b77eadb258f1cf49aa88e26" ||
		sources.SpecPath != "specifications/vcf-operations/log-management-openapi.json" {
		t.Fatalf("unexpected official source record: %#v", sources)
	}
	gotIDs := make([]string, 0, len(sources.OperationIDs))
	for _, operation := range sources.OperationIDs {
		gotIDs = append(gotIDs, operation.OperationID+" "+operation.Method+" "+operation.Path)
	}
	wantIDs := make([]string, 0, len(wantOperations))
	for _, operation := range wantOperations {
		wantIDs = append(wantIDs, operation.OperationID+" "+operation.Method+" "+operation.Path)
	}
	sort.Strings(gotIDs)
	sort.Strings(wantIDs)
	if !reflect.DeepEqual(gotIDs, wantIDs) {
		t.Fatalf("official operation records = %#v, want %#v", gotIDs, wantIDs)
	}
}

func TestRequestWireShape(t *testing.T) {
	tests := []struct {
		name          string
		initialSecret string
		run           func(context.Context, *vcfopslogs.Client) (any, error)
		wantResponse  any
		wantOperation string
		wantPath      string
		wantBody      string
	}{
		{
			name: "create secret with name",
			run: func(ctx context.Context, client *vcfopslogs.Client) (any, error) {
				return client.CreateAgentSecret(ctx, vcfopslogs.CreateAgentSecretRequest{Name: pointer("collector-a")})
			},
			wantResponse:  vcfopslogs.AgentSecret{ID: "created-id", Name: "collector-a", Secret: "created-value", Status: "ACTIVE"},
			wantOperation: mockvcf.CreateAgentSecretOperation,
			wantPath:      mockvcf.CreateAgentSecretPath,
			wantBody:      `{"name":"collector-a"}`,
		},
		{
			name: "omit unset optional secret name",
			run: func(ctx context.Context, client *vcfopslogs.Client) (any, error) {
				return client.CreateAgentSecret(ctx, vcfopslogs.CreateAgentSecretRequest{})
			},
			wantResponse:  vcfopslogs.AgentSecret{ID: "created-id", Secret: "created-value", Status: "ACTIVE"},
			wantOperation: mockvcf.CreateAgentSecretOperation,
			wantPath:      mockvcf.CreateAgentSecretPath,
			wantBody:      `{}`,
		},
		{
			name:          "omit unset optional ttl",
			initialSecret: "agent-secret-a",
			run: func(ctx context.Context, client *vcfopslogs.Client) (any, error) {
				return client.CreateAgentSession(ctx, vcfopslogs.CreateAgentSessionOptions{})
			},
			wantResponse:  vcfopslogs.AgentSession{AccessToken: "access-token", Name: "agent", NewSecret: "rotated-agent-secret", TTL: 1_800_000},
			wantOperation: mockvcf.CreateAgentSessionOperation,
			wantPath:      mockvcf.CreateAgentSessionPath,
			wantBody:      `{"secret":"agent-secret-a"}`,
		},
		{
			name:          "preserve explicit zero ttl",
			initialSecret: "agent-secret-b",
			run: func(ctx context.Context, client *vcfopslogs.Client) (any, error) {
				return client.CreateAgentSession(ctx, vcfopslogs.CreateAgentSessionOptions{TTL: pointer(int64(0))})
			},
			wantResponse:  vcfopslogs.AgentSession{AccessToken: "access-token", Name: "agent", NewSecret: "rotated-agent-secret", TTL: 1_800_000},
			wantOperation: mockvcf.CreateAgentSessionOperation,
			wantPath:      mockvcf.CreateAgentSessionPath,
			wantBody:      `{"secret":"agent-secret-b","ttl":0}`,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			initialSecret := test.initialSecret
			if initialSecret == "" {
				initialSecret = "unused-agent-secret"
			}
			server := mockvcf.New(mockvcf.Config{
				AdminToken:         "admin-token",
				InitialAgentSecret: initialSecret,
				CreatedID:          "created-id",
				CreatedSecret:      "created-value",
				CreatedStatus:      "ACTIVE",
				ExchangeResults: []mockvcf.ExchangeResult{{
					StatusCode: http.StatusOK, AccessToken: "access-token", Name: "agent",
					NewSecret: "rotated-agent-secret", TTL: 1_800_000,
				}},
			})
			defer server.Close()

			client, err := vcfopslogs.NewClient(server.URL(), "admin-token", initialSecret, server.Client())
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			gotResponse, err := test.run(context.Background(), client)
			if err != nil {
				t.Fatalf("request: %v", err)
			}
			if !reflect.DeepEqual(gotResponse, test.wantResponse) {
				t.Fatalf("decoded response = %s, want %s", formatJSON(gotResponse), formatJSON(test.wantResponse))
			}
			requests := server.Requests()
			if len(requests) != 1 {
				t.Fatalf("request count = %d, want 1", len(requests))
			}
			request := requests[0]
			if request.OperationID != test.wantOperation || request.Method != http.MethodPost ||
				request.Path != test.wantPath || request.RawQuery != "" || request.Body == nil || string(request.Body) != test.wantBody {
				t.Fatalf("wire request = operation=%q method=%q path=%q query=%q body=%q", request.OperationID, request.Method, request.Path, request.RawQuery, request.Body)
			}
			wantHeaders := http.Header{
				"Accept":          {"application/json"},
				"Accept-Encoding": {"gzip"},
				"Content-Length":  {strconv.Itoa(len(test.wantBody))},
				"Content-Type":    {"application/json"},
				"User-Agent":      {"Go-http-client/1.1"},
				"X-Jwt-Token":     {"admin-token"},
			}
			if !reflect.DeepEqual(request.Header, wantHeaders) {
				t.Fatalf("wire headers = %#v, want %#v", request.Header, wantHeaders)
			}
			if request.Proto != "HTTP/1.1" || request.ContentLength != int64(len(test.wantBody)) || len(request.TransferEncoding) != 0 {
				t.Fatalf("wire framing = proto=%q contentLength=%d transferEncoding=%#v", request.Proto, request.ContentLength, request.TransferEncoding)
			}
			if request.Host != strings.TrimPrefix(server.URL(), "http://") {
				t.Fatalf("Host = %q, want loopback host from %q", request.Host, server.URL())
			}
		})
	}
}

func TestSessionRotationDoesNotSendWaiterWithOldSecret(t *testing.T) {
	server := mockvcf.New(mockvcf.Config{
		AdminToken:         "admin-token",
		InitialAgentSecret: "secret-generation-0",
		BlockFirstExchange: true,
		ExchangeResults: []mockvcf.ExchangeResult{
			{StatusCode: http.StatusOK, AccessToken: "token-1", Name: "agent", NewSecret: "secret-generation-1", TTL: 1_800_000},
			{StatusCode: http.StatusOK, AccessToken: "token-2", Name: "agent", NewSecret: "secret-generation-2", TTL: 60_000},
		},
	})
	defer server.Close()
	client, err := vcfopslogs.NewClient(server.URL(), "admin-token", "secret-generation-0", server.Client())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	firstResult := make(chan error, 1)
	go func() {
		_, callErr := client.CreateAgentSession(context.Background(), vcfopslogs.CreateAgentSessionOptions{})
		firstResult <- callErr
	}()
	select {
	case <-server.FirstExchangeStarted():
	case <-time.After(2 * time.Second):
		t.Fatal("first exchange never reached loopback mock")
	}

	waitContext, cancel := context.WithTimeout(context.Background(), 250*time.Millisecond)
	defer cancel()
	_, waitErr := client.CreateAgentSession(waitContext, vcfopslogs.CreateAgentSessionOptions{TTL: pointer(int64(60_000))})
	if !errors.Is(waitErr, context.DeadlineExceeded) {
		t.Fatalf("waiting exchange error = %v, want context deadline", waitErr)
	}
	if requests := server.Requests(); len(requests) != 1 {
		t.Fatalf("waiting caller sent a request with the old secret; request count = %d, want 1", len(requests))
	}

	server.ReleaseFirstExchange()
	select {
	case err := <-firstResult:
		if err != nil {
			t.Fatalf("first exchange: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("first exchange did not finish after release")
	}

	session, err := client.CreateAgentSession(context.Background(), vcfopslogs.CreateAgentSessionOptions{TTL: pointer(int64(60_000))})
	if err != nil {
		t.Fatalf("exchange after rotation: %v", err)
	}
	if session.AccessToken != "token-2" || session.NewSecret != "secret-generation-2" {
		t.Fatalf("second session = %#v", session)
	}
	requests := server.Requests()
	if len(requests) != 2 {
		t.Fatalf("request count = %d, want 2", len(requests))
	}
	wantBodies := []string{
		`{"secret":"secret-generation-0"}`,
		`{"secret":"secret-generation-1","ttl":60000}`,
	}
	for index, want := range wantBodies {
		if got := string(requests[index].Body); got != want {
			t.Fatalf("request %d body = %q, want %q", index, got, want)
		}
	}
}

func TestFailedExchangeKeepsCurrentSecret(t *testing.T) {
	server := mockvcf.New(mockvcf.Config{
		AdminToken:         "admin-token",
		InitialAgentSecret: "still-current",
		ExchangeResults: []mockvcf.ExchangeResult{
			{StatusCode: http.StatusBadRequest, ErrorCode: "AGENT_ERROR", ErrorMessage: "temporary rejection"},
			{StatusCode: http.StatusOK, AccessToken: "token", Name: "agent", NewSecret: "now-rotated", TTL: 1_800_000},
		},
	})
	defer server.Close()
	client, err := vcfopslogs.NewClient(server.URL(), "admin-token", "still-current", server.Client())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	_, err = client.CreateAgentSession(context.Background(), vcfopslogs.CreateAgentSessionOptions{})
	var apiError *vcfopslogs.APIError
	if !errors.As(err, &apiError) || apiError.StatusCode != http.StatusBadRequest ||
		apiError.ErrorCode != "AGENT_ERROR" || apiError.ErrorMessage != "temporary rejection" {
		t.Fatalf("first error = %#v (%v)", apiError, err)
	}
	if _, err := client.CreateAgentSession(context.Background(), vcfopslogs.CreateAgentSessionOptions{}); err != nil {
		t.Fatalf("retry after failed exchange: %v", err)
	}
	requests := server.Requests()
	if len(requests) != 2 || string(requests[0].Body) != `{"secret":"still-current"}` || string(requests[1].Body) != `{"secret":"still-current"}` {
		t.Fatalf("failed exchange changed the current secret: %#v", requests)
	}
}

func TestConstructorRejectsMalformedInputs(t *testing.T) {
	tests := []struct {
		name       string
		baseURL    string
		adminToken string
		secret     string
	}{
		{name: "relative URL", baseURL: "/relative", adminToken: "token", secret: "secret"},
		{name: "unsupported scheme", baseURL: "ftp://127.0.0.1", adminToken: "token", secret: "secret"},
		{name: "URL credentials", baseURL: "https://user@example.com", adminToken: "token", secret: "secret"},
		{name: "URL query", baseURL: "https://example.com?x=1", adminToken: "token", secret: "secret"},
		{name: "empty admin token", baseURL: "https://example.com", secret: "secret"},
		{name: "empty initial secret", baseURL: "https://example.com", adminToken: "token"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := vcfopslogs.NewClient(test.baseURL, test.adminToken, test.secret, nil); err == nil {
				t.Fatal("NewClient succeeded, want validation error")
			}
		})
	}
}

func TestMockRejectsOperationsOutsideContract(t *testing.T) {
	server := mockvcf.New(mockvcf.Config{AdminToken: "admin-token", InitialAgentSecret: "secret"})
	defer server.Close()
	tests := []struct {
		name       string
		method     string
		path       string
		wantStatus int
	}{
		{name: "wrong method on contract path", method: http.MethodGet, path: mockvcf.CreateAgentSecretPath, wantStatus: http.StatusMethodNotAllowed},
		{name: "operation absent from projection", method: http.MethodPost, path: "/api/v2/agent/secrets/old/revoke", wantStatus: http.StatusNotFound},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			request, err := http.NewRequest(test.method, server.URL()+test.path, nil)
			if err != nil {
				t.Fatalf("build request: %v", err)
			}
			response, err := server.Client().Do(request)
			if err != nil {
				t.Fatalf("request mock: %v", err)
			}
			defer response.Body.Close()
			if response.StatusCode != test.wantStatus {
				t.Fatalf("status = %d, want %d", response.StatusCode, test.wantStatus)
			}
		})
	}
}

func readJSONFile(t *testing.T, path string, target any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func formatJSON(value any) string {
	encoded, err := json.Marshal(value)
	if err != nil {
		return fmt.Sprintf("%#v", value)
	}
	return string(encoded)
}
