package protected_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"

	nsx "example.com/nsxchange"
	"example.com/nsxchange/internal/nsxmock"
)

const (
	sourceCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	specPath     = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
	groupOp      = "PatchGroupForDomain"
	policyOp     = "PatchSecurityPolicyForDomain"
)

func repositoryRoot(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(file), "..", ".."))
}

func TestDerivedContractAndOfficialSources(t *testing.T) {
	root := repositoryRoot(t)

	var contract struct {
		BasePath   string `json:"base_path"`
		Operations []struct {
			OperationID    string   `json:"operation_id"`
			Method         string   `json:"method"`
			Path           string   `json:"path"`
			PathParameters []string `json:"path_parameters"`
			RequestSchema  string   `json:"request_schema"`
			SuccessStatus  int      `json:"success_status"`
			ErrorStatuses  []int    `json:"error_statuses"`
		} `json:"operations"`
	}
	readJSON(t, filepath.Join(root, "docs", "contract.json"), &contract)

	if contract.BasePath != "/policy/api/v1" {
		t.Fatalf("base_path = %q", contract.BasePath)
	}
	wantOperations := []struct {
		id, method, path, schema string
		params                   []string
	}{
		{groupOp, "PATCH", "/infra/domains/{domain-id}/groups/{group-id}", "#/definitions/Group", []string{"domain-id", "group-id"}},
		{policyOp, "PATCH", "/infra/domains/{domain-id}/security-policies/{security-policy-id}", "#/definitions/SecurityPolicy", []string{"domain-id", "security-policy-id"}},
	}
	if len(contract.Operations) != len(wantOperations) {
		t.Fatalf("operation count = %d, want %d", len(contract.Operations), len(wantOperations))
	}
	wantErrors := []int{301, 307, 400, 403, 409, 412, 500, 503}
	for i, want := range wantOperations {
		got := contract.Operations[i]
		if got.OperationID != want.id || got.Method != want.method || got.Path != want.path ||
			got.RequestSchema != want.schema || got.SuccessStatus != http.StatusOK ||
			!reflect.DeepEqual(got.PathParameters, want.params) ||
			!reflect.DeepEqual(got.ErrorStatuses, wantErrors) {
			t.Fatalf("operation[%d] does not match pinned spec: %+v", i, got)
		}
	}

	var sources struct {
		Repository string `json:"repository"`
		Commit     string `json:"commit"`
		SpecPath   string `json:"spec_path"`
		License    string `json:"license"`
		Sources    []struct {
			OperationID string `json:"operation_id"`
			SpecPath    string `json:"spec_path"`
			Commit      string `json:"commit"`
		} `json:"sources"`
	}
	readJSON(t, filepath.Join(root, "docs", "official_sources.json"), &sources)
	if sources.Repository != "https://github.com/vmware/vcf-api-specs" ||
		sources.Commit != sourceCommit || sources.SpecPath != specPath || sources.License != "Apache-2.0" {
		t.Fatalf("official source metadata is not pinned: %+v", sources)
	}
	if len(sources.Sources) != 2 {
		t.Fatalf("source count = %d", len(sources.Sources))
	}
	for i, operationID := range []string{groupOp, policyOp} {
		got := sources.Sources[i]
		if got.OperationID != operationID || got.SpecPath != specPath || got.Commit != sourceCommit {
			t.Fatalf("source[%d] = %+v", i, got)
		}
	}
}

