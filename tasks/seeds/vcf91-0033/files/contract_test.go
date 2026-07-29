package hostrefresh_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"

	hr "vcf91-0033"
	"vcf91-0033/internal/contractmock"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedSpec   = "specifications/sddc-manager/sddc-manager-openapi.json"
	contractSHA256 = "351cbe655bae0043a58aa2ad2a479bbef2436cd54e40f08834b6bb8fd6f3b023"
	sourcesSHA256  = "4ce2e1b2ce3d296f6d9b4b1a1f5de4721d633ccbad4886f0e2dc633cbe9b71ca"
)

type operationSource struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

func TestProtectedContractProvenance(t *testing.T) {
	assertFileHash(t, "docs/contract.json", contractSHA256)
	assertFileHash(t, "docs/official_sources.json", sourcesSHA256)

	var contract struct {
		DerivedFrom struct {
			Commit   string `json:"repository_commit_sha"`
			SpecPath string `json:"spec_path"`
			OpenAPI  string `json:"openapi"`
			Version  string `json:"info_version"`
			License  string `json:"repository_license"`
		} `json:"derived_from"`
		Operations []operationSource          `json:"operations"`
		Schemas    map[string]json.RawMessage `json:"schemas"`
	}
	readJSON(t, "docs/contract.json", &contract)

	var sources struct {
		Repository struct {
			Commit  string `json:"commit_sha"`
			License string `json:"license"`
		} `json:"repository"`
		Specification struct {
			Path    string `json:"path"`
			Version string `json:"info_version"`
		} `json:"specification"`
		Operations []operationSource `json:"operations"`
		Derivation string            `json:"derivation"`
	}
	readJSON(t, "docs/official_sources.json", &sources)

	if contract.DerivedFrom.Commit != expectedCommit ||
		sources.Repository.Commit != expectedCommit {
		t.Fatalf(
			"wrong repository commit: contract=%q sources=%q",
			contract.DerivedFrom.Commit,
			sources.Repository.Commit,
		)
	}
	if contract.DerivedFrom.SpecPath != expectedSpec ||
		sources.Specification.Path != expectedSpec {
		t.Fatalf(
			"wrong specification path: contract=%q sources=%q",
			contract.DerivedFrom.SpecPath,
			sources.Specification.Path,
		)
	}
	if contract.DerivedFrom.OpenAPI != "3.0.1" ||
		contract.DerivedFrom.Version != "9.1.0.0" ||
		sources.Specification.Version != "9.1.0.0" ||
		contract.DerivedFrom.License != "Apache-2.0" ||
		sources.Repository.License != "Apache-2.0" {
		t.Fatalf(
			"incorrect version/license provenance: contract=%+v sources=%+v",
			contract.DerivedFrom,
			sources,
		)
	}
	if !strings.Contains(sources.Derivation, "OpenAPI specification") ||
		!strings.Contains(sources.Derivation, "No rendered documentation page") {
		t.Fatalf("source derivation is not explicit: %q", sources.Derivation)
	}

	wantOperations := []operationSource{
		{
			OperationID: "updateHosts",
			Method:      "PATCH",
			Path:        "/v1/hosts",
		},
		{
			OperationID: "getTask",
			Method:      "GET",
			Path:        "/v1/tasks/{id}",
		},
		{
			OperationID: "getHosts",
			Method:      "GET",
			Path:        "/v1/hosts",
		},
	}
	if !reflect.DeepEqual(contract.Operations, wantOperations) ||
		!reflect.DeepEqual(sources.Operations, wantOperations) {
		t.Fatalf(
			"operation provenance mismatch\ncontract: %#v\nsources: %#v\nwant: %#v",
			contract.Operations,
			sources.Operations,
			wantOperations,
		)
	}

	var updateSchema struct {
		Required   []string `json:"required"`
		Properties map[string]struct {
			Type     string `json:"type"`
			Ref      string `json:"$ref"`
			MinItems int    `json:"minItems"`
			MaxItems int    `json:"maxItems"`
		} `json:"properties"`
	}
	if err := json.Unmarshal(
		contract.Schemas["HostsUpdateSpec"],
		&updateSchema,
	); err != nil {
		t.Fatalf("decode HostsUpdateSpec: %v", err)
	}
	if !reflect.DeepEqual(updateSchema.Required, []string{"hostIds"}) ||
		updateSchema.Properties["hostIds"].Type != "array" ||
		updateSchema.Properties["hostIds"].MinItems != 1 ||
		updateSchema.Properties["hostIds"].MaxItems != 100 ||
		updateSchema.Properties["hostsRefreshSpec"].Ref !=
			"#/components/schemas/HostsRefreshSpec" {
		t.Fatalf("HostsUpdateSpec projection mismatch: %+v", updateSchema)
	}

	var refreshSchema struct {
		Required   []string `json:"required"`
		Properties map[string]struct {
			Type string `json:"type"`
		} `json:"properties"`
	}
	if err := json.Unmarshal(
		contract.Schemas["HostsRefreshSpec"],
		&refreshSchema,
	); err != nil {
		t.Fatalf("decode HostsRefreshSpec: %v", err)
	}
	if !reflect.DeepEqual(refreshSchema.Required, []string{"forceRefresh"}) ||
		refreshSchema.Properties["forceRefresh"].Type != "boolean" {
		t.Fatalf("HostsRefreshSpec projection mismatch: %+v", refreshSchema)
	}

	var taskSchema struct {
		Required   []string `json:"required"`
		Properties map[string]struct {
			ReadOnly bool   `json:"readOnly"`
			Type     string `json:"type"`
		} `json:"properties"`
	}
	if err := json.Unmarshal(contract.Schemas["Task"], &taskSchema); err != nil {
		t.Fatalf("decode Task: %v", err)
	}
	if !reflect.DeepEqual(
		taskSchema.Required,
		[]string{"creationTimestamp", "id", "name", "status"},
	) ||
		!taskSchema.Properties["id"].ReadOnly ||
		!taskSchema.Properties["status"].ReadOnly ||
		taskSchema.Properties["errors"].Type != "array" {
		t.Fatalf("Task projection mismatch: %+v", taskSchema)
	}
}

