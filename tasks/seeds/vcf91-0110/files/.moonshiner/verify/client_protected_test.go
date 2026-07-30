package vcenter_test

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"example.com/vcfinventory/internal/mockvcenter"
	"example.com/vcfinventory/vcenter"
)

func TestProtectedContractProvenance(t *testing.T) {
	t.Parallel()

	var sources struct {
		Repository  string   `json:"repository"`
		License     string   `json:"license"`
		CommitSHA   string   `json:"commit_sha"`
		BlobSHA     string   `json:"spec_blob_sha"`
		SpecPath    string   `json:"spec_path"`
		APIVersion  string   `json:"api_version"`
		OperationID []string `json:"operation_ids"`
		Operations  []struct {
			OperationID string `json:"operation_id"`
			CommitSHA   string `json:"source_commit_sha"`
			SpecPath    string `json:"source_spec_path"`
		} `json:"operations"`
	}
	readJSON(t, "../docs/official_sources.json", &sources)

	const (
		commit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
		blob   = "8028b0824c4ff3503d05f44814f967938a795c40"
		path   = "specifications/vsphere/openapi/automation/vcenter.yaml"
	)
	wantOperations := []string{
		"Cis.Session_create",
		"Vcenter.Datacenter_list",
		"Vcenter.VM_list",
	}
	if sources.Repository != "https://github.com/vmware/vcf-api-specs" {
		t.Errorf("repository = %q", sources.Repository)
	}
	if sources.License != "Apache-2.0" {
		t.Errorf("license = %q", sources.License)
	}
	if sources.CommitSHA != commit || sources.BlobSHA != blob ||
		sources.SpecPath != path || sources.APIVersion != "9.1.0.0" {
		t.Errorf("unexpected pinned source: %#v", sources)
	}
	if !reflect.DeepEqual(sources.OperationID, wantOperations) {
		t.Errorf("operation IDs = %v, want %v", sources.OperationID, wantOperations)
	}
	for index, operation := range sources.Operations {
		if operation.OperationID != wantOperations[index] ||
			operation.CommitSHA != commit || operation.SpecPath != path {
			t.Errorf("source operation %d = %#v", index, operation)
		}
	}

	type parameter struct {
		Name     string `json:"name"`
		In       string `json:"in"`
		Required bool   `json:"required"`
		Style    string `json:"style"`
		Explode  bool   `json:"explode"`
	}
	type operation struct {
		OperationID string      `json:"operationId"`
		Method      string      `json:"method"`
		SpecPath    string      `json:"spec_path"`
		WirePath    string      `json:"wire_path"`
		Parameters  []parameter `json:"parameters"`
	}
	var contract struct {
		Derived struct {
			CommitSHA string `json:"commit_sha"`
			BlobSHA   string `json:"spec_blob_sha"`
			SpecPath  string `json:"spec_path"`
		} `json:"derived_from"`
		Server struct {
			BasePath string `json:"base_path"`
		} `json:"server"`
		Operations []operation `json:"operations"`
	}
	readJSON(t, "../docs/contract.json", &contract)
	if contract.Derived.CommitSHA != commit ||
		contract.Derived.BlobSHA != blob ||
		contract.Derived.SpecPath != path ||
		contract.Server.BasePath != "/api" {
		t.Errorf("contract provenance changed: %#v", contract.Derived)
	}
	wantContract := []operation{
		{
			OperationID: "Cis.Session_create",
			Method:      http.MethodPost,
			SpecPath:    "/session",
			WirePath:    "/api/session",
		},
		{
			OperationID: "Vcenter.Datacenter_list",
			Method:      http.MethodGet,
			SpecPath:    "/vcenter/datacenter",
			WirePath:    "/api/vcenter/datacenter",
			Parameters: []parameter{
				{"datacenters", "query", false, "form", true},
				{"names", "query", false, "form", true},
				{"folders", "query", false, "form", true},
			},
		},
		{
			OperationID: "Vcenter.VM_list",
			Method:      http.MethodGet,
			SpecPath:    "/vcenter/vm",
			WirePath:    "/api/vcenter/vm",
			Parameters: []parameter{
				{"vms", "query", false, "form", true},
				{"names", "query", false, "form", true},
				{"folders", "query", false, "form", true},
				{"datacenters", "query", false, "form", true},
				{"hosts", "query", false, "form", true},
				{"clusters", "query", false, "form", true},
				{"resource_pools", "query", false, "form", true},
				{"power_states", "query", false, "form", true},
			},
		},
	}
	if !reflect.DeepEqual(contract.Operations, wantContract) {
		t.Errorf("contract operations = %#v, want %#v", contract.Operations, wantContract)
	}
	if got := mockvcenter.OperationIDs(); !reflect.DeepEqual(got, wantOperations) {
		t.Errorf("mock operation IDs = %v, want %v", got, wantOperations)
	}
}

