package vcfarch

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
	"unicode/utf8"
)

const (
	artifactPath = "architecture.json"
	openAPIPath  = "specifications/vcf-installer/vcf-installer-openapi.json"
)

// TestArchitectureArtifact deliberately validates greenfieldSddcSpec against
// the SddcSpec in the vendored installer OpenAPI document before it loads or
// evaluates either grading fixture. Live-research notes are never opened here.
func TestArchitectureArtifact(t *testing.T) {
	openAPIRaw := mustRead(t, openAPIPath)
	artifactRaw := mustRead(t, artifactPath)

	var document map[string]any
	mustJSON(t, openAPIPath, openAPIRaw, &document, false)
	var envelope map[string]json.RawMessage
	mustJSON(t, artifactPath, artifactRaw, &envelope, false)
	var artifactDocument map[string]any
	mustJSON(t, artifactPath, artifactRaw, &artifactDocument, false)
	sddcRaw, ok := envelope["greenfieldSddcSpec"]
	if !ok {
		t.Fatal("architecture.json: missing greenfieldSddcSpec")
	}
	var sddc any
	mustJSON(t, "greenfieldSddcSpec", sddcRaw, &sddc, false)
	validator := schemaValidator{document: document}
	if err := validator.validateRef("#/components/schemas/SddcSpec", sddc, "greenfieldSddcSpec"); err != nil {
		t.Fatalf("greenfieldSddcSpec does not validate against the installer SddcSpec: %v", err)
	}
	if err := validateArtifactShape(artifactDocument); err != nil {
		t.Fatalf("architecture.json does not have the fixed artifact shape: %v", err)
	}

	// Only after the installer-schema validation succeeds may fixture-backed
	// architecture checks begin.
	inventory, err := LoadInventory("estate.json")
	if err != nil {
		t.Fatalf("LoadInventory: %v", err)
	}
	snapshot, err := LoadCompatibilitySnapshot("compatibility-snapshot.json")
	if err != nil {
		t.Fatalf("LoadCompatibilitySnapshot: %v", err)
	}
	gotHash := sha256.Sum256(openAPIRaw)
	if hex.EncodeToString(gotHash[:]) != snapshot.InstallerSchema.SHA256 {
		t.Fatalf("installer schema digest does not match pinned compatibility snapshot")
	}
	var estateDocument map[string]any
	mustJSON(t, "estate.json", mustRead(t, "estate.json"), &estateDocument, false)
	if err := validateGreenfieldMapping(sddc, estateDocument["greenfieldDesign"]); err != nil {
		t.Fatalf("greenfieldSddcSpec is not populated from estate.greenfieldDesign: %v", err)
	}

	var artifact Artifact
	mustJSON(t, artifactPath, artifactRaw, &artifact, true)
	want, err := BuildArchitecture(inventory, snapshot)
	if err != nil {
		t.Fatalf("BuildArchitecture: %v", err)
	}
	if !reflect.DeepEqual(artifact, want) {
		got, _ := json.MarshalIndent(artifact, "", "  ")
		expected, _ := json.MarshalIndent(want, "", "  ")
		t.Fatalf("architecture.json differs from the package result\n--- got\n%s\n--- want\n%s", got, expected)
	}
	if artifact.SchemaVersion != "1.0" {
		t.Fatalf("schemaVersion = %q, want 1.0", artifact.SchemaVersion)
	}
	if artifact.TargetFleetVersion != snapshot.TargetFleetVersion {
		t.Fatalf("targetFleetVersion = %q, want pinned %q", artifact.TargetFleetVersion, snapshot.TargetFleetVersion)
	}
	if strings.TrimSpace(artifact.MigrationPlan.Strategy) == "" {
		t.Fatal("migrationPlan.strategy is empty")
	}

	t.Run("licensing-removes-supported-topology", func(t *testing.T) {
		existingCores := 0
		for _, cluster := range inventory.Clusters {
			existingCores += cluster.HostCount * cluster.CoresPerHost
		}
		if artifact.SelectedTopology.ID != "CONVERGE_EXISTING_MANAGEMENT" {
			t.Fatalf("selected topology = %q", artifact.SelectedTopology.ID)
		}
		if !artifact.SelectedTopology.TechnicallySupported || artifact.SelectedTopology.RequiredCores != existingCores {
			t.Fatalf("selected topology does not preserve the supported %d-core estate: %+v", existingCores, artifact.SelectedTopology)
		}
		if len(artifact.DiscardedTopologies) != 1 {
			t.Fatalf("discarded topology count = %d, want 1", len(artifact.DiscardedTopologies))
		}
		discarded := artifact.DiscardedTopologies[0]
		if discarded.ID != "NEW_DEDICATED_MANAGEMENT_DOMAIN" || !discarded.TechnicallySupported {
			t.Fatalf("wrong technically-valid topology discarded: %+v", discarded)
		}
		if discarded.ReasonCode != "LICENSE_CORE_ENTITLEMENT_EXCEEDED" || discarded.RequiredCores <= discarded.LicensedCores {
			t.Fatalf("discarded topology is not removed by entitlement: %+v", discarded)
		}

		pinned := make(map[string]Topology, len(snapshot.Topologies))
		for _, topology := range snapshot.Topologies {
			if _, duplicate := pinned[topology.ID]; duplicate {
				t.Fatalf("duplicate pinned topology %q", topology.ID)
			}
			pinned[topology.ID] = topology
		}
		decisions := append([]TopologyDecision{artifact.SelectedTopology}, artifact.DiscardedTopologies...)
		seen := make(map[string]bool, len(decisions))
		for _, decision := range decisions {
			topology, ok := pinned[decision.ID]
			if !ok {
				t.Fatalf("artifact contains unpinned topology %q", decision.ID)
			}
			if seen[decision.ID] {
				t.Fatalf("artifact contains duplicate topology decision %q", decision.ID)
			}
			seen[decision.ID] = true
			wantRequired := existingCores + topology.AdditionalCoreDemand
			if decision.TechnicallySupported != topology.TechnicallySupported ||
				decision.LicensedCores != inventory.Entitlement.LicensedPhysicalCores ||
				decision.RequiredCores != wantRequired || decision.Gate != topology.Gate {
				t.Fatalf("topology %q does not preserve pinned support/gate and core arithmetic: got %+v, want licensed=%d required=%d pinned=%+v",
					decision.ID, decision, inventory.Entitlement.LicensedPhysicalCores, wantRequired, topology)
			}
		}
		if len(seen) != len(pinned) {
			t.Fatalf("artifact records %d of %d pinned topologies", len(seen), len(pinned))
		}
	})

	t.Run("every-component-follows-pinned-route", func(t *testing.T) {
		plans := make(map[string]ComponentPlan, len(artifact.MigrationPlan.Components))
		orders := make([]int, 0)
		for _, plan := range artifact.MigrationPlan.Components {
			if _, duplicate := plans[plan.ID]; duplicate {
				t.Fatalf("duplicate migration component %q", plan.ID)
			}
			plans[plan.ID] = plan
			for _, phase := range plan.Phases {
				orders = append(orders, phase.Order)
				if len(phase.Gates) == 0 {
					t.Fatalf("component %q phase %d has no gate", plan.ID, phase.Order)
				}
			}
		}
		if len(plans) != len(inventory.Components) {
			t.Fatalf("planned components = %d, inventory components = %d", len(plans), len(inventory.Components))
		}
		for _, component := range inventory.Components {
			component := component
			t.Run(component.ID, func(t *testing.T) {
				plan, ok := plans[component.ID]
				if !ok {
					t.Fatalf("inventory component %q is absent from migration plan", component.ID)
				}
				route, ok := findRoute(snapshot.Routes, component.Type, component.Version)
				if !ok {
					t.Fatalf("fixture has no pinned route for %s %s", component.Type, component.Version)
				}
				if plan.Type != component.Type || plan.Product != component.Product || plan.CurrentVersion != component.Version {
					t.Fatalf("source identity/version mismatch: %+v", plan)
				}
				if plan.TargetProduct != route.TargetProduct || plan.TargetVersion != route.TargetVersion ||
					!reflect.DeepEqual(plan.UpgradePath, route.UpgradePath) ||
					!reflect.DeepEqual(plan.Gates, route.Gates) ||
					!reflect.DeepEqual(plan.Phases, route.Phases) {
					t.Fatalf("plan does not follow pinned route\n got: %+v\nwant: %+v", plan, route)
				}
			})
		}
		sort.Ints(orders)
		for i, order := range orders {
			if order != i+1 {
				t.Fatalf("phase orders = %v; want one total order from 1 through %d", orders, len(orders))
			}
		}
	})

	t.Run("build-uses-supplied-core-inventory", func(t *testing.T) {
		candidate := cloneJSONValue(t, inventory)
		candidate.Clusters[0].HostCount++
		existingCores := 0
		for _, cluster := range candidate.Clusters {
			existingCores += cluster.HostCount * cluster.CoresPerHost
		}
		candidate.Entitlement.LicensedPhysicalCores = existingCores
		loaded, err := LoadInventory(writeJSONValue(t, "estate.json", candidate))
		if err != nil {
			t.Fatalf("load altered valid inventory: %v", err)
		}
		got, err := BuildArchitecture(loaded, snapshot)
		if err != nil {
			t.Fatalf("build altered valid inventory: %v", err)
		}
		if got.SelectedTopology.RequiredCores != existingCores || got.SelectedTopology.LicensedCores != existingCores {
			t.Fatalf("altered inventory core arithmetic was ignored: %+v", got.SelectedTopology)
		}
	})

	t.Run("build-uses-supplied-route", func(t *testing.T) {
		candidateInventory := cloneJSONValue(t, inventory)
		candidateSnapshot := cloneJSONValue(t, snapshot)
		const alternativeVersion = "4.2.2.2"
		foundComponent := false
		for i := range candidateInventory.Components {
			if candidateInventory.Components[i].Type == "nsx" {
				candidateInventory.Components[i].Version = alternativeVersion
				foundComponent = true
			}
		}
		foundRoute := false
		var expectedRoute CompatibilityRoute
		for i := range candidateSnapshot.Routes {
			route := &candidateSnapshot.Routes[i]
			if route.ComponentType != "nsx" {
				continue
			}
			oldVersion := route.CurrentVersion
			route.CurrentVersion = alternativeVersion
			route.UpgradePath[0] = alternativeVersion
			for j := range route.Phases {
				if route.Phases[j].FromVersion == oldVersion {
					route.Phases[j].FromVersion = alternativeVersion
				}
				if route.Phases[j].ToVersion == oldVersion {
					route.Phases[j].ToVersion = alternativeVersion
				}
			}
			expectedRoute = *route
			foundRoute = true
		}
		if !foundComponent || !foundRoute {
			t.Fatal("fixtures do not contain the NSX component and route used by the variation")
		}
		loadedInventory, err := LoadInventory(writeJSONValue(t, "estate.json", candidateInventory))
		if err != nil {
			t.Fatalf("load altered valid inventory: %v", err)
		}
		loadedSnapshot, err := LoadCompatibilitySnapshot(writeJSONValue(t, "compatibility-snapshot.json", candidateSnapshot))
		if err != nil {
			t.Fatalf("load altered valid snapshot: %v", err)
		}
		got, err := BuildArchitecture(loadedInventory, loadedSnapshot)
		if err != nil {
			t.Fatalf("build altered valid route: %v", err)
		}
		var gotPlan *ComponentPlan
		for i := range got.MigrationPlan.Components {
			if got.MigrationPlan.Components[i].Type == "nsx" {
				gotPlan = &got.MigrationPlan.Components[i]
			}
		}
		if gotPlan == nil || gotPlan.CurrentVersion != alternativeVersion ||
			gotPlan.TargetProduct != expectedRoute.TargetProduct || gotPlan.TargetVersion != expectedRoute.TargetVersion ||
			!reflect.DeepEqual(gotPlan.UpgradePath, expectedRoute.UpgradePath) ||
			!reflect.DeepEqual(gotPlan.Gates, expectedRoute.Gates) ||
			!reflect.DeepEqual(gotPlan.Phases, expectedRoute.Phases) {
			t.Fatalf("BuildArchitecture ignored the supplied alternate pinned route: got %+v, want %+v", gotPlan, expectedRoute)
		}
	})

	t.Run("deterministic", func(t *testing.T) {
		again, err := BuildArchitecture(inventory, snapshot)
		if err != nil {
			t.Fatal(err)
		}
		firstJSON, err := json.Marshal(want)
		if err != nil {
			t.Fatal(err)
		}
		againJSON, err := json.Marshal(again)
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(firstJSON, againJSON) {
			t.Fatal("BuildArchitecture is not deterministic")
		}
		firstPath := filepath.Join(t.TempDir(), "first.json")
		secondPath := filepath.Join(t.TempDir(), "second.json")
		if err := WriteArtifact(firstPath, want); err != nil {
			t.Fatal(err)
		}
		if err := WriteArtifact(secondPath, again); err != nil {
			t.Fatal(err)
		}
		firstBytes := mustRead(t, firstPath)
		secondBytes := mustRead(t, secondPath)
		if !bytes.Equal(firstBytes, secondBytes) {
			t.Fatal("WriteArtifact is not byte-for-byte deterministic")
		}
		if !bytes.Equal(firstBytes, artifactRaw) {
			t.Fatal("checked-in architecture.json is not the current WriteArtifact output")
		}

		variant := cloneJSONValue(t, want)
		variant.MigrationPlan.Strategy += "_SERIALIZATION_CHECK"
		variantPath := filepath.Join(t.TempDir(), "variant.json")
		if err := WriteArtifact(variantPath, variant); err != nil {
			t.Fatal(err)
		}
		var decoded Artifact
		mustJSON(t, variantPath, mustRead(t, variantPath), &decoded, true)
		if !reflect.DeepEqual(decoded, variant) {
			t.Fatal("WriteArtifact did not serialize the supplied artifact")
		}
	})
}

