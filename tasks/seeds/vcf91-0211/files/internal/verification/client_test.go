package verification_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"example.com/vcf-installer-task-collector/internal/contractmock"
	"example.com/vcf-installer-task-collector/vcfinstaller"
)

const (
	pinnedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	specPath     = "specifications/vcf-installer/vcf-installer-openapi.json"
	testToken    = "local-test-token"
)

type metadata struct {
	PageNumber    int `json:"pageNumber"`
	PageSize      int `json:"pageSize"`
	TotalElements int `json:"totalElements"`
	TotalPages    int `json:"totalPages"`
}

type page struct {
	Elements     []vcfinstaller.Task `json:"elements"`
	PageMetadata metadata            `json:"pageMetadata"`
}

func protectedPath(parts ...string) string {
	all := append([]string{"..", ".."}, parts...)
	return filepath.Join(all...)
}

func pointer[T any](value T) *T { return &value }

func TestPinnedContractProvenanceAndMockSurface(t *testing.T) {
	t.Parallel()

	var sources struct {
		Repository   string   `json:"repository"`
		License      string   `json:"license"`
		CommitSHA    string   `json:"commitSha"`
		SpecPath     string   `json:"specPath"`
		SpecVersion  string   `json:"specVersion"`
		OperationIDs []string `json:"operationIds"`
		Operations   []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
			Source      string `json:"source"`
		} `json:"operations"`
	}
	data, err := io.ReadAll(mustOpen(t, protectedPath("docs", "official_sources.json")))
	if err != nil {
		t.Fatalf("read official sources: %v", err)
	}
	if err := json.Unmarshal(data, &sources); err != nil {
		t.Fatalf("decode official sources: %v", err)
	}
	if sources.Repository != "https://github.com/vmware/vcf-api-specs" ||
		sources.License != "Apache-2.0" || sources.CommitSHA != pinnedCommit ||
		sources.SpecPath != specPath || sources.SpecVersion != "9.1.0.0" {
		t.Fatalf("unexpected source provenance: %+v", sources)
	}
	if !reflect.DeepEqual(sources.OperationIDs, []string{"getTasks"}) || len(sources.Operations) != 1 {
		t.Fatalf("unexpected operation records: ids=%v records=%+v", sources.OperationIDs, sources.Operations)
	}
	op := sources.Operations[0]
	if op.OperationID != "getTasks" || op.Method != http.MethodGet || op.Path != "/v1/tasks" ||
		!strings.Contains(op.Source, pinnedCommit+"/"+specPath) {
		t.Fatalf("operation source is not pinned to the specification: %+v", op)
	}

	var responderCalls atomic.Int32
	server := contractmock.New(t, protectedPath("docs", "contract.json"), func(contractmock.Request) contractmock.Response {
		responderCalls.Add(1)
		return contractmock.JSONResponse(t, http.StatusOK, page{
			Elements:     []vcfinstaller.Task{},
			PageMetadata: metadata{PageNumber: 0, PageSize: 0, TotalElements: 0, TotalPages: 0},
		})
	})
	if server.OperationID() != "getTasks" {
		t.Fatalf("mock loaded operation %q", server.OperationID())
	}
	method, path := server.Route()
	if method != http.MethodGet || path != "/v1/tasks" {
		t.Fatalf("mock loaded route %s %s", method, path)
	}
	parsed, err := url.Parse(server.URL())
	if err != nil || parsed.Hostname() != "127.0.0.1" {
		t.Fatalf("mock is not IPv4 loopback-only: %q (%v)", server.URL(), err)
	}

	response, err := http.Get(server.URL() + "/v1/not-in-contract")
	if err != nil {
		t.Fatalf("call rejected route: %v", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("route absent from contract returned %d", response.StatusCode)
	}
	if responderCalls.Load() != 0 {
		t.Fatalf("contract responder served an unnamed operation")
	}
	if len(server.Requests()) != 1 {
		t.Fatalf("request log did not capture the rejected route")
	}
}

