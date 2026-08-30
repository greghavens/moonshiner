package verification_test

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"example.com/vcf-vcenter-session-rotation/internal/contractmock"
	"example.com/vcf-vcenter-session-rotation/vcenter"
)

const (
	wantCommit     = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
	wantSpec       = "specifications/vsphere/openapi/automation/vcenter.yaml"
	wantAPIVersion = "9.0.0.0"
	wantBasePath   = "/api"
)

func repositoryRoot(t *testing.T) string {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("locate protected verifier")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(filename), "..", ".."))
}

func contractPath(t *testing.T) string {
	t.Helper()
	return filepath.Join(repositoryRoot(t), "docs", "contract.json")
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
			RepositoryTag       string `json:"repositoryTag"`
			RepositoryCommitSHA string `json:"repositoryCommitSha"`
			SpecPath            string `json:"specPath"`
			License             string `json:"license"`
			OpenAPI             string `json:"openapi"`
			APIVersion          string `json:"apiVersion"`
			ServerBasePath      string `json:"serverBasePath"`
		} `json:"source"`
		SecuritySchemes struct {
			BasicAuth struct {
				Type   string `json:"type"`
				Scheme string `json:"scheme"`
			} `json:"basic_auth"`
			APIKeyAuth struct {
				Type string `json:"type"`
				Name string `json:"name"`
				In   string `json:"in"`
			} `json:"api_key_auth"`
		} `json:"securitySchemes"`
		Operations []struct {
			OperationID string   `json:"operationId"`
			Method      string   `json:"method"`
			Path        string   `json:"path"`
			Security    []string `json:"security"`
			RequestBody bool     `json:"requestBody"`
		} `json:"operations"`
		FocusedFilterProfile struct {
			DeclaredQueryOrder []string `json:"declaredQueryOrder"`
			UnsetBehavior      string   `json:"unsetBehavior"`
			BodylessOperations []string `json:"bodylessOperations"`
		} `json:"focusedFilterProfile"`
	}
	var sources struct {
		Repository          string   `json:"repository"`
		RepositoryTag       string   `json:"repositoryTag"`
		RepositoryCommitSHA string   `json:"repositoryCommitSha"`
		SpecPath            string   `json:"specPath"`
		SpecURL             string   `json:"specUrl"`
		SpecVersion         string   `json:"specVersion"`
		License             string   `json:"license"`
		OperationIDs        []string `json:"operationIds"`
		Operations          []struct {
			OperationID         string `json:"operationId"`
			Method              string `json:"method"`
			Path                string `json:"path"`
			SpecJSONPointer     string `json:"specJsonPointer"`
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
		contract.Source.RepositoryTag != "9.0.0.0" ||
		contract.Source.RepositoryCommitSHA != wantCommit ||
		contract.Source.SpecPath != wantSpec ||
		contract.Source.License != "Apache-2.0" ||
		contract.Source.OpenAPI != "3.0.3" ||
		contract.Source.APIVersion != wantAPIVersion ||
		contract.Source.ServerBasePath != wantBasePath {
		t.Fatalf("contract source changed: %+v", contract.Source)
	}
	if contract.SecuritySchemes.BasicAuth.Type != "http" || contract.SecuritySchemes.BasicAuth.Scheme != "basic" ||
		contract.SecuritySchemes.APIKeyAuth.Type != "apiKey" ||
		contract.SecuritySchemes.APIKeyAuth.Name != contractmock.SessionHeader ||
		contract.SecuritySchemes.APIKeyAuth.In != "header" {
		t.Fatalf("contract security schemes changed: %+v", contract.SecuritySchemes)
	}
	if sources.Repository != "vmware/vcf-api-specs" || sources.RepositoryTag != "9.0.0.0" ||
		sources.RepositoryCommitSHA != wantCommit || sources.SpecPath != wantSpec ||
		sources.SpecVersion != wantAPIVersion || sources.License != "Apache-2.0" ||
		sources.Derivation.DocumentationPageUsed {
		t.Fatalf("official source changed: %+v", sources)
	}
	if !strings.Contains(sources.SpecURL, wantCommit) || !strings.HasSuffix(sources.SpecURL, wantSpec) {
		t.Fatalf("official spec URL is not pinned to the tagged revision: %q", sources.SpecURL)
	}

	want := []struct {
		id, method, path, security, pointer string
	}{
		{"Cis.Session_create", http.MethodPost, "/session", "basic_auth", "/paths/~1session/post/operationId"},
		{"Cis.Session_delete", http.MethodDelete, "/session", "api_key_auth", "/paths/~1session/delete/operationId"},
		{"Vcenter.VM_list", http.MethodGet, "/vcenter/vm", "api_key_auth", "/paths/~1vcenter~1vm/get/operationId"},
	}
	if len(contract.Operations) != len(want) || len(sources.Operations) != len(want) || len(sources.OperationIDs) != len(want) {
		t.Fatalf("operation counts contract=%d sources=%d ids=%d, want %d each",
			len(contract.Operations), len(sources.Operations), len(sources.OperationIDs), len(want))
	}
	for index, expected := range want {
		t.Run(expected.id, func(t *testing.T) {
			operation := contract.Operations[index]
			source := sources.Operations[index]
			if operation.OperationID != expected.id || operation.Method != expected.method || operation.Path != expected.path {
				t.Fatalf("contract operation = %+v, want %+v", operation, expected)
			}
			if len(operation.Security) != 1 || operation.Security[0] != expected.security || operation.RequestBody {
				t.Fatalf("contract operation security/body = %+v", operation)
			}
			if source.OperationID != expected.id || source.Method != expected.method || source.Path != expected.path ||
				source.SpecJSONPointer != expected.pointer || source.RepositoryCommitSHA != wantCommit ||
				source.SpecPath != wantSpec || sources.OperationIDs[index] != expected.id {
				t.Fatalf("official operation = %+v, want %+v", source, expected)
			}
		})
	}
	if !reflect.DeepEqual(contract.FocusedFilterProfile.DeclaredQueryOrder, contractmock.FilterQueryMembers()) ||
		contract.FocusedFilterProfile.UnsetBehavior != "omit" ||
		len(contract.FocusedFilterProfile.BodylessOperations) != len(want) {
		t.Fatalf("focused filter profile changed: %+v", contract.FocusedFilterProfile)
	}
}

func TestRotateDrainsInFlightRequestBeforeDeletingOldSession(t *testing.T) {
	server := contractmock.Start(t, contractPath(t), contractmock.RotateWhileBusy)
	client := newClient(t, server)
	ctx := context.Background()
	if err := client.Login(ctx); err != nil {
		t.Fatalf("Login: %v", err)
	}

	arrived, release := server.GateVMList()
	heldVMs := make(chan []vcenter.VM, 1)
	heldErr := make(chan error, 1)
	go func() {
		vms, err := client.ListVMs(ctx, vcenter.Filter{PowerStates: []string{"POWERED_ON"}})
		heldVMs <- vms
		heldErr <- err
	}()
	waitForClose(t, arrived, "the held Vcenter.VM_list request never reached the mock")

	rotated := make(chan error, 1)
	go func() { rotated <- client.Rotate(ctx, credential(server.NextCredential())) }()

	// The replacement session must exist before the old one is retired, so the
	// third request arrives while the second is still open.
	waitForRequestCount(t, server, 3)
	for _, request := range server.Requests() {
		if request.OperationID == "Cis.Session_delete" {
			t.Fatalf("the old session was deleted while a request was still using it: %v", request)
		}
	}
	select {
	case err := <-rotated:
		t.Fatalf("Rotate returned (%v) before the in-flight request drained", err)
	case <-time.After(200 * time.Millisecond):
	}

	release()
	if err := <-heldErr; err != nil {
		t.Fatalf("the in-flight request was stranded on the rotated-away session: %v", err)
	}
	if got, want := <-heldVMs, expectedVMs(server, "vm-101", "vm-103"); !reflect.DeepEqual(got, want) {
		t.Fatalf("held ListVMs = %s, want %s", describe(got), describe(want))
	}
	if err := <-rotated; err != nil {
		t.Fatalf("Rotate: %v", err)
	}

	after, err := client.ListVMs(ctx, vcenter.Filter{Names: []string{"db-tier-01"}})
	if err != nil {
		t.Fatalf("ListVMs after rotation: %v", err)
	}
	if want := expectedVMs(server, "vm-103"); !reflect.DeepEqual(after, want) {
		t.Fatalf("ListVMs after rotation = %s, want %s", describe(after), describe(want))
	}
	if err := client.Logout(ctx); err != nil {
		t.Fatalf("Logout: %v", err)
	}

	tokens := server.IssuedTokens()
	if len(tokens) != 2 {
		t.Fatalf("issued tokens = %v, want exactly two", tokens)
	}
	requests := server.Requests()
	wantOperations := []string{"Cis.Session_create", "Vcenter.VM_list", "Cis.Session_create", "Cis.Session_delete", "Vcenter.VM_list", "Cis.Session_delete"}
	wantTargets := []string{
		"/api/session",
		"/api/vcenter/vm?power_states=POWERED_ON",
		"/api/session",
		"/api/session",
		"/api/vcenter/vm?names=db-tier-01",
		"/api/session",
	}
	wantStatuses := []int{201, 200, 201, 204, 200, 204}
	wantTokens := []string{"", tokens[0], "", tokens[0], tokens[1], tokens[1]}
	wantCredentials := []contractmock.Credential{server.FirstCredential(), {}, server.NextCredential(), {}, {}, {}}
	if len(requests) != len(wantOperations) {
		t.Fatalf("request log = %v, want exactly %d requests", requests, len(wantOperations))
	}
	for index, request := range requests {
		if request.OperationID != wantOperations[index] || request.RawTarget != wantTargets[index] || request.ResponseStatus != wantStatuses[index] {
			t.Errorf("request %d = %v, want %s %s => %d", index, request, wantOperations[index], wantTargets[index], wantStatuses[index])
		}
		if request.Stranded {
			t.Errorf("request %d was answered on a session that had already been deleted: %v", index, request)
		}
		assertBodylessWireShape(t, index, request)
		assertSingleHeader(t, request.Header, "Accept", "application/json")
		if request.OperationID == "Cis.Session_create" {
			assertHeaderAbsent(t, request.Header, contractmock.SessionHeader)
			assertSingleHeader(t, request.Header, "Authorization", basicAuth(wantCredentials[index]))
			continue
		}
		assertHeaderAbsent(t, request.Header, "Authorization")
		assertSingleHeader(t, request.Header, contractmock.SessionHeader, wantTokens[index])
	}

	if requests[1].CompletionOrder < requests[2].CompletionOrder {
		t.Errorf("the replacement session was created only after the in-flight request finished: %v then %v", requests[2], requests[1])
	}
	if requests[3].CompletionOrder < requests[1].CompletionOrder {
		t.Errorf("Cis.Session_delete completed before the request still using that session: %v then %v", requests[3], requests[1])
	}
}

func TestConcurrentListVMsSurviveRotation(t *testing.T) {
	server := contractmock.Start(t, contractPath(t), contractmock.RotateWhileBusy)
	client := newClient(t, server)
	ctx := context.Background()
	if err := client.Login(ctx); err != nil {
		t.Fatalf("Login: %v", err)
	}

	const workers, callsPerWorker = 6, 8
	start := make(chan struct{})
	failures := make(chan error, workers*callsPerWorker)
	rotated := make(chan error, 1)
	var group sync.WaitGroup
	for worker := 0; worker < workers; worker++ {
		group.Add(1)
		go func() {
			defer group.Done()
			<-start
			for call := 0; call < callsPerWorker; call++ {
				if _, err := client.ListVMs(ctx, vcenter.Filter{PowerStates: []string{"POWERED_ON"}}); err != nil {
					failures <- err
					return
				}
			}
		}()
	}
	group.Add(1)
	go func() {
		defer group.Done()
		<-start
		rotated <- client.Rotate(ctx, credential(server.NextCredential()))
	}()
	close(start)
	group.Wait()
	close(failures)

	for err := range failures {
		t.Errorf("a concurrent ListVMs was stranded by the rotation: %v", err)
	}
	if err := <-rotated; err != nil {
		t.Fatalf("Rotate: %v", err)
	}

	tokens := server.IssuedTokens()
	if len(tokens) != 2 {
		t.Fatalf("issued tokens = %v, want exactly two", tokens)
	}
	deletions, retiredAt := 0, 0
	for _, request := range server.Requests() {
		if request.Stranded {
			t.Errorf("request answered on an already-deleted session: %v", request)
		}
		switch request.OperationID {
		case "Vcenter.VM_list":
			if request.ResponseStatus != http.StatusOK {
				t.Errorf("list request failed during rotation: %v", request)
			}
			if request.SessionToken != tokens[0] && request.SessionToken != tokens[1] {
				t.Errorf("list request used an unknown session token: %v", request)
			}
		case "Cis.Session_delete":
			deletions++
			retiredAt = request.CompletionOrder
			if request.SessionToken != tokens[0] {
				t.Errorf("the wrong session was deleted: %v", request)
			}
		}
	}
	if deletions != 1 {
		t.Fatalf("Cis.Session_delete count = %d, want exactly one", deletions)
	}
	for _, request := range server.Requests() {
		if request.OperationID == "Vcenter.VM_list" && request.SessionToken == tokens[0] && request.CompletionOrder > retiredAt {
			t.Errorf("a request kept using the retired session after it was deleted: %v", request)
		}
	}
}

func TestLogoutDrainsInFlightRequestBeforeDeletingSession(t *testing.T) {
	server := contractmock.Start(t, contractPath(t), contractmock.RotateWhileBusy)
	client := newClient(t, server)
	ctx := context.Background()
	if err := client.Login(ctx); err != nil {
		t.Fatalf("Login: %v", err)
	}

	arrived, release := server.GateVMList()
	held := make(chan error, 1)
	go func() {
		_, err := client.ListVMs(ctx, vcenter.Filter{})
		held <- err
	}()
	waitForClose(t, arrived, "the held Vcenter.VM_list request never reached the mock")

	loggedOut := make(chan error, 1)
	go func() { loggedOut <- client.Logout(ctx) }()
	select {
	case err := <-loggedOut:
		t.Fatalf("Logout returned (%v) before the in-flight request drained", err)
	case <-time.After(200 * time.Millisecond):
	}
	for _, request := range server.Requests() {
		if request.OperationID == "Cis.Session_delete" {
			t.Fatalf("the session was deleted while a request was still using it: %v", request)
		}
	}

	release()
	if err := <-held; err != nil {
		t.Fatalf("the in-flight request was stranded by Logout: %v", err)
	}
	if err := <-loggedOut; err != nil {
		t.Fatalf("Logout: %v", err)
	}
	requests := server.Requests()
	if len(requests) != 3 || requests[0].OperationID != "Cis.Session_create" ||
		requests[1].OperationID != "Vcenter.VM_list" || requests[2].OperationID != "Cis.Session_delete" {
		t.Fatalf("request log = %v, want create, list, delete", requests)
	}
	if requests[1].Stranded || requests[2].CompletionOrder < requests[1].CompletionOrder {
		t.Fatalf("session deletion did not follow the held request's completion: %v", requests)
	}
	if requests[1].SessionToken == "" || requests[2].SessionToken != requests[1].SessionToken {
		t.Fatalf("Logout deleted a token other than the one used by the held request: %v", requests)
	}
}

func TestLoginAfterRotatedLogoutUsesReplacementCredential(t *testing.T) {
	server := contractmock.Start(t, contractPath(t), contractmock.RotateWhileBusy)
	client := newClient(t, server)
	ctx := context.Background()
	if err := client.Login(ctx); err != nil {
		t.Fatalf("initial Login: %v", err)
	}
	if err := client.Rotate(ctx, credential(server.NextCredential())); err != nil {
		t.Fatalf("Rotate: %v", err)
	}
	if err := client.Logout(ctx); err != nil {
		t.Fatalf("first Logout: %v", err)
	}
	if err := client.Login(ctx); err != nil {
		t.Fatalf("Login after rotated Logout: %v", err)
	}
	if err := client.Logout(ctx); err != nil {
		t.Fatalf("second Logout: %v", err)
	}

	requests := server.Requests()
	wantOperations := []string{
		"Cis.Session_create",
		"Cis.Session_create",
		"Cis.Session_delete",
		"Cis.Session_delete",
		"Cis.Session_create",
		"Cis.Session_delete",
	}
	if len(requests) != len(wantOperations) {
		t.Fatalf("request log = %v, want %v", requests, wantOperations)
	}
	for index, want := range wantOperations {
		if requests[index].OperationID != want {
			t.Fatalf("request %d = %v, want %s", index, requests[index], want)
		}
	}
	assertSingleHeader(t, requests[4].Header, "Authorization", basicAuth(server.NextCredential()))
	assertHeaderAbsent(t, requests[4].Header, contractmock.SessionHeader)
}

func TestVMListFilterWireShapeTable(t *testing.T) {
	tests := []struct {
		name       string
		filter     vcenter.Filter
		wantTarget string
		wantQuery  url.Values
		wantOrder  []string
		wantVMs    []string
	}{
		{
			name:       "an unset filter omits every optional member",
			filter:     vcenter.Filter{},
			wantTarget: "/api/vcenter/vm",
			wantQuery:  url.Values{},
			wantOrder:  nil,
			wantVMs:    []string{"vm-101", "vm-102", "vm-103", "vm-104"},
		},
		{
			name:       "empty members are unset, not empty values",
			filter:     vcenter.Filter{VMs: []string{}, Names: []string{}, PowerStates: []string{}},
			wantTarget: "/api/vcenter/vm",
			wantQuery:  url.Values{},
			wantOrder:  nil,
			wantVMs:    []string{"vm-101", "vm-102", "vm-103", "vm-104"},
		},
		{
			name:       "a set member explodes into repeated keys",
			filter:     vcenter.Filter{Names: []string{"app-tier-01", "db-tier-01"}},
			wantTarget: "/api/vcenter/vm?names=app-tier-01&names=db-tier-01",
			wantQuery:  url.Values{"names": {"app-tier-01", "db-tier-01"}},
			wantOrder:  []string{"names"},
			wantVMs:    []string{"vm-101", "vm-103"},
		},
		{
			name:       "members follow the declared order, not the caller order",
			filter:     vcenter.Filter{PowerStates: []string{"POWERED_ON"}, Names: []string{"app-tier-01", "db-tier-01"}},
			wantTarget: "/api/vcenter/vm?names=app-tier-01&names=db-tier-01&power_states=POWERED_ON",
			wantQuery:  url.Values{"names": {"app-tier-01", "db-tier-01"}, "power_states": {"POWERED_ON"}},
			wantOrder:  []string{"names", "power_states"},
			wantVMs:    []string{"vm-101", "vm-103"},
		},
		{
			name:      "values are query escaped",
			filter:    vcenter.Filter{Names: []string{"web tier/01"}},
			wantQuery: url.Values{"names": {"web tier/01"}},
			wantOrder: []string{"names"},
			wantVMs:   []string{"vm-104"},
		},
		{
			name:       "several set members keep the declared order",
			filter:     vcenter.Filter{VMs: []string{"vm-101", "vm-103"}, Clusters: []string{"domain-c7"}, PowerStates: []string{"POWERED_ON"}},
			wantTarget: "/api/vcenter/vm?vms=vm-101&vms=vm-103&clusters=domain-c7&power_states=POWERED_ON",
			wantQuery:  url.Values{"vms": {"vm-101", "vm-103"}, "clusters": {"domain-c7"}, "power_states": {"POWERED_ON"}},
			wantOrder:  []string{"vms", "clusters", "power_states"},
			wantVMs:    []string{"vm-101", "vm-103"},
		},
		{
			name: "every declared member is serialized",
			filter: vcenter.Filter{
				VMs:           []string{"vm-101", "vm-103"},
				Names:         []string{"app-tier-01", "db-tier-01"},
				Folders:       []string{"group-v3"},
				Datacenters:   []string{"datacenter-2"},
				Hosts:         []string{"host-17"},
				Clusters:      []string{"domain-c7"},
				ResourcePools: []string{"resgroup-9"},
				PowerStates:   []string{"POWERED_ON"},
			},
			wantTarget: "/api/vcenter/vm?vms=vm-101&vms=vm-103&names=app-tier-01&names=db-tier-01&folders=group-v3&datacenters=datacenter-2&hosts=host-17&clusters=domain-c7&resource_pools=resgroup-9&power_states=POWERED_ON",
			wantQuery: url.Values{
				"vms":            {"vm-101", "vm-103"},
				"names":          {"app-tier-01", "db-tier-01"},
				"folders":        {"group-v3"},
				"datacenters":    {"datacenter-2"},
				"hosts":          {"host-17"},
				"clusters":       {"domain-c7"},
				"resource_pools": {"resgroup-9"},
				"power_states":   {"POWERED_ON"},
			},
			wantOrder: contractmock.FilterQueryMembers(),
			wantVMs:   []string{"vm-101", "vm-103"},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.Start(t, contractPath(t), contractmock.RotateWhileBusy)
			client := newClient(t, server)
			ctx := context.Background()
			if err := client.Login(ctx); err != nil {
				t.Fatalf("Login: %v", err)
			}
			vms, err := client.ListVMs(ctx, test.filter)
			if err != nil {
				t.Fatalf("ListVMs: %v", err)
			}

			requests := server.Requests()
			if len(requests) != 2 || requests[1].OperationID != "Vcenter.VM_list" {
				t.Fatalf("request log = %v, want one login and one list", requests)
			}
			request := requests[1]
			assertBodylessWireShape(t, 1, request)
			if test.wantTarget != "" && request.RawTarget != test.wantTarget {
				t.Fatalf("raw target = %q, want %q", request.RawTarget, test.wantTarget)
			}
			target, err := url.ParseRequestURI(request.RawTarget)
			if err != nil {
				t.Fatalf("parse target %q: %v", request.RawTarget, err)
			}
			if target.Path != "/api/vcenter/vm" {
				t.Fatalf("path = %q, want /api/vcenter/vm", target.Path)
			}
			if len(test.wantOrder) == 0 && strings.Contains(request.RawTarget, "?") {
				t.Fatalf("raw target %q carries a query for a wholly unset filter", request.RawTarget)
			}
			if strings.ContainsAny(target.RawQuery, " \t") {
				t.Fatalf("raw query %q is not escaped", target.RawQuery)
			}
			if got := target.Query(); !reflect.DeepEqual(got, test.wantQuery) {
				t.Fatalf("query = %v, want %v", got, test.wantQuery)
			}
			if got := queryMemberOrder(t, target.RawQuery); !reflect.DeepEqual(got, test.wantOrder) {
				t.Fatalf("query member order = %v, want %v", got, test.wantOrder)
			}
			for _, member := range contractmock.FilterQueryMembers() {
				if _, present := test.wantQuery[member]; present {
					continue
				}
				if _, sent := target.Query()[member]; sent {
					t.Errorf("unset optional member %q was sent instead of omitted", member)
				}
			}
			if want := expectedVMs(server, test.wantVMs...); !reflect.DeepEqual(vms, want) {
				t.Fatalf("ListVMs = %s, want %s", describe(vms), describe(want))
			}
		})
	}
}

