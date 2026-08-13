package vcfinstaller_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	installer "example.com/vcfinstaller"
	"example.com/vcfinstaller/internal/contractmock"
)

func pointer[T any](value T) *T { return &value }

func TestDownloadBundleWireAndPolling(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name       string
		bundleID   string
		spec       installer.BundleDownloadSpec
		statuses   []string
		wantBody   string
		wantStatus string
	}{
		{
			name:       "download now omits other optionals and polls past two nonterminal states",
			bundleID:   "bundle/alpha?revision#1% done",
			spec:       installer.BundleDownloadSpec{DownloadNow: pointer(true)},
			statuses:   []string{"PENDING", "Pending", "IN_PROGRESS", "SUCCESSFUL"},
			wantBody:   `{"bundleDownloadSpec":{"downloadNow":true}}`,
			wantStatus: "SUCCESSFUL",
		},
		{
			name:       "scheduled timestamp omits unset booleans and accepts display statuses",
			bundleID:   "bundle beta",
			spec:       installer.BundleDownloadSpec{ScheduledTimestamp: pointer("2025-01-24T10:00:00Z")},
			statuses:   []string{"In Progress", "Successful"},
			wantBody:   `{"bundleDownloadSpec":{"scheduledTimestamp":"2025-01-24T10:00:00Z"}}`,
			wantStatus: "Successful",
		},
		{
			name:       "explicit false is present while unset peers remain omitted",
			bundleID:   "bundle-false",
			spec:       installer.BundleDownloadSpec{CancelNow: pointer(false)},
			statuses:   []string{"SKIPPED"},
			wantBody:   `{"bundleDownloadSpec":{"cancelNow":false}}`,
			wantStatus: "SKIPPED",
		},
		{
			name:       "all unset optional fields produce an empty nested object",
			bundleID:   "bundle-empty",
			spec:       installer.BundleDownloadSpec{},
			statuses:   []string{"COMPLETED_WITH_WARNING"},
			wantBody:   `{"bundleDownloadSpec":{}}`,
			wantStatus: "COMPLETED_WITH_WARNING",
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server := contractmock.New(test.statuses)
			defer server.Close()

			client, err := installer.NewClient(server.URL, server.Client(), 0)
			if err != nil {
				t.Fatalf("NewClient() error = %v", err)
			}
			got, err := client.DownloadBundle(context.Background(), test.bundleID, test.spec)
			if err != nil {
				t.Fatalf("DownloadBundle() error = %v", err)
			}
			wantTaskID := contractmock.TaskIDPrefix + test.bundleID
			if got.ID != wantTaskID || got.Status != test.wantStatus {
				t.Fatalf("DownloadBundle() task = %#v, want id %q and status %q", got, wantTaskID, test.wantStatus)
			}

			requests := server.Requests()
			if len(requests) != 1+len(test.statuses) {
				t.Fatalf("request count = %d, want %d; requests = %#v", len(requests), 1+len(test.statuses), requests)
			}
			wantPatchURI := "/v1/bundles/" + escapePathSegment(test.bundleID)
			assertRequest(t, requests[0], http.MethodPatch, wantPatchURI, "application/json", test.wantBody)
			for index, request := range requests[1:] {
				assertRequest(t, request, http.MethodGet, "/v1/tasks/"+escapePathSegment(wantTaskID), "", "")
				if index >= len(test.statuses) {
					t.Fatalf("unexpected extra poll %d", index)
				}
			}
		})
	}
}

