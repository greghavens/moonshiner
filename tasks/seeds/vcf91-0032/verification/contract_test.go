package taskdiagnosis_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"

	td "vcf91-0032"
	"vcf91-0032/internal/contractmock"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedSpec   = "specifications/sddc-manager/sddc-manager-openapi.json"
	contractSHA256 = "e2f7989fdd1d85f0ec8a415a002e907cbba7d7169be776ca4b18be66ef9c4af6"
	sourcesSHA256  = "b984a79188a906f06070ec9e14217741b2a01efe39454ab187494a390fe1606b"
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
				Type       string `json:"type"`
				ReadOnly   bool   `json:"readOnly"`
				Deprecated bool   `json:"deprecated"`
				Ref        string `json:"$ref"`
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
		{OperationID: "getTask", Method: "GET", Path: "/v1/tasks/{id}"},
		{OperationID: "getNotifications", Method: "GET", Path: "/v1/notifications"},
		{OperationID: "startSupportBundle", Method: "POST", Path: "/v1/system/support-bundles"},
		{OperationID: "getSupportBundleStatus", Method: "GET", Path: "/v1/system/support-bundles/{id}"},
		{OperationID: "exportSupportBundleByID", Method: "GET", Path: "/v1/system/support-bundles/{id}/data"},
	}
	if !reflect.DeepEqual(contract.Operations, wantOperations) ||
		!reflect.DeepEqual(sources.Operations, wantOperations) {
		t.Fatalf("operation provenance mismatch\ncontract: %#v\nsources: %#v\nwant: %#v",
			contract.Operations, sources.Operations, wantOperations)
	}

	spec := contract.Schemas["SupportBundleSpec"]
	if len(spec.Required) != 0 ||
		sortedKeys(spec.Properties) != "logs,options,scope" {
		t.Fatalf("SupportBundleSpec optional-property projection mismatch: %+v", spec)
	}
	logs := contract.Schemas["Logs"]
	if len(logs.Required) != 0 ||
		sortedKeys(logs.Properties) != "apiLogs,automationLogs,esxLogs,hcxLogs,lifecycleLogs,nsxLogs,operationsForLogs,operationsLogs,sddcManagerLogs,systemDebugLogs,vcLogs,vmScreenshots,vmsLogs,vraLogs,vrliLogs,vropsLogs,vrslcmLogs,wcpLogs" ||
		!logs.Properties["vraLogs"].Deprecated ||
		logs.Properties["apiLogs"].Type != "boolean" ||
		logs.Properties["sddcManagerLogs"].Type != "boolean" {
		t.Fatalf("Logs schema projection mismatch: %+v", logs)
	}
	task := contract.Schemas["Task"]
	if !reflect.DeepEqual(
		task.Required,
		[]string{"creationTimestamp", "id", "name", "status"},
	) ||
		!task.Properties["id"].ReadOnly ||
		!task.Properties["errors"].ReadOnly ||
		!task.Properties["resources"].ReadOnly {
		t.Fatalf("Task projection mismatch: %+v", task)
	}
	resource := contract.Schemas["Resource"]
	if !reflect.DeepEqual(resource.Required, []string{"resourceId", "type"}) {
		t.Fatalf("Resource required fields = %v", resource.Required)
	}
}