func TestListAllTasksWirePaginationAndStableOrder(t *testing.T) {
	t.Parallel()

	type successCase struct {
		name        string
		options     vcfinstaller.ListTasksOptions
		pages       map[int]page
		want        []vcfinstaller.Task
		wantTargets []string
	}

	firstType := "SDDC_INSTALL"
	secondType := "DEPOT_SYNC"
	allFilters := vcfinstaller.ListTasksOptions{
		PageSize:       2,
		Limit:          pointer(7),
		TaskStatus:     pointer("IN_PROGRESS"),
		TaskType:       pointer("SDDC_INSTALL"),
		ResourceID:     pointer("domain/blue"),
		ResourceType:   pointer("DOMAIN"),
		CompletedAfter: pointer(int64(0)),
		OrderDirection: pointer("ASC"),
		OrderBy:        pointer("creationTimestamp"),
		TaskName:       pointer("install & patch"),
		DoLiveRefresh:  pointer(false),
	}

	cases := []successCase{
		{
			name:    "three pages omit all unset options",
			options: vcfinstaller.ListTasksOptions{PageSize: 2},
			pages: map[int]page{
				0: {
					Elements: []vcfinstaller.Task{
						{ID: "task-d", Name: "fourth", Status: "SUCCESSFUL", CreationTimestamp: "2026-08-02T04:00:00Z"},
						{ID: "task-a", Name: "first", Type: &firstType, Status: "PENDING", CreationTimestamp: "2026-08-02T01:00:00Z"},
					},
					PageMetadata: metadata{PageNumber: 0, PageSize: 2, TotalElements: 5, TotalPages: 3},
				},
				1: {
					Elements: []vcfinstaller.Task{
						{ID: "task-c", Name: "third", Status: "FAILED", CreationTimestamp: "2026-08-02T03:00:00Z"},
						{ID: "task-e", Name: "tie-second", Status: "IN_PROGRESS", CreationTimestamp: "2026-08-02T02:00:00Z"},
					},
					PageMetadata: metadata{PageNumber: 1, PageSize: 2, TotalElements: 5, TotalPages: 3},
				},
				2: {
					Elements: []vcfinstaller.Task{
						{ID: "task-b", Name: "tie-first", Type: &secondType, Status: "QUEUED", CreationTimestamp: "2026-08-02T02:00:00Z"},
					},
					PageMetadata: metadata{PageNumber: 2, PageSize: 1, TotalElements: 5, TotalPages: 3},
				},
			},
			want: []vcfinstaller.Task{
				{ID: "task-a", Name: "first", Type: &firstType, Status: "PENDING", CreationTimestamp: "2026-08-02T01:00:00Z"},
				{ID: "task-b", Name: "tie-first", Type: &secondType, Status: "QUEUED", CreationTimestamp: "2026-08-02T02:00:00Z"},
				{ID: "task-e", Name: "tie-second", Status: "IN_PROGRESS", CreationTimestamp: "2026-08-02T02:00:00Z"},
				{ID: "task-c", Name: "third", Status: "FAILED", CreationTimestamp: "2026-08-02T03:00:00Z"},
				{ID: "task-d", Name: "fourth", Status: "SUCCESSFUL", CreationTimestamp: "2026-08-02T04:00:00Z"},
			},
			wantTargets: []string{
				"/v1/tasks?pageSize=2",
				"/v1/tasks?pageNumber=1&pageSize=2",
				"/v1/tasks?pageNumber=2&pageSize=2",
			},
		},
		{
			name:    "explicit false zero and filters are preserved",
			options: allFilters,
			pages: map[int]page{
				0: {
					Elements: []vcfinstaller.Task{
						{ID: "task-z", Name: "later", Status: "SUCCESSFUL", CreationTimestamp: "2026-08-02T12:00:00Z"},
						{ID: "task-y", Name: "earlier", Status: "SUCCESSFUL", CreationTimestamp: "2026-08-02T11:00:00Z"},
					},
					PageMetadata: metadata{PageNumber: 0, PageSize: 2, TotalElements: 3, TotalPages: 2},
				},
				1: {
					Elements: []vcfinstaller.Task{
						{ID: "task-x", Name: "earliest", Status: "SUCCESSFUL", CreationTimestamp: "2026-08-02T10:00:00Z"},
					},
					PageMetadata: metadata{PageNumber: 1, PageSize: 1, TotalElements: 3, TotalPages: 2},
				},
			},
			want: []vcfinstaller.Task{
				{ID: "task-x", Name: "earliest", Status: "SUCCESSFUL", CreationTimestamp: "2026-08-02T10:00:00Z"},
				{ID: "task-y", Name: "earlier", Status: "SUCCESSFUL", CreationTimestamp: "2026-08-02T11:00:00Z"},
				{ID: "task-z", Name: "later", Status: "SUCCESSFUL", CreationTimestamp: "2026-08-02T12:00:00Z"},
			},
			wantTargets: []string{
				"/v1/tasks?completedAfter=0&doLiveRefresh=false&limit=7&orderBy=creationTimestamp&orderDirection=ASC&pageSize=2&resourceId=domain%2Fblue&resourceType=DOMAIN&taskName=install+%26+patch&taskStatus=IN_PROGRESS&taskType=SDDC_INSTALL",
				"/v1/tasks?completedAfter=0&doLiveRefresh=false&limit=7&orderBy=creationTimestamp&orderDirection=ASC&pageNumber=1&pageSize=2&resourceId=domain%2Fblue&resourceType=DOMAIN&taskName=install+%26+patch&taskStatus=IN_PROGRESS&taskType=SDDC_INSTALL",
			},
		},
		{
			name: "explicit empty strings and integer zero are preserved",
			options: vcfinstaller.ListTasksOptions{
				PageSize:       1,
				Limit:          pointer(0),
				TaskStatus:     pointer(""),
				TaskType:       pointer(""),
				ResourceID:     pointer(""),
				ResourceType:   pointer(""),
				OrderDirection: pointer(""),
				OrderBy:        pointer(""),
				TaskName:       pointer(""),
			},
			pages: map[int]page{
				0: {
					Elements: []vcfinstaller.Task{
						{ID: "task-empty", Name: "explicit empties", Status: "PENDING", CreationTimestamp: "2026-08-02T01:00:00Z"},
					},
					PageMetadata: metadata{PageNumber: 0, PageSize: 1, TotalElements: 1, TotalPages: 1},
				},
			},
			want: []vcfinstaller.Task{
				{ID: "task-empty", Name: "explicit empties", Status: "PENDING", CreationTimestamp: "2026-08-02T01:00:00Z"},
			},
			wantTargets: []string{
				"/v1/tasks?limit=0&orderBy=&orderDirection=&pageSize=1&resourceId=&resourceType=&taskName=&taskStatus=&taskType=",
			},
		},
		{
			name:    "raw timestamp and case-sensitive ID ordering",
			options: vcfinstaller.ListTasksOptions{PageSize: 4},
			pages: map[int]page{
				0: {
					Elements: []vcfinstaller.Task{
						{ID: "a-task", Name: "same lower", Status: "PENDING", CreationTimestamp: "2026-08-02T05:00:00Z"},
						{ID: "later-in-time", Name: "raw first", Status: "PENDING", CreationTimestamp: "2026-08-02T01:00:00-05:00"},
						{ID: "A-task", Name: "same upper", Status: "PENDING", CreationTimestamp: "2026-08-02T05:00:00Z"},
					},
					PageMetadata: metadata{PageNumber: 0, PageSize: 3, TotalElements: 3, TotalPages: 1},
				},
			},
			want: []vcfinstaller.Task{
				{ID: "later-in-time", Name: "raw first", Status: "PENDING", CreationTimestamp: "2026-08-02T01:00:00-05:00"},
				{ID: "A-task", Name: "same upper", Status: "PENDING", CreationTimestamp: "2026-08-02T05:00:00Z"},
				{ID: "a-task", Name: "same lower", Status: "PENDING", CreationTimestamp: "2026-08-02T05:00:00Z"},
			},
			wantTargets: []string{"/v1/tasks?pageSize=4"},
		},
	}

	for _, test := range cases {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server := contractmock.New(t, protectedPath("docs", "contract.json"), func(request contractmock.Request) contractmock.Response {
				pageNumber, err := contractmock.PageNumber(request)
				if err != nil {
					return contractmock.JSONResponse(t, http.StatusBadRequest, map[string]string{"errorCode": "BAD_PAGE", "message": err.Error()})
				}
				document, ok := test.pages[pageNumber]
				if !ok {
					return contractmock.JSONResponse(t, http.StatusBadRequest, map[string]string{"errorCode": "NO_PAGE", "message": fmt.Sprint(pageNumber)})
				}
				return contractmock.JSONResponse(t, http.StatusOK, document)
			})

			client, err := vcfinstaller.NewClient(server.URL(), testToken, nil)
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			got, err := client.ListAllTasks(context.Background(), test.options)
			if err != nil {
				t.Fatalf("ListAllTasks: %v", err)
			}
			if !reflect.DeepEqual(got, test.want) {
				t.Fatalf("tasks mismatch\n got: %#v\nwant: %#v", got, test.want)
			}
			assertExactRequests(t, server.Requests(), test.wantTargets)
		})
	}
}

