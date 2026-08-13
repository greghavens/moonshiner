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
	"runtime"
	"sort"
	"strings"
	"testing"

	"example.com/vcf-installer-token-refresh/internal/contractmock"
	"example.com/vcf-installer-token-refresh/vcfinstaller"
)

const (
	wantCommit = "c3f3b52c845dd967cabbc21680e893292077d5ba"
	wantSpec   = "specifications/vcf-installer/vcf-installer-openapi.json"
)

func repositoryRoot(t *testing.T) string {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("locate protected verifier")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(filename), "..", ".."))
}

func TestOfficialSourceRecordsEveryContractOperation(t *testing.T) {
	root := repositoryRoot(t)
	readJSON := func(name string, destination any) {
		t.Helper()
		data, err := os.ReadFile(filepath.Join(root, name))
		if err != nil {
			t.Fatalf("read %s: %v", name, err)
		}
		if err := json.Unmarshal(data, destination); err != nil {
			t.Fatalf("decode %s: %v", name, err)
		}
	}
	var contract struct {
		ContractFormat string `json:"contractFormat"`
		Source         struct {
			Repository          string `json:"repository"`
			RepositoryCommitSHA string `json:"repositoryCommitSha"`
			SpecPath            string `json:"specPath"`
			License             string `json:"license"`
			OpenAPI             string `json:"openapi"`
			APIVersion          string `json:"apiVersion"`
		} `json:"source"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operations"`
		FocusedPaginationProfile struct {
			First []string `json:"firstPageQueryMembers"`
			Later []string `json:"laterPageQueryMembers"`
			Unset []string `json:"unsetQueryMembers"`
		} `json:"focusedPaginationProfile"`
	}
	var sources struct {
		Repository          string   `json:"repository"`
		RepositoryCommitSHA string   `json:"repositoryCommitSha"`
		SpecPath            string   `json:"specPath"`
		License             string   `json:"license"`
		OperationIDs        []string `json:"operationIds"`
		Operations          []struct {
			OperationID         string `json:"operationId"`
			Method              string `json:"method"`
			Path                string `json:"path"`
			RepositoryCommitSHA string `json:"repositoryCommitSha"`
			SpecPath            string `json:"specPath"`
		} `json:"operations"`
		Derivation struct {
			DocumentationPageUsed bool `json:"documentationPageUsedAsContractSource"`
		} `json:"derivation"`
	}
	readJSON("docs/contract.json", &contract)
	readJSON("docs/official_sources.json", &sources)
	if contract.ContractFormat != "focused-openapi-projection-v1" ||
		contract.Source.Repository != "vmware/vcf-api-specs" ||
		contract.Source.RepositoryCommitSHA != wantCommit ||
		contract.Source.SpecPath != wantSpec ||
		contract.Source.License != "Apache-2.0" ||
		contract.Source.OpenAPI != "3.0.1" ||
		contract.Source.APIVersion != "9.1.0.0" {
		t.Fatalf("contract source changed: %+v", contract.Source)
	}
	if sources.Repository != "vmware/vcf-api-specs" || sources.RepositoryCommitSHA != wantCommit ||
		sources.SpecPath != wantSpec || sources.License != "Apache-2.0" || sources.Derivation.DocumentationPageUsed {
		t.Fatalf("official source changed: %+v", sources)
	}

	want := []struct {
		id, method, path string
	}{
		{"getTasks", http.MethodGet, "/v1/tasks"},
		{"refreshAccessToken", http.MethodPatch, "/v1/tokens/access-token/refresh"},
	}
	if len(contract.Operations) != len(want) || len(sources.Operations) != len(want) || len(sources.OperationIDs) != len(want) {
		t.Fatalf("operation counts contract=%d sources=%d ids=%d", len(contract.Operations), len(sources.Operations), len(sources.OperationIDs))
	}
	for index, expected := range want {
		t.Run(expected.id, func(t *testing.T) {
			operation := contract.Operations[index]
			source := sources.Operations[index]
			if operation.OperationID != expected.id || operation.Method != expected.method || operation.Path != expected.path {
				t.Fatalf("contract operation = %+v, want %+v", operation, expected)
			}
			if source.OperationID != expected.id || source.Method != expected.method || source.Path != expected.path ||
				source.RepositoryCommitSHA != wantCommit || source.SpecPath != wantSpec || sources.OperationIDs[index] != expected.id {
				t.Fatalf("official operation = %+v", source)
			}
		})
	}
	if !reflect.DeepEqual(contract.FocusedPaginationProfile.First, []string{"pageSize"}) ||
		!reflect.DeepEqual(contract.FocusedPaginationProfile.Later, []string{"pageNumber", "pageSize"}) ||
		!reflect.DeepEqual(contract.FocusedPaginationProfile.Unset, contractmock.ForbiddenQueryMembers()) {
		t.Fatalf("focused omission profile changed: %+v", contract.FocusedPaginationProfile)
	}
}