func TestDiagnoseCorrelatesTaskEventsAndLogsWithExactWire(t *testing.T) {
	const cause = "certificate thumbprint changed on the target vCenter"
	server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
		plan := validPlan(runtime, cause)
		plan.StartStatus = "COMPLETED_WITH_SUCCESS"
		plan.BundlePolls = []contractmock.BundleReply{
			{Status: "IN_PROGRESS"},
			{Status: "COMPLETED_WITH_SUCCESS"},
		}
		plan.Notifications = append(
			[]contractmock.Notification{{
				Type:     "RESOURCE_HEALTH",
				Severity: "WARNING",
				Message: contractmock.Message{
					ID:               "unrelated-event",
					LocalizedMessage: "another resource changed state",
				},
				Resources: []contractmock.NotifiableResource{{
					ID:   "another-resource",
					Type: "VCENTER",
					Name: "unrelated.example.test",
				}},
			}},
			plan.Notifications...,
		)
		plan.ArchiveFiles = []contractmock.ArchiveFile{
			{
				Name: "manifest.txt",
				Data: []byte("support bundle generated by protected fixture\n"),
			},
			{
				Name: "logs/api/api.log",
				Data: []byte(
					evidenceLine(runtime.TaskID, runtime.ReferenceToken, "unrelated-event", "wrong resource event") +
						evidenceLine(runtime.TaskID, "wrong-reference", runtime.EventID, "wrong reference") +
						evidenceLine(runtime.TaskID, runtime.ReferenceToken, runtime.EventID, cause),
				),
			},
		}
		return plan
	})
	runtime := server.Runtime()

	var paceMu sync.Mutex
	var paceCalls []int
	client := newClient(t, server, 4, func(
		ctx context.Context,
		operationID string,
		completedPolls int,
	) error {
		if operationID != "getSupportBundleStatus" {
			t.Errorf("Pace operationId = %q", operationID)
		}
		paceMu.Lock()
		paceCalls = append(paceCalls, completedPolls)
		paceMu.Unlock()
		return ctx.Err()
	})

	report, err := client.DiagnoseTaskFailure(context.Background(), runtime.TaskID)
	if err != nil {
		t.Fatalf("DiagnoseTaskFailure returned %T: %v", err, err)
	}
	if report.Task.ID != runtime.TaskID ||
		report.Task.Status != " Failed " ||
		report.Bundle.ID != runtime.BundleID ||
		report.Bundle.Status != "COMPLETED_WITH_SUCCESS" ||
		report.Cause != cause ||
		report.EvidencePath != "logs/api/api.log" ||
		report.EventID != runtime.EventID {
		t.Fatalf("report lost correlated evidence: %+v", report)
	}
	if len(report.RelevantEvents) != 1 ||
		report.RelevantEvents[0].Message.ID != runtime.EventID ||
		report.RelevantEvents[0].Resources[0].ID != runtime.ResourceID {
		t.Fatalf("relevant events were not filtered in server order: %+v", report.RelevantEvents)
	}

	requests := server.Requests()
	wantOperations := []string{
		"getTask",
		"getNotifications",
		"startSupportBundle",
		"getSupportBundleStatus",
		"getSupportBundleStatus",
		"exportSupportBundleByID",
	}
	wantMethods := []string{"GET", "GET", "POST", "GET", "GET", "GET"}
	wantPaths := []string{
		"/v1/tasks/" + runtime.TaskID,
		"/v1/notifications",
		"/v1/system/support-bundles",
		"/v1/system/support-bundles/" + runtime.BundleID,
		"/v1/system/support-bundles/" + runtime.BundleID,
		"/v1/system/support-bundles/" + runtime.BundleID + "/data",
	}
	if len(requests) != len(wantOperations) {
		t.Fatalf("request count = %d, want %d: %#v", len(requests), len(wantOperations), requests)
	}
	if !strings.Contains(requests[0].EscapedPath, "task%20id%2Ftask-") {
		t.Fatalf("task ID was not path-escaped as one segment: %+v", requests[0])
	}
	for index, request := range requests {
		if request.OperationID != wantOperations[index] ||
			request.Method != wantMethods[index] ||
			request.Path != wantPaths[index] ||
			request.RawQuery != "" ||
			request.ForceQuery {
			t.Fatalf("request %d target mismatch: %+v", index, request)
		}
		if !reflect.DeepEqual(
			request.Header.Values("Authorization"),
			[]string{"Bearer " + runtime.AccessToken},
		) {
			t.Fatalf("request %d Authorization = %#v", index, request.Header.Values("Authorization"))
		}
		wantAccept := "application/json"
		if index == len(requests)-1 {
			wantAccept = "application/octet-stream"
		}
		if !reflect.DeepEqual(request.Header.Values("Accept"), []string{wantAccept}) {
			t.Fatalf("request %d Accept = %#v, want %q", index, request.Header.Values("Accept"), wantAccept)
		}
		if request.Method == http.MethodPost {
			if !reflect.DeepEqual(
				request.Header.Values("Content-Type"),
				[]string{"application/json"},
			) ||
				request.ContentLength != int64(len(request.Body)) ||
				len(request.TransferEncoding) != 0 {
				t.Fatalf("POST entity metadata mismatch: %+v", request)
			}
		} else if len(request.Header.Values("Content-Type")) != 0 ||
			len(request.Body) != 0 ||
			request.ContentLength != 0 ||
			len(request.TransferEncoding) != 0 {
			t.Fatalf("GET %d unexpectedly carried an entity: %+v", index, request)
		}
	}
	wantBody := `{"logs":{"sddcManagerLogs":true,"apiLogs":true}}`
	if string(requests[2].Body) != wantBody {
		t.Fatalf("support bundle bytes = %q, want %q", requests[2].Body, wantBody)
	}
	assertJSONBody(t, requests[2].Body, map[string]any{
		"logs": map[string]any{
			"sddcManagerLogs": true,
			"apiLogs":         true,
		},
	})
	for _, absent := range []string{
		`"options"`, `"scope"`, `"vcLogs"`, `"nsxLogs"`, `"esxLogs"`,
		`"hcxLogs"`, `"wcpLogs"`, `"systemDebugLogs"`, `"vmScreenshots"`,
		`"vraLogs"`, `"vropsLogs"`, `"vrliLogs"`, `"vrslcmLogs"`,
		`"automationLogs"`, `"operationsLogs"`, `"operationsForLogs"`,
		`"lifecycleLogs"`, `"vmsLogs"`, "null",
	} {
		if strings.Contains(string(requests[2].Body), absent) {
			t.Fatalf("unset optional field %s appeared on wire: %s", absent, requests[2].Body)
		}
	}

	paceMu.Lock()
	gotPace := append([]int(nil), paceCalls...)
	paceMu.Unlock()
	if !reflect.DeepEqual(gotPace, []int{1}) {
		t.Fatalf("Pace calls = %v, want [1]", gotPace)
	}
}