func TestListAllTasksRejectsFailuresWithoutPartialResults(t *testing.T) {
	t.Parallel()

	validTask := vcfinstaller.Task{ID: "task-a", Name: "first", Status: "PENDING", CreationTimestamp: "2026-08-02T01:00:00Z"}
	duplicateTokenTask := vcfinstaller.Task{ID: testToken, Name: "token-shaped ID", Status: "PENDING", CreationTimestamp: "2026-08-02T01:00:00Z"}

	type failureCase struct {
		name      string
		responses map[int]contractmock.Response
		pageSize  int
		wantAPI   *vcfinstaller.APIError
	}
	cases := []failureCase{
		{
			name: "non-200 is APIError",
			responses: map[int]contractmock.Response{
				0: contractmock.JSONResponse(t, http.StatusInternalServerError, map[string]string{
					"errorCode": "VCF_TASKS_BUSY", "message": "task inventory unavailable: " + testToken,
				}),
			},
			wantAPI: &vcfinstaller.APIError{StatusCode: 500, ErrorCode: "VCF_TASKS_BUSY", Message: "task inventory unavailable: " + testToken},
		},
		{
			name: "non-200 includes other 2xx statuses",
			responses: map[int]contractmock.Response{
				0: contractmock.JSONResponse(t, http.StatusCreated, map[string]string{
					"errorCode": "WRONG_STATUS", "message": "only 200 is successful",
				}),
			},
			wantAPI: &vcfinstaller.APIError{StatusCode: 201, ErrorCode: "WRONG_STATUS", Message: "only 200 is successful"},
		},
		{
			name: "non-JSON success media type",
			responses: map[int]contractmock.Response{
				0: {
					Status:      http.StatusOK,
					ContentType: "text/plain",
					Body:        []byte(`{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":0,"totalElements":0,"totalPages":0}}`),
				},
			},
		},
		{
			name: "malformed success JSON",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":`),
			},
		},
		{
			name: "trailing success JSON",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":0,"totalElements":0,"totalPages":0}} {}`),
			},
		},
		{
			name: "missing elements",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"pageMetadata":{"pageNumber":0,"pageSize":0,"totalElements":0,"totalPages":0}}`),
			},
		},
		{
			name: "null elements",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":null,"pageMetadata":{"pageNumber":0,"pageSize":0,"totalElements":0,"totalPages":0}}`),
			},
		},
		{
			name: "null task element",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":[null],"pageMetadata":{"pageNumber":0,"pageSize":1,"totalElements":1,"totalPages":1}}`),
			},
		},
		{
			name: "present null optional task type",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":[{"id":"task-a","name":"first","type":null,"status":"PENDING","creationTimestamp":"2026-08-02T01:00:00Z"}],"pageMetadata":{"pageNumber":0,"pageSize":1,"totalElements":1,"totalPages":1}}`),
			},
		},
		{
			name: "non-string optional task type",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":[{"id":"task-a","name":"first","type":7,"status":"PENDING","creationTimestamp":"2026-08-02T01:00:00Z"}],"pageMetadata":{"pageNumber":0,"pageSize":1,"totalElements":1,"totalPages":1}}`),
			},
		},
		{
			name: "missing page metadata",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":[]}`),
			},
		},
		{
			name: "missing metadata member",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":0,"totalElements":0}}`),
			},
		},
		{
			name: "null metadata member",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":null,"totalElements":0,"totalPages":0}}`),
			},
		},
		{
			name: "page metadata is not an object",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":[],"pageMetadata":[]}`),
			},
		},
		{
			name: "boolean metadata member",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":[],"pageMetadata":{"pageNumber":false,"pageSize":0,"totalElements":0,"totalPages":0}}`),
			},
		},
		{
			name: "fractional metadata member",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":0.5,"totalElements":0,"totalPages":0}}`),
			},
		},
		{
			name: "negative metadata member",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":0,"totalElements":-1,"totalPages":0}}`),
			},
		},
		{
			name: "wrong returned first page",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":[],"pageMetadata":{"pageNumber":1,"pageSize":0,"totalElements":0,"totalPages":0}}`),
			},
		},
		{
			name: "metadata page size differs from elements",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":1,"totalElements":0,"totalPages":0}}`),
			},
		},
		{
			name: "incoherent total pages",
			responses: map[int]contractmock.Response{
				0: rawJSON(http.StatusOK, `{"elements":[{"id":"task-a","name":"first","status":"PENDING","creationTimestamp":"2026-08-02T01:00:00Z"}],"pageMetadata":{"pageNumber":0,"pageSize":1,"totalElements":1,"totalPages":2}}`),
			},
		},
		{
			name: "empty non-final page",
			responses: map[int]contractmock.Response{
				0: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements:     []vcfinstaller.Task{},
					PageMetadata: metadata{PageNumber: 0, PageSize: 0, TotalElements: 2, TotalPages: 2},
				}),
			},
		},
		{
			name:     "partially filled non-final page",
			pageSize: 2,
			responses: map[int]contractmock.Response{
				0: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements:     []vcfinstaller.Task{validTask},
					PageMetadata: metadata{PageNumber: 0, PageSize: 1, TotalElements: 3, TotalPages: 2},
				}),
			},
		},
		{
			name: "overfull page",
			responses: map[int]contractmock.Response{
				0: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements: []vcfinstaller.Task{
						validTask,
						{ID: "task-b", Name: "second", Status: "PENDING", CreationTimestamp: "2026-08-02T02:00:00Z"},
					},
					PageMetadata: metadata{PageNumber: 0, PageSize: 2, TotalElements: 2, TotalPages: 2},
				}),
			},
		},
		{
			name:     "elements overshoot total",
			pageSize: 2,
			responses: map[int]contractmock.Response{
				0: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements: []vcfinstaller.Task{
						validTask,
						{ID: "task-b", Name: "second", Status: "PENDING", CreationTimestamp: "2026-08-02T02:00:00Z"},
					},
					PageMetadata: metadata{PageNumber: 0, PageSize: 2, TotalElements: 1, TotalPages: 1},
				}),
			},
		},
		{
			name: "changed totals after partial page",
			responses: map[int]contractmock.Response{
				0: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements:     []vcfinstaller.Task{validTask},
					PageMetadata: metadata{PageNumber: 0, PageSize: 1, TotalElements: 2, TotalPages: 2},
				}),
				1: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements:     []vcfinstaller.Task{{ID: "task-b", Name: "second", Status: "SUCCESSFUL", CreationTimestamp: "2026-08-02T02:00:00Z"}},
					PageMetadata: metadata{PageNumber: 1, PageSize: 1, TotalElements: 3, TotalPages: 3},
				}),
			},
		},
		{
			name: "repeated returned page",
			responses: map[int]contractmock.Response{
				0: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements:     []vcfinstaller.Task{validTask},
					PageMetadata: metadata{PageNumber: 0, PageSize: 1, TotalElements: 2, TotalPages: 2},
				}),
				1: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements:     []vcfinstaller.Task{{ID: "task-b", Name: "second", Status: "SUCCESSFUL", CreationTimestamp: "2026-08-02T02:00:00Z"}},
					PageMetadata: metadata{PageNumber: 0, PageSize: 1, TotalElements: 2, TotalPages: 2},
				}),
			},
		},
		{
			name: "premature final page",
			responses: map[int]contractmock.Response{
				0: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements:     []vcfinstaller.Task{validTask},
					PageMetadata: metadata{PageNumber: 0, PageSize: 1, TotalElements: 2, TotalPages: 2},
				}),
				1: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements:     []vcfinstaller.Task{},
					PageMetadata: metadata{PageNumber: 1, PageSize: 0, TotalElements: 2, TotalPages: 2},
				}),
			},
		},
		{
			name: "duplicate task across pages",
			responses: map[int]contractmock.Response{
				0: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements:     []vcfinstaller.Task{duplicateTokenTask},
					PageMetadata: metadata{PageNumber: 0, PageSize: 1, TotalElements: 2, TotalPages: 2},
				}),
				1: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements:     []vcfinstaller.Task{duplicateTokenTask},
					PageMetadata: metadata{PageNumber: 1, PageSize: 1, TotalElements: 2, TotalPages: 2},
				}),
			},
		},
		{
			name: "blank required task name",
			responses: map[int]contractmock.Response{
				0: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements:     []vcfinstaller.Task{{ID: "task-a", Name: " ", Status: "PENDING", CreationTimestamp: "2026-08-02T01:00:00Z"}},
					PageMetadata: metadata{PageNumber: 0, PageSize: 1, TotalElements: 1, TotalPages: 1},
				}),
			},
		},
		{
			name: "blank required task ID",
			responses: map[int]contractmock.Response{
				0: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements:     []vcfinstaller.Task{{ID: " ", Name: "first", Status: "PENDING", CreationTimestamp: "2026-08-02T01:00:00Z"}},
					PageMetadata: metadata{PageNumber: 0, PageSize: 1, TotalElements: 1, TotalPages: 1},
				}),
			},
		},
		{
			name: "blank required task status",
			responses: map[int]contractmock.Response{
				0: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements:     []vcfinstaller.Task{{ID: "task-a", Name: "first", Status: " ", CreationTimestamp: "2026-08-02T01:00:00Z"}},
					PageMetadata: metadata{PageNumber: 0, PageSize: 1, TotalElements: 1, TotalPages: 1},
				}),
			},
		},
		{
			name: "blank required task creation timestamp",
			responses: map[int]contractmock.Response{
				0: contractmock.JSONResponse(t, http.StatusOK, page{
					Elements:     []vcfinstaller.Task{{ID: "task-a", Name: "first", Status: "PENDING", CreationTimestamp: " "}},
					PageMetadata: metadata{PageNumber: 0, PageSize: 1, TotalElements: 1, TotalPages: 1},
				}),
			},
		},
	}

	for _, test := range cases {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server := contractmock.New(t, protectedPath("docs", "contract.json"), func(request contractmock.Request) contractmock.Response {
				pageNumber, err := contractmock.PageNumber(request)
				if err != nil {
					return rawJSON(http.StatusBadRequest, `{"errorCode":"BAD_PAGE","message":"bad page"}`)
				}
				response, ok := test.responses[pageNumber]
				if !ok {
					return rawJSON(http.StatusInternalServerError, `{"errorCode":"UNEXPECTED_PAGE","message":"unexpected page"}`)
				}
				return response
			})
			client, err := vcfinstaller.NewClient(server.URL(), testToken, &http.Client{})
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			pageSize := test.pageSize
			if pageSize == 0 {
				pageSize = 1
			}
			got, err := client.ListAllTasks(context.Background(), vcfinstaller.ListTasksOptions{PageSize: pageSize})
			if err == nil {
				t.Fatalf("expected error, got tasks %#v", got)
			}
			if strings.Contains(err.Error(), testToken) {
				t.Fatalf("error disclosed access token: %v", err)
			}
			if got != nil {
				t.Fatalf("failure returned a partial/non-nil slice: %#v", got)
			}
			if test.wantAPI != nil {
				var apiError *vcfinstaller.APIError
				if !errors.As(err, &apiError) {
					t.Fatalf("error type %T, want *APIError: %v", err, err)
				}
				if *apiError != *test.wantAPI {
					t.Fatalf("APIError = %+v, want %+v", apiError, test.wantAPI)
				}
			} else {
				var protocolError *vcfinstaller.ProtocolError
				if !errors.As(err, &protocolError) {
					t.Fatalf("error type %T, want *ProtocolError: %v", err, err)
				}
			}
		})
	}
}

