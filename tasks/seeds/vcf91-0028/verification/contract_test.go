package depotdelete_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	depotdelete "vcf91-0028"
	"vcf91-0028/internal/contractmock"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedSpec   = "specifications/sddc-manager/sddc-manager-openapi.json"
	contractSHA256 = "579afeb5543c24d6b0c963aa6e853095de72d70488b30c3027e9acc4e37abbb6"
	sourcesSHA256  = "e5fedb68a22a9ee579eb3d12fc06e993694893df8e9195b73bfb4c39d6d455ea"
	mockSHA256     = "c71f9bfdfcf958551cbc14f0f961166f158d25b604cbf0927db41e2524044748"
)

type operationSource struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

func TestProtectedContractProvenance(t *testing.T) {
	assertFileHash(t, "docs/contract.json", contractSHA256)
	assertFileHash(t, "docs/official_sources.json", sourcesSHA256)
	assertFileHash(t, "internal/contractmock/server.go", mockSHA256)

	var contract struct {
		DerivedFrom struct {
			Commit   string `json:"repository_commit_sha"`
			SpecPath string `json:"spec_path"`
			OpenAPI  string `json:"openapi"`
			Version  string `json:"info_version"`
			License  string `json:"repository_license"`
		} `json:"derived_from"`
		Operations []struct {
			operationSource
			QueryParameters []struct {
				Name     string         `json:"name"`
				In       string         `json:"in"`
				Required bool           `json:"required"`
				Schema   map[string]any `json:"schema"`
			} `json:"query_parameters"`
			Responses map[string]struct {
				Description string `json:"description"`
				SchemaRef   string `json:"schema_ref"`
			} `json:"responses"`
		} `json:"operations"`
		Schemas struct {
			Error struct {
				ProjectedProperties map[string]any `json:"projected_properties"`
			} `json:"Error"`
		} `json:"schemas"`
	}
	readJSON(t, "docs/contract.json", &contract)

	var sources struct {
		Repository struct {
			Commit  string `json:"commit_sha"`
			License string `json:"license"`
		} `json:"repository"`
		Specification struct {
			Path    string `json:"path"`
			OpenAPI string `json:"openapi_version"`
			Version string `json:"info_version"`
		} `json:"specification"`
		Operations []struct {
			operationSource
			Commit   string `json:"repository_commit_sha"`
			SpecPath string `json:"spec_path"`
			Pointer  string `json:"json_pointer"`
		} `json:"operations"`
	}
	readJSON(t, "docs/official_sources.json", &sources)

	if contract.DerivedFrom.Commit != expectedCommit ||
		sources.Repository.Commit != expectedCommit {
		t.Fatalf("wrong repository commit: contract=%q sources=%q",
			contract.DerivedFrom.Commit, sources.Repository.Commit)
	}
	if contract.DerivedFrom.SpecPath != expectedSpec ||
		sources.Specification.Path != expectedSpec {
		t.Fatalf("wrong specification path: contract=%q sources=%q",
			contract.DerivedFrom.SpecPath, sources.Specification.Path)
	}
	if contract.DerivedFrom.OpenAPI != "3.0.1" ||
		sources.Specification.OpenAPI != "3.0.1" ||
		contract.DerivedFrom.Version != "9.1.0.0" ||
		sources.Specification.Version != "9.1.0.0" ||
		contract.DerivedFrom.License != "Apache-2.0" ||
		sources.Repository.License != "Apache-2.0" {
		t.Fatal("contract does not pin the VCF 9.1 OpenAPI and repository license")
	}

	wantOperation := operationSource{
		OperationID: "deleteDepotSettings",
		Method:      http.MethodDelete,
		Path:        "/v1/system/settings/depot",
	}
	if len(contract.Operations) != 1 ||
		contract.Operations[0].operationSource != wantOperation {
		t.Fatalf("contract operation mismatch: %#v", contract.Operations)
	}
	if len(sources.Operations) != 1 ||
		sources.Operations[0].operationSource != wantOperation ||
		sources.Operations[0].Commit != expectedCommit ||
		sources.Operations[0].SpecPath != expectedSpec ||
		sources.Operations[0].Pointer !=
			"/paths/~1v1~1system~1settings~1depot/delete" {
		t.Fatalf("official operation source mismatch: %#v", sources.Operations)
	}

	operation := contract.Operations[0]
	if len(operation.QueryParameters) != 1 {
		t.Fatalf("wrong query projection: %#v", operation.QueryParameters)
	}
	parameter := operation.QueryParameters[0]
	if parameter.Name != "depotType" ||
		parameter.In != "query" ||
		parameter.Required ||
		!reflect.DeepEqual(parameter.Schema, map[string]any{"type": "string"}) {
		t.Fatalf("wrong depotType projection: %#v", parameter)
	}
	if len(operation.Responses) != 3 ||
		operation.Responses["204"].Description != "No Content" ||
		operation.Responses["400"].SchemaRef != "#/components/schemas/Error" ||
		operation.Responses["500"].SchemaRef != "#/components/schemas/Error" {
		t.Fatalf("wrong response projection: %#v", operation.Responses)
	}
	wantErrorProperties := []string{
		"errorCode",
		"message",
		"referenceToken",
		"remediationMessage",
	}
	gotErrorProperties := make([]string, 0, len(contract.Schemas.Error.ProjectedProperties))
	for name := range contract.Schemas.Error.ProjectedProperties {
		gotErrorProperties = append(gotErrorProperties, name)
	}
	sortStrings(gotErrorProperties)
	if !reflect.DeepEqual(gotErrorProperties, wantErrorProperties) {
		t.Fatalf("wrong Error projection: %v", gotErrorProperties)
	}
}