func TestWorkflowGatesAndPollingAreTableDriven(t *testing.T) {
	tests := []struct {
		name         string
		maxPolls     int
		mutate       func(contractmock.RuntimeValues, *contractmock.Plan)
		wantError    any
		wantRequests int
	}{
		{
			name: "non-failed task stops before notifications",
			mutate: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.Task.Status = "SUCCESSFUL"
			},
			wantError:    &td.TaskStateError{},
			wantRequests: 1,
		},
		{
			name: "failed task needs a reference token",
			mutate: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.Task.Errors = []contractmock.VCFError{{Message: "generic failure"}}
			},
			wantError:    &td.ProtocolError{},
			wantRequests: 1,
		},
		{
			name: "events must share a task resource",
			mutate: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.Notifications[0].Resources[0].ID = "other-resource"
			},
			wantError:    &td.EvidenceError{},
			wantRequests: 2,
		},
		{
			name: "accepted bundle is always polled",
			mutate: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.StartStatus = "COMPLETED_WITH_SUCCESS"
				plan.BundlePolls = []contractmock.BundleReply{{
					Status: "COMPLETED_WITH_FAILURE",
				}}
			},
			wantError:    &td.BundleTerminalError{},
			wantRequests: 4,
		},
		{
			name: "unknown bundle status is protocol failure",
			mutate: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.BundlePolls = []contractmock.BundleReply{{Status: "SUCCESSFUL"}}
			},
			wantError:    &td.ProtocolError{},
			wantRequests: 4,
		},
		{
			name:     "pending bundle is bounded",
			maxPolls: 2,
			mutate: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.BundlePolls = []contractmock.BundleReply{
					{Status: "PENDING"},
					{Status: "IN_PROGRESS"},
				}
			},
			wantError:    &td.PollLimitError{},
			wantRequests: 5,
		},
		{
			name: "uncorrelated logs never become a cause",
			mutate: func(runtime contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.ArchiveFiles = []contractmock.ArchiveFile{{
					Name: "logs/sddc-manager.log",
					Data: []byte(evidenceLine(
						runtime.TaskID,
						"different-reference",
						runtime.EventID,
						"plausible but uncorrelated cause",
					)),
				}}
			},
			wantError:    &td.EvidenceError{},
			wantRequests: 5,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
				plan := validPlan(runtime, "correlated cause")
				if test.mutate != nil {
					test.mutate(runtime, &plan)
				}
				return plan
			})
			maxPolls := test.maxPolls
			if maxPolls == 0 {
				maxPolls = 3
			}
			client := newClient(t, server, maxPolls, nil)
			_, err := client.DiagnoseTaskFailure(
				context.Background(),
				server.Runtime().TaskID,
			)
			if err == nil || !errorAsType(err, test.wantError) {
				t.Fatalf("error = %T %v, want %T", err, err, test.wantError)
			}
			if len(server.Requests()) != test.wantRequests {
				t.Fatalf("request count = %d, want %d: %#v",
					len(server.Requests()), test.wantRequests, server.Requests())
			}
		})
	}
}