func TestFilterValidationTable(t *testing.T) {
	tests := []struct {
		name   string
		filter vcenter.Filter
	}{
		{name: "blank vms", filter: vcenter.Filter{VMs: []string{"vm-101", " "}}},
		{name: "repeated vms", filter: vcenter.Filter{VMs: []string{"vm-101", "vm-101"}}},
		{name: "blank names", filter: vcenter.Filter{Names: []string{"app-tier-01", " "}}},
		{name: "repeated names", filter: vcenter.Filter{Names: []string{"app-tier-01", "app-tier-01"}}},
		{name: "blank folders", filter: vcenter.Filter{Folders: []string{"group-v3", " "}}},
		{name: "repeated folders", filter: vcenter.Filter{Folders: []string{"group-v3", "group-v3"}}},
		{name: "blank datacenters", filter: vcenter.Filter{Datacenters: []string{"datacenter-2", " "}}},
		{name: "repeated datacenters", filter: vcenter.Filter{Datacenters: []string{"datacenter-2", "datacenter-2"}}},
		{name: "blank hosts", filter: vcenter.Filter{Hosts: []string{"host-17", " "}}},
		{name: "repeated hosts", filter: vcenter.Filter{Hosts: []string{"host-17", "host-17"}}},
		{name: "blank clusters", filter: vcenter.Filter{Clusters: []string{"domain-c7", " "}}},
		{name: "repeated clusters", filter: vcenter.Filter{Clusters: []string{"domain-c7", "domain-c7"}}},
		{name: "blank resource pools", filter: vcenter.Filter{ResourcePools: []string{"resgroup-9", " "}}},
		{name: "repeated resource pools", filter: vcenter.Filter{ResourcePools: []string{"resgroup-9", "resgroup-9"}}},
		{name: "blank power states", filter: vcenter.Filter{PowerStates: []string{"POWERED_ON", " "}}},
		{name: "repeated power states", filter: vcenter.Filter{PowerStates: []string{"POWERED_ON", "POWERED_ON"}}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.Start(t, contractPath(t), contractmock.RotateWhileBusy)
			client := newClient(t, server)
			ctx := context.Background()
			if err := client.Login(ctx); err != nil {
				t.Fatalf("Login: %v", err)
			}
			vms, err := client.ListVMs(ctx, test.filter)
			if err == nil {
				t.Fatalf("ListVMs accepted an invalid filter and returned %+v", vms)
			}
			if vms != nil {
				t.Errorf("rejected filter returned %+v, want no partial result", vms)
			}
			if requests := server.Requests(); len(requests) != 1 {
				t.Fatalf("request log = %v, want only the login request", requests)
			}
		})
	}
}