func TestListAllTasksReturnsFreshValuesAndAcceptsJSONParameters(t *testing.T) {
	t.Parallel()

	taskType := "SDDC_INSTALL"
	document := page{
		Elements: []vcfinstaller.Task{{
			ID: "task-a", Name: "first", Type: &taskType, Status: "PENDING", CreationTimestamp: "2026-08-02T01:00:00Z",
		}},
		PageMetadata: metadata{PageNumber: 0, PageSize: 1, TotalElements: 1, TotalPages: 1},
	}
	server := contractmock.New(t, protectedPath("docs", "contract.json"), func(contractmock.Request) contractmock.Response {
		response := contractmock.JSONResponse(t, http.StatusOK, document)
		response.ContentType = "Application/JSON; charset=utf-8"
		return response
	})
	client, err := vcfinstaller.NewClient(server.URL(), testToken, nil)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	first, err := client.ListAllTasks(context.Background(), vcfinstaller.ListTasksOptions{PageSize: 1})
	if err != nil {
		t.Fatalf("first ListAllTasks: %v", err)
	}
	first[0].Name = "mutated"
	*first[0].Type = "MUTATED"
	second, err := client.ListAllTasks(context.Background(), vcfinstaller.ListTasksOptions{PageSize: 1})
	if err != nil {
		t.Fatalf("second ListAllTasks: %v", err)
	}
	if !reflect.DeepEqual(second, document.Elements) {
		t.Fatalf("second result reused caller-mutated values: got %#v, want %#v", second, document.Elements)
	}
	assertExactRequests(t, server.Requests(), []string{"/v1/tasks?pageSize=1", "/v1/tasks?pageSize=1"})
}