func TestPaceFailureStopsBeforeAnotherPoll(t *testing.T) {
	server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
		plan := validPlan(runtime, "unused")
		plan.BundlePolls = []contractmock.BundleReply{
			{Status: "IN_PROGRESS"},
			{Status: "COMPLETED_WITH_SUCCESS"},
		}
		return plan
	})
	paceErr := errors.New("maintenance window ended")
	client := newClient(t, server, 3, func(
		context.Context,
		string,
		int,
	) error {
		return paceErr
	})
	report, err := client.DiagnoseTaskFailure(
		context.Background(),
		server.Runtime().TaskID,
	)
	if !errors.Is(err, paceErr) {
		t.Fatalf("error = %T %v, want Pace error", err, err)
	}
	if report.Task.ID != server.Runtime().TaskID ||
		len(report.RelevantEvents) != 1 ||
		report.Bundle.Status != "IN_PROGRESS" {
		t.Fatalf("partial report lost retrieved evidence: %+v", report)
	}
	if len(server.Requests()) != 4 {
		t.Fatalf("Pace failure did not stop traffic: %#v", server.Requests())
	}
}

func TestOperationAPIErrorsAreTableDrivenAndRedacted(t *testing.T) {
	tests := []struct {
		name         string
		operationID  string
		wantRequests int
		mutate       func(*contractmock.Plan)
	}{
		{
			name:         "get task",
			operationID:  "getTask",
			wantRequests: 1,
			mutate: func(plan *contractmock.Plan) {
				plan.TaskHTTPStatus = http.StatusInternalServerError
				plan.TaskAPIError = secretAPIError()
			},
		},
		{
			name:         "get notifications",
			operationID:  "getNotifications",
			wantRequests: 2,
			mutate: func(plan *contractmock.Plan) {
				plan.NotificationsHTTPStatus = http.StatusInternalServerError
				plan.NotificationsAPIError = secretAPIError()
			},
		},
		{
			name:         "start support bundle",
			operationID:  "startSupportBundle",
			wantRequests: 3,
			mutate: func(plan *contractmock.Plan) {
				plan.StartHTTPStatus = http.StatusConflict
				plan.StartAPIError = secretAPIError()
			},
		},
		{
			name:         "get support bundle status",
			operationID:  "getSupportBundleStatus",
			wantRequests: 4,
			mutate: func(plan *contractmock.Plan) {
				plan.BundlePolls = []contractmock.BundleReply{{
					HTTPStatus: http.StatusInternalServerError,
					APIError:   secretAPIError(),
				}}
			},
		},
		{
			name:         "export support bundle",
			operationID:  "exportSupportBundleByID",
			wantRequests: 5,
			mutate: func(plan *contractmock.Plan) {
				plan.ExportHTTPStatus = http.StatusNotFound
				plan.ExportAPIError = secretAPIError()
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
				plan := validPlan(runtime, "secret evidence cause")
				test.mutate(&plan)
				return plan
			})
			runtime := server.Runtime()
			client := newClient(t, server, 2, nil)
			_, err := client.DiagnoseTaskFailure(context.Background(), runtime.TaskID)
			var apiError *td.APIError
			if !errors.As(err, &apiError) {
				t.Fatalf("error = %T %v, want *APIError", err, err)
			}
			if apiError.OperationID != test.operationID ||
				apiError.ErrorCode != "SECRET_CODE" ||
				apiError.Message != "secret server message" ||
				apiError.RemediationMessage != "secret remediation" ||
				apiError.ReferenceToken != "secret-api-reference" {
				t.Fatalf("API error lost decoded fields: %+v", apiError)
			}
			for _, secret := range []string{
				runtime.AccessToken,
				"secret server message",
				"secret remediation",
				"secret-api-reference",
				runtime.ReferenceToken,
			} {
				if strings.Contains(err.Error(), secret) {
					t.Fatalf("error string exposed %q: %v", secret, err)
				}
			}
			if len(server.Requests()) != test.wantRequests {
				t.Fatalf("request count = %d, want %d", len(server.Requests()), test.wantRequests)
			}
		})
	}
}

