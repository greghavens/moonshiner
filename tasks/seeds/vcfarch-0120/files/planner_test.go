package vcfarch

import (
	"os"
	"testing"
)

func loadFixtures(t *testing.T) (Inventory, CompatibilitySnapshot) {
	t.Helper()
	invFile, err := os.Open("fixtures/estate_inventory.json")
	if err != nil {
		t.Fatal(err)
	}
	defer invFile.Close()
	inventory, err := LoadInventory(invFile)
	if err != nil {
		t.Fatal(err)
	}
	snapshotFile, err := os.Open("fixtures/compatibility_snapshot.json")
	if err != nil {
		t.Fatal(err)
	}
	defer snapshotFile.Close()
	snapshot, err := LoadCompatibility(snapshotFile)
	if err != nil {
		t.Fatal(err)
	}
	return inventory, snapshot
}

func TestBuildTable(t *testing.T) {
	inventory, snapshot := loadFixtures(t)
	tests := []struct {
		name    string
		mutate  func(*Inventory, *CompatibilitySnapshot)
		wantErr bool
	}{
		{name: "supported estate"},
		{name: "below minimum hosts", mutate: func(i *Inventory, _ *CompatibilitySnapshot) {
			i.Design.Hosts = i.Design.Hosts[:3]
		}, wantErr: true},
		{name: "unreachable component target", mutate: func(_ *Inventory, s *CompatibilitySnapshot) {
			s.Components[0].UpgradeEdges = nil
		}, wantErr: true},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			i := inventory
			i.Design.Hosts = append([]string(nil), inventory.Design.Hosts...)
			s := snapshot
			s.Components = append([]ComponentRule(nil), snapshot.Components...)
			for n := range s.Components {
				s.Components[n].UpgradeEdges = append([]UpgradeEdge(nil), snapshot.Components[n].UpgradeEdges...)
			}
			if tc.mutate != nil {
				tc.mutate(&i, &s)
			}
			artifact, err := Build(i, s)
			if (err != nil) != tc.wantErr {
				t.Fatalf("Build() error = %v, wantErr %v", err, tc.wantErr)
			}
			if !tc.wantErr {
				if got := len(artifact.HostSpecs); got != snapshot.Architecture.MinimumHostCount {
					t.Fatalf("host count = %d, want %d", got, snapshot.Architecture.MinimumHostCount)
				}
				if got := artifact.MigrationPlan.EstateID; got != inventory.EstateID {
					t.Fatalf("estate ID = %q, want %q", got, inventory.EstateID)
				}
			}
		})
	}
}
