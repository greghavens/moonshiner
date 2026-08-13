package vcfops

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"strings"
	"testing"

	"example.com/vcfops/internal/mockvcf"
)

func TestListAllSymptomDefinitions(t *testing.T) {
	definitions := fixtureDefinitions()
	tests := []struct {
		name         string
		opts         ListSymptomDefinitionsOptions
		wantIDs      []string
		wantRequests []string
	}{
		{
			name:    "all pages with optional filters unset",
			opts:    ListSymptomDefinitionsOptions{PageSize: 2},
			wantIDs: []string{"id-1", "id-4", "id-2", "id-5", "id-3"},
			wantRequests: []string{
				"/suite-api/api/symptomdefinitions?page=0&pageSize=2",
				"/suite-api/api/symptomdefinitions?page=1&pageSize=2",
				"/suite-api/api/symptomdefinitions?page=2&pageSize=2",
			},
		},
		{
			name: "filters use contract names and repeated id values",
			opts: ListSymptomDefinitionsOptions{
				AdapterKind:  "VMWARE",
				ResourceKind: "VirtualMachine",
				IDs:          []string{"id-4", "id-1"},
				Name:         "alpha",
				PageSize:     1,
			},
			wantIDs: []string{"id-1", "id-4"},
			wantRequests: []string{
				"/suite-api/api/symptomdefinitions?adapterKind=VMWARE&id=id-4&id=id-1&name=alpha&page=0&pageSize=1&resourceKind=VirtualMachine",
				"/suite-api/api/symptomdefinitions?adapterKind=VMWARE&id=id-4&id=id-1&name=alpha&page=1&pageSize=1&resourceKind=VirtualMachine",
			},
		},
		{
			name:    "zero page size uses specification default",
			opts:    ListSymptomDefinitionsOptions{},
			wantIDs: []string{"id-1", "id-4", "id-2", "id-5", "id-3"},
			wantRequests: []string{
				"/suite-api/api/symptomdefinitions?page=0&pageSize=1000",
			},
		},
		{
			name:    "explicit empty id slice is omitted",
			opts:    ListSymptomDefinitionsOptions{IDs: []string{}, PageSize: 1000},
			wantIDs: []string{"id-1", "id-4", "id-2", "id-5", "id-3"},
			wantRequests: []string{
				"/suite-api/api/symptomdefinitions?page=0&pageSize=1000",
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := mockvcf.New(definitions)
			defer server.Close()

			client, err := NewClient(server.URL(), "token-123", server.Client())
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			got, err := client.ListAllSymptomDefinitions(context.Background(), tt.opts)
			if err != nil {
				t.Fatalf("ListAllSymptomDefinitions: %v", err)
			}
			if gotIDs := ids(got); !reflect.DeepEqual(gotIDs, tt.wantIDs) {
				t.Fatalf("IDs = %v, want %v", gotIDs, tt.wantIDs)
			}
			if tt.name == "all pages with optional filters unset" {
				assertFullPayloads(t, got, definitions)
			}

			requests := server.Requests()
			if len(requests) != len(tt.wantRequests) {
				t.Fatalf("request count = %d, want %d: %#v", len(requests), len(tt.wantRequests), requests)
			}
			for i, request := range requests {
				if request.Method != http.MethodGet {
					t.Errorf("request %d method = %q, want GET", i, request.Method)
				}
				assertEquivalentRequestURI(t, request.RequestURI, tt.wantRequests[i])
				if request.Header.Get("Authorization") != "token-123" {
					t.Errorf("request %d Authorization = %q", i, request.Header.Get("Authorization"))
				}
				if request.Header.Get("Accept") != "application/json" {
					t.Errorf("request %d Accept = %q", i, request.Header.Get("Accept"))
				}
				if request.Body != "" {
					t.Errorf("request %d body = %q, want empty", i, request.Body)
				}
				if request.Header.Get("Content-Type") != "" {
					t.Errorf("request %d Content-Type = %q, want omitted", i, request.Header.Get("Content-Type"))
				}
			}

			if tt.name == "all pages with optional filters unset" {
				for _, request := range requests {
					requestURL, err := url.ParseRequestURI(request.RequestURI)
					if err != nil {
						t.Fatalf("parse captured request URI %q: %v", request.RequestURI, err)
					}
					for _, absent := range []string{"adapterKind", "resourceKind", "id", "name"} {
						if _, present := requestURL.Query()[absent]; present {
							t.Errorf("unset optional field %q was sent in %q", absent, request.RequestURI)
						}
					}
				}
			}
		})
	}
}

