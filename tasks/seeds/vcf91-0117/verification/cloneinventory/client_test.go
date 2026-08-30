package cloneinventory_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"vcf91-0117/cloneinventory"
	"vcf91-0117/internal/contractmock"
)

const (
	testSession = "session-secret-vcf91-0117"
	testTimeout = 2 * time.Second
)

func TestCloneAndInventoryPollsToTerminalAndSortsEveryResponse(t *testing.T) {
	cpuTwo := int64(2)
	cpuFour := int64(4)
	memory := int64(8192)
	taskID := "task/β +?%25"
	taskResult := json.RawMessage(`{"vm":"vm-new","generation":17}`)
	baseVMs := []contractmock.VM{
		{VM: "vm-02", Name: "Alpha", PowerState: "POWERED_OFF", CPUCount: &cpuTwo},
		{VM: "vm-10", Name: "Alpha", PowerState: "SUSPENDED", MemorySizeMiB: &memory},
		{VM: "vm-20", Name: "Zeta", PowerState: "POWERED_ON", CPUCount: &cpuFour},
	}
	server := contractmock.Start(t, contractmock.Scenario{
		TaskID:       taskID,
		TaskStatuses: []string{"PENDING", "RUNNING", "BLOCKED", "SUCCEEDED"},
		TaskResult:   taskResult,
		VMs:          baseVMs,
	})
	client := mustClient(t, server.URL(), testSession, 0, 8, nil)
	request := cloneinventory.CloneRequest{
		SourceVM: "source-α\nquoted\"",
		Name:     "nightly clone 雪",
	}

	var results []cloneinventory.CloneInventoryResult
	for run := 0; run < 2; run++ {
		result, err := client.CloneAndInventory(context.Background(), request)
		if err != nil {
			t.Fatalf("run %d: CloneAndInventory() error = %v", run+1, err)
		}
		results = append(results, result)
		if result.TaskID != taskID || result.TaskStatus != "SUCCEEDED" || result.PollCount != 4 {
			t.Fatalf("run %d: terminal evidence = %#v", run+1, result)
		}
		if !jsonEqual(result.TaskResult, taskResult) {
			t.Fatalf("run %d: task result = %s, want %s", run+1, result.TaskResult, taskResult)
		}
		assertSortedVMs(t, result.VMs)
	}
	if !reflect.DeepEqual(results[0].VMs, results[1].VMs) {
		t.Fatalf("flipped server responses produced unstable output:\nfirst: %#v\nsecond: %#v", results[0].VMs, results[1].VMs)
	}

	records := server.Records()
	if len(records) != 12 {
		t.Fatalf("request count = %d, want 12", len(records))
	}
	expectedOperations := []string{
		cloneinventory.CloneTaskOperation,
		cloneinventory.TaskGetOperation,
		cloneinventory.TaskGetOperation,
		cloneinventory.TaskGetOperation,
		cloneinventory.TaskGetOperation,
		cloneinventory.VMListOperation,
	}
	expectedCloneBody, err := json.Marshal(struct {
		Source string `json:"source"`
		Name   string `json:"name"`
	}{Source: request.SourceVM, Name: request.Name})
	if err != nil {
		t.Fatal(err)
	}
	escapedTask := url.PathEscape(taskID)
	for run := 0; run < 2; run++ {
		window := records[run*len(expectedOperations) : (run+1)*len(expectedOperations)]
		for index, operationID := range expectedOperations {
			record := window[index]
			if record.OperationID != operationID {
				t.Fatalf("run %d request %d operation = %q, want %q", run+1, index+1, record.OperationID, operationID)
			}
			assertCommonHeaders(t, record, testSession)
		}
		cloneRecord := window[0]
		if cloneRecord.Method != http.MethodPost ||
			cloneRecord.RequestURI != "/api/vcenter/vm?action=clone&vmw-task=true" ||
			cloneRecord.Body != string(expectedCloneBody) ||
			cloneRecord.ContentLength != int64(len(expectedCloneBody)) {
			t.Fatalf("run %d clone wire record = %#v", run+1, cloneRecord)
		}
		if got := values(cloneRecord, "Content-Type"); !reflect.DeepEqual(got, []string{"application/json"}) {
			t.Fatalf("run %d clone Content-Type = %v", run+1, got)
		}
		for index := 1; index <= 4; index++ {
			taskRecord := window[index]
			if taskRecord.Method != http.MethodGet ||
				taskRecord.RequestURI != "/api/cis/tasks/"+escapedTask {
				t.Fatalf("run %d task request %d = %#v", run+1, index, taskRecord)
			}
			assertBodylessGET(t, taskRecord)
		}
		listRecord := window[5]
		if listRecord.Method != http.MethodGet || listRecord.RequestURI != "/api/vcenter/vm" {
			t.Fatalf("run %d list request = %#v", run+1, listRecord)
		}
		assertBodylessGET(t, listRecord)
	}
}

