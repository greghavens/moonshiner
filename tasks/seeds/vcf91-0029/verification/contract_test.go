package systembaseline_test

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
	"sync"
	"sync/atomic"
	"testing"

	sb "vcf91-0029"
	"vcf91-0029/internal/contractmock"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedSpec   = "specifications/sddc-manager/sddc-manager-openapi.json"
	contractSHA256 = "378abce4f2e5ffb8b3d55f656ab7b70d306d31a3e2a2992b756fd6a96e14d4b6"
	sourcesSHA256  = "a187b14156122f5d61d94a8788e3d10c1e465110adff16f7fb8fcc2c8eaee207"
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
		Operations []operationSource `json:"operations"`
		Schemas    map[string]struct {
			Required   []string `json:"required"`
			Properties map[string]struct {
				Type     string   `json:"type"`
				Format   string   `json:"format"`
				ReadOnly bool     `json:"readOnly"`
				Enum     []string `json:"enum"`
			} `json:"properties"`
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
			Version string `json:"info_version"`
		} `json:"specification"`
		Operations []operationSource `json:"operations"`
		Derivation string            `json:"derivation"`
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
		contract.DerivedFrom.Version != "9.1.0.0" ||
		sources.Specification.Version != "9.1.0.0" ||
		contract.DerivedFrom.License != "Apache-2.0" ||
		sources.Repository.License != "Apache-2.0" {
		t.Fatalf("incorrect version/license provenance: contract=%+v sources=%+v",
			contract.DerivedFrom, sources)
	}
	if !strings.Contains(sources.Derivation, "OpenAPI specification") ||
		!strings.Contains(sources.Derivation, "No rendered documentation page") {
		t.Fatalf("source derivation is not explicit: %q", sources.Derivation)
	}

	wantOperations := []operationSource{
		{OperationID: "updateProxyConfiguration", Method: "PATCH", Path: "/v1/system/proxy-configuration"},
		{OperationID: "setCeipStatus", Method: "PATCH", Path: "/v1/system/ceip"},
		{OperationID: "getTask", Method: "GET", Path: "/v1/tasks/{id}"},
	}
	if !reflect.DeepEqual(contract.Operations, wantOperations) ||
		!reflect.DeepEqual(sources.Operations, wantOperations) {
		t.Fatalf("operation provenance mismatch\ncontract: %#v\nsources: %#v\nwant: %#v",
			contract.Operations, sources.Operations, wantOperations)
	}

	proxy, ok := contract.Schemas["ProxyConfiguration"]
	if !ok || len(proxy.Required) != 0 {
		t.Fatalf("ProxyConfiguration projection lost its optional-property contract: %+v", proxy)
	}
	wantProxyProperties := []string{
		"host", "isAuthenticated", "isConfigured", "isEnabled",
		"password", "port", "transferProtocol", "username",
	}
	gotProxyProperties := make([]string, 0, len(proxy.Properties))
	for name := range proxy.Properties {
		gotProxyProperties = append(gotProxyProperties, name)
	}
	sortStrings(gotProxyProperties)
	if !reflect.DeepEqual(gotProxyProperties, wantProxyProperties) ||
		!proxy.Properties["isConfigured"].ReadOnly ||
		proxy.Properties["port"].Format != "int32" ||
		!reflect.DeepEqual(proxy.Properties["transferProtocol"].Enum, []string{"HTTP", "HTTPS"}) {
		t.Fatalf("ProxyConfiguration schema projection mismatch: %+v", proxy)
	}
	ceip := contract.Schemas["CeipUpdateSpec"]
	if !reflect.DeepEqual(ceip.Required, []string{"status"}) {
		t.Fatalf("CeipUpdateSpec required fields = %v", ceip.Required)
	}
	task := contract.Schemas["Task"]
	if !reflect.DeepEqual(task.Required, []string{"creationTimestamp", "id", "name", "status"}) {
		t.Fatalf("Task required fields = %v", task.Required)
	}
}