func TestStrictInputLoading(t *testing.T) {
	inventoryJSON := mustRead(t, "estate.json")
	snapshotJSON := mustRead(t, "compatibility-snapshot.json")
	withUnknown := func(data []byte) string {
		t.Helper()
		var object map[string]any
		mustJSON(t, "valid input", data, &object, false)
		object["unexpectedVerifierField"] = true
		result, err := json.Marshal(object)
		if err != nil {
			t.Fatal(err)
		}
		return string(result)
	}
	tests := []struct {
		name string
		body string
		load func(string) error
	}{
		{
			name: "inventory unknown field",
			body: withUnknown(inventoryJSON),
			load: func(path string) error { _, err := LoadInventory(path); return err },
		},
		{
			name: "inventory trailing value",
			body: string(inventoryJSON) + "\n{}",
			load: func(path string) error { _, err := LoadInventory(path); return err },
		},
		{
			name: "inventory semantic validation",
			body: `{}`,
			load: func(path string) error { _, err := LoadInventory(path); return err },
		},
		{
			name: "snapshot unknown field",
			body: withUnknown(snapshotJSON),
			load: func(path string) error { _, err := LoadCompatibilitySnapshot(path); return err },
		},
		{
			name: "snapshot trailing value",
			body: string(snapshotJSON) + "\n{}",
			load: func(path string) error { _, err := LoadCompatibilitySnapshot(path); return err },
		},
		{
			name: "snapshot semantic validation",
			body: `{}`,
			load: func(path string) error { _, err := LoadCompatibilitySnapshot(path); return err },
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "input.json")
			if err := os.WriteFile(path, []byte(test.body), 0o600); err != nil {
				t.Fatal(err)
			}
			err := test.load(path)
			if err == nil || strings.TrimSpace(err.Error()) == "" {
				t.Fatalf("error = %v, want a useful validation error", err)
			}
		})
	}
}

