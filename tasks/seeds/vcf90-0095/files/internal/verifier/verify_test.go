package verifier_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"

	"example.com/vcfopslogs/internal/contractmock"
	"example.com/vcfopslogs/logs"
)

const (
	contractPath = "../../docs/contract.json"
	sourcesPath  = "../../docs/official_sources.json"
	commitSHA    = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
	specPath     = "specifications/vcf-operations/vcf-operations-for-logs-openapi.json"
)

func TestDerivedContractProvenance(t *testing.T) {
	t.Parallel()

	readJSON := func(path string) map[string]any {
		t.Helper()
		raw, err := os.ReadFile(filepath.Clean(path))
		if err != nil {
			t.Fatal(err)
		}
		var value map[string]any
		if err := json.Unmarshal(raw, &value); err != nil {
			t.Fatal(err)
		}
		return value
	}

	contract := readJSON(contractPath)
	if got := contract["title"]; got != "VCF Operations for Logs" {
		t.Fatalf("contract title = %v; this must be the 9.0 Operations for Logs spec, not the renamed 9.1 product", got)
	}
	if got := contract["serverBasePath"]; got != "/api/v2" {
		t.Fatalf("serverBasePath = %v", got)
	}
	source := contract["source"].(map[string]any)
	assertSourceFields(t, source)

	operations := contract["operations"].([]any)
	gotOperations := make(map[string]map[string]any, len(operations))
	for _, raw := range operations {
		op := raw.(map[string]any)
		gotOperations[op["operationId"].(string)] = op
	}
	if len(gotOperations) != 2 {
		t.Fatalf("contract operations = %v", sortedKeys(gotOperations))
	}
	getOp := gotOperations["GET_log-forwarder"]
	putOp := gotOperations["PUT_log-forwarder-id"]
	if getOp == nil || getOp["method"] != "GET" || getOp["path"] != "/log-forwarder" {
		t.Fatalf("GET_log-forwarder contract is missing or incorrect: %v", getOp)
	}
	if putOp == nil || putOp["method"] != "PUT" || putOp["path"] != "/log-forwarder/{id}" {
		t.Fatalf("PUT_log-forwarder-id contract is missing or incorrect: %v", putOp)
	}
	query := getOp["queryParameters"].([]any)[0].(map[string]any)
	if query["name"] != "showDetails" || query["required"] != false {
		t.Fatalf("showDetails contract = %v", query)
	}

	schemas := contract["schemas"].(map[string]any)
	update := schemas["forwarders.put.request"].(map[string]any)
	required := stringsFromAny(update["required"].([]any))
	if !reflect.DeepEqual(required, []string{"host", "port", "protocol", "sslEnabled"}) {
		t.Fatalf("update required fields = %v", required)
	}
	properties := update["properties"].(map[string]any)
	for _, name := range []string{"acceptCert", "name", "host", "port", "protocol", "sslEnabled", "workerCount", "diskCacheSize", "tags", "filter", "transportProtocol", "forwardComplementaryFields", "testConnection"} {
		if properties[name] == nil {
			t.Fatalf("update schema is missing %q", name)
		}
	}

	sources := readJSON(sourcesPath)
	assertSourceFields(t, sources)
	ids := stringsFromAny(sources["operationIds"].([]any))
	if !reflect.DeepEqual(ids, []string{"GET_log-forwarder", "PUT_log-forwarder-id"}) {
		t.Fatalf("official source operationIds = %v", ids)
	}
	if specURL := sources["specUrl"].(string); !strings.Contains(specURL, commitSHA+"/"+specPath) {
		t.Fatalf("official spec URL is not commit-pinned: %s", specURL)
	}
}

func TestNewClientAcceptsNilHTTPClient(t *testing.T) {
	mock := contractmock.New(t, contractPath, contractmock.Config{
		List: contractmock.Response{Status: http.StatusOK, Body: `[]`},
	})
	previousDefault := http.DefaultClient
	http.DefaultClient = mock.Client()
	t.Cleanup(func() { http.DefaultClient = previousDefault })

	client, err := logs.NewClient(mock.URL(), "session-123", nil)
	if err != nil {
		t.Fatalf("NewClient rejected an omitted HTTP client: %v", err)
	}
	if client == nil {
		t.Fatal("NewClient returned a nil client")
	}
	forwarders, err := client.ListForwarders(context.Background(), nil)
	if err != nil {
		t.Fatalf("client using default HTTP client failed: %v", err)
	}
	if len(forwarders) != 0 || len(mock.Requests()) != 1 {
		t.Fatalf("default HTTP client result = %#v, requests = %d", forwarders, len(mock.Requests()))
	}
}

