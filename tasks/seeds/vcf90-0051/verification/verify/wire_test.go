// Package verify holds the protected verification for the vmreconfig change
// set. It drives the client against the loopback appliance in internal/vcmock
// and asserts the exact shape of every request that reached the wire, the
// provenance of the contract those requests were derived from, and the accuracy
// of the report a partially applied change set produces.
//
// No live VMware endpoint is contacted.
package verify

import (
	"context"
	"encoding/json"
	"errors"
	"go/ast"
	"go/parser"
	"go/token"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"vcf.local/vcenterchange/internal/contract"
	"vcf.local/vcenterchange/internal/vcmock"
	"vcf.local/vcenterchange/vmreconfig"
)

func TestVMReconfigPackageIncludesTests(t *testing.T) {
	paths, err := filepath.Glob("../vmreconfig/*_test.go")
	if err != nil {
		t.Fatalf("find vmreconfig tests: %v", err)
	}
	for _, path := range paths {
		file, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", path, err)
		}
		for _, declaration := range file.Decls {
			fn, ok := declaration.(*ast.FuncDecl)
			if ok && fn.Recv == nil && strings.HasPrefix(fn.Name.Name, "Test") {
				return
			}
		}
	}
	t.Fatal("vmreconfig has no package tests; add at least one Test function in vmreconfig/*_test.go")
}

// The 9.0.0.0 revision of the vSphere Automation API specification for vCenter,
// in VMware's Apache-2.0 vcf-api-specs repository.
const (
	wantTag      = "9.0.0.0"
	wantSha      = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
	wantSpecPath = "specifications/vsphere/openapi/automation/vcenter.yaml"
	// The 9.1.0.0 revision of the same file. The contract must not be pinned to it.
	otherRevisionSha = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
)

// wantOperations is every operationId this change set is allowed to use, with
// the method, path and fixed query the specification gives it.
var wantOperations = []struct {
	id      string
	method  string
	path    string
	query   map[string]string
	success int
}{
	{"Vcenter.Vm.Power_get", "GET", "/vcenter/vm/{vm}/power", map[string]string{}, 200},
	{"Vcenter.Vm.Power_stop", "POST", "/vcenter/vm/{vm}/power", map[string]string{"action": "stop"}, 204},
	{"Vcenter.Vm.Hardware.Memory_update", "PATCH", "/vcenter/vm/{vm}/hardware/memory", map[string]string{}, 204},
	{"Vcenter.Vm.Hardware.Cpu_update", "PATCH", "/vcenter/vm/{vm}/hardware/cpu", map[string]string{}, 204},
	{"Vcenter.Vm.Hardware.Disk_create", "POST", "/vcenter/vm/{vm}/hardware/disk", map[string]string{}, 201},
	{"Vcenter.Vm.Power_start", "POST", "/vcenter/vm/{vm}/power", map[string]string{"action": "start"}, 204},
}

// ---------------------------------------------------------------------------
// provenance
// ---------------------------------------------------------------------------