func TestCLI(t *testing.T) {
	temp := t.TempDir()
	binary := filepath.Join(temp, "vcfarch")
	if output, err := exec.Command("go", "build", "-o", binary, "./cmd/vcfarch").CombinedOutput(); err != nil {
		t.Fatalf("build cmd/vcfarch: %v\n%s", err, output)
	}
	copyFile := func(name string) {
		t.Helper()
		data := mustRead(t, name)
		target := filepath.Join(temp, name)
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(target, data, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	copyFile("estate.json")
	copyFile("compatibility-snapshot.json")
	copyFile(openAPIPath)
	copyFile("specifications/vcf-installer/LICENSE")
	command := exec.Command(binary)
	command.Dir = temp
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("run cmd/vcfarch with default flags: %v\n%s", err, output)
	}
	if got, want := mustRead(t, filepath.Join(temp, "architecture.json")), mustRead(t, artifactPath); !bytes.Equal(got, want) {
		t.Fatal("CLI default output differs from checked-in architecture.json")
	}

	customOut := filepath.Join(temp, "custom.json")
	command = exec.Command(binary,
		"-inventory", filepath.Join(temp, "estate.json"),
		"-compat", filepath.Join(temp, "compatibility-snapshot.json"),
		"-out", customOut,
	)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("run cmd/vcfarch with explicit flags: %v\n%s", err, output)
	}
	if got, want := mustRead(t, customOut), mustRead(t, artifactPath); !bytes.Equal(got, want) {
		t.Fatal("CLI explicit-flag output differs from checked-in architecture.json")
	}
}