func TestListForwardersWireShape(t *testing.T) {
	trueValue, falseValue := true, false
	tests := []struct {
		name        string
		showDetails *bool
		query       string
	}{
		{name: "unset is omitted", showDetails: nil, query: ""},
		{name: "explicit false is retained", showDetails: &falseValue, query: "showDetails=false"},
		{name: "explicit true is retained", showDetails: &trueValue, query: "showDetails=true"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			mock := contractmock.New(t, contractPath, contractmock.Config{
				List: contractmock.Response{Status: http.StatusOK, Body: `[{"name":"primary","host":"logs-a.example.test","port":9543,"protocol":"CFAPI","sslEnabled":true,"workerCount":4,"diskCacheSize":1000000000,"tags":{},"filter":"","forwardComplementaryFields":false,"id":"fw-a"}]`},
			})
			if err := mock.AssertLoopback(); err != nil {
				t.Fatal(err)
			}
			client, err := logs.NewClient(mock.URL(), "session-123", mock.Client())
			if err != nil {
				t.Fatal(err)
			}
			forwarders, err := client.ListForwarders(context.Background(), tt.showDetails)
			if err != nil {
				t.Fatal(err)
			}
			if len(forwarders) != 1 || forwarders[0].ID != "fw-a" || forwarders[0].Host != "logs-a.example.test" {
				t.Fatalf("decoded forwarders = %#v", forwarders)
			}
			requests := mock.Requests()
			if len(requests) != 1 {
				t.Fatalf("request count = %d", len(requests))
			}
			request := requests[0]
			if request.Method != http.MethodGet || request.Path != "/api/v2/log-forwarder" || request.RawQuery != tt.query {
				t.Fatalf("wire target = %s %s?%s", request.Method, request.Path, request.RawQuery)
			}
			assertCommonHeaders(t, request, false)
			if len(request.Body) != 0 {
				t.Fatalf("GET body = %q", request.Body)
			}
		})
	}
}

func TestUpdateForwarderWireShape(t *testing.T) {
	zero := 0
	zero64 := int64(0)
	falseValue := false
	empty := ""
	emptyTags := map[string]string{}
	tests := []struct {
		name    string
		request logs.UpdateForwarderRequest
		want    map[string]any
	}{
		{
			name: "unset optional fields are absent",
			request: logs.UpdateForwarderRequest{
				Host: "new-logs.example.test", Port: 9543, Protocol: "CFAPI", SSLEnabled: false,
			},
			want: map[string]any{
				"host": "new-logs.example.test", "port": float64(9543), "protocol": "CFAPI", "sslEnabled": false,
			},
		},
		{
			name: "explicit optional zero values are present",
			request: logs.UpdateForwarderRequest{
				Host: "new-logs.example.test", Port: 9000, Protocol: "SYSLOG", SSLEnabled: true,
				AcceptCert: &falseValue, Name: &empty, WorkerCount: &zero, DiskCacheSize: &zero64,
				Tags: &emptyTags, Filter: &empty, TransportProtocol: &empty,
				ForwardComplementaryFields: &falseValue, TestConnection: &falseValue,
			},
			want: map[string]any{
				"host": "new-logs.example.test", "port": float64(9000), "protocol": "SYSLOG", "sslEnabled": true,
				"acceptCert": false, "name": "", "workerCount": float64(0), "diskCacheSize": float64(0),
				"tags": map[string]any{}, "filter": "", "transportProtocol": "",
				"forwardComplementaryFields": false, "testConnection": false,
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			mock := contractmock.New(t, contractPath, contractmock.Config{
				Updates: map[string]contractmock.Response{
					"fw-primary": {Status: http.StatusOK, Body: `{"name":"primary","host":"new-logs.example.test","port":9543,"protocol":"CFAPI","sslEnabled":false,"workerCount":4,"diskCacheSize":1000000000,"tags":{},"filter":"","forwardComplementaryFields":false,"id":"fw-primary"}`},
				},
			})
			client, err := logs.NewClient(mock.URL(), "session-123", mock.Client())
			if err != nil {
				t.Fatal(err)
			}
			forwarder, err := client.UpdateForwarder(context.Background(), "fw-primary", tt.request)
			if err != nil {
				t.Fatal(err)
			}
			if forwarder.ID != "fw-primary" {
				t.Fatalf("decoded forwarder = %#v", forwarder)
			}
			requests := mock.Requests()
			if len(requests) != 1 {
				t.Fatalf("request count = %d", len(requests))
			}
			request := requests[0]
			if request.Method != http.MethodPut || request.Path != "/api/v2/log-forwarder/fw-primary" || request.RawQuery != "" {
				t.Fatalf("wire target = %s %s?%s", request.Method, request.Path, request.RawQuery)
			}
			assertCommonHeaders(t, request, true)
			var got map[string]any
			if err := json.Unmarshal(request.Body, &got); err != nil {
				t.Fatalf("request is not JSON: %v", err)
			}
			if !reflect.DeepEqual(got, tt.want) {
				t.Fatalf("request body\n got: %#v\nwant: %#v", got, tt.want)
			}
		})
	}
}