func TestListAllTasksDoesNotSilentlyTruncateLargeValidResponse(t *testing.T) {
	t.Parallel()

	largeName := strings.Repeat("n", (4<<20)+257)
	document := page{
		Elements: []vcfinstaller.Task{{
			ID: "task-large", Name: largeName, Status: "SUCCESSFUL", CreationTimestamp: "2026-08-02T01:00:00Z",
		}},
		PageMetadata: metadata{PageNumber: 0, PageSize: 1, TotalElements: 1, TotalPages: 1},
	}
	server := contractmock.New(t, protectedPath("docs", "contract.json"), func(contractmock.Request) contractmock.Response {
		return contractmock.JSONResponse(t, http.StatusOK, document)
	})
	client, err := vcfinstaller.NewClient(server.URL(), testToken, nil)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	tasks, err := client.ListAllTasks(context.Background(), vcfinstaller.ListTasksOptions{PageSize: 1})
	if err != nil {
		t.Fatalf("ListAllTasks: %v", err)
	}
	if len(tasks) != 1 {
		t.Fatalf("large task response returned %d tasks, want 1", len(tasks))
	}
	if tasks[0].Name != largeName {
		t.Fatalf("large task name length=%d, want %d", len(tasks[0].Name), len(largeName))
	}
}

func TestNewClientAndInputValidation(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name      string
		baseURL   string
		token     string
		wantError bool
	}{
		{name: "http root", baseURL: "http://127.0.0.1:8080", token: "token"},
		{name: "https root slash", baseURL: "https://127.0.0.1:8443/", token: "token"},
		{name: "case-insensitive HTTP scheme", baseURL: "HTTP://127.0.0.1:8080", token: "token"},
		{name: "wrong scheme", baseURL: "ftp://127.0.0.1/resource", token: "token", wantError: true},
		{name: "relative URL", baseURL: "127.0.0.1:8080", token: "token", wantError: true},
		{name: "missing hostname", baseURL: "http://:8080", token: "token", wantError: true},
		{name: "userinfo is not a service root", baseURL: "https://user@example.test", token: "token", wantError: true},
		{name: "path is not service root", baseURL: "https://127.0.0.1/root", token: "token", wantError: true},
		{name: "query is not service root", baseURL: "https://127.0.0.1/?x=1", token: "token", wantError: true},
		{name: "bare query is not service root", baseURL: "https://127.0.0.1?", token: "token", wantError: true},
		{name: "fragment is not service root", baseURL: "https://127.0.0.1/#fragment", token: "token", wantError: true},
		{name: "blank token", baseURL: "https://127.0.0.1", token: " ", wantError: true},
		{name: "DEL is header unsafe", baseURL: "https://127.0.0.1", token: "secret\x7fvalue", wantError: true},
		{name: "header unsafe token", baseURL: "https://127.0.0.1", token: "secret\r\nX-Leak: yes", wantError: true},
	}
	for _, test := range cases {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			_, err := vcfinstaller.NewClient(test.baseURL, test.token, nil)
			if (err != nil) != test.wantError {
				t.Fatalf("NewClient error = %v, wantError=%v", err, test.wantError)
			}
			if err != nil && strings.Contains(err.Error(), test.token) && strings.TrimSpace(test.token) != "" {
				t.Fatalf("error disclosed token: %v", err)
			}
		})
	}

	client, err := vcfinstaller.NewClient("http://127.0.0.1:1", "token", nil)
	if err != nil {
		t.Fatalf("valid constructor: %v", err)
	}
	for _, pageSize := range []int{-1, 0, 101} {
		got, err := client.ListAllTasks(context.Background(), vcfinstaller.ListTasksOptions{PageSize: pageSize})
		if err == nil || got != nil {
			t.Fatalf("PageSize %d: got=%#v err=%v", pageSize, got, err)
		}
	}
	got, err := client.ListAllTasks(nil, vcfinstaller.ListTasksOptions{PageSize: 1})
	if err == nil || got != nil {
		t.Fatalf("nil context: got=%#v err=%v", got, err)
	}

	server := contractmock.New(t, protectedPath("docs", "contract.json"), func(request contractmock.Request) contractmock.Response {
		return contractmock.JSONResponse(t, http.StatusOK, page{
			Elements:     []vcfinstaller.Task{},
			PageMetadata: metadata{PageNumber: 0, PageSize: 0, TotalElements: 0, TotalPages: 0},
		})
	})
	boundaryClient, err := vcfinstaller.NewClient(server.URL(), "token", nil)
	if err != nil {
		t.Fatalf("boundary NewClient: %v", err)
	}
	for _, pageSize := range []int{1, 100} {
		got, err := boundaryClient.ListAllTasks(context.Background(), vcfinstaller.ListTasksOptions{PageSize: pageSize})
		if err != nil || len(got) != 0 {
			t.Fatalf("valid PageSize %d: got=%#v err=%v", pageSize, got, err)
		}
	}
}

