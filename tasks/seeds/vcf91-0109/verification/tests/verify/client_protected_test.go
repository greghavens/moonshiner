package vcenter_test

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"reflect"
	"testing"
	"time"

	"example.com/vcfasync/internal/mockvcenter"
	"example.com/vcfasync/vcenter"
)

func TestProtectedContractProvenance(t *testing.T) {
	t.Parallel()

	var sources struct {
		Repository  string   `json:"repository"`
		License     string   `json:"license"`
		CommitSHA   string   `json:"commit_sha"`
		SpecPath    string   `json:"spec_path"`
		APIVersion  string   `json:"api_version"`
		OperationID []string `json:"operation_ids"`
	}
	readJSON(t, "../docs/official_sources.json", &sources)

	if sources.Repository != "https://github.com/vmware/vcf-api-specs" {
		t.Errorf("repository = %q", sources.Repository)
	}
	if sources.License != "Apache-2.0" {
		t.Errorf("license = %q", sources.License)
	}
	if sources.CommitSHA != "3949fc33339fc5ea1b77eadb258f1cf49aa88e26" {
		t.Errorf("commit SHA = %q", sources.CommitSHA)
	}
	if sources.SpecPath != "specifications/vsphere/openapi/automation/vcenter.yaml" {
		t.Errorf("spec path = %q", sources.SpecPath)
	}
	if sources.APIVersion != "9.1.0.0" {
		t.Errorf("API version = %q", sources.APIVersion)
	}
	wantOperations := []string{"Vcenter.VM_clone$Task", "Cis.Tasks_get"}
	if !reflect.DeepEqual(sources.OperationID, wantOperations) {
		t.Errorf("operation IDs = %v, want %v", sources.OperationID, wantOperations)
	}

	type contractOperation struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	}
	var contract struct {
		DerivedFrom struct {
			CommitSHA string `json:"commit_sha"`
			SpecPath  string `json:"spec_path"`
		} `json:"derived_from"`
		BasePath   string              `json:"server_base_path"`
		Operations []contractOperation `json:"operations"`
	}
	readJSON(t, "../docs/contract.json", &contract)
	if contract.DerivedFrom.CommitSHA != sources.CommitSHA {
		t.Errorf("contract commit SHA = %q", contract.DerivedFrom.CommitSHA)
	}
	if contract.DerivedFrom.SpecPath != sources.SpecPath {
		t.Errorf("contract spec path = %q", contract.DerivedFrom.SpecPath)
	}
	if contract.BasePath != "/api" {
		t.Errorf("server base path = %q", contract.BasePath)
	}
	wantContract := []contractOperation{
		{"Vcenter.VM_clone$Task", "POST", "/vcenter/vm?action=clone&vmw-task=true"},
		{"Cis.Tasks_get", "GET", "/cis/tasks/{task}"},
	}
	if !reflect.DeepEqual(contract.Operations, wantContract) {
		t.Errorf("contract operations = %#v, want %#v", contract.Operations, wantContract)
	}
	if got := mockvcenter.OperationIDs(); !reflect.DeepEqual(got, wantOperations) {
		t.Errorf("mock operation IDs = %v, want %v", got, wantOperations)
	}
}