func TestUpdateForwarderEscapesIDAsOnePathSegment(t *testing.T) {
	t.Parallel()

	mock := contractmock.New(t, contractPath, contractmock.Config{
		Updates: map[string]contractmock.Response{
			"team A/fw?primary#1": {Status: http.StatusOK, Body: `{"name":"one","host":"logs.example.test","port":9543,"protocol":"CFAPI","sslEnabled":true,"workerCount":4,"diskCacheSize":1000000000,"tags":{},"filter":"","forwardComplementaryFields":false,"id":"team A/fw?primary#1"}`},
		},
	})
	client, err := logs.NewClient(mock.URL(), "session-123", mock.Client())
	if err != nil {
		t.Fatal(err)
	}
	_, err = client.UpdateForwarder(context.Background(), "team A/fw?primary#1", logs.UpdateForwarderRequest{
		Host: "logs.example.test", Port: 9543, Protocol: "CFAPI", SSLEnabled: true,
	})
	if err != nil {
		t.Fatal(err)
	}
	if got := mock.Requests()[0].Path; got != "/api/v2/log-forwarder/team%20A%2Ffw%3Fprimary%231" {
		t.Fatalf("escaped path = %q", got)
	}
}

func TestUpdateForwarderReturnsTypedAPIErrorForPlainErrorBody(t *testing.T) {
	t.Parallel()

	mock := contractmock.New(t, contractPath, contractmock.Config{
		Updates: map[string]contractmock.Response{
			"fw-expired": {Status: http.StatusUnauthorized, Body: "session expired"},
		},
	})
	client, err := logs.NewClient(mock.URL(), "expired-session", mock.Client())
	if err != nil {
		t.Fatal(err)
	}
	_, updateErr := client.UpdateForwarder(context.Background(), "fw-expired", requiredUpdate("logs.example.test"))
	if updateErr == nil {
		t.Fatal("UpdateForwarder returned nil error for HTTP 401")
	}
	var apiErr *logs.APIError
	if !errors.As(updateErr, &apiErr) {
		t.Fatalf("UpdateForwarder error type = %T; want *logs.APIError", updateErr)
	}
	if apiErr.StatusCode != http.StatusUnauthorized || apiErr.ErrorMessage != "session expired" || apiErr.ErrorCode != "" || apiErr.ErrorDetails != nil {
		t.Fatalf("API error = %#v", apiErr)
	}
}