func TestRefreshPollsThenReturnsStableSortedCollection(t *testing.T) {
	server := newServer(t, validPlan)
	runtime := server.Runtime()

	var paceMu sync.Mutex
	var paceCalls []int
	client, err := hr.NewClient(hr.Config{
		BaseURL:     server.URL(),
		AccessToken: runtime.AccessToken,
		HTTPClient:  server.Client(),
		MaxPolls:    4,
		Pace: func(
			ctx context.Context,
			operationID string,
			completedPolls int,
		) error {
			if operationID != "getTask" {
				t.Errorf("Pace operationId = %q", operationID)
			}
			paceMu.Lock()
			paceCalls = append(paceCalls, completedPolls)
			paceMu.Unlock()
			return ctx.Err()
		},
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	force := true
	request := hr.RefreshRequest{
		HostIDs:      []string{runtime.HostZuluID, runtime.HostAlphaID},
		ForceRefresh: &force,
	}

	first, err := client.RefreshHosts(context.Background(), request)
	if err != nil {
		t.Fatalf("first RefreshHosts returned %T: %v", err, err)
	}
	second, err := client.RefreshHosts(context.Background(), request)
	if err != nil {
		t.Fatalf("second RefreshHosts returned %T: %v", err, err)
	}

	wantHosts := []hr.Host{
		{
			ID:     runtime.HostAlphaID,
			FQDN:   runtime.AlphaFQDN,
			Status: "UNASSIGNED_USEABLE",
		},
		{
			ID:     runtime.HostCharlieID,
			FQDN:   runtime.AlphaFQDN,
			Status: "ASSIGNED",
		},
		{
			ID:     runtime.HostZuluID,
			FQDN:   runtime.ZuluFQDN,
			Status: "UNASSIGNED_UNUSEABLE",
		},
	}
	if !reflect.DeepEqual(first.Hosts, wantHosts) ||
		!reflect.DeepEqual(second.Hosts, wantHosts) {
		t.Fatalf(
			"alternating collection order was not normalized\nfirst: %#v\nsecond: %#v\nwant: %#v",
			first.Hosts,
			second.Hosts,
			wantHosts,
		)
	}
	if first.Task.ID != runtime.TaskID ||
		first.Task.Status != " Successful " ||
		second.Task.ID != runtime.TaskID ||
		second.Task.Status != " Successful " {
		t.Fatalf("terminal Task was not preserved: first=%+v second=%+v", first.Task, second.Task)
	}

	requests := server.Requests()
	wantOperations := []string{
		"updateHosts", "getTask", "getTask", "getHosts",
		"updateHosts", "getTask", "getTask", "getHosts",
	}
	wantMethods := []string{
		"PATCH", "GET", "GET", "GET",
		"PATCH", "GET", "GET", "GET",
	}
	if len(requests) != len(wantOperations) {
		t.Fatalf("request count = %d, want %d: %#v", len(requests), len(wantOperations), requests)
	}
	for index, captured := range requests {
		if captured.OperationID != wantOperations[index] ||
			captured.Method != wantMethods[index] ||
			captured.RawQuery != "" ||
			captured.ForceQuery {
			t.Fatalf("request %d target mismatch: %+v", index, captured)
		}
		wantPath := "/v1/hosts"
		if captured.OperationID == "getTask" {
			wantPath = "/v1/tasks/" + runtime.TaskID
			wantEscaped := "/v1/tasks/" + url.PathEscape(runtime.TaskID)
			if captured.EscapedPath != wantEscaped {
				t.Fatalf(
					"request %d escaped path = %q, want %q",
					index,
					captured.EscapedPath,
					wantEscaped,
				)
			}
		}
		if captured.Path != wantPath {
			t.Fatalf("request %d path = %q, want %q", index, captured.Path, wantPath)
		}
		if !reflect.DeepEqual(
			captured.Header.Values("Authorization"),
			[]string{"Bearer " + runtime.AccessToken},
		) ||
			!reflect.DeepEqual(
				captured.Header.Values("Accept"),
				[]string{"application/json"},
			) {
			t.Fatalf("request %d auth/accept mismatch: %#v", index, captured.Header)
		}
		if captured.Method == http.MethodPatch {
			if !reflect.DeepEqual(
				captured.Header.Values("Content-Type"),
				[]string{"application/json"},
			) ||
				captured.ContentLength != int64(len(captured.Body)) ||
				len(captured.TransferEncoding) != 0 {
				t.Fatalf("PATCH entity metadata mismatch: %+v", captured)
			}
		} else if len(captured.Header.Values("Content-Type")) != 0 ||
			len(captured.Body) != 0 ||
			captured.ContentLength != 0 ||
			len(captured.TransferEncoding) != 0 {
			t.Fatalf("GET %d unexpectedly carried an entity: %+v", index, captured)
		}
	}
	wantBody := fmt.Sprintf(
		`{"hostIds":["%s","%s"],"hostsRefreshSpec":{"forceRefresh":true}}`,
		runtime.HostZuluID,
		runtime.HostAlphaID,
	)
	for _, index := range []int{0, 4} {
		if string(requests[index].Body) != wantBody {
			t.Fatalf(
				"PATCH %d body = %q, want %q",
				index,
				requests[index].Body,
				wantBody,
			)
		}
		assertJSONBody(t, requests[index].Body, map[string]any{
			"hostIds": []any{runtime.HostZuluID, runtime.HostAlphaID},
			"hostsRefreshSpec": map[string]any{
				"forceRefresh": true,
			},
		})
		if strings.Contains(string(requests[index].Body), "null") {
			t.Fatalf("PATCH %d synthesized null: %s", index, requests[index].Body)
		}
	}

	paceMu.Lock()
	gotPace := append([]int(nil), paceCalls...)
	paceMu.Unlock()
	if !reflect.DeepEqual(gotPace, []int{1, 1}) {
		t.Fatalf("Pace calls = %v, want [1 1]", gotPace)
	}
}

func TestOptionalRefreshMemberWireShapeTableDriven(t *testing.T) {
	tests := []struct {
		name  string
		force *bool
		tail  string
	}{
		{
			name: "omitted",
			tail: "}",
		},
		{
			name:  "explicit false",
			force: boolPointer(false),
			tail:  `,"hostsRefreshSpec":{"forceRefresh":false}}`,
		},
		{
			name:  "explicit true",
			force: boolPointer(true),
			tail:  `,"hostsRefreshSpec":{"forceRefresh":true}}`,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
				plan := validPlan(runtime)
				plan.TaskPolls = []contractmock.TaskReply{{
					Task: taskWithStatus(runtime, "SUCCESSFUL"),
				}}
				return plan
			})
			runtime := server.Runtime()
			client := newClient(t, server, 2, nil)
			_, err := client.RefreshHosts(context.Background(), hr.RefreshRequest{
				HostIDs:      []string{runtime.HostAlphaID},
				ForceRefresh: test.force,
			})
			if err != nil {
				t.Fatalf("RefreshHosts: %v", err)
			}
			requests := server.Requests()
			if len(requests) != 3 {
				t.Fatalf("request count = %d, want 3", len(requests))
			}
			want := fmt.Sprintf(
				`{"hostIds":["%s"]%s`,
				runtime.HostAlphaID,
				test.tail,
			)
			if string(requests[0].Body) != want {
				t.Fatalf("body = %q, want %q", requests[0].Body, want)
			}
			if test.force == nil &&
				strings.Contains(string(requests[0].Body), "hostsRefreshSpec") {
				t.Fatalf("optional member was not omitted: %s", requests[0].Body)
			}
		})
	}
}