func TestListTasksRefreshesAndResumesExactWire(t *testing.T) {
	server := contractmock.Start(t, filepath.Join(repositoryRoot(t), "docs", "contract.json"), contractmock.ExpireOnce)
	client, err := vcfinstaller.NewClient(
		server.URL(),
		server.OldToken(),
		server.RefreshTokenID(),
		&http.Client{},
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	tasks, err := client.ListTasks(context.Background(), server.PageSize())
	if err != nil {
		t.Fatalf("ListTasks: %v", err)
	}

	wantTasks := make([]vcfinstaller.Task, 0, len(server.Tasks()))
	for _, task := range server.Tasks() {
		wantTasks = append(wantTasks, vcfinstaller.Task{
			ID: task.ID, Name: task.Name, Type: task.Type,
			Status: task.Status, CreationTimestamp: task.CreationTimestamp,
		})
	}
	sort.Slice(wantTasks, func(left, right int) bool {
		if wantTasks[left].CreationTimestamp == wantTasks[right].CreationTimestamp {
			return wantTasks[left].ID < wantTasks[right].ID
		}
		return wantTasks[left].CreationTimestamp < wantTasks[right].CreationTimestamp
	})
	if !reflect.DeepEqual(tasks, wantTasks) {
		t.Fatalf("tasks were lost, duplicated, or mis-sorted\ngot:  %+v\nwant: %+v", tasks, wantTasks)
	}

	requests := server.Requests()
	wantOperations := []string{"getTasks", "getTasks", "refreshAccessToken", "getTasks", "getTasks"}
	wantStatuses := []int{200, 401, 200, 200, 200}
	wantTargets := []string{
		"/v1/tasks?pageSize=2",
		"/v1/tasks?pageNumber=1&pageSize=2",
		"/v1/tokens/access-token/refresh",
		"/v1/tasks?pageNumber=1&pageSize=2",
		"/v1/tasks?pageNumber=2&pageSize=2",
	}
	wantAuthorization := []string{
		"Bearer " + server.OldToken(),
		"Bearer " + server.OldToken(),
		"Bearer " + server.OldToken(),
		"Bearer " + server.NewToken(),
		"Bearer " + server.NewToken(),
	}
	if len(requests) != len(wantOperations) {
		t.Fatalf("request log = %v, want exactly five requests", requests)
	}
	for index, request := range requests {
		if request.OperationID != wantOperations[index] || request.RawTarget != wantTargets[index] || request.ResponseStatus != wantStatuses[index] {
			t.Errorf("request %d = %v, want %s %s => %d", index, request, wantOperations[index], wantTargets[index], wantStatuses[index])
		}
		assertSingleHeader(t, request.Header, "Authorization", wantAuthorization[index])
		assertSingleHeader(t, request.Header, "Accept", "application/json")
	}
	if requests[1].RawTarget != requests[3].RawTarget {
		t.Error("the interrupted page request was not retried identically")
	}
	if countTarget(requests, wantTargets[0]) != 1 {
		t.Error("completed page zero was replayed")
	}

	for _, index := range []int{0, 1, 3, 4} {
		request := requests[index]
		if request.Method != http.MethodGet || len(request.Body) != 0 || request.ContentLength != 0 || len(request.TransferEncoding) != 0 {
			t.Errorf("GET %d framing method=%s body=%d contentLength=%d transfer=%v", index, request.Method, len(request.Body), request.ContentLength, request.TransferEncoding)
		}
		if values := request.Header.Values("Content-Type"); len(values) != 0 {
			t.Errorf("GET %d Content-Type = %v, want absent", index, values)
		}
		parsed, err := url.ParseRequestURI(request.RawTarget)
		if err != nil {
			t.Fatalf("parse target %q: %v", request.RawTarget, err)
		}
		query := parsed.Query()
		for _, forbidden := range contractmock.ForbiddenQueryMembers() {
			if _, present := query[forbidden]; present {
				t.Errorf("GET %d sent unset optional query member %q", index, forbidden)
			}
		}
	}

	refresh := requests[2]
	wantRefreshBody, _ := json.Marshal(server.RefreshTokenID())
	if refresh.Method != http.MethodPatch || string(refresh.Body) != string(wantRefreshBody) {
		t.Errorf("refresh wire method=%s body=%q, want PATCH %q", refresh.Method, refresh.Body, wantRefreshBody)
	}
	if refresh.ContentLength != int64(len(wantRefreshBody)) || len(refresh.TransferEncoding) != 0 {
		t.Errorf("refresh framing contentLength=%d transfer=%v body=%d", refresh.ContentLength, refresh.TransferEncoding, len(refresh.Body))
	}
	assertSingleHeader(t, refresh.Header, "Content-Type", "application/json")
}

func TestRefreshPolicyTable(t *testing.T) {
	tests := []struct {
		name         string
		mode         contractmock.Mode
		operationID  string
		status       int
		requestCount int
		refreshCount int
	}{
		{name: "HTTP 500 does not refresh", mode: contractmock.FailWith500, operationID: "getTasks", status: 500, requestCount: 2, refreshCount: 0},
		{name: "second 401 is terminal", mode: contractmock.SecondUnauthorized, operationID: "getTasks", status: 401, requestCount: 4, refreshCount: 1},
		{name: "refresh 401 is terminal", mode: contractmock.RefreshUnauthorized, operationID: "refreshAccessToken", status: 401, requestCount: 3, refreshCount: 1},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.Start(t, filepath.Join(repositoryRoot(t), "docs", "contract.json"), test.mode)
			client, err := vcfinstaller.NewClient(server.URL(), server.OldToken(), server.RefreshTokenID(), nil)
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			tasks, err := client.ListTasks(context.Background(), server.PageSize())
			if tasks != nil {
				t.Fatalf("failure returned partial tasks: %+v", tasks)
			}
			var apiError *vcfinstaller.APIError
			if !errors.As(err, &apiError) || apiError.OperationID != test.operationID || apiError.StatusCode != test.status {
				t.Fatalf("error = %T %v, want APIError{%s %d}", err, err, test.operationID, test.status)
			}
			message := err.Error()
			for _, secret := range []string{server.OldToken(), server.NewToken(), server.RefreshTokenID()} {
				if strings.Contains(message, secret) {
					t.Error("error exposes a token")
				}
			}
			requests := server.Requests()
			if len(requests) != test.requestCount {
				t.Fatalf("requests = %v, want count %d", requests, test.requestCount)
			}
			refreshes := 0
			for _, request := range requests {
				if request.OperationID == "refreshAccessToken" {
					refreshes++
				}
			}
			if refreshes != test.refreshCount {
				t.Errorf("refresh count = %d, want %d", refreshes, test.refreshCount)
			}
		})
	}
}