func TestClientIsSafeForConcurrentCallers(t *testing.T) {
	t.Parallel()

	mock := contractmock.New(t, contractPath, contractmock.Config{
		List: contractmock.Response{Status: http.StatusOK, Body: `[{"name":"shared","host":"logs.example.test","port":9543,"protocol":"CFAPI","sslEnabled":true,"workerCount":4,"diskCacheSize":1000000000,"tags":{},"filter":"","forwardComplementaryFields":false,"id":"fw-shared"}]`},
		Updates: map[string]contractmock.Response{
			"fw-shared": {Status: http.StatusOK, Body: `{"name":"shared","host":"logs.example.test","port":9543,"protocol":"CFAPI","sslEnabled":true,"workerCount":4,"diskCacheSize":1000000000,"tags":{},"filter":"","forwardComplementaryFields":false,"id":"fw-shared"}`},
		},
	})
	client, err := logs.NewClient(mock.URL(), "session-123", mock.Client())
	if err != nil {
		t.Fatal(err)
	}

	const callers = 12
	errorsByCall := make(chan error, callers*2)
	var wait sync.WaitGroup
	for i := 0; i < callers; i++ {
		showDetails := i%2 == 0
		wait.Add(2)
		go func() {
			defer wait.Done()
			_, callErr := client.ListForwarders(context.Background(), &showDetails)
			errorsByCall <- callErr
		}()
		go func() {
			defer wait.Done()
			_, callErr := client.UpdateForwarder(context.Background(), "fw-shared", requiredUpdate("logs.example.test"))
			errorsByCall <- callErr
		}()
	}
	wait.Wait()
	close(errorsByCall)
	for callErr := range errorsByCall {
		if callErr != nil {
			t.Fatalf("concurrent call failed: %v", callErr)
		}
	}

	requests := mock.Requests()
	if len(requests) != callers*2 {
		t.Fatalf("request count = %d", len(requests))
	}
	getCount, putCount := 0, 0
	for _, request := range requests {
		switch request.Method {
		case http.MethodGet:
			getCount++
			if request.Path != "/api/v2/log-forwarder" || (request.RawQuery != "showDetails=true" && request.RawQuery != "showDetails=false") {
				t.Fatalf("concurrent GET target = %s?%s", request.Path, request.RawQuery)
			}
			assertCommonHeaders(t, request, false)
		case http.MethodPut:
			putCount++
			if request.Path != "/api/v2/log-forwarder/fw-shared" || request.RawQuery != "" {
				t.Fatalf("concurrent PUT target = %s?%s", request.Path, request.RawQuery)
			}
			assertCommonHeaders(t, request, true)
		default:
			t.Fatalf("unexpected concurrent request method %q", request.Method)
		}
	}
	if getCount != callers || putCount != callers {
		t.Fatalf("request methods: GET=%d PUT=%d", getCount, putCount)
	}
}