func TestDeleteDepotSettingsRetriesWithoutDuplicateEffect(t *testing.T) {
	depotType := "VCF_DEPOT"
	tests := []struct {
		name       string
		options    depotdelete.DeleteDepotOptions
		wantTarget string
		wantQuery  string
	}{
		{
			name:       "unset optional depot type is absent",
			options:    depotdelete.DeleteDepotOptions{},
			wantTarget: "/v1/system/settings/depot",
			wantQuery:  "",
		},
		{
			name: "explicit depot type is sent once",
			options: depotdelete.DeleteDepotOptions{
				DepotType: &depotType,
			},
			wantTarget: "/v1/system/settings/depot?depotType=VCF_DEPOT",
			wantQuery:  "depotType=VCF_DEPOT",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.New(contractmock.Plan{
				FailFirstAfterApply: true,
			})
			defer server.Close()

			var retryCalls []int
			client, err := depotdelete.NewClient(depotdelete.Config{
				BaseURL:     server.URL() + "/",
				AccessToken: server.Token(),
				HTTPClient:  server.Client(),
				MaxAttempts: 2,
				BeforeRetry: func(_ context.Context, completed int) error {
					retryCalls = append(retryCalls, completed)
					return nil
				},
			})
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}

			result, err := client.DeleteDepotSettings(context.Background(), test.options)
			if err != nil {
				t.Fatalf("DeleteDepotSettings: %v", err)
			}
			if result != (depotdelete.Result{Attempts: 2, Retried: true}) {
				t.Fatalf("result = %#v", result)
			}
			if !reflect.DeepEqual(retryCalls, []int{1}) {
				t.Fatalf("BeforeRetry calls = %v", retryCalls)
			}
			if server.EffectCount() != 1 {
				t.Fatalf("effect count = %d, want 1", server.EffectCount())
			}

			requests := server.Requests()
			if len(requests) != 2 {
				t.Fatalf("request count = %d, want 2", len(requests))
			}
			wantHost := strings.TrimPrefix(server.URL(), "http://")
			for index, request := range requests {
				if request.OperationID != "deleteDepotSettings" ||
					request.Method != http.MethodDelete ||
					request.RequestURI != test.wantTarget ||
					request.Path != "/v1/system/settings/depot" ||
					request.RawQuery != test.wantQuery ||
					request.Host != wantHost {
					t.Fatalf("request %d target mismatch: %#v", index, request)
				}
				if request.Header.Get("Accept") != "application/json" ||
					request.Header.Get("Authorization") != "Bearer "+server.Token() {
					t.Fatalf("request %d required headers: %#v", index, request.Header)
				}
				if request.Header.Get("Content-Type") != "" ||
					request.ContentLength != 0 ||
					len(request.TransferEncoding) != 0 ||
					len(request.Body) != 0 {
					t.Fatalf("request %d unexpectedly has a body: %#v", index, request)
				}
			}
			if requests[0].RequestURI != requests[1].RequestURI ||
				requests[0].Method != requests[1].Method ||
				!reflect.DeepEqual(requests[0].Body, requests[1].Body) {
				t.Fatal("retry is not wire-identical to the first mutation")
			}
			if requests[0].ResponseStatus != http.StatusInternalServerError ||
				requests[1].ResponseStatus != http.StatusNoContent {
				t.Fatalf("response sequence = %d, %d",
					requests[0].ResponseStatus, requests[1].ResponseStatus)
			}
		})
	}
}