func TestInputValidationTable(t *testing.T) {
	constructorTests := []struct {
		name, baseURL, accessToken, refreshID string
	}{
		{name: "relative URL", baseURL: "/v1", accessToken: "access", refreshID: "refresh"},
		{name: "non HTTP scheme", baseURL: "ftp://127.0.0.1", accessToken: "access", refreshID: "refresh"},
		{name: "missing host", baseURL: "https:///", accessToken: "access", refreshID: "refresh"},
		{name: "service root path", baseURL: "https://127.0.0.1/example", accessToken: "access", refreshID: "refresh"},
		{name: "service root credentials", baseURL: "https://user@127.0.0.1", accessToken: "access", refreshID: "refresh"},
		{name: "service root query", baseURL: "https://127.0.0.1?x=1", accessToken: "access", refreshID: "refresh"},
		{name: "service root fragment", baseURL: "https://127.0.0.1#x", accessToken: "access", refreshID: "refresh"},
		{name: "blank access token", baseURL: "https://127.0.0.1", accessToken: " ", refreshID: "refresh"},
		{name: "header unsafe access token", baseURL: "https://127.0.0.1", accessToken: "secret\r\nvalue", refreshID: "refresh"},
		{name: "control byte access token", baseURL: "https://127.0.0.1", accessToken: "secret\x00value", refreshID: "refresh"},
		{name: "delete byte access token", baseURL: "https://127.0.0.1", accessToken: "secret\x7fvalue", refreshID: "refresh"},
		{name: "blank refresh id", baseURL: "https://127.0.0.1", accessToken: "access", refreshID: ""},
		{name: "whitespace refresh id", baseURL: "https://127.0.0.1", accessToken: "access", refreshID: " \t"},
	}
	for _, test := range constructorTests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := vcfinstaller.NewClient(test.baseURL, test.accessToken, test.refreshID, nil); err == nil {
				t.Fatal("NewClient accepted invalid input")
			}
		})
	}

	client, err := vcfinstaller.NewClient("http://127.0.0.1:1", "access", "refresh", nil)
	if err != nil {
		t.Fatal(err)
	}
	workflowTests := []struct {
		name     string
		ctx      context.Context
		pageSize int
	}{
		{name: "nil context", ctx: nil, pageSize: 2},
		{name: "zero page size", ctx: context.Background(), pageSize: 0},
		{name: "oversized page", ctx: context.Background(), pageSize: 101},
	}
	for _, test := range workflowTests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := client.ListTasks(test.ctx, test.pageSize); err == nil {
				t.Fatal("ListTasks accepted invalid input")
			}
		})
	}
}