func TestRotationFailureTable(t *testing.T) {
	tests := []struct {
		name           string
		mode           contractmock.Mode
		action         string
		wantOperation  string
		wantStatus     int
		wantOperations []string
		verify         func(t *testing.T, server *contractmock.Server, client *vcenter.Client)
	}{
		{
			name:           "a rejected replacement secret leaves the old session live",
			mode:           contractmock.RejectNextCredential,
			action:         "rotate",
			wantOperation:  "Cis.Session_create",
			wantStatus:     http.StatusUnauthorized,
			wantOperations: []string{"Cis.Session_create", "Cis.Session_create"},
			verify: func(t *testing.T, server *contractmock.Server, client *vcenter.Client) {
				vms, err := client.ListVMs(context.Background(), vcenter.Filter{Names: []string{"db-tier-01"}})
				if err != nil {
					t.Fatalf("the original session was retired after a failed rotation: %v", err)
				}
				if want := expectedVMs(server, "vm-103"); !reflect.DeepEqual(vms, want) {
					t.Fatalf("ListVMs = %s, want %s", describe(vms), describe(want))
				}
				requests := server.Requests()
				last := requests[len(requests)-1]
				if last.SessionToken != server.IssuedTokens()[0] {
					t.Errorf("post-failure request used %v, want the original session token", last)
				}
			},
		},
		{
			name:           "a failed old-session delete is reported once and not retried",
			mode:           contractmock.DeleteUnavailable,
			action:         "rotate",
			wantOperation:  "Cis.Session_delete",
			wantStatus:     http.StatusServiceUnavailable,
			wantOperations: []string{"Cis.Session_create", "Cis.Session_create", "Cis.Session_delete"},
			verify: func(t *testing.T, server *contractmock.Server, client *vcenter.Client) {
				if _, err := client.ListVMs(context.Background(), vcenter.Filter{}); err != nil {
					t.Fatalf("the replacement session was not current after rotation: %v", err)
				}
				requests := server.Requests()
				deletions := 0
				for _, request := range requests {
					if request.OperationID == "Cis.Session_delete" {
						deletions++
					}
				}
				if len(requests) != 4 || deletions != 1 {
					t.Fatalf("request log after the next operation = %v, want one unretried delete", requests)
				}
				last := requests[len(requests)-1]
				if last.SessionToken != server.IssuedTokens()[1] {
					t.Errorf("post-rotation request used %v, want the replacement session token", last)
				}
			},
		},
		{
			name:           "a legacy value envelope is not a session token",
			mode:           contractmock.LegacySessionEnvelope,
			action:         "login",
			wantOperation:  "Cis.Session_create",
			wantOperations: []string{"Cis.Session_create"},
			verify: func(t *testing.T, server *contractmock.Server, client *vcenter.Client) {
				if _, err := client.ListVMs(context.Background(), vcenter.Filter{}); !errors.Is(err, vcenter.ErrNoSession) {
					t.Fatalf("ListVMs error = %v, want ErrNoSession", err)
				}
				if requests := server.Requests(); len(requests) != 1 {
					t.Fatalf("request log = %v, want no request without a session", requests)
				}
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.Start(t, contractPath(t), test.mode)
			client := newClient(t, server)
			ctx := context.Background()

			var err error
			if test.action == "rotate" {
				if loginErr := client.Login(ctx); loginErr != nil {
					t.Fatalf("Login: %v", loginErr)
				}
				err = client.Rotate(ctx, credential(server.NextCredential()))
			} else {
				err = client.Login(ctx)
			}
			if err == nil {
				t.Fatal("the failing rotation step reported success")
			}
			if test.wantStatus != 0 {
				var apiError *vcenter.APIError
				if !errors.As(err, &apiError) || apiError.OperationID != test.wantOperation || apiError.StatusCode != test.wantStatus {
					t.Fatalf("error = %T %v, want APIError{%s %d}", err, err, test.wantOperation, test.wantStatus)
				}
			} else {
				var protocolError *vcenter.ProtocolError
				if !errors.As(err, &protocolError) || protocolError.OperationID != test.wantOperation {
					t.Fatalf("error = %T %v, want ProtocolError{%s}", err, err, test.wantOperation)
				}
			}
			for _, secret := range []string{
				server.FirstCredential().Password,
				server.NextCredential().Password,
				strings.Join(server.IssuedTokens(), " "),
			} {
				if secret != "" && strings.Contains(err.Error(), secret) {
					t.Error("the error message exposes a credential or session token")
				}
			}

			requests := server.Requests()
			if len(requests) != len(test.wantOperations) {
				t.Fatalf("request log = %v, want %v", requests, test.wantOperations)
			}
			for index, request := range requests {
				if request.OperationID != test.wantOperations[index] {
					t.Fatalf("request %d = %v, want %s", index, request, test.wantOperations[index])
				}
				assertBodylessWireShape(t, index, request)
			}
			test.verify(t, server, client)
		})
	}
}