func cloneJSONValue[T any](t *testing.T, value T) T {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	var result T
	if err := json.Unmarshal(data, &result); err != nil {
		t.Fatal(err)
	}
	return result
}

func writeJSONValue(t *testing.T, name string, value any) string {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return b
}

func mustJSON(t *testing.T, name string, data []byte, dst any, strict bool) {
	t.Helper()
	dec := json.NewDecoder(bytes.NewReader(data))
	if strict {
		dec.DisallowUnknownFields()
	}
	if err := dec.Decode(dst); err != nil {
		t.Fatalf("decode %s: %v", name, err)
	}
	if err := dec.Decode(&struct{}{}); err != io.EOF {
		t.Fatalf("decode %s: trailing JSON value", name)
	}
}

func validateArtifactShape(document map[string]any) error {
	if err := exactObjectKeys(document, "artifact", []string{
		"schemaVersion", "targetFleetVersion", "selectedTopology", "discardedTopologies",
		"greenfieldSddcSpec", "migrationPlan",
	}, nil); err != nil {
		return err
	}
	if err := topologyShape(document["selectedTopology"], "selectedTopology"); err != nil {
		return err
	}
	discarded, ok := document["discardedTopologies"].([]any)
	if !ok {
		return fmt.Errorf("discardedTopologies: want array")
	}
	for i, topology := range discarded {
		if err := topologyShape(topology, fmt.Sprintf("discardedTopologies[%d]", i)); err != nil {
			return err
		}
	}
	plan, err := objectAt(document["migrationPlan"], "migrationPlan")
	if err != nil {
		return err
	}
	if err := exactObjectKeys(plan, "migrationPlan", []string{"strategy", "components"}, nil); err != nil {
		return err
	}
	components, ok := plan["components"].([]any)
	if !ok {
		return fmt.Errorf("migrationPlan.components: want array")
	}
	for i, value := range components {
		path := fmt.Sprintf("migrationPlan.components[%d]", i)
		component, err := objectAt(value, path)
		if err != nil {
			return err
		}
		if err := exactObjectKeys(component, path, []string{
			"id", "type", "product", "currentVersion", "targetProduct", "targetVersion",
			"upgradePath", "gates", "phases",
		}, nil); err != nil {
			return err
		}
		phases, ok := component["phases"].([]any)
		if !ok {
			return fmt.Errorf("%s.phases: want array", path)
		}
		for j, value := range phases {
			phasePath := fmt.Sprintf("%s.phases[%d]", path, j)
			phase, err := objectAt(value, phasePath)
			if err != nil {
				return err
			}
			if err := exactObjectKeys(phase, phasePath, []string{
				"order", "action", "fromVersion", "toVersion", "gates",
			}, nil); err != nil {
				return err
			}
		}
	}
	return nil
}