func TestDownloadBundleTerminalErrorsReturnFinalTask(t *testing.T) {
	t.Parallel()

	for _, terminal := range []string{"FAILED", "Failed", "CANCELLED", "Cancelled"} {
		terminal := terminal
		t.Run(terminal, func(t *testing.T) {
			t.Parallel()
			server := contractmock.New([]string{terminal})
			defer server.Close()
			client, err := installer.NewClient(server.URL, server.Client(), 0)
			if err != nil {
				t.Fatalf("NewClient() error = %v", err)
			}

			got, err := client.DownloadBundle(context.Background(), "error-bundle", installer.BundleDownloadSpec{})
			if err == nil {
				t.Fatal("DownloadBundle() error = nil, want terminal task error")
			}
			if got.ID != contractmock.TaskIDPrefix+"error-bundle" || got.Status != terminal {
				t.Fatalf("DownloadBundle() task = %#v, want final status %q", got, terminal)
			}
			if requests := server.Requests(); len(requests) != 2 {
				t.Fatalf("request count = %d, want start plus one terminal poll", len(requests))
			}
		})
	}
}

func TestDownloadBundleHonorsContextDuringPollInterval(t *testing.T) {
	server := contractmock.New([]string{"IN_PROGRESS"})
	defer server.Close()
	client, err := installer.NewClient(server.URL, server.Client(), time.Millisecond)
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	result := make(chan struct {
		task installer.Task
		err  error
	}, 1)
	go func() {
		task, err := client.DownloadBundle(ctx, "cancelled-context", installer.BundleDownloadSpec{})
		result <- struct {
			task installer.Task
			err  error
		}{task: task, err: err}
	}()
	<-server.PollObserved()
	cancel()
	got := <-result
	err = got.err
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("DownloadBundle() error = %v, want context.Canceled", err)
	}
	if got.task.ID != contractmock.TaskIDPrefix+"cancelled-context" {
		t.Fatalf("DownloadBundle() task = %#v, want last observed task", got.task)
	}
	if requests := server.Requests(); len(requests) < 2 {
		t.Fatalf("request count = %d, want a start and at least one poll", len(requests))
	}
}

func TestClientRejectsNonContractTaskResponses(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name      string
		startCode int
		getCode   int
		body      string
	}{
		{name: "wrong HTTP status", startCode: http.StatusOK, getCode: http.StatusAccepted, body: `{"id":"task","name":"Task","status":"PENDING","creationTimestamp":"2026-08-13T12:00:00Z"}`},
		{name: "malformed JSON", startCode: http.StatusAccepted, getCode: http.StatusOK, body: `{`},
		{name: "trailing JSON value", startCode: http.StatusAccepted, getCode: http.StatusOK, body: `{"id":"task","name":"Task","status":"PENDING","creationTimestamp":"2026-08-13T12:00:00Z"}{}`},
		{name: "missing required task fields", startCode: http.StatusAccepted, getCode: http.StatusOK, body: `{"id":"task","status":"PENDING"}`},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			operations := []struct {
				name       string
				statusCode int
				call       func(*installer.Client) error
			}{
				{
					name:       "startBundleDownloadByID",
					statusCode: test.startCode,
					call: func(client *installer.Client) error {
						_, err := client.StartBundleDownload(context.Background(), "bundle", installer.BundleDownloadSpec{})
						return err
					},
				},
				{
					name:       "getTask",
					statusCode: test.getCode,
					call: func(client *installer.Client) error {
						_, err := client.GetTask(context.Background(), "task")
						return err
					},
				},
			}
			for _, operation := range operations {
				operation := operation
				t.Run(operation.name, func(t *testing.T) {
					server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
						w.WriteHeader(operation.statusCode)
						_, _ = w.Write([]byte(test.body))
					}))
					defer server.Close()
					client, err := installer.NewClient(server.URL, server.Client(), 0)
					if err != nil {
						t.Fatalf("NewClient() error = %v", err)
					}
					if err := operation.call(client); err == nil {
						t.Fatalf("%s error = nil, want contract response error", operation.name)
					}
				})
			}
		})
	}
}