func TestApplySystemBaselineReportsLaterTaskFailureAndExactWire(t *testing.T) {
	failure := contractmock.VCFError{
		ErrorCode:          "VCF_CEIP_UPDATE_FAILED",
		Message:            "the CEIP service rejected the transition",
		RemediationMessage: "check the outbound service route",
		ReferenceToken:     "ref-ceip-2901",
	}
	server := newServer(t, contractmock.Plan{
		ProxyPolls: []contractmock.PollReply{
			{TaskStatus: "IN_PROGRESS"},
			{TaskStatus: "SUCCESSFUL"},
		},
		CeipPolls: []contractmock.PollReply{
			{TaskStatus: "IN_PROGRESS"},
			{TaskStatus: "FAILED", Errors: []contractmock.VCFError{failure}},
		},
	})
	runtime := server.Runtime()

	type paceCall struct {
		OperationID   string
		CompletedPoll int
	}
	var paceMu sync.Mutex
	var paceCalls []paceCall
	client := newClient(t, server, 4, func(
		ctx context.Context,
		operationID string,
		completedPolls int,
	) error {
		paceMu.Lock()
		paceCalls = append(paceCalls, paceCall{operationID, completedPolls})
		paceMu.Unlock()
		return ctx.Err()
	})

	enabled := true
	host := "egress-proxy.example.test"
	port := int32(3128)
	protocol := "HTTPS"
	report, err := client.ApplySystemBaseline(context.Background(), sb.BaselineSpec{
		Proxy: sb.ProxyConfiguration{
			IsEnabled:        &enabled,
			Host:             &host,
			Port:             &port,
			TransferProtocol: &protocol,
		},
		CEIP: sb.CeipUpdateSpec{Status: "DISABLE"},
	})

	var terminalError *sb.TaskTerminalError
	if !errors.As(err, &terminalError) {
		t.Fatalf("error = %T %v, want *TaskTerminalError", err, err)
	}
	if terminalError.OperationID != "setCeipStatus" ||
		terminalError.Task.ID != runtime.CeipTaskID ||
		terminalError.Task.Status != "FAILED" ||
		!reflect.DeepEqual(terminalError.Task.Errors, []sb.VCFError{{
			ErrorCode:          failure.ErrorCode,
			Message:            failure.Message,
			RemediationMessage: failure.RemediationMessage,
			ReferenceToken:     failure.ReferenceToken,
		}}) {
		t.Fatalf("later terminal error lost contract details: %+v", terminalError)
	}
	wantReport := sb.Report{Steps: []sb.StepResult{
		{
			OperationID: "updateProxyConfiguration",
			TaskID:      runtime.ProxyTaskID,
			Status:      "SUCCESSFUL",
			PollCount:   2,
		},
		{
			OperationID: "setCeipStatus",
			TaskID:      runtime.CeipTaskID,
			Status:      "FAILED",
			PollCount:   2,
		},
	}}
	if !reflect.DeepEqual(report, wantReport) {
		t.Fatalf("partial report did not preserve successful earlier work\n got: %#v\nwant: %#v",
			report, wantReport)
	}
	if strings.Contains(err.Error(), runtime.AccessToken) ||
		strings.Contains(err.Error(), failure.Message) {
		t.Fatalf("error string exposed a bearer token or server message: %v", err)
	}

	requests := server.Requests()
	wantOperations := []string{
		"updateProxyConfiguration", "getTask", "getTask",
		"setCeipStatus", "getTask", "getTask",
	}
	wantMethods := []string{"PATCH", "GET", "GET", "PATCH", "GET", "GET"}
	wantPaths := []string{
		"/v1/system/proxy-configuration",
		"/v1/tasks/" + runtime.ProxyTaskID,
		"/v1/tasks/" + runtime.ProxyTaskID,
		"/v1/system/ceip",
		"/v1/tasks/" + runtime.CeipTaskID,
		"/v1/tasks/" + runtime.CeipTaskID,
	}
	if len(requests) != len(wantPaths) {
		t.Fatalf("request count = %d, want %d: %#v", len(requests), len(wantPaths), requests)
	}
	for index, request := range requests {
		if request.OperationID != wantOperations[index] ||
			request.Method != wantMethods[index] ||
			request.Path != wantPaths[index] ||
			request.RawQuery != "" {
			t.Fatalf("request %d target mismatch: %+v", index, request)
		}
		if !reflect.DeepEqual(request.Header.Values("Authorization"), []string{"Bearer " + runtime.AccessToken}) {
			t.Fatalf("request %d Authorization = %#v", index, request.Header.Values("Authorization"))
		}
		if !reflect.DeepEqual(request.Header.Values("Accept"), []string{"application/json"}) {
			t.Fatalf("request %d Accept = %#v", index, request.Header.Values("Accept"))
		}
		if request.Method == http.MethodPatch {
			if !reflect.DeepEqual(request.Header.Values("Content-Type"), []string{"application/json"}) ||
				len(request.Body) == 0 {
				t.Fatalf("PATCH %d entity metadata/body mismatch: %+v", index, request)
			}
		} else if len(request.Header.Values("Content-Type")) != 0 ||
			len(request.Body) != 0 ||
			request.ContentLength != 0 ||
			len(request.TransferEncoding) != 0 {
			t.Fatalf("GET %d unexpectedly carried an entity: %+v", index, request)
		}
	}

	assertJSONBody(t, requests[0].Body, map[string]any{
		"isEnabled":        true,
		"host":             host,
		"port":             float64(port),
		"transferProtocol": protocol,
	})
	proxyText := string(requests[0].Body)
	for _, absent := range []string{
		"isConfigured", "username", "password", "isAuthenticated", "null",
	} {
		if strings.Contains(proxyText, absent) {
			t.Fatalf("unset/read-only proxy property %q appeared on wire: %s", absent, proxyText)
		}
	}
	assertJSONBody(t, requests[3].Body, map[string]any{"status": "DISABLE"})

	paceMu.Lock()
	gotPace := append([]paceCall(nil), paceCalls...)
	paceMu.Unlock()
	wantPace := []paceCall{
		{OperationID: "updateProxyConfiguration", CompletedPoll: 1},
		{OperationID: "setCeipStatus", CompletedPoll: 1},
	}
	if !reflect.DeepEqual(gotPace, wantPace) {
		t.Fatalf("Pace calls = %#v, want %#v", gotPace, wantPace)
	}
}

