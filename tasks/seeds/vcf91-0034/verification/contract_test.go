package runsnapshot_test

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
	"testing"

	rs "vcf91-0034"
	"vcf91-0034/internal/contractmock"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedSpec   = "specifications/sddc-manager/sddc-manager-openapi.json"
	contractSHA256 = "4bb5156ee9c02110a1b65911869c02895f18de3cd448faa4e26787a27dff0862"
	sourcesSHA256  = "8f041aa8982c760acc78a0f2fe12e18ec804e38af4b82dd2e9a7a06388d2e38e"
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
		Operations []struct {
			operationSource
			QueryParameters []struct {
				Name string `json:"name"`
			} `json:"query_parameters"`
			Request struct {
				Schema map[string]any `json:"schema"`
			} `json:"request"`
			Responses map[string]struct {
				Schema    map[string]any `json:"schema"`
				SchemaRef string         `json:"schema_ref"`
			} `json:"responses"`
		} `json:"operations"`
		Schemas map[string]json.RawMessage `json:"schemas"`
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
		sources.Repository.Commit != expectedCommit ||
		contract.DerivedFrom.SpecPath != expectedSpec ||
		sources.Specification.Path != expectedSpec {
		t.Fatalf(
			"wrong pinned source: contract=%+v sources=%+v",
			contract.DerivedFrom,
			sources,
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
		{OperationID: "getDomains", Method: "GET", Path: "/v1/domains"},
		{OperationID: "getTasks", Method: "GET", Path: "/v1/tasks"},
		{
			OperationID: "refreshAccessToken",
			Method:      "PATCH",
			Path:        "/v1/tokens/access-token/refresh",
		},
	}
	gotOperations := make([]operationSource, len(contract.Operations))
	for index, operation := range contract.Operations {
		gotOperations[index] = operation.operationSource
	}
	if !reflect.DeepEqual(gotOperations, wantOperations) ||
		!reflect.DeepEqual(sources.Operations, wantOperations) {
		t.Fatalf(
			"operation provenance mismatch\ncontract: %#v\nsources: %#v\nwant: %#v",
			gotOperations,
			sources.Operations,
			wantOperations,
		)
	}

	wantDomainQueries := []string{
		"type", "name", "vcFqdn", "vcInstanceId",
		"isManagementSsoDomain", "pageNumber", "pageSize", "useCache",
	}
	wantTaskQueries := []string{
		"limit", "taskStatus", "taskType", "resourceId", "resourceType",
		"completedAfter", "pageNumber", "pageSize", "orderDirection",
		"orderBy", "taskName", "doLiveRefresh",
	}
	if got := parameterNames(contract.Operations[0].QueryParameters); !reflect.DeepEqual(got, wantDomainQueries) {
		t.Fatalf("getDomains query projection = %v, want %v", got, wantDomainQueries)
	}
	if got := parameterNames(contract.Operations[1].QueryParameters); !reflect.DeepEqual(got, wantTaskQueries) {
		t.Fatalf("getTasks query projection = %v, want %v", got, wantTaskQueries)
	}

	refresh := contract.Operations[2]
	if !reflect.DeepEqual(refresh.Request.Schema, map[string]any{
		"type":        "string",
		"description": "ID of the refresh token",
	}) ||
		!reflect.DeepEqual(refresh.Responses["200"].Schema, map[string]any{
			"type": "string",
		}) {
		t.Fatalf("refreshAccessToken JSON string contract mismatch: %+v", refresh)
	}
	if contract.Operations[0].Responses["200"].SchemaRef !=
		"#/components/schemas/PageOfDomain" ||
		contract.Operations[1].Responses["200"].SchemaRef !=
			"#/components/schemas/PageOfTask" {
		t.Fatalf("collection response references were not projected")
	}

	var taskSchema struct {
		Required   []string `json:"required"`
		Properties map[string]struct {
			Type     string `json:"type"`
			ReadOnly bool   `json:"readOnly"`
		} `json:"properties"`
	}
	if json.Unmarshal(contract.Schemas["Task"], &taskSchema) != nil ||
		!reflect.DeepEqual(
			taskSchema.Required,
			[]string{"creationTimestamp", "id", "name", "status"},
		) ||
		taskSchema.Properties["id"].Type != "string" ||
		!taskSchema.Properties["id"].ReadOnly ||
		taskSchema.Properties["errors"].Type != "array" {
		t.Fatalf("Task schema projection mismatch: %+v", taskSchema)
	}
}