func TestSuccessfulResponseValidationTable(t *testing.T) {
	const taskA = `{"id":"a","name":"name-a","status":"RUNNING","creationTimestamp":"2026-01-01T00:00:00Z"}`
	const taskB = `{"id":"b","name":"name-b","status":"SUCCESSFUL","creationTimestamp":"2026-01-01T00:00:01Z"}`
	const taskC = `{"id":"c","name":"name-c","status":"SUCCESSFUL","creationTimestamp":"2026-01-01T00:00:02Z"}`

	page := func(number, size, total, pages int, elements string) string {
		return fmt.Sprintf(`{"elements":%s,"pageMetadata":{"pageNumber":%d,"pageSize":%d,"totalElements":%d,"totalPages":%d}}`, elements, number, size, total, pages)
	}
	tests := []struct {
		name   string
		bodies []string
	}{
		{name: "top level is not an object", bodies: []string{`[]`}},
		{name: "elements missing", bodies: []string{`{"pageMetadata":{"pageNumber":0,"pageSize":2,"totalElements":0,"totalPages":0}}`}},
		{name: "elements null", bodies: []string{`{"elements":null,"pageMetadata":{"pageNumber":0,"pageSize":2,"totalElements":0,"totalPages":0}}`}},
		{name: "elements is not an array", bodies: []string{`{"elements":{},"pageMetadata":{"pageNumber":0,"pageSize":2,"totalElements":0,"totalPages":0}}`}},
		{name: "metadata missing", bodies: []string{`{"elements":[]}`}},
		{name: "metadata null", bodies: []string{`{"elements":[],"pageMetadata":null}`}},
		{name: "metadata is not an object", bodies: []string{`{"elements":[],"pageMetadata":[]}`}},
		{name: "metadata member missing", bodies: []string{`{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":2,"totalElements":0}}`}},
		{name: "metadata member boolean", bodies: []string{`{"elements":[],"pageMetadata":{"pageNumber":false,"pageSize":2,"totalElements":0,"totalPages":0}}`}},
		{name: "metadata member fractional", bodies: []string{`{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":2.5,"totalElements":0,"totalPages":0}}`}},
		{name: "negative metadata", bodies: []string{page(-1, 2, 0, 0, `[]`)}},
		{name: "nonpositive metadata page size", bodies: []string{page(0, 0, 0, 0, `[]`)}},
		{name: "changed metadata page size", bodies: []string{page(0, 1, 0, 0, `[]`)}},
		{name: "unrequested page", bodies: []string{page(1, 2, 1, 1, `[`+taskA+`]`)}},
		{name: "incoherent totals", bodies: []string{page(0, 2, 3, 1, `[`+taskA+`]`)}},
		{name: "overfull page", bodies: []string{page(0, 2, 3, 2, `[`+taskA+`,`+taskB+`,`+taskC+`]`)}},
		{name: "empty nonterminal page", bodies: []string{page(0, 2, 1, 1, `[]`)}},
		{name: "declared total overshoot", bodies: []string{page(0, 2, 1, 1, `[`+taskA+`,`+taskB+`]`)}},
		{name: "sequence cannot progress", bodies: []string{page(0, 2, 2, 1, `[`+taskA+`]`)}},
		{name: "totals change", bodies: []string{
			page(0, 2, 3, 2, `[`+taskA+`,`+taskB+`]`),
			page(1, 2, 4, 2, `[`+taskC+`]`),
		}},
		{name: "task is not an object", bodies: []string{page(0, 2, 1, 1, `["task"]`)}},
		{name: "required task member missing", bodies: []string{page(0, 2, 1, 1, `[{"name":"n","status":"s","creationTimestamp":"t"}]`)}},
		{name: "required task member blank", bodies: []string{page(0, 2, 1, 1, `[{"id":"id","name":" ","status":"s","creationTimestamp":"t"}]`)}},
		{name: "required task member not a string", bodies: []string{page(0, 2, 1, 1, `[{"id":"id","name":"n","status":1,"creationTimestamp":"t"}]`)}},
		{name: "optional type is null", bodies: []string{page(0, 2, 1, 1, `[{"id":"id","name":"n","type":null,"status":"s","creationTimestamp":"t"}]`)}},
		{name: "optional type is not a string", bodies: []string{page(0, 2, 1, 1, `[{"id":"id","name":"n","type":false,"status":"s","creationTimestamp":"t"}]`)}},
		{name: "duplicate task id", bodies: []string{page(0, 2, 2, 1, `[`+taskA+`,`+taskA+`]`)}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			responses := make([]scriptedResponse, len(test.bodies))
			for index, body := range test.bodies {
				responses[index] = scriptedResponse{status: http.StatusOK, contentType: "application/json", body: body}
			}
			tasks, err, _ := runScript(t, 2, responses)
			if tasks != nil {
				t.Fatalf("protocol failure returned partial tasks: %+v", tasks)
			}
			var protocolError *vcfinstaller.ProtocolError
			if !errors.As(err, &protocolError) || protocolError.OperationID != "getTasks" {
				t.Fatalf("error = %T %v, want getTasks ProtocolError", err, err)
			}
		})
	}
}