func TestWorkflowStatusRulesAndStopBehavior(t *testing.T) {
	tests := []struct {
		name         string
		plan         contractmock.Plan
		maxPolls     int
		wantStatuses []string
		wantRequests int
		checkError   func(*testing.T, error)
	}{
		{
			name: "mixed_case_and_space_successes",
			plan: contractmock.Plan{
				ProxyPolls: []contractmock.PollReply{{TaskStatus: "Successful"}},
				CeipPolls:  []contractmock.PollReply{{TaskStatus: "Completed With Warning"}},
			},
			maxPolls:     2,
			wantStatuses: []string{"Successful", "Completed With Warning"},
			wantRequests: 4,
			checkError: func(t *testing.T, err error) {
				t.Helper()
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
			},
		},
		{
			name: "first_step_terminal_failure_stops_second_mutation",
			plan: contractmock.Plan{
				ProxyPolls: []contractmock.PollReply{{TaskStatus: "CANCELLED"}},
			},
			maxPolls:     2,
			wantStatuses: []string{"CANCELLED"},
			wantRequests: 2,
			checkError: func(t *testing.T, err error) {
				t.Helper()
				var target *sb.TaskTerminalError
				if !errors.As(err, &target) ||
					target.OperationID != "updateProxyConfiguration" {
					t.Fatalf("error = %T %v, want proxy TaskTerminalError", err, err)
				}
			},
		},
		{
			name: "poll_limit_stops_workflow",
			plan: contractmock.Plan{
				ProxyPolls: []contractmock.PollReply{{TaskStatus: "QUEUED"}},
			},
			maxPolls:     1,
			wantStatuses: []string{"QUEUED"},
			wantRequests: 2,
			checkError: func(t *testing.T, err error) {
				t.Helper()
				var target *sb.PollLimitError
				if !errors.As(err, &target) ||
					target.OperationID != "updateProxyConfiguration" ||
					target.MaxPolls != 1 ||
					target.LastStatus != "QUEUED" {
					t.Fatalf("poll limit details lost: %+v (%v)", target, err)
				}
			},
		},
		{
			name: "unknown_status_is_contract_failure",
			plan: contractmock.Plan{
				ProxyPolls: []contractmock.PollReply{{TaskStatus: "PAUSED_BY_OPERATOR"}},
			},
			maxPolls:     3,
			wantStatuses: []string{"PAUSED_BY_OPERATOR"},
			wantRequests: 2,
			checkError: func(t *testing.T, err error) {
				t.Helper()
				var target *sb.ProtocolError
				if !errors.As(err, &target) ||
					target.OperationID != "getTask" ||
					!strings.Contains(target.Reason, "PAUSED_BY_OPERATOR") {
					t.Fatalf("error = %T %+v, want getTask ProtocolError", err, err)
				}
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(t, test.plan)
			client := newClient(t, server, test.maxPolls, nil)
			report, err := client.ApplySystemBaseline(context.Background(), disabledBaseline())
			test.checkError(t, err)
			if len(report.Steps) != len(test.wantStatuses) {
				t.Fatalf("steps = %#v, want statuses %v", report.Steps, test.wantStatuses)
			}
			for index, want := range test.wantStatuses {
				if report.Steps[index].Status != want {
					t.Fatalf("step %d status = %q, want %q", index, report.Steps[index].Status, want)
				}
			}
			if got := len(server.Requests()); got != test.wantRequests {
				t.Fatalf("request count = %d, want %d", got, test.wantRequests)
			}
		})
	}
}

