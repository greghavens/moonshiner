package vcfops_test

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"reflect"
	"testing"

	"example.com/vcfops"
	"example.com/vcfops/internal/mockvcf"
)

const (
	resourceA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
	resourceB = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
)

var _ error = vcfops.ErrNotImplemented

func TestCollectAlertsRefreshesWithoutLosingWork(t *testing.T) {
	server := mockvcf.NewServer()
	defer server.Close()

	client, err := vcfops.NewClient(server.URL(), vcfops.Credentials{
		Username: "ops-user",
		Password: "ops-pass",
	}, server.Client())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	alerts, err := client.CollectAlerts(context.Background(), vcfops.AlertQuery{
		ResourceIDs: []string{resourceA, resourceB},
		PageSize:    2,
	})
	if err != nil {
		t.Fatalf("CollectAlerts: %v", err)
	}

	want := []vcfops.Alert{
		{AlertID: "11111111-1111-4111-8111-111111111111", ResourceID: resourceA, AlertLevel: "WARNING", StartTimeUTC: 100, UpdateTimeUTC: 110},
		{AlertID: "22222222-2222-4222-8222-222222222222", ResourceID: resourceB, AlertLevel: "CRITICAL", StartTimeUTC: 200, UpdateTimeUTC: 210},
		{AlertID: "33333333-3333-4333-8333-333333333333", ResourceID: resourceA, AlertLevel: "INFORMATION", StartTimeUTC: 300, UpdateTimeUTC: 310},
	}
	if !reflect.DeepEqual(alerts, want) {
		t.Fatalf("alerts: got %#v, want %#v", alerts, want)
	}

	assertWireLog(t, server.Log.Entries())
}

func assertWireLog(t *testing.T, got []mockvcf.Request) {
	t.Helper()

	type wireWant struct {
		method        string
		path          string
		rawQuery      string
		authorization string
		contentType   string
		body          map[string]string
	}
	wants := []wireWant{
		{method: "POST", path: "/suite-api/api/auth/token/acquire", contentType: "application/json", body: map[string]string{"username": "ops-user", "password": "ops-pass"}},
		{method: "GET", path: "/suite-api/api/alerts", rawQuery: "page=0&pageSize=2&resourceId=aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa&resourceId=bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", authorization: "token-1"},
		{method: "GET", path: "/suite-api/api/alerts", rawQuery: "page=1&pageSize=2&resourceId=aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa&resourceId=bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", authorization: "token-1"},
		{method: "POST", path: "/suite-api/api/auth/token/acquire", contentType: "application/json", body: map[string]string{"username": "ops-user", "password": "ops-pass"}},
		{method: "GET", path: "/suite-api/api/alerts", rawQuery: "page=1&pageSize=2&resourceId=aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa&resourceId=bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", authorization: "token-2"},
	}
	if len(got) != len(wants) {
		t.Fatalf("request count: got %d, want %d\nrequests: %#v", len(got), len(wants), got)
	}

	for i, want := range wants {
		t.Run(want.method+" "+want.path+" #"+string(rune('0'+i)), func(t *testing.T) {
			req := got[i]
			if req.Method != want.method || req.Path != want.path || req.RawQuery != want.rawQuery {
				t.Errorf("request line: got %s %s?%s, want %s %s?%s", req.Method, req.Path, req.RawQuery, want.method, want.path, want.rawQuery)
			}
			if auth := req.Header.Get("Authorization"); auth != want.authorization {
				t.Errorf("Authorization: got %q, want %q", auth, want.authorization)
			}
			if contentType := req.Header.Get("Content-Type"); contentType != want.contentType {
				t.Errorf("Content-Type: got %q, want %q", contentType, want.contentType)
			}
			if req.Header.Get("Accept") != "application/json" {
				t.Errorf("Accept: got %q, want application/json", req.Header.Get("Accept"))
			}

			if want.body == nil {
				if len(req.Body) != 0 {
					t.Errorf("GET body must be empty, got %q", req.Body)
				}
				return
			}
			var body map[string]string
			if err := json.Unmarshal(req.Body, &body); err != nil {
				t.Fatalf("decode request body: %v", err)
			}
			if !reflect.DeepEqual(body, want.body) {
				t.Errorf("JSON body: got %#v, want %#v (unset authSource must be omitted)", body, want.body)
			}
		})
	}
}

func TestOptionalAlertFiltersAreOmitted(t *testing.T) {
	server := mockvcf.NewServer()
	defer server.Close()

	client, err := vcfops.NewClient(server.URL(), vcfops.Credentials{Username: "ops-user", Password: "ops-pass"}, server.Client())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if _, err := client.CollectAlerts(context.Background(), vcfops.AlertQuery{PageSize: 2}); err != nil {
		t.Fatalf("CollectAlerts: %v", err)
	}

	for _, req := range server.Log.Entries() {
		if req.Path != "/suite-api/api/alerts" {
			continue
		}
		query := mustParseQuery(t, req.RawQuery)
		for _, name := range []string{"id", "resourceId"} {
			if _, present := query[name]; present {
				t.Errorf("unset optional query field %q must be omitted; raw query %q", name, req.RawQuery)
			}
		}
	}
}

