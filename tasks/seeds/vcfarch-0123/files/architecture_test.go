package vcfarch

import (
	"bytes"
	"encoding/json"
	"os"
	"sync"
	"testing"
)

func inputs(t *testing.T) ([]byte, []byte) {
	t.Helper()
	estate, err := os.ReadFile("fixtures/estate.json")
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := os.ReadFile("snapshots/compatibility-2026-05-12.json")
	if err != nil {
		t.Fatal(err)
	}
	return estate, snapshot
}

func withThroughput(t *testing.T, raw []byte, throughput float64) []byte {
	t.Helper()
	return mutateJSON(t, raw, func(value map[string]any) {
		value["edgeRequirement"].(map[string]any)["requiredNorthSouthGbps"] = throughput
	})
}

func mutateJSON(t *testing.T, raw []byte, mutate func(map[string]any)) []byte {
	t.Helper()
	var value map[string]any
	if err := json.Unmarshal(raw, &value); err != nil {
		t.Fatal(err)
	}
	mutate(value)
	changed, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return changed
}

func TestBuildValidation(t *testing.T) {
	estate, snapshot := inputs(t)
	unknownVersion := bytes.Replace(
		estate,
		[]byte(`"8.0.3.00600-24853646"`),
		[]byte(`"8.0.3.99999-00000000"`),
		1,
	)
	unknownGate := mutateJSON(t, snapshot, func(value map[string]any) {
		paths := value["supportedPaths"].([]any)
		path := paths[0].(map[string]any)
		path["requiredGates"] = append(path["requiredGates"].([]any), "not-a-declared-gate")
	})
	cyclicPrecedence := mutateJSON(t, snapshot, func(value map[string]any) {
		relations := value["precedence"].([]any)
		value["precedence"] = append(relations, map[string]any{
			"before": "chi-vsan01",
			"after":  "chi-vc01",
		})
	})
	duplicateComponent := mutateJSON(t, estate, func(value map[string]any) {
		components := value["components"].([]any)
		value["components"] = append(components, components[0])
	})
	undersizedUplink := mutateJSON(t, estate, func(value map[string]any) {
		requirement := value["edgeRequirement"].(map[string]any)
		uplinks := requirement["availableUplinks"].([]any)
		uplinks[0].(map[string]any)["linkGbps"] = 10
	})

	tests := []struct {
		name     string
		estate   []byte
		snapshot []byte
		wantErr  bool
	}{
		{name: "fixture", estate: estate, snapshot: snapshot},
		{name: "malformed estate", estate: []byte(`{`), snapshot: snapshot, wantErr: true},
		{name: "malformed snapshot", estate: estate, snapshot: []byte(`{`), wantErr: true},
		{name: "unsupported component path", estate: unknownVersion, snapshot: snapshot, wantErr: true},
		{name: "unsatisfied throughput", estate: withThroughput(t, estate, 101), snapshot: snapshot, wantErr: true},
		{name: "unknown required gate", estate: estate, snapshot: unknownGate, wantErr: true},
		{name: "cyclic component precedence", estate: estate, snapshot: cyclicPrecedence, wantErr: true},
		{name: "duplicate component", estate: duplicateComponent, snapshot: snapshot, wantErr: true},
		{name: "undersized failure uplink", estate: undersizedUplink, snapshot: snapshot, wantErr: true},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			result, err := Build(test.estate, test.snapshot)
			if (err != nil) != test.wantErr {
				t.Fatalf("Build() error = %v, wantErr %v", err, test.wantErr)
			}
			if !test.wantErr && !json.Valid(result) {
				t.Fatal("Build() returned invalid JSON")
			}
		})
	}
}