func TestSnapshotRefreshesOnlyInterruptedOperationAndSortsEveryResponse(
	t *testing.T,
) {
	server := newServer(t, contractmock.Plan{})
	runtime := server.Runtime()
	client, err := rs.NewClient(rs.Config{
		BaseURL:        server.URL(),
		AccessToken:    runtime.AccessToken,
		RefreshTokenID: runtime.RefreshTokenID,
		HTTPClient:     server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	first, err := client.Snapshot(context.Background())
	if err != nil {
		t.Fatalf("first Snapshot returned %T: %v", err, err)
	}
	second, err := client.Snapshot(context.Background())
	if err != nil {
		t.Fatalf("second Snapshot returned %T: %v", err, err)
	}

	want := expectedSnapshot(runtime)
	if !reflect.DeepEqual(first, want) || !reflect.DeepEqual(second, want) {
		t.Fatalf(
			"flipped collection order was not normalized or prior work was lost\n"+
				"first:  %#v\nsecond: %#v\nwant:   %#v",
			first,
			second,
			want,
		)
	}
	assertSorted(t, first)
	assertSorted(t, second)

	requests := server.Requests()
	wantRequests := []wireExpectation{
		{
			operationID:   "getDomains",
			method:        http.MethodGet,
			path:          "/v1/domains",
			rawQuery:      "pageNumber=0&pageSize=100",
			authorization: "Bearer " + runtime.AccessToken,
		},
		{
			operationID:   "getTasks",
			method:        http.MethodGet,
			path:          "/v1/tasks",
			rawQuery:      "pageNumber=0&pageSize=100",
			authorization: "Bearer " + runtime.AccessToken,
		},
		{
			operationID: "refreshAccessToken",
			method:      http.MethodPatch,
			path:        "/v1/tokens/access-token/refresh",
			contentType: "application/json",
			body:        mustJSON(t, runtime.RefreshTokenID),
		},
		{
			operationID:   "getTasks",
			method:        http.MethodGet,
			path:          "/v1/tasks",
			rawQuery:      "pageNumber=0&pageSize=100",
			authorization: "Bearer " + runtime.NewAccessToken,
		},
		{
			operationID:   "getDomains",
			method:        http.MethodGet,
			path:          "/v1/domains",
			rawQuery:      "pageNumber=0&pageSize=100",
			authorization: "Bearer " + runtime.NewAccessToken,
		},
		{
			operationID:   "getTasks",
			method:        http.MethodGet,
			path:          "/v1/tasks",
			rawQuery:      "pageNumber=0&pageSize=100",
			authorization: "Bearer " + runtime.NewAccessToken,
		},
	}
	if len(requests) != len(wantRequests) {
		t.Fatalf("request count = %d, want %d: %#v", len(requests), len(wantRequests), requests)
	}
	wantHost := strings.TrimPrefix(server.URL(), "http://")
	for index := range wantRequests {
		assertWireRequest(t, index, requests[index], wantRequests[index], wantHost)
	}
}

func TestConfigValidationIsLocalAndStrict(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*rs.Config)
	}{
		{
			name: "base URL path",
			mutate: func(config *rs.Config) {
				config.BaseURL += "/v1"
			},
		},
		{
			name: "base URL query",
			mutate: func(config *rs.Config) {
				config.BaseURL += "?x=1"
			},
		},
		{
			name: "base URL fragment",
			mutate: func(config *rs.Config) {
				config.BaseURL += "#x"
			},
		},
		{
			name: "base URL credentials",
			mutate: func(config *rs.Config) {
				config.BaseURL = "http://user@127.0.0.1"
			},
		},
		{
			name: "unsupported scheme",
			mutate: func(config *rs.Config) {
				config.BaseURL = "ftp://127.0.0.1"
			},
		},
		{
			name: "blank access token",
			mutate: func(config *rs.Config) {
				config.AccessToken = " \t"
			},
		},
		{
			name: "whitespace in access token",
			mutate: func(config *rs.Config) {
				config.AccessToken = "token value"
			},
		},
		{
			name: "blank refresh token id",
			mutate: func(config *rs.Config) {
				config.RefreshTokenID = ""
			},
		},
		{
			name: "whitespace in refresh token id",
			mutate: func(config *rs.Config) {
				config.RefreshTokenID = "refresh\nvalue"
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(t, contractmock.Plan{})
			runtime := server.Runtime()
			config := rs.Config{
				BaseURL:        server.URL(),
				AccessToken:    runtime.AccessToken,
				RefreshTokenID: runtime.RefreshTokenID,
				HTTPClient:     server.Client(),
			}
			test.mutate(&config)
			if client, err := rs.NewClient(config); err == nil || client != nil {
				t.Fatalf("NewClient accepted invalid config: client=%#v err=%v", client, err)
			}
			if requests := server.Requests(); len(requests) != 0 {
				t.Fatalf("validation performed network traffic: %#v", requests)
			}
		})
	}
}

