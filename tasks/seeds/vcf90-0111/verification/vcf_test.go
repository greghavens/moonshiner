// Protected acceptance tests for the VCF 9.0 Installer getTasks operation.
// The loopback server exposes only GET /v1/tasks and records the exact wire
// requests. It never contacts a VMware endpoint.
package vcfinstaller

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strconv"
	"sync"
	"testing"
)

const (
	contractTag    = "9.0.0.0"
	contractCommit = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
	contractPath   = "specifications/vcf-installer/vcf-installer-openapi.json"
	contractOp     = "getTasks"
)

type requestRecord struct {
	Method      string
	RequestURI  string
	Accept      string
	ContentType string
	Body        string
}

type requestLog struct {
	mu      sync.Mutex
	records []requestRecord
}

type roundTripperFunc func(*http.Request) (*http.Response, error)

func (f roundTripperFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

func (l *requestLog) add(r requestRecord) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.records = append(l.records, r)
}

func (l *requestLog) snapshot() []requestRecord {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]requestRecord(nil), l.records...)
}

type fixtureTask struct {
	ID                  string `json:"id"`
	Name                string `json:"name"`
	Type                string `json:"type,omitempty"`
	Status              string `json:"status"`
	CreationTimestamp   string `json:"creationTimestamp"`
	CompletionTimestamp string `json:"completionTimestamp,omitempty"`
	IsCancellable       bool   `json:"isCancellable"`
	IsRetryable         bool   `json:"isRetryable"`
}

type taskMock struct {
	server *httptest.Server
	log    requestLog
	pages  [][]fixtureTask
}

func newTaskMock(t *testing.T, pages [][]fixtureTask) *taskMock {
	t.Helper()
	m := &taskMock{pages: pages}
	m.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		m.log.add(requestRecord{
			Method:      r.Method,
			RequestURI:  r.URL.RequestURI(),
			Accept:      r.Header.Get("Accept"),
			ContentType: r.Header.Get("Content-Type"),
			Body:        string(body),
		})

		if r.Method != http.MethodGet || r.URL.Path != "/v1/tasks" {
			http.NotFound(w, r)
			return
		}

		pageNumber := 0
		if values, present := r.URL.Query()["pageNumber"]; present {
			if len(values) != 1 || values[0] == "" {
				http.Error(w, "pageNumber must have one integer value", http.StatusBadRequest)
				return
			}
			parsed, err := strconv.Atoi(values[0])
			if err != nil || parsed < 0 {
				http.Error(w, "invalid pageNumber", http.StatusBadRequest)
				return
			}
			pageNumber = parsed
		}
		if pageNumber >= len(m.pages) && len(m.pages) != 0 {
			http.Error(w, "page out of range", http.StatusBadRequest)
			return
		}

		totalElements := 0
		for _, page := range m.pages {
			totalElements += len(page)
		}
		elements := []fixtureTask{}
		if len(m.pages) != 0 {
			elements = m.pages[pageNumber]
		}
		response := struct {
			Elements     []fixtureTask `json:"elements"`
			PageMetadata struct {
				PageNumber    int `json:"pageNumber"`
				PageSize      int `json:"pageSize"`
				TotalElements int `json:"totalElements"`
				TotalPages    int `json:"totalPages"`
			} `json:"pageMetadata"`
		}{Elements: elements}
		response.PageMetadata.PageNumber = pageNumber
		response.PageMetadata.PageSize = len(elements)
		response.PageMetadata.TotalElements = totalElements
		response.PageMetadata.TotalPages = len(m.pages)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(response)
	}))
	t.Cleanup(m.server.Close)
	return m
}

func ptr[T any](value T) *T { return &value }

