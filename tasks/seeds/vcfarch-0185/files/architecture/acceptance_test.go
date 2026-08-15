package architecture_test

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"vcfplan/architecture"
)

func loadAuthorities(t *testing.T) (architecture.Estate, architecture.CompatibilitySnapshot) {
	t.Helper()

	estateData, err := os.ReadFile("../testdata/estate.json")
	if err != nil {
		t.Fatal(err)
	}
	var estate architecture.Estate
	if err := decodeFixedAuthority(estateData, &estate); err != nil {
		t.Fatalf("decode fixed estate: %v", err)
	}

	snapshotData, err := os.ReadFile("../testdata/compatibility_snapshot.json")
	if err != nil {
		t.Fatal(err)
	}
	var snapshot architecture.CompatibilitySnapshot
	if err := decodeFixedAuthority(snapshotData, &snapshot); err != nil {
		t.Fatalf("decode fixed compatibility snapshot: %v", err)
	}
	return estate, snapshot
}

func decodeFixedAuthority(data []byte, value any) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(value); err != nil {
		return err
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return fmt.Errorf("unexpected trailing JSON: %v", err)
	}
	return nil
}

func decodePlan(t *testing.T, r io.Reader) architecture.Plan {
	t.Helper()
	var plan architecture.Plan
	decoder := json.NewDecoder(r)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&plan); err != nil {
		t.Fatalf("decode migration plan: %v", err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		t.Fatalf("migration plan has trailing JSON: %v", err)
	}
	return plan
}

func TestBuildAndArtifactMatchPinnedAuthorities(t *testing.T) {
	estate, snapshot := loadAuthorities(t)
	built, err := architecture.BuildPlan(estate, snapshot)
	if err != nil {
		t.Fatalf("BuildPlan: %v", err)
	}
	if err := verifyPlan(built, estate, snapshot); err != nil {
		t.Fatalf("built plan: %v", err)
	}

	artifactFile, err := os.Open("../migration-plan.json")
	if err != nil {
		t.Fatalf("open migration-plan.json: %v", err)
	}
	defer artifactFile.Close()
	artifact := decodePlan(t, artifactFile)
	if err := verifyPlan(artifact, estate, snapshot); err != nil {
		t.Fatalf("migration-plan.json: %v", err)
	}
	if !reflect.DeepEqual(built, artifact) {
		t.Fatal("BuildPlan result and migration-plan.json differ")
	}
	if err := architecture.ValidatePlan(artifact, estate, snapshot); err != nil {
		t.Fatalf("ValidatePlan rejected authoritative artifact: %v", err)
	}

	var encoded bytes.Buffer
	if err := architecture.WritePlan(&encoded, built); err != nil {
		t.Fatalf("WritePlan: %v", err)
	}
	decoded := decodePlan(t, &encoded)
	if !reflect.DeepEqual(decoded, built) {
		t.Fatal("WritePlan did not preserve the plan")
	}
}