func TestTransportErrorDoesNotExposeItsCause(t *testing.T) {
	server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
		return validPlan(runtime, "unused")
	})
	runtime := server.Runtime()
	client := newClient(t, server, 1, nil)
	server.Close()

	_, err := client.DiagnoseTaskFailure(context.Background(), runtime.TaskID)
	var transportError *td.TransportError
	if !errors.As(err, &transportError) ||
		transportError.OperationID != "getTask" ||
		transportError.Cause == nil {
		t.Fatalf("error = %T %v, want getTask TransportError", err, err)
	}
	causeText := transportError.Cause.Error()
	if strings.Contains(err.Error(), runtime.AccessToken) ||
		(causeText != "" && strings.Contains(err.Error(), causeText)) {
		t.Fatalf("transport error exposed token or cause: %v", err)
	}
}

func TestArchiveSafetyLimitsAreTableDriven(t *testing.T) {
	manyFiles := make([]contractmock.ArchiveFile, 65)
	for index := range manyFiles {
		manyFiles[index] = contractmock.ArchiveFile{
			Name: fmt.Sprintf("logs/%03d.log", index),
			Data: []byte("{}\n"),
		}
	}
	expandedFiles := make([]contractmock.ArchiveFile, 5)
	for index := range expandedFiles {
		expandedFiles[index] = contractmock.ArchiveFile{
			Name: fmt.Sprintf("logs/expanded-%d.txt", index),
			Data: bytesOf('x', 1<<20),
		}
	}
	tests := []struct {
		name  string
		files []contractmock.ArchiveFile
		raw   []byte
	}{
		{
			name: "malformed gzip",
			raw:  []byte("not a gzip archive"),
		},
		{
			name: "path traversal",
			files: []contractmock.ArchiveFile{{
				Name: "../outside.log",
				Data: []byte("{}\n"),
			}},
		},
		{
			name:  "entry count",
			files: manyFiles,
		},
		{
			name: "per file expansion",
			files: []contractmock.ArchiveFile{{
				Name: "logs/too-large.log",
				Data: bytesOf('x', (1<<20)+1),
			}},
		},
		{
			name:  "total expansion",
			files: expandedFiles,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
				plan := validPlan(runtime, "unused")
				plan.ArchiveFiles = test.files
				plan.RawArchive = test.raw
				return plan
			})
			client := newClient(t, server, 2, nil)
			_, err := client.DiagnoseTaskFailure(
				context.Background(),
				server.Runtime().TaskID,
			)
			var protocolError *td.ProtocolError
			if !errors.As(err, &protocolError) ||
				protocolError.OperationID != "exportSupportBundleByID" {
				t.Fatalf("error = %T %v, want export ProtocolError", err, err)
			}
		})
	}
}