func assertExactRequests(t *testing.T, requests []contractmock.Request, wantTargets []string) {
	t.Helper()
	if len(requests) != len(wantTargets) {
		t.Fatalf("request count = %d, want %d: %+v", len(requests), len(wantTargets), requests)
	}
	for i, request := range requests {
		if request.Method != http.MethodGet || request.Path != "/v1/tasks" || request.RequestURI != wantTargets[i] {
			t.Errorf("request %d target = %s %q (path %q), want GET %q", i, request.Method, request.RequestURI, request.Path, wantTargets[i])
		}
		if got := request.Header.Values("Authorization"); !reflect.DeepEqual(got, []string{"Bearer " + testToken}) {
			t.Errorf("request %d Authorization values = %q", i, got)
		}
		if got := request.Header.Values("Accept"); !reflect.DeepEqual(got, []string{"application/json"}) {
			t.Errorf("request %d Accept values = %q", i, got)
		}
		if got := request.Header.Values("Content-Type"); len(got) != 0 {
			t.Errorf("request %d unexpectedly sent Content-Type %q", i, got)
		}
		if len(request.Body) != 0 || request.ContentLength > 0 || len(request.TransferEncoding) != 0 {
			t.Errorf("request %d was not bodyless: body=%q contentLength=%d transferEncoding=%v", i, request.Body, request.ContentLength, request.TransferEncoding)
		}
	}
}

func rawJSON(status int, body string) contractmock.Response {
	return contractmock.Response{Status: status, ContentType: "application/json", Body: []byte(body)}
}

func mustOpen(t testing.TB, path string) io.Reader {
	t.Helper()
	file, err := os.Open(filepath.Clean(path))
	if err != nil {
		t.Fatalf("open %s: %v", path, err)
	}
	t.Cleanup(func() { _ = file.Close() })
	return file
}