func TestPollingAndFailureGatesTableDriven(t *testing.T) {
	tests := []struct {
		name         string
		maxPolls     int
		mutate       func(contractmock.RuntimeValues, *contractmock.Plan)
		wantKind     string
		wantRequests int
		wantPace     []int
	}{
		{
			name: "accepted terminal looking task is still polled",
			mutate: func(runtime contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.UpdateTask.Status = "SUCCESSFUL"
				failed := taskWithStatus(runtime, "FAILED")
				failed.Errors = []contractmock.VCFError{{
					Message:        "sensitive server failure",
					ReferenceToken: runtime.ReferenceToken,
				}}
				plan.TaskPolls = []contractmock.TaskReply{{Task: failed}}
			},
			wantKind:     "terminal",
			wantRequests: 2,
		},
		{
			name: "completed with warning is successful",
			mutate: func(runtime contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.TaskPolls = []contractmock.TaskReply{{
					Task: taskWithStatus(runtime, " COMPLETED WITH WARNING "),
				}}
			},
			wantRequests: 3,
		},
		{
			name: "unknown status is a protocol error",
			mutate: func(runtime contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.TaskPolls = []contractmock.TaskReply{{
					Task: taskWithStatus(runtime, "DONE"),
				}}
			},
			wantKind:     "protocol",
			wantRequests: 2,
		},
		{
			name:     "nonterminal task is bounded",
			maxPolls: 2,
			mutate: func(runtime contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.TaskPolls = []contractmock.TaskReply{
					{Task: taskWithStatus(runtime, "QUEUED")},
					{Task: taskWithStatus(runtime, "IN_PROGRESS")},
				}
			},
			wantKind:     "poll-limit",
			wantRequests: 3,
			wantPace:     []int{1},
		},
		{
			name: "submit API error stops",
			mutate: func(runtime contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.UpdateHTTPStatus = http.StatusBadRequest
				plan.UpdateAPIError = contractmock.VCFError{
					ErrorCode:      "INVALID_HOST",
					Message:        "do not expose " + runtime.AccessToken,
					ReferenceToken: runtime.ReferenceToken,
				}
			},
			wantKind:     "api",
			wantRequests: 1,
		},
		{
			name: "poll API error stops before collection",
			mutate: func(runtime contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.TaskPolls = []contractmock.TaskReply{{
					HTTPStatus: http.StatusInternalServerError,
					APIError: contractmock.VCFError{
						ErrorCode: "TASK_UNAVAILABLE",
						Message:   "poll failed " + runtime.AccessToken,
					},
				}}
			},
			wantKind:     "api",
			wantRequests: 2,
		},
		{
			name: "collection API error follows terminal success",
			mutate: func(runtime contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.TaskPolls = []contractmock.TaskReply{{
					Task: taskWithStatus(runtime, "SKIPPED"),
				}}
				plan.HostsHTTPStatus = http.StatusInternalServerError
				plan.HostsAPIError = contractmock.VCFError{
					ErrorCode: "INVENTORY_FAILED",
					Message:   "inventory unavailable " + runtime.AccessToken,
				}
			},
			wantKind:     "api",
			wantRequests: 3,
		},
		{
			name: "mismatched polled task id is rejected",
			mutate: func(runtime contractmock.RuntimeValues, plan *contractmock.Plan) {
				task := taskWithStatus(runtime, "SUCCESSFUL")
				task.ID = "a-different-task"
				plan.TaskPolls = []contractmock.TaskReply{{Task: task}}
			},
			wantKind:     "protocol",
			wantRequests: 2,
		},
		{
			name: "accepted task needs an id",
			mutate: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.UpdateTask.ID = ""
			},
			wantKind:     "protocol",
			wantRequests: 1,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
				plan := validPlan(runtime)
				test.mutate(runtime, &plan)
				return plan
			})
			runtime := server.Runtime()
			maxPolls := test.maxPolls
			if maxPolls == 0 {
				maxPolls = 4
			}
			var paceCalls []int
			client := newClient(
				t,
				server,
				maxPolls,
				func(_ context.Context, operationID string, completed int) error {
					if operationID != "getTask" {
						t.Errorf("Pace operationId = %q", operationID)
					}
					paceCalls = append(paceCalls, completed)
					return nil
				},
			)
			result, err := client.RefreshHosts(
				context.Background(),
				hr.RefreshRequest{HostIDs: []string{runtime.HostAlphaID}},
			)
			assertErrorKind(t, err, test.wantKind)
			if got := len(server.Requests()); got != test.wantRequests {
				t.Fatalf(
					"request count = %d, want %d: %#v",
					got,
					test.wantRequests,
					server.Requests(),
				)
			}
			if !reflect.DeepEqual(paceCalls, test.wantPace) {
				t.Fatalf("Pace calls = %v, want %v", paceCalls, test.wantPace)
			}
			if test.wantKind == "terminal" {
				if result.Task.Status != "FAILED" ||
					len(result.Task.Errors) != 1 ||
					result.Task.Errors[0].ReferenceToken != runtime.ReferenceToken {
					t.Fatalf("terminal task details were not preserved: %+v", result.Task)
				}
				for _, secret := range []string{
					runtime.ReferenceToken,
					"sensitive server failure",
				} {
					if strings.Contains(err.Error(), secret) {
						t.Fatalf("terminal error leaked %q: %v", secret, err)
					}
				}
			}
		})
	}
}