func TestApplyReportsAttemptedStepsAndWireShape(t *testing.T) {
	root := repositoryRoot(t)
	contractPath := filepath.Join(root, "docs", "contract.json")
	cases := []struct {
		name           string
		failure        *nsxmock.Failure
		wantStatuses   []nsx.StepStatus
		wantHTTP       []int
		wantOperations []string
	}{
		{
			name: "later policy conflict retains two successes",
			failure: &nsxmock.Failure{
				OperationID: policyOp,
				Path:        "/policy/api/v1/infra/domains/default/security-policies/app-to-db",
				Status:      http.StatusConflict,
				ErrorCode:   500045,
				Message:     "security policy is locked",
				ModuleName:  "Policy",
			},
			wantStatuses:   []nsx.StepStatus{nsx.StepApplied, nsx.StepApplied, nsx.StepFailed},
			wantHTTP:       []int{200, 200, 409},
			wantOperations: []string{groupOp, groupOp, policyOp},
		},
		{
			name: "second group failure stops before policy",
			failure: &nsxmock.Failure{
				OperationID: groupOp,
				Path:        "/policy/api/v1/infra/domains/default/groups/db-vms",
				Status:      http.StatusPreconditionFailed,
				ErrorCode:   500087,
				Message:     "stale revision",
				ModuleName:  "Policy",
			},
			wantStatuses:   []nsx.StepStatus{nsx.StepApplied, nsx.StepFailed},
			wantHTTP:       []int{200, 412},
			wantOperations: []string{groupOp, groupOp},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			server, err := nsxmock.New(contractPath, tc.failure)
			if err != nil {
				t.Fatal(err)
			}
			defer server.Close()

			client, err := nsx.NewClient(server.URL(), server.Client())
			if err != nil {
				t.Fatal(err)
			}
			report, err := client.Apply(context.Background(), sampleChange())
			if err == nil {
				t.Fatal("Apply error = nil")
			}
			var apiErr *nsx.APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("Apply error type = %T, want *APIError", err)
			}
			if apiErr.OperationID != tc.failure.OperationID || apiErr.StatusCode != tc.failure.Status ||
				apiErr.ErrorCode != tc.failure.ErrorCode || apiErr.Message != tc.failure.Message ||
				apiErr.ModuleName != tc.failure.ModuleName {
				t.Fatalf("APIError = %+v", apiErr)
			}

			if len(report.Steps) != len(tc.wantStatuses) {
				t.Fatalf("steps = %+v", report.Steps)
			}
			for i := range report.Steps {
				if report.Steps[i].Status != tc.wantStatuses[i] ||
					report.Steps[i].HTTPStatus != tc.wantHTTP[i] ||
					report.Steps[i].OperationID != tc.wantOperations[i] {
					t.Fatalf("step[%d] = %+v", i, report.Steps[i])
				}
			}

			requests := server.Requests()
			if len(requests) != len(tc.wantStatuses) {
				t.Fatalf("request count = %d", len(requests))
			}
			stepNames := []string{"source-group", "destination-group", "security-policy"}
			for i, request := range requests {
				if report.Steps[i].Name != stepNames[i] ||
					report.Steps[i].ResourcePath != request.RequestURI {
					t.Fatalf("step[%d] does not identify its request: step=%+v request=%+v", i, report.Steps[i], request)
				}
			}
			assertWirePrefix(t, requests)
		})
	}
}