func TestListTasksWirePaginationAndStableOrder(t *testing.T) {
	pageSet := [][]fixtureTask{
		{
			{ID: "task-z", Name: "last", Type: "INSTALL", Status: "IN_PROGRESS", CreationTimestamp: "2026-07-01T10:00:00Z", IsCancellable: true},
			{ID: "task-b", Name: "tie-second", Type: "VALIDATE", Status: "SUCCESSFUL", CreationTimestamp: "2026-01-02T03:04:05Z", CompletionTimestamp: "2026-01-02T03:10:00Z", IsRetryable: true},
		},
		{
			{ID: "task-a", Name: "tie-first", Type: "VALIDATE", Status: "SUCCESSFUL", CreationTimestamp: "2026-01-02T03:04:05Z"},
			{ID: "task-c", Name: "first", Type: "DISCOVERY", Status: "FAILED", CreationTimestamp: "2025-12-31T23:59:59Z", IsRetryable: true},
		},
		{
			{ID: "task-m", Name: "equal-one", Status: "PENDING", CreationTimestamp: "2026-05-05T05:05:05Z"},
			{ID: "task-m", Name: "equal-two", Status: "PENDING", CreationTimestamp: "2026-05-05T05:05:05Z"},
		},
	}

	allOptions := ListTasksOptions{
		Limit:          ptr[int32](0),
		TaskStatus:     ptr("SUCCESSFUL"),
		TaskType:       ptr("DOMAIN_UPGRADE"),
		ResourceID:     ptr("domain-7"),
		ResourceType:   ptr("DOMAIN"),
		CompletedAfter: ptr[int64](0),
		PageSize:       ptr[int32](2),
		OrderDirection: ptr("ASC"),
		OrderBy:        ptr("creationTimestamp"),
		TaskName:       ptr("upgrade check"),
		DoLiveRefresh:  ptr(false),
	}

	tests := []struct {
		name            string
		pages           [][]fixtureTask
		opts            ListTasksOptions
		useCustomClient bool
		wantURIs        []string
		want            []Task
	}{
		{
			name:     "unset optionals are absent and every page is fetched",
			pages:    pageSet,
			wantURIs: []string{"/v1/tasks", "/v1/tasks?pageNumber=1", "/v1/tasks?pageNumber=2"},
			want: []Task{
				{ID: "task-c", Name: "first", Type: "DISCOVERY", Status: "FAILED", CreationTimestamp: "2025-12-31T23:59:59Z", IsRetryable: true},
				{ID: "task-a", Name: "tie-first", Type: "VALIDATE", Status: "SUCCESSFUL", CreationTimestamp: "2026-01-02T03:04:05Z"},
				{ID: "task-b", Name: "tie-second", Type: "VALIDATE", Status: "SUCCESSFUL", CreationTimestamp: "2026-01-02T03:04:05Z", CompletionTimestamp: "2026-01-02T03:10:00Z", IsRetryable: true},
				{ID: "task-m", Name: "equal-one", Status: "PENDING", CreationTimestamp: "2026-05-05T05:05:05Z"},
				{ID: "task-m", Name: "equal-two", Status: "PENDING", CreationTimestamp: "2026-05-05T05:05:05Z"},
				{ID: "task-z", Name: "last", Type: "INSTALL", Status: "IN_PROGRESS", CreationTimestamp: "2026-07-01T10:00:00Z", IsCancellable: true},
			},
		},
		{
			name:            "set zero false and filters survive pagination",
			pages:           [][]fixtureTask{{{ID: "later", Name: "later", Status: "SUCCESSFUL", CreationTimestamp: "2026-02-03T04:05:07Z"}}, {{ID: "earlier", Name: "earlier", Status: "SUCCESSFUL", CreationTimestamp: "2026-02-03T04:05:06Z"}}},
			opts:            allOptions,
			useCustomClient: true,
			wantURIs: []string{
				"/v1/tasks?completedAfter=0&doLiveRefresh=false&limit=0&orderBy=creationTimestamp&orderDirection=ASC&pageSize=2&resourceId=domain-7&resourceType=DOMAIN&taskName=upgrade+check&taskStatus=SUCCESSFUL&taskType=DOMAIN_UPGRADE",
				"/v1/tasks?completedAfter=0&doLiveRefresh=false&limit=0&orderBy=creationTimestamp&orderDirection=ASC&pageNumber=1&pageSize=2&resourceId=domain-7&resourceType=DOMAIN&taskName=upgrade+check&taskStatus=SUCCESSFUL&taskType=DOMAIN_UPGRADE",
			},
			want: []Task{
				{ID: "earlier", Name: "earlier", Status: "SUCCESSFUL", CreationTimestamp: "2026-02-03T04:05:06Z"},
				{ID: "later", Name: "later", Status: "SUCCESSFUL", CreationTimestamp: "2026-02-03T04:05:07Z"},
			},
		},
		{
			name:     "creation timestamps are compared lexicographically",
			pages:    [][]fixtureTask{{{ID: "task-2", Name: "encountered-first", Status: "PENDING", CreationTimestamp: "2"}, {ID: "task-10", Name: "lexical-first", Status: "PENDING", CreationTimestamp: "10"}}},
			wantURIs: []string{"/v1/tasks"},
			want: []Task{
				{ID: "task-10", Name: "lexical-first", Status: "PENDING", CreationTimestamp: "10"},
				{ID: "task-2", Name: "encountered-first", Status: "PENDING", CreationTimestamp: "2"},
			},
		},
		{
			name:     "non-nil empty string is sent",
			pages:    nil,
			opts:     ListTasksOptions{TaskName: ptr("")},
			wantURIs: []string{"/v1/tasks?taskName="},
			want:     []Task{},
		},
		{
			name:     "empty collection succeeds",
			pages:    nil,
			wantURIs: []string{"/v1/tasks"},
			want:     []Task{},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			mock := newTaskMock(t, tc.pages)
			config := Config{BaseURL: mock.server.URL + "/"}
			customClientCalls := 0
			if tc.useCustomClient {
				config.HTTPClient = &http.Client{Transport: roundTripperFunc(func(request *http.Request) (*http.Response, error) {
					customClientCalls++
					return http.DefaultTransport.RoundTrip(request)
				})}
			}
			client, err := New(config)
			if err != nil {
				t.Fatalf("New: %v", err)
			}
			got, err := client.ListTasks(context.Background(), tc.opts)
			if err != nil {
				t.Fatalf("ListTasks: %v", err)
			}
			if !reflect.DeepEqual(got, tc.want) {
				t.Fatalf("ListTasks() = %#v, want %#v", got, tc.want)
			}

			records := mock.log.snapshot()
			if len(records) != len(tc.wantURIs) {
				t.Fatalf("request log has %d entries, want %d: %#v", len(records), len(tc.wantURIs), records)
			}
			if tc.useCustomClient && customClientCalls != len(tc.wantURIs) {
				t.Fatalf("custom HTTP client handled %d requests, want %d", customClientCalls, len(tc.wantURIs))
			}
			for i, record := range records {
				if record.Method != http.MethodGet {
					t.Errorf("request[%d] method = %q, want GET", i, record.Method)
				}
				if record.RequestURI != tc.wantURIs[i] {
					t.Errorf("request[%d] URI = %q, want %q", i, record.RequestURI, tc.wantURIs[i])
				}
				if record.Accept != "application/json" {
					t.Errorf("request[%d] Accept = %q, want application/json", i, record.Accept)
				}
				if record.ContentType != "" {
					t.Errorf("request[%d] Content-Type = %q, want omitted", i, record.ContentType)
				}
				if record.Body != "" {
					t.Errorf("request[%d] body = %q, want empty", i, record.Body)
				}
			}
		})
	}
}

