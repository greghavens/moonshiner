package cpusweep_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"

	cs "vcf90-0048"
	"vcf90-0048/internal/contractmock"
)

const (
	expectedTag    = "9.0.0.0"
	expectedCommit = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
	expectedSpec   = "specifications/vsphere/openapi/automation/vcenter.yaml"
	rejectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	contractSHA256 = "7afbaf42877913cf23e4218e293a55923ad43ba5bda4114673fe96e0d353b712"
	sourcesSHA256  = "c2bb34ebbf0133f532270aaa1c9849d3d1ea9641ba9242ef9444cda4d1fdc328"
)

// updateBody is the body the sweep must send for the default plan.
const updateBody = `{"count":4,"cores_per_socket":2}`

type operationSource struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func wantOperations() []operationSource {
	return []operationSource{
		{
			OperationID: "Cis.Session_create",
			Method:      "POST",
			Path:        "/session",
		},
		{
			OperationID: "Vcenter.VM_list",
			Method:      "GET",
			Path:        "/vcenter/vm",
		},
		{
			OperationID: "Vcenter.Vm.Hardware.Cpu_get",
			Method:      "GET",
			Path:        "/vcenter/vm/{vm}/hardware/cpu",
		},
		{
			OperationID: "Vcenter.Vm.Hardware.Cpu_update",
			Method:      "PATCH",
			Path:        "/vcenter/vm/{vm}/hardware/cpu",
		},
	}
}

func TestProtectedContractProvenance(t *testing.T) {
	assertFileHash(t, "docs/contract.json", contractSHA256)
	assertFileHash(t, "docs/official_sources.json", sourcesSHA256)

	var contract struct {
		DerivedFrom struct {
			Tag      string `json:"repository_tag"`
			Commit   string `json:"repository_commit_sha"`
			SpecPath string `json:"spec_path"`
			OpenAPI  string `json:"openapi"`
			Title    string `json:"info_title"`
			Version  string `json:"info_version"`
			License  string `json:"repository_license"`
		} `json:"derived_from"`
		Server struct {
			URL      string `json:"url"`
			BasePath string `json:"base_path"`
		} `json:"server"`
		SecuritySchemes map[string]struct {
			Type   string `json:"type"`
			Scheme string `json:"scheme"`
			Name   string `json:"name"`
			In     string `json:"in"`
		} `json:"security_schemes"`
		Operations []json.RawMessage          `json:"operations"`
		Schemas    map[string]json.RawMessage `json:"schemas"`
	}
	readJSON(t, "docs/contract.json", &contract)

	var sources struct {
		Repository struct {
			Tag     string `json:"tag"`
			Commit  string `json:"commit_sha"`
			License string `json:"license"`
		} `json:"repository"`
		Specification struct {
			Path    string `json:"path"`
			OpenAPI string `json:"openapi_version"`
			Version string `json:"info_version"`
		} `json:"specification"`
		Operations   []operationSource `json:"operations"`
		Derivation   string            `json:"derivation"`
		NotDerivedOf struct {
			RejectedTag    string `json:"rejected_tag"`
			RejectedCommit string `json:"rejected_commit_sha"`
		} `json:"not_derived_from"`
	}
	readJSON(t, "docs/official_sources.json", &sources)

	if contract.DerivedFrom.Commit != expectedCommit ||
		sources.Repository.Commit != expectedCommit {
		t.Fatalf(
			"wrong repository commit: contract=%q sources=%q want %q",
			contract.DerivedFrom.Commit,
			sources.Repository.Commit,
			expectedCommit,
		)
	}
	if contract.DerivedFrom.Tag != expectedTag || sources.Repository.Tag != expectedTag {
		t.Fatalf(
			"wrong repository tag: contract=%q sources=%q",
			contract.DerivedFrom.Tag,
			sources.Repository.Tag,
		)
	}
	if contract.DerivedFrom.SpecPath != expectedSpec ||
		sources.Specification.Path != expectedSpec {
		t.Fatalf(
			"wrong specification path: contract=%q sources=%q",
			contract.DerivedFrom.SpecPath,
			sources.Specification.Path,
		)
	}
	if contract.DerivedFrom.OpenAPI != "3.0.3" ||
		sources.Specification.OpenAPI != "3.0.3" ||
		contract.DerivedFrom.Title != "vSphere Automation API" ||
		contract.DerivedFrom.Version != "9.0.0.0" ||
		sources.Specification.Version != "9.0.0.0" ||
		contract.DerivedFrom.License != "Apache-2.0" ||
		sources.Repository.License != "Apache-2.0" {
		t.Fatalf(
			"incorrect version/license provenance: contract=%+v sources=%+v",
			contract.DerivedFrom,
			sources,
		)
	}
	if sources.NotDerivedOf.RejectedCommit != rejectedCommit ||
		sources.NotDerivedOf.RejectedTag != "9.1.0.0" ||
		sources.NotDerivedOf.RejectedCommit == expectedCommit {
		t.Fatalf(
			"the 9.1 revision is not recorded as excluded: %+v",
			sources.NotDerivedOf,
		)
	}
	if !strings.Contains(sources.Derivation, "OpenAPI specification") ||
		!strings.Contains(sources.Derivation, "No rendered documentation page") ||
		!strings.Contains(sources.Derivation, "9.1") {
		t.Fatalf("source derivation is not explicit: %q", sources.Derivation)
	}

	if contract.Server.URL != "https://{host}/api" || contract.Server.BasePath != "/api" {
		t.Fatalf("server projection mismatch: %+v", contract.Server)
	}
	if contract.SecuritySchemes["basic_auth"].Type != "http" ||
		contract.SecuritySchemes["basic_auth"].Scheme != "basic" ||
		contract.SecuritySchemes["api_key_auth"].Type != "apiKey" ||
		contract.SecuritySchemes["api_key_auth"].Name != "vmware-api-session-id" ||
		contract.SecuritySchemes["api_key_auth"].In != "header" {
		t.Fatalf("security scheme projection mismatch: %+v", contract.SecuritySchemes)
	}

	var summaries []operationSource
	for _, raw := range contract.Operations {
		var summary operationSource
		if err := json.Unmarshal(raw, &summary); err != nil {
			t.Fatalf("decode operation: %v", err)
		}
		summaries = append(summaries, summary)
	}
	if !reflect.DeepEqual(summaries, wantOperations()) ||
		!reflect.DeepEqual(sources.Operations, wantOperations()) {
		t.Fatalf(
			"operation provenance mismatch\ncontract: %#v\nsources: %#v\nwant: %#v",
			summaries,
			sources.Operations,
			wantOperations(),
		)
	}

	assertOperationDetail(t, contract.Operations)
	assertSchemaProjection(t, contract.Schemas)
}