func TestProtectedCollectRefreshKeepsProgressAndExactWire(t *testing.T) {
	t.Parallel()

	scenario := standardScenario()
	scenario.ExpireFirstToken = true
	scenario.ExpireAfter = 1
	server := newMock(t, scenario)
	client := newClient(t, server, scenario)

	snapshot, err := client.CollectInventory(context.Background())
	if err != nil {
		t.Fatalf("CollectInventory: %v", err)
	}
	wantDatacenters := []vcenter.DatacenterSummary{
		{Datacenter: "datacenter-2", Name: "Alpha"},
		{Datacenter: "datacenter-1", Name: "Zulu"},
	}
	wantVMs := []vcenter.VMSummary{
		{VM: "vm-1", Name: "App", PowerState: vcenter.PowerStatePoweredOn},
		{VM: "vm-3", Name: "App", PowerState: vcenter.PowerStateSuspended},
		{
			VM: "vm-2", Name: "Database",
			PowerState: vcenter.PowerStatePoweredOff,
			CPUCount:   int64Pointer(8), MemorySizeMiB: int64Pointer(32768),
		},
	}
	if !reflect.DeepEqual(snapshot.Datacenters, wantDatacenters) {
		t.Errorf("datacenters = %#v, want %#v", snapshot.Datacenters, wantDatacenters)
	}
	if !reflect.DeepEqual(snapshot.VMs, wantVMs) {
		t.Errorf("VMs = %#v, want %#v", snapshot.VMs, wantVMs)
	}

	requests := server.Requests()
	if len(requests) != 5 {
		t.Fatalf("request count = %d, want 5", len(requests))
	}
	assertSessionRequest(t, requests[0], scenario, "/api/session")
	assertListRequest(t, requests[1], "/api/vcenter/datacenter", scenario.Tokens[0])
	assertListRequest(t, requests[2], "/api/vcenter/vm", scenario.Tokens[0])
	assertSessionRequest(t, requests[3], scenario, "/api/session")
	assertListRequest(t, requests[4], "/api/vcenter/vm", scenario.Tokens[1])

	if requests[1].RawQuery != "" || requests[2].RawQuery != "" ||
		requests[4].RawQuery != "" {
		t.Errorf(
			"unset optional fields were not omitted: %q, %q, %q",
			requests[1].RawQuery,
			requests[2].RawQuery,
			requests[4].RawQuery,
		)
	}
	datacenterCalls := 0
	for _, request := range requests {
		if request.Path == "/api/vcenter/datacenter" {
			datacenterCalls++
		}
	}
	if datacenterCalls != 1 {
		t.Errorf("datacenter calls = %d, want completed work retained", datacenterCalls)
	}
}

func TestProtectedExplodedFilterWireTable(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name      string
		invoke    func(*vcenter.Client) error
		wantPath  string
		wantQuery string
	}{
		{
			name: "datacenter order encoding and empty omission",
			invoke: func(client *vcenter.Client) error {
				_, err := client.ListDatacenters(context.Background(), vcenter.DatacenterFilter{
					Datacenters: []string{"datacenter/9", "datacenter +2"},
					Names:       []string{},
					Folders:     []string{"group-ü"},
				})
				return err
			},
			wantPath:  "/api/vcenter/datacenter",
			wantQuery: "datacenters=datacenter%2F9&datacenters=datacenter%20%2B2&folders=group-%C3%BC",
		},
		{
			name: "VM declaration order and unset omission",
			invoke: func(client *vcenter.Client) error {
				_, err := client.ListVMs(context.Background(), vcenter.VMFilter{
					VMs:           []string{"vm-7"},
					Names:         nil,
					Folders:       []string{"group/vm"},
					Datacenters:   []string{},
					Hosts:         []string{"host 2"},
					Clusters:      []string{},
					ResourcePools: []string{"resgroup-1"},
					PowerStates: []vcenter.PowerState{
						vcenter.PowerStatePoweredOn,
						vcenter.PowerStateSuspended,
					},
				})
				return err
			},
			wantPath: "/api/vcenter/vm",
			wantQuery: "vms=vm-7&folders=group%2Fvm&hosts=host%202" +
				"&resource_pools=resgroup-1&power_states=POWERED_ON" +
				"&power_states=SUSPENDED",
		},
	}

	for _, testCase := range testCases {
		testCase := testCase
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()
			scenario := standardScenario()
			server := newMock(t, scenario)
			client := newClient(t, server, scenario)

			if err := testCase.invoke(client); err != nil {
				t.Fatalf("list: %v", err)
			}
			requests := server.Requests()
			if len(requests) != 2 {
				t.Fatalf("request count = %d, want 2", len(requests))
			}
			assertListRequest(t, requests[1], testCase.wantPath, scenario.Tokens[0])
			if requests[1].RawQuery != testCase.wantQuery {
				t.Errorf("raw query = %q, want %q", requests[1].RawQuery, testCase.wantQuery)
			}
			wantURI := testCase.wantPath + "?" + testCase.wantQuery
			if requests[1].RequestURI != wantURI {
				t.Errorf("request URI = %q, want %q", requests[1].RequestURI, wantURI)
			}
		})
	}
}