func TestContractProvenance(t *testing.T) {
	raw, err := os.ReadFile("../docs/official_sources.json")
	if err != nil {
		t.Fatalf("read docs/official_sources.json: %v", err)
	}
	var src struct {
		Repository       string `json:"repository"`
		RepositoryTag    string `json:"repository_tag"`
		RepositoryCommit string `json:"repository_commit_sha"`
		SpecPath         string `json:"spec_path"`
		SpecInfoVersion  string `json:"spec_info_version"`
		License          string `json:"license"`
		DerivedArtifact  string `json:"derived_artifact"`
		Operations       []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
			SpecPath    string `json:"spec_path"`
			CommitSha   string `json:"repository_commit_sha"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(raw, &src); err != nil {
		t.Fatalf("parse docs/official_sources.json: %v", err)
	}

	if !strings.Contains(src.Repository, "vmware/vcf-api-specs") {
		t.Errorf("repository = %q, want the vmware/vcf-api-specs repository", src.Repository)
	}
	if src.RepositoryTag != wantTag {
		t.Errorf("repository_tag = %q, want %q", src.RepositoryTag, wantTag)
	}
	if src.RepositoryCommit != wantSha {
		t.Errorf("repository_commit_sha = %q, want the %s tag commit %q", src.RepositoryCommit, wantTag, wantSha)
	}
	if src.RepositoryCommit == otherRevisionSha {
		t.Errorf("repository_commit_sha is the 9.1.0.0 revision; the contract must be pinned to %s", wantTag)
	}
	if src.SpecPath != wantSpecPath {
		t.Errorf("spec_path = %q, want %q", src.SpecPath, wantSpecPath)
	}
	if src.SpecInfoVersion != wantTag {
		t.Errorf("spec_info_version = %q, want %q", src.SpecInfoVersion, wantTag)
	}
	if src.License != "Apache-2.0" {
		t.Errorf("license = %q, want Apache-2.0", src.License)
	}
	if src.DerivedArtifact != "docs/contract.json" {
		t.Errorf("derived_artifact = %q, want docs/contract.json", src.DerivedArtifact)
	}

	// Every operationId the change set uses is recorded, each carrying the spec
	// path and the commit it was read from.
	recorded := map[string]bool{}
	for _, op := range src.Operations {
		recorded[op.OperationID] = true
		if op.SpecPath != wantSpecPath {
			t.Errorf("operation %s: spec_path = %q, want %q", op.OperationID, op.SpecPath, wantSpecPath)
		}
		if op.CommitSha != wantSha {
			t.Errorf("operation %s: repository_commit_sha = %q, want %q", op.OperationID, op.CommitSha, wantSha)
		}
	}
	for _, want := range wantOperations {
		if !recorded[want.id] {
			t.Errorf("docs/official_sources.json does not record operationId %q", want.id)
		}
	}
}

func TestContractNamesExactlyTheSpecifiedOperations(t *testing.T) {
	c := loadContract(t)

	if c.API.RepositoryCommitSha != wantSha {
		t.Errorf("contract api.repositoryCommitSha = %q, want %q", c.API.RepositoryCommitSha, wantSha)
	}
	if c.API.Version != wantTag {
		t.Errorf("contract api.version = %q, want %q", c.API.Version, wantTag)
	}
	if c.API.SpecPath != wantSpecPath {
		t.Errorf("contract api.specPath = %q, want %q", c.API.SpecPath, wantSpecPath)
	}
	if c.API.BasePath != "/api" {
		t.Errorf("contract api.basePath = %q, want /api", c.API.BasePath)
	}
	if c.Authorization.HeaderName != "vmware-api-session-id" {
		t.Errorf("contract authorization.headerName = %q, want vmware-api-session-id", c.Authorization.HeaderName)
	}

	if got, want := len(c.Operations), len(wantOperations); got != want {
		t.Fatalf("contract names %d operations, want exactly %d", got, want)
	}
	for _, want := range wantOperations {
		op, ok := c.Operation(want.id)
		if !ok {
			t.Errorf("contract does not name operationId %q", want.id)
			continue
		}
		if op.Method != want.method {
			t.Errorf("%s: method = %q, want %q", want.id, op.Method, want.method)
		}
		if op.Path != want.path {
			t.Errorf("%s: path = %q, want %q", want.id, op.Path, want.path)
		}
		if !reflect.DeepEqual(op.Query, want.query) {
			t.Errorf("%s: query = %v, want %v", want.id, op.Query, want.query)
		}
		if op.SuccessStatus != want.success {
			t.Errorf("%s: successStatus = %d, want %d", want.id, op.SuccessStatus, want.success)
		}
	}

	// The property sets the omit-empty rule is stated against.
	for schema, want := range map[string][]string{
		"Vcenter.Vm.Hardware.Memory.UpdateSpec":   {"hot_add_enabled", "size_mib"},
		"Vcenter.Vm.Hardware.Cpu.UpdateSpec":      {"cores_per_socket", "count", "hot_add_enabled", "hot_remove_enabled"},
		"Vcenter.Vm.Hardware.Disk.VmdkCreateSpec": {"capacity", "name", "storage_policy"},
		"Vcenter.Vm.Hardware.ScsiAddressSpec":     {"bus", "unit"},
	} {
		s, ok := c.Schema(schema)
		if !ok {
			t.Errorf("contract has no schema %s", schema)
			continue
		}
		got := append([]string(nil), s.AllowedProperties...)
		sort.Strings(got)
		if !reflect.DeepEqual(got, want) {
			t.Errorf("%s allowed properties = %v, want %v", schema, got, want)
		}
	}
	// bus is the one required property in this contract; dropping it when zero
	// is the mistake the omit-empty rule has to survive.
	if s, ok := c.Schema("Vcenter.Vm.Hardware.ScsiAddressSpec"); ok {
		if !reflect.DeepEqual(s.RequiredProperties, []string{"bus"}) {
			t.Errorf("ScsiAddressSpec requiredProperties = %v, want [bus]", s.RequiredProperties)
		}
	}
}

// ---------------------------------------------------------------------------
// wire shape
// ---------------------------------------------------------------------------

func TestWireShapeOfAFullChangeSet(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{})
	client := newClient(t, s)

	report, err := client.Apply(context.Background(), vmreconfig.ChangeSet{
		VM:     "vm-101",
		Memory: &vmreconfig.MemoryChange{SizeMiB: i64(8192)},
		CPU:    &vmreconfig.CPUChange{Count: i64(4), CoresPerSocket: i64(2)},
		Disks: []vmreconfig.DiskChange{{
			Type:    "SCSI",
			SCSI:    &vmreconfig.SCSIAddress{Bus: 0},
			NewVMDK: &vmreconfig.NewVMDK{CapacityBytes: i64(42949672960)},
		}},
		RestorePowerState: true,
	})
	if err != nil {
		t.Fatalf("Apply: unexpected error: %v", err)
	}

	// The full sequence, including the closing read-back of the power state.
	wantSeq := []string{
		"Vcenter.Vm.Power_get",
		"Vcenter.Vm.Power_stop",
		"Vcenter.Vm.Hardware.Memory_update",
		"Vcenter.Vm.Hardware.Cpu_update",
		"Vcenter.Vm.Hardware.Disk_create",
		"Vcenter.Vm.Power_start",
		"Vcenter.Vm.Power_get",
	}
	assertSequence(t, s, wantSeq)

	reqs := s.Requests()
	for _, req := range reqs {
		assertCommonHeaders(t, req, s.SessionID)
	}

	// Vcenter.Vm.Power_get: a GET with no body, no Content-Type and no query.
	get := reqs[0]
	assertCommonHeaders(t, get, s.SessionID)
	if get.Method != http.MethodGet {
		t.Errorf("Power_get method = %s, want GET", get.Method)
	}
	if get.Path != "/api/vcenter/vm/vm-101/power" {
		t.Errorf("Power_get path = %q, want /api/vcenter/vm/vm-101/power", get.Path)
	}
	if get.RawQuery != "" {
		t.Errorf("Power_get carries query %q, want none", get.RawQuery)
	}
	if len(get.Body) != 0 {
		t.Errorf("Power_get carries a body %q, want none", get.Body)
	}
	if ct := get.Header.Get("Content-Type"); ct != "" {
		t.Errorf("Power_get sends Content-Type %q; a bodiless request must not", ct)
	}

	// Vcenter.Vm.Power_stop: the action query parameter, no body.
	stop := reqs[1]
	assertCommonHeaders(t, stop, s.SessionID)
	if stop.Method != http.MethodPost {
		t.Errorf("Power_stop method = %s, want POST", stop.Method)
	}
	if got := stop.Query.Get("action"); got != "stop" {
		t.Errorf("Power_stop action = %q, want stop", got)
	}
	if len(stop.Body) != 0 {
		t.Errorf("Power_stop carries a body %q, want none", stop.Body)
	}

	// Vcenter.Vm.Hardware.Memory_update: only the property the caller set.
	mem := reqs[2]
	assertCommonHeaders(t, mem, s.SessionID)
	if mem.Method != http.MethodPatch {
		t.Errorf("Memory_update method = %s, want PATCH", mem.Method)
	}
	if ct := mem.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		t.Errorf("Memory_update Content-Type = %q, want application/json", ct)
	}
	assertJSONObject(t, mem, map[string]any{"size_mib": float64(8192)})

	// Vcenter.Vm.Hardware.Cpu_update: hot_add_enabled and hot_remove_enabled
	// were never set, so they must be absent rather than present and false.
	cpu := reqs[3]
	assertJSONObject(t, cpu, map[string]any{"count": float64(4), "cores_per_socket": float64(2)})

	// Vcenter.Vm.Hardware.Disk_create: nested objects carry only what was set,
	// and the required bus survives being zero.
	disk := reqs[4]
	assertJSONObject(t, disk, map[string]any{
		"type":     "SCSI",
		"scsi":     map[string]any{"bus": float64(0)},
		"new_vmdk": map[string]any{"capacity": float64(42949672960)},
	})

	start := reqs[5]
	if got := start.Query.Get("action"); got != "start" {
		t.Errorf("Power_start action = %q, want start", got)
	}
	if len(start.Body) != 0 {
		t.Errorf("Power_start carries a body %q, want none", start.Body)
	}

	// The report describes what actually happened.
	if report.InitialPowerState != vcmock.PoweredOn {
		t.Errorf("InitialPowerState = %q, want POWERED_ON", report.InitialPowerState)
	}
	if report.FinalPowerState != vcmock.PoweredOn {
		t.Errorf("FinalPowerState = %q, want POWERED_ON", report.FinalPowerState)
	}
	if got, want := report.CreatedDiskIDs, []string{"2000"}; !reflect.DeepEqual(got, want) {
		t.Errorf("CreatedDiskIDs = %v, want %v", got, want)
	}
	assertStatuses(t, report, []vmreconfig.StepStatus{
		vmreconfig.StepApplied, // Power_stop
		vmreconfig.StepApplied, // Memory_update
		vmreconfig.StepApplied, // Cpu_update
		vmreconfig.StepApplied, // Disk_create
		vmreconfig.StepApplied, // Power_start
	})
}

// TestUnsetOptionalPropertiesAreOmitted is the wire rule stated on its own: a
// property the caller did not set is absent, and a property the caller did set
// is sent even when its value is the zero value of its type.
func TestUnsetOptionalPropertiesAreOmitted(t *testing.T) {
	tests := []struct {
		name      string
		change    vmreconfig.ChangeSet
		operation string
		wantBody  map[string]any
	}{
		{
			name: "memory size only",
			change: vmreconfig.ChangeSet{
				VM:     "vm-7",
				Memory: &vmreconfig.MemoryChange{SizeMiB: i64(4096)},
			},
			operation: "Vcenter.Vm.Hardware.Memory_update",
			wantBody:  map[string]any{"size_mib": float64(4096)},
		},
		{
			name: "memory hot add explicitly disabled",
			change: vmreconfig.ChangeSet{
				VM:     "vm-7",
				Memory: &vmreconfig.MemoryChange{SizeMiB: i64(4096), HotAddEnabled: b(false)},
			},
			operation: "Vcenter.Vm.Hardware.Memory_update",
			// An explicit false is a requested change and must reach the wire.
			wantBody: map[string]any{"size_mib": float64(4096), "hot_add_enabled": false},
		},
		{
			name: "memory hot add only",
			change: vmreconfig.ChangeSet{
				VM:     "vm-7",
				Memory: &vmreconfig.MemoryChange{HotAddEnabled: b(true)},
			},
			operation: "Vcenter.Vm.Hardware.Memory_update",
			wantBody:  map[string]any{"hot_add_enabled": true},
		},
		{
			name: "cpu count only",
			change: vmreconfig.ChangeSet{
				VM:  "vm-7",
				CPU: &vmreconfig.CPUChange{Count: i64(2)},
			},
			operation: "Vcenter.Vm.Hardware.Cpu_update",
			wantBody:  map[string]any{"count": float64(2)},
		},
		{
			name: "cpu hot remove explicitly disabled",
			change: vmreconfig.ChangeSet{
				VM:  "vm-7",
				CPU: &vmreconfig.CPUChange{Count: i64(2), HotRemoveEnabled: b(false)},
			},
			operation: "Vcenter.Vm.Hardware.Cpu_update",
			wantBody:  map[string]any{"count": float64(2), "hot_remove_enabled": false},
		},
		{
			name: "cpu hot add only",
			change: vmreconfig.ChangeSet{
				VM:  "vm-7",
				CPU: &vmreconfig.CPUChange{HotAddEnabled: b(true)},
			},
			operation: "Vcenter.Vm.Hardware.Cpu_update",
			wantBody:  map[string]any{"hot_add_enabled": true},
		},
		{
			name: "disk with no address and no vmdk name",
			change: vmreconfig.ChangeSet{
				VM: "vm-7",
				Disks: []vmreconfig.DiskChange{{
					NewVMDK: &vmreconfig.NewVMDK{CapacityBytes: i64(1073741824)},
				}},
			},
			operation: "Vcenter.Vm.Hardware.Disk_create",
			// No "type":"", no "scsi":{}, no "name":"".
			wantBody: map[string]any{"new_vmdk": map[string]any{"capacity": float64(1073741824)}},
		},
		{
			name: "disk with a named vmdk on scsi bus 0 unit 0",
			change: vmreconfig.ChangeSet{
				VM: "vm-7",
				Disks: []vmreconfig.DiskChange{{
					Type:    "SCSI",
					SCSI:    &vmreconfig.SCSIAddress{Bus: 0, Unit: i64(0)},
					NewVMDK: &vmreconfig.NewVMDK{Name: "data-01", CapacityBytes: i64(1073741824)},
				}},
			},
			operation: "Vcenter.Vm.Hardware.Disk_create",
			// bus and unit are both legitimately zero and must both be sent.
			wantBody: map[string]any{
				"type":     "SCSI",
				"scsi":     map[string]any{"bus": float64(0), "unit": float64(0)},
				"new_vmdk": map[string]any{"name": "data-01", "capacity": float64(1073741824)},
			},
		},
		{
			name: "empty vmdk spec sends an empty object, not omitted keys",
			change: vmreconfig.ChangeSet{
				VM:    "vm-7",
				Disks: []vmreconfig.DiskChange{{NewVMDK: &vmreconfig.NewVMDK{}}},
			},
			operation: "Vcenter.Vm.Hardware.Disk_create",
			wantBody:  map[string]any{"new_vmdk": map[string]any{}},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			s := vcmock.New(t, vcmock.Config{})
			client := newClient(t, s)
			if _, err := client.Apply(context.Background(), tc.change); err != nil {
				t.Fatalf("Apply: unexpected error: %v", err)
			}
			got := s.RequestsFor(tc.operation)
			if len(got) != 1 {
				t.Fatalf("%s was called %d times, want once", tc.operation, len(got))
			}
			assertJSONObject(t, got[0], tc.wantBody)
		})
	}
}

// TestFullyPopulatedRequestBodies checks that every property exposed by the
// public change types reaches the wire, including explicit false and zero
// values. The omission tests above exercise the inverse rule.
func TestFullyPopulatedRequestBodies(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{})
	client := newClient(t, s)

	if _, err := client.Apply(context.Background(), vmreconfig.ChangeSet{
		VM: "vm-101",
		Memory: &vmreconfig.MemoryChange{
			SizeMiB:       i64(16384),
			HotAddEnabled: b(false),
		},
		CPU: &vmreconfig.CPUChange{
			Count:            i64(8),
			CoresPerSocket:   i64(4),
			HotAddEnabled:    b(true),
			HotRemoveEnabled: b(false),
		},
		Disks: []vmreconfig.DiskChange{{
			Type:    "SCSI",
			SCSI:    &vmreconfig.SCSIAddress{Bus: 0, Unit: i64(0)},
			NewVMDK: &vmreconfig.NewVMDK{Name: "data-01", CapacityBytes: i64(42949672960)},
		}},
	}); err != nil {
		t.Fatalf("Apply: unexpected error: %v", err)
	}

	assertJSONObject(t, s.RequestsFor("Vcenter.Vm.Hardware.Memory_update")[0], map[string]any{
		"size_mib": float64(16384), "hot_add_enabled": false,
	})
	assertJSONObject(t, s.RequestsFor("Vcenter.Vm.Hardware.Cpu_update")[0], map[string]any{
		"count": float64(8), "cores_per_socket": float64(4),
		"hot_add_enabled": true, "hot_remove_enabled": false,
	})
	assertJSONObject(t, s.RequestsFor("Vcenter.Vm.Hardware.Disk_create")[0], map[string]any{
		"type": "SCSI",
		"scsi": map[string]any{"bus": float64(0), "unit": float64(0)},
		"new_vmdk": map[string]any{
			"name": "data-01", "capacity": float64(42949672960),
		},
	})
}

// TestOnlyContractOperationsAreCalled proves the client never reaches for a
// route the contract does not name.
func TestOnlyContractOperationsAreCalled(t *testing.T) {
	c := loadContract(t)
	named := map[string]bool{}
	for _, id := range c.OperationIDs() {
		named[id] = true
	}

	s := vcmock.New(t, vcmock.Config{})
	client := newClient(t, s)
	if _, err := client.Apply(context.Background(), vmreconfig.ChangeSet{
		VM:                "vm-101",
		Memory:            &vmreconfig.MemoryChange{SizeMiB: i64(2048)},
		CPU:               &vmreconfig.CPUChange{Count: i64(2)},
		Disks:             []vmreconfig.DiskChange{{NewVMDK: &vmreconfig.NewVMDK{CapacityBytes: i64(1073741824)}}},
		RestorePowerState: true,
	}); err != nil {
		t.Fatalf("Apply: unexpected error: %v", err)
	}
	for _, r := range s.Requests() {
		if r.OperationID == "" {
			t.Errorf("request %d (%s %s%s) matched no operation the contract names",
				r.Seq, r.Method, r.Path, querySuffix(r.RawQuery))
			continue
		}
		if !named[r.OperationID] {
			t.Errorf("request %d used operation %q, which the contract does not name", r.Seq, r.OperationID)
		}
		if r.Status >= 400 {
			t.Errorf("request %d (%s) was refused with HTTP %d", r.Seq, r.OperationID, r.Status)
		}
	}
}

func TestEveryDeclaredAdapterTypeIsAccepted(t *testing.T) {
	for _, adapterType := range []string{"IDE", "SCSI", "SATA", "NVME"} {
		t.Run(adapterType, func(t *testing.T) {
			s := vcmock.New(t, vcmock.Config{})
			client := newClient(t, s)
			if _, err := client.Apply(context.Background(), vmreconfig.ChangeSet{
				VM: "vm-101",
				Disks: []vmreconfig.DiskChange{{
					Type: adapterType, NewVMDK: &vmreconfig.NewVMDK{},
				}},
			}); err != nil {
				t.Fatalf("Apply: %v", err)
			}
			requests := s.RequestsFor("Vcenter.Vm.Hardware.Disk_create")
			if len(requests) != 1 {
				t.Fatalf("Disk_create calls = %d, want 1", len(requests))
			}
			assertJSONObject(t, requests[0], map[string]any{
				"type": adapterType, "new_vmdk": map[string]any{},
			})
		})
	}
}

// ---------------------------------------------------------------------------
// a later step fails
// ---------------------------------------------------------------------------

func TestLaterStepFailsAndEarlierStepsAreReported(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{
		DiskIDs: []string{"2000", "2001"},
		Failures: []vcmock.Failure{{
			Operation:  "Vcenter.Vm.Hardware.Disk_create",
			Occurrence: 2,
			Status:     http.StatusBadRequest,
			ErrorType:  "UNABLE_TO_ALLOCATE_RESOURCE",
			Message:    "Datastore ds-01 has insufficient free space for a 500 GB disk.",
		}},
	})
	client := newClient(t, s)

	report, err := client.Apply(context.Background(), vmreconfig.ChangeSet{
		VM:     "vm-101",
		Memory: &vmreconfig.MemoryChange{SizeMiB: i64(16384)},
		CPU:    &vmreconfig.CPUChange{Count: i64(8), CoresPerSocket: i64(4)},
		Disks: []vmreconfig.DiskChange{
			{Type: "SCSI", NewVMDK: &vmreconfig.NewVMDK{Name: "data-01", CapacityBytes: i64(107374182400)}},
			{Type: "SCSI", NewVMDK: &vmreconfig.NewVMDK{Name: "data-02", CapacityBytes: i64(536870912000)}},
		},
		RestorePowerState: true,
	})

	if err == nil {
		t.Fatal("Apply: got nil error, want the failure of the second disk")
	}

	// The error is the appliance's, carried faithfully.
	var apiErr *vmreconfig.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("Apply error %v is not an *vmreconfig.APIError", err)
	}
	if apiErr.Operation != "Vcenter.Vm.Hardware.Disk_create" {
		t.Errorf("APIError.Operation = %q, want Vcenter.Vm.Hardware.Disk_create", apiErr.Operation)
	}
	if apiErr.Status != http.StatusBadRequest {
		t.Errorf("APIError.Status = %d, want 400", apiErr.Status)
	}
	if apiErr.ErrorType != "UNABLE_TO_ALLOCATE_RESOURCE" {
		t.Errorf("APIError.ErrorType = %q, want UNABLE_TO_ALLOCATE_RESOURCE", apiErr.ErrorType)
	}
	if !strings.Contains(apiErr.Message, "insufficient free space") {
		t.Errorf("APIError.Message = %q, want the appliance's default_message", apiErr.Message)
	}
	if !errors.Is(err, vmreconfig.ErrAPI) {
		t.Error("Apply error does not match vmreconfig.ErrAPI")
	}

	// The report is not discarded. Everything that landed is still reported as
	// having landed, and the step behind the failure is NOT_ATTEMPTED rather
	// than SKIPPED: it is outstanding work, not work that was unnecessary.
	assertStatuses(t, report, []vmreconfig.StepStatus{
		vmreconfig.StepApplied,      // Power_stop
		vmreconfig.StepApplied,      // Memory_update
		vmreconfig.StepApplied,      // Cpu_update
		vmreconfig.StepApplied,      // Disk_create, data-01
		vmreconfig.StepFailed,       // Disk_create, data-02
		vmreconfig.StepNotAttempted, // Power_start
	})

	if got, want := report.Applied(), []string{
		"Vcenter.Vm.Power_stop",
		"Vcenter.Vm.Hardware.Memory_update",
		"Vcenter.Vm.Hardware.Cpu_update",
		"Vcenter.Vm.Hardware.Disk_create",
	}; !reflect.DeepEqual(got, want) {
		t.Errorf("Applied() = %v, want %v", got, want)
	}
	if got, want := report.CreatedDiskIDs, []string{"2000"}; !reflect.DeepEqual(got, want) {
		t.Errorf("CreatedDiskIDs = %v, want %v; only the first disk was created", got, want)
	}

	failed, ok := report.Failed()
	if !ok {
		t.Fatal("Report.Failed() reports no failed step")
	}
	if failed.Operation != "Vcenter.Vm.Hardware.Disk_create" {
		t.Errorf("failed step operation = %q", failed.Operation)
	}
	if failed.Err == nil {
		t.Error("the failed step carries no Err")
	}
	if failed.Detail == "" {
		t.Error("the failed step carries no Detail")
	}
	for i, step := range report.Steps {
		if step.Status != vmreconfig.StepFailed && step.Err != nil {
			t.Errorf("step %d (%s) is %s but carries Err %v", i, step.Operation, step.Status, step.Err)
		}
		if step.Detail == "" {
			t.Errorf("step %d (%s) carries no Detail", i, step.Operation)
		}
	}

	// The machine was left powered off, and the report says so because it read
	// the state back rather than assuming it.
	if report.InitialPowerState != vcmock.PoweredOn {
		t.Errorf("InitialPowerState = %q, want POWERED_ON", report.InitialPowerState)
	}
	if report.FinalPowerState != vcmock.PoweredOff {
		t.Errorf("FinalPowerState = %q, want POWERED_OFF; the change set failed before powering back on",
			report.FinalPowerState)
	}
	if got := s.PowerState(); got != vcmock.PoweredOff {
		t.Errorf("the appliance is %s, want POWERED_OFF", got)
	}

	// Nothing behind the failure reached the wire.
	if got := s.RequestsFor("Vcenter.Vm.Power_start"); len(got) != 0 {
		t.Errorf("Vcenter.Vm.Power_start was called %d times after a failed step, want never", len(got))
	}
	if got := s.RequestsFor("Vcenter.Vm.Hardware.Disk_create"); len(got) != 2 {
		t.Errorf("Disk_create was called %d times, want 2", len(got))
	}
	assertSequence(t, s, []string{
		"Vcenter.Vm.Power_get",
		"Vcenter.Vm.Power_stop",
		"Vcenter.Vm.Hardware.Memory_update",
		"Vcenter.Vm.Hardware.Cpu_update",
		"Vcenter.Vm.Hardware.Disk_create",
		"Vcenter.Vm.Hardware.Disk_create",
		"Vcenter.Vm.Power_get",
	})
}

// TestFailureBeforeTheHardwareSteps checks the same reporting rules when the
// failure happens early instead of late.
func TestFailureBeforeTheHardwareSteps(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{
		Failures: []vcmock.Failure{{
			Operation: "Vcenter.Vm.Power_stop",
			Status:    http.StatusBadRequest,
			ErrorType: "RESOURCE_BUSY",
			Message:   "The virtual machine is busy completing another operation.",
		}},
	})
	client := newClient(t, s)

	report, err := client.Apply(context.Background(), vmreconfig.ChangeSet{
		VM:                "vm-101",
		Memory:            &vmreconfig.MemoryChange{SizeMiB: i64(8192)},
		CPU:               &vmreconfig.CPUChange{Count: i64(4)},
		Disks:             []vmreconfig.DiskChange{{NewVMDK: &vmreconfig.NewVMDK{CapacityBytes: i64(1073741824)}}},
		RestorePowerState: true,
	})
	if err == nil {
		t.Fatal("Apply: got nil error, want the failure of the power stop")
	}
	assertStatuses(t, report, []vmreconfig.StepStatus{
		vmreconfig.StepFailed,       // Power_stop
		vmreconfig.StepNotAttempted, // Memory_update
		vmreconfig.StepNotAttempted, // Cpu_update
		vmreconfig.StepNotAttempted, // Disk_create
		vmreconfig.StepNotAttempted, // Power_start
	})
	if got := report.Applied(); len(got) != 0 {
		t.Errorf("Applied() = %v, want nothing", got)
	}
	if len(report.CreatedDiskIDs) != 0 {
		t.Errorf("CreatedDiskIDs = %v, want nothing", report.CreatedDiskIDs)
	}
	// Only the preflight read and the refused stop, then the closing read.
	assertSequence(t, s, []string{
		"Vcenter.Vm.Power_get",
		"Vcenter.Vm.Power_stop",
		"Vcenter.Vm.Power_get",
	})
	if report.FinalPowerState != vcmock.PoweredOn {
		t.Errorf("FinalPowerState = %q, want POWERED_ON; the stop never took effect", report.FinalPowerState)
	}
}

// ---------------------------------------------------------------------------
// steps that are genuinely unnecessary
// ---------------------------------------------------------------------------

func TestAlreadyPoweredOffSkipsTheStopWithoutSendingIt(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{InitialPowerState: vcmock.PoweredOff})
	client := newClient(t, s)

	report, err := client.Apply(context.Background(), vmreconfig.ChangeSet{
		VM:                "vm-101",
		Memory:            &vmreconfig.MemoryChange{SizeMiB: i64(8192)},
		RestorePowerState: true,
	})
	if err != nil {
		t.Fatalf("Apply: unexpected error: %v", err)
	}
	if got := s.RequestsFor("Vcenter.Vm.Power_stop"); len(got) != 0 {
		t.Errorf("Power_stop was sent %d times to a machine that was already off, want never", len(got))
	}
	assertStatuses(t, report, []vmreconfig.StepStatus{
		vmreconfig.StepSkipped, // Power_stop, already off
		vmreconfig.StepApplied, // Memory_update
		vmreconfig.StepSkipped, // Cpu_update, not requested
		vmreconfig.StepSkipped, // Power_start, the machine was not on to begin with
	})
	if report.FinalPowerState != vcmock.PoweredOff {
		t.Errorf("FinalPowerState = %q, want POWERED_OFF", report.FinalPowerState)
	}
	if got := s.RequestsFor("Vcenter.Vm.Power_start"); len(got) != 0 {
		t.Errorf("Power_start was sent %d times for a machine that started powered off, want never", len(got))
	}
}

// TestAlreadyInDesiredStateIsNotAFailure covers the machine that powers itself
// off between the caller's read and the caller's stop. The specification
// declares 400 on Vcenter.Vm.Power_stop as Vapi.Std.Errors.AlreadyInDesiredState,
// and nothing is outstanding, so the change set carries on.
func TestAlreadyInDesiredStateIsNotAFailure(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{PowerStopAlreadyOff: true})
	client := newClient(t, s)

	report, err := client.Apply(context.Background(), vmreconfig.ChangeSet{
		VM:     "vm-101",
		Memory: &vmreconfig.MemoryChange{SizeMiB: i64(8192)},
		CPU:    &vmreconfig.CPUChange{Count: i64(4)},
	})
	if err != nil {
		t.Fatalf("Apply: unexpected error: %v", err)
	}
	assertStatuses(t, report, []vmreconfig.StepStatus{
		vmreconfig.StepSkipped, // Power_stop, already in the desired state
		vmreconfig.StepApplied, // Memory_update
		vmreconfig.StepApplied, // Cpu_update
		vmreconfig.StepSkipped, // Power_start, not requested
	})
	if got := s.RequestsFor("Vcenter.Vm.Power_stop"); len(got) != 1 {
		t.Errorf("Power_stop was sent %d times, want once", len(got))
	}
}

func TestAlreadyInDesiredStateOnlySkipsTheDeclared400(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{Failures: []vcmock.Failure{{
		Operation: "Vcenter.Vm.Power_stop",
		Status:    http.StatusInternalServerError,
		ErrorType: "ALREADY_IN_DESIRED_STATE",
		Message:   "The operation could not be completed.",
	}}})
	client := newClient(t, s)

	report, err := client.Apply(context.Background(), vmreconfig.ChangeSet{
		VM:     "vm-101",
		Memory: &vmreconfig.MemoryChange{SizeMiB: i64(8192)},
	})
	if err == nil {
		t.Fatal("Apply: got nil error, want the HTTP 500 refusal")
	}
	assertStatuses(t, report, []vmreconfig.StepStatus{
		vmreconfig.StepFailed,
		vmreconfig.StepNotAttempted,
		vmreconfig.StepNotAttempted,
		vmreconfig.StepNotAttempted,
	})
	var apiErr *vmreconfig.APIError
	if !errors.As(err, &apiErr) || apiErr.Status != http.StatusInternalServerError {
		t.Fatalf("Apply error = %v, want the power-stop APIError with HTTP 500", err)
	}
}

func TestNotRestoringPowerLeavesTheMachineOff(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{})
	client := newClient(t, s)

	report, err := client.Apply(context.Background(), vmreconfig.ChangeSet{
		VM:                "vm-101",
		CPU:               &vmreconfig.CPUChange{Count: i64(4)},
		RestorePowerState: false,
	})
	if err != nil {
		t.Fatalf("Apply: unexpected error: %v", err)
	}
	if got := s.RequestsFor("Vcenter.Vm.Power_start"); len(got) != 0 {
		t.Errorf("Power_start was sent %d times without RestorePowerState, want never", len(got))
	}
	if report.FinalPowerState != vcmock.PoweredOff {
		t.Errorf("FinalPowerState = %q, want POWERED_OFF", report.FinalPowerState)
	}
	assertStatuses(t, report, []vmreconfig.StepStatus{
		vmreconfig.StepApplied, // Power_stop
		vmreconfig.StepSkipped, // Memory_update, not requested
		vmreconfig.StepApplied, // Cpu_update
		vmreconfig.StepSkipped, // Power_start, not requested
	})
}

func TestSuspendedMachineIsStoppedButNotRestarted(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{InitialPowerState: vcmock.Suspended})
	client := newClient(t, s)

	report, err := client.Apply(context.Background(), vmreconfig.ChangeSet{
		VM:                "vm-101",
		CPU:               &vmreconfig.CPUChange{Count: i64(2)},
		RestorePowerState: true,
	})
	if err != nil {
		t.Fatalf("Apply: %v", err)
	}
	assertStatuses(t, report, []vmreconfig.StepStatus{
		vmreconfig.StepApplied,
		vmreconfig.StepSkipped,
		vmreconfig.StepApplied,
		vmreconfig.StepSkipped,
	})
	if got := len(s.RequestsFor("Vcenter.Vm.Power_stop")); got != 1 {
		t.Errorf("Power_stop calls = %d, want 1", got)
	}
	if got := len(s.RequestsFor("Vcenter.Vm.Power_start")); got != 0 {
		t.Errorf("Power_start calls = %d, want 0", got)
	}
	if report.FinalPowerState != vcmock.PoweredOff {
		t.Errorf("FinalPowerState = %q, want POWERED_OFF", report.FinalPowerState)
	}
}

// ---------------------------------------------------------------------------
// local validation
// ---------------------------------------------------------------------------

func TestInvalidInputSendsNothing(t *testing.T) {
	tests := []struct {
		name   string
		change vmreconfig.ChangeSet
	}{
		{"no vm", vmreconfig.ChangeSet{Memory: &vmreconfig.MemoryChange{SizeMiB: i64(1024)}}},
		{"no changes", vmreconfig.ChangeSet{VM: "vm-1"}},
		{"empty memory change", vmreconfig.ChangeSet{VM: "vm-1", Memory: &vmreconfig.MemoryChange{}}},
		{"empty cpu change", vmreconfig.ChangeSet{VM: "vm-1", CPU: &vmreconfig.CPUChange{}}},
		{"zero memory size", vmreconfig.ChangeSet{VM: "vm-1", Memory: &vmreconfig.MemoryChange{SizeMiB: i64(0)}}},
		{"negative cpu count", vmreconfig.ChangeSet{VM: "vm-1", CPU: &vmreconfig.CPUChange{Count: i64(-2)}}},
		{"count not a multiple of cores per socket", vmreconfig.ChangeSet{
			VM:  "vm-1",
			CPU: &vmreconfig.CPUChange{Count: i64(5), CoresPerSocket: i64(2)},
		}},
		{"unknown adapter type", vmreconfig.ChangeSet{
			VM:    "vm-1",
			Disks: []vmreconfig.DiskChange{{Type: "USB", NewVMDK: &vmreconfig.NewVMDK{CapacityBytes: i64(1024)}}},
		}},
		{"disk with no backing", vmreconfig.ChangeSet{
			VM:    "vm-1",
			Disks: []vmreconfig.DiskChange{{Type: "SCSI"}},
		}},
		{"zero cpu count", vmreconfig.ChangeSet{VM: "vm-1", CPU: &vmreconfig.CPUChange{Count: i64(0)}}},
		{"non-positive cores per socket", vmreconfig.ChangeSet{
			VM:  "vm-1",
			CPU: &vmreconfig.CPUChange{CoresPerSocket: i64(0)},
		}},
		{"non-positive disk capacity", vmreconfig.ChangeSet{
			VM:    "vm-1",
			Disks: []vmreconfig.DiskChange{{NewVMDK: &vmreconfig.NewVMDK{CapacityBytes: i64(0)}}},
		}},
		{"scsi address on a non-scsi adapter", vmreconfig.ChangeSet{
			VM: "vm-1",
			Disks: []vmreconfig.DiskChange{{
				Type: "SATA", SCSI: &vmreconfig.SCSIAddress{Bus: 0}, NewVMDK: &vmreconfig.NewVMDK{},
			}},
		}},
		{"vmdk name carries the extension", vmreconfig.ChangeSet{
			VM:    "vm-1",
			Disks: []vmreconfig.DiskChange{{NewVMDK: &vmreconfig.NewVMDK{Name: "data-01.vmdk"}}},
		}},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			s := vcmock.New(t, vcmock.Config{})
			client := newClient(t, s)
			_, err := client.Apply(context.Background(), tc.change)
			if err == nil {
				t.Fatal("Apply: got nil error, want ErrInvalidRequest")
			}
			if !errors.Is(err, vmreconfig.ErrInvalidRequest) {
				t.Errorf("Apply error %v does not match ErrInvalidRequest", err)
			}
			if got := s.Requests(); len(got) != 0 {
				t.Errorf("an invalid change set sent %d requests, want none", len(got))
			}
		})
	}
}

func TestNewRejectsBadConfig(t *testing.T) {
	tests := []struct {
		name string
		cfg  vmreconfig.Config
	}{
		{"no base url", vmreconfig.Config{SessionID: "sid"}},
		{"non http scheme", vmreconfig.Config{BaseURL: "ftp://vc.example.com", SessionID: "sid"}},
		{"no session id", vmreconfig.Config{BaseURL: "https://vc.example.com"}},
		{"header unsafe session id", vmreconfig.Config{BaseURL: "https://vc.example.com", SessionID: "a\r\nb"}},
		{"session id with a control byte", vmreconfig.Config{BaseURL: "https://vc.example.com", SessionID: "a\x00b"}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := vmreconfig.New(tc.cfg); err == nil {
				t.Fatal("New: got nil error, want ErrInvalidRequest")
			} else if !errors.Is(err, vmreconfig.ErrInvalidRequest) {
				t.Errorf("New error %v does not match ErrInvalidRequest", err)
			}
		})
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestNewPerformsNoIOAndApplyUsesTheConfiguredHTTPClient(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{})
	next := http.DefaultTransport
	calls := 0
	custom := &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		return next.RoundTrip(r)
	})}

	client, err := vmreconfig.New(vmreconfig.Config{
		BaseURL: s.URL, SessionID: s.SessionID, HTTPClient: custom,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if calls != 0 {
		t.Fatalf("New performed %d HTTP requests, want none", calls)
	}
	if _, err := client.Apply(context.Background(), vmreconfig.ChangeSet{
		VM:  "vm-101",
		CPU: &vmreconfig.CPUChange{Count: i64(2)},
	}); err != nil {
		t.Fatalf("Apply: %v", err)
	}
	if calls == 0 {
		t.Fatal("Apply did not use Config.HTTPClient")
	}
}

func TestFinalPowerReadFailureIsReturned(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{Failures: []vcmock.Failure{{
		Operation:  "Vcenter.Vm.Power_get",
		Occurrence: 2,
		Status:     http.StatusServiceUnavailable,
		ErrorType:  "SERVICE_UNAVAILABLE",
		Message:    "Power state could not be read.",
	}}})
	client := newClient(t, s)

	report, err := client.Apply(context.Background(), vmreconfig.ChangeSet{
		VM:  "vm-101",
		CPU: &vmreconfig.CPUChange{Count: i64(2)},
	})
	if err == nil {
		t.Fatal("Apply: got nil error when the final power-state read was refused")
	}
	var apiErr *vmreconfig.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("Apply error %v is not an *APIError", err)
	}
	if apiErr.Operation != "Vcenter.Vm.Power_get" || apiErr.Status != http.StatusServiceUnavailable {
		t.Errorf("APIError = %#v, want final Power_get HTTP 503", apiErr)
	}
	if report.FinalPowerState != "" {
		t.Errorf("FinalPowerState = %q after its read failed, want unknown", report.FinalPowerState)
	}
	assertStatuses(t, report, []vmreconfig.StepStatus{
		vmreconfig.StepApplied,
		vmreconfig.StepSkipped,
		vmreconfig.StepApplied,
		vmreconfig.StepSkipped,
	})
}

// TestBaseURLTrailingSlash checks the separator is not doubled.
func TestBaseURLTrailingSlash(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{})
	client, err := vmreconfig.New(vmreconfig.Config{
		BaseURL:   s.URL + "/",
		SessionID: s.SessionID,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if _, err := client.Apply(context.Background(), vmreconfig.ChangeSet{
		VM:  "vm-101",
		CPU: &vmreconfig.CPUChange{Count: i64(2)},
	}); err != nil {
		t.Fatalf("Apply: unexpected error: %v", err)
	}
	for _, r := range s.Requests() {
		if strings.Contains(r.Path, "//") {
			t.Errorf("request %d path %q doubles the separator", r.Seq, r.Path)
		}
		if !strings.HasPrefix(r.Path, "/api/vcenter/") {
			t.Errorf("request %d path %q does not hang off the /api base path", r.Seq, r.Path)
		}
	}
}

// TestContextCancellationStopsTheChangeSet checks the client honours the
// context instead of running the sequence to the end.
func TestContextCancellationStopsTheChangeSet(t *testing.T) {
	s := vcmock.New(t, vcmock.Config{})
	client := newClient(t, s)

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	_, err := client.Apply(ctx, vmreconfig.ChangeSet{
		VM:     "vm-101",
		Memory: &vmreconfig.MemoryChange{SizeMiB: i64(2048)},
	})
	if err == nil {
		t.Fatal("Apply: got nil error on a cancelled context")
	}
	if got := s.RequestsFor("Vcenter.Vm.Hardware.Memory_update"); len(got) != 0 {
		t.Errorf("a cancelled context still sent %d memory updates", len(got))
	}
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

func loadContract(t *testing.T) *contract.Contract {
	t.Helper()
	c, err := contract.LoadDefault()
	if err != nil {
		t.Fatalf("load contract: %v", err)
	}
	return c
}

func newClient(t *testing.T, s *vcmock.Server) *vmreconfig.Client {
	t.Helper()
	c, err := vmreconfig.New(vmreconfig.Config{BaseURL: s.URL, SessionID: s.SessionID})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return c
}

func i64(v int64) *int64 { return &v }
func b(v bool) *bool     { return &v }

func querySuffix(raw string) string {
	if raw == "" {
		return ""
	}
	return "?" + raw
}

// assertCommonHeaders checks the session header and Accept, which every
// operation in the contract carries, and checks that the client did not reach
// for a Bearer scheme this API does not use.
func assertCommonHeaders(t *testing.T, r vcmock.Recorded, sessionID string) {
	t.Helper()
	if got := r.Header.Get("vmware-api-session-id"); got != sessionID {
		t.Errorf("request %d (%s): vmware-api-session-id = %q, want %q", r.Seq, r.OperationID, got, sessionID)
	}
	if got := r.Header.Get("Accept"); !strings.Contains(got, "application/json") {
		t.Errorf("request %d (%s): Accept = %q, want application/json", r.Seq, r.OperationID, got)
	}
	if got := r.Header.Get("Authorization"); got != "" {
		t.Errorf("request %d (%s): sends Authorization %q; this API authenticates with vmware-api-session-id",
			r.Seq, r.OperationID, got)
	}
}

// assertSequence compares the whole request log, in order, against the
// operations that were expected to reach the wire.
func assertSequence(t *testing.T, s *vcmock.Server, want []string) {
	t.Helper()
	got := s.OperationIDs()
	if reflect.DeepEqual(got, want) {
		return
	}
	t.Errorf("request sequence:\n got: %v\nwant: %v", got, want)
}

// assertJSONObject compares a recorded request body against the exact object it
// should have been, key for key and all the way down. It is the check that
// catches an unset optional property serialized as an empty value: an extra key
// is as much a failure as a missing one.
func assertJSONObject(t *testing.T, r vcmock.Recorded, want map[string]any) {
	t.Helper()
	got, err := r.JSONBody()
	if err != nil {
		t.Fatalf("request %d (%s): %v", r.Seq, r.OperationID, err)
	}
	if reflect.DeepEqual(got, want) {
		return
	}
	gotJSON, _ := json.Marshal(got)
	wantJSON, _ := json.Marshal(want)
	t.Errorf("request %d (%s) body:\n got: %s\nwant: %s\n(an unset optional property must be absent, not present and empty)",
		r.Seq, r.OperationID, gotJSON, wantJSON)
}

// assertStatuses compares the per-step outcomes of a report.
func assertStatuses(t *testing.T, report vmreconfig.Report, want []vmreconfig.StepStatus) {
	t.Helper()
	got := make([]vmreconfig.StepStatus, 0, len(report.Steps))
	for _, s := range report.Steps {
		got = append(got, s.Status)
	}
	if reflect.DeepEqual(got, want) {
		return
	}
	lines := make([]string, 0, len(report.Steps))
	for _, s := range report.Steps {
		lines = append(lines, "  "+s.Operation+" = "+string(s.Status))
	}
	t.Errorf("step statuses:\n got: %v\nwant: %v\nreport:\n%s", got, want, strings.Join(lines, "\n"))
}