func assertOperationDetail(t *testing.T, raws []json.RawMessage) {
	t.Helper()
	type parameter struct {
		Name    string `json:"name"`
		In      string `json:"in"`
		Style   string `json:"style"`
		Explode bool   `json:"explode"`
		Schema  struct {
			Type        string `json:"type"`
			UniqueItems bool   `json:"uniqueItems"`
		} `json:"schema"`
	}
	type operation struct {
		OperationID string                `json:"operationId"`
		Security    []map[string][]string `json:"security"`
		PathParams  []parameter           `json:"path_parameters"`
		QueryParams []parameter           `json:"query_parameters"`
		Request     struct {
			Required  bool   `json:"required"`
			MediaType string `json:"media_type"`
			SchemaRef string `json:"schema_ref"`
		} `json:"request"`
		Responses map[string]struct {
			MediaType string          `json:"media_type"`
			SchemaRef string          `json:"schema_ref"`
			Schema    json.RawMessage `json:"schema"`
		} `json:"responses"`
	}

	operations := map[string]operation{}
	for _, raw := range raws {
		var decoded operation
		if err := json.Unmarshal(raw, &decoded); err != nil {
			t.Fatalf("decode operation detail: %v", err)
		}
		operations[decoded.OperationID] = decoded
	}

	login := operations["Cis.Session_create"]
	if len(login.Security) != 1 {
		t.Fatalf("Cis.Session_create security mismatch: %+v", login.Security)
	}
	if _, ok := login.Security[0]["basic_auth"]; !ok {
		t.Fatalf("Cis.Session_create must require basic_auth: %+v", login.Security)
	}
	if login.Responses["201"].MediaType != "application/json" ||
		!strings.Contains(string(login.Responses["201"].Schema), `"type": "string"`) {
		t.Fatalf("Cis.Session_create 201 projection mismatch: %+v", login.Responses["201"])
	}

	for _, operationID := range []string{
		"Vcenter.VM_list",
		"Vcenter.Vm.Hardware.Cpu_get",
		"Vcenter.Vm.Hardware.Cpu_update",
	} {
		operation := operations[operationID]
		if len(operation.Security) != 1 {
			t.Fatalf("%s security mismatch: %+v", operationID, operation.Security)
		}
		if _, ok := operation.Security[0]["api_key_auth"]; !ok {
			t.Fatalf("%s must require api_key_auth: %+v", operationID, operation.Security)
		}
	}

	list := operations["Vcenter.VM_list"]
	byName := map[string]parameter{}
	for _, param := range list.QueryParams {
		byName[param.Name] = param
	}
	for _, name := range []string{"names", "power_states"} {
		param, ok := byName[name]
		if !ok || param.In != "query" || param.Style != "form" || !param.Explode ||
			param.Schema.Type != "array" || !param.Schema.UniqueItems {
			t.Fatalf("Vcenter.VM_list %s parameter projection mismatch: %+v", name, param)
		}
	}
	if list.Responses["200"].MediaType != "application/json" {
		t.Fatalf("Vcenter.VM_list 200 projection mismatch: %+v", list.Responses["200"])
	}

	get := operations["Vcenter.Vm.Hardware.Cpu_get"]
	if len(get.PathParams) != 1 || get.PathParams[0].Name != "vm" ||
		get.PathParams[0].In != "path" {
		t.Fatalf("Cpu_get path parameter projection mismatch: %+v", get.PathParams)
	}
	if get.Responses["200"].SchemaRef !=
		"#/components/schemas/Vcenter.Vm.Hardware.Cpu.Info" {
		t.Fatalf("Cpu_get 200 projection mismatch: %+v", get.Responses["200"])
	}

	update := operations["Vcenter.Vm.Hardware.Cpu_update"]
	if !update.Request.Required ||
		update.Request.MediaType != "application/json" ||
		update.Request.SchemaRef !=
			"#/components/schemas/Vcenter.Vm.Hardware.Cpu.UpdateSpec" {
		t.Fatalf("Cpu_update request projection mismatch: %+v", update.Request)
	}
	noContent, ok := update.Responses["204"]
	if !ok || noContent.MediaType != "" || noContent.SchemaRef != "" {
		t.Fatalf("Cpu_update 204 must carry no content: %+v", update.Responses["204"])
	}
	if update.Responses["401"].SchemaRef !=
		"#/components/schemas/Vapi.Std.Errors.Unauthenticated" {
		t.Fatalf("Cpu_update 401 projection mismatch: %+v", update.Responses["401"])
	}
}

func assertSchemaProjection(t *testing.T, schemas map[string]json.RawMessage) {
	t.Helper()
	type schema struct {
		Required   []string `json:"required"`
		Properties map[string]struct {
			Type        string `json:"type"`
			Format      string `json:"format"`
			Ref         string `json:"$ref"`
			UniqueItems bool   `json:"uniqueItems"`
		} `json:"properties"`
	}
	decode := func(name string) schema {
		raw, ok := schemas[name]
		if !ok {
			t.Fatalf("contract omits schema %s", name)
		}
		var decoded schema
		if err := json.Unmarshal(raw, &decoded); err != nil {
			t.Fatalf("decode %s: %v", name, err)
		}
		return decoded
	}

	// Every UpdateSpec member is optional; that is why an unset member must be
	// omitted from the request body rather than sent as a zero value.
	updateSpec := decode("Vcenter.Vm.Hardware.Cpu.UpdateSpec")
	if len(updateSpec.Required) != 0 {
		t.Fatalf("Cpu.UpdateSpec must declare no required members: %+v", updateSpec.Required)
	}
	wantUpdate := map[string]string{
		"count":              "integer",
		"cores_per_socket":   "integer",
		"hot_add_enabled":    "boolean",
		"hot_remove_enabled": "boolean",
	}
	if len(updateSpec.Properties) != len(wantUpdate) {
		t.Fatalf("Cpu.UpdateSpec property set mismatch: %+v", updateSpec.Properties)
	}
	for name, kind := range wantUpdate {
		if updateSpec.Properties[name].Type != kind {
			t.Fatalf("Cpu.UpdateSpec %s type = %q, want %q",
				name, updateSpec.Properties[name].Type, kind)
		}
	}

	info := decode("Vcenter.Vm.Hardware.Cpu.Info")
	if !reflect.DeepEqual(info.Required, []string{
		"cores_per_socket",
		"count",
		"hot_add_enabled",
		"hot_remove_enabled",
	}) {
		t.Fatalf("Cpu.Info required mismatch: %+v", info.Required)
	}

	summary := decode("Vcenter.VM.Summary")
	if !reflect.DeepEqual(summary.Required, []string{"name", "power_state", "vm"}) {
		t.Fatalf("VM.Summary required mismatch: %+v", summary.Required)
	}

	failure := decode("Vapi.Std.Errors.Error")
	if !reflect.DeepEqual(failure.Required, []string{"error_type", "messages"}) {
		t.Fatalf("Vapi.Std.Errors.Error required mismatch: %+v", failure.Required)
	}
	if _, ok := schemas["Vapi.Std.Errors.Unauthenticated"]; !ok {
		t.Fatalf("contract omits Vapi.Std.Errors.Unauthenticated")
	}
}