func TestTerminalFailuresAndPollBoundAreTableDriven(t *testing.T) {
	tests := []struct {
		name       string
		statuses   []string
		maxPolls   int
		wantPolls  int
		wantType   any
		secretText string
	}{
		{
			name:       "failed after running",
			statuses:   []string{"RUNNING", "FAILED"},
			maxPolls:   6,
			wantPolls:  2,
			wantType:   (*cloneinventory.TaskFailedError)(nil),
			secretText: "server-only failure detail",
		},
		{
			name:      "unknown state",
			statuses:  []string{"QUEUED"},
			maxPolls:  6,
			wantPolls: 1,
			wantType:  (*cloneinventory.ProtocolError)(nil),
		},
		{
			name:      "exact poll exhaustion",
			statuses:  []string{"PENDING", "RUNNING", "BLOCKED", "SUCCEEDED"},
			maxPolls:  3,
			wantPolls: 3,
			wantType:  (*cloneinventory.PollLimitError)(nil),
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.Start(t, contractmock.Scenario{
				TaskID:       "task-" + strings.ReplaceAll(test.name, " ", "-"),
				TaskStatuses: test.statuses,
				ErrorText:    test.secretText,
				VMs: []contractmock.VM{
					{VM: "must-not-list", Name: "must-not-list", PowerState: "POWERED_OFF"},
				},
			})
			client := mustClient(t, server.URL(), testSession, 0, test.maxPolls, nil)
			_, err := client.CloneAndInventory(context.Background(), cloneinventory.CloneRequest{
				SourceVM: "vm-source",
				Name:     "clone",
			})
			if err == nil {
				t.Fatal("CloneAndInventory() error = nil")
			}
			switch test.wantType.(type) {
			case *cloneinventory.TaskFailedError:
				var target *cloneinventory.TaskFailedError
				if !errors.As(err, &target) {
					t.Fatalf("error type = %T, want *TaskFailedError", err)
				}
				if target.TaskInfo.Status != "FAILED" {
					t.Fatalf("failed task status = %q", target.TaskInfo.Status)
				}
			case *cloneinventory.ProtocolError:
				var target *cloneinventory.ProtocolError
				if !errors.As(err, &target) {
					t.Fatalf("error type = %T, want *ProtocolError", err)
				}
				if target.OperationID != cloneinventory.TaskGetOperation {
					t.Fatalf("protocol operation = %q", target.OperationID)
				}
			case *cloneinventory.PollLimitError:
				var target *cloneinventory.PollLimitError
				if !errors.As(err, &target) {
					t.Fatalf("error type = %T, want *PollLimitError", err)
				}
				if target.MaxPolls != test.maxPolls {
					t.Fatalf("poll limit = %d, want %d", target.MaxPolls, test.maxPolls)
				}
			}
			for _, formatted := range []string{fmt.Sprintf("%v", err), fmt.Sprintf("%+v", err), fmt.Sprintf("%#v", err)} {
				for _, forbidden := range []string{testSession, test.secretText} {
					if forbidden != "" && strings.Contains(formatted, forbidden) {
						t.Fatalf("error leaked %q: %q", forbidden, formatted)
					}
				}
			}
			records := server.Records()
			if len(records) != test.wantPolls+1 {
				t.Fatalf("request count = %d, want %d", len(records), test.wantPolls+1)
			}
			for _, record := range records {
				if record.OperationID == cloneinventory.VMListOperation {
					t.Fatal("inventory was requested before successful terminal state")
				}
			}
		})
	}
}