func TestSessionCreateResponseContractTable(t *testing.T) {
	tests := []struct {
		name       string
		response   fixtureResponse
		wantStatus int
	}{
		{name: "status must be exactly 201", response: fixtureResponse{status: http.StatusOK, mediaType: "application/json", body: `"session-token"`}, wantStatus: http.StatusOK},
		{name: "media type must be JSON", response: fixtureResponse{status: http.StatusCreated, mediaType: "text/plain", body: `"session-token"`}},
		{name: "body must be valid JSON", response: fixtureResponse{status: http.StatusCreated, mediaType: "application/json", body: `"unfinished`}},
		{name: "token must be a bare string", response: fixtureResponse{status: http.StatusCreated, mediaType: "application/json", body: `{"value":"session-token"}`}},
		{name: "token must not be blank", response: fixtureResponse{status: http.StatusCreated, mediaType: "application/json", body: `" \t "`}},
		{name: "token must be header safe", response: fixtureResponse{status: http.StatusCreated, mediaType: "application/json", body: `"\u0001"`}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			client, _ := newFixtureResponseClient(t, test.response, validListResponse(), validDeleteResponse(), false)
			err := client.Login(context.Background())
			if err == nil {
				t.Fatal("Login accepted a contract-violating success response")
			}
			if test.wantStatus != 0 {
				var apiError *vcenter.APIError
				if !errors.As(err, &apiError) || apiError.OperationID != "Cis.Session_create" || apiError.StatusCode != test.wantStatus {
					t.Fatalf("error = %T %v, want APIError{Cis.Session_create %d}", err, err, test.wantStatus)
				}
			} else {
				var protocolError *vcenter.ProtocolError
				if !errors.As(err, &protocolError) || protocolError.OperationID != "Cis.Session_create" {
					t.Fatalf("error = %T %v, want ProtocolError{Cis.Session_create}", err, err)
				}
			}
			if _, listErr := client.ListVMs(context.Background(), vcenter.Filter{}); !errors.Is(listErr, vcenter.ErrNoSession) {
				t.Fatalf("ListVMs after rejected Login = %v, want ErrNoSession", listErr)
			}
		})
	}
}