// TestSweepRefreshesExpiredSessionWithoutLosingWork drives the token expiry to
// every position in the workflow and requires that the interrupted request is
// replayed verbatim while completed work is neither lost nor repeated.
func TestSweepRefreshesExpiredSessionWithoutLosingWork(t *testing.T) {
	// The authenticated request order the default plan must produce.
	baseline := []string{
		contractmock.VMList,
		contractmock.CPUGet,    // alpha
		contractmock.CPUUpdate, // alpha
		contractmock.CPUGet,    // bravo
		contractmock.CPUUpdate, // bravo
		contractmock.CPUGet,    // charlie, already correct
		contractmock.CPUGet,    // delta
		contractmock.CPUUpdate, // delta
	}

	for _, budget := range []int{0, 1, 2, 3, 4, 5, 6, 7} {
		t.Run(fmt.Sprintf("expires_after_%d_requests", budget), func(t *testing.T) {
			server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
				plan := validPlan(runtime)
				plan.TokenBudgets = []int{budget}
				return plan
			})
			runtime := server.Runtime()

			var hookMu sync.Mutex
			var hookCalls []int
			client := newClient(t, server, 2, func(
				ctx context.Context,
				operationID string,
				refreshCount int,
			) error {
				if operationID != cs.OpSessionCreate {
					t.Errorf("OnReauth operationId = %q", operationID)
				}
				hookMu.Lock()
				hookCalls = append(hookCalls, refreshCount)
				hookMu.Unlock()
				return ctx.Err()
			})

			result, err := client.Sweep(context.Background(), defaultRequest())
			if err != nil {
				t.Fatalf("Sweep: %v", err)
			}
			if result.Reauths != 1 {
				t.Fatalf("Reauths = %d, want 1", result.Reauths)
			}
			hookMu.Lock()
			gotHook := append([]int(nil), hookCalls...)
			hookMu.Unlock()
			if !reflect.DeepEqual(gotHook, []int{1}) {
				t.Fatalf("OnReauth calls = %v, want [1]", gotHook)
			}

			// The interrupted request is replayed once, after one extra login.
			wantSequence := []string{contractmock.SessionCreate}
			wantSequence = append(wantSequence, baseline[:budget]...)
			wantSequence = append(wantSequence, baseline[budget], contractmock.SessionCreate)
			wantSequence = append(wantSequence, baseline[budget:]...)

			requests := server.Requests()
			if got := operationSequence(requests); !reflect.DeepEqual(got, wantSequence) {
				t.Fatalf("operation sequence =\n%v\nwant\n%v", got, wantSequence)
			}

			expired := requests[budget+1]
			replay := requests[budget+3]
			if expired.Status != http.StatusUnauthorized {
				t.Fatalf("interrupted request status = %d, want 401", expired.Status)
			}
			if requests[budget+2].OperationID != contractmock.SessionCreate {
				t.Fatalf("the 401 was not followed by Cis.Session_create")
			}
			if expired.Method != replay.Method ||
				expired.EscapedPath != replay.EscapedPath ||
				expired.RawQuery != replay.RawQuery ||
				string(expired.Body) != string(replay.Body) {
				t.Fatalf("replay is not verbatim:\nexpired=%+v\nreplay=%+v", expired, replay)
			}
			tokens := server.IssuedTokens()
			if len(tokens) != 2 {
				t.Fatalf("issued tokens = %d, want 2", len(tokens))
			}
			if expired.Header.Get(contractmock.SessionHeader) != tokens[0] {
				t.Fatalf("interrupted request did not carry the first session token")
			}
			if replay.Header.Get(contractmock.SessionHeader) != tokens[1] {
				t.Fatalf("replay did not carry the refreshed session token")
			}

			// No work is lost and none is repeated: each virtual machine that
			// needed a change was updated exactly once.
			applied := server.Applied()
			wantApplied := []string{runtime.AlphaID, runtime.BravoID, runtime.DeltaID}
			var gotApplied []string
			for _, update := range applied {
				gotApplied = append(gotApplied, update.VM)
				if string(update.Body) != updateBody {
					t.Fatalf("applied body = %q, want %q", update.Body, updateBody)
				}
			}
			if !reflect.DeepEqual(gotApplied, wantApplied) {
				t.Fatalf("applied updates = %v, want %v", gotApplied, wantApplied)
			}

			inventory := server.Inventory()
			for id, info := range inventory {
				if info.Count != 4 || info.CoresPerSocket != 2 {
					t.Fatalf("virtual machine %s ended at %+v, want 4 cores / 2 per socket",
						id, info)
				}
			}

			wantUpdated := []bool{true, true, false, true}
			wantMachines := validPlan(runtime).VMs
			if len(result.Outcomes) != len(wantUpdated) {
				t.Fatalf("outcomes = %d, want %d", len(result.Outcomes), len(wantUpdated))
			}
			for index, outcome := range result.Outcomes {
				machine := wantMachines[index]
				if outcome.VM != machine.ID || outcome.Name != machine.Name ||
					outcome.PowerState != machine.PowerState ||
					outcome.Before != (cs.CPUInfo{
						Count:            machine.CPU.Count,
						CoresPerSocket:   machine.CPU.CoresPerSocket,
						HotAddEnabled:    machine.CPU.HotAddEnabled,
						HotRemoveEnabled: machine.CPU.HotRemoveEnabled,
					}) {
					t.Fatalf("outcome %d did not preserve the server-order machine: %+v",
						index, outcome)
				}
				if outcome.Updated != wantUpdated[index] {
					t.Fatalf("outcome %d Updated = %v, want %v",
						index, outcome.Updated, wantUpdated[index])
				}
				wantChanged := []string(nil)
				if wantUpdated[index] {
					wantChanged = []string{"count", "cores_per_socket"}
				}
				if !equalStrings(outcome.Changed, wantChanged) ||
					len(outcome.Deferred) != 0 {
					t.Fatalf("outcome %d member decisions = changed %v, deferred %v",
						index, outcome.Changed, outcome.Deferred)
				}
			}
		})
	}
}

// TestUpdateSpecWireShapeTableDriven pins the exact request body, including
// that an unmanaged or already-correct optional member is omitted rather than
// sent as a zero value or an explicit null.
func TestUpdateSpecWireShapeTableDriven(t *testing.T) {
	tests := []struct {
		name         string
		powerState   string
		current      contractmock.CPUInfo
		desired      cs.Desired
		wantBody     string
		wantChanged  []string
		wantDeferred []string
	}{
		{
			name:        "count only",
			powerState:  "POWERED_ON",
			current:     contractmock.CPUInfo{Count: 2, CoresPerSocket: 1},
			desired:     cs.Desired{Count: int64Pointer(6)},
			wantBody:    `{"count":6}`,
			wantChanged: []string{"count"},
		},
		{
			name:        "cores per socket only",
			powerState:  "POWERED_ON",
			current:     contractmock.CPUInfo{Count: 6, CoresPerSocket: 1},
			desired:     cs.Desired{CoresPerSocket: int64Pointer(3)},
			wantBody:    `{"cores_per_socket":3}`,
			wantChanged: []string{"cores_per_socket"},
		},
		{
			name:       "both count members",
			powerState: "POWERED_ON",
			current:    contractmock.CPUInfo{Count: 2, CoresPerSocket: 1},
			desired: cs.Desired{
				Count:          int64Pointer(6),
				CoresPerSocket: int64Pointer(3),
			},
			wantBody:    `{"count":6,"cores_per_socket":3}`,
			wantChanged: []string{"count", "cores_per_socket"},
		},
		{
			name:       "already correct sends nothing",
			powerState: "POWERED_ON",
			current:    contractmock.CPUInfo{Count: 6, CoresPerSocket: 3},
			desired: cs.Desired{
				Count:          int64Pointer(6),
				CoresPerSocket: int64Pointer(3),
			},
			wantBody: "",
		},
		{
			name:        "hot add on a powered off machine",
			powerState:  "POWERED_OFF",
			current:     contractmock.CPUInfo{Count: 6, CoresPerSocket: 3},
			desired:     cs.Desired{HotAddEnabled: boolPointer(true)},
			wantBody:    `{"hot_add_enabled":true}`,
			wantChanged: []string{"hot_add_enabled"},
		},
		{
			name:         "hot add is deferred while running",
			powerState:   "POWERED_ON",
			current:      contractmock.CPUInfo{Count: 6, CoresPerSocket: 3},
			desired:      cs.Desired{HotAddEnabled: boolPointer(true)},
			wantBody:     "",
			wantDeferred: []string{"hot_add_enabled"},
		},
		{
			name:       "explicit false is sent when it differs",
			powerState: "POWERED_OFF",
			current: contractmock.CPUInfo{
				Count:            6,
				CoresPerSocket:   3,
				HotRemoveEnabled: true,
			},
			desired:     cs.Desired{HotRemoveEnabled: boolPointer(false)},
			wantBody:    `{"hot_remove_enabled":false}`,
			wantChanged: []string{"hot_remove_enabled"},
		},
		{
			name:       "running machine keeps only the permitted member",
			powerState: "POWERED_ON",
			current:    contractmock.CPUInfo{Count: 2, CoresPerSocket: 3},
			desired: cs.Desired{
				Count:         int64Pointer(6),
				HotAddEnabled: boolPointer(true),
			},
			wantBody:     `{"count":6}`,
			wantChanged:  []string{"count"},
			wantDeferred: []string{"hot_add_enabled"},
		},
		{
			name:       "every member differs",
			powerState: "POWERED_OFF",
			current:    contractmock.CPUInfo{Count: 2, CoresPerSocket: 1},
			desired: cs.Desired{
				Count:            int64Pointer(6),
				CoresPerSocket:   int64Pointer(3),
				HotAddEnabled:    boolPointer(true),
				HotRemoveEnabled: boolPointer(true),
			},
			wantBody: `{"count":6,"cores_per_socket":3,` +
				`"hot_add_enabled":true,"hot_remove_enabled":true}`,
			wantChanged: []string{
				"count",
				"cores_per_socket",
				"hot_add_enabled",
				"hot_remove_enabled",
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
				return contractmock.Plan{VMs: []contractmock.VM{{
					ID:         runtime.AlphaID,
					Name:       runtime.AlphaName,
					PowerState: test.powerState,
					CPU:        test.current,
				}}}
			})
			client := newClient(t, server, 1, nil)

			result, err := client.Sweep(context.Background(), cs.SweepRequest{
				Desired: test.desired,
			})
			if err != nil {
				t.Fatalf("Sweep: %v", err)
			}
			if len(result.Outcomes) != 1 {
				t.Fatalf("outcomes = %d, want 1", len(result.Outcomes))
			}
			outcome := result.Outcomes[0]
			if !equalStrings(outcome.Changed, test.wantChanged) {
				t.Fatalf("Changed = %v, want %v", outcome.Changed, test.wantChanged)
			}
			if !equalStrings(outcome.Deferred, test.wantDeferred) {
				t.Fatalf("Deferred = %v, want %v", outcome.Deferred, test.wantDeferred)
			}

			var updates []contractmock.Request
			for _, request := range server.Requests() {
				if request.OperationID == contractmock.CPUUpdate {
					updates = append(updates, request)
				}
			}
			if test.wantBody == "" {
				if len(updates) != 0 {
					t.Fatalf("expected no Cpu_update, got %d", len(updates))
				}
				if outcome.Updated {
					t.Fatalf("Updated = true, want false")
				}
				return
			}
			if len(updates) != 1 {
				t.Fatalf("Cpu_update count = %d, want 1", len(updates))
			}
			if !outcome.Updated {
				t.Fatalf("Updated = false, want true")
			}
			body := string(updates[0].Body)
			if body != test.wantBody {
				t.Fatalf("body = %q, want %q", body, test.wantBody)
			}
			if strings.Contains(body, "null") {
				t.Fatalf("body sent an explicit null: %q", body)
			}
			for _, member := range []string{
				"count",
				"cores_per_socket",
				"hot_add_enabled",
				"hot_remove_enabled",
			} {
				present := strings.Contains(body, `"`+member+`":`)
				wanted := containsString(test.wantChanged, member)
				if present != wanted {
					t.Fatalf("member %q present = %v, want %v in %q",
						member, present, wanted, body)
				}
			}
			if updates[0].ContentLength != int64(len(test.wantBody)) {
				t.Fatalf("Content-Length = %d, want %d",
					updates[0].ContentLength, len(test.wantBody))
			}
			if got := updates[0].Header.Get("Content-Type"); got != "application/json" {
				t.Fatalf("Content-Type = %q", got)
			}
		})
	}
}