func TestResponseMediaTypeStatusAndFreshOutput(t *testing.T) {
	validEmpty := func(pageSize int) string {
		return fmt.Sprintf(`{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":%d,"totalElements":0,"totalPages":0}}`, pageSize)
	}

	for _, contentType := range []string{"", "text/plain", "application/problem+json"} {
		t.Run("invalid media type "+contentType, func(t *testing.T) {
			tasks, err, _ := runScript(t, 2, []scriptedResponse{{status: 200, contentType: contentType, body: validEmpty(2)}})
			if tasks != nil {
				t.Fatalf("failure returned tasks: %+v", tasks)
			}
			var protocolError *vcfinstaller.ProtocolError
			if !errors.As(err, &protocolError) || protocolError.OperationID != "getTasks" {
				t.Fatalf("error = %T %v, want getTasks ProtocolError", err, err)
			}
		})
	}

	for _, pageSize := range []int{1, 100} {
		tasks, err, _ := runScript(t, pageSize, []scriptedResponse{{
			status: http.StatusOK, contentType: "application/json; charset=utf-8", body: validEmpty(pageSize),
		}})
		if err != nil || len(tasks) != 0 {
			t.Fatalf("parameterized JSON or page-size %d boundary failed: tasks=%v err=%v", pageSize, tasks, err)
		}
	}

	tasks, err, requestCount := runScript(t, 2, []scriptedResponse{{
		status: http.StatusTeapot, contentType: "text/plain", body: "not JSON",
	}})
	if tasks != nil {
		t.Fatalf("HTTP failure returned tasks: %+v", tasks)
	}
	var apiError *vcfinstaller.APIError
	if !errors.As(err, &apiError) || apiError.OperationID != "getTasks" || apiError.StatusCode != http.StatusTeapot || requestCount != 1 {
		t.Fatalf("error=%T %v requests=%d, want getTasks APIError{418} without refresh", err, err, requestCount)
	}

	const outputPage = `{"elements":[{"id":"b","name":"B","status":"s","creationTimestamp":"t"},{"id":"A","name":"A","type":"","status":"s","creationTimestamp":"t"}],"pageMetadata":{"pageNumber":0,"pageSize":2,"totalElements":2,"totalPages":1}}`
	responses := []scriptedResponse{
		{status: 200, contentType: "application/json", body: outputPage},
		{status: 200, contentType: "application/json", body: outputPage},
	}
	first, err, _ := runScriptCalls(t, 2, responses, 2, func(call int, tasks []vcfinstaller.Task) {
		if len(tasks) != 2 || tasks[0].ID != "A" || tasks[1].ID != "b" || tasks[0].Type == nil || *tasks[0].Type != "" || tasks[1].Type != nil {
			t.Fatalf("call %d output = %+v", call, tasks)
		}
		if call == 0 {
			*tasks[0].Type = "mutated"
		}
	})
	if err != nil || len(first) != 2 {
		t.Fatalf("fresh output calls failed: first=%+v err=%v", first, err)
	}
}

