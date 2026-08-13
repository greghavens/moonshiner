package protected_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"vcf91-0077/internal/contractmock"
	nsxpolicy "vcf91-0077/nsxpolicy"
)

const (
	contractPath = "../../docs/contract.json"
	sourcesPath  = "../../docs/official_sources.json"
	pinnedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	pinnedBlob   = "102d15fd342f6a45bb6d84a5b39a916c65929f4c"
	pinnedSpec   = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
)

func TestOfficialSpecificationProjection(t *testing.T) {
	t.Parallel()
	contract, err := contractmock.LoadContract(contractPath)
	if err != nil {
		t.Fatalf("LoadContract: %v", err)
	}
	if contract.Source.Repository != "https://github.com/vmware/vcf-api-specs" ||
		contract.Source.RepositoryCommitSHA != pinnedCommit ||
		contract.Source.SpecBlobSHA != pinnedBlob || contract.Source.SpecPath != pinnedSpec ||
		contract.Source.License != "Apache-2.0" ||
		!strings.Contains(contract.Source.Derivation, "no rendered documentation page") {
		t.Fatalf("unexpected contract provenance: %#v", contract.Source)
	}
	if contract.SecurityDefinitions["BasicAuth"].Type != "basic" {
		t.Fatalf("contract does not retain BasicAuth: %#v", contract.SecurityDefinitions)
	}

	wants := []struct {
		id, method, path, success string
		parameterNames            []string
	}{
		{
			id: contractmock.TagBulkUpdate, method: http.MethodPut,
			path: "/infra/tags/tag-operations/{operation-id}", success: "#/definitions/TagBulkOperation",
			parameterNames: []string{"operation-id:path", "cursor:query", "enforcement_point_path:query", "include_mark_for_delete_objects:query", "included_fields:query", "page_size:query", "sort_ascending:query", "sort_by:query", "tag-bulk-operation:body"},
		},
		{
			id: contractmock.GetTagBulkOperationStatus, method: http.MethodGet,
			path: "/infra/tags/tag-operations/{operation-id}/status", success: "#/definitions/TagBulkOperationStatus",
			parameterNames: []string{"operation-id:path", "cursor:query", "enforcement_point_path:query", "include_mark_for_delete_objects:query", "included_fields:query", "page_size:query", "sort_ascending:query", "sort_by:query"},
		},
	}
	for _, want := range wants {
		op := contract.Operations[want.id]
		if op.OperationID != want.id || op.Method != want.method || op.Path != want.path ||
			op.Responses["200"].SchemaRef != want.success {
			t.Fatalf("operation %s mismatch: %#v", want.id, op)
		}
		gotParameters := make([]string, 0, len(op.Parameters))
		for _, parameter := range op.Parameters {
			gotParameters = append(gotParameters, parameter.Name+":"+parameter.In)
			if parameter.Name == "page_size" {
				if parameter.Minimum == nil || *parameter.Minimum != 0 ||
					parameter.Maximum == nil || *parameter.Maximum != 1000 ||
					parameter.Default != float64(1000) {
					t.Fatalf("page_size projection mismatch: %#v", parameter)
				}
			}
			if parameter.Name == "include_mark_for_delete_objects" && parameter.Default != false {
				t.Fatalf("include_mark_for_delete_objects default mismatch: %#v", parameter)
			}
		}
		if !reflect.DeepEqual(gotParameters, want.parameterNames) {
			t.Fatalf("%s parameters = %v, want %v", want.id, gotParameters, want.parameterNames)
		}
		wantErrors := []string{"301", "307", "400", "403", "409", "412", "500", "503"}
		gotErrors := make([]string, 0, len(op.Responses)-1)
		for status := range op.Responses {
			if status != "200" {
				gotErrors = append(gotErrors, status)
			}
		}
		sort.Strings(gotErrors)
		if !reflect.DeepEqual(gotErrors, wantErrors) {
			t.Fatalf("%s errors = %v, want %v", want.id, gotErrors, wantErrors)
		}
	}
	statusEnum := contract.Definitions["TagBulkOperationStatus"].Properties["status"].Enum
	if !reflect.DeepEqual(statusEnum, []string{"Success", "Running", "Error", "Pending"}) ||
		contract.Definitions["Tag"].Properties["scope"].MaxLength != 128 ||
		contract.Definitions["Tag"].Properties["tag"].MaxLength != 256 {
		t.Fatalf("focused definitions mismatch: statuses=%v", statusEnum)
	}
	if contract.PollingRule.SubmissionOperationID != contractmock.TagBulkUpdate ||
		contract.PollingRule.PollOperationID != contractmock.GetTagBulkOperationStatus ||
		contract.PollingRule.AcceptedIsTerminal || contract.PollingRule.MinimumStatusPolls != 1 ||
		!reflect.DeepEqual(contract.PollingRule.NonterminalStatuses, []string{"Pending", "Running"}) ||
		!reflect.DeepEqual(contract.PollingRule.SuccessfulStatuses, []string{"Success"}) ||
		!reflect.DeepEqual(contract.PollingRule.FailedStatuses, []string{"Error"}) {
		t.Fatalf("polling projection mismatch: %#v", contract.PollingRule)
	}

	var sources struct {
		Repository string   `json:"repository"`
		Commit     string   `json:"repository_commit_sha"`
		License    string   `json:"license"`
		SpecPath   string   `json:"spec_path"`
		Blob       string   `json:"spec_blob_sha"`
		SpecURL    string   `json:"spec_url"`
		Derivation string   `json:"derivation"`
		IDs        []string `json:"operationIds"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			BasePath    string `json:"base_path"`
			Path        string `json:"path"`
			Commit      string `json:"repository_commit_sha"`
			SpecPath    string `json:"spec_path"`
		} `json:"operations"`
	}
	readJSON(t, sourcesPath, &sources)
	wantIDs := []string{contractmock.TagBulkUpdate, contractmock.GetTagBulkOperationStatus}
	if sources.Repository != "https://github.com/vmware/vcf-api-specs" ||
		sources.Commit != pinnedCommit || sources.License != "Apache-2.0" ||
		sources.SpecPath != pinnedSpec || sources.Blob != pinnedBlob ||
		!strings.Contains(sources.SpecURL, pinnedCommit+"/"+pinnedSpec) ||
		!strings.Contains(sources.Derivation, "no rendered documentation page") ||
		!reflect.DeepEqual(sources.IDs, wantIDs) || len(sources.Operations) != 2 {
		t.Fatalf("official source pin mismatch: %#v", sources)
	}
	for i, operation := range sources.Operations {
		want := wants[i]
		if operation.OperationID != want.id || operation.Method != want.method ||
			operation.BasePath != "/policy/api/v1" || operation.Path != want.path ||
			operation.Commit != pinnedCommit || operation.SpecPath != pinnedSpec {
			t.Fatalf("official operation %d mismatch: %#v", i, operation)
		}
	}
}

func TestPollsToTerminalAndSortsEveryFlippedCollection(t *testing.T) {
	tests := []struct {
		name        string
		statuses    []string
		operationID string
		wantFirst   int
	}{
		{name: "nonterminal sequence", statuses: []string{"Pending", "Running", "Success"}, operationID: "batch +/=?ß", wantFirst: 3},
		{name: "accepted mutation still needs status GET", statuses: []string{"Success"}, operationID: "immediate", wantFirst: 1},
	}
	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			srv := contractmock.New(t, contractPath, contractmock.Script{Statuses: tt.statuses})
			client := newClient(t, srv, 6, 0)
			scope := "env/prod"
			request := nsxpolicy.BulkTagRequest{
				Tag: nsxpolicy.Tag{Scope: &scope, Tag: "blue"},
				ApplyTo: []nsxpolicy.ResourceInfo{{
					ResourceType: "VirtualMachine",
					ResourceIDs:  []string{"vm-shared", "vm-b"},
				}},
				RemoveFrom: []nsxpolicy.ResourceInfo{{
					ResourceType: "VirtualMachine",
					ResourceIDs:  []string{"vm-d", "vm-shared"},
				}},
			}

			first, err := client.ApplyTagAndWait(context.Background(), tt.operationID, request)
			if err != nil {
				t.Fatalf("first ApplyTagAndWait: %v", err)
			}
			second, err := client.ApplyTagAndWait(context.Background(), tt.operationID, request)
			if err != nil {
				t.Fatalf("second ApplyTagAndWait: %v", err)
			}
			wantOutcomes := successfulOutcomes()
			for iteration, result := range []nsxpolicy.Result{first, second} {
				wantPolls := 1
				if iteration == 0 {
					wantPolls = tt.wantFirst
				}
				if result.OperationID != tt.operationID ||
					result.Path != "/infra/tags/tag-operations/"+tt.operationID ||
					result.Polls != wantPolls || !reflect.DeepEqual(result.Outcomes, wantOutcomes) {
					t.Fatalf("iteration %d result is not terminal and sorted:\n got: %#v\nwant outcomes: %#v", iteration, result, wantOutcomes)
				}
			}

			log := srv.Requests()
			wantCount := 2 + tt.wantFirst + 1
			if len(log) != wantCount {
				t.Fatalf("request log has %d entries, want %d: %#v", len(log), wantCount, log)
			}
			putPath := "/policy/api/v1/infra/tags/tag-operations/" + escapeOperationID(tt.operationID)
			statusPath := putPath + "/status"
			putIndexes := map[int]bool{0: true, tt.wantFirst + 1: true}
			for i, entry := range log {
				if putIndexes[i] {
					assertWire(t, i, entry, nsxpolicy.OperationTagBulkUpdate, http.MethodPut, putPath)
					wantBody := `{"tag":{"scope":"env/prod","tag":"blue"},"apply_to":[{"resource_type":"VirtualMachine","resource_ids":["vm-shared","vm-b"]}],"remove_from":[{"resource_type":"VirtualMachine","resource_ids":["vm-d","vm-shared"]}]}`
					if string(entry.Body) != wantBody {
						t.Fatalf("PUT %d body = %s, want %s", i, entry.Body, wantBody)
					}
					if got := entry.Header.Values("Content-Type"); !reflect.DeepEqual(got, []string{"application/json"}) {
						t.Fatalf("PUT %d Content-Type = %#v", i, got)
					}
				} else {
					assertWire(t, i, entry, nsxpolicy.OperationGetTagBulkOperationStatus, http.MethodGet, statusPath)
					if len(entry.Body) != 0 || entry.ContentLength > 0 || len(entry.TransferEncoding) != 0 {
						t.Fatalf("GET %d carried a body: %#v", i, entry)
					}
					if got := entry.Header.Values("Content-Type"); len(got) != 0 {
						t.Fatalf("GET %d Content-Type = %#v, want absent", i, got)
					}
				}
			}
		})
	}
}

func TestTerminalFailureTimeoutAndUnknownStatus(t *testing.T) {
	tests := []struct {
		name         string
		statuses     []string
		maxPolls     int
		wantRequests int
		check        func(*testing.T, error)
	}{
		{
			name: "Error is terminal", statuses: []string{"Running", "Error", "Success"}, maxPolls: 5, wantRequests: 3,
			check: func(t *testing.T, err error) {
				var failed *nsxpolicy.OperationFailedError
				if !errors.As(err, &failed) || failed.Polls != 2 || failed.Final.Status != nsxpolicy.StatusError {
					t.Fatalf("error = %#v, want retained terminal failure", err)
				}
				want := successfulOutcomes()
				want[2].TagStatus = "Error"
				want[2].Details = "resource was not found"
				if !reflect.DeepEqual(failed.Outcomes, want) {
					t.Fatalf("failure outcomes are not sorted/retained:\n got: %#v\nwant: %#v", failed.Outcomes, want)
				}
			},
		},
		{
			name: "poll budget is exact", statuses: []string{"Pending", "Running", "Success"}, maxPolls: 2, wantRequests: 3,
			check: func(t *testing.T, err error) {
				var timeout *nsxpolicy.PollTimeoutError
				if !errors.As(err, &timeout) || timeout.MaxPolls != 2 || timeout.Last.Status != nsxpolicy.StatusRunning {
					t.Fatalf("error = %#v, want exact timeout", err)
				}
			},
		},
		{
			name: "unknown status fails closed", statuses: []string{"Queued", "Success"}, maxPolls: 5, wantRequests: 2,
			check: func(t *testing.T, err error) {
				var protocol *nsxpolicy.ProtocolError
				if !errors.As(err, &protocol) || protocol.OperationID != nsxpolicy.OperationGetTagBulkOperationStatus {
					t.Fatalf("error = %#v, want status ProtocolError", err)
				}
			},
		},
	}
	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			srv := contractmock.New(t, contractPath, contractmock.Script{Statuses: tt.statuses})
			client := newClient(t, srv, tt.maxPolls, 0)
			_, err := client.ApplyTagAndWait(context.Background(), "terminal", validRequest())
			if err == nil {
				t.Fatal("ApplyTagAndWait unexpectedly succeeded")
			}
			tt.check(t, err)
			if got := len(srv.Requests()); got != tt.wantRequests {
				t.Fatalf("request count = %d, want %d", got, tt.wantRequests)
			}
		})
	}
}

func TestHTTPAndSuccessfulEnvelopeFailures(t *testing.T) {
	tests := []struct {
		name          string
		script        contractmock.Script
		wantOperation string
		wantRequests  int
		wantAPI       bool
	}{
		{name: "PUT HTTP failure", script: contractmock.Script{PutHTTPStatus: 503}, wantOperation: nsxpolicy.OperationTagBulkUpdate, wantRequests: 1, wantAPI: true},
		{name: "poll HTTP failure", script: contractmock.Script{Statuses: []string{"Success"}, PollHTTPStatus: 503}, wantOperation: nsxpolicy.OperationGetTagBulkOperationStatus, wantRequests: 2, wantAPI: true},
		{name: "wrong successful media type", script: contractmock.Script{Statuses: []string{"Success"}, SuccessContentType: "text/plain"}, wantOperation: nsxpolicy.OperationTagBulkUpdate, wantRequests: 1},
		{name: "trailing status object", script: contractmock.Script{Statuses: []string{"Success"}, TrailingStatusJSON: true}, wantOperation: nsxpolicy.OperationGetTagBulkOperationStatus, wantRequests: 2},
	}
	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			srv := contractmock.New(t, contractPath, tt.script)
			client := newClient(t, srv, 3, 0)
			_, err := client.ApplyTagAndWait(context.Background(), "failure", validRequest())
			if err == nil {
				t.Fatal("ApplyTagAndWait unexpectedly succeeded")
			}
			if tt.wantAPI {
				var apiError *nsxpolicy.APIError
				if !errors.As(err, &apiError) || apiError.OperationID != tt.wantOperation ||
					apiError.StatusCode != 503 || apiError.ErrorCode == nil || *apiError.ErrorCode != 9001 ||
					apiError.ErrorMessage != "fixture failure" || apiError.ModuleName != "Policy" ||
					apiError.Details != "contract mock" || apiError.Envelope == nil {
					t.Fatalf("API error mismatch: %#v", err)
				}
				if strings.Contains(err.Error(), "fixture failure") || strings.Contains(err.Error(), "contract mock") {
					t.Fatalf("error string exposed server text: %q", err)
				}
			} else {
				var protocol *nsxpolicy.ProtocolError
				if !errors.As(err, &protocol) || protocol.OperationID != tt.wantOperation {
					t.Fatalf("error = %#v, want ProtocolError for %s", err, tt.wantOperation)
				}
			}
			if got := len(srv.Requests()); got != tt.wantRequests {
				t.Fatalf("request count = %d, want %d", got, tt.wantRequests)
			}
		})
	}
}

func TestValidationOccursBeforeTraffic(t *testing.T) {
	empty := ""
	longScope := strings.Repeat("界", 129)
	longTag := strings.Repeat("é", 257)
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	tests := []struct {
		name        string
		ctx         context.Context
		operationID string
		request     nsxpolicy.BulkTagRequest
		wantIs      error
	}{
		{name: "nil context", ctx: nil, operationID: "op", request: validRequest()},
		{name: "already cancelled", ctx: cancelled, operationID: "op", request: validRequest(), wantIs: context.Canceled},
		{name: "blank operation id", ctx: context.Background(), operationID: " ", request: validRequest()},
		{name: "padded operation id", ctx: context.Background(), operationID: " op ", request: validRequest()},
		{name: "blank tag", ctx: context.Background(), operationID: "op", request: requestWithTag(" ")},
		{name: "tag over character limit", ctx: context.Background(), operationID: "op", request: requestWithTag(longTag)},
		{name: "explicit empty scope", ctx: context.Background(), operationID: "op", request: requestWithScope(&empty)},
		{name: "scope over character limit", ctx: context.Background(), operationID: "op", request: requestWithScope(&longScope)},
		{name: "neither action set", ctx: context.Background(), operationID: "op", request: nsxpolicy.BulkTagRequest{Tag: nsxpolicy.Tag{Tag: "blue"}}},
		{name: "empty apply_to", ctx: context.Background(), operationID: "op", request: nsxpolicy.BulkTagRequest{Tag: nsxpolicy.Tag{Tag: "blue"}, ApplyTo: []nsxpolicy.ResourceInfo{}}},
		{name: "empty remove_from", ctx: context.Background(), operationID: "op", request: nsxpolicy.BulkTagRequest{Tag: nsxpolicy.Tag{Tag: "blue"}, RemoveFrom: []nsxpolicy.ResourceInfo{}}},
		{name: "wrong resource type", ctx: context.Background(), operationID: "op", request: requestWithGroup(nsxpolicy.ResourceInfo{ResourceType: "Group", ResourceIDs: []string{"id"}})},
		{name: "empty resource ids", ctx: context.Background(), operationID: "op", request: requestWithGroup(nsxpolicy.ResourceInfo{ResourceType: "VirtualMachine", ResourceIDs: []string{}})},
		{name: "blank resource id", ctx: context.Background(), operationID: "op", request: requestWithGroup(nsxpolicy.ResourceInfo{ResourceType: "VirtualMachine", ResourceIDs: []string{" "}})},
	}
	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			srv := contractmock.New(t, contractPath, contractmock.Script{})
			client := newClient(t, srv, 3, 0)
			_, err := client.ApplyTagAndWait(tt.ctx, tt.operationID, tt.request)
			if err == nil {
				t.Fatal("ApplyTagAndWait unexpectedly succeeded")
			}
			if tt.wantIs != nil && !errors.Is(err, tt.wantIs) {
				t.Fatalf("error = %v, want errors.Is(_, %v)", err, tt.wantIs)
			}
			if log := srv.Requests(); len(log) != 0 {
				t.Fatalf("invalid call made requests: %#v", log)
			}
		})
	}
}

func TestNewClientValidationIsLocalAndCallerClientIsNotMutated(t *testing.T) {
	configs := []struct {
		name string
		cfg  nsxpolicy.Config
	}{
		{name: "missing URL", cfg: nsxpolicy.Config{Username: "u", Password: "p", MaxPolls: 1}},
		{name: "relative URL", cfg: nsxpolicy.Config{BaseURL: "manager.example.com", Username: "u", Password: "p", MaxPolls: 1}},
		{name: "unsupported scheme", cfg: nsxpolicy.Config{BaseURL: "ftp://manager.example.com", Username: "u", Password: "p", MaxPolls: 1}},
		{name: "userinfo", cfg: nsxpolicy.Config{BaseURL: "https://u@manager.example.com", Username: "u", Password: "p", MaxPolls: 1}},
		{name: "non-root path", cfg: nsxpolicy.Config{BaseURL: "https://manager.example.com/policy", Username: "u", Password: "p", MaxPolls: 1}},
		{name: "encoded path", cfg: nsxpolicy.Config{BaseURL: "https://manager.example.com/%2f", Username: "u", Password: "p", MaxPolls: 1}},
		{name: "query", cfg: nsxpolicy.Config{BaseURL: "https://manager.example.com?x=1", Username: "u", Password: "p", MaxPolls: 1}},
		{name: "bare query", cfg: nsxpolicy.Config{BaseURL: "https://manager.example.com?", Username: "u", Password: "p", MaxPolls: 1}},
		{name: "fragment", cfg: nsxpolicy.Config{BaseURL: "https://manager.example.com#x", Username: "u", Password: "p", MaxPolls: 1}},
		{name: "blank username", cfg: nsxpolicy.Config{BaseURL: "https://manager.example.com", Username: " ", Password: "p", MaxPolls: 1}},
		{name: "colon username", cfg: nsxpolicy.Config{BaseURL: "https://manager.example.com", Username: "u:x", Password: "p", MaxPolls: 1}},
		{name: "unsafe password", cfg: nsxpolicy.Config{BaseURL: "https://manager.example.com", Username: "u", Password: "p\nvalue", MaxPolls: 1}},
		{name: "negative interval", cfg: nsxpolicy.Config{BaseURL: "https://manager.example.com", Username: "u", Password: "p", PollInterval: -1, MaxPolls: 1}},
		{name: "zero polls", cfg: nsxpolicy.Config{BaseURL: "https://manager.example.com", Username: "u", Password: "p"}},
	}
	for _, tt := range configs {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if _, err := nsxpolicy.NewClient(tt.cfg); err == nil {
				t.Fatal("NewClient unexpectedly succeeded")
			}
		})
	}

	caller := &http.Client{Timeout: time.Second}
	client, err := nsxpolicy.NewClient(nsxpolicy.Config{
		BaseURL: "https://manager.example.com/", Username: "admin", Password: "secret",
		HTTPClient: caller, MaxPolls: 1,
	})
	if err != nil || client == nil {
		t.Fatalf("valid NewClient: client=%#v err=%v", client, err)
	}
	if caller.CheckRedirect != nil {
		t.Fatal("NewClient mutated caller-owned HTTPClient")
	}
}

func TestContextCancellationInterruptsPollingWait(t *testing.T) {
	srv := contractmock.New(t, contractPath, contractmock.Script{Statuses: []string{"Running", "Success"}})
	client := newClient(t, srv, 3, time.Hour)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, err := client.ApplyTagAndWait(ctx, "cancel", validRequest())
		done <- err
	}()
	deadline := time.After(2 * time.Second)
	for len(srv.Requests()) < 2 {
		select {
		case <-deadline:
			t.Fatal("first status poll did not arrive")
		default:
			time.Sleep(time.Millisecond)
		}
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("error = %v, want context.Canceled", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("context cancellation did not interrupt polling wait")
	}
	if got := len(srv.Requests()); got != 2 {
		t.Fatalf("request count after cancellation = %d, want 2", got)
	}
}

func TestContractMockRejectsOperationAbsentFromProjection(t *testing.T) {
	srv := contractmock.New(t, contractPath, contractmock.Script{})
	response, err := srv.Client().Get(srv.URL + "/policy/api/v1/infra/segments")
	if err != nil {
		t.Fatal(err)
	}
	_ = response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", response.StatusCode)
	}
	log := srv.Requests()
	if len(log) != 1 || log[0].OperationID != "" {
		t.Fatalf("unexpected undeclared-route log: %#v", log)
	}
}

func successfulOutcomes() []nsxpolicy.Outcome {
	return []nsxpolicy.Outcome{
		{Action: nsxpolicy.ActionApply, ResourceType: "VirtualMachine", ResourceID: "vm-b", TagStatus: "Success"},
		{Action: nsxpolicy.ActionRemove, ResourceType: "VirtualMachine", ResourceID: "vm-c", TagStatus: "Success"},
		{Action: nsxpolicy.ActionRemove, ResourceType: "VirtualMachine", ResourceID: "vm-d", TagStatus: "Success"},
		{Action: nsxpolicy.ActionApply, ResourceType: "VirtualMachine", ResourceID: "vm-e", TagStatus: "Success"},
		{Action: nsxpolicy.ActionApply, ResourceType: "VirtualMachine", ResourceID: "vm-shared", TagStatus: "Success"},
		{Action: nsxpolicy.ActionRemove, ResourceType: "VirtualMachine", ResourceID: "vm-shared", TagStatus: "Success"},
	}
}

func validRequest() nsxpolicy.BulkTagRequest {
	return nsxpolicy.BulkTagRequest{
		Tag:     nsxpolicy.Tag{Tag: "blue"},
		ApplyTo: []nsxpolicy.ResourceInfo{{ResourceType: "VirtualMachine", ResourceIDs: []string{"vm-b"}}},
	}
}

func requestWithTag(tag string) nsxpolicy.BulkTagRequest {
	request := validRequest()
	request.Tag.Tag = tag
	return request
}

func requestWithScope(scope *string) nsxpolicy.BulkTagRequest {
	request := validRequest()
	request.Tag.Scope = scope
	return request
}

func requestWithGroup(group nsxpolicy.ResourceInfo) nsxpolicy.BulkTagRequest {
	request := validRequest()
	request.ApplyTo = []nsxpolicy.ResourceInfo{group}
	return request
}

func newClient(t testing.TB, srv *contractmock.Server, maxPolls int, interval time.Duration) *nsxpolicy.Client {
	t.Helper()
	client, err := nsxpolicy.NewClient(nsxpolicy.Config{
		BaseURL: srv.URL, Username: "admin", Password: "secret", HTTPClient: srv.Client(),
		PollInterval: interval, MaxPolls: maxPolls,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func assertWire(t testing.TB, index int, entry contractmock.Request, operationID, method, target string) {
	t.Helper()
	if entry.OperationID != operationID || entry.Method != method ||
		entry.EscapedPath != target || entry.RequestURI != target || entry.RawQuery != "" {
		t.Fatalf("request %d route mismatch: %#v, want %s %s", index, entry, method, target)
	}
	if got := entry.Header.Values("Authorization"); !reflect.DeepEqual(got, []string{"Basic YWRtaW46c2VjcmV0"}) {
		t.Fatalf("request %d Authorization = %#v", index, got)
	}
	if got := entry.Header.Values("Accept"); !reflect.DeepEqual(got, []string{"application/json"}) {
		t.Fatalf("request %d Accept = %#v", index, got)
	}
}

func escapeOperationID(value string) string {
	replacer := strings.NewReplacer(" ", "%20", "/", "%2F", "?", "%3F", "ß", "%C3%9F")
	return replacer.Replace(value)
}

func readJSON(t testing.TB, path string, target any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatal(err)
	}
}