func TestBuildDerivesArtifactFromInputs(t *testing.T) {
	estate, snapshot := inputs(t)
	changedEstate := mutateJSON(t, estate, func(value map[string]any) {
		value["estateId"] = "alternate-estate"
		value["fleetId"] = "alternate-fleet"
		greenfield := value["greenfield"].(map[string]any)
		greenfield["sddcId"] = "chi02-m02"
		greenfield["managementPoolName"] = "chi02-m02-network-pool"
		greenfield["hosts"].([]any)[0] = "chi02-m02-esx01"
		greenfield["vcenter"].(map[string]any)["hostname"] = "chi02-m02-vc01.vcf.example"
		greenfield["networks"].([]any)[0].(map[string]any)["vlanId"] = 1711
	})

	result, err := Build(changedEstate, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	var derived struct {
		Greenfield struct {
			SddcID             string `json:"sddcId"`
			ManagementPoolName string `json:"managementPoolName"`
			HostSpecs          []struct {
				Hostname string `json:"hostname"`
			} `json:"hostSpecs"`
			VcenterSpec struct {
				Hostname string `json:"vcenterHostname"`
			} `json:"vcenterSpec"`
			NetworkSpecs []struct {
				VLANID int `json:"vlanId"`
			} `json:"networkSpecs"`
		} `json:"greenfield"`
		MigrationPlan struct {
			EstateID string `json:"estateId"`
			FleetID  string `json:"fleetId"`
		} `json:"migrationPlan"`
	}
	if err := json.Unmarshal(result, &derived); err != nil {
		t.Fatal(err)
	}
	if derived.MigrationPlan.EstateID != "alternate-estate" || derived.MigrationPlan.FleetID != "alternate-fleet" ||
		derived.Greenfield.SddcID != "chi02-m02" || derived.Greenfield.ManagementPoolName != "chi02-m02-network-pool" ||
		len(derived.Greenfield.HostSpecs) == 0 || derived.Greenfield.HostSpecs[0].Hostname != "chi02-m02-esx01" ||
		derived.Greenfield.VcenterSpec.Hostname != "chi02-m02-vc01.vcf.example" ||
		len(derived.Greenfield.NetworkSpecs) == 0 || derived.Greenfield.NetworkSpecs[0].VLANID != 1711 {
		t.Fatal("Build did not derive greenfield and migration identity values from the estate input")
	}

	changedPath := mutateJSON(t, snapshot, func(value map[string]any) {
		path := value["supportedPaths"].([]any)[0].(map[string]any)["path"].([]any)
		path[1] = "8.8.0.4"
	})
	result, err = Build(estate, changedPath)
	if err != nil {
		t.Fatal(err)
	}
	var plan struct {
		MigrationPlan struct {
			Steps []struct {
				ComponentID string   `json:"componentId"`
				Path        []string `json:"path"`
				Order       int      `json:"order"`
			} `json:"steps"`
		} `json:"migrationPlan"`
	}
	if err := json.Unmarshal(result, &plan); err != nil {
		t.Fatal(err)
	}
	if got := plan.MigrationPlan.Steps[0].Path; len(got) != 3 || got[1] != "8.8.0.4" {
		t.Fatalf("migration path = %v, want snapshot-derived intermediate", got)
	}

	changedOrder := mutateJSON(t, snapshot, func(value map[string]any) {
		relations := value["precedence"].([]any)
		value["precedence"] = append(relations, map[string]any{
			"before": "dal-vsan01",
			"after":  "chi-nsx01",
		})
	})
	result, err = Build(estate, changedOrder)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(result, &plan); err != nil {
		t.Fatal(err)
	}
	positions := make(map[string]int, len(plan.MigrationPlan.Steps))
	for _, step := range plan.MigrationPlan.Steps {
		positions[step.ComponentID] = step.Order
	}
	if positions["dal-vsan01"] >= positions["chi-nsx01"] {
		t.Fatal("Build did not derive migration ordering from snapshot precedence")
	}
}

func TestEdgeFormFactorSelection(t *testing.T) {
	estate, snapshot := inputs(t)
	tests := []struct {
		name       string
		throughput float64
		want       string
	}{
		{name: "medium boundary", throughput: 2, want: "medium"},
		{name: "large", throughput: 8, want: "large"},
		{name: "xlarge estate requirement", throughput: 18, want: "xlarge"},
		{name: "xlarge link boundary", throughput: 25, want: "xlarge"},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			result, err := Build(withThroughput(t, estate, test.throughput), snapshot)
			if err != nil {
				t.Fatal(err)
			}
			var artifact struct {
				EdgeDesign struct {
					FormFactor string `json:"formFactor"`
				} `json:"edgeDesign"`
			}
			if err := json.Unmarshal(result, &artifact); err != nil {
				t.Fatal(err)
			}
			if artifact.EdgeDesign.FormFactor != test.want {
				t.Fatalf("form factor = %q, want %q", artifact.EdgeDesign.FormFactor, test.want)
			}
		})
	}
}

func TestBuildIsDeterministicAndRaceSafe(t *testing.T) {
	estate, snapshot := inputs(t)
	want, err := Build(estate, snapshot)
	if err != nil {
		t.Fatal(err)
	}

	const workers = 16
	results := make(chan []byte, workers)
	errors := make(chan error, workers)
	var group sync.WaitGroup
	for range workers {
		group.Add(1)
		go func() {
			defer group.Done()
			got, err := Build(estate, snapshot)
			if err != nil {
				errors <- err
				return
			}
			results <- got
		}()
	}
	group.Wait()
	close(results)
	close(errors)

	for err := range errors {
		t.Error(err)
	}
	for got := range results {
		if !bytes.Equal(got, want) {
			t.Error("concurrent Build result was not deterministic")
		}
	}
}

func TestCheckedInArtifactMatchesBuild(t *testing.T) {
	estate, snapshot := inputs(t)
	want, err := Build(estate, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile("architecture.json")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, want) {
		t.Fatal("architecture.json is not synchronized with Build output")
	}
}