func TestLaterHTTPFailureKeepsEarlierCompletedStep(t *testing.T) {
	server := newServer(t, contractmock.Plan{
		ProxyPolls:       []contractmock.PollReply{{TaskStatus: "SUCCESSFUL"}},
		CeipSubmitStatus: http.StatusConflict,
		CeipSubmitError: contractmock.VCFError{
			ErrorCode:          "VCF_CEIP_CONFLICT",
			Message:            "another CEIP update is active",
			RemediationMessage: "wait for the active task",
			ReferenceToken:     "ref-conflict-29",
		},
	})
	runtime := server.Runtime()
	client := newClient(t, server, 2, nil)
	report, err := client.ApplySystemBaseline(context.Background(), disabledBaseline())

	var apiError *sb.APIError
	if !errors.As(err, &apiError) {
		t.Fatalf("error = %T %v, want *APIError", err, err)
	}
	if apiError.OperationID != "setCeipStatus" ||
		apiError.StatusCode != http.StatusConflict ||
		apiError.ErrorCode != "VCF_CEIP_CONFLICT" ||
		apiError.Message != "another CEIP update is active" ||
		apiError.RemediationMessage != "wait for the active task" ||
		apiError.ReferenceToken != "ref-conflict-29" {
		t.Fatalf("API error envelope not preserved: %+v", apiError)
	}
	want := sb.Report{Steps: []sb.StepResult{{
		OperationID: "updateProxyConfiguration",
		TaskID:      runtime.ProxyTaskID,
		Status:      "SUCCESSFUL",
		PollCount:   1,
	}}}
	if !reflect.DeepEqual(report, want) {
		t.Fatalf("earlier completed step lost after later HTTP failure: %#v", report)
	}
	requests := server.Requests()
	if len(requests) != 3 ||
		requests[2].OperationID != "setCeipStatus" {
		t.Fatalf("unexpected request sequence: %#v", requests)
	}
	if strings.Contains(err.Error(), runtime.AccessToken) ||
		strings.Contains(err.Error(), apiError.Message) {
		t.Fatalf("API error text leaked sensitive/server data: %v", err)
	}
}