func TestProtectedFilterValidationBeforeIO(t *testing.T) {
	t.Parallel()

	testCases := []struct {
		name   string
		invoke func(*vcenter.Client) error
	}{
		{
			name: "duplicate datacenters",
			invoke: func(client *vcenter.Client) error {
				_, err := client.ListDatacenters(context.Background(), vcenter.DatacenterFilter{
					Datacenters: []string{"datacenter-1", "datacenter-1"},
				})
				return err
			},
		},
		{
			name: "blank VM name",
			invoke: func(client *vcenter.Client) error {
				_, err := client.ListVMs(context.Background(), vcenter.VMFilter{
					Names: []string{"app", " "},
				})
				return err
			},
		},
		{
			name: "unknown VM power state",
			invoke: func(client *vcenter.Client) error {
				_, err := client.ListVMs(context.Background(), vcenter.VMFilter{
					PowerStates: []vcenter.PowerState{"PAUSED"},
				})
				return err
			},
		},
	}

	for _, testCase := range testCases {
		testCase := testCase
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()
			scenario := standardScenario()
			server := newMock(t, scenario)
			client := newClient(t, server, scenario)
			before := len(server.Requests())
			if err := testCase.invoke(client); err == nil {
				t.Fatal("expected validation error")
			}
			if after := len(server.Requests()); after != before {
				t.Errorf("request count changed from %d to %d", before, after)
			}
		})
	}
}

func TestProtectedReplacementFailureReturnsPartialWorkAndStops(t *testing.T) {
	t.Parallel()

	scenario := standardScenario()
	scenario.ExpireFirstToken = true
	scenario.ExpireAfter = 1
	scenario.RejectReplacement = true
	scenario.ErrorSecret = "payload-secret-must-not-appear"
	server := newMock(t, scenario)
	client := newClient(t, server, scenario)

	snapshot, err := client.CollectInventory(context.Background())
	if err == nil {
		t.Fatal("expected replacement-token failure")
	}
	if len(snapshot.Datacenters) != len(scenario.Datacenters) ||
		snapshot.VMs != nil {
		t.Errorf("partial snapshot = %#v", snapshot)
	}
	var apiError *vcenter.APIError
	if !errors.As(err, &apiError) {
		t.Fatalf("error type = %T, want *APIError", err)
	}
	if apiError.OperationID != mockvcenter.VMListOperationID ||
		apiError.StatusCode != http.StatusUnauthorized {
		t.Errorf("API error = %#v", apiError)
	}
	if strings.Contains(err.Error(), scenario.ErrorSecret) ||
		strings.Contains(err.Error(), scenario.Password) ||
		strings.Contains(err.Error(), scenario.Tokens[0]) ||
		strings.Contains(err.Error(), scenario.Tokens[1]) {
		t.Errorf("error leaked a secret: %q", err)
	}
	requests := server.Requests()
	if len(requests) != 5 {
		t.Fatalf("request count = %d, want bounded five requests", len(requests))
	}
	if requests[1].Path != "/api/vcenter/datacenter" ||
		requests[2].Path != "/api/vcenter/vm" ||
		requests[3].Path != "/api/session" ||
		requests[4].Path != "/api/vcenter/vm" {
		t.Errorf("unexpected retry sequence: %#v", requests)
	}
}