func TestVMListResponseContractTable(t *testing.T) {
	tests := []struct {
		name       string
		response   fixtureResponse
		wantStatus int
	}{
		{name: "status must be exactly 200", response: fixtureResponse{status: http.StatusCreated, mediaType: "application/json", body: `[]`}, wantStatus: http.StatusCreated},
		{name: "media type must be JSON", response: fixtureResponse{status: http.StatusOK, mediaType: "text/plain", body: `[]`}},
		{name: "body must be valid JSON", response: fixtureResponse{status: http.StatusOK, mediaType: "application/json", body: `[`}},
		{name: "array must not be null", response: fixtureResponse{status: http.StatusOK, mediaType: "application/json", body: `null`}},
		{name: "top level must be an array", response: fixtureResponse{status: http.StatusOK, mediaType: "application/json", body: `{}`}},
		{name: "each summary must be an object", response: fixtureResponse{status: http.StatusOK, mediaType: "application/json", body: `[null]`}},
		{name: "required property must be present", response: fixtureResponse{status: http.StatusOK, mediaType: "application/json", body: `[{"vm":"vm-1","name":"one"}]`}},
		{name: "required property must be a string", response: fixtureResponse{status: http.StatusOK, mediaType: "application/json", body: `[{"vm":"vm-1","name":7,"power_state":"POWERED_ON"}]`}},
		{name: "required property must not be blank", response: fixtureResponse{status: http.StatusOK, mediaType: "application/json", body: `[{"vm":"vm-1","name":" ","power_state":"POWERED_ON"}]`}},
		{name: "vm identifier must be unique", response: fixtureResponse{status: http.StatusOK, mediaType: "application/json", body: `[{"vm":"vm-1","name":"one","power_state":"POWERED_ON"},{"vm":"vm-1","name":"duplicate","power_state":"POWERED_OFF"}]`}},
		{name: "cpu count must not be boolean", response: fixtureResponse{status: http.StatusOK, mediaType: "application/json", body: `[{"vm":"vm-1","name":"one","power_state":"POWERED_ON","cpu_count":true}]`}},
		{name: "cpu count must be an integer", response: fixtureResponse{status: http.StatusOK, mediaType: "application/json", body: `[{"vm":"vm-1","name":"one","power_state":"POWERED_ON","cpu_count":1.5}]`}},
		{name: "memory must fit int64", response: fixtureResponse{status: http.StatusOK, mediaType: "application/json", body: `[{"vm":"vm-1","name":"one","power_state":"POWERED_ON","memory_size_mib":9223372036854775808}]`}},
		{name: "failure returns no partial slice", response: fixtureResponse{status: http.StatusOK, mediaType: "application/json", body: `[{"vm":"vm-1","name":"one","power_state":"POWERED_ON"},{"vm":"vm-2","name":"two","power_state":"POWERED_ON","memory_size_mib":"large"}]`}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			client, _ := newFixtureResponseClient(t, validCreateResponse(), test.response, validDeleteResponse(), false)
			if err := client.Login(context.Background()); err != nil {
				t.Fatalf("Login: %v", err)
			}
			vms, err := client.ListVMs(context.Background(), vcenter.Filter{})
			if err == nil {
				t.Fatalf("ListVMs accepted a contract-violating success response: %+v", vms)
			}
			if vms != nil {
				t.Errorf("failed ListVMs returned a partial slice: %+v", vms)
			}
			if test.wantStatus != 0 {
				var apiError *vcenter.APIError
				if !errors.As(err, &apiError) || apiError.OperationID != "Vcenter.VM_list" || apiError.StatusCode != test.wantStatus {
					t.Fatalf("error = %T %v, want APIError{Vcenter.VM_list %d}", err, err, test.wantStatus)
				}
			} else {
				var protocolError *vcenter.ProtocolError
				if !errors.As(err, &protocolError) || protocolError.OperationID != "Vcenter.VM_list" {
					t.Fatalf("error = %T %v, want ProtocolError{Vcenter.VM_list}", err, err)
				}
			}
			for _, secret := range []string{"fixture-secret", "fixture-session-token"} {
				if strings.Contains(err.Error(), secret) {
					t.Errorf("error exposes a credential or session token")
				}
			}
		})
	}
}

