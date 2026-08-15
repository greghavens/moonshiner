package verifier_test

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"vcfmigration/migrationplan"
)

const projectRoot = ".."

func readJSON(t *testing.T, path string, dst any) {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(projectRoot, path))
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

type inventory struct {
	SchemaVersion string `json:"schema_version"`
	InventoryID   string `json:"inventory_id"`
	Fleet         struct {
		ID               string `json:"id"`
		Version          string `json:"version"`
		ManagementDomain struct {
			ID               string `json:"id"`
			ClusterID        string `json:"cluster_id"`
			ChangeConstraint string `json:"change_constraint"`
			ExistingControl  string `json:"existing_control_plane"`
		} `json:"management_domain"`
		Workload struct {
			ID              string   `json:"id"`
			VCenterID       string   `json:"vcenter_id"`
			VCenterVersion  string   `json:"vcenter_version"`
			ClusterID       string   `json:"cluster_id"`
			NetworkID       string   `json:"network_id"`
			FaultDomains    int      `json:"fault_domains"`
			FreeCapacity    capacity `json:"free_capacity"`
			ImportPrechecks []string `json:"import_prechecks"`
		} `json:"workload_domain_candidate"`
	} `json:"fleet"`
	Products []inventoryProduct `json:"products"`
}

type capacity struct {
	VCPU       int `json:"vcpu"`
	MemoryGiB  int `json:"memory_gib"`
	StorageGiB int `json:"storage_gib"`
}

type inventoryProduct struct {
	ID               string          `json:"id"`
	Product          string          `json:"product"`
	Version          string          `json:"version"`
	CurrentVCenterID string          `json:"current_vcenter_id"`
	ManagedBy        string          `json:"managed_by,omitempty"`
	Demand           map[string]int  `json:"demand,omitempty"`
	Topology         map[string]any  `json:"topology,omitempty"`
	Items            []inventoryItem `json:"items"`
}

type inventoryItem struct {
	ID   string `json:"id"`
	Kind string `json:"kind"`
}

type snapshot struct {
	SchemaVersion string `json:"schema_version"`
	SnapshotID    string `json:"snapshot_id"`
	AsOf          string `json:"as_of"`
	InventoryID   string `json:"inventory_id"`
	Architecture  struct {
		PlanID                 string `json:"plan_id"`
		ManagementDomainID     string `json:"management_domain_id"`
		ManagementDomainChange string `json:"management_domain_change"`
		WorkloadDomainID       string `json:"workload_domain_id"`
	} `json:"architecture"`
	ProductRules       []productRule             `json:"product_rules"`
	RequiredPlacements []migrationplan.Placement `json:"required_placements"`
	RequiredGates      []requiredGate            `json:"required_gates"`
	RequiredSteps      []requiredStep            `json:"required_steps"`
}

type productRule struct {
	SourceID            string            `json:"source_id"`
	SourceProduct       string            `json:"source_product"`
	SourceVersion       string            `json:"source_version"`
	TargetComponent     string            `json:"target_component"`
	TargetVersion       string            `json:"target_version"`
	MigrationMode       string            `json:"migration_mode"`
	EndOfGeneralSupport string            `json:"end_of_general_support"`
	ItemDispositions    map[string]string `json:"item_dispositions"`
}

type requiredGate struct {
	ID   string `json:"id"`
	Kind string `json:"kind"`
}

type requiredStep struct {
	ID       string   `json:"id"`
	Requires []string `json:"requires"`
	Produces []string `json:"produces"`
}