func TestNewValidation(t *testing.T) {
	tests := []struct {
		name    string
		baseURL string
		wantErr bool
	}{
		{name: "empty", baseURL: "", wantErr: true},
		{name: "relative", baseURL: "relative/path", wantErr: true},
		{name: "malformed", baseURL: "://bad", wantErr: true},
		{name: "absolute", baseURL: "https://example.com"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			_, err := New(Config{BaseURL: tc.baseURL})
			if (err != nil) != tc.wantErr {
				t.Fatalf("New(BaseURL=%q) error = %v, wantErr %v", tc.baseURL, err, tc.wantErr)
			}
		})
	}
}

func TestListTasksFailures(t *testing.T) {
	tests := []struct {
		name    string
		respond func(http.ResponseWriter, *http.Request)
		cancel  bool
	}{
		{
			name: "non-2xx",
			respond: func(w http.ResponseWriter, _ *http.Request) {
				http.Error(w, `{"errorCode":"VCF_ERROR","message":"failed"}`, http.StatusInternalServerError)
			},
		},
		{
			name: "malformed JSON",
			respond: func(w http.ResponseWriter, _ *http.Request) {
				_, _ = io.WriteString(w, `{"elements":[`)
			},
		},
		{
			name: "missing pagination metadata",
			respond: func(w http.ResponseWriter, _ *http.Request) {
				_, _ = io.WriteString(w, `{"elements":[]}`)
			},
		},
		{
			name: "negative page number",
			respond: func(w http.ResponseWriter, _ *http.Request) {
				_, _ = io.WriteString(w, `{"elements":[],"pageMetadata":{"pageNumber":-1,"pageSize":0,"totalElements":0,"totalPages":0}}`)
			},
		},
		{
			name: "negative page size",
			respond: func(w http.ResponseWriter, _ *http.Request) {
				_, _ = io.WriteString(w, `{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":-1,"totalElements":0,"totalPages":1}}`)
			},
		},
		{
			name: "negative total elements",
			respond: func(w http.ResponseWriter, _ *http.Request) {
				_, _ = io.WriteString(w, `{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":0,"totalElements":-1,"totalPages":1}}`)
			},
		},
		{
			name: "negative total pages",
			respond: func(w http.ResponseWriter, _ *http.Request) {
				_, _ = io.WriteString(w, `{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":0,"totalElements":0,"totalPages":-1}}`)
			},
		},
		{
			name: "zero pages with elements",
			respond: func(w http.ResponseWriter, _ *http.Request) {
				_, _ = io.WriteString(w, `{"elements":[{"id":"unexpected"}],"pageMetadata":{"pageNumber":0,"pageSize":1,"totalElements":1,"totalPages":0}}`)
			},
		},
		{
			name: "zero pages with positive total elements",
			respond: func(w http.ResponseWriter, _ *http.Request) {
				_, _ = io.WriteString(w, `{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":0,"totalElements":1,"totalPages":0}}`)
			},
		},
		{
			name: "page outside total pages",
			respond: func(w http.ResponseWriter, _ *http.Request) {
				_, _ = io.WriteString(w, `{"elements":[],"pageMetadata":{"pageNumber":2,"totalPages":2}}`)
			},
		},
		{name: "canceled context", cancel: true},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if tc.respond != nil {
					tc.respond(w, r)
				}
			}))
			t.Cleanup(server.Close)
			client, err := New(Config{BaseURL: server.URL})
			if err != nil {
				t.Fatalf("New: %v", err)
			}
			ctx := context.Background()
			if tc.cancel {
				var cancel context.CancelFunc
				ctx, cancel = context.WithCancel(ctx)
				cancel()
			}
			_, err = client.ListTasks(ctx, ListTasksOptions{})
			if err == nil {
				t.Fatal("ListTasks returned nil error")
			}
			if tc.cancel && !errors.Is(err, context.Canceled) {
				t.Fatalf("ListTasks error = %v, want errors.Is(context.Canceled)", err)
			}
		})
	}
}