func TestProtectedCloneWireAndPolling(t *testing.T) {
	t.Parallel()

	const (
		sessionID = "session-wire-42"
		taskID    = "task-wire-42"
		resultVM  = "vm-result-42"
	)
	server := newMock(t, mockvcenter.Scenario{
		SessionID: sessionID,
		TaskID:    taskID,
		ResultVM:  resultVM,
		Statuses: []mockvcenter.Status{
			mockvcenter.StatusPending,
			mockvcenter.StatusRunning,
			mockvcenter.StatusBlocked,
			mockvcenter.StatusSucceeded,
		},
	})
	client := newClient(t, server, sessionID)

	got, err := client.CloneAndWait(context.Background(), vcenter.CloneSpec{
		Source:        "vm-source-42",
		Name:          "clone-minimal",
		DisksToRemove: []string{},
		DisksToUpdate: map[string]vcenter.DiskCloneSpec{},
	}, 0)
	if err != nil {
		t.Fatalf("CloneAndWait: %v", err)
	}
	if got != resultVM {
		t.Fatalf("result = %q, want %q", got, resultVM)
	}

	requests := server.Requests()
	if len(requests) != 5 {
		t.Fatalf("request count = %d, want 5", len(requests))
	}
	clone := requests[0]
	assertRequest(t, clone, http.MethodPost, "/api/vcenter/vm", "action=clone&vmw-task=true", sessionID)
	if got := clone.Header.Get("Content-Type"); got != "application/json" {
		t.Errorf("clone Content-Type = %q", got)
	}
	var body map[string]any
	if err := json.Unmarshal(clone.Body, &body); err != nil {
		t.Fatalf("decode clone request: %v", err)
	}
	wantBody := map[string]any{
		"source": "vm-source-42",
		"name":   "clone-minimal",
	}
	if !reflect.DeepEqual(body, wantBody) {
		t.Errorf("minimal clone body = %#v, want %#v", body, wantBody)
	}

	for index, request := range requests[1:] {
		assertRequest(t, request, http.MethodGet, "/api/cis/tasks/"+taskID, "", sessionID)
		if len(request.Body) != 0 {
			t.Errorf("poll %d sent a body: %q", index+1, request.Body)
		}
		if request.Header.Get("Content-Type") != "" {
			t.Errorf("poll %d sent Content-Type %q", index+1, request.Header.Get("Content-Type"))
		}
	}
}

func TestProtectedOptionalWireShapes(t *testing.T) {
	t.Parallel()

	powerOff := false
	testCases := []struct {
		name     string
		spec     vcenter.CloneSpec
		wantBody map[string]any
	}{
		{
			name: "explicit false is present",
			spec: vcenter.CloneSpec{
				Source:  "vm-source-options",
				Name:    "clone-power-off",
				PowerOn: &powerOff,
			},
			wantBody: map[string]any{
				"source":   "vm-source-options",
				"name":     "clone-power-off",
				"power_on": false,
			},
		},
		{
			name: "populated nested fields only",
			spec: vcenter.CloneSpec{
				Source: "vm-source-options",
				Name:   "clone-placed",
				Placement: &vcenter.ClonePlacementSpec{
					Folder:    "group-v9",
					Datastore: "datastore-19",
				},
				DisksToUpdate: map[string]vcenter.DiskCloneSpec{
					"2000": {Datastore: "datastore-20"},
				},
				GuestCustomizationSpec: &vcenter.GuestCustomizationSpec{Name: "linux-base"},
			},
			wantBody: map[string]any{
				"source": "vm-source-options",
				"name":   "clone-placed",
				"placement": map[string]any{
					"folder":    "group-v9",
					"datastore": "datastore-19",
				},
				"disks_to_update": map[string]any{
					"2000": map[string]any{
						"datastore": "datastore-20",
					},
				},
				"guest_customization_spec": map[string]any{
					"name": "linux-base",
				},
			},
		},
	}

	for _, testCase := range testCases {
		testCase := testCase
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			server := newMock(t, mockvcenter.Scenario{
				SessionID: "session-options",
				TaskID:    "task-options",
				ResultVM:  "vm-options",
				Statuses:  []mockvcenter.Status{mockvcenter.StatusSucceeded},
			})
			client := newClient(t, server, "session-options")
			if _, err := client.CloneAndWait(context.Background(), testCase.spec, 0); err != nil {
				t.Fatalf("CloneAndWait: %v", err)
			}

			requests := server.Requests()
			if len(requests) != 2 {
				t.Fatalf("request count = %d, want 2", len(requests))
			}
			var body map[string]any
			if err := json.Unmarshal(requests[0].Body, &body); err != nil {
				t.Fatalf("decode body: %v", err)
			}
			if !reflect.DeepEqual(body, testCase.wantBody) {
				t.Errorf("clone body = %#v, want %#v", body, testCase.wantBody)
			}
		})
	}
}