func TestPublicLoadersUseTheFixedAuthorities(t *testing.T) {
	wantEstate, wantSnapshot := loadAuthorities(t)
	estateData, err := os.ReadFile("../testdata/estate.json")
	if err != nil {
		t.Fatal(err)
	}
	snapshotData, err := os.ReadFile("../testdata/compatibility_snapshot.json")
	if err != nil {
		t.Fatal(err)
	}

	gotEstate, err := architecture.LoadEstate(bytes.NewReader(estateData))
	if err != nil {
		t.Fatalf("LoadEstate: %v", err)
	}
	if !reflect.DeepEqual(gotEstate, wantEstate) {
		t.Fatal("LoadEstate did not return the fixed fixture")
	}
	gotSnapshot, err := architecture.LoadCompatibilitySnapshot(bytes.NewReader(snapshotData))
	if err != nil {
		t.Fatalf("LoadCompatibilitySnapshot: %v", err)
	}
	if !reflect.DeepEqual(gotSnapshot, wantSnapshot) {
		t.Fatal("LoadCompatibilitySnapshot did not return the pinned snapshot")
	}

	modifiedEstate := wantEstate
	modifiedEstate.Site = "TEST-SITE"
	modifiedEstateData, err := json.Marshal(modifiedEstate)
	if err != nil {
		t.Fatal(err)
	}
	gotModifiedEstate, err := architecture.LoadEstate(bytes.NewReader(modifiedEstateData))
	if err != nil || !reflect.DeepEqual(gotModifiedEstate, modifiedEstate) {
		t.Fatalf("LoadEstate did not decode its reader: got %+v, err %v", gotModifiedEstate, err)
	}

	modifiedSnapshot := wantSnapshot
	modifiedSnapshot.SnapshotID = "test-snapshot"
	modifiedSnapshotData, err := json.Marshal(modifiedSnapshot)
	if err != nil {
		t.Fatal(err)
	}
	gotModifiedSnapshot, err := architecture.LoadCompatibilitySnapshot(bytes.NewReader(modifiedSnapshotData))
	if err != nil || !reflect.DeepEqual(gotModifiedSnapshot, modifiedSnapshot) {
		t.Fatalf("LoadCompatibilitySnapshot did not decode its reader: got %+v, err %v", gotModifiedSnapshot, err)
	}

	malformed := []struct {
		name string
		load func() error
	}{
		{
			name: "estate malformed JSON",
			load: func() error {
				_, err := architecture.LoadEstate(strings.NewReader(`{"estateId":`))
				return err
			},
		},
		{
			name: "estate unknown field",
			load: func() error {
				_, err := architecture.LoadEstate(strings.NewReader(`{"estateId":"x","unexpected":true}`))
				return err
			},
		},
		{
			name: "estate trailing value",
			load: func() error {
				_, err := architecture.LoadEstate(strings.NewReader(`{} {}`))
				return err
			},
		},
		{
			name: "snapshot malformed JSON",
			load: func() error {
				_, err := architecture.LoadCompatibilitySnapshot(strings.NewReader(`{"snapshotId":`))
				return err
			},
		},
		{
			name: "snapshot unknown field",
			load: func() error {
				_, err := architecture.LoadCompatibilitySnapshot(strings.NewReader(`{"snapshotId":"x","unexpected":true}`))
				return err
			},
		},
		{
			name: "snapshot trailing value",
			load: func() error {
				_, err := architecture.LoadCompatibilitySnapshot(strings.NewReader(`{} {}`))
				return err
			},
		},
	}
	for _, test := range malformed {
		t.Run(test.name, func(t *testing.T) {
			if err := test.load(); err == nil {
				t.Fatal("malformed authority unexpectedly accepted")
			}
		})
	}
}

func TestValidatePlanAcceptsAnyFeasibleHostPlacement(t *testing.T) {
	estate, snapshot := loadAuthorities(t)
	plan, err := architecture.BuildPlan(estate, snapshot)
	if err != nil {
		t.Fatal(err)
	}

	for i := range plan.Architecture.Placements {
		placement := &plan.Architecture.Placements[i]
		for j := range placement.HostIDs {
			placement.HostIDs[j] = estate.Hosts[(i+j+1)%len(estate.Hosts)].ID
		}
	}
	if err := verifyPlan(plan, estate, snapshot); err != nil {
		t.Fatalf("feasible placement rejected by independent verifier: %v", err)
	}
	if err := architecture.ValidatePlan(plan, estate, snapshot); err != nil {
		t.Fatalf("ValidatePlan rejected feasible placement: %v", err)
	}
	var encoded bytes.Buffer
	if err := architecture.WritePlan(&encoded, plan); err != nil {
		t.Fatalf("WritePlan: %v", err)
	}
	if decoded := decodePlan(t, &encoded); !reflect.DeepEqual(decoded, plan) {
		t.Fatal("WritePlan did not preserve a feasible non-default placement")
	}
}

func TestHostCountAndFTTAreJointlyValidated(t *testing.T) {
	estate, snapshot := loadAuthorities(t)
	valid, err := architecture.BuildPlan(estate, snapshot)
	if err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name      string
		mutate    func(*architecture.Plan, *architecture.Estate)
		wantError string
	}{
		{name: "four hosts FTT one", mutate: func(*architecture.Plan, *architecture.Estate) {}},
		{
			name: "below VCF management minimum",
			mutate: func(plan *architecture.Plan, estate *architecture.Estate) {
				plan.Architecture.HostCount = 3
				estate.Hosts = estate.Hosts[:3]
			},
			wantError: "minimum host count is 4",
		},
		{
			name: "four hosts contradict FTT two",
			mutate: func(plan *architecture.Plan, estate *architecture.Estate) {
				plan.Architecture.FailuresToTolerate = 2
				estate.Storage.FailuresToTolerate = 2
			},
			wantError: "FTT 2 requires at least 5 hosts",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			planCopy := clonePlan(t, valid)
			estateCopy := estate
			test.mutate(&planCopy, &estateCopy)
			err := verifyPlan(planCopy, estateCopy, snapshot)
			implementationErr := architecture.ValidatePlan(planCopy, estateCopy, snapshot)
			if test.wantError == "" {
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
				if implementationErr != nil {
					t.Fatalf("ValidatePlan rejected valid case: %v", implementationErr)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), test.wantError) {
				t.Fatalf("got %v, want error containing %q", err, test.wantError)
			}
			if implementationErr == nil {
				t.Fatal("ValidatePlan accepted invalid host/FTT case")
			}
		})
	}
}