func TestMalformedSuccessResponsesAreTableDriven(t *testing.T) {
	validTask := json.RawMessage(`{
		"description":{"id":"clone","default_message":"clone","args":[]},
		"service":"com.vmware.vcenter.vm",
		"operation":"clone",
		"status":"SUCCEEDED",
		"cancelable":false,
		"result":{"vm":"vm-1"}
	}`)
	tests := []struct {
		name          string
		taskBodies    []json.RawMessage
		listBodies    []json.RawMessage
		wantOperation string
	}{
		{
			name: "task description lacks required args",
			taskBodies: []json.RawMessage{json.RawMessage(`{
				"description":{"id":"clone","default_message":"clone"},
				"service":"com.vmware.vcenter.vm",
				"operation":"clone",
				"status":"SUCCEEDED",
				"cancelable":false
			}`)},
			wantOperation: cloneinventory.TaskGetOperation,
		},
		{
			name:          "VM has unknown power state",
			taskBodies:    []json.RawMessage{validTask},
			listBodies:    []json.RawMessage{json.RawMessage(`[{"vm":"vm-1","name":"bad","power_state":"HALTED"}]`)},
			wantOperation: cloneinventory.VMListOperation,
		},
		{
			name:          "optional integer is boolean",
			taskBodies:    []json.RawMessage{validTask},
			listBodies:    []json.RawMessage{json.RawMessage(`[{"vm":"vm-1","name":"bad","power_state":"POWERED_OFF","cpu_count":true}]`)},
			wantOperation: cloneinventory.VMListOperation,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.Start(t, contractmock.Scenario{
				TaskID:     "task-malformed",
				TaskBodies: test.taskBodies,
				ListBodies: test.listBodies,
			})
			client := mustClient(t, server.URL(), testSession, 0, 3, nil)
			_, err := client.CloneAndInventory(context.Background(), cloneinventory.CloneRequest{
				SourceVM: "source",
				Name:     "clone",
			})
			var protocolError *cloneinventory.ProtocolError
			if !errors.As(err, &protocolError) {
				t.Fatalf("error = %v (%T), want *ProtocolError", err, err)
			}
			if protocolError.OperationID != test.wantOperation {
				t.Fatalf("protocol operation = %q, want %q", protocolError.OperationID, test.wantOperation)
			}
		})
	}
}

func TestValidationHappensBeforeTraffic(t *testing.T) {
	server := contractmock.Start(t, contractmock.Scenario{
		TaskID:       "unused-task",
		TaskStatuses: []string{"SUCCEEDED"},
	})
	validConfig := cloneinventory.Config{
		BaseURL:      server.URL(),
		SessionID:    testSession,
		Timeout:      testTimeout,
		PollInterval: 0,
		MaxPolls:     3,
	}
	configTests := []struct {
		name   string
		mutate func(*cloneinventory.Config)
	}{
		{"relative URL", func(config *cloneinventory.Config) { config.BaseURL = "/relative" }},
		{"credentials in URL", func(config *cloneinventory.Config) { config.BaseURL = "http://user:pass@127.0.0.1" }},
		{"query in URL", func(config *cloneinventory.Config) { config.BaseURL += "?x=1" }},
		{"fragment in URL", func(config *cloneinventory.Config) { config.BaseURL += "#x" }},
		{"non-root path", func(config *cloneinventory.Config) { config.BaseURL += "/api" }},
		{"blank session", func(config *cloneinventory.Config) { config.SessionID = " \t" }},
		{"unsafe session", func(config *cloneinventory.Config) { config.SessionID = "secret\r\nX: y" }},
		{"zero timeout", func(config *cloneinventory.Config) { config.Timeout = 0 }},
		{"negative interval", func(config *cloneinventory.Config) { config.PollInterval = -time.Nanosecond }},
		{"zero polls", func(config *cloneinventory.Config) { config.MaxPolls = 0 }},
	}
	for _, test := range configTests {
		t.Run(test.name, func(t *testing.T) {
			config := validConfig
			test.mutate(&config)
			if _, err := cloneinventory.NewClient(config); err == nil {
				t.Fatal("NewClient() error = nil")
			}
		})
	}

	client := mustClient(t, server.URL(), testSession, 0, 3, nil)
	callTests := []struct {
		name    string
		context context.Context
		request cloneinventory.CloneRequest
	}{
		{"nil context", nil, cloneinventory.CloneRequest{SourceVM: "source", Name: "clone"}},
		{"blank source", context.Background(), cloneinventory.CloneRequest{SourceVM: "\n ", Name: "clone"}},
		{"blank name", context.Background(), cloneinventory.CloneRequest{SourceVM: "source", Name: "\t"}},
	}
	for _, test := range callTests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := client.CloneAndInventory(test.context, test.request); err == nil {
				t.Fatal("CloneAndInventory() error = nil")
			}
		})
	}
	if records := server.Records(); len(records) != 0 {
		t.Fatalf("invalid inputs made %d requests", len(records))
	}
}