func TestProxyPresenceAwareEncoding(t *testing.T) {
	tests := []struct {
		name     string
		proxy    sb.ProxyConfiguration
		wantBody map[string]any
	}{
		{
			name: "explicit_false_is_present",
			proxy: sb.ProxyConfiguration{
				IsEnabled: boolPointer(false),
			},
			wantBody: map[string]any{"isEnabled": false},
		},
		{
			name: "optional_authentication_fields_are_present_when_set",
			proxy: sb.ProxyConfiguration{
				IsEnabled:        boolPointer(true),
				Host:             stringPointer("proxy-auth.example.test"),
				Port:             int32Pointer(8443),
				TransferProtocol: stringPointer("HTTPS"),
				Username:         stringPointer("svc-vcf"),
				Password:         stringPointer("fixture-password"),
				IsAuthenticated:  boolPointer(true),
			},
			wantBody: map[string]any{
				"isEnabled":        true,
				"host":             "proxy-auth.example.test",
				"port":             float64(8443),
				"transferProtocol": "HTTPS",
				"username":         "svc-vcf",
				"password":         "fixture-password",
				"isAuthenticated":  true,
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(t, contractmock.Plan{})
			client := newClient(t, server, 1, nil)
			_, err := client.ApplySystemBaseline(context.Background(), sb.BaselineSpec{
				Proxy: test.proxy,
				CEIP:  sb.CeipUpdateSpec{Status: "ENABLE"},
			})
			if err != nil {
				t.Fatalf("ApplySystemBaseline: %v", err)
			}
			requests := server.Requests()
			if len(requests) != 4 {
				t.Fatalf("request count = %d, want 4", len(requests))
			}
			assertJSONBody(t, requests[0].Body, test.wantBody)
			if strings.Contains(string(requests[0].Body), "null") ||
				strings.Contains(string(requests[0].Body), "isConfigured") {
				t.Fatalf("proxy request emitted null/read-only property: %s", requests[0].Body)
			}
		})
	}
}

func TestPaceFailureReturnsBothKnownStepStates(t *testing.T) {
	server := newServer(t, contractmock.Plan{
		ProxyPolls: []contractmock.PollReply{{TaskStatus: "SUCCESSFUL"}},
		CeipPolls: []contractmock.PollReply{
			{TaskStatus: "IN_PROGRESS"},
			{TaskStatus: "SUCCESSFUL"},
		},
	})
	sentinel := errors.New("operator stopped pacing")
	client := newClient(t, server, 3, func(
		_ context.Context,
		operationID string,
		_ int,
	) error {
		if operationID == "setCeipStatus" {
			return sentinel
		}
		return nil
	})
	report, err := client.ApplySystemBaseline(context.Background(), disabledBaseline())
	if !errors.Is(err, sentinel) {
		t.Fatalf("error = %v, want Pace sentinel", err)
	}
	if len(report.Steps) != 2 ||
		report.Steps[0].Status != "SUCCESSFUL" ||
		report.Steps[1].Status != "IN_PROGRESS" ||
		report.Steps[1].PollCount != 1 {
		t.Fatalf("report lost known states after Pace failure: %#v", report)
	}
	if got := len(server.Requests()); got != 4 {
		t.Fatalf("Pace failure sent %d requests, want 4", got)
	}
}