func TestListTasksRejectsMismatchedPageMetadata(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("pageNumber") == "" {
			_, _ = io.WriteString(w, `{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":0,"totalElements":0,"totalPages":2}}`)
			return
		}
		_, _ = io.WriteString(w, `{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":0,"totalElements":0,"totalPages":2}}`)
	}))
	t.Cleanup(server.Close)

	client, err := New(Config{BaseURL: server.URL})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	_, err = client.ListTasks(context.Background(), ListTasksOptions{})
	if err == nil {
		t.Fatal("ListTasks returned nil error for mismatched pageMetadata.pageNumber")
	}
}

func TestPinnedContractProvenance(t *testing.T) {
	type operation struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	}
	type sourceDocument struct {
		Tag              string      `json:"tag"`
		TagCommitSHA     string      `json:"tagCommitSha"`
		SpecPath         string      `json:"specPath"`
		OperationIDs     []string    `json:"operationIds"`
		Operations       []operation `json:"operations"`
		SpecificationURL string      `json:"specificationUrl"`
	}
	type contractDocument struct {
		Source struct {
			Tag          string `json:"tag"`
			TagCommitSHA string `json:"tagCommitSha"`
			SpecPath     string `json:"specPath"`
		} `json:"source"`
		OperationIDs []string `json:"operationIds"`
		Paths        map[string]map[string]struct {
			OperationID string `json:"operationId"`
		} `json:"paths"`
	}

	t.Run("official sources name the exact tagged operation", func(t *testing.T) {
		body, err := os.ReadFile("docs/official_sources.json")
		if err != nil {
			t.Fatal(err)
		}
		var document sourceDocument
		if err := json.Unmarshal(body, &document); err != nil {
			t.Fatal(err)
		}
		if document.Tag != contractTag || document.TagCommitSHA != contractCommit || document.SpecPath != contractPath {
			t.Fatalf("wrong source pin: %+v", document)
		}
		if !reflect.DeepEqual(document.OperationIDs, []string{contractOp}) {
			t.Fatalf("operationIds = %#v, want [%s]", document.OperationIDs, contractOp)
		}
		wantOperations := []operation{{OperationID: contractOp, Method: "GET", Path: "/v1/tasks"}}
		if !reflect.DeepEqual(document.Operations, wantOperations) {
			t.Fatalf("operations = %#v, want %#v", document.Operations, wantOperations)
		}
		if document.SpecificationURL != "https://raw.githubusercontent.com/vmware/vcf-api-specs/"+contractCommit+"/"+contractPath {
			t.Fatalf("specificationUrl is not commit-pinned: %q", document.SpecificationURL)
		}
	})

	t.Run("contract contains only getTasks", func(t *testing.T) {
		body, err := os.ReadFile("docs/contract.json")
		if err != nil {
			t.Fatal(err)
		}
		var document contractDocument
		if err := json.Unmarshal(body, &document); err != nil {
			t.Fatal(err)
		}
		if document.Source.Tag != contractTag || document.Source.TagCommitSHA != contractCommit || document.Source.SpecPath != contractPath {
			t.Fatalf("wrong contract source pin: %+v", document.Source)
		}
		if !reflect.DeepEqual(document.OperationIDs, []string{contractOp}) {
			t.Fatalf("operationIds = %#v, want [%s]", document.OperationIDs, contractOp)
		}
		if len(document.Paths) != 1 || len(document.Paths["/v1/tasks"]) != 1 || document.Paths["/v1/tasks"]["get"].OperationID != contractOp {
			t.Fatalf("contract paths do not contain only GET /v1/tasks getTasks: %#v", document.Paths)
		}
	})
}