func TestPaceErrorStopsBeforeAnotherPoll(t *testing.T) {
	server := newServer(t, validPlan)
	runtime := server.Runtime()
	sentinel := errors.New("pacing cancelled")
	client := newClient(
		t,
		server,
		4,
		func(_ context.Context, operationID string, completed int) error {
			if operationID != "getTask" || completed != 1 {
				t.Fatalf(
					"Pace call = (%q, %d), want (getTask, 1)",
					operationID,
					completed,
				)
			}
			return sentinel
		},
	)
	result, err := client.RefreshHosts(
		context.Background(),
		hr.RefreshRequest{HostIDs: []string{runtime.HostAlphaID}},
	)
	if !errors.Is(err, sentinel) {
		t.Fatalf("Pace failure returned %T: %v", err, err)
	}
	if result.Task.Status != " In Progress " {
		t.Fatalf("last task was not preserved: %+v", result.Task)
	}
	requests := server.Requests()
	if len(requests) != 2 ||
		requests[0].OperationID != "updateHosts" ||
		requests[1].OperationID != "getTask" {
		t.Fatalf("Pace failure did not stop traffic: %#v", requests)
	}
}

func TestValidationIsLocalAndTableDriven(t *testing.T) {
	configTests := []struct {
		name   string
		config hr.Config
	}{
		{
			name: "non HTTP scheme",
			config: hr.Config{
				BaseURL:     "ftp://127.0.0.1",
				AccessToken: "token",
				MaxPolls:    1,
			},
		},
		{
			name: "embedded credentials",
			config: hr.Config{
				BaseURL:     "http://user:pass@127.0.0.1",
				AccessToken: "token",
				MaxPolls:    1,
			},
		},
		{
			name: "query",
			config: hr.Config{
				BaseURL:     "http://127.0.0.1?x=1",
				AccessToken: "token",
				MaxPolls:    1,
			},
		},
		{
			name: "fragment",
			config: hr.Config{
				BaseURL:     "http://127.0.0.1#x",
				AccessToken: "token",
				MaxPolls:    1,
			},
		},
		{
			name: "non root path",
			config: hr.Config{
				BaseURL:     "http://127.0.0.1/sdk",
				AccessToken: "token",
				MaxPolls:    1,
			},
		},
		{
			name: "blank token",
			config: hr.Config{
				BaseURL:     "http://127.0.0.1",
				AccessToken: " ",
				MaxPolls:    1,
			},
		},
		{
			name: "whitespace bearing token",
			config: hr.Config{
				BaseURL:     "http://127.0.0.1",
				AccessToken: "token\tvalue",
				MaxPolls:    1,
			},
		},
		{
			name: "zero polls",
			config: hr.Config{
				BaseURL:     "http://127.0.0.1",
				AccessToken: "token",
			},
		},
	}
	for _, test := range configTests {
		t.Run(test.name, func(t *testing.T) {
			if client, err := hr.NewClient(test.config); err == nil || client != nil {
				t.Fatalf("NewClient accepted invalid config: %+v", test.config)
			}
		})
	}
	if _, err := hr.NewClient(hr.Config{
		BaseURL:     "http://127.0.0.1/",
		AccessToken: "token",
		MaxPolls:    1,
	}); err != nil {
		t.Fatalf("NewClient rejected root origin/defaults: %v", err)
	}

	server := newServer(t, validPlan)
	runtime := server.Runtime()
	client := newClient(t, server, 2, nil)
	tooMany := make([]string, 101)
	for index := range tooMany {
		tooMany[index] = fmt.Sprintf("host-%d", index)
	}
	requestTests := []struct {
		name    string
		ctx     context.Context
		request hr.RefreshRequest
	}{
		{
			name:    "empty host ids",
			ctx:     context.Background(),
			request: hr.RefreshRequest{},
		},
		{
			name: "too many host ids",
			ctx:  context.Background(),
			request: hr.RefreshRequest{
				HostIDs: tooMany,
			},
		},
		{
			name: "blank host id",
			ctx:  context.Background(),
			request: hr.RefreshRequest{
				HostIDs: []string{""},
			},
		},
		{
			name: "surrounding whitespace",
			ctx:  context.Background(),
			request: hr.RefreshRequest{
				HostIDs: []string{" " + runtime.HostAlphaID},
			},
		},
		{
			name: "nil context",
			request: hr.RefreshRequest{
				HostIDs: []string{runtime.HostAlphaID},
			},
		},
	}
	for _, test := range requestTests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := client.RefreshHosts(test.ctx, test.request); err == nil {
				t.Fatal("RefreshHosts accepted invalid local input")
			}
		})
	}
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := client.RefreshHosts(cancelled, hr.RefreshRequest{
		HostIDs: []string{runtime.HostAlphaID},
	})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled validation returned %T: %v", err, err)
	}
	if requests := server.Requests(); len(requests) != 0 {
		t.Fatalf("local validation sent traffic: %#v", requests)
	}

	var nilClient *hr.Client
	if _, err := nilClient.RefreshHosts(context.Background(), hr.RefreshRequest{
		HostIDs: []string{runtime.HostAlphaID},
	}); err == nil {
		t.Fatal("nil Client was accepted")
	}
}

