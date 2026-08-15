package vcfarch

import (
	"encoding/json"
	"os"
	"reflect"
	"sort"
	"testing"
)

const (
	scenarioFixture = "fixtures/scenario.json"
	estateFixture   = "fixtures/estate.json"
	snapshotFixture = "compatibility/vcf-9.0.0-snapshot.json"
)

func TestBuildMatchesCheckedInArtifact(t *testing.T) {
	wantBytes, err := os.ReadFile("architecture.json")
	if err != nil {
		t.Fatal(err)
	}
	var want Architecture
	if err := json.Unmarshal(wantBytes, &want); err != nil {
		t.Fatalf("decode architecture.json: %v", err)
	}

	got, err := Build(scenarioFixture, estateFixture, snapshotFixture)
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	if !reflect.DeepEqual(got, want) {
		gotJSON, _ := json.MarshalIndent(got, "", "  ")
		t.Fatalf("Build result differs from architecture.json\ngot:\n%s", gotJSON)
	}
}

func TestBuildContract(t *testing.T) {
	a, err := Build(scenarioFixture, estateFixture, snapshotFixture)
	if err != nil {
		t.Fatal(err)
	}

	checks := []struct {
		name string
		ok   func(Architecture) bool
	}{
		{"schema version", func(a Architecture) bool { return a.SchemaVersion == 1 }},
		{"stretched topology", func(a Architecture) bool { return a.Greenfield.Topology.Mode == "stretched-management-domain" }},
		{"independent witness", func(a Architecture) bool {
			w := a.Greenfield.Topology.Witness
			return w.Site == "aus-witness" && w.Dedicated && !w.MemberOfDataCluster
		}},
		{"eight data hosts", func(a Architecture) bool { return len(a.Greenfield.SddcSpec.HostSpecs) == 8 }},
		{"site loss capacity", func(a Architecture) bool { return a.Greenfield.Capacity.SurvivesDataSiteLoss }},
		{"complete migration", func(a Architecture) bool { return len(a.ExistingEstate.MigrationPlan.Steps) == 14 }},
	}
	for _, tc := range checks {
		t.Run(tc.name, func(t *testing.T) {
			if !tc.ok(a) {
				t.Fatalf("contract check %q failed", tc.name)
			}
		})
	}

	orders := make([]int, len(a.ExistingEstate.MigrationPlan.Steps))
	for i, step := range a.ExistingEstate.MigrationPlan.Steps {
		orders[i] = step.Order
	}
	if !sort.IntsAreSorted(orders) {
		t.Fatalf("migration steps are not ordered: %v", orders)
	}
}
