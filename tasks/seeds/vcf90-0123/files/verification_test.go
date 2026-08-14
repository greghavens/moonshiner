package vsandp_test

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"sync"
	"testing"
	"time"

	vsandp "vcf90-0123"
	"vcf90-0123/internal/mockdp"
)

const (
	createSnapshotOperationID = "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task"
	getTaskOperationID        = "Snapservice.Tasks_get"
	specPath                  = "specifications/vsan-data-protection/vsan-data-protection-openapi.yaml"
	commitSHA                 = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
)

type tokenSource struct {
	mu           sync.Mutex
	initial      string
	refreshed    string
	tokenCalls   int
	refreshCalls int
}

func (s *tokenSource) Token(context.Context) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.tokenCalls++
	return s.initial, nil
}

func (s *tokenSource) Refresh(context.Context) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.refreshCalls++
	return s.refreshed, nil
}

func (s *tokenSource) counts() (int, int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.tokenCalls, s.refreshCalls
}

func TestCreateSnapshotAndWaitWireContract(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		spec     vsandp.CreateSnapshotSpec
		body     string
		statuses []string
	}{
		{
			name:     "unset retention is omitted",
			spec:     vsandp.CreateSnapshotSpec{Name: "nightly"},
			body:     `{"name":"nightly"}`,
			statuses: []string{"RUNNING", "SUCCEEDED"},
		},
		{
			name: "set retention has the specification shape",
			spec: vsandp.CreateSnapshotSpec{
				Name: "manual",
				Retention: &vsandp.RetentionPeriod{
					Unit:     "HOUR",
					Duration: 24,
				},
			},
			body:     `{"name":"manual","retention":{"unit":"HOUR","duration":24}}`,
			statuses: []string{"PENDING", "BLOCKED", "SUCCEEDED"},
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()

			mock := mockdp.New(mockdp.Config{
				InitialToken:   "session-old",
				RefreshedToken: "session-new",
				TaskID:         "task/77",
				TaskStatuses:   test.statuses,
			})
			defer mock.Close()

			tokens := &tokenSource{initial: "session-old", refreshed: "session-new"}
			client := vsandp.NewClient(mock.URL(), mock.Client(), tokens)
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()

			result, err := client.CreateSnapshotAndWait(ctx, "cluster/blue", "pg one", test.spec)
			if err != nil {
				t.Fatalf("CreateSnapshotAndWait returned error: %v", err)
			}
			if result.TaskID != "task/77" || result.Task.Status != "SUCCEEDED" {
				t.Fatalf("result = %#v; want original task id and SUCCEEDED", result)
			}
			if mock.CreatedSnapshots() != 1 {
				t.Fatalf("accepted snapshot creates = %d; want exactly 1", mock.CreatedSnapshots())
			}
			tokenCalls, refreshCalls := tokens.counts()
			if tokenCalls != 1 || refreshCalls != 1 {
				t.Fatalf("token calls = (%d initial, %d refresh); want (1, 1)", tokenCalls, refreshCalls)
			}

			createURI := "/api/snapservice/clusters/cluster%2Fblue/protection-groups/pg%20one/snapshots?vmw-task=true"
			taskURI := "/api/snapservice/tasks/task%2F77"
			want := []mockdp.Request{
				{
					Method:      http.MethodPost,
					RequestURI:  createURI,
					SessionID:   "session-old",
					ContentType: "application/json",
					Accept:      "application/json",
					Body:        test.body,
				},
				{
					Method:     http.MethodGet,
					RequestURI: taskURI,
					SessionID:  "session-old",
					Accept:     "application/json",
				},
			}
			for range test.statuses {
				want = append(want, mockdp.Request{
					Method:     http.MethodGet,
					RequestURI: taskURI,
					SessionID:  "session-new",
					Accept:     "application/json",
				})
			}

			if got := mock.Requests(); !reflect.DeepEqual(got, want) {
				t.Fatalf("request log mismatch\n got: %#v\nwant: %#v", got, want)
			}
		})
	}
}