func TestFailurePolicyIsBounded(t *testing.T) {
	tests := []struct {
		name          string
		status        int
		maxAttempts   int
		wantAttempts  int
		wantCallbacks []int
	}{
		{
			name:         "bad request is terminal",
			status:       http.StatusBadRequest,
			maxAttempts:  3,
			wantAttempts: 1,
		},
		{
			name:          "documented server error exhausts bound",
			status:        http.StatusInternalServerError,
			maxAttempts:   3,
			wantAttempts:  3,
			wantCallbacks: []int{1, 2},
		},
		{
			name:         "unexpected status is terminal",
			status:       http.StatusServiceUnavailable,
			maxAttempts:  3,
			wantAttempts: 1,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.New(contractmock.Plan{RejectStatus: test.status})
			defer server.Close()

			var callbacks []int
			client, err := depotdelete.NewClient(depotdelete.Config{
				BaseURL:     server.URL(),
				AccessToken: server.Token(),
				HTTPClient:  server.Client(),
				MaxAttempts: test.maxAttempts,
				BeforeRetry: func(_ context.Context, completed int) error {
					callbacks = append(callbacks, completed)
					return nil
				},
			})
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			result, err := client.DeleteDepotSettings(
				context.Background(),
				depotdelete.DeleteDepotOptions{},
			)
			var apiError *depotdelete.APIError
			if !errors.As(err, &apiError) {
				t.Fatalf("error = %T %v, want *APIError", err, err)
			}
			if apiError.OperationID != "deleteDepotSettings" ||
				apiError.StatusCode != test.status ||
				apiError.ErrorCode == "" ||
				apiError.Message == "" ||
				apiError.RemediationMessage == "" ||
				apiError.ReferenceToken == "" {
				t.Fatalf("APIError = %#v", apiError)
			}
			if strings.Contains(err.Error(), server.Token()) ||
				strings.Contains(err.Error(), apiError.Message) {
				t.Fatalf("error text exposes sensitive response detail: %q", err)
			}
			if result.Attempts != test.wantAttempts ||
				result.Retried != (test.wantAttempts > 1) {
				t.Fatalf("result = %#v", result)
			}
			if len(server.Requests()) != test.wantAttempts {
				t.Fatalf("request count = %d, want %d",
					len(server.Requests()), test.wantAttempts)
			}
			if !reflect.DeepEqual(callbacks, test.wantCallbacks) {
				t.Fatalf("BeforeRetry calls = %v, want %v",
					callbacks, test.wantCallbacks)
			}
			if server.EffectCount() != 0 {
				t.Fatalf("rejected request changed state %d times", server.EffectCount())
			}
		})
	}
}