func topologyShape(value any, path string) error {
	topology, err := objectAt(value, path)
	if err != nil {
		return err
	}
	return exactObjectKeys(topology, path, []string{
		"id", "technicallySupported", "licensedCores", "requiredCores", "gate",
	}, []string{"reasonCode"})
}

func exactObjectKeys(object map[string]any, path string, required, optional []string) error {
	allowed := make(map[string]bool, len(required)+len(optional))
	for _, name := range required {
		allowed[name] = true
		if _, ok := object[name]; !ok {
			return fmt.Errorf("%s: missing required field %q", path, name)
		}
	}
	for _, name := range optional {
		allowed[name] = true
	}
	for name := range object {
		if !allowed[name] {
			return fmt.Errorf("%s: unexpected field %q", path, name)
		}
	}
	return nil
}

func objectAt(value any, path string) (map[string]any, error) {
	object, ok := value.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%s: want object", path)
	}
	return object, nil
}

func validateGreenfieldMapping(sddcValue, designValue any) error {
	sddc, err := objectAt(sddcValue, "greenfieldSddcSpec")
	if err != nil {
		return err
	}
	design, err := objectAt(designValue, "greenfieldDesign")
	if err != nil {
		return err
	}
	for _, name := range []string{"sddcId", "workflowType", "version", "managementPoolName", "ntpServers"} {
		if err := mappedValue(design, name, sddc, name, "greenfieldSddcSpec."+name); err != nil {
			return err
		}
	}
	designDNS, err := objectAt(design["dns"], "greenfieldDesign.dns")
	if err != nil {
		return err
	}
	sddcDNS, err := objectAt(sddc["dnsSpec"], "greenfieldSddcSpec.dnsSpec")
	if err != nil {
		return err
	}
	for name := range designDNS {
		if err := mappedValue(designDNS, name, sddcDNS, name, "greenfieldSddcSpec.dnsSpec."+name); err != nil {
			return err
		}
	}

	designVcenter, err := objectAt(design["vcenter"], "greenfieldDesign.vcenter")
	if err != nil {
		return err
	}
	sddcVcenter, err := objectAt(sddc["vcenterSpec"], "greenfieldSddcSpec.vcenterSpec")
	if err != nil {
		return err
	}
	for source, target := range map[string]string{
		"hostname": "vcenterHostname", "rootPassword": "rootVcenterPassword",
		"vmSize": "vmSize", "ssoDomain": "ssoDomain",
	} {
		if err := mappedValue(designVcenter, source, sddcVcenter, target, "greenfieldSddcSpec.vcenterSpec."+target); err != nil {
			return err
		}
	}

	designManager, err := objectAt(design["sddcManager"], "greenfieldDesign.sddcManager")
	if err != nil {
		return err
	}
	sddcManager, err := objectAt(sddc["sddcManagerSpec"], "greenfieldSddcSpec.sddcManagerSpec")
	if err != nil {
		return err
	}
	for _, name := range []string{"hostname", "rootPassword", "sshPassword"} {
		if err := mappedValue(designManager, name, sddcManager, name, "greenfieldSddcSpec.sddcManagerSpec."+name); err != nil {
			return err
		}
	}

	designNSX, err := objectAt(design["nsx"], "greenfieldDesign.nsx")
	if err != nil {
		return err
	}
	sddcNSX, err := objectAt(sddc["nsxtSpec"], "greenfieldSddcSpec.nsxtSpec")
	if err != nil {
		return err
	}
	for source, target := range map[string]string{
		"vipFqdn": "vipFqdn", "managerSize": "nsxtManagerSize",
		"rootPassword": "rootNsxtManagerPassword", "adminPassword": "nsxtAdminPassword",
		"auditPassword": "nsxtAuditPassword", "transportVlanId": "transportVlanId",
	} {
		if err := mappedValue(designNSX, source, sddcNSX, target, "greenfieldSddcSpec.nsxtSpec."+target); err != nil {
			return err
		}
	}
	managerNames, ok := designNSX["managerHostnames"].([]any)
	if !ok {
		return fmt.Errorf("greenfieldDesign.nsx.managerHostnames: want array")
	}
	managerSpecs, ok := sddcNSX["nsxtManagers"].([]any)
	if !ok || len(managerSpecs) != len(managerNames) {
		return fmt.Errorf("greenfieldSddcSpec.nsxtSpec.nsxtManagers does not preserve managerHostnames")
	}
	gotManagers := make(map[any]bool, len(managerSpecs))
	for i, value := range managerSpecs {
		manager, err := objectAt(value, fmt.Sprintf("greenfieldSddcSpec.nsxtSpec.nsxtManagers[%d]", i))
		if err != nil {
			return err
		}
		gotManagers[manager["hostname"]] = true
	}
	for _, hostname := range managerNames {
		if !gotManagers[hostname] {
			return fmt.Errorf("greenfieldSddcSpec.nsxtSpec.nsxtManagers does not preserve manager %v", hostname)
		}
	}

	designNetworks, ok := design["networks"].([]any)
	if !ok {
		return fmt.Errorf("greenfieldDesign.networks: want array")
	}
	sddcNetworks, ok := sddc["networkSpecs"].([]any)
	if !ok || len(sddcNetworks) != len(designNetworks) {
		return fmt.Errorf("greenfieldSddcSpec.networkSpecs does not preserve greenfieldDesign.networks")
	}
	networksByType := make(map[any]map[string]any, len(sddcNetworks))
	for i, value := range sddcNetworks {
		network, err := objectAt(value, fmt.Sprintf("greenfieldSddcSpec.networkSpecs[%d]", i))
		if err != nil {
			return err
		}
		networksByType[network["networkType"]] = network
	}
	for i, value := range designNetworks {
		network, err := objectAt(value, fmt.Sprintf("greenfieldDesign.networks[%d]", i))
		if err != nil {
			return err
		}
		target, present := networksByType[network["networkType"]]
		if !present {
			return fmt.Errorf("greenfieldSddcSpec.networkSpecs is missing network type %v", network["networkType"])
		}
		for name := range network {
			if err := mappedValue(network, name, target, name, fmt.Sprintf("greenfieldSddcSpec.networkSpecs[%v].%s", network["networkType"], name)); err != nil {
				return err
			}
		}
	}

	designHosts, ok := design["hosts"].([]any)
	if !ok {
		return fmt.Errorf("greenfieldDesign.hosts: want array")
	}
	sddcHosts, ok := sddc["hostSpecs"].([]any)
	if !ok || len(sddcHosts) != len(designHosts) {
		return fmt.Errorf("greenfieldSddcSpec.hostSpecs does not preserve greenfieldDesign.hosts")
	}
	hostsByName := make(map[any]map[string]any, len(sddcHosts))
	for i, value := range sddcHosts {
		host, err := objectAt(value, fmt.Sprintf("greenfieldSddcSpec.hostSpecs[%d]", i))
		if err != nil {
			return err
		}
		hostsByName[host["hostname"]] = host
	}
	for i, value := range designHosts {
		host, err := objectAt(value, fmt.Sprintf("greenfieldDesign.hosts[%d]", i))
		if err != nil {
			return err
		}
		target, present := hostsByName[host["hostname"]]
		if !present {
			return fmt.Errorf("greenfieldSddcSpec.hostSpecs is missing host %v", host["hostname"])
		}
		if !reflect.DeepEqual(target["hostname"], host["hostname"]) {
			return fmt.Errorf("greenfieldSddcSpec host name does not preserve greenfieldDesign.hosts")
		}
		credentials, err := objectAt(target["credentials"], fmt.Sprintf("greenfieldSddcSpec.hostSpecs[%v].credentials", host["hostname"]))
		if err != nil {
			return err
		}
		for _, name := range []string{"username", "password"} {
			if err := mappedValue(host, name, credentials, name, fmt.Sprintf("greenfieldSddcSpec.hostSpecs[%v].credentials.%s", host["hostname"], name)); err != nil {
				return err
			}
		}
	}
	return nil
}