func TestConstructionAndLocalValidationAreTableDriven(t *testing.T) {
	base := td.Config{
		BaseURL:     "http://127.0.0.1:8080",
		AccessToken: "token",
		MaxPolls:    1,
	}
	tests := []struct {
		name   string
		mutate func(*td.Config)
	}{
		{
			name: "unsupported scheme",
			mutate: func(config *td.Config) {
				config.BaseURL = "ftp://127.0.0.1"
			},
		},
		{
			name: "credentials",
			mutate: func(config *td.Config) {
				config.BaseURL = "http://user:pass@127.0.0.1"
			},
		},
		{
			name: "non-root path",
			mutate: func(config *td.Config) {
				config.BaseURL += "/api"
			},
		},
		{
			name: "query",
			mutate: func(config *td.Config) {
				config.BaseURL += "?x=1"
			},
		},
		{
			name: "fragment",
			mutate: func(config *td.Config) {
				config.BaseURL += "#fragment"
			},
		},
		{
			name: "blank token",
			mutate: func(config *td.Config) {
				config.AccessToken = " "
			},
		},
		{
			name: "whitespace token",
			mutate: func(config *td.Config) {
				config.AccessToken = "secret token"
			},
		},
		{
			name: "zero polls",
			mutate: func(config *td.Config) {
				config.MaxPolls = 0
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			config := base
			test.mutate(&config)
			if client, err := td.NewClient(config); err == nil || client != nil {
				t.Fatalf("NewClient() = (%v, %v), want validation error", client, err)
			}
		})
	}

	server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
		return validPlan(runtime, "unused")
	})
	client := newClient(t, server, 1, nil)
	for _, taskID := range []string{"", " ", "\ttask", "task "} {
		if _, err := client.DiagnoseTaskFailure(context.Background(), taskID); err == nil {
			t.Fatalf("task ID %q passed validation", taskID)
		}
		if len(server.Requests()) != 0 {
			t.Fatalf("invalid task ID caused traffic: %#v", server.Requests())
		}
	}
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := client.DiagnoseTaskFailure(cancelled, server.Runtime().TaskID); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled context error = %T %v", err, err)
	}
	if _, err := client.DiagnoseTaskFailure(nil, server.Runtime().TaskID); err == nil {
		t.Fatal("nil context passed validation")
	}
	if len(server.Requests()) != 0 {
		t.Fatalf("invalid context caused traffic: %#v", server.Requests())
	}
}

func TestMalformedSuccessAndMediaTypesAreRejected(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(contractmock.RuntimeValues, *contractmock.Plan)
		wantOp string
	}{
		{
			name: "task ID mismatch",
			mutate: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.Task.ID = "different-task"
			},
			wantOp: "getTask",
		},
		{
			name: "empty accepted bundle ID",
			mutate: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				empty := ""
				plan.StartBundleID = &empty
			},
			wantOp: "startSupportBundle",
		},
		{
			name: "archive media type",
			mutate: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.ExportContentType = "text/plain"
			},
			wantOp: "exportSupportBundleByID",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
				plan := validPlan(runtime, "cause")
				test.mutate(runtime, &plan)
				return plan
			})
			client := newClient(t, server, 2, nil)
			_, err := client.DiagnoseTaskFailure(
				context.Background(),
				server.Runtime().TaskID,
			)
			var protocolError *td.ProtocolError
			if !errors.As(err, &protocolError) || protocolError.OperationID != test.wantOp {
				t.Fatalf("error = %T %v, want %s ProtocolError", err, err, test.wantOp)
			}
		})
	}
}