func TestProtectedTerminalAndValidationOutcomes(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name        string
		spec        vcenter.CloneSpec
		statuses    []mockvcenter.Status
		wantErr     bool
		wantCalls   int
		preCanceled bool
	}{
		{
			name:      "failed task",
			spec:      vcenter.CloneSpec{Source: "vm-source", Name: "clone-fails"},
			statuses:  []mockvcenter.Status{mockvcenter.StatusFailed},
			wantErr:   true,
			wantCalls: 2,
		},
		{
			name:      "missing source rejected before HTTP",
			spec:      vcenter.CloneSpec{Name: "clone-invalid"},
			statuses:  []mockvcenter.Status{mockvcenter.StatusSucceeded},
			wantErr:   true,
			wantCalls: 0,
		},
		{
			name:        "pre-canceled context",
			spec:        vcenter.CloneSpec{Source: "vm-source", Name: "clone-canceled"},
			statuses:    []mockvcenter.Status{mockvcenter.StatusSucceeded},
			wantErr:     true,
			wantCalls:   0,
			preCanceled: true,
		},
	}

	for _, testCase := range testCases {
		testCase := testCase
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()

			server := newMock(t, mockvcenter.Scenario{
				SessionID: "session-outcome",
				TaskID:    "task-outcome",
				ResultVM:  "vm-outcome",
				Statuses:  testCase.statuses,
			})
			client := newClient(t, server, "session-outcome")
			ctx := context.Background()
			if testCase.preCanceled {
				canceled, cancel := context.WithCancel(ctx)
				cancel()
				ctx = canceled
			}
			_, err := client.CloneAndWait(ctx, testCase.spec, time.Millisecond)
			if (err != nil) != testCase.wantErr {
				t.Fatalf("error = %v, wantErr %v", err, testCase.wantErr)
			}
			if got := len(server.Requests()); got != testCase.wantCalls {
				t.Errorf("request count = %d, want %d", got, testCase.wantCalls)
			}
		})
	}
}

func TestProtectedMockRejectsOperationsOutsideContract(t *testing.T) {
	t.Parallel()

	server := newMock(t, mockvcenter.Scenario{
		SessionID: "session-narrow",
		TaskID:    "task-narrow",
		ResultVM:  "vm-narrow",
		Statuses:  []mockvcenter.Status{mockvcenter.StatusSucceeded},
	})
	request, err := http.NewRequest(http.MethodGet, server.URL()+"/api/vcenter/vm", nil)
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("vmware-api-session-id", "session-narrow")
	response, err := server.Client().Do(request)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = io.Copy(io.Discard, response.Body)
	_ = response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Errorf("status = %d, want 404", response.StatusCode)
	}
}

func newMock(t *testing.T, scenario mockvcenter.Scenario) *mockvcenter.Server {
	t.Helper()
	server, err := mockvcenter.New(scenario)
	if err != nil {
		t.Fatalf("start mock: %v", err)
	}
	t.Cleanup(server.Close)
	return server
}

func newClient(t *testing.T, server *mockvcenter.Server, sessionID string) *vcenter.Client {
	t.Helper()
	httpClient := server.Client()
	httpClient.Timeout = 2 * time.Second
	client, err := vcenter.NewClient(server.URL(), sessionID, httpClient)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func assertRequest(t *testing.T, request mockvcenter.Request, method, path, rawQuery, sessionID string) {
	t.Helper()
	if request.Method != method {
		t.Errorf("method = %q, want %q", request.Method, method)
	}
	if request.Path != path {
		t.Errorf("path = %q, want %q", request.Path, path)
	}
	if request.RawQuery != rawQuery {
		t.Errorf("raw query = %q, want %q", request.RawQuery, rawQuery)
	}
	if got := request.Header.Get("vmware-api-session-id"); got != sessionID {
		t.Errorf("session header = %q, want %q", got, sessionID)
	}
	if got := request.Header.Get("Accept"); got != "application/json" {
		t.Errorf("Accept = %q, want application/json", got)
	}
}

func readJSON(t *testing.T, path string, destination any) {
	t.Helper()
	content, err := io.ReadAll(mustOpen(t, path))
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	decoder := json.NewDecoder(bytes.NewReader(content))
	if err := decoder.Decode(destination); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func mustOpen(t *testing.T, path string) io.ReadCloser {
	t.Helper()
	file, err := os.Open(path)
	if err != nil {
		t.Fatalf("open %s: %v", path, err)
	}
	return file
}