func TestRefreshResponseValidationTable(t *testing.T) {
	tests := []struct {
		name         string
		refresh      scriptedResponse
		wantAPIError bool
		wantStatus   int
	}{
		{name: "non-200", refresh: scriptedResponse{status: 500, contentType: "text/plain", body: "not JSON"}, wantAPIError: true, wantStatus: 500},
		{name: "missing JSON media type", refresh: scriptedResponse{status: 200, contentType: "text/plain", body: `"replacement"`}},
		{name: "JSON object", refresh: scriptedResponse{status: 200, contentType: "application/json", body: `{}`}},
		{name: "blank JSON string", refresh: scriptedResponse{status: 200, contentType: "application/json", body: `"  "`}},
		{name: "control byte JSON string", refresh: scriptedResponse{status: 200, contentType: "application/json", body: `"new\u0000token"`}},
		{name: "delete byte JSON string", refresh: scriptedResponse{status: 200, contentType: "application/json", body: `"new\u007ftoken"`}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			tasks, err, requestCount := runScript(t, 2, []scriptedResponse{
				{status: 401, contentType: "application/json", body: `{}`},
				test.refresh,
			})
			if tasks != nil || requestCount != 2 {
				t.Fatalf("tasks=%+v requests=%d, want nil and two", tasks, requestCount)
			}
			if test.wantAPIError {
				var apiError *vcfinstaller.APIError
				if !errors.As(err, &apiError) || apiError.OperationID != "refreshAccessToken" || apiError.StatusCode != test.wantStatus {
					t.Fatalf("error = %T %v, want refreshAccessToken APIError{%d}", err, err, test.wantStatus)
				}
				return
			}
			var protocolError *vcfinstaller.ProtocolError
			if !errors.As(err, &protocolError) || protocolError.OperationID != "refreshAccessToken" {
				t.Fatalf("error = %T %v, want refreshAccessToken ProtocolError", err, err)
			}
		})
	}
}