func TestHTTPAndTransportErrorsAreStructuredAndRedacted(t *testing.T) {
	t.Run("HTTP error", func(t *testing.T) {
		serverText := "server payload " + testSession
		server := contractmock.Start(t, contractmock.Scenario{
			TaskID:       "task-http-error",
			TaskStatuses: []string{"SUCCEEDED"},
			CloneStatus:  http.StatusInternalServerError,
			ErrorType:    "INTERNAL_SERVER_ERROR",
			ErrorText:    serverText,
		})
		client := mustClient(t, server.URL(), testSession, 0, 3, nil)
		_, err := client.CloneAndInventory(context.Background(), cloneinventory.CloneRequest{
			SourceVM: "source",
			Name:     "clone",
		})
		var apiError *cloneinventory.APIError
		if !errors.As(err, &apiError) {
			t.Fatalf("error = %v (%T), want *APIError", err, err)
		}
		if apiError.OperationID != cloneinventory.CloneTaskOperation ||
			apiError.StatusCode != http.StatusInternalServerError ||
			apiError.ErrorType != "INTERNAL_SERVER_ERROR" ||
			len(apiError.Messages) != 1 {
			t.Fatalf("APIError = %#v", apiError)
		}
		for _, text := range []string{fmt.Sprintf("%v", err), fmt.Sprintf("%+v", err), fmt.Sprintf("%#v", err)} {
			if strings.Contains(text, testSession) || strings.Contains(text, serverText) {
				t.Fatalf("HTTP error string leaked protected data: %q", text)
			}
		}
	})

	t.Run("transport error", func(t *testing.T) {
		transportSecret := "dial detail " + testSession
		httpClient := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
			return nil, errors.New(transportSecret)
		})}
		client := mustClient(t, "http://127.0.0.1:1", testSession, 0, 3, httpClient)
		_, err := client.CloneAndInventory(context.Background(), cloneinventory.CloneRequest{
			SourceVM: "source",
			Name:     "clone",
		})
		var transportError *cloneinventory.TransportError
		if !errors.As(err, &transportError) {
			t.Fatalf("error = %v (%T), want *TransportError", err, err)
		}
		for _, text := range []string{fmt.Sprintf("%v", err), fmt.Sprintf("%+v", err), fmt.Sprintf("%#v", err)} {
			if strings.Contains(text, testSession) || strings.Contains(text, transportSecret) {
				t.Fatalf("transport error string leaked protected data: %q", text)
			}
		}
	})
}

func TestCancellationRemainsDiscoverable(t *testing.T) {
	server := contractmock.Start(t, contractmock.Scenario{
		TaskID:       "task-cancel",
		TaskStatuses: []string{"PENDING"},
	})
	client := mustClient(t, server.URL(), testSession, time.Second, 4, nil)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := client.CloneAndInventory(ctx, cloneinventory.CloneRequest{
		SourceVM: "source",
		Name:     "clone",
	})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want errors.Is(context.Canceled)", err)
	}
	if records := server.Records(); len(records) != 0 {
		t.Fatalf("pre-canceled context made %d requests", len(records))
	}
}

func TestNewClientDoesNotMutateCallerHTTPClient(t *testing.T) {
	redirect := func(*http.Request, []*http.Request) error { return nil }
	caller := &http.Client{
		Timeout:       77 * time.Second,
		CheckRedirect: redirect,
	}
	client, err := cloneinventory.NewClient(cloneinventory.Config{
		BaseURL:      "http://127.0.0.1:1",
		SessionID:    testSession,
		HTTPClient:   caller,
		Timeout:      testTimeout,
		PollInterval: 0,
		MaxPolls:     3,
	})
	if err != nil || client == nil {
		t.Fatalf("NewClient() = %v, %v", client, err)
	}
	if caller.Timeout != 77*time.Second {
		t.Fatalf("caller timeout mutated to %s", caller.Timeout)
	}
	if reflect.ValueOf(caller.CheckRedirect).Pointer() != reflect.ValueOf(redirect).Pointer() {
		t.Fatal("caller redirect policy was mutated")
	}
}