func TestApplyForwarderUpdatesRetainsEarlierSuccessWhenLaterStepFails(t *testing.T) {
	t.Parallel()

	mock := contractmock.New(t, contractPath, contractmock.Config{
		Updates: map[string]contractmock.Response{
			"fw-a": {Status: http.StatusOK, Body: `{"name":"primary","host":"logs-a-new.example.test","port":9543,"protocol":"CFAPI","sslEnabled":true,"workerCount":4,"diskCacheSize":1000000000,"tags":{},"filter":"","forwardComplementaryFields":false,"id":"fw-a"}`},
			"fw-b": {Status: http.StatusOK, Body: `{"name":"secondary","host":"logs-b-new.example.test","port":9543,"protocol":"CFAPI","sslEnabled":true,"workerCount":4,"diskCacheSize":1000000000,"tags":{},"filter":"","forwardComplementaryFields":false,"id":"fw-b"}`},
			"fw-c": {Status: http.StatusInternalServerError, Body: `{"errorMessage":"capacity exhausted","errorCode":"LIMIT_ERROR","errorDetails":{"node":"logs-2"}}`},
			"fw-d": {Status: http.StatusOK, Body: `{"name":"fourth","host":"logs-d-new.example.test","port":9543,"protocol":"CFAPI","sslEnabled":true,"workerCount":4,"diskCacheSize":1000000000,"tags":{},"filter":"","forwardComplementaryFields":false,"id":"fw-d"}`},
		},
	})
	client, err := logs.NewClient(mock.URL(), "session-123", mock.Client())
	if err != nil {
		t.Fatal(err)
	}
	changes := []logs.ForwarderChange{
		{ID: "fw-a", Request: requiredUpdate("logs-a-new.example.test")},
		{ID: "fw-b", Request: requiredUpdate("logs-b-new.example.test")},
		{ID: "fw-c", Request: requiredUpdate("logs-c-new.example.test")},
		{ID: "fw-d", Request: requiredUpdate("logs-d-new.example.test")},
	}
	results, applyErr := client.ApplyForwarderUpdates(context.Background(), changes)
	if applyErr == nil {
		t.Fatal("ApplyForwarderUpdates returned nil error")
	}
	if len(results) != 3 {
		t.Fatalf("result count = %d; want one result per attempted step", len(results))
	}
	if first := results[0]; first.ID != "fw-a" || first.Err != nil || first.Forwarder == nil || first.Forwarder.Host != "logs-a-new.example.test" {
		t.Fatalf("first result lost or inaccurate: %#v", first)
	}
	if second := results[1]; second.ID != "fw-b" || second.Err != nil || second.Forwarder == nil || second.Forwarder.Host != "logs-b-new.example.test" {
		t.Fatalf("second result lost or inaccurate: %#v", second)
	}
	failed := results[2]
	if failed.ID != "fw-c" || failed.Forwarder != nil || failed.Err == nil {
		t.Fatalf("failed result is inaccurate: %#v", failed)
	}
	var resultAPIError, returnedAPIError *logs.APIError
	if !errors.As(failed.Err, &resultAPIError) || !errors.As(applyErr, &returnedAPIError) {
		t.Fatalf("errors are not typed APIError values: result=%T returned=%T", failed.Err, applyErr)
	}
	for name, apiErr := range map[string]*logs.APIError{"result": resultAPIError, "returned": returnedAPIError} {
		if apiErr.StatusCode != http.StatusInternalServerError || apiErr.ErrorMessage != "capacity exhausted" || apiErr.ErrorCode != "LIMIT_ERROR" || apiErr.ErrorDetails["node"] != "logs-2" {
			t.Fatalf("%s API error = %#v", name, apiErr)
		}
	}
	requests := mock.Requests()
	if len(requests) != 3 {
		t.Fatalf("request count = %d; the fourth step must not be attempted", len(requests))
	}
	if requests[0].Path != "/api/v2/log-forwarder/fw-a" || requests[1].Path != "/api/v2/log-forwarder/fw-b" || requests[2].Path != "/api/v2/log-forwarder/fw-c" {
		t.Fatalf("request order = %q, %q, %q", requests[0].Path, requests[1].Path, requests[2].Path)
	}
}

func TestContractMockRejectsUnselectedOperations(t *testing.T) {
	t.Parallel()

	mock := contractmock.New(t, contractPath, contractmock.Config{})
	response, err := mock.Client().Post(mock.URL()+"/api/v2/log-forwarder", "application/json", strings.NewReader(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("unselected POST status = %d", response.StatusCode)
	}
}

func assertSourceFields(t *testing.T, source map[string]any) {
	t.Helper()
	if source["repository"] != "https://github.com/vmware/vcf-api-specs" || source["license"] != "Apache-2.0" || source["tag"] != "9.0.0.0" || source["commitSha"] != commitSHA || source["specPath"] != specPath {
		t.Fatalf("incorrect official source provenance: %v", source)
	}
}

func assertCommonHeaders(t *testing.T, request contractmock.Request, hasBody bool) {
	t.Helper()
	if got := request.Header.Get("Authorization"); got != "Bearer session-123" {
		t.Fatalf("Authorization = %q", got)
	}
	if got := request.Header.Get("Accept"); got != "application/json" {
		t.Fatalf("Accept = %q", got)
	}
	if got := request.Header.Get("Content-Type"); hasBody && got != "application/json" {
		t.Fatalf("Content-Type = %q", got)
	} else if !hasBody && got != "" {
		t.Fatalf("bodyless request Content-Type = %q", got)
	}
}

func requiredUpdate(host string) logs.UpdateForwarderRequest {
	return logs.UpdateForwarderRequest{Host: host, Port: 9543, Protocol: "CFAPI", SSLEnabled: true}
}

func stringsFromAny(values []any) []string {
	strings := make([]string, len(values))
	for i, value := range values {
		strings[i] = value.(string)
	}
	return strings
}

func sortedKeys[V any](values map[string]V) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}