func TestLocalValidationSendsNoTraffic(t *testing.T) {
	var calls atomic.Int32
	transport := roundTripFunc(func(*http.Request) (*http.Response, error) {
		calls.Add(1)
		return nil, errors.New("network must not be reached")
	})
	validConfig := sb.Config{
		BaseURL:     "http://127.0.0.1:39091",
		AccessToken: "local-validation-token",
		HTTPClient:  &http.Client{Transport: transport},
		MaxPolls:    1,
	}
	configTests := []struct {
		name   string
		mutate func(*sb.Config)
	}{
		{name: "relative_url", mutate: func(c *sb.Config) { c.BaseURL = "/relative" }},
		{name: "non_http_url", mutate: func(c *sb.Config) { c.BaseURL = "ftp://127.0.0.1" }},
		{name: "embedded_credentials", mutate: func(c *sb.Config) { c.BaseURL = "http://user@127.0.0.1" }},
		{name: "non_root_path", mutate: func(c *sb.Config) { c.BaseURL = "http://127.0.0.1/api" }},
		{name: "query", mutate: func(c *sb.Config) { c.BaseURL = "http://127.0.0.1?x=1" }},
		{name: "fragment", mutate: func(c *sb.Config) { c.BaseURL = "http://127.0.0.1#x" }},
		{name: "empty_token", mutate: func(c *sb.Config) { c.AccessToken = "" }},
		{name: "token_whitespace", mutate: func(c *sb.Config) { c.AccessToken = "bad token" }},
		{name: "zero_polls", mutate: func(c *sb.Config) { c.MaxPolls = 0 }},
	}
	for _, test := range configTests {
		t.Run("config_"+test.name, func(t *testing.T) {
			config := validConfig
			test.mutate(&config)
			if client, err := sb.NewClient(config); err == nil || client != nil {
				t.Fatalf("NewClient(%+v) = %+v, %v; want local error", config, client, err)
			}
		})
	}

	client, err := sb.NewClient(validConfig)
	if err != nil {
		t.Fatalf("NewClient(valid): %v", err)
	}
	inputTests := []struct {
		name string
		spec sb.BaselineSpec
	}{
		{name: "unset_is_enabled", spec: sb.BaselineSpec{CEIP: sb.CeipUpdateSpec{Status: "ENABLE"}}},
		{name: "enabled_without_host", spec: sb.BaselineSpec{
			Proxy: sb.ProxyConfiguration{IsEnabled: boolPointer(true), Port: int32Pointer(80)},
			CEIP:  sb.CeipUpdateSpec{Status: "ENABLE"},
		}},
		{name: "enabled_without_port", spec: sb.BaselineSpec{
			Proxy: sb.ProxyConfiguration{IsEnabled: boolPointer(true), Host: stringPointer("proxy.example.test")},
			CEIP:  sb.CeipUpdateSpec{Status: "ENABLE"},
		}},
		{name: "invalid_protocol", spec: sb.BaselineSpec{
			Proxy: sb.ProxyConfiguration{
				IsEnabled:        boolPointer(true),
				Host:             stringPointer("proxy.example.test"),
				Port:             int32Pointer(80),
				TransferProtocol: stringPointer("SOCKS"),
			},
			CEIP: sb.CeipUpdateSpec{Status: "ENABLE"},
		}},
		{name: "authenticated_without_credentials", spec: sb.BaselineSpec{
			Proxy: sb.ProxyConfiguration{
				IsEnabled:       boolPointer(true),
				Host:            stringPointer("proxy.example.test"),
				Port:            int32Pointer(80),
				IsAuthenticated: boolPointer(true),
			},
			CEIP: sb.CeipUpdateSpec{Status: "ENABLE"},
		}},
		{name: "invalid_ceip_action", spec: sb.BaselineSpec{
			Proxy: sb.ProxyConfiguration{IsEnabled: boolPointer(false)},
			CEIP:  sb.CeipUpdateSpec{Status: "DISABLED"},
		}},
	}
	for _, test := range inputTests {
		t.Run("input_"+test.name, func(t *testing.T) {
			report, err := client.ApplySystemBaseline(context.Background(), test.spec)
			if err == nil {
				t.Fatalf("ApplySystemBaseline(%+v) unexpectedly succeeded", test.spec)
			}
			if len(report.Steps) != 0 {
				t.Fatalf("invalid input returned steps: %#v", report)
			}
		})
	}
	if got := calls.Load(); got != 0 {
		t.Fatalf("local validation sent %d HTTP requests", got)
	}
}