func TestContractMockRejectsUnnamedOperations(t *testing.T) {
	server := contractmock.Start(t, contractmock.Scenario{
		TaskID:       "task-unused",
		TaskStatuses: []string{"SUCCEEDED"},
	})
	response, err := http.Get(server.URL() + "/api/session")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("unnamed operation status = %d, want 404", response.StatusCode)
	}
	records := server.Records()
	if len(records) != 1 || records[0].OperationID != "" {
		t.Fatalf("unnamed operation log = %#v", records)
	}
}

func mustClient(
	t testing.TB,
	baseURL string,
	sessionID string,
	pollInterval time.Duration,
	maxPolls int,
	httpClient *http.Client,
) *cloneinventory.Client {
	t.Helper()
	client, err := cloneinventory.NewClient(cloneinventory.Config{
		BaseURL:      baseURL,
		SessionID:    sessionID,
		HTTPClient:   httpClient,
		Timeout:      testTimeout,
		PollInterval: pollInterval,
		MaxPolls:     maxPolls,
	})
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}
	return client
}

func assertSortedVMs(t testing.TB, vms []cloneinventory.VMSummary) {
	t.Helper()
	if len(vms) != 3 {
		t.Fatalf("VM count = %d, want 3", len(vms))
	}
	if !sort.SliceIsSorted(vms, func(left, right int) bool {
		if vms[left].Name == vms[right].Name {
			return vms[left].VM < vms[right].VM
		}
		return vms[left].Name < vms[right].Name
	}) {
		t.Fatalf("VMs are not sorted by (Name, VM): %#v", vms)
	}
	gotKeys := []string{
		vms[0].Name + "/" + vms[0].VM,
		vms[1].Name + "/" + vms[1].VM,
		vms[2].Name + "/" + vms[2].VM,
	}
	wantKeys := []string{"Alpha/vm-02", "Alpha/vm-10", "Zeta/vm-20"}
	if !reflect.DeepEqual(gotKeys, wantKeys) {
		t.Fatalf("sorted VM keys = %v, want %v", gotKeys, wantKeys)
	}
}

func assertCommonHeaders(t testing.TB, record contractmock.RequestRecord, session string) {
	t.Helper()
	if values := values(record, "Accept"); !reflect.DeepEqual(values, []string{"application/json"}) {
		t.Fatalf("%s Accept = %v", record.OperationID, values)
	}
	if values := values(record, "vmware-api-session-id"); !reflect.DeepEqual(values, []string{session}) {
		t.Fatalf("%s session header = %v", record.OperationID, values)
	}
	if values := values(record, "Authorization"); len(values) != 0 {
		t.Fatalf("%s sent Authorization = %v", record.OperationID, values)
	}
}

func assertBodylessGET(t testing.TB, record contractmock.RequestRecord) {
	t.Helper()
	if record.Body != "" || record.ContentLength != 0 {
		t.Fatalf("%s GET body/content length = %q/%d", record.OperationID, record.Body, record.ContentLength)
	}
	if values := values(record, "Content-Type"); len(values) != 0 {
		t.Fatalf("%s GET Content-Type = %v", record.OperationID, values)
	}
	if values := values(record, "Content-Length"); len(values) != 0 {
		t.Fatalf("%s GET Content-Length header = %v", record.OperationID, values)
	}
	if strings.Contains(record.RequestURI, "?") {
		t.Fatalf("%s GET unexpectedly has query: %q", record.OperationID, record.RequestURI)
	}
}

func values(record contractmock.RequestRecord, name string) []string {
	return record.Header[http.CanonicalHeaderKey(name)]
}

func jsonEqual(left, right []byte) bool {
	var leftValue any
	var rightValue any
	if json.Unmarshal(left, &leftValue) != nil || json.Unmarshal(right, &rightValue) != nil {
		return false
	}
	return reflect.DeepEqual(leftValue, rightValue)
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}