// TestListFilterQueryWireShapeTableDriven pins the Vcenter.VM_list query,
// including that an unset filter produces no parameter and no dangling query
// delimiter.
func TestListFilterQueryWireShapeTableDriven(t *testing.T) {
	tests := []struct {
		name        string
		names       func(contractmock.RuntimeValues) []string
		powerStates []string
		wantQuery   func(contractmock.RuntimeValues) string
	}{
		{
			name:      "no filters",
			wantQuery: func(contractmock.RuntimeValues) string { return "" },
		},
		{
			name:        "empty slices are unset",
			names:       func(contractmock.RuntimeValues) []string { return []string{} },
			powerStates: []string{},
			wantQuery:   func(contractmock.RuntimeValues) string { return "" },
		},
		{
			name: "single name",
			names: func(runtime contractmock.RuntimeValues) []string {
				return []string{runtime.AlphaName}
			},
			wantQuery: func(runtime contractmock.RuntimeValues) string {
				return "names=" + url.QueryEscape(runtime.AlphaName)
			},
		},
		{
			name: "name needing percent encoding",
			names: func(runtime contractmock.RuntimeValues) []string {
				return []string{runtime.FilterName}
			},
			wantQuery: func(runtime contractmock.RuntimeValues) string {
				return "names=" + url.QueryEscape(runtime.FilterName)
			},
		},
		{
			name: "repeated names keep caller order",
			names: func(runtime contractmock.RuntimeValues) []string {
				return []string{runtime.BravoName, runtime.AlphaName}
			},
			wantQuery: func(runtime contractmock.RuntimeValues) string {
				return "names=" + url.QueryEscape(runtime.BravoName) +
					"&names=" + url.QueryEscape(runtime.AlphaName)
			},
		},
		{
			name:        "power states only",
			powerStates: []string{"POWERED_OFF", "SUSPENDED"},
			wantQuery: func(contractmock.RuntimeValues) string {
				return "power_states=POWERED_OFF&power_states=SUSPENDED"
			},
		},
		{
			name: "both filters",
			names: func(runtime contractmock.RuntimeValues) []string {
				return []string{runtime.AlphaName}
			},
			powerStates: []string{"POWERED_OFF"},
			wantQuery: func(runtime contractmock.RuntimeValues) string {
				return "names=" + url.QueryEscape(runtime.AlphaName) +
					"&power_states=POWERED_OFF"
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(t, validPlan)
			runtime := server.Runtime()
			client := newClient(t, server, 1, nil)

			request := defaultRequest()
			if test.names != nil {
				request.Names = test.names(runtime)
			}
			request.PowerStates = test.powerStates
			if _, err := client.Sweep(context.Background(), request); err != nil {
				t.Fatalf("Sweep: %v", err)
			}

			requests := server.Requests()
			var list contractmock.Request
			found := false
			for _, candidate := range requests {
				if candidate.OperationID == contractmock.VMList {
					list = candidate
					found = true
					break
				}
			}
			if !found {
				t.Fatalf("Vcenter.VM_list was never called")
			}
			want := test.wantQuery(runtime)
			if list.RawQuery != want {
				t.Fatalf("RawQuery = %q, want %q", list.RawQuery, want)
			}
			if list.ForceQuery {
				t.Fatalf("the request carried a dangling query delimiter")
			}
			if list.EscapedPath != "/api/vcenter/vm" {
				t.Fatalf("EscapedPath = %q, want /api/vcenter/vm", list.EscapedPath)
			}
			if list.Status != http.StatusOK {
				t.Fatalf("Vcenter.VM_list status = %d; the fixture rejected the query",
					list.Status)
			}
			assertNoRequestBody(t, list)
		})
	}
}