func TestListAllSymptomDefinitionsUsesNormalURLQueryEncoding(t *testing.T) {
	requestURI := ""
	transport := roundTripFunc(func(req *http.Request) (*http.Response, error) {
		requestURI = req.URL.RequestURI()
		return &http.Response{
			StatusCode: http.StatusOK,
			Header:     make(http.Header),
			Body: io.NopCloser(strings.NewReader(
				`{"pageInfo":{"totalCount":0,"page":0,"pageSize":2},"symptomDefinitions":[]}`,
			)),
			Request: req,
		}, nil
	})
	client, err := NewClient("https://vcf.example", "token-123", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	opts := ListSymptomDefinitionsOptions{
		AdapterKind:  "VM WARE",
		ResourceKind: "Virtual/Machine",
		IDs:          []string{"id one", "id&two"},
		Name:         "a b&c/d",
		PageSize:     2,
	}

	got, err := client.ListAllSymptomDefinitions(context.Background(), opts)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Fatalf("definitions = %#v, want empty", got)
	}
	const want = "/suite-api/api/symptomdefinitions?adapterKind=VM+WARE&id=id+one&id=id%26two&name=a+b%26c%2Fd&page=0&pageSize=2&resourceKind=Virtual%2FMachine"
	assertEquivalentRequestURI(t, requestURI, want)
}

func TestListAllSymptomDefinitionsResponseErrors(t *testing.T) {
	const oneDefinition = `{"pageInfo":{"totalCount":2,"page":0,"pageSize":1},"symptomDefinitions":[{"id":"id-1","name":"Alpha","adapterKindKey":"VMWARE","resourceKindKey":"VirtualMachine","state":{}}]}`
	const emptySecondPage = `{"pageInfo":{"totalCount":2,"page":1,"pageSize":1},"symptomDefinitions":[]}`

	tests := []struct {
		name      string
		statuses  []int
		bodies    []string
		wantCalls int
	}{
		{
			name:      "redirect is a non-2xx response",
			statuses:  []int{http.StatusFound},
			bodies:    []string{`redirect`},
			wantCalls: 1,
		},
		{
			name:      "server error",
			statuses:  []int{http.StatusServiceUnavailable},
			bodies:    []string{`unavailable`},
			wantCalls: 1,
		},
		{
			name:      "malformed JSON",
			statuses:  []int{http.StatusOK},
			bodies:    []string{`{"pageInfo":`},
			wantCalls: 1,
		},
		{
			name:      "multiple JSON documents",
			statuses:  []int{http.StatusOK},
			bodies:    []string{`{"pageInfo":{"totalCount":0},"symptomDefinitions":[]} {}`},
			wantCalls: 1,
		},
		{
			name:      "empty page before advertised total",
			statuses:  []int{http.StatusOK, http.StatusOK},
			bodies:    []string{oneDefinition, emptySecondPage},
			wantCalls: 2,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			calls := 0
			transport := roundTripFunc(func(req *http.Request) (*http.Response, error) {
				if calls >= len(tt.bodies) {
					calls++
					return nil, errors.New("unexpected extra request")
				}
				response := &http.Response{
					StatusCode: tt.statuses[calls],
					Header:     make(http.Header),
					Body:       io.NopCloser(strings.NewReader(tt.bodies[calls])),
					Request:    req,
				}
				calls++
				return response, nil
			})
			client, err := NewClient("https://vcf.example", "token-123", &http.Client{Transport: transport})
			if err != nil {
				t.Fatal(err)
			}

			if _, err := client.ListAllSymptomDefinitions(context.Background(), ListSymptomDefinitionsOptions{PageSize: 1}); err == nil {
				t.Fatal("ListAllSymptomDefinitions returned nil error")
			}
			if calls != tt.wantCalls {
				t.Fatalf("request count = %d, want %d", calls, tt.wantCalls)
			}
		})
	}
}