func TestPlanSemantics(t *testing.T) {
	var inv inventory
	var snap snapshot
	var artifact migrationplan.Plan
	readJSON(t, "fixtures/estate.json", &inv)
	readJSON(t, "spec/compatibility-snapshot.json", &snap)
	readJSON(t, "migration-plan.json", &artifact)

	t.Run("builder matches committed artifact", func(t *testing.T) {
		got, err := migrationplan.Build(
			filepath.Join(projectRoot, "fixtures/estate.json"),
			filepath.Join(projectRoot, "spec/compatibility-snapshot.json"),
		)
		if err != nil {
			t.Fatalf("Build: %v", err)
		}
		if !reflect.DeepEqual(got, artifact) {
			t.Fatal("Build output differs from migration-plan.json")
		}
	})

	t.Run("authority identities and immutable domain", func(t *testing.T) {
		if artifact.SchemaVersion != "1.0" || artifact.InventoryID != inv.InventoryID || artifact.SnapshotID != snap.SnapshotID || artifact.PlanID != snap.Architecture.PlanID {
			t.Fatalf("artifact authority identifiers do not match fixture and snapshot")
		}
		want := migrationplan.Objective{
			ManagementDomainID:     snap.Architecture.ManagementDomainID,
			ManagementDomainChange: snap.Architecture.ManagementDomainChange,
			WorkloadDomainID:       snap.Architecture.WorkloadDomainID,
		}
		if artifact.Objective != want {
			t.Fatalf("objective = %#v, want %#v", artifact.Objective, want)
		}
		if inv.Fleet.ManagementDomain.ChangeConstraint != "no-component-version-placement-or-capacity-change" {
			t.Fatal("fixture's management-domain invariant is missing")
		}
	})

	t.Run("source mappings and exhaustive dispositions", func(t *testing.T) {
		if len(artifact.Sources) != len(snap.ProductRules) || len(inv.Products) != len(snap.ProductRules) {
			t.Fatalf("sources=%d products=%d rules=%d", len(artifact.Sources), len(inv.Products), len(snap.ProductRules))
		}
		products := make(map[string]inventoryProduct, len(inv.Products))
		for _, p := range inv.Products {
			products[p.ID] = p
		}
		for i, rule := range snap.ProductRules {
			got := artifact.Sources[i]
			p, ok := products[rule.SourceID]
			if !ok {
				t.Fatalf("rule source %q absent from inventory", rule.SourceID)
			}
			if got.SourceID != rule.SourceID || got.SourceProduct != p.Product || got.SourceProduct != rule.SourceProduct || got.SourceVersion != p.Version || got.SourceVersion != rule.SourceVersion || got.TargetComponent != rule.TargetComponent || got.TargetVersion != rule.TargetVersion || got.MigrationMode != rule.MigrationMode {
				t.Fatalf("source mapping %q does not match fixture/snapshot", rule.SourceID)
			}
			if got.Support.EndOfGeneralSupport != rule.EndOfGeneralSupport || got.Support.StatusAtSnapshot != "within-general-support" || !strings.Contains(got.Support.Boundary, rule.EndOfGeneralSupport) {
				t.Fatalf("support boundary for %q is incomplete: %#v", rule.SourceID, got.Support)
			}
			if len(got.Items) != len(p.Items) || len(got.Items) != len(rule.ItemDispositions) {
				t.Fatalf("%q item dispositions are not exhaustive", rule.SourceID)
			}
			seen := map[string]bool{}
			for _, item := range got.Items {
				wantDisposition, exists := rule.ItemDispositions[item.ItemID]
				if !exists || item.Disposition != wantDisposition || seen[item.ItemID] {
					t.Fatalf("unexpected disposition for %q: %q", item.ItemID, item.Disposition)
				}
				if !concreteDispositionMethod(item.Disposition, item.Method) {
					t.Fatalf("item %q does not have a concrete %s method: %q", item.ItemID, item.Disposition, item.Method)
				}
				seen[item.ItemID] = true
			}
			for _, item := range p.Items {
				if !seen[item.ID] {
					t.Fatalf("inventoried item %q has no disposition", item.ID)
				}
			}
		}
	})

	t.Run("placement sizing and workload capacity", func(t *testing.T) {
		if !reflect.DeepEqual(artifact.Placements, snap.RequiredPlacements) {
			t.Fatalf("placements do not match pinned sizing authority")
		}
		var used capacity
		for _, p := range artifact.Placements {
			if p.DomainID != inv.Fleet.Workload.ID || p.ClusterID != inv.Fleet.Workload.ClusterID || p.NetworkID != inv.Fleet.Workload.NetworkID {
				t.Fatalf("%s is not wholly placed in the added workload domain", p.Component)
			}
			if p.DomainID == inv.Fleet.ManagementDomain.ID || p.ClusterID == inv.Fleet.ManagementDomain.ClusterID {
				t.Fatalf("%s disturbs the management domain", p.Component)
			}
			used.VCPU += p.Topology.Nodes * p.Topology.VCPUPerNode
			used.MemoryGiB += p.Topology.Nodes * p.Topology.MemoryGiBPerNode
			used.StorageGiB += p.Topology.Nodes * p.Topology.DiskGiBPerNode
		}
		free := inv.Fleet.Workload.FreeCapacity
		if used.VCPU > free.VCPU || used.MemoryGiB > free.MemoryGiB || used.StorageGiB > free.StorageGiB {
			t.Fatalf("target sizing exceeds workload capacity: used=%+v free=%+v", used, free)
		}
	})

	t.Run("ordered steps and gates", func(t *testing.T) {
		if len(artifact.Gates) != len(snap.RequiredGates) {
			t.Fatalf("gates=%d, want %d", len(artifact.Gates), len(snap.RequiredGates))
		}
		gateKinds := make(map[string]string, len(artifact.Gates))
		for _, gate := range artifact.Gates {
			if _, duplicate := gateKinds[gate.ID]; duplicate {
				t.Fatalf("duplicate gate %q", gate.ID)
			}
			gateKinds[gate.ID] = gate.Kind
		}
		for _, want := range snap.RequiredGates {
			if gateKinds[want.ID] != want.Kind {
				t.Fatalf("gate %q kind=%q, want %q", want.ID, gateKinds[want.ID], want.Kind)
			}
		}
		if len(artifact.Steps) != len(snap.RequiredSteps) {
			t.Fatalf("steps=%d, want %d", len(artifact.Steps), len(snap.RequiredSteps))
		}
		produced := map[string]bool{}
		for i, want := range snap.RequiredSteps {
			got := artifact.Steps[i]
			if got.Order != i+1 || got.ID != want.ID || !reflect.DeepEqual(got.Requires, want.Requires) || !reflect.DeepEqual(got.Produces, want.Produces) {
				t.Fatalf("step %d does not match pinned order/dependencies: %#v", i+1, got)
			}
			for _, id := range append(append([]string{}, got.Requires...), got.Produces...) {
				if _, exists := gateKinds[id]; !exists {
					t.Fatalf("step %q references unknown gate %q", got.ID, id)
				}
			}
			for _, id := range got.Requires {
				if gateKinds[id] != "precondition" && !produced[id] {
					t.Fatalf("step %q consumes %q before an earlier step produces it", got.ID, id)
				}
			}
			for _, id := range got.Produces {
				produced[id] = true
			}
		}
	})
}

func concreteDispositionMethod(disposition, method string) bool {
	if len(strings.Fields(method)) < 6 {
		return false
	}
	lower := strings.ToLower(method)
	var actions []string
	switch disposition {
	case "carry":
		actions = []string{"export", "import", "preserve", "carry", "copy", "transfer", "include"}
	case "recreate":
		actions = []string{"create", "recreate", "configure", "re-enter", "repoint"}
	case "abandon":
		actions = []string{"do not", "remove", "leave", "exclude", "retire"}
	default:
		return false
	}
	for _, action := range actions {
		if strings.Contains(lower, action) {
			return true
		}
	}
	return false
}
