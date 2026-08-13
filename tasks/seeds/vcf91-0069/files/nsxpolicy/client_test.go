package nsxpolicy_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"vcf91-0069/internal/contractmock"
	nsxpolicy "vcf91-0069/nsxpolicy"
)

const (
	contractPath = "../docs/contract.json"
	sourcePath   = "../docs/official_sources.json"
	specCommit   = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	specBlobSHA  = "102d15fd342f6a45bb6d84a5b39a916c65929f4c"
	specPath     = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
)

func TestPinnedOfficialContract(t *testing.T) {
	t.Parallel()
	contract, err := contractmock.LoadContract(contractPath)
	if err != nil {
		t.Fatalf("LoadContract: %v", err)
	}
	if contract.Source.Repository != "https://github.com/vmware/vcf-api-specs" ||
		contract.Source.RepositoryCommitSHA != specCommit ||
		contract.Source.SpecPath != specPath ||
		contract.Source.SpecBlobSHA != specBlobSHA ||
		contract.Source.License != "Apache-2.0" {
		t.Fatalf("unexpected source provenance: %+v", contract.Source)
	}
	if contract.Info.Title != "NSX Policy API" || contract.Info.Version != "9.1.0.0" ||
		contract.BasePath != "/policy/api/v1" {
		t.Fatalf("unexpected specification identity: %+v basePath=%q", contract.Info, contract.BasePath)
	}
	if got := contract.SecurityDefinitions["BasicAuth"].Type; got != "basic" {
		t.Fatalf("BasicAuth type = %q, want basic", got)
	}
	if len(contract.Operations) != 2 {
		t.Fatalf("got %d operations, want exactly 2", len(contract.Operations))
	}
	wantOperations := []struct {
		id, method, path, response string
	}{
		{contractmock.TagBulkUpdate, http.MethodPut, "/infra/tags/tag-operations/{operation-id}", "200"},
		{contractmock.GetTagBulkOperationStatus, http.MethodGet, "/infra/tags/tag-operations/{operation-id}/status", "200"},
	}
	for i, want := range wantOperations {
		operation := contract.Operations[i]
		if operation.OperationID != want.id || operation.Method != want.method || operation.Path != want.path {
			t.Fatalf("operation %d = %+v, want %q %q %q", i, operation, want.id, want.method, want.path)
		}
		if _, ok := operation.Responses[want.response]; !ok {
			t.Fatalf("operation %q lacks documented 200 response", operation.OperationID)
		}
	}
	if contract.PollingRule.SubmissionOperationID != contractmock.TagBulkUpdate ||
		contract.PollingRule.PollOperationID != contractmock.GetTagBulkOperationStatus ||
		contract.PollingRule.AcceptedIsTerminal || contract.PollingRule.MinimumStatusPolls != 1 ||
		!reflect.DeepEqual(contract.PollingRule.NonterminalStatuses, []string{"Pending", "Running"}) ||
		!reflect.DeepEqual(contract.PollingRule.SuccessfulStatuses, []string{"Success"}) ||
		!reflect.DeepEqual(contract.PollingRule.FailedStatuses, []string{"Error"}) {
		t.Fatalf("unexpected polling rule: %+v", contract.PollingRule)
	}

	data, err := os.ReadFile(sourcePath)
	if err != nil {
		t.Fatal(err)
	}
	var sources struct {
		CommitSHA    string   `json:"commit_sha"`
		SpecPath     string   `json:"spec_path"`
		SpecBlobSHA  string   `json:"spec_blob_sha"`
		OperationIDs []string `json:"operationIds"`
		Operations   []struct {
			OperationID    string `json:"operationId"`
			SourceSpecPath string `json:"source_spec_path"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(data, &sources); err != nil {
		t.Fatal(err)
	}
	wantIDs := []string{contractmock.TagBulkUpdate, contractmock.GetTagBulkOperationStatus}
	if sources.CommitSHA != specCommit || sources.SpecPath != specPath ||
		sources.SpecBlobSHA != specBlobSHA || !reflect.DeepEqual(sources.OperationIDs, wantIDs) ||
		len(sources.Operations) != len(wantIDs) {
		t.Fatalf("official_sources.json does not pin the contract: %+v", sources)
	}
	for i, operation := range sources.Operations {
		if operation.OperationID != wantIDs[i] || operation.SourceSpecPath != specPath {
			t.Fatalf("official source operation %d = %+v", i, operation)
		}
	}
}

func TestApplyTagAndWaitWireAndTerminalPolling(t *testing.T) {
	t.Parallel()
	scope := "env/prod"
	tests := []struct {
		name        string
		operationID string
		request     nsxpolicy.BulkTagRequest
		statuses    []string
		wantBody    string
		wantPutPath string
		wantPolls   int
	}{
		{
			name:        "unset scope and remove_from are omitted",
			operationID: "batch +/=?",
			request: nsxpolicy.BulkTagRequest{
				Tag: nsxpolicy.Tag{Tag: "prod&blue"},
				ApplyTo: []nsxpolicy.ResourceInfo{{
					ResourceType: "VirtualMachine",
					ResourceIDs:  []string{"vm-2", "vm-1"},
				}},
			},
			statuses:    []string{"Pending", "Running", "Success"},
			wantBody:    `{"tag":{"tag":"prod&blue"},"apply_to":[{"resource_type":"VirtualMachine","resource_ids":["vm-2","vm-1"]}]}`,
			wantPutPath: "/policy/api/v1/infra/tags/tag-operations/batch%20+%2F=%3F",
			wantPolls:   3,
		},
		{
			name:        "unset apply_to is omitted and explicit scope is present",
			operationID: "retire-9",
			request: nsxpolicy.BulkTagRequest{
				Tag: nsxpolicy.Tag{Scope: &scope, Tag: "legacy"},
				RemoveFrom: []nsxpolicy.ResourceInfo{{
					ResourceType: "VirtualMachine",
					ResourceIDs:  []string{"vm-9"},
				}},
			},
			statuses:    []string{"Success"},
			wantBody:    `{"tag":{"scope":"env/prod","tag":"legacy"},"remove_from":[{"resource_type":"VirtualMachine","resource_ids":["vm-9"]}]}`,
			wantPutPath: "/policy/api/v1/infra/tags/tag-operations/retire-9",
			wantPolls:   1,
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server := contractmock.New(t, contractPath, contractmock.Script{Statuses: test.statuses})
			client := newClient(t, server, test.wantPolls+2)

			result, err := client.ApplyTagAndWait(context.Background(), test.operationID, test.request)
			if err != nil {
				t.Fatalf("ApplyTagAndWait: %v", err)
			}
			if result.OperationID != test.operationID || result.Status != nsxpolicy.StatusSuccess ||
				result.Polls != test.wantPolls || result.Terminal.Status != nsxpolicy.StatusSuccess ||
				result.Path != "/infra/tags/tag-operations/"+test.operationID {
				t.Fatalf("unexpected result: %+v", result)
			}

			requests := server.Requests()
			if len(requests) != 1+test.wantPolls {
				t.Fatalf("got %d requests, want %d: %+v", len(requests), 1+test.wantPolls, requests)
			}
			for i, request := range requests {
				wantOperation := contractmock.GetTagBulkOperationStatus
				wantMethod := http.MethodGet
				wantPath := test.wantPutPath + "/status"
				if i == 0 {
					wantOperation = contractmock.TagBulkUpdate
					wantMethod = http.MethodPut
					wantPath = test.wantPutPath
				}
				if request.OperationID != wantOperation || request.Method != wantMethod ||
					request.EscapedPath != wantPath || request.RequestURI != wantPath || request.RawQuery != "" {
					t.Errorf("request %d route = %+v, want %s %s", i, request, wantMethod, wantPath)
				}
				if got := request.Header.Values("Authorization"); !reflect.DeepEqual(got, []string{"Basic YWRtaW46c2VjcmV0"}) {
					t.Errorf("request %d Authorization = %#v", i, got)
				}
				if got := request.Header.Values("Accept"); !reflect.DeepEqual(got, []string{"application/json"}) {
					t.Errorf("request %d Accept = %#v", i, got)
				}
				if i == 0 {
					if got := request.Header.Values("Content-Type"); !reflect.DeepEqual(got, []string{"application/json"}) {
						t.Errorf("PUT Content-Type = %#v", got)
					}
					if got := string(request.Body); got != test.wantBody {
						t.Errorf("PUT body:\n got: %s\nwant: %s", got, test.wantBody)
					}
				} else {
					if got := request.Header.Values("Content-Type"); len(got) != 0 {
						t.Errorf("GET %d Content-Type = %#v, want absent", i, got)
					}
					if len(request.Body) != 0 {
						t.Errorf("GET %d body = %q, want empty", i, request.Body)
					}
				}
			}
		})
	}
}

func TestTerminalFailureTimeoutAndUnknownStatus(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name       string
		statuses   []string
		maxPolls   int
		wantPolls  int
		checkError func(*testing.T, error)
	}{
		{
			name:      "Error is terminal and retains details",
			statuses:  []string{"Running", "Error", "Success"},
			maxPolls:  5,
			wantPolls: 2,
			checkError: func(t *testing.T, err error) {
				var failed *nsxpolicy.OperationFailedError
				if !errors.As(err, &failed) {
					t.Fatalf("error = %v, want *OperationFailedError", err)
				}
				if failed.Polls != 2 || failed.Final.Status != nsxpolicy.StatusError ||
					len(failed.Final.RemoveFrom) != 1 ||
					failed.Final.RemoveFrom[0].ResourceTagStatus[0].ResourceID != "vm-missing" {
					t.Fatalf("failure did not retain terminal document: %+v", failed)
				}
			},
		},
		{
			name:      "poll budget is exact",
			statuses:  []string{"Pending", "Running", "Success"},
			maxPolls:  2,
			wantPolls: 2,
			checkError: func(t *testing.T, err error) {
				var timeout *nsxpolicy.PollTimeoutError
				if !errors.As(err, &timeout) {
					t.Fatalf("error = %v, want *PollTimeoutError", err)
				}
				if timeout.MaxPolls != 2 || timeout.Last.Status != nsxpolicy.StatusRunning {
					t.Fatalf("unexpected timeout: %+v", timeout)
				}
			},
		},
		{
			name:      "unknown status is a protocol error",
			statuses:  []string{"Queued", "Success"},
			maxPolls:  5,
			wantPolls: 1,
			checkError: func(t *testing.T, err error) {
				var protocol *nsxpolicy.ProtocolError
				if !errors.As(err, &protocol) || protocol.OperationID != nsxpolicy.OperationGetTagBulkOperationStatus {
					t.Fatalf("error = %v, want status ProtocolError", err)
				}
			},
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server := contractmock.New(t, contractPath, contractmock.Script{
				Statuses:          test.statuses,
				TerminalErrorInfo: true,
			})
			client := newClient(t, server, test.maxPolls)
			_, err := client.ApplyTagAndWait(context.Background(), "terminal-case", validRequest())
			if err == nil {
				t.Fatal("ApplyTagAndWait returned nil error")
			}
			test.checkError(t, err)
			if got := len(server.Requests()); got != 1+test.wantPolls {
				t.Fatalf("got %d requests, want exactly %d", got, 1+test.wantPolls)
			}
		})
	}
}

func TestHTTPFailuresRetainNamedOperationAndStop(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name          string
		script        contractmock.Script
		wantOperation string
		wantRequests  int
	}{
		{
			name:          "PUT failure",
			script:        contractmock.Script{PutHTTPStatus: http.StatusServiceUnavailable},
			wantOperation: nsxpolicy.OperationTagBulkUpdate,
			wantRequests:  1,
		},
		{
			name:          "status failure",
			script:        contractmock.Script{Statuses: []string{"Success"}, PollHTTPStatus: http.StatusServiceUnavailable},
			wantOperation: nsxpolicy.OperationGetTagBulkOperationStatus,
			wantRequests:  2,
		},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server := contractmock.New(t, contractPath, test.script)
			client := newClient(t, server, 3)
			_, err := client.ApplyTagAndWait(context.Background(), "http-failure", validRequest())
			var apiError *nsxpolicy.APIError
			if !errors.As(err, &apiError) {
				t.Fatalf("error = %v, want *APIError", err)
			}
			if apiError.OperationID != test.wantOperation || apiError.StatusCode != http.StatusServiceUnavailable ||
				apiError.ErrorCode != 9001 || apiError.ErrorMessage != "fixture failure" ||
				apiError.ModuleName != "Policy" || apiError.Details != "contract mock" || apiError.Envelope == nil {
				t.Fatalf("unexpected APIError: %+v", apiError)
			}
			if got := len(server.Requests()); got != test.wantRequests {
				t.Fatalf("got %d requests, want %d", got, test.wantRequests)
			}
		})
	}
}

func TestValidationOccursBeforeTraffic(t *testing.T) {
	t.Parallel()
	empty := ""
	tooLongScope := strings.Repeat("x", 129)
	tests := []struct {
		name        string
		ctx         context.Context
		operationID string
		request     nsxpolicy.BulkTagRequest
	}{
		{name: "nil context", ctx: nil, operationID: "op", request: validRequest()},
		{name: "blank operation id", ctx: context.Background(), operationID: " ", request: validRequest()},
		{name: "surrounding operation whitespace", ctx: context.Background(), operationID: " op ", request: validRequest()},
		{name: "blank tag", ctx: context.Background(), operationID: "op", request: nsxpolicy.BulkTagRequest{Tag: nsxpolicy.Tag{Tag: " "}, ApplyTo: validRequest().ApplyTo}},
		{name: "explicit empty scope", ctx: context.Background(), operationID: "op", request: nsxpolicy.BulkTagRequest{Tag: nsxpolicy.Tag{Scope: &empty, Tag: "tag"}, ApplyTo: validRequest().ApplyTo}},
		{name: "scope above contract maximum", ctx: context.Background(), operationID: "op", request: nsxpolicy.BulkTagRequest{Tag: nsxpolicy.Tag{Scope: &tooLongScope, Tag: "tag"}, ApplyTo: validRequest().ApplyTo}},
		{name: "neither direction set", ctx: context.Background(), operationID: "op", request: nsxpolicy.BulkTagRequest{Tag: nsxpolicy.Tag{Tag: "tag"}}},
		{name: "explicit empty apply_to", ctx: context.Background(), operationID: "op", request: nsxpolicy.BulkTagRequest{Tag: nsxpolicy.Tag{Tag: "tag"}, ApplyTo: []nsxpolicy.ResourceInfo{}}},
		{name: "unsupported resource type", ctx: context.Background(), operationID: "op", request: nsxpolicy.BulkTagRequest{Tag: nsxpolicy.Tag{Tag: "tag"}, ApplyTo: []nsxpolicy.ResourceInfo{{ResourceType: "Group", ResourceIDs: []string{"id"}}}}},
		{name: "empty resource ids", ctx: context.Background(), operationID: "op", request: nsxpolicy.BulkTagRequest{Tag: nsxpolicy.Tag{Tag: "tag"}, ApplyTo: []nsxpolicy.ResourceInfo{{ResourceType: "VirtualMachine", ResourceIDs: []string{}}}}},
		{name: "blank resource id", ctx: context.Background(), operationID: "op", request: nsxpolicy.BulkTagRequest{Tag: nsxpolicy.Tag{Tag: "tag"}, ApplyTo: []nsxpolicy.ResourceInfo{{ResourceType: "VirtualMachine", ResourceIDs: []string{" "}}}}},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server := contractmock.New(t, contractPath, contractmock.Script{})
			client := newClient(t, server, 3)
			if _, err := client.ApplyTagAndWait(test.ctx, test.operationID, test.request); err == nil {
				t.Fatal("ApplyTagAndWait returned nil error")
			}
			if got := len(server.Requests()); got != 0 {
				t.Fatalf("validation made %d requests, want 0", got)
			}
		})
	}
}

func TestContextCancellationStopsBetweenPolls(t *testing.T) {
	t.Parallel()
	server := contractmock.New(t, contractPath, contractmock.Script{Statuses: []string{"Running", "Success"}})
	client, err := nsxpolicy.NewClient(nsxpolicy.Config{
		BaseURL: server.URL, Username: "admin", Password: "secret", HTTPClient: server.Client(),
		PollInterval: time.Hour, MaxPolls: 3,
	})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	result := make(chan error, 1)
	go func() {
		_, err := client.ApplyTagAndWait(ctx, "cancelled", validRequest())
		result <- err
	}()
	deadline := time.After(2 * time.Second)
	for len(server.Requests()) < 2 {
		select {
		case <-deadline:
			t.Fatal("first status poll did not arrive")
		default:
			time.Sleep(time.Millisecond)
		}
	}
	cancel()
	select {
	case err := <-result:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("error = %v, want context.Canceled", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("cancellation did not interrupt polling wait")
	}
	if got := len(server.Requests()); got != 2 {
		t.Fatalf("got %d requests after cancellation, want 2", got)
	}
}

func TestContractMockRejectsUnlistedOperation(t *testing.T) {
	t.Parallel()
	server := contractmock.New(t, contractPath, contractmock.Script{})
	response, err := server.Client().Get(server.URL + "/policy/api/v1/infra/segments")
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", response.StatusCode)
	}
	requests := server.Requests()
	if len(requests) != 1 || requests[0].OperationID != "" {
		t.Fatalf("unexpected request log: %+v", requests)
	}
}

func validRequest() nsxpolicy.BulkTagRequest {
	return nsxpolicy.BulkTagRequest{
		Tag: nsxpolicy.Tag{Tag: "production"},
		ApplyTo: []nsxpolicy.ResourceInfo{{
			ResourceType: "VirtualMachine",
			ResourceIDs:  []string{"vm-1"},
		}},
	}
}

func newClient(t testing.TB, server *contractmock.Server, maxPolls int) *nsxpolicy.Client {
	t.Helper()
	client, err := nsxpolicy.NewClient(nsxpolicy.Config{
		BaseURL: server.URL, Username: "admin", Password: "secret", HTTPClient: server.Client(),
		PollInterval: 0, MaxPolls: maxPolls,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}