func TestVMListPreservesResponseOrderAndNullableIntegers(t *testing.T) {
	list := fixtureResponse{
		status:    http.StatusOK,
		mediaType: "application/json; charset=utf-8",
		body:      `[{"vm":"vm-2","name":"two","power_state":"POWERED_OFF","cpu_count":null,"memory_size_mib":null},{"vm":"vm-1","name":"one","power_state":"POWERED_ON","cpu_count":4,"memory_size_mib":8192}]`,
	}
	client, _ := newFixtureResponseClient(t, validCreateResponse(), list, validDeleteResponse(), false)
	if err := client.Login(context.Background()); err != nil {
		t.Fatalf("Login: %v", err)
	}
	vms, err := client.ListVMs(context.Background(), vcenter.Filter{})
	if err != nil {
		t.Fatalf("ListVMs: %v", err)
	}
	cpu, memory := int64(4), int64(8192)
	want := []vcenter.VM{
		{VM: "vm-2", Name: "two", PowerState: "POWERED_OFF"},
		{VM: "vm-1", Name: "one", PowerState: "POWERED_ON", CPUCount: &cpu, MemorySizeMiB: &memory},
	}
	if !reflect.DeepEqual(vms, want) {
		t.Fatalf("ListVMs = %s, want %s", describe(vms), describe(want))
	}
}