func TestStructuredErrorsArePreservedAndRedacted(t *testing.T) {
	server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
		plan := validPlan(runtime)
		plan.UpdateHTTPStatus = http.StatusInternalServerError
		plan.UpdateAPIError = contractmock.VCFError{
			ErrorCode:          "HOST_REFRESH_FAILED",
			Message:            "server text contains " + runtime.AccessToken,
			RemediationMessage: "remediation contains " + runtime.ReferenceToken,
			ReferenceToken:     runtime.ReferenceToken,
		}
		return plan
	})
	runtime := server.Runtime()
	client := newClient(t, server, 2, nil)
	_, err := client.RefreshHosts(context.Background(), hr.RefreshRequest{
		HostIDs: []string{runtime.HostAlphaID},
	})
	var apiError *hr.APIError
	if !errors.As(err, &apiError) {
		t.Fatalf("error = %T, want *APIError", err)
	}
	if apiError.OperationID != "updateHosts" ||
		apiError.Status != http.StatusInternalServerError ||
		apiError.ErrorCode != "HOST_REFRESH_FAILED" ||
		apiError.Message != "server text contains "+runtime.AccessToken ||
		apiError.RemediationMessage != "remediation contains "+runtime.ReferenceToken ||
		apiError.ReferenceToken != runtime.ReferenceToken {
		t.Fatalf("API error fields were not preserved: %+v", apiError)
	}
	for _, secret := range []string{
		runtime.AccessToken,
		runtime.ReferenceToken,
		apiError.Message,
		apiError.RemediationMessage,
	} {
		if strings.Contains(apiError.Error(), secret) {
			t.Fatalf("API error text leaked %q: %v", secret, apiError)
		}
	}

	transportSecret := "transport-secret-" + runtime.AccessToken
	sentinel := fmt.Errorf("dial failed with %s", transportSecret)
	transportClient, err := hr.NewClient(hr.Config{
		BaseURL:     "http://127.0.0.1:1",
		AccessToken: runtime.AccessToken,
		MaxPolls:    1,
		HTTPClient: &http.Client{Transport: roundTripFunc(
			func(*http.Request) (*http.Response, error) {
				return nil, sentinel
			},
		)},
	})
	if err != nil {
		t.Fatalf("NewClient for transport test: %v", err)
	}
	_, err = transportClient.RefreshHosts(context.Background(), hr.RefreshRequest{
		HostIDs: []string{runtime.HostAlphaID},
	})
	var transportError *hr.TransportError
	if !errors.As(err, &transportError) || !errors.Is(err, sentinel) {
		t.Fatalf("transport error did not preserve cause: %T %v", err, err)
	}
	if strings.Contains(err.Error(), transportSecret) ||
		strings.Contains(err.Error(), runtime.AccessToken) {
		t.Fatalf("transport error leaked details: %v", err)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(
	request *http.Request,
) (*http.Response, error) {
	return function(request)
}

func newServer(
	t *testing.T,
	factory func(contractmock.RuntimeValues) contractmock.Plan,
) *contractmock.Server {
	t.Helper()
	server, err := contractmock.New("docs/contract.json", factory)
	if err != nil {
		t.Fatalf("start contract mock: %v", err)
	}
	t.Cleanup(server.Close)
	return server
}

func validPlan(runtime contractmock.RuntimeValues) contractmock.Plan {
	return contractmock.Plan{
		UpdateTask: taskWithStatus(runtime, "SUCCESSFUL"),
		TaskPolls: []contractmock.TaskReply{
			{Task: taskWithStatus(runtime, " In Progress ")},
			{Task: taskWithStatus(runtime, " Successful ")},
		},
		Hosts: []contractmock.Host{
			{
				ID:     runtime.HostZuluID,
				FQDN:   runtime.ZuluFQDN,
				Status: "UNASSIGNED_UNUSEABLE",
			},
			{
				ID:     runtime.HostCharlieID,
				FQDN:   runtime.AlphaFQDN,
				Status: "ASSIGNED",
			},
			{
				ID:     runtime.HostAlphaID,
				FQDN:   runtime.AlphaFQDN,
				Status: "UNASSIGNED_USEABLE",
			},
		},
	}
}

func taskWithStatus(
	runtime contractmock.RuntimeValues,
	status string,
) contractmock.Task {
	return contractmock.Task{
		ID:                runtime.TaskID,
		Name:              "Refresh Hosts",
		Type:              "HOST_REFRESH",
		Status:            status,
		CreationTimestamp: "2026-07-28T12:00:00Z",
	}
}

func newClient(
	t *testing.T,
	server *contractmock.Server,
	maxPolls int,
	pace hr.PaceFunc,
) *hr.Client {
	t.Helper()
	client, err := hr.NewClient(hr.Config{
		BaseURL:     server.URL(),
		AccessToken: server.Runtime().AccessToken,
		HTTPClient:  server.Client(),
		MaxPolls:    maxPolls,
		Pace:        pace,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func assertErrorKind(t *testing.T, err error, kind string) {
	t.Helper()
	if kind == "" {
		if err != nil {
			t.Fatalf("unexpected error %T: %v", err, err)
		}
		return
	}
	if err == nil {
		t.Fatalf("expected %s error, got nil", kind)
	}
	switch kind {
	case "api":
		var target *hr.APIError
		if !errors.As(err, &target) {
			t.Fatalf("error = %T, want *APIError", err)
		}
	case "protocol":
		var target *hr.ProtocolError
		if !errors.As(err, &target) {
			t.Fatalf("error = %T, want *ProtocolError", err)
		}
	case "terminal":
		var target *hr.TaskTerminalError
		if !errors.As(err, &target) {
			t.Fatalf("error = %T, want *TaskTerminalError", err)
		}
	case "poll-limit":
		var target *hr.PollLimitError
		if !errors.As(err, &target) {
			t.Fatalf("error = %T, want *PollLimitError", err)
		}
	default:
		t.Fatalf("unknown test error kind %q", kind)
	}
}

func boolPointer(value bool) *bool {
	return &value
}

func assertJSONBody(t *testing.T, data []byte, want any) {
	t.Helper()
	var got any
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("decode request JSON: %v", err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("request JSON = %#v, want %#v", got, want)
	}
}

func readJSON(t *testing.T, path string, out any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, out); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func assertFileHash(t *testing.T, path string, expected string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	sum := sha256.Sum256(data)
	got := hex.EncodeToString(sum[:])
	if got != expected {
		t.Fatalf("%s sha256 = %s, want %s", path, got, expected)
	}
}