func TestBeforeRetryErrorStopsImmediately(t *testing.T) {
	server := contractmock.New(contractmock.Plan{
		RejectStatus: http.StatusInternalServerError,
	})
	defer server.Close()

	sentinel := errors.New("stop retry")
	client, err := depotdelete.NewClient(depotdelete.Config{
		BaseURL:     server.URL(),
		AccessToken: server.Token(),
		HTTPClient:  server.Client(),
		MaxAttempts: 4,
		BeforeRetry: func(_ context.Context, completed int) error {
			if completed != 1 {
				t.Fatalf("completed attempts = %d", completed)
			}
			return sentinel
		},
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	result, err := client.DeleteDepotSettings(
		context.Background(),
		depotdelete.DeleteDepotOptions{},
	)
	if !errors.Is(err, sentinel) {
		t.Fatalf("error = %v, want callback error", err)
	}
	if result != (depotdelete.Result{Attempts: 1, Retried: false}) {
		t.Fatalf("result = %#v", result)
	}
	if len(server.Requests()) != 1 {
		t.Fatalf("request count = %d, want 1", len(server.Requests()))
	}
}

func TestLocalValidationPerformsNoTraffic(t *testing.T) {
	var calls atomic.Int32
	httpClient := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		calls.Add(1)
		return nil, errors.New("unexpected traffic")
	})}
	valid := depotdelete.Config{
		BaseURL:     "http://127.0.0.1:8080",
		AccessToken: "runtime-token",
		HTTPClient:  httpClient,
		MaxAttempts: 2,
	}
	tests := []struct {
		name   string
		mutate func(*depotdelete.Config)
	}{
		{name: "empty URL", mutate: func(c *depotdelete.Config) { c.BaseURL = "" }},
		{name: "wrong scheme", mutate: func(c *depotdelete.Config) { c.BaseURL = "ftp://127.0.0.1" }},
		{name: "embedded credentials", mutate: func(c *depotdelete.Config) { c.BaseURL = "http://user:pass@127.0.0.1" }},
		{name: "non-root path", mutate: func(c *depotdelete.Config) { c.BaseURL = "http://127.0.0.1/api" }},
		{name: "query", mutate: func(c *depotdelete.Config) { c.BaseURL = "http://127.0.0.1?x=1" }},
		{name: "fragment", mutate: func(c *depotdelete.Config) { c.BaseURL = "http://127.0.0.1/#x" }},
		{name: "blank token", mutate: func(c *depotdelete.Config) { c.AccessToken = "" }},
		{name: "token whitespace", mutate: func(c *depotdelete.Config) { c.AccessToken = "secret token" }},
		{name: "zero attempts", mutate: func(c *depotdelete.Config) { c.MaxAttempts = 0 }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			config := valid
			test.mutate(&config)
			if _, err := depotdelete.NewClient(config); err == nil {
				t.Fatal("NewClient unexpectedly succeeded")
			}
		})
	}

	client, err := depotdelete.NewClient(valid)
	if err != nil {
		t.Fatalf("valid NewClient: %v", err)
	}
	invalidDepotTypes := []string{"", " VCF_DEPOT", "VCF_DEPOT "}
	for _, depotType := range invalidDepotTypes {
		result, err := client.DeleteDepotSettings(
			context.Background(),
			depotdelete.DeleteDepotOptions{DepotType: &depotType},
		)
		if err == nil {
			t.Fatalf("DepotType %q unexpectedly succeeded", depotType)
		}
		if result.Attempts != 0 {
			t.Fatalf("DepotType %q attempts = %d", depotType, result.Attempts)
		}
	}
	if calls.Load() != 0 {
		t.Fatalf("local validation sent %d requests", calls.Load())
	}
}