func TestContractMockRejectsOperationsOutsideProtectedContract(t *testing.T) {
	server := contractmock.New([]string{"SUCCESSFUL"})
	defer server.Close()

	tests := []struct {
		method string
		path   string
	}{
		{method: http.MethodPost, path: "/v1/bundles/bundle"},
		{method: http.MethodGet, path: "/v1/system"},
		{method: http.MethodPatch, path: "/v1/bundles/bundle/extra"},
		{method: http.MethodGet, path: "/v1/tasks/not-created"},
	}
	for _, test := range tests {
		request, err := http.NewRequest(test.method, server.URL+test.path, nil)
		if err != nil {
			t.Fatal(err)
		}
		response, err := server.Client().Do(request)
		if err != nil {
			t.Fatal(err)
		}
		_ = response.Body.Close()
		if response.StatusCode != http.StatusNotFound {
			t.Fatalf("%s %s status = %d, want 404", test.method, test.path, response.StatusCode)
		}
	}
	if got := len(server.Requests()); got != len(tests) {
		t.Fatalf("request log length = %d, want %d", got, len(tests))
	}
}

func TestProtectedContractAndSourcePin(t *testing.T) {
	contractBytes, err := os.ReadFile("docs/contract.json")
	if err != nil {
		t.Fatal(err)
	}
	var contract struct {
		OpenAPI string `json:"openapi"`
		Info    struct {
			Version string `json:"version"`
		} `json:"info"`
		Operations map[string]struct {
			Method    string `json:"method"`
			Path      string `json:"path"`
			Operation struct {
				OperationID string `json:"operationId"`
			} `json:"operation"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(contractBytes, &contract); err != nil {
		t.Fatalf("decode contract: %v", err)
	}
	if contract.OpenAPI != "3.0.1" || contract.Info.Version != "9.0.0.0" || len(contract.Operations) != 2 {
		t.Fatalf("unexpected contract identity: %#v", contract)
	}
	wantOperations := map[string][2]string{
		contractmock.StartBundleDownloadOperationID: {http.MethodPatch, "/v1/bundles/{id}"},
		contractmock.GetTaskOperationID:             {http.MethodGet, "/v1/tasks/{id}"},
	}
	for operationID, want := range wantOperations {
		operation, ok := contract.Operations[operationID]
		if !ok || operation.Operation.OperationID != operationID || operation.Method != want[0] || operation.Path != want[1] {
			t.Fatalf("operation %q = %#v, want method/path %v", operationID, operation, want)
		}
	}

	sourcesBytes, err := os.ReadFile("docs/official_sources.json")
	if err != nil {
		t.Fatal(err)
	}
	var sources struct {
		Tag          string   `json:"tag"`
		CommitSHA    string   `json:"commit_sha"`
		SpecPath     string   `json:"spec_path"`
		OperationIDs []string `json:"operation_ids"`
	}
	if err := json.Unmarshal(sourcesBytes, &sources); err != nil {
		t.Fatalf("decode official sources: %v", err)
	}
	if sources.Tag != "9.0.0.0" || sources.CommitSHA != "85151f6b1bb58f13b6ac0304bfec53904bea085f" || sources.SpecPath != "specifications/vcf-installer/vcf-installer-openapi.json" || !reflect.DeepEqual(sources.OperationIDs, []string{"startBundleDownloadByID", "getTask"}) {
		t.Fatalf("unexpected official source pin: %#v", sources)
	}
}

func assertRequest(t *testing.T, got contractmock.RecordedRequest, method, requestURI, contentType, body string) {
	t.Helper()
	if got.Method != method || got.RequestURI != requestURI || got.ContentType != contentType || string(got.Body) != body {
		t.Fatalf("request = {method:%q uri:%q content-type:%q body:%q}, want {method:%q uri:%q content-type:%q body:%q}", got.Method, got.RequestURI, got.ContentType, got.Body, method, requestURI, contentType, body)
	}
}

func escapePathSegment(value string) string {
	replacer := strings.NewReplacer("%", "%25", "/", "%2F", " ", "%20", "?", "%3F", "#", "%23")
	return replacer.Replace(value)
}