func mappedValue(source map[string]any, sourceName string, target map[string]any, targetName, path string) error {
	want, present := source[sourceName]
	if !present {
		return fmt.Errorf("source field %q is missing", sourceName)
	}
	got, present := target[targetName]
	if !present {
		return fmt.Errorf("%s is missing", path)
	}
	if !reflect.DeepEqual(got, want) {
		return fmt.Errorf("%s does not preserve greenfieldDesign.%s", path, sourceName)
	}
	return nil
}

func findRoute(routes []CompatibilityRoute, componentType, currentVersion string) (CompatibilityRoute, bool) {
	for _, route := range routes {
		if route.ComponentType == componentType && route.CurrentVersion == currentVersion {
			return route, true
		}
	}
	return CompatibilityRoute{}, false
}

type schemaValidator struct {
	document map[string]any
}

func (v schemaValidator) validateRef(ref string, value any, path string) error {
	schema, err := v.resolve(ref)
	if err != nil {
		return err
	}
	return v.validate(schema, value, path)
}

func (v schemaValidator) resolve(ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("unsupported external schema reference %q", ref)
	}
	var current any = v.document
	for _, escaped := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		part := strings.ReplaceAll(strings.ReplaceAll(escaped, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("schema reference %q traverses a non-object", ref)
		}
		current, ok = object[part]
		if !ok {
			return nil, fmt.Errorf("schema reference %q is missing %q", ref, part)
		}
	}
	schema, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("schema reference %q is not an object", ref)
	}
	return schema, nil
}