func TestMockRejectsUnnamedRoutesAndReturnsLogCopies(t *testing.T) {
	server, err := nsxmock.New(filepath.Join(repositoryRoot(t), "docs", "contract.json"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()

	cases := []struct {
		method string
		path   string
	}{
		{http.MethodGet, "/policy/api/v1/infra/domains/default/groups/app-vms"},
		{http.MethodPatch, "/policy/api/v1/infra/segments/not-in-contract"},
		{http.MethodPost, "/policy/api/v1/infra/domains/default/security-policies/app-to-db"},
	}
	for _, tc := range cases {
		req, err := http.NewRequest(tc.method, server.URL()+tc.path, nil)
		if err != nil {
			t.Fatal(err)
		}
		resp, err := server.Client().Do(req)
		if err != nil {
			t.Fatal(err)
		}
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		if resp.StatusCode != http.StatusNotFound {
			t.Fatalf("%s %s status = %d", tc.method, tc.path, resp.StatusCode)
		}
	}

	first := server.Requests()
	if len(first) != len(cases) {
		t.Fatalf("request count = %d", len(first))
	}
	first[0].Header.Set("Mutated", "yes")
	first[0].Body = append(first[0].Body, 'x')
	second := server.Requests()
	if second[0].Header.Get("Mutated") != "" || strings.HasSuffix(string(second[0].Body), "x") {
		t.Fatal("Requests did not return a deep copy")
	}
}

func TestOptionalBooleanEncoding(t *testing.T) {
	falseValue := false
	cases := []struct {
		name  string
		value nsx.SecurityPolicy
		want  string
	}{
		{"unset omitted", nsx.SecurityPolicy{}, `{}`},
		{"explicit false retained", nsx.SecurityPolicy{Stateful: &falseValue}, `{"stateful":false}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := json.Marshal(tc.value)
			if err != nil {
				t.Fatal(err)
			}
			if string(got) != tc.want {
				t.Fatalf("JSON = %s, want %s", got, tc.want)
			}
		})
	}
}

func sampleChange() nsx.Change {
	return nsx.Change{
		DomainID: "default",
		SourceGroup: nsx.GroupChange{
			ID: "app-vms", DisplayName: "App VMs", TagScope: "tier", TagValue: "app",
		},
		DestinationGroup: nsx.GroupChange{
			ID: "db-vms", DisplayName: "DB VMs", TagScope: "tier", TagValue: "db",
		},
		Policy: nsx.PolicyChange{
			ID: "app-to-db", DisplayName: "App to DB", Category: "Application",
			RuleDisplayName: "Allow PostgreSQL", ServicePath: "/infra/services/PostgreSQL",
		},
	}
}

func assertWirePrefix(t *testing.T, requests []nsxmock.Request) {
	t.Helper()
	want := []struct {
		operationID string
		uri         string
		body        string
	}{
		{
			groupOp,
			"/policy/api/v1/infra/domains/default/groups/app-vms",
			`{"display_name":"App VMs","expression":[{"resource_type":"Condition","member_type":"VirtualMachine","key":"Tag","operator":"EQUALS","value":"tier|app"}]}`,
		},
		{
			groupOp,
			"/policy/api/v1/infra/domains/default/groups/db-vms",
			`{"display_name":"DB VMs","expression":[{"resource_type":"Condition","member_type":"VirtualMachine","key":"Tag","operator":"EQUALS","value":"tier|db"}]}`,
		},
		{
			policyOp,
			"/policy/api/v1/infra/domains/default/security-policies/app-to-db",
			`{"display_name":"App to DB","category":"Application","rules":[{"display_name":"Allow PostgreSQL","action":"ALLOW","source_groups":["/infra/domains/default/groups/app-vms"],"destination_groups":["/infra/domains/default/groups/db-vms"],"services":["/infra/services/PostgreSQL"]}]}`,
		},
	}
	if len(requests) > len(want) {
		t.Fatalf("too many requests: %d", len(requests))
	}
	for i, got := range requests {
		if got.OperationID != want[i].operationID || got.Method != http.MethodPatch ||
			got.RequestURI != want[i].uri || string(got.Body) != want[i].body {
			t.Fatalf("request[%d] = operation=%q method=%q uri=%q body=%s", i, got.OperationID, got.Method, got.RequestURI, got.Body)
		}
		if got.Header.Get("Accept") != "application/json" ||
			got.Header.Get("Content-Type") != "application/json" {
			t.Fatalf("request[%d] headers = %v", i, got.Header)
		}
		assertNoEmptyJSONValues(t, got.Body)
	}
}

func assertNoEmptyJSONValues(t *testing.T, body []byte) {
	t.Helper()
	var value any
	if err := json.Unmarshal(body, &value); err != nil {
		t.Fatal(err)
	}
	var walk func(any)
	walk = func(v any) {
		switch x := v.(type) {
		case map[string]any:
			for key, child := range x {
				if child == nil || child == "" {
					t.Fatalf("unset optional field %q was sent empty in %s", key, body)
				}
				walk(child)
			}
		case []any:
			if len(x) == 0 {
				t.Fatalf("empty optional array was sent in %s", body)
			}
			for _, child := range x {
				walk(child)
			}
		}
	}
	walk(value)
}

func readJSON(t *testing.T, path string, dst any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, dst); err != nil {
		t.Fatalf("%s: %v", path, err)
	}
}