func TestListAllSymptomDefinitionsUsesSuppliedContextAndHTTPClient(t *testing.T) {
	type contextKey struct{}
	transportError := errors.New("transport sentinel")
	sawContextValue := false
	transport := roundTripFunc(func(req *http.Request) (*http.Response, error) {
		sawContextValue = req.Context().Value(contextKey{}) == "context sentinel"
		return nil, transportError
	})
	client, err := NewClient("https://vcf.example", "token-123", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.WithValue(context.Background(), contextKey{}, "context sentinel")

	if _, err := client.ListAllSymptomDefinitions(ctx, ListSymptomDefinitionsOptions{PageSize: 1}); !errors.Is(err, transportError) {
		t.Fatalf("error = %v, want wrapped transport sentinel", err)
	}
	if !sawContextValue {
		t.Fatal("supplied context was not attached to the request sent through the supplied HTTP client")
	}
}

func TestListAllSymptomDefinitionsRejectsNegativePageSizeWithoutRequest(t *testing.T) {
	server := mockvcf.New(fixtureDefinitions())
	defer server.Close()
	client, err := NewClient(server.URL(), "token-123", server.Client())
	if err != nil {
		t.Fatal(err)
	}

	if _, err := client.ListAllSymptomDefinitions(context.Background(), ListSymptomDefinitionsOptions{PageSize: -1}); err == nil {
		t.Fatal("negative page size returned nil error")
	}
	if requests := server.Requests(); len(requests) != 0 {
		t.Fatalf("negative page size made %d requests", len(requests))
	}
}

func TestMockServesOnlyContractOperation(t *testing.T) {
	server := mockvcf.New(nil)
	defer server.Close()

	req, err := http.NewRequest(http.MethodGet, server.URL()+"/suite-api/api/resources", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "token-123")
	response, err := server.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, response.Body)
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("uncontracted route status = %d, want 404", response.StatusCode)
	}
}

func TestOfficialContractProvenance(t *testing.T) {
	type sourcesDocument struct {
		License    string `json:"license"`
		Tag        string `json:"tag"`
		CommitSHA  string `json:"commit_sha"`
		SpecPath   string `json:"spec_path"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operations"`
	}

	raw, err := os.ReadFile("docs/official_sources.json")
	if err != nil {
		t.Fatal(err)
	}
	var got sourcesDocument
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if got.License != "Apache-2.0" || got.Tag != "9.0.0.0" ||
		got.CommitSHA != "85151f6b1bb58f13b6ac0304bfec53904bea085f" ||
		got.SpecPath != "specifications/vcf-operations/vcf-operations-openapi.json" {
		t.Fatalf("unexpected official source provenance: %#v", got)
	}
	if len(got.Operations) != 1 || got.Operations[0].OperationID != mockvcf.OperationID ||
		got.Operations[0].Method != http.MethodGet || got.Operations[0].Path != "/api/symptomdefinitions" {
		t.Fatalf("unexpected operation provenance: %#v", got.Operations)
	}
}