func TestValidationRejectsCompatibilityDrift(t *testing.T) {
	estate, snapshot := loadAuthorities(t)
	valid, err := architecture.BuildPlan(estate, snapshot)
	if err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name   string
		mutate func(*architecture.Plan)
	}{
		{
			name:   "wrong foundation version",
			mutate: func(plan *architecture.Plan) { plan.Architecture.FoundationVersion = "9.0.1" },
		},
		{
			name:   "omitted source product",
			mutate: func(plan *architecture.Plan) { plan.Migrations = plan.Migrations[:2] },
		},
		{
			name:   "unsupported version hop",
			mutate: func(plan *architecture.Plan) { plan.Migrations[0].Path[1] = "8.17.0" },
		},
		{
			name:   "lost content decision",
			mutate: func(plan *architecture.Plan) { plan.Migrations[2].Content.Abandons = nil },
		},
		{
			name:   "missing migration gate",
			mutate: func(plan *architecture.Plan) { plan.Migrations[1].Gates = plan.Migrations[1].Gates[:2] },
		},
		{
			name:   "undersized logs",
			mutate: func(plan *architecture.Plan) { plan.Architecture.Placements[2].Capacity.IngestGiBPerDay = 75 },
		},
		{
			name: "co-located HA nodes",
			mutate: func(plan *architecture.Plan) {
				plan.Architecture.Placements[1].HostIDs[2] = plan.Architecture.Placements[1].HostIDs[0]
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			candidate := clonePlan(t, valid)
			test.mutate(&candidate)
			if err := verifyPlan(candidate, estate, snapshot); err == nil {
				t.Fatal("mutated plan unexpectedly accepted")
			}
			if err := architecture.ValidatePlan(candidate, estate, snapshot); err == nil {
				t.Fatal("ValidatePlan accepted compatibility drift")
			}
		})
	}
}

func clonePlan(t *testing.T, plan architecture.Plan) architecture.Plan {
	t.Helper()
	data, err := json.Marshal(plan)
	if err != nil {
		t.Fatal(err)
	}
	return decodePlan(t, bytes.NewReader(data))
}