func TestSessionDeleteStatusMustBeExactly204(t *testing.T) {
	client, _ := newFixtureResponseClient(t, validCreateResponse(), validListResponse(), fixtureResponse{status: http.StatusOK, mediaType: "application/json", body: `{}`}, false)
	if err := client.Login(context.Background()); err != nil {
		t.Fatalf("Login: %v", err)
	}
	err := client.Logout(context.Background())
	var apiError *vcenter.APIError
	if !errors.As(err, &apiError) || apiError.OperationID != "Cis.Session_delete" || apiError.StatusCode != http.StatusOK {
		t.Fatalf("Logout error = %T %v, want APIError{Cis.Session_delete 200}", err, err)
	}
}

func TestNilHTTPClientUsesDefaultClient(t *testing.T) {
	client, defaultTransport := newFixtureResponseClient(t, validCreateResponse(), validListResponse(), validDeleteResponse(), true)
	ctx := context.Background()
	if err := client.Login(ctx); err != nil {
		t.Fatalf("Login through http.DefaultClient: %v", err)
	}
	if vms, err := client.ListVMs(ctx, vcenter.Filter{}); err != nil || len(vms) != 0 {
		t.Fatalf("ListVMs through http.DefaultClient = %+v, %v; want empty success", vms, err)
	}
	if err := client.Logout(ctx); err != nil {
		t.Fatalf("Logout through http.DefaultClient: %v", err)
	}
	if calls := defaultTransport.calls.Load(); calls != 3 {
		t.Fatalf("http.DefaultClient transport calls = %d, want exactly three", calls)
	}
}

func TestInputValidationTable(t *testing.T) {
	constructorTests := []struct {
		name        string
		serviceRoot string
		credential  vcenter.Credential
	}{
		{name: "relative service root", serviceRoot: "/api", credential: vcenter.Credential{Username: "user", Password: "secret"}},
		{name: "service root carries a path", serviceRoot: "https://127.0.0.1/api", credential: vcenter.Credential{Username: "user", Password: "secret"}},
		{name: "service root carries a slash path", serviceRoot: "https://127.0.0.1/", credential: vcenter.Credential{Username: "user", Password: "secret"}},
		{name: "service root carries a query", serviceRoot: "https://127.0.0.1?probe=1", credential: vcenter.Credential{Username: "user", Password: "secret"}},
		{name: "service root carries a fragment", serviceRoot: "https://127.0.0.1#probe", credential: vcenter.Credential{Username: "user", Password: "secret"}},
		{name: "service root carries userinfo", serviceRoot: "https://name:secret@127.0.0.1", credential: vcenter.Credential{Username: "user", Password: "secret"}},
		{name: "non HTTP scheme", serviceRoot: "ftp://127.0.0.1", credential: vcenter.Credential{Username: "user", Password: "secret"}},
		{name: "blank username", serviceRoot: "https://127.0.0.1", credential: vcenter.Credential{Username: "  ", Password: "secret"}},
		{name: "blank password", serviceRoot: "https://127.0.0.1", credential: vcenter.Credential{Username: "user", Password: ""}},
	}
	for _, test := range constructorTests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := vcenter.NewClient(test.serviceRoot, test.credential, nil); err == nil {
				t.Fatal("NewClient accepted invalid input")
			}
		})
	}

	server := contractmock.Start(t, contractPath(t), contractmock.RotateWhileBusy)
	client := newClient(t, server)
	ctx := context.Background()
	sessionless := []struct {
		name string
		call func() error
	}{
		{name: "list without a session", call: func() error { _, err := client.ListVMs(ctx, vcenter.Filter{}); return err }},
		{name: "rotate without a session", call: func() error { return client.Rotate(ctx, credential(server.NextCredential())) }},
		{name: "logout without a session", call: func() error { return client.Logout(ctx) }},
	}
	for _, test := range sessionless {
		t.Run(test.name, func(t *testing.T) {
			if err := test.call(); !errors.Is(err, vcenter.ErrNoSession) {
				t.Fatalf("error = %v, want ErrNoSession", err)
			}
			if requests := server.Requests(); len(requests) != 0 {
				t.Fatalf("request log = %v, want no request without a session", requests)
			}
		})
	}

	if err := client.Login(ctx); err != nil {
		t.Fatalf("Login: %v", err)
	}
	guarded := []struct {
		name string
		call func() error
	}{
		{name: "second login", call: func() error { return client.Login(ctx) }},
		{name: "nil context login", call: func() error { return client.Login(nil) }}, //nolint:staticcheck
		{name: "blank replacement username", call: func() error {
			return client.Rotate(ctx, vcenter.Credential{Username: " ", Password: "secret"})
		}},
		{name: "blank replacement password", call: func() error {
			return client.Rotate(ctx, vcenter.Credential{Username: "user", Password: ""})
		}},
		{name: "nil context list", call: func() error { _, err := client.ListVMs(nil, vcenter.Filter{}); return err }},      //nolint:staticcheck
		{name: "nil context rotate", call: func() error { return client.Rotate(nil, credential(server.NextCredential())) }}, //nolint:staticcheck
		{name: "nil context logout", call: func() error { return client.Logout(nil) }},                                      //nolint:staticcheck
	}
	for _, test := range guarded {
		t.Run(test.name, func(t *testing.T) {
			before := len(server.Requests())
			if err := test.call(); err == nil {
				t.Fatal("the client accepted invalid input")
			}
			if after := len(server.Requests()); after != before {
				t.Fatalf("request count %d -> %d, want no request", before, after)
			}
		})
	}

	if err := client.Logout(ctx); err != nil {
		t.Fatalf("Logout: %v", err)
	}
	if err := client.Logout(ctx); !errors.Is(err, vcenter.ErrNoSession) {
		t.Fatalf("second Logout error = %v, want ErrNoSession", err)
	}
}

type fixtureResponse struct {
	status    int
	mediaType string
	body      string
}

type countingRoundTripper struct {
	base  http.RoundTripper
	calls atomic.Int64
}

func (transport *countingRoundTripper) RoundTrip(request *http.Request) (*http.Response, error) {
	transport.calls.Add(1)
	return transport.base.RoundTrip(request)
}