func TestContractNamesOnlyMockOperation(t *testing.T) {
	type contractDocument struct {
		OperationIDs []string `json:"x-operationIds"`
		Servers      []struct {
			URL string `json:"url"`
		} `json:"servers"`
		Paths map[string]map[string]struct {
			OperationID string `json:"operationId"`
			Parameters  []struct {
				Name string `json:"name"`
				In   string `json:"in"`
			} `json:"parameters"`
		} `json:"paths"`
	}

	raw, err := os.ReadFile("docs/contract.json")
	if err != nil {
		t.Fatal(err)
	}
	var contract contractDocument
	if err := json.Unmarshal(raw, &contract); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(contract.OperationIDs, []string{mockvcf.OperationID}) {
		t.Fatalf("contract operationIds = %v", contract.OperationIDs)
	}
	if len(contract.Servers) != 1 || contract.Servers[0].URL != "/suite-api" {
		t.Fatalf("contract servers = %#v", contract.Servers)
	}
	if len(contract.Paths) != 1 {
		t.Fatalf("contract has %d paths, want one", len(contract.Paths))
	}
	operation, ok := contract.Paths["/api/symptomdefinitions"]["get"]
	if !ok || operation.OperationID != mockvcf.OperationID {
		t.Fatalf("contract GET operation = %#v", operation)
	}
	wantParameters := []string{"adapterKind", "resourceKind", "id", "name", "page", "pageSize"}
	gotParameters := make([]string, len(operation.Parameters))
	for i, parameter := range operation.Parameters {
		if parameter.In != "query" {
			t.Fatalf("parameter %q is in %q, want query", parameter.Name, parameter.In)
		}
		gotParameters[i] = parameter.Name
	}
	if !reflect.DeepEqual(gotParameters, wantParameters) {
		t.Fatalf("contract parameters = %v, want %v", gotParameters, wantParameters)
	}
}

func ids(definitions []SymptomDefinition) []string {
	out := make([]string, len(definitions))
	for i, definition := range definitions {
		out[i] = definition.ID
	}
	return out
}

func assertEquivalentRequestURI(t *testing.T, got, want string) {
	t.Helper()
	gotURL, err := url.ParseRequestURI(got)
	if err != nil {
		t.Fatalf("parse request URI %q: %v", got, err)
	}
	wantURL, err := url.ParseRequestURI(want)
	if err != nil {
		t.Fatalf("parse expected request URI %q: %v", want, err)
	}
	if gotURL.Path != wantURL.Path || !reflect.DeepEqual(gotURL.Query(), wantURL.Query()) {
		t.Errorf("request URI = %q, want path/query equivalent to %q", got, want)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func assertFullPayloads(t *testing.T, got []SymptomDefinition, want []mockvcf.SymptomDefinition) {
	t.Helper()
	byID := make(map[string]SymptomDefinition, len(got))
	for _, definition := range got {
		byID[definition.ID] = definition
	}
	for _, expected := range want {
		actual, ok := byID[expected.ID]
		if !ok {
			t.Errorf("missing full payload for %q", expected.ID)
			continue
		}
		if actual.Name != expected.Name || actual.AdapterKindKey != expected.AdapterKindKey ||
			actual.ResourceKindKey != expected.ResourceKindKey ||
			!reflect.DeepEqual(actual.WaitCycles, expected.WaitCycles) ||
			!reflect.DeepEqual(actual.CancelCycles, expected.CancelCycles) ||
			!reflect.DeepEqual(actual.RealtimeMonitoringEnabled, expected.RealtimeMonitoringEnabled) ||
			!reflect.DeepEqual(actual.State, expected.State) {
			t.Errorf("payload for %q was not retained: %#v", expected.ID, actual)
		}
	}
}

func fixtureDefinitions() []mockvcf.SymptomDefinition {
	state := json.RawMessage(`{"severity":"WARNING","condition":{"type":"CONDITION_HT","key":"cpu|demandmhz","operator":"GT_EQ","value":"95","valueType":"NUMERIC","instanced":false,"thresholdType":"STATIC"}}`)
	waitCycles, cancelCycles, realtime := 2, 3, false
	return []mockvcf.SymptomDefinition{
		{ID: "id-3", Name: "Zulu", AdapterKindKey: "VMWARE", ResourceKindKey: "VirtualMachine", WaitCycles: &waitCycles, CancelCycles: &cancelCycles, RealtimeMonitoringEnabled: &realtime, State: state},
		{ID: "id-4", Name: "Alpha", AdapterKindKey: "VMWARE", ResourceKindKey: "VirtualMachine", State: state},
		{ID: "id-2", Name: "Beta", AdapterKindKey: "VMWARE", ResourceKindKey: "VirtualMachine", State: state},
		{ID: "id-1", Name: "Alpha", AdapterKindKey: "VMWARE", ResourceKindKey: "VirtualMachine", State: state},
		{ID: "id-5", Name: "Gamma", AdapterKindKey: "GENERIC", ResourceKindKey: "Other", State: state},
	}
}