func (v schemaValidator) validate(schema map[string]any, value any, path string) error {
	if ref, ok := schema["$ref"].(string); ok {
		return v.validateRef(ref, value, path)
	}
	if value == nil {
		if nullable, _ := schema["nullable"].(bool); nullable {
			return nil
		}
		return fmt.Errorf("%s: null is not allowed", path)
	}
	for _, keyword := range []string{"allOf", "anyOf", "oneOf"} {
		branches, ok := schema[keyword].([]any)
		if !ok {
			continue
		}
		matches := 0
		for _, raw := range branches {
			branch, ok := raw.(map[string]any)
			if ok && v.validate(branch, value, path) == nil {
				matches++
			}
		}
		switch keyword {
		case "allOf":
			if matches != len(branches) {
				return fmt.Errorf("%s: does not satisfy every allOf branch", path)
			}
		case "anyOf":
			if matches == 0 {
				return fmt.Errorf("%s: does not satisfy any anyOf branch", path)
			}
		case "oneOf":
			if matches != 1 {
				return fmt.Errorf("%s: satisfies %d oneOf branches, want exactly one", path, matches)
			}
		}
	}
	if enum, ok := schema["enum"].([]any); ok {
		matched := false
		for _, allowed := range enum {
			if reflect.DeepEqual(value, allowed) {
				matched = true
				break
			}
		}
		if !matched {
			return fmt.Errorf("%s: value is not in enum", path)
		}
	}
	typeName, _ := schema["type"].(string)
	switch typeName {
	case "object":
		object, ok := value.(map[string]any)
		if !ok {
			return fmt.Errorf("%s: want object", path)
		}
		if required, ok := schema["required"].([]any); ok {
			for _, raw := range required {
				name, _ := raw.(string)
				if _, present := object[name]; !present {
					return fmt.Errorf("%s: missing required property %q", path, name)
				}
			}
		}
		properties, _ := schema["properties"].(map[string]any)
		for name, childValue := range object {
			raw, known := properties[name]
			if !known {
				if additional, ok := schema["additionalProperties"].(bool); ok && !additional {
					return fmt.Errorf("%s: additional property %q is forbidden", path, name)
				}
				continue
			}
			childSchema, ok := raw.(map[string]any)
			if !ok {
				return fmt.Errorf("%s.%s: property schema is malformed", path, name)
			}
			if err := v.validate(childSchema, childValue, path+"."+name); err != nil {
				return err
			}
		}
	case "array":
		array, ok := value.([]any)
		if !ok {
			return fmt.Errorf("%s: want array", path)
		}
		if minimum, ok := number(schema["minItems"]); ok && float64(len(array)) < minimum {
			return fmt.Errorf("%s: fewer than %.0f items", path, minimum)
		}
		if maximum, ok := number(schema["maxItems"]); ok && float64(len(array)) > maximum {
			return fmt.Errorf("%s: more than %.0f items", path, maximum)
		}
		if raw, ok := schema["items"].(map[string]any); ok {
			for i, item := range array {
				if err := v.validate(raw, item, fmt.Sprintf("%s[%d]", path, i)); err != nil {
					return err
				}
			}
		}
	case "string":
		text, ok := value.(string)
		if !ok {
			return fmt.Errorf("%s: want string", path)
		}
		length := float64(utf8.RuneCountInString(text))
		if minimum, ok := number(schema["minLength"]); ok && length < minimum {
			return fmt.Errorf("%s: string is shorter than %.0f", path, minimum)
		}
		if maximum, ok := number(schema["maxLength"]); ok && length > maximum {
			return fmt.Errorf("%s: string is longer than %.0f", path, maximum)
		}
		if pattern, ok := schema["pattern"].(string); ok {
			re, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: invalid schema pattern: %w", path, err)
			}
			if !re.MatchString(text) {
				return fmt.Errorf("%s: does not match %q", path, pattern)
			}
		}
	case "integer", "number":
		n, ok := value.(float64)
		if !ok || typeName == "integer" && math.Trunc(n) != n {
			return fmt.Errorf("%s: want %s", path, typeName)
		}
		if minimum, ok := number(schema["minimum"]); ok && n < minimum {
			return fmt.Errorf("%s: %.2f is below minimum %.2f", path, n, minimum)
		}
		if maximum, ok := number(schema["maximum"]); ok && n > maximum {
			return fmt.Errorf("%s: %.2f is above maximum %.2f", path, n, maximum)
		}
		if format, _ := schema["format"].(string); typeName == "integer" && format == "int32" && (n < math.MinInt32 || n > math.MaxInt32) {
			return fmt.Errorf("%s: %.0f is outside int32", path, n)
		}
	case "boolean":
		if _, ok := value.(bool); !ok {
			return fmt.Errorf("%s: want boolean", path)
		}
	}
	return nil
}

func number(value any) (float64, bool) {
	n, ok := value.(float64)
	return n, ok
}