func validCreateResponse() fixtureResponse {
	return fixtureResponse{status: http.StatusCreated, mediaType: "application/json", body: `"fixture-session-token"`}
}

func validListResponse() fixtureResponse {
	return fixtureResponse{status: http.StatusOK, mediaType: "application/json", body: `[]`}
}

func validDeleteResponse() fixtureResponse {
	return fixtureResponse{status: http.StatusNoContent}
}

// newFixtureResponseClient uses a real loopback HTTP service to exercise
// response validation independently from the stateful rotation mock.
func newFixtureResponseClient(t *testing.T, create, list, delete fixtureResponse, useDefaultClient bool) (*vcenter.Client, *countingRoundTripper) {
	t.Helper()
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var response fixtureResponse
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/session":
			response = create
		case r.Method == http.MethodGet && r.URL.Path == "/api/vcenter/vm":
			response = list
		case r.Method == http.MethodDelete && r.URL.Path == "/api/session":
			response = delete
		default:
			response = fixtureResponse{status: http.StatusNotFound, mediaType: "application/json", body: `{}`}
		}
		if response.mediaType != "" {
			w.Header().Set("Content-Type", response.mediaType)
		}
		w.WriteHeader(response.status)
		if response.body != "" {
			_, _ = w.Write([]byte(response.body))
		}
	})
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("start response fixture: %v", err)
	}
	server := httptest.NewUnstartedServer(handler)
	server.Listener = listener
	server.Start()
	t.Cleanup(server.Close)
	var httpClient *http.Client
	var defaultTransport *countingRoundTripper
	if !useDefaultClient {
		httpClient = server.Client()
	} else {
		previousDefault := http.DefaultClient
		defaultTransport = &countingRoundTripper{base: server.Client().Transport}
		http.DefaultClient = &http.Client{Transport: defaultTransport}
		t.Cleanup(func() { http.DefaultClient = previousDefault })
	}
	client, err := vcenter.NewClient(server.URL, vcenter.Credential{Username: "fixture-user", Password: "fixture-secret"}, httpClient)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client, defaultTransport
}

func newClient(t *testing.T, server *contractmock.Server) *vcenter.Client {
	t.Helper()
	client, err := vcenter.NewClient(server.URL(), credential(server.FirstCredential()), &http.Client{Timeout: 15 * time.Second})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func credential(source contractmock.Credential) vcenter.Credential {
	return vcenter.Credential{Username: source.Username, Password: source.Password}
}

func expectedVMs(server *contractmock.Server, identifiers ...string) []vcenter.VM {
	wanted := map[string]bool{}
	for _, identifier := range identifiers {
		wanted[identifier] = true
	}
	result := make([]vcenter.VM, 0, len(identifiers))
	for _, vm := range server.VMs() {
		if !wanted[vm.VM] {
			continue
		}
		result = append(result, vcenter.VM{
			VM:            vm.VM,
			Name:          vm.Name,
			PowerState:    vm.PowerState,
			CPUCount:      vm.CPUCount,
			MemorySizeMiB: vm.MemorySizeMiB,
		})
	}
	return result
}

func describe(vms []vcenter.VM) string {
	if vms == nil {
		return "<nil>"
	}
	rendered := make([]string, 0, len(vms))
	for _, vm := range vms {
		rendered = append(rendered, fmt.Sprintf("{%s %s %s cpu=%s memoryMiB=%s}",
			vm.VM, vm.Name, vm.PowerState, renderInt64(vm.CPUCount), renderInt64(vm.MemorySizeMiB)))
	}
	return "[" + strings.Join(rendered, " ") + "]"
}

func renderInt64(value *int64) string {
	if value == nil {
		return "absent"
	}
	return fmt.Sprintf("%d", *value)
}

func assertBodylessWireShape(t *testing.T, index int, request contractmock.Request) {
	t.Helper()
	if len(request.Body) != 0 || request.ContentLength != 0 || len(request.TransferEncoding) != 0 {
		t.Errorf("request %d is not bodyless: body=%d contentLength=%d transferEncoding=%v",
			index, len(request.Body), request.ContentLength, request.TransferEncoding)
	}
	if values := request.Header.Values("Content-Type"); len(values) != 0 {
		t.Errorf("request %d sent Content-Type %v, want absent", index, values)
	}
}

func assertSingleHeader(t *testing.T, header http.Header, name, want string) {
	t.Helper()
	values := header.Values(name)
	if len(values) != 1 || values[0] != want {
		t.Errorf("%s values = %q, want exactly one entry", name, values)
	}
}

func assertHeaderAbsent(t *testing.T, header http.Header, name string) {
	t.Helper()
	if values := header.Values(name); len(values) != 0 {
		t.Errorf("%s values = %q, want absent", name, values)
	}
}

func basicAuth(credential contractmock.Credential) string {
	return "Basic " + base64.StdEncoding.EncodeToString([]byte(credential.Username+":"+credential.Password))
}

// queryMemberOrder returns the distinct query members in first-appearance
// order and fails when a member's repeated keys are not contiguous.
func queryMemberOrder(t *testing.T, rawQuery string) []string {
	t.Helper()
	if rawQuery == "" {
		return nil
	}
	var order []string
	seen := map[string]bool{}
	previous := ""
	for _, pair := range strings.Split(rawQuery, "&") {
		member, _, found := strings.Cut(pair, "=")
		if !found || member == "" {
			t.Fatalf("query pair %q is not a member=value assignment", pair)
		}
		if member != previous {
			if seen[member] {
				t.Fatalf("query member %q is repeated non-contiguously in %q", member, rawQuery)
			}
			order = append(order, member)
			seen[member] = true
			previous = member
		}
	}
	return order
}

func waitForClose(t *testing.T, signal <-chan struct{}, message string) {
	t.Helper()
	select {
	case <-signal:
	case <-time.After(15 * time.Second):
		t.Fatal(message)
	}
}

func waitForRequestCount(t *testing.T, server *contractmock.Server, want int) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if len(server.Requests()) >= want {
			return
		}
		time.Sleep(2 * time.Millisecond)
	}
	t.Fatalf("request log = %v, want at least %d requests", server.Requests(), want)
}

func ExampleClient_Rotate() {
	fmt.Println("the verifier drives only an ephemeral loopback vCenter fixture")
	// Output: the verifier drives only an ephemeral loopback vCenter fixture
}