func TestSetOptionalFieldsAreSent(t *testing.T) {
	server := mockvcf.NewServer()
	defer server.Close()

	authSource := "corporate-sso"
	client, err := vcfops.NewClient(server.URL(), vcfops.Credentials{
		Username:   "ops-user",
		Password:   "ops-pass",
		AuthSource: &authSource,
	}, server.Client())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	ids := []string{
		"11111111-1111-4111-8111-111111111111",
		"33333333-3333-4333-8333-333333333333",
	}
	if _, err := client.CollectAlerts(context.Background(), vcfops.AlertQuery{IDs: ids, PageSize: 2}); err != nil {
		t.Fatalf("CollectAlerts: %v", err)
	}

	for _, req := range server.Log.Entries() {
		switch req.Path {
		case "/suite-api/api/auth/token/acquire":
			var body map[string]string
			if err := json.Unmarshal(req.Body, &body); err != nil {
				t.Fatalf("decode acquireToken body: %v", err)
			}
			want := map[string]string{"username": "ops-user", "password": "ops-pass", "authSource": authSource}
			if !reflect.DeepEqual(body, want) {
				t.Errorf("acquireToken body: got %#v, want %#v", body, want)
			}
		case "/suite-api/api/alerts":
			query := mustParseQuery(t, req.RawQuery)
			if got := query["id"]; !reflect.DeepEqual(got, ids) {
				t.Errorf("id filters: got %v, want %v", got, ids)
			}
			if _, present := query["resourceId"]; present {
				t.Errorf("unset resourceId must be omitted; raw query %q", req.RawQuery)
			}
		}
	}
}

func mustParseQuery(t *testing.T, raw string) map[string][]string {
	t.Helper()
	req, err := http.NewRequest(http.MethodGet, "http://loopback/?"+raw, nil)
	if err != nil {
		t.Fatal(err)
	}
	return req.URL.Query()
}

func TestMockServesOnlyContractOperations(t *testing.T) {
	server := mockvcf.NewServer()
	defer server.Close()

	checks := []struct {
		method string
		path   string
	}{
		{method: http.MethodGet, path: "/suite-api/api/resources"},
		{method: http.MethodPost, path: "/suite-api/api/alerts"},
		{method: http.MethodGet, path: "/suite-api/api/auth/token/acquire"},
	}
	for _, check := range checks {
		t.Run(check.method+" "+check.path, func(t *testing.T) {
			req, err := http.NewRequest(check.method, server.URL()+check.path, nil)
			if err != nil {
				t.Fatal(err)
			}
			resp, err := server.Client().Do(req)
			if err != nil {
				t.Fatal(err)
			}
			_, _ = io.Copy(io.Discard, resp.Body)
			_ = resp.Body.Close()
			if resp.StatusCode != http.StatusNotFound {
				t.Fatalf("status: got %d, want 404", resp.StatusCode)
			}
		})
	}
}

func TestOfficialContractPin(t *testing.T) {
	type source struct {
		Tag          string   `json:"tag"`
		CommitSHA    string   `json:"commit_sha"`
		SpecPath     string   `json:"spec_path"`
		OperationIDs []string `json:"operation_ids"`
	}
	data, err := os.ReadFile("docs/official_sources.json")
	if err != nil {
		t.Fatal(err)
	}
	var got source
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatal(err)
	}
	want := source{
		Tag:          "9.0.0.0",
		CommitSHA:    "85151f6b1bb58f13b6ac0304bfec53904bea085f",
		SpecPath:     "specifications/vcf-operations/vcf-operations-openapi.json",
		OperationIDs: []string{mockvcf.AcquireTokenOperationID, mockvcf.GetAlertsOperationID},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("official source pin: got %#v, want %#v", got, want)
	}

	contractData, err := os.ReadFile("docs/contract.json")
	if err != nil {
		t.Fatal(err)
	}
	var contract struct {
		Source struct {
			Tag       string `json:"tag"`
			CommitSHA string `json:"commit_sha"`
			Path      string `json:"path"`
		} `json:"source"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(contractData, &contract); err != nil {
		t.Fatal(err)
	}
	if contract.Source.Tag != want.Tag || contract.Source.CommitSHA != want.CommitSHA || contract.Source.Path != want.SpecPath {
		t.Fatalf("contract source pin does not match official_sources.json: %#v", contract.Source)
	}
	wantOperations := []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	}{
		{OperationID: "acquireToken", Method: "POST", Path: "/api/auth/token/acquire"},
		{OperationID: "getAlerts", Method: "GET", Path: "/api/alerts"},
	}
	if !reflect.DeepEqual(contract.Operations, wantOperations) {
		t.Fatalf("contract operations: got %#v, want %#v", contract.Operations, wantOperations)
	}
}