func TestCanceledContextIsPreserved(t *testing.T) {
	server := newServer(t, contractmock.Plan{})
	client := newClient(t, server, 1, nil)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	report, err := client.ApplySystemBaseline(ctx, disabledBaseline())
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %T %v, want context.Canceled", err, err)
	}
	if len(report.Steps) != 0 || len(server.Requests()) != 0 {
		t.Fatalf("canceled call changed state: report=%#v requests=%#v", report, server.Requests())
	}
}

func TestContractMockRejectsOperationsOutsideFocusedContract(t *testing.T) {
	server := newServer(t, contractmock.Plan{})
	request, err := http.NewRequest(http.MethodPut, server.URL()+"/v1/system/dns-configuration", nil)
	if err != nil {
		t.Fatal(err)
	}
	response, err := server.Client().Do(request)
	if err != nil {
		t.Fatalf("out-of-contract request: %v", err)
	}
	defer response.Body.Close()
	body, _ := io.ReadAll(response.Body)
	if response.StatusCode != http.StatusNotFound ||
		!strings.Contains(string(body), "NOT_IN_CONTRACT") {
		t.Fatalf("out-of-contract response = %d %s", response.StatusCode, body)
	}
	requests := server.Requests()
	if len(requests) != 1 || requests[0].OperationID != "" {
		t.Fatalf("out-of-contract log entry = %#v", requests)
	}
}

func newServer(t *testing.T, plan contractmock.Plan) *contractmock.Server {
	t.Helper()
	server, err := contractmock.New("docs/contract.json", plan)
	if err != nil {
		t.Fatalf("start contract mock: %v", err)
	}
	t.Cleanup(server.Close)
	return server
}

func newClient(
	t *testing.T,
	server *contractmock.Server,
	maxPolls int,
	pace func(context.Context, string, int) error,
) *sb.Client {
	t.Helper()
	client, err := sb.NewClient(sb.Config{
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

func disabledBaseline() sb.BaselineSpec {
	return sb.BaselineSpec{
		Proxy: sb.ProxyConfiguration{IsEnabled: boolPointer(false)},
		CEIP:  sb.CeipUpdateSpec{Status: "DISABLE"},
	}
}

func boolPointer(value bool) *bool       { return &value }
func stringPointer(value string) *string { return &value }
func int32Pointer(value int32) *int32    { return &value }

func assertJSONBody(t *testing.T, body []byte, want any) {
	t.Helper()
	var got any
	if err := json.Unmarshal(body, &got); err != nil {
		t.Fatalf("request body is not JSON: %v; body=%q", err, body)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("JSON wire shape mismatch\n got: %#v\nwant: %#v\nbody: %s", got, want, body)
	}
}

func readJSON(t *testing.T, path string, target any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func assertFileHash(t *testing.T, path, want string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read protected %s: %v", path, err)
	}
	sum := sha256.Sum256(data)
	got := hex.EncodeToString(sum[:])
	if got != want {
		t.Fatalf("protected fixture %s hash = %s, want %s", path, got, want)
	}
}

func sortStrings(values []string) {
	for i := 1; i < len(values); i++ {
		for j := i; j > 0 && values[j] < values[j-1]; j-- {
			values[j], values[j-1] = values[j-1], values[j]
		}
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}