func TestLoginAndSessionHeaderWireShape(t *testing.T) {
	server := newServer(t, validPlan)
	runtime := server.Runtime()
	client := newClient(t, server, 1, nil)

	if _, err := client.Sweep(context.Background(), defaultRequest()); err != nil {
		t.Fatalf("Sweep: %v", err)
	}
	requests := server.Requests()
	if len(requests) == 0 {
		t.Fatalf("no requests were made")
	}

	login := requests[0]
	if login.OperationID != contractmock.SessionCreate {
		t.Fatalf("first request = %q, want Cis.Session_create", login.OperationID)
	}
	if login.Method != http.MethodPost || login.EscapedPath != "/api/session" {
		t.Fatalf("login target = %s %s, want POST /api/session",
			login.Method, login.EscapedPath)
	}
	if login.RawQuery != "" || login.ForceQuery {
		t.Fatalf("login carried a query: %q", login.RawQuery)
	}
	assertNoRequestBody(t, login)
	if got := login.Header.Get("Authorization"); got != server.BasicAuthorization() {
		t.Fatalf("login Authorization header is not the contract's basic_auth value")
	}
	if login.Header.Get(contractmock.SessionHeader) != "" {
		t.Fatalf("login must not send the api_key_auth header")
	}
	if got := login.Header.Get("Accept"); got != "application/json" {
		t.Fatalf("login Accept = %q", got)
	}

	token := server.IssuedTokens()[0]
	for _, request := range requests[1:] {
		if request.Header.Get(contractmock.SessionHeader) != token {
			t.Fatalf("%s did not carry the session token", request.OperationID)
		}
		if request.Header.Get("Authorization") != "" {
			t.Fatalf("%s must not send basic_auth credentials", request.OperationID)
		}
		if got := request.Header.Get("Accept"); got != "application/json" {
			t.Fatalf("%s Accept = %q", request.OperationID, got)
		}
		if request.Method == http.MethodGet {
			assertNoRequestBody(t, request)
		}
		if len(request.TransferEncoding) != 0 {
			t.Fatalf("%s used transfer encoding %v",
				request.OperationID, request.TransferEncoding)
		}
	}

	// Cpu_get and Cpu_update address the machine as one escaped path segment.
	wantPath := "/api/vcenter/vm/" + url.PathEscape(runtime.AlphaID) + "/hardware/cpu"
	for _, request := range requests {
		switch request.OperationID {
		case contractmock.CPUGet, contractmock.CPUUpdate:
			if !strings.HasPrefix(request.EscapedPath, "/api/vcenter/vm/") ||
				!strings.HasSuffix(request.EscapedPath, "/hardware/cpu") {
				t.Fatalf("CPU target = %q", request.EscapedPath)
			}
			if request.RawQuery != "" || request.ForceQuery {
				t.Fatalf("CPU request carried a query: %q", request.RawQuery)
			}
		}
	}
	if requests[1].OperationID != contractmock.VMList {
		t.Fatalf("second request = %q, want Vcenter.VM_list", requests[1].OperationID)
	}
	if requests[2].EscapedPath != wantPath {
		t.Fatalf("first Cpu_get path = %q, want %q", requests[2].EscapedPath, wantPath)
	}

	// The vm parameter is an opaque string and must be escaped as exactly one
	// path segment.
	escapedTarget := "/api/vcenter/vm/" +
		url.PathEscape(runtime.CharlieID) + "/hardware/cpu"
	if !strings.Contains(escapedTarget, "%2F") {
		t.Fatalf("the fixture identifier no longer requires escaping")
	}
	found := false
	for _, request := range requests {
		if request.OperationID != contractmock.CPUGet ||
			request.EscapedPath != escapedTarget {
			continue
		}
		found = true
		if request.Status != http.StatusOK {
			t.Fatalf("escaped Cpu_get status = %d, want 200", request.Status)
		}
	}
	if !found {
		t.Fatalf(
			"no Cpu_get addressed %q as a single escaped path segment",
			runtime.CharlieID,
		)
	}
}

func TestSessionRefreshGatesTableDriven(t *testing.T) {
	errHook := errors.New("refresh refused by the operator")

	tests := []struct {
		name          string
		maxReauth     int
		tokenBudgets  []int
		hook          cs.ReauthFunc
		wantKind      string
		wantSentinel  error
		wantLogins    int
		wantReauths   int
		wantApplied   int
		wantOutcomes  int
		wantLastIsFor string
	}{
		{
			name:         "budget exhausted",
			maxReauth:    1,
			tokenBudgets: []int{2, 2},
			wantKind:     "reauth-limit",
			wantLogins:   2,
			wantReauths:  1,
			wantApplied:  1,
			wantOutcomes: 1,
		},
		{
			name:         "hook stops the sweep",
			maxReauth:    2,
			tokenBudgets: []int{2},
			hook: func(context.Context, string, int) error {
				return errHook
			},
			wantSentinel: errHook,
			wantLogins:   1,
			wantReauths:  0,
			wantApplied:  0,
			wantOutcomes: 0,
		},
		{
			name:         "one replay only",
			maxReauth:    3,
			tokenBudgets: []int{2, 0},
			wantKind:     "api",
			wantLogins:   2,
			wantReauths:  1,
			wantApplied:  0,
			wantOutcomes: 0,
		},
		{
			name:         "two refreshes complete the sweep",
			maxReauth:    2,
			tokenBudgets: []int{2, 3},
			wantKind:     "",
			wantLogins:   3,
			wantReauths:  2,
			wantApplied:  3,
			wantOutcomes: 4,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			budgets := test.tokenBudgets
			server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
				plan := validPlan(runtime)
				plan.TokenBudgets = budgets
				return plan
			})
			client := newClient(t, server, test.maxReauth, test.hook)

			result, err := client.Sweep(context.Background(), defaultRequest())
			if test.wantSentinel != nil {
				if !errors.Is(err, test.wantSentinel) {
					t.Fatalf("error = %v, want %v", err, test.wantSentinel)
				}
			} else {
				assertErrorKind(t, err, test.wantKind)
			}
			if test.wantKind == "reauth-limit" {
				var limitErr *cs.ReauthLimitError
				if !errors.As(err, &limitErr) ||
					limitErr.OperationID != cs.OpCPUUpdate ||
					limitErr.Limit != test.maxReauth {
					t.Fatalf("ReauthLimitError did not preserve its fields: %+v", limitErr)
				}
			}
			if len(server.IssuedTokens()) != test.wantLogins {
				t.Fatalf("session creates = %d, want %d",
					len(server.IssuedTokens()), test.wantLogins)
			}
			if len(server.Applied()) != test.wantApplied {
				t.Fatalf("applied updates = %d, want %d",
					len(server.Applied()), test.wantApplied)
			}
			if len(result.Outcomes) != test.wantOutcomes {
				t.Fatalf("partial outcomes = %d, want %d",
					len(result.Outcomes), test.wantOutcomes)
			}
			if result.Reauths != test.wantReauths {
				t.Fatalf("Reauths = %d, want %d", result.Reauths, test.wantReauths)
			}

			// Traffic stops at the failure; nothing is attempted afterwards.
			requests := server.Requests()
			if err != nil {
				last := requests[len(requests)-1]
				switch test.wantKind {
				case "reauth-limit":
					if last.Status != http.StatusUnauthorized {
						t.Fatalf("last request status = %d, want 401", last.Status)
					}
				case "api":
					if last.Status != http.StatusUnauthorized ||
						last.OperationID != contractmock.CPUUpdate {
						t.Fatalf("last request = %s/%d",
							last.OperationID, last.Status)
					}
				}
				if test.wantSentinel != nil &&
					last.OperationID == contractmock.SessionCreate {
					t.Fatalf("a session refresh ran after the hook refused it")
				}
			}
		})
	}
}