func TestProtectedConcurrentExpiredTokenCoalescesRefresh(t *testing.T) {
	t.Parallel()

	scenario := standardScenario()
	scenario.ExpireFirstToken = true
	scenario.ExpireAfter = 0
	server := newMock(t, scenario)
	client := newClient(t, server, scenario)

	const callers = 12
	start := make(chan struct{})
	errs := make(chan error, callers)
	var wait sync.WaitGroup
	wait.Add(callers)
	for index := 0; index < callers; index++ {
		go func() {
			defer wait.Done()
			<-start
			_, err := client.ListDatacenters(
				context.Background(),
				vcenter.DatacenterFilter{},
			)
			errs <- err
		}()
	}
	close(start)
	wait.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Errorf("concurrent list: %v", err)
		}
	}

	sessionCreates := 0
	for _, request := range server.Requests() {
		if request.Path == "/api/session" {
			sessionCreates++
		}
	}
	if sessionCreates != 2 {
		t.Errorf("session creates = %d, want initial plus one replacement", sessionCreates)
	}
}

func TestProtectedMockRejectsOperationsOutsideContract(t *testing.T) {
	t.Parallel()

	scenario := standardScenario()
	server := newMock(t, scenario)
	request, err := http.NewRequest(
		http.MethodGet,
		server.URL()+"/api/vcenter/host",
		nil,
	)
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("vmware-api-session-id", scenario.Tokens[0])
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

func standardScenario() mockvcenter.Scenario {
	cpu := int64(8)
	memory := int64(32768)
	return mockvcenter.Scenario{
		Username: "administrator@vsphere.local",
		Password: "pässword:inventory",
		Tokens: []string{
			"session-initial-7f6b",
			"session-replacement-9c2a",
		},
		Datacenters: []mockvcenter.Datacenter{
			{Datacenter: "datacenter-1", Name: "Zulu"},
			{Datacenter: "datacenter-2", Name: "Alpha"},
		},
		VMs: []mockvcenter.VM{
			{VM: "vm-3", Name: "App", PowerState: "SUSPENDED"},
			{
				VM: "vm-2", Name: "Database", PowerState: "POWERED_OFF",
				CPUCount: &cpu, MemorySizeMiB: &memory,
			},
			{VM: "vm-1", Name: "App", PowerState: "POWERED_ON"},
		},
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

func newClient(
	t *testing.T,
	server *mockvcenter.Server,
	scenario mockvcenter.Scenario,
) *vcenter.Client {
	t.Helper()
	httpClient := server.Client()
	httpClient.Timeout = 3 * time.Second
	client, err := vcenter.NewClient(
		context.Background(),
		server.URL(),
		scenario.Username,
		scenario.Password,
		httpClient,
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func assertSessionRequest(
	t *testing.T,
	request mockvcenter.Request,
	scenario mockvcenter.Scenario,
	wantURI string,
) {
	t.Helper()
	if request.Method != http.MethodPost || request.RequestURI != wantURI {
		t.Errorf("session request = %s %s", request.Method, request.RequestURI)
	}
	raw := []byte(scenario.Username + ":" + scenario.Password)
	wantAuthorization := "Basic " + base64.StdEncoding.EncodeToString(raw)
	if got := request.Header.Get("Authorization"); got != wantAuthorization {
		t.Errorf("Authorization = %q, want exact Basic value", got)
	}
	if got := request.Header.Get("Accept"); got != "application/json" {
		t.Errorf("session Accept = %q", got)
	}
	if got := request.Header.Get("vmware-api-session-id"); got != "" {
		t.Errorf("session sent API session header %q", got)
	}
	if got := request.Header.Get("Content-Type"); got != "" {
		t.Errorf("bodyless session Content-Type = %q", got)
	}
	if len(request.Body) != 0 {
		t.Errorf("session body = %q", request.Body)
	}
}

func assertListRequest(
	t *testing.T,
	request mockvcenter.Request,
	wantPath string,
	wantToken string,
) {
	t.Helper()
	if request.Method != http.MethodGet || request.Path != wantPath {
		t.Errorf("list request = %s %s, want GET %s", request.Method, request.Path, wantPath)
	}
	if got := request.Header.Get("vmware-api-session-id"); got != wantToken {
		t.Errorf("session header = %q, want %q", got, wantToken)
	}
	if got := request.Header.Get("Accept"); got != "application/json" {
		t.Errorf("list Accept = %q", got)
	}
	if got := request.Header.Get("Authorization"); got != "" {
		t.Errorf("list Authorization = %q", got)
	}
	if got := request.Header.Get("Content-Type"); got != "" {
		t.Errorf("bodyless list Content-Type = %q", got)
	}
	if len(request.Body) != 0 {
		t.Errorf("list body = %q", request.Body)
	}
}

func int64Pointer(value int64) *int64 {
	return &value
}

func readJSON(t *testing.T, path string, destination any) {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	decoder := json.NewDecoder(bytes.NewReader(content))
	if err := decoder.Decode(destination); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}