func TestExactStatusesRefreshBoundAndPageValidation(t *testing.T) {
	blank := " \t"
	tests := []struct {
		name          string
		plan          contractmock.Plan
		wantOperation string
		wantStatus    int
		wantProtocol  bool
		wantRequests  int
	}{
		{
			name:          "getDomains other 2xx",
			plan:          contractmock.Plan{DomainStatus: http.StatusCreated},
			wantOperation: "getDomains",
			wantStatus:    http.StatusCreated,
			wantRequests:  1,
		},
		{
			name:          "getTasks other 2xx",
			plan:          contractmock.Plan{TaskStatus: http.StatusCreated},
			wantOperation: "getTasks",
			wantStatus:    http.StatusCreated,
			wantRequests:  4,
		},
		{
			name:          "refresh other 2xx",
			plan:          contractmock.Plan{RefreshStatus: http.StatusCreated},
			wantOperation: "refreshAccessToken",
			wantStatus:    http.StatusCreated,
			wantRequests:  3,
		},
		{
			name:          "second unauthorized is terminal",
			plan:          contractmock.Plan{RejectRefreshedToken: true},
			wantOperation: "getTasks",
			wantStatus:    http.StatusUnauthorized,
			wantRequests:  4,
		},
		{
			name:          "blank refreshed token",
			plan:          contractmock.Plan{RefreshTokenValue: &blank},
			wantOperation: "refreshAccessToken",
			wantProtocol:  true,
			wantRequests:  3,
		},
		{
			name: "inconsistent domain page",
			plan: contractmock.Plan{
				MutateDomains: func(payload map[string]any) {
					metadata := payload["pageMetadata"].(map[string]any)
					metadata["totalElements"] = 99
				},
			},
			wantOperation: "getDomains",
			wantProtocol:  true,
			wantRequests:  1,
		},
		{
			name: "task missing required name",
			plan: contractmock.Plan{
				MutateTasks: func(payload map[string]any) {
					elements := payload["elements"].([]contractmock.Task)
					elements[0].Name = ""
				},
			},
			wantOperation: "getTasks",
			wantProtocol:  true,
			wantRequests:  4,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(t, test.plan)
			client := newClient(t, server)
			_, err := client.Snapshot(context.Background())
			if err == nil {
				t.Fatal("Snapshot unexpectedly succeeded")
			}
			if test.wantProtocol {
				var protocolError *rs.ProtocolError
				if !errors.As(err, &protocolError) ||
					protocolError.OperationID != test.wantOperation {
					t.Fatalf("error = %T %+v, want ProtocolError for %s",
						err, err, test.wantOperation)
				}
			} else {
				var apiError *rs.APIError
				if !errors.As(err, &apiError) ||
					apiError.OperationID != test.wantOperation ||
					apiError.StatusCode != test.wantStatus {
					t.Fatalf(
						"error = %T %+v, want APIError %s HTTP %d",
						err,
						err,
						test.wantOperation,
						test.wantStatus,
					)
				}
			}
			if got := len(server.Requests()); got != test.wantRequests {
				t.Fatalf("request count = %d, want %d", got, test.wantRequests)
			}
		})
	}
}