func TestValidationIsLocalAndTableDriven(t *testing.T) {
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()

	tests := []struct {
		name    string
		ctx     context.Context
		request cs.SweepRequest
	}{
		{
			name:    "nil context",
			request: defaultRequest(),
		},
		{
			name:    "cancelled context",
			ctx:     cancelled,
			request: defaultRequest(),
		},
		{
			name:    "no managed member",
			ctx:     context.Background(),
			request: cs.SweepRequest{},
		},
		{
			name: "non positive count",
			ctx:  context.Background(),
			request: cs.SweepRequest{
				Desired: cs.Desired{Count: int64Pointer(0)},
			},
		},
		{
			name: "non positive cores per socket",
			ctx:  context.Background(),
			request: cs.SweepRequest{
				Desired: cs.Desired{CoresPerSocket: int64Pointer(-1)},
			},
		},
		{
			name: "count is not a multiple of cores per socket",
			ctx:  context.Background(),
			request: cs.SweepRequest{
				Desired: cs.Desired{
					Count:          int64Pointer(5),
					CoresPerSocket: int64Pointer(2),
				},
			},
		},
		{
			name: "blank name filter",
			ctx:  context.Background(),
			request: cs.SweepRequest{
				Names:   []string{""},
				Desired: cs.Desired{Count: int64Pointer(4)},
			},
		},
		{
			name: "padded name filter",
			ctx:  context.Background(),
			request: cs.SweepRequest{
				Names:   []string{" alpha"},
				Desired: cs.Desired{Count: int64Pointer(4)},
			},
		},
		{
			name: "duplicate name filter",
			ctx:  context.Background(),
			request: cs.SweepRequest{
				Names:   []string{"alpha", "alpha"},
				Desired: cs.Desired{Count: int64Pointer(4)},
			},
		},
		{
			name: "unsupported power state",
			ctx:  context.Background(),
			request: cs.SweepRequest{
				PowerStates: []string{"POWERED_DOWN"},
				Desired:     cs.Desired{Count: int64Pointer(4)},
			},
		},
		{
			name: "blank power state",
			ctx:  context.Background(),
			request: cs.SweepRequest{
				PowerStates: []string{""},
				Desired:     cs.Desired{Count: int64Pointer(4)},
			},
		},
		{
			name: "padded power state",
			ctx:  context.Background(),
			request: cs.SweepRequest{
				PowerStates: []string{" POWERED_ON"},
				Desired:     cs.Desired{Count: int64Pointer(4)},
			},
		},
		{
			name: "duplicate power state",
			ctx:  context.Background(),
			request: cs.SweepRequest{
				PowerStates: []string{"POWERED_ON", "POWERED_ON"},
				Desired:     cs.Desired{Count: int64Pointer(4)},
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(t, validPlan)
			client := newClient(t, server, 1, nil)
			//nolint:staticcheck // a nil context is part of the tested contract.
			_, err := client.Sweep(test.ctx, test.request)
			if err == nil {
				t.Fatalf("expected a local validation error")
			}
			if len(server.Requests()) != 0 {
				t.Fatalf("invalid input reached the network: %d requests",
					len(server.Requests()))
			}
		})
	}
}

func TestNewClientRejectsInvalidConfigTableDriven(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*cs.Config)
	}{
		{"empty base url", func(c *cs.Config) { c.BaseURL = "" }},
		{"non http scheme", func(c *cs.Config) { c.BaseURL = "ftp://vcenter.example" }},
		{"embedded credentials", func(c *cs.Config) {
			c.BaseURL = "https://user:pass@vcenter.example"
		}},
		{"path in base url", func(c *cs.Config) {
			c.BaseURL = "https://vcenter.example/api"
		}},
		{"query in base url", func(c *cs.Config) {
			c.BaseURL = "https://vcenter.example?x=1"
		}},
		{"fragment in base url", func(c *cs.Config) {
			c.BaseURL = "https://vcenter.example#top"
		}},
		{"empty fragment in base url", func(c *cs.Config) {
			c.BaseURL = "https://vcenter.example#"
		}},
		{"blank username", func(c *cs.Config) { c.Username = "   " }},
		{"username with whitespace", func(c *cs.Config) { c.Username = "a b" }},
		{"empty password", func(c *cs.Config) { c.Password = "" }},
		{"password with whitespace", func(c *cs.Config) { c.Password = "p\tq" }},
		{"zero reauth budget", func(c *cs.Config) { c.MaxReauth = 0 }},
		{"negative reauth budget", func(c *cs.Config) { c.MaxReauth = -1 }},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			config := cs.Config{
				BaseURL:   "https://vcenter.example",
				Username:  "administrator@vsphere.local",
				Password:  "correct-horse",
				MaxReauth: 1,
			}
			test.mutate(&config)
			if _, err := cs.NewClient(config); err == nil {
				t.Fatalf("NewClient accepted an invalid configuration")
			}
		})
	}
}

