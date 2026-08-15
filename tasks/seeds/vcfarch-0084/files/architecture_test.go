package vcfarch_test

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"

	"vcfarch"
)

func TestBuildMatchesCommittedArtifact(t *testing.T) {
	var inventory vcfarch.Inventory
	var snapshot vcfarch.CompatibilitySnapshot
	var committed vcfarch.Architecture
	readFixture(t, "testdata/estate.json", &inventory)
	readFixture(t, "testdata/compatibility-snapshot.json", &snapshot)
	readFixture(t, "architecture.json", &committed)

	built, err := vcfarch.Build(inventory, snapshot)
	if err != nil {
		t.Fatalf("Build() error: %v", err)
	}
	if !reflect.DeepEqual(built, committed) {
		t.Fatal("Build() output differs from architecture.json")
	}
}

func readFixture(t *testing.T, path string, destination any) {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, destination); err != nil {
		t.Fatal(err)
	}
}
