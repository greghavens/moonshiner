package vcfarch

import (
	"encoding/json"
	"reflect"
	"testing"
)

func fixtureInputs(t *testing.T) Inputs {
	t.Helper()
	in, err := LoadInputs("fixtures/requirements.json", "fixtures/estate.json", "fixtures/compatibility-snapshot.json")
	if err != nil {
		t.Fatal(err)
	}
	return in
}

func cloneInputs(t *testing.T, in Inputs) Inputs {
	t.Helper()
	b, err := json.Marshal(in)
	if err != nil {
		t.Fatal(err)
	}
	var clone Inputs
	if err := json.Unmarshal(b, &clone); err != nil {
		t.Fatal(err)
	}
	return clone
}

func TestBuildGreenfield(t *testing.T) {
	base := fixtureInputs(t)
	tests := []struct {
		name    string
		mutate  func(*Inputs)
		wantErr bool
	}{
		{name: "supported design"},
		{name: "unsupported target", mutate: func(in *Inputs) { in.Requirements.TargetVersion = "9.2.0.0" }, wantErr: true},
		{name: "wrong site roles", mutate: func(in *Inputs) {
			in.Requirements.Sites[0].Role, in.Requirements.Sites[1].Role = "RECOVERY", "PRIMARY"
		}, wantErr: true},
		{name: "undersized management domain", mutate: func(in *Inputs) {
			in.Requirements.Sites[0].ManagementHosts = in.Requirements.Sites[0].ManagementHosts[:5]
		}, wantErr: true},
		{name: "undersized primary workload domain", mutate: func(in *Inputs) {
			in.Requirements.Sites[0].WorkloadHosts = in.Requirements.Sites[0].WorkloadHosts[:5]
		}, wantErr: true},
		{name: "undersized recovery workload domain", mutate: func(in *Inputs) {
			in.Requirements.Sites[1].WorkloadHosts = in.Requirements.Sites[1].WorkloadHosts[:5]
		}, wantErr: true},
		{name: "operations exceed pinned size", mutate: func(in *Inputs) {
			in.Requirements.Capacity.MonitoredObjects = in.Snapshot.Sizing.Operations.MaximumManagedObjects + 1
		}, wantErr: true},
		{name: "automation machines exceed pinned size", mutate: func(in *Inputs) {
			in.Requirements.Capacity.AutomationManagedMachines = in.Snapshot.Sizing.Automation.MaximumManagedMachines + 1
		}, wantErr: true},
		{name: "automation concurrency exceeds pinned size", mutate: func(in *Inputs) {
			in.Requirements.Capacity.AutomationConcurrentDeploys = in.Snapshot.Sizing.Automation.MaximumConcurrentDeploys + 1
		}, wantErr: true},
		{name: "logs exceed pinned size", mutate: func(in *Inputs) {
			in.Requirements.Capacity.LogIngestGiBPerDay = in.Snapshot.Sizing.LogManagement.MaximumIngestGiBPerDay + 1
		}, wantErr: true},
		{name: "log retention exceeds pinned size", mutate: func(in *Inputs) {
			in.Requirements.Capacity.LogHotRetentionDays = in.Snapshot.Sizing.LogManagement.MaximumHotRetentionDays + 1
		}, wantErr: true},
		{name: "site latency exceeds design", mutate: func(in *Inputs) {
			in.Requirements.Availability.InterSiteRTTMs = in.Snapshot.Sizing.MaximumInterSiteRTTMs + 1
		}, wantErr: true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			in := cloneInputs(t, base)
			if tc.mutate != nil {
				tc.mutate(&in)
			}
			sddc, architecture, err := BuildGreenfield(in)
			if (err != nil) != tc.wantErr {
				t.Fatalf("BuildGreenfield() error = %v, wantErr %v", err, tc.wantErr)
			}
			if tc.wantErr {
				return
			}
			if sddc["version"] != in.Requirements.TargetVersion {
				t.Fatalf("SddcSpec version = %v", sddc["version"])
			}
			if architecture.Target != in.Snapshot.SupportedCombination {
				t.Fatalf("target combination = %#v", architecture.Target)
			}
			if architecture.Capacity != in.Requirements.Capacity {
				t.Fatalf("capacity = %#v", architecture.Capacity)
			}
			if len(architecture.Domains) != 3 || len(architecture.Products) != 3 {
				t.Fatalf("got %d domains and %d products", len(architecture.Domains), len(architecture.Products))
			}
		})
	}
}

func TestBuildMigrationPlan(t *testing.T) {
	base := fixtureInputs(t)
	tests := []struct {
		name    string
		mutate  func(*Inputs)
		wantErr bool
	}{
		{name: "complete inventory"},
		{name: "missing inventory component", mutate: func(in *Inputs) { in.Estate.Components = in.Estate.Components[1:] }, wantErr: true},
		{name: "duplicate inventory component", mutate: func(in *Inputs) { in.Estate.Components = append(in.Estate.Components, in.Estate.Components[0]) }, wantErr: true},
		{name: "version drift", mutate: func(in *Inputs) { in.Estate.Components[0].Version = "9.0.1" }, wantErr: true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			in := cloneInputs(t, base)
			if tc.mutate != nil {
				tc.mutate(&in)
			}
			plan, err := BuildMigrationPlan(in)
			if (err != nil) != tc.wantErr {
				t.Fatalf("BuildMigrationPlan() error = %v, wantErr %v", err, tc.wantErr)
			}
			if tc.wantErr {
				return
			}
			if len(plan.Steps) != len(in.Estate.Components) {
				t.Fatalf("steps = %d, inventory = %d", len(plan.Steps), len(in.Estate.Components))
			}
			for i, rule := range in.Snapshot.UpgradePlan {
				step := plan.Steps[i]
				if step.Order != i+1 || step.Component != rule.Component || !reflect.DeepEqual(step.Gates, rule.Gates) {
					t.Fatalf("step %d = %#v, rule = %#v", i+1, step, rule)
				}
			}
		})
	}
}