func TestTransportErrorsDoNotExposeTokens(t *testing.T) {
	const oldToken = "old-access-secret"
	const newToken = "new-access-secret"
	const refreshID = "refresh-token-secret"
	call := 0
	httpClient := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		call++
		switch call {
		case 1:
			return testResponse(request, http.StatusUnauthorized, "application/json", `{}`), nil
		case 2:
			return testResponse(request, http.StatusOK, "application/json", `"`+newToken+`"`), nil
		default:
			return nil, fmt.Errorf("transport included %s %s %s", oldToken, newToken, refreshID)
		}
	})}
	client, err := vcfinstaller.NewClient("https://127.0.0.1", oldToken, refreshID, httpClient)
	if err != nil {
		t.Fatal(err)
	}
	tasks, err := client.ListTasks(context.Background(), 2)
	if tasks != nil || err == nil || call != 3 {
		t.Fatalf("tasks=%+v err=%v calls=%d", tasks, err, call)
	}
	for _, secret := range []string{oldToken, newToken, refreshID} {
		if strings.Contains(err.Error(), secret) {
			t.Errorf("transport error exposed %q", secret)
		}
	}
}

func TestRedirectsAreNotFollowed(t *testing.T) {
	call := 0
	httpClient := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		call++
		if call == 1 {
			response := testResponse(request, http.StatusTemporaryRedirect, "application/json", `{}`)
			response.Header.Set("Location", "https://outside.invalid/not-in-contract")
			return response, nil
		}
		return testResponse(request, http.StatusOK, "application/json", `{"elements":[],"pageMetadata":{"pageNumber":0,"pageSize":2,"totalElements":0,"totalPages":0}}`), nil
	})}
	client, err := vcfinstaller.NewClient("https://vcf.invalid", "access", "refresh", httpClient)
	if err != nil {
		t.Fatal(err)
	}
	tasks, err := client.ListTasks(context.Background(), 2)
	if tasks != nil || call != 1 {
		t.Fatalf("tasks=%+v calls=%d, want nil and one request", tasks, call)
	}
	var apiError *vcfinstaller.APIError
	if !errors.As(err, &apiError) || apiError.OperationID != "getTasks" || apiError.StatusCode != http.StatusTemporaryRedirect {
		t.Fatalf("error = %T %v, want getTasks APIError{%d}", err, err, http.StatusTemporaryRedirect)
	}
}

type scriptedResponse struct {
	status      int
	contentType string
	body        string
}

func runScript(t *testing.T, pageSize int, responses []scriptedResponse) ([]vcfinstaller.Task, error, int) {
	t.Helper()
	var result []vcfinstaller.Task
	var resultErr error
	_, resultErr, count := runScriptCalls(t, pageSize, responses, 1, func(_ int, tasks []vcfinstaller.Task) {
		result = tasks
	})
	return result, resultErr, count
}

func runScriptCalls(t *testing.T, pageSize int, responses []scriptedResponse, calls int, inspect func(int, []vcfinstaller.Task)) ([]vcfinstaller.Task, error, int) {
	t.Helper()
	next := 0
	httpClient := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if next >= len(responses) {
			next++
			return testResponse(request, http.StatusInternalServerError, "application/json", `{}`), nil
		}
		response := responses[next]
		next++
		return testResponse(request, response.status, response.contentType, response.body), nil
	})}
	client, err := vcfinstaller.NewClient("https://vcf.invalid/", "access", "refresh", httpClient)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	var first []vcfinstaller.Task
	for call := 0; call < calls; call++ {
		tasks, callErr := client.ListTasks(context.Background(), pageSize)
		if callErr != nil {
			return first, callErr, next
		}
		if call == 0 {
			first = tasks
		}
		inspect(call, tasks)
	}
	return first, nil, next
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func testResponse(request *http.Request, status int, contentType, body string) *http.Response {
	header := make(http.Header)
	if contentType != "" {
		header.Set("Content-Type", contentType)
	}
	return &http.Response{
		StatusCode: status,
		Header:     header,
		Body:       io.NopCloser(strings.NewReader(body)),
		Request:    request,
	}
}

func assertSingleHeader(t *testing.T, header http.Header, name, want string) {
	t.Helper()
	values := header.Values(name)
	if len(values) != 1 || values[0] != want {
		t.Errorf("%s values = %q, want exactly [%q]", name, values, want)
	}
}

func countTarget(requests []contractmock.Request, target string) int {
	count := 0
	for _, request := range requests {
		if request.RawTarget == target {
			count++
		}
	}
	return count
}

func ExampleClient_ListTasks() {
	fmt.Println("the verifier uses only an ephemeral loopback service")
	// Output: the verifier uses only an ephemeral loopback service
}