func TestContextCancellationIsPreserved(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	client, err := depotdelete.NewClient(depotdelete.Config{
		BaseURL:     "http://127.0.0.1:1",
		AccessToken: "runtime-token",
		MaxAttempts: 2,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	result, err := client.DeleteDepotSettings(ctx, depotdelete.DeleteDepotOptions{})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context.Canceled", err)
	}
	if result.Attempts != 0 {
		t.Fatalf("attempts = %d, want 0", result.Attempts)
	}
}

func TestTransportFailureIsRetriedAndRedacted(t *testing.T) {
	token := "runtime-sensitive-token"
	transportDetail := "dial failed with " + token
	var calls atomic.Int32
	var callbacks []int
	client, err := depotdelete.NewClient(depotdelete.Config{
		BaseURL:     "http://127.0.0.1:8080",
		AccessToken: token,
		HTTPClient: &http.Client{
			Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
				calls.Add(1)
				return nil, errors.New(transportDetail)
			}),
		},
		MaxAttempts: 2,
		BeforeRetry: func(_ context.Context, completed int) error {
			callbacks = append(callbacks, completed)
			return nil
		},
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	result, err := client.DeleteDepotSettings(
		context.Background(),
		depotdelete.DeleteDepotOptions{},
	)
	var transportError *depotdelete.TransportError
	if !errors.As(err, &transportError) {
		t.Fatalf("error = %T %v, want *TransportError", err, err)
	}
	if transportError.OperationID != "deleteDepotSettings" {
		t.Fatalf("TransportError = %#v", transportError)
	}
	if strings.Contains(err.Error(), token) ||
		strings.Contains(err.Error(), transportDetail) {
		t.Fatalf("transport error exposes detail: %q", err)
	}
	if result != (depotdelete.Result{Attempts: 2, Retried: true}) {
		t.Fatalf("result = %#v", result)
	}
	if calls.Load() != 2 {
		t.Fatalf("transport calls = %d, want 2", calls.Load())
	}
	if !reflect.DeepEqual(callbacks, []int{1}) {
		t.Fatalf("BeforeRetry calls = %v", callbacks)
	}
}

func TestRedirectIsNotFollowed(t *testing.T) {
	var calls atomic.Int32
	client, err := depotdelete.NewClient(depotdelete.Config{
		BaseURL:     "http://127.0.0.1:8080",
		AccessToken: "runtime-token",
		HTTPClient: &http.Client{
			Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
				if calls.Add(1) != 1 {
					t.Fatal("redirect target was contacted")
				}
				return &http.Response{
					StatusCode: http.StatusFound,
					Header: http.Header{
						"Location": []string{"http://127.0.0.1:8081/outside"},
					},
					Body:    io.NopCloser(strings.NewReader("")),
					Request: request,
				}, nil
			}),
		},
		MaxAttempts: 2,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	result, err := client.DeleteDepotSettings(
		context.Background(),
		depotdelete.DeleteDepotOptions{},
	)
	var apiError *depotdelete.APIError
	if !errors.As(err, &apiError) || apiError.StatusCode != http.StatusFound {
		t.Fatalf("error = %T %v, want 302 APIError", err, err)
	}
	if result != (depotdelete.Result{Attempts: 1, Retried: false}) {
		t.Fatalf("result = %#v", result)
	}
	if calls.Load() != 1 {
		t.Fatalf("transport calls = %d, want 1", calls.Load())
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func readJSON(t *testing.T, path string, target any) {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(content, target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func assertFileHash(t *testing.T, path, want string) {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read protected file %s: %v", path, err)
	}
	sum := sha256.Sum256(content)
	got := hex.EncodeToString(sum[:])
	if got != want {
		t.Fatalf("protected file %s hash = %s, want %s", path, got, want)
	}
}

func sortStrings(values []string) {
	for i := 1; i < len(values); i++ {
		for j := i; j > 0 && values[j] < values[j-1]; j-- {
			values[j], values[j-1] = values[j-1], values[j]
		}
	}
}