func validPlan(
	runtime contractmock.RuntimeValues,
	cause string,
) contractmock.Plan {
	return contractmock.Plan{
		Task: contractmock.Task{
			ID:                runtime.TaskID,
			Name:              "Expand workload domain",
			Type:              "DOMAIN_EXPANSION",
			Status:            " Failed ",
			CreationTimestamp: "2026-05-13T11:59:00Z",
			Errors: []contractmock.VCFError{{
				ErrorCode:      "VCF_OPERATION_FAILED",
				Message:        "the operation could not be completed",
				ReferenceToken: runtime.ReferenceToken,
			}},
			Resources: []contractmock.Resource{{
				ResourceID: runtime.ResourceID,
				FQDN:       "vc01.example.test",
				Type:       "VCENTER",
				Name:       "vc01",
			}},
		},
		Notifications: []contractmock.Notification{{
			Type:     "RESOURCE_HEALTH",
			Severity: "CRITICAL",
			Message: contractmock.Message{
				ID:               runtime.EventID,
				LocalizedMessage: "target vCenter trust changed",
			},
			CreationTimestamp: "2026-05-13T11:59:30Z",
			Resources: []contractmock.NotifiableResource{{
				ID:   runtime.ResourceID,
				Type: "VCENTER",
				Name: "vc01.example.test",
			}},
		}},
		BundlePolls: []contractmock.BundleReply{{
			Status: "COMPLETED_WITH_SUCCESS",
		}},
		ArchiveFiles: []contractmock.ArchiveFile{{
			Name: "logs/sddc-manager/domainmanager.log",
			Data: []byte(evidenceLine(
				runtime.TaskID,
				runtime.ReferenceToken,
				runtime.EventID,
				cause,
			)),
		}},
	}
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

func newClient(
	t *testing.T,
	server *contractmock.Server,
	maxPolls int,
	pace td.PaceFunc,
) *td.Client {
	t.Helper()
	client, err := td.NewClient(td.Config{
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

func evidenceLine(taskID, referenceToken, eventID, cause string) string {
	value := map[string]string{
		"taskId":         taskID,
		"referenceToken": referenceToken,
		"eventId":        eventID,
		"cause":          cause,
	}
	data, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	return string(data) + "\n"
}

func secretAPIError() contractmock.VCFError {
	return contractmock.VCFError{
		ErrorCode:          "SECRET_CODE",
		Message:            "secret server message",
		RemediationMessage: "secret remediation",
		ReferenceToken:     "secret-api-reference",
	}
}

func errorAsType(err error, target any) bool {
	switch target.(type) {
	case *td.TaskStateError:
		var got *td.TaskStateError
		return errors.As(err, &got)
	case *td.ProtocolError:
		var got *td.ProtocolError
		return errors.As(err, &got)
	case *td.EvidenceError:
		var got *td.EvidenceError
		return errors.As(err, &got)
	case *td.BundleTerminalError:
		var got *td.BundleTerminalError
		return errors.As(err, &got)
	case *td.PollLimitError:
		var got *td.PollLimitError
		return errors.As(err, &got)
	default:
		return false
	}
}

func bytesOf(value byte, count int) []byte {
	data := make([]byte, count)
	for index := range data {
		data[index] = value
	}
	return data
}

func assertJSONBody(t *testing.T, body []byte, want any) {
	t.Helper()
	var got any
	if err := json.Unmarshal(body, &got); err != nil {
		t.Fatalf("invalid JSON body %q: %v", body, err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("JSON body = %#v, want %#v", got, want)
	}
}

func sortedKeys[V any](values map[string]V) string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	for left := 0; left < len(keys); left++ {
		for right := left + 1; right < len(keys); right++ {
			if keys[right] < keys[left] {
				keys[left], keys[right] = keys[right], keys[left]
			}
		}
	}
	return strings.Join(keys, ",")
}

func readJSON(t *testing.T, path string, output any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, output); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func assertFileHash(t *testing.T, path string, want string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read protected file %s: %v", path, err)
	}
	sum := sha256.Sum256(data)
	got := hex.EncodeToString(sum[:])
	if got != want {
		t.Fatalf("protected file %s hash = %s, want %s", path, got, want)
	}
}