func TestNewClientDefaultsAreUsableAndConstructionIsLocal(t *testing.T) {
	server := newServer(t, validPlan)
	runtime := server.Runtime()
	client, err := cs.NewClient(cs.Config{
		BaseURL:   server.URL(),
		Username:  runtime.Username,
		Password:  runtime.Password,
		MaxReauth: 1,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if requests := server.Requests(); len(requests) != 0 {
		t.Fatalf("NewClient performed %d requests", len(requests))
	}
	if _, err := client.Sweep(context.Background(), defaultRequest()); err != nil {
		t.Fatalf("Sweep with default HTTP client and callback: %v", err)
	}
}

func TestStructuredErrorsArePreservedAndRedacted(t *testing.T) {
	t.Run("api error", func(t *testing.T) {
		server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
			plan := validPlan(runtime)
			plan.CPUGetStatus = map[string]int{runtime.AlphaID: http.StatusInternalServerError}
			return plan
		})
		client := newClient(t, server, 1, nil)

		_, err := client.Sweep(context.Background(), defaultRequest())
		var apiErr *cs.APIError
		if !errors.As(err, &apiErr) {
			t.Fatalf("error = %T, want *APIError", err)
		}
		if apiErr.OperationID != cs.OpCPUGet ||
			apiErr.Status != http.StatusInternalServerError ||
			apiErr.ErrorType != "ERROR" ||
			!reflect.DeepEqual(apiErr.Messages, []string{
				"the fixture rejected Cpu_get",
			}) {
			t.Fatalf("APIError did not preserve the structured response: %+v", apiErr)
		}
		assertRedacted(t, server, err, apiErr.Messages[0])
	})

	// The contract declares 204 for Cpu_update; any other status is a failure.
	t.Run("update must answer exactly 204", func(t *testing.T) {
		server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
			plan := validPlan(runtime)
			plan.CPUUpdateStatus = map[string]int{runtime.AlphaID: http.StatusOK}
			return plan
		})
		client := newClient(t, server, 1, nil)

		_, err := client.Sweep(context.Background(), defaultRequest())
		var apiErr *cs.APIError
		if !errors.As(err, &apiErr) {
			t.Fatalf("error = %T (%v), want *APIError", err, err)
		}
		if apiErr.OperationID != cs.OpCPUUpdate || apiErr.Status != http.StatusOK {
			t.Fatalf("APIError = %+v, want Cpu_update / 200", apiErr)
		}
		if len(server.Applied()) != 0 {
			t.Fatalf("a rejected update was committed")
		}
	})

	statusTests := []struct {
		name        string
		operationID string
		status      int
		build       func(contractmock.RuntimeValues, *contractmock.Plan)
	}{
		{
			name:        "session create must answer exactly 201",
			operationID: cs.OpSessionCreate,
			status:      http.StatusOK,
			build: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.SessionStatuses = []int{http.StatusOK}
			},
		},
		{
			name:        "vm list must answer exactly 200",
			operationID: cs.OpVMList,
			status:      http.StatusCreated,
			build: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.ListStatus = http.StatusCreated
			},
		},
		{
			name:        "cpu get must answer exactly 200",
			operationID: cs.OpCPUGet,
			status:      http.StatusCreated,
			build: func(runtime contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.CPUGetStatus = map[string]int{
					runtime.AlphaID: http.StatusCreated,
				}
			},
		},
	}
	for _, test := range statusTests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
				plan := validPlan(runtime)
				test.build(runtime, &plan)
				return plan
			})
			client := newClient(t, server, 1, nil)

			_, err := client.Sweep(context.Background(), defaultRequest())
			var apiErr *cs.APIError
			if !errors.As(err, &apiErr) ||
				apiErr.OperationID != test.operationID ||
				apiErr.Status != test.status {
				t.Fatalf("error = %T (%+v), want %s / %d",
					err, apiErr, test.operationID, test.status)
			}
		})
	}

	t.Run("session-create 401 is never refreshed", func(t *testing.T) {
		server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
			plan := validPlan(runtime)
			plan.SessionStatuses = []int{http.StatusUnauthorized}
			return plan
		})
		hookCalls := 0
		client := newClient(t, server, 2, func(context.Context, string, int) error {
			hookCalls++
			return nil
		})

		result, err := client.Sweep(context.Background(), defaultRequest())
		var apiErr *cs.APIError
		if !errors.As(err, &apiErr) ||
			apiErr.OperationID != cs.OpSessionCreate ||
			apiErr.Status != http.StatusUnauthorized {
			t.Fatalf("error = %T (%+v), want session-create APIError / 401", err, apiErr)
		}
		if hookCalls != 0 || result.Reauths != 0 || len(server.Requests()) != 1 {
			t.Fatalf("login 401 triggered more traffic: hook=%d result=%+v requests=%d",
				hookCalls, result, len(server.Requests()))
		}
	})

	t.Run("redirects are not followed", func(t *testing.T) {
		server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
			plan := validPlan(runtime)
			plan.SessionStatuses = []int{http.StatusFound}
			plan.SessionRedirectLocation = "/api/session"
			return plan
		})
		client := newClient(t, server, 1, nil)

		_, err := client.Sweep(context.Background(), defaultRequest())
		var apiErr *cs.APIError
		if !errors.As(err, &apiErr) ||
			apiErr.OperationID != cs.OpSessionCreate ||
			apiErr.Status != http.StatusFound {
			t.Fatalf("error = %T (%+v), want session-create APIError / 302", err, apiErr)
		}
		if requests := server.Requests(); len(requests) != 1 {
			t.Fatalf("redirect caused %d requests, want 1", len(requests))
		}
	})

	t.Run("unauthenticated challenge is preserved but not printed", func(t *testing.T) {
		server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
			plan := validPlan(runtime)
			plan.TokenBudgets = []int{2, 0}
			return plan
		})
		client := newClient(t, server, 2, nil)

		_, err := client.Sweep(context.Background(), defaultRequest())
		var apiErr *cs.APIError
		if !errors.As(err, &apiErr) {
			t.Fatalf("error = %T, want *APIError", err)
		}
		if apiErr.Status != http.StatusUnauthorized ||
			apiErr.ErrorType != "UNAUTHENTICATED" ||
			apiErr.Challenge == "" {
			t.Fatalf("APIError did not preserve the challenge: %+v", apiErr)
		}
		assertRedacted(t, server, err, apiErr.Challenge)
	})

	protocolTests := []struct {
		name  string
		build func(contractmock.RuntimeValues, *contractmock.Plan)
	}{
		{
			name: "session token is not a JSON string",
			build: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.SessionBody = `{"value":"token"}`
			},
		},
		{
			name: "session token is blank",
			build: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.SessionBody = `"   "`
			},
		},
		{
			name: "session token contains Unicode whitespace",
			build: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.SessionBody = `"token\u2003value"`
			},
		},
		{
			name: "session response carries trailing data",
			build: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.SessionBody = `"first" "second"`
			},
		},
		{
			name: "list response is not an array",
			build: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.ListBody = `{"value":[]}`
			},
		},
		{
			name: "list summary contains a blank required member",
			build: func(runtime contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.ListBody = fmt.Sprintf(
					`[{"vm":"%s","name":" ","power_state":"POWERED_OFF"}]`,
					runtime.AlphaID,
				)
			},
		},
		{
			name: "oversized list response is otherwise valid JSON",
			build: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.ListBody = "[]" + strings.Repeat(" ", (1<<20)+1)
			},
		},
		{
			name: "session success has the wrong media type",
			build: func(runtime contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.SessionBody = fmt.Sprintf("%q", runtime.Tokens[0])
				plan.SessionContentType = "text/plain"
			},
		},
		{
			name: "list success has the wrong media type",
			build: func(_ contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.ListBody = "[]"
				plan.ListContentType = "text/plain"
			},
		},
		{
			name: "cpu info success has the wrong media type",
			build: func(runtime contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.CPUGetBody = map[string]string{
					runtime.AlphaID: `{"count":2,"cores_per_socket":1,` +
						`"hot_add_enabled":false,"hot_remove_enabled":false}`,
				}
				plan.CPUGetContentType = map[string]string{
					runtime.AlphaID: "text/plain",
				}
			},
		},
		{
			name: "cpu info response carries trailing data",
			build: func(runtime contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.CPUGetBody = map[string]string{
					runtime.AlphaID: `{"count":2,"cores_per_socket":1,` +
						`"hot_add_enabled":false,"hot_remove_enabled":false} true`,
				}
			},
		},
		{
			name: "cpu info omits a required member",
			build: func(runtime contractmock.RuntimeValues, plan *contractmock.Plan) {
				plan.CPUGetBody = map[string]string{
					runtime.AlphaID: `{"count":2,"cores_per_socket":1,"hot_add_enabled":false}`,
				}
			},
		},
	}
	for _, test := range protocolTests {
		t.Run("protocol: "+test.name, func(t *testing.T) {
			build := test.build
			server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
				plan := validPlan(runtime)
				build(runtime, &plan)
				return plan
			})
			client := newClient(t, server, 1, nil)

			_, err := client.Sweep(context.Background(), defaultRequest())
			var protocolErr *cs.ProtocolError
			if !errors.As(err, &protocolErr) {
				t.Fatalf("error = %T (%v), want *ProtocolError", err, err)
			}
			assertRedacted(t, server, err)
		})
	}

	t.Run("protocol: 204 response carries a body", func(t *testing.T) {
		server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
			return contractmock.Plan{VMs: []contractmock.VM{{
				ID:         runtime.AlphaID,
				Name:       runtime.AlphaName,
				PowerState: "POWERED_OFF",
				CPU: contractmock.CPUInfo{
					Count:          2,
					CoresPerSocket: 1,
				},
			}}}
		})
		baseClient := server.Client()
		transport := baseClient.Transport
		if transport == nil {
			transport = http.DefaultTransport
		}
		alteredClient := *baseClient
		alteredClient.Transport = roundTripFunc(func(
			request *http.Request,
		) (*http.Response, error) {
			response, err := transport.RoundTrip(request)
			if err == nil && request.Method == http.MethodPatch &&
				response.StatusCode == http.StatusNoContent {
				_ = response.Body.Close()
				response.Body = io.NopCloser(strings.NewReader("unexpected"))
				response.ContentLength = int64(len("unexpected"))
			}
			return response, err
		})
		runtime := server.Runtime()
		client, err := cs.NewClient(cs.Config{
			BaseURL:    server.URL(),
			Username:   runtime.Username,
			Password:   runtime.Password,
			HTTPClient: &alteredClient,
			MaxReauth:  1,
		})
		if err != nil {
			t.Fatalf("NewClient: %v", err)
		}

		_, err = client.Sweep(context.Background(), defaultRequest())
		var protocolErr *cs.ProtocolError
		if !errors.As(err, &protocolErr) || protocolErr.OperationID != cs.OpCPUUpdate {
			t.Fatalf("error = %T (%v), want Cpu_update ProtocolError", err, err)
		}
	})

	t.Run("transport error", func(t *testing.T) {
		server := newServer(t, validPlan)
		client := newClient(t, server, 1, nil)
		server.Close()

		_, err := client.Sweep(context.Background(), defaultRequest())
		var transportErr *cs.TransportError
		if !errors.As(err, &transportErr) {
			t.Fatalf("error = %T, want *TransportError", err)
		}
		if transportErr.Unwrap() == nil {
			t.Fatalf("TransportError did not preserve the underlying failure")
		}
		if strings.Contains(err.Error(), transportErr.Unwrap().Error()) {
			t.Fatalf("TransportError leaked the transport text: %v", err)
		}
	})

	t.Run("cancellation propagates", func(t *testing.T) {
		server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
			plan := validPlan(runtime)
			plan.TokenBudgets = []int{2}
			return plan
		})
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		client := newClient(t, server, 2, func(
			hookCtx context.Context,
			_ string,
			_ int,
		) error {
			cancel()
			return hookCtx.Err()
		})

		_, err := client.Sweep(ctx, defaultRequest())
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("error = %v, want context.Canceled", err)
		}
	})
}