func TestErrorsAreStructuredRedactedAndContextAware(t *testing.T) {
	server := newServer(t, contractmock.Plan{TaskStatus: http.StatusInternalServerError})
	runtime := server.Runtime()
	client := newClient(t, server)
	partial, err := client.Snapshot(context.Background())
	if wantDomains := expectedSnapshot(runtime).Domains; !reflect.DeepEqual(partial.Domains, wantDomains) ||
		len(partial.Tasks) != 0 {
		t.Fatalf(
			"later failure discarded or invented completed work: got %#v want domains %#v",
			partial,
			wantDomains,
		)
	}
	var apiError *rs.APIError
	if !errors.As(err, &apiError) ||
		apiError.OperationID != "getTasks" ||
		apiError.StatusCode != http.StatusInternalServerError ||
		apiError.ErrorCode != "TASK_FAILED" ||
		apiError.Message == "" ||
		apiError.RemediationMessage == "" ||
		apiError.ReferenceToken == "" {
		t.Fatalf("structured API error was not preserved: %T %+v", err, err)
	}
	assertSecretsAbsent(t, err.Error(), runtime)
	if strings.Contains(err.Error(), apiError.Message) {
		t.Fatalf("API Error text exposed decoded server message: %q", err)
	}

	transportText := "transport leaked " + runtime.AccessToken
	transportClient, newErr := rs.NewClient(rs.Config{
		BaseURL:        server.URL(),
		AccessToken:    runtime.AccessToken,
		RefreshTokenID: runtime.RefreshTokenID,
		HTTPClient: &http.Client{
			Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
				return nil, errors.New(transportText)
			}),
		},
	})
	if newErr != nil {
		t.Fatalf("NewClient: %v", newErr)
	}
	_, err = transportClient.Snapshot(context.Background())
	var transportError *rs.TransportError
	if !errors.As(err, &transportError) || transportError.OperationID != "getDomains" {
		t.Fatalf("error = %T %+v, want getDomains TransportError", err, err)
	}
	if strings.Contains(err.Error(), transportText) ||
		strings.Contains(err.Error(), runtime.AccessToken) {
		t.Fatalf("transport Error text leaked secret or cause: %q", err)
	}

	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	_, err = transportClient.Snapshot(cancelled)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled Snapshot error = %T %v, want errors.Is(context.Canceled)", err, err)
	}
}

type wireExpectation struct {
	operationID   string
	method        string
	path          string
	rawQuery      string
	authorization string
	contentType   string
	body          []byte
}

func assertWireRequest(
	t *testing.T,
	index int,
	got contractmock.Request,
	want wireExpectation,
	wantHost string,
) {
	t.Helper()
	if got.OperationID != want.operationID ||
		got.Method != want.method ||
		got.Path != want.path ||
		got.EscapedPath != want.path ||
		got.RawQuery != want.rawQuery ||
		got.ForceQuery {
		t.Fatalf(
			"request %d target mismatch\n got: %+v\nwant: %+v",
			index,
			got,
			want,
		)
	}
	if got.Host != wantHost {
		t.Fatalf("request %d Host = %q, want %q", index, got.Host, wantHost)
	}
	if !reflect.DeepEqual(got.Header.Values("Accept"), []string{"application/json"}) {
		t.Fatalf("request %d Accept = %#v", index, got.Header.Values("Accept"))
	}
	if want.authorization == "" {
		if values := got.Header.Values("Authorization"); len(values) != 0 {
			t.Fatalf("request %d unexpectedly sent Authorization: %#v", index, values)
		}
	} else if !reflect.DeepEqual(
		got.Header.Values("Authorization"),
		[]string{want.authorization},
	) {
		t.Fatalf("request %d Authorization = %#v", index, got.Header.Values("Authorization"))
	}
	if want.contentType == "" {
		if values := got.Header.Values("Content-Type"); len(values) != 0 {
			t.Fatalf("request %d unexpectedly sent Content-Type: %#v", index, values)
		}
	} else if !reflect.DeepEqual(
		got.Header.Values("Content-Type"),
		[]string{want.contentType},
	) {
		t.Fatalf("request %d Content-Type = %#v", index, got.Header.Values("Content-Type"))
	}
	wantLength := int64(len(want.body))
	if got.ContentLength != wantLength ||
		len(got.TransferEncoding) != 0 ||
		!reflect.DeepEqual(got.Body, want.body) {
		t.Fatalf(
			"request %d entity mismatch: length=%d transfer=%v body=%q, want length=%d body=%q",
			index,
			got.ContentLength,
			got.TransferEncoding,
			got.Body,
			wantLength,
			want.body,
		)
	}
	allowedHeaders := map[string]bool{
		"Accept":          true,
		"Accept-Encoding": true,
		"Authorization":   true,
		"Content-Length":  true,
		"Content-Type":    true,
		"User-Agent":      true,
	}
	for name := range got.Header {
		if !allowedHeaders[name] {
			t.Fatalf("request %d sent unexpected header %q", index, name)
		}
	}
}