// verifyPlan is deliberately independent of the submitted ValidatePlan. It reads
// only the artifact and the two pinned local authorities.
func verifyPlan(plan architecture.Plan, estate architecture.Estate, snapshot architecture.CompatibilitySnapshot) error {
	if plan.SchemaVersion != "vcf-migration-plan/v1" {
		return fmt.Errorf("unexpected schema version %q", plan.SchemaVersion)
	}
	if plan.SnapshotID != snapshot.SnapshotID || plan.EstateID != estate.EstateID {
		return fmt.Errorf("authority identifiers do not match")
	}
	arch := plan.Architecture
	if arch.Site != estate.Site || arch.DeploymentModel != snapshot.Architecture.DeploymentModel ||
		arch.FoundationVersion != snapshot.TargetFoundationVersion || arch.Cluster != snapshot.Architecture.PlacementCluster {
		return fmt.Errorf("target topology does not match the estate and snapshot")
	}
	if arch.HostCount != len(estate.Hosts) {
		return fmt.Errorf("declared host count %d does not match inventory count %d", arch.HostCount, len(estate.Hosts))
	}
	if arch.HostCount < snapshot.Architecture.MinimumHostCount {
		return fmt.Errorf("minimum host count is %d, got %d", snapshot.Architecture.MinimumHostCount, arch.HostCount)
	}
	if arch.FailuresToTolerate != estate.Storage.FailuresToTolerate {
		return fmt.Errorf("declared FTT does not match estate storage policy")
	}
	requiredHosts, ok := snapshot.Architecture.MinimumHostsByFTT[strconv.Itoa(arch.FailuresToTolerate)]
	if !ok {
		return fmt.Errorf("no host rule for FTT %d", arch.FailuresToTolerate)
	}
	if arch.HostCount < requiredHosts {
		return fmt.Errorf("FTT %d requires at least %d hosts, got %d", arch.FailuresToTolerate, requiredHosts, arch.HostCount)
	}
	if arch.StoragePolicy != estate.Storage.Policy || arch.StoragePolicy != snapshot.Architecture.RequiredStorage {
		return fmt.Errorf("storage policy mismatch")
	}

	knownHosts := make(map[string]bool, len(estate.Hosts))
	hostCapacity := make(map[string]architecture.Host, len(estate.Hosts))
	usedCPU := make(map[string]int, len(estate.Hosts))
	usedMemory := make(map[string]int, len(estate.Hosts))
	for _, host := range estate.Hosts {
		knownHosts[host.ID] = true
		hostCapacity[host.ID] = host
	}
	if len(arch.Placements) != len(snapshot.Sizing) {
		return fmt.Errorf("got %d placements, want %d", len(arch.Placements), len(snapshot.Sizing))
	}
	for i, sizing := range snapshot.Sizing {
		placement := arch.Placements[i]
		if placement.Component != sizing.Component || placement.Version != sizing.Version || placement.Profile != sizing.Profile ||
			placement.NodeCount != sizing.NodeCount || placement.VCPUPerNode != sizing.VCPUPerNode ||
			placement.MemoryGiBPerNode != sizing.MemoryGiBPerNode || !reflect.DeepEqual(placement.Capacity, sizing.Capacity) {
			return fmt.Errorf("placement for %s does not match pinned sizing", sizing.Component)
		}
		if placement.Cluster != estate.Cluster || !placement.AntiAffinity || len(placement.HostIDs) != placement.NodeCount {
			return fmt.Errorf("placement for %s does not meet cluster or anti-affinity requirements", sizing.Component)
		}
		seen := map[string]bool{}
		for _, hostID := range placement.HostIDs {
			if !knownHosts[hostID] || seen[hostID] {
				return fmt.Errorf("placement for %s has invalid or repeated host %q", sizing.Component, hostID)
			}
			seen[hostID] = true
			usedCPU[hostID] += placement.VCPUPerNode
			usedMemory[hostID] += placement.MemoryGiBPerNode
		}
	}
	for hostID, host := range hostCapacity {
		if usedCPU[hostID] > host.CPUCores || usedMemory[hostID] > host.MemoryGiB {
			return fmt.Errorf("placement exceeds capacity of host %s", hostID)
		}
	}
	if arch.Placements[0].Capacity.Objects < estate.Workload.OperationsObjects || arch.Placements[0].Capacity.Metrics < estate.Workload.OperationsMetrics {
		return fmt.Errorf("VCF Operations capacity is below workload")
	}
	if arch.Placements[1].Capacity.AutomationConcurrentRequests < estate.Workload.AutomationConcurrentRequests {
		return fmt.Errorf("VCF Automation capacity is below workload")
	}
	if arch.Placements[2].Capacity.IngestGiBPerDay < estate.Workload.LogIngestGiBPerDay {
		return fmt.Errorf("VCF Operations for Logs capacity is below workload")
	}

	if len(plan.Migrations) != len(snapshot.Products) || len(plan.Migrations) != len(estate.Products) {
		return fmt.Errorf("every source product must have exactly one migration")
	}
	for i, rule := range snapshot.Products {
		migration := plan.Migrations[i]
		product := estate.Products[i]
		if migration.Order != i+1 || migration.Order != rule.Order {
			return fmt.Errorf("migration order is not contiguous at index %d", i)
		}
		if product.InventoryID != rule.InventoryID || product.Name != rule.SourceName || product.Version != rule.SourceVersion {
			return fmt.Errorf("fixture and snapshot disagree for migration %d", i+1)
		}
		if migration.Source != (architecture.ProductRef{InventoryID: rule.InventoryID, Name: rule.SourceName, Version: rule.SourceVersion}) {
			return fmt.Errorf("migration %d source is incomplete", i+1)
		}
		if migration.Target != (architecture.ProductRef{Name: rule.TargetComponent, Version: rule.TargetVersion}) || migration.Method != rule.MigrationMethod {
			return fmt.Errorf("migration %d target or method mismatch", i+1)
		}
		if !reflect.DeepEqual(migration.Path, rule.SupportedPath) || !reflect.DeepEqual(migration.Content, rule.Content) ||
			!reflect.DeepEqual(migration.Gates, rule.RequiredGates) || migration.Support != rule.Support {
			return fmt.Errorf("migration %d conflicts with pinned compatibility", i+1)
		}
		if len(migration.Path) < 2 || migration.Path[0] != migration.Source.Version || migration.Path[len(migration.Path)-1] != migration.Target.Version {
			return fmt.Errorf("migration %d path endpoints are invalid", i+1)
		}
	}
	return nil
}