func TestConcurrentSweepsShareNoSessionState(t *testing.T) {
	server := newServer(t, validPlan)
	runtime := server.Runtime()
	client := newClient(t, server, 1, nil)

	var wait sync.WaitGroup
	errs := make([]error, 2)
	targets := []string{runtime.AlphaName, runtime.BravoName}
	for index, name := range targets {
		wait.Add(1)
		go func(index int, name string) {
			defer wait.Done()
			request := defaultRequest()
			request.Names = []string{name}
			_, err := client.Sweep(context.Background(), request)
			errs[index] = err
		}(index, name)
	}
	wait.Wait()

	for index, err := range errs {
		if err != nil {
			t.Fatalf("concurrent sweep %d: %v", index, err)
		}
	}
	applied := server.Applied()
	if len(applied) != 2 {
		t.Fatalf("applied updates = %d, want 2", len(applied))
	}
	seen := map[string]bool{}
	for _, update := range applied {
		if string(update.Body) != updateBody {
			t.Fatalf("applied body = %q, want %q", update.Body, updateBody)
		}
		seen[update.VM] = true
	}
	if !seen[runtime.AlphaID] || !seen[runtime.BravoID] {
		t.Fatalf("concurrent sweeps did not update their own targets: %v", seen)
	}
}

func TestOnlyContractOperationsAreCalled(t *testing.T) {
	server := newServer(t, func(runtime contractmock.RuntimeValues) contractmock.Plan {
		plan := validPlan(runtime)
		plan.TokenBudgets = []int{3}
		return plan
	})
	client := newClient(t, server, 1, nil)

	if _, err := client.Sweep(context.Background(), defaultRequest()); err != nil {
		t.Fatalf("Sweep: %v", err)
	}
	allowed := map[string]bool{
		contractmock.SessionCreate: true,
		contractmock.VMList:        true,
		contractmock.CPUGet:        true,
		contractmock.CPUUpdate:     true,
	}
	for index, request := range server.Requests() {
		if !allowed[request.OperationID] {
			t.Fatalf("request %d targeted %q %s, which the contract does not name",
				index, request.Method, request.EscapedPath)
		}
		if request.Status == http.StatusNotFound {
			t.Fatalf("request %d was not served by the pinned contract: %s",
				index, request.EscapedPath)
		}
	}
}

func defaultRequest() cs.SweepRequest {
	return cs.SweepRequest{
		Desired: cs.Desired{
			Count:          int64Pointer(4),
			CoresPerSocket: int64Pointer(2),
		},
	}
}

func validPlan(runtime contractmock.RuntimeValues) contractmock.Plan {
	return contractmock.Plan{
		VMs: []contractmock.VM{
			{
				ID:         runtime.AlphaID,
				Name:       runtime.AlphaName,
				PowerState: "POWERED_OFF",
				CPU: contractmock.CPUInfo{
					Count:          2,
					CoresPerSocket: 1,
				},
			},
			{
				ID:         runtime.BravoID,
				Name:       runtime.BravoName,
				PowerState: "POWERED_ON",
				CPU: contractmock.CPUInfo{
					Count:          2,
					CoresPerSocket: 1,
					HotAddEnabled:  true,
				},
			},
			{
				ID:         runtime.CharlieID,
				Name:       runtime.FilterName,
				PowerState: "POWERED_OFF",
				CPU: contractmock.CPUInfo{
					Count:          4,
					CoresPerSocket: 2,
				},
			},
			{
				ID:         runtime.DeltaID,
				Name:       runtime.DeltaName,
				PowerState: "POWERED_ON",
				CPU: contractmock.CPUInfo{
					Count:            8,
					CoresPerSocket:   4,
					HotAddEnabled:    true,
					HotRemoveEnabled: true,
				},
			},
		},
	}
}

func newServer(
	t *testing.T,
	planFactory func(contractmock.RuntimeValues) contractmock.Plan,
) *contractmock.Server {
	t.Helper()
	server, err := contractmock.New("docs/contract.json", planFactory)
	if err != nil {
		t.Fatalf("start contract mock: %v", err)
	}
	t.Cleanup(server.Close)
	return server
}

func newClient(
	t *testing.T,
	server *contractmock.Server,
	maxReauth int,
	onReauth cs.ReauthFunc,
) *cs.Client {
	t.Helper()
	runtime := server.Runtime()
	client, err := cs.NewClient(cs.Config{
		BaseURL:    server.URL(),
		Username:   runtime.Username,
		Password:   runtime.Password,
		HTTPClient: server.Client(),
		MaxReauth:  maxReauth,
		OnReauth:   onReauth,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func operationSequence(requests []contractmock.Request) []string {
	out := make([]string, 0, len(requests))
	for _, request := range requests {
		out = append(out, request.OperationID)
	}
	return out
}

func assertNoRequestBody(t *testing.T, request contractmock.Request) {
	t.Helper()
	if len(request.Body) != 0 {
		t.Fatalf("%s carried a body: %q", request.OperationID, request.Body)
	}
	if request.ContentLength > 0 {
		t.Fatalf("%s declared Content-Length %d",
			request.OperationID, request.ContentLength)
	}
	if request.Header.Get("Content-Type") != "" {
		t.Fatalf("%s declared a Content-Type without a body", request.OperationID)
	}
	if len(request.TransferEncoding) != 0 {
		t.Fatalf("%s used transfer encoding %v",
			request.OperationID, request.TransferEncoding)
	}
}

// assertRedacted requires that no secret or server-supplied prose reaches the
// error string.
func assertRedacted(
	t *testing.T,
	server *contractmock.Server,
	err error,
	extras ...string,
) {
	t.Helper()
	runtime := server.Runtime()
	secrets := append([]string{runtime.Password}, runtime.Tokens...)
	secrets = append(secrets, extras...)
	text := err.Error()
	for _, secret := range secrets {
		if secret == "" {
			continue
		}
		if strings.Contains(text, secret) {
			t.Fatalf("error string leaked %q: %s", secret, text)
		}
	}
}

func assertErrorKind(t *testing.T, err error, kind string) {
	t.Helper()
	if kind == "" {
		if err != nil {
			t.Fatalf("unexpected error %T: %v", err, err)
		}
		return
	}
	if err == nil {
		t.Fatalf("expected %s error, got nil", kind)
	}
	switch kind {
	case "api":
		var target *cs.APIError
		if !errors.As(err, &target) {
			t.Fatalf("error = %T (%v), want *APIError", err, err)
		}
	case "protocol":
		var target *cs.ProtocolError
		if !errors.As(err, &target) {
			t.Fatalf("error = %T (%v), want *ProtocolError", err, err)
		}
	case "reauth-limit":
		var target *cs.ReauthLimitError
		if !errors.As(err, &target) {
			t.Fatalf("error = %T (%v), want *ReauthLimitError", err, err)
		}
	default:
		t.Fatalf("unknown test error kind %q", kind)
	}
}

func equalStrings(got []string, want []string) bool {
	if len(got) != len(want) {
		return false
	}
	for index := range got {
		if got[index] != want[index] {
			return false
		}
	}
	return true
}

func containsString(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}

func int64Pointer(value int64) *int64 {
	return &value
}

func boolPointer(value bool) *bool {
	return &value
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

func assertFileHash(t *testing.T, path string, expected string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	sum := sha256.Sum256(data)
	got := hex.EncodeToString(sum[:])
	if got != expected {
		t.Fatalf("%s sha256 = %s, want %s", path, got, expected)
	}
}