func TestFailedTaskReturnsErrorWithoutCreatingAgain(t *testing.T) {
	t.Parallel()

	mock := mockdp.New(mockdp.Config{
		InitialToken:   "session-old",
		RefreshedToken: "session-new",
		TaskID:         "task-failed",
		TaskStatuses:   []string{"FAILED"},
	})
	defer mock.Close()
	tokens := &tokenSource{initial: "session-old", refreshed: "session-new"}
	client := vsandp.NewClient(mock.URL(), mock.Client(), tokens)

	result, err := client.CreateSnapshotAndWait(
		context.Background(),
		"cluster-1",
		"pg-1",
		vsandp.CreateSnapshotSpec{Name: "manual"},
	)
	if err == nil {
		t.Fatal("FAILED task returned nil error")
	}
	if result.TaskID != "task-failed" || result.Task.Status != "FAILED" {
		t.Fatalf("failed result = %#v; want task id and FAILED info", result)
	}
	if mock.CreatedSnapshots() != 1 {
		t.Fatalf("accepted snapshot creates = %d; want exactly 1", mock.CreatedSnapshots())
	}
}

func TestMockRejectsOperationsOutsidePinnedContract(t *testing.T) {
	t.Parallel()

	mock := mockdp.New(mockdp.Config{})
	defer mock.Close()
	response, err := mock.Client().Get(mock.URL() + "/snapservice/info/about")
	if err != nil {
		t.Fatalf("loopback request failed: %v", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("unsupported operation status = %d; want 404", response.StatusCode)
	}
}

func TestPinnedSpecificationProvenance(t *testing.T) {
	t.Parallel()

	type operation struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	}
	type contractDocument struct {
		OpenAPI        string `json:"openapi"`
		APIVersion     string `json:"api_version"`
		ServerBasePath string `json:"server_base_path"`
		Authentication struct {
			Type string `json:"type"`
			In   string `json:"in"`
			Name string `json:"name"`
		} `json:"authentication"`
		Operations []operation `json:"operations"`
	}
	type sourcesDocument struct {
		Repository string      `json:"repository"`
		License    string      `json:"license"`
		Tag        string      `json:"tag"`
		CommitSHA  string      `json:"commit_sha"`
		SpecPath   string      `json:"spec_path"`
		Operations []operation `json:"operations"`
	}

	var contract contractDocument
	readJSON(t, "docs/contract.json", &contract)
	var sources sourcesDocument
	readJSON(t, "docs/official_sources.json", &sources)

	wantOperations := []operation{
		{
			OperationID: createSnapshotOperationID,
			Method:      http.MethodPost,
			Path:        "/snapservice/clusters/{cluster}/protection-groups/{pg}/snapshots?vmw-task=true",
		},
		{
			OperationID: getTaskOperationID,
			Method:      http.MethodGet,
			Path:        "/snapservice/tasks/{task}",
		},
	}
	if contract.OpenAPI != "3.0.3" || contract.APIVersion != "9.0.0.0" || contract.ServerBasePath != "/api" {
		t.Fatalf("wrong OpenAPI identity: %#v", contract)
	}
	if contract.Authentication.Type != "apiKey" || contract.Authentication.In != "header" || contract.Authentication.Name != "vmware-api-session-id" {
		t.Fatalf("wrong authentication contract: %#v", contract.Authentication)
	}
	if !reflect.DeepEqual(contract.Operations, wantOperations) {
		t.Fatalf("contract operations = %#v; want %#v", contract.Operations, wantOperations)
	}
	if sources.Repository != "https://github.com/vmware/vcf-api-specs" ||
		sources.License != "Apache-2.0" ||
		sources.Tag != "9.0.0.0" ||
		sources.CommitSHA != commitSHA ||
		sources.SpecPath != specPath {
		t.Fatalf("wrong official source pin: %#v", sources)
	}
	if !reflect.DeepEqual(sources.Operations, wantOperations) {
		t.Fatalf("source operations = %#v; want %#v", sources.Operations, wantOperations)
	}
	wantMockIDs := []string{createSnapshotOperationID, getTaskOperationID}
	if got := mockdp.OperationIDs(); !reflect.DeepEqual(got, wantMockIDs) {
		t.Fatalf("mock operation ids = %q; want %q", got, wantMockIDs)
	}
}

func readJSON(t *testing.T, path string, destination any) {
	t.Helper()
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(body, destination); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func ExampleCreateSnapshotSpec_optionalRetention() {
	spec := vsandp.CreateSnapshotSpec{Name: "nightly"}
	body, _ := json.Marshal(spec)
	fmt.Println(string(body))
	// Output: {"name":"nightly"}
}