func expectedSnapshot(runtime contractmock.RuntimeValues) rs.Snapshot {
	domains := make([]rs.Domain, len(runtime.Domains))
	for index, domain := range runtime.Domains {
		domains[index] = rs.Domain{
			ID: domain.ID, Name: domain.Name, Status: domain.Status, Type: domain.Type,
		}
	}
	tasks := make([]rs.Task, len(runtime.Tasks))
	for index, task := range runtime.Tasks {
		tasks[index] = rs.Task{
			ID: task.ID, Name: task.Name, Type: task.Type, Status: task.Status,
			CreationTimestamp: task.CreationTimestamp,
		}
	}
	return rs.Snapshot{Domains: domains, Tasks: tasks}
}

func assertSorted(t *testing.T, snapshot rs.Snapshot) {
	t.Helper()
	for index := 1; index < len(snapshot.Domains); index++ {
		previous, current := snapshot.Domains[index-1], snapshot.Domains[index]
		if previous.Name > current.Name ||
			(previous.Name == current.Name && previous.ID > current.ID) {
			t.Fatalf("domains are not sorted at %d: %#v", index, snapshot.Domains)
		}
	}
	for index := 1; index < len(snapshot.Tasks); index++ {
		previous, current := snapshot.Tasks[index-1], snapshot.Tasks[index]
		if previous.Name > current.Name ||
			(previous.Name == current.Name && previous.ID > current.ID) {
			t.Fatalf("tasks are not sorted at %d: %#v", index, snapshot.Tasks)
		}
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

func newClient(t *testing.T, server *contractmock.Server) *rs.Client {
	t.Helper()
	runtime := server.Runtime()
	client, err := rs.NewClient(rs.Config{
		BaseURL:        server.URL(),
		AccessToken:    runtime.AccessToken,
		RefreshTokenID: runtime.RefreshTokenID,
		HTTPClient:     server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func parameterNames(parameters []struct {
	Name string `json:"name"`
}) []string {
	names := make([]string, len(parameters))
	for index, parameter := range parameters {
		names[index] = parameter.Name
	}
	return names
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

func assertFileHash(t *testing.T, path, want string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read protected file %s: %v", path, err)
	}
	sum := sha256.Sum256(data)
	if got := hex.EncodeToString(sum[:]); got != want {
		t.Fatalf("protected file %s hash = %s, want %s", path, got, want)
	}
}

func mustJSON(t *testing.T, value any) []byte {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("marshal fixture JSON: %v", err)
	}
	return data
}

func assertSecretsAbsent(
	t *testing.T,
	text string,
	runtime contractmock.RuntimeValues,
) {
	t.Helper()
	for _, secret := range []string{
		runtime.AccessToken,
		runtime.NewAccessToken,
		runtime.RefreshTokenID,
	} {
		if strings.Contains(text, secret) {
			t.Fatalf("error text exposed secret %q: %q", secret, text)
		}
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func ExampleSnapshot() {
	fmt.Println("Snapshot sorts domains and tasks by name, then id")
	// Output: Snapshot sorts domains and tasks by name, then id
}
