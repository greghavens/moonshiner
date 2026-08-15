package acceptance_test

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	arch "vcfarch-0125/architecture"
)

const installerSHA256 = "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"

// TestArchitectureArtifact is intentionally the sole top-level acceptance
// test. Its first assertion validates the submitted SddcSpec against the
// Installer OpenAPI document; compatibility and package checks only run after
// that schema gate has succeeded.
func TestArchitectureArtifact(t *testing.T) {
	root := filepath.Clean("..")
	artifactPath := filepath.Join(root, "out", "architecture.json")
	artifactBytes, err := os.ReadFile(artifactPath)
	if err != nil {
		t.Fatalf("read architecture artifact: %v", err)
	}

	artifactDoc, err := decodeJSON(artifactBytes)
	if err != nil {
		t.Fatalf("decode architecture artifact before SddcSpec validation: %v", err)
	}
	sddcSpec, err := objectAt(artifactDoc, "greenfield", "sddcSpec")
	if err != nil {
		t.Fatalf("locate greenfield SddcSpec: %v", err)
	}
	installerPath := filepath.Join(root, "specifications", "vcf-installer", "vcf-installer-openapi.json")
	installerBytes, err := os.ReadFile(installerPath)
	if err != nil {
		t.Fatalf("read Installer OpenAPI: %v", err)
	}
	installerDoc, err := decodeJSON(installerBytes)
	if err != nil {
		t.Fatalf("decode Installer OpenAPI: %v", err)
	}
	installerRoot, ok := installerDoc.(map[string]any)
	if !ok {
		t.Fatal("Installer OpenAPI root is not an object")
	}
	sddcSchema, err := objectAt(installerDoc, "components", "schemas", "SddcSpec")
	if err != nil {
		t.Fatalf("locate components.schemas.SddcSpec: %v", err)
	}
	if err := validateSchema(sddcSchema, sddcSpec, installerRoot, "$.greenfield.sddcSpec"); err != nil {
		t.Fatalf("SddcSpec does not validate against the VCF Installer 9.1 schema: %v", err)
	}

	// No artifact, fixture, compatibility, or implementation assertion may be
	// moved above the Installer-schema validation block.
	t.Run("pinned installer provenance", func(t *testing.T) {
		sum := sha256.Sum256(installerBytes)
		if got := hex.EncodeToString(sum[:]); got != installerSHA256 {
			t.Fatalf("Installer OpenAPI hash = %s, want pinned %s", got, installerSHA256)
		}
		version, err := valueAt(installerDoc, "info", "version")
		if err != nil || version != "9.1.0.0" {
			t.Fatalf("Installer info.version = %v (%v), want 9.1.0.0", version, err)
		}
	})

	t.Run("consulted research record", func(t *testing.T) {
		validateResearchRecord(t, filepath.Join(root, "research", "consulted.json"))
	})

	t.Run("fleet architecture schema", func(t *testing.T) {
		schemaBytes, err := os.ReadFile(filepath.Join(root, "schemas", "fleet-architecture.schema.json"))
		if err != nil {
			t.Fatal(err)
		}
		schemaDoc, err := decodeJSON(schemaBytes)
		if err != nil {
			t.Fatal(err)
		}
		schemaRoot := schemaDoc.(map[string]any)
		if err := validateSchema(schemaRoot, artifactDoc, schemaRoot, "$"); err != nil {
			t.Fatalf("architecture schema: %v", err)
		}
	})

	inventory := loadInventory(t, filepath.Join(root, "fixtures", "estate.json"))
	snapshot := loadSnapshot(t, filepath.Join(root, "compatibility", "vcf-9.1-snapshot.json"))

	var submitted arch.Architecture
	if err := json.Unmarshal(artifactBytes, &submitted); err != nil {
		t.Fatalf("decode typed artifact: %v", err)
	}
	expected, err := oracleBuild(inventory, snapshot)
	if err != nil {
		t.Fatalf("protected fixture/snapshot are inconsistent: %v", err)
	}
	if !sameJSON(submitted, expected) {
		want, _ := json.MarshalIndent(expected, "", "  ")
		got, _ := json.MarshalIndent(submitted, "", "  ")
		t.Fatalf("artifact differs from fixture and pinned authority\nwant:\n%s\ngot:\n%s", want, got)
	}

	validateIndependentInvariants(t, submitted, inventory, snapshot)

	plannerCases := []struct {
		name      string
		mutate    func(*arch.Inventory, *arch.CompatibilitySnapshot)
		wantError bool
	}{
		{name: "base inventory"},
		{
			name: "OSA is selected when it is the only allowed architecture",
			mutate: func(inv *arch.Inventory, _ *arch.CompatibilitySnapshot) {
				inv.Greenfield.AllowedStorageArchitectures = []string{"OSA"}
			},
		},
		{
			name: "migration order does not depend on inventory order",
			mutate: func(inv *arch.Inventory, _ *arch.CompatibilitySnapshot) {
				for left, right := 0, len(inv.Components)-1; left < right; left, right = left+1, right-1 {
					inv.Components[left], inv.Components[right] = inv.Components[right], inv.Components[left]
				}
			},
		},
		{
			name: "unsupported source version is rejected",
			mutate: func(inv *arch.Inventory, _ *arch.CompatibilitySnapshot) {
				for i := range inv.Components {
					if inv.Components[i].Type == "vcenter" {
						inv.Components[i].Version = "7.0.0"
					}
				}
			},
			wantError: true,
		},
	}
	for _, tc := range plannerCases {
		t.Run("planner/"+tc.name, func(t *testing.T) {
			inv := clone(inventory)
			snap := clone(snapshot)
			if tc.mutate != nil {
				tc.mutate(&inv, &snap)
			}
			want, oracleErr := oracleBuild(inv, snap)
			got, buildErr := arch.Build(inv, snap)
			if tc.wantError {
				if oracleErr == nil || buildErr == nil {
					t.Fatalf("unsupported transition: oracle error=%v, Build error=%v", oracleErr, buildErr)
				}
				return
			}
			if oracleErr != nil || buildErr != nil {
				t.Fatalf("oracle error=%v, Build error=%v", oracleErr, buildErr)
			}
			if !sameJSON(got, want) {
				t.Fatalf("Build result does not derive from supplied inputs")
			}
		})
	}

	t.Run("CLI reproduces committed artifact", func(t *testing.T) {
		tempOut := filepath.Join(t.TempDir(), "nested", "architecture.json")
		cmd := exec.Command("go", "run", "./cmd/vcfarch",
			"-inventory", "fixtures/estate.json",
			"-compatibility", "compatibility/vcf-9.1-snapshot.json",
			"-out", tempOut)
		cmd.Dir = root
		output, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("CLI failed: %v\n%s", err, output)
		}
		generated, err := os.ReadFile(tempOut)
		if err != nil {
			t.Fatal(err)
		}
		generatedDoc, err := decodeJSON(generated)
		if err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(generatedDoc, artifactDoc) {
			t.Fatal("CLI output differs from committed out/architecture.json")
		}
	})
}

func validateResearchRecord(t *testing.T, path string) {
	t.Helper()
	type source struct {
		Title      string `json:"title"`
		URL        string `json:"url"`
		AccessedOn string `json:"accessedOn"`
		UsedFor    string `json:"usedFor"`
	}
	type record struct {
		Sources       []source `json:"sources"`
		Discrepancies []any    `json:"discrepancies"`
	}

	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read consulted research record: %v", err)
	}
	var got record
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("decode consulted research record: %v", err)
	}
	if len(got.Sources) == 0 {
		t.Fatal("research record has no sources")
	}
	seenURLs := make(map[string]bool)
	var consulted strings.Builder
	for i, source := range got.Sources {
		if strings.TrimSpace(source.Title) == "" || strings.TrimSpace(source.UsedFor) == "" {
			t.Fatalf("research source %d lacks a title or usedFor fact", i)
		}
		if _, err := time.Parse("2006-01-02", source.AccessedOn); err != nil {
			t.Fatalf("research source %d accessedOn %q is not an ISO date", i, source.AccessedOn)
		}
		parsed, err := url.Parse(source.URL)
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" {
			t.Fatalf("research source %d URL %q is not a valid HTTPS URL", i, source.URL)
		}
		host := strings.ToLower(parsed.Hostname())
		if host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com") &&
			host != "vmware.com" && !strings.HasSuffix(host, ".vmware.com") {
			t.Fatalf("research source %d URL %q is not Broadcom/VMware-published material", i, source.URL)
		}
		if seenURLs[source.URL] {
			t.Fatalf("research source URL %q is duplicated", source.URL)
		}
		seenURLs[source.URL] = true
		consulted.WriteString(" ")
		consulted.WriteString(strings.ToLower(source.Title))
		consulted.WriteString(" ")
		consulted.WriteString(strings.ToLower(source.UsedFor))
	}
	coverage := consulted.String()
	for _, topic := range []string{
		"vcenter", "esxi", "vsan", "nsx", "live site recovery", "vsphere replication", "osa", "esa",
	} {
		if !strings.Contains(coverage, topic) {
			t.Errorf("research record does not state what was consulted for %s", topic)
		}
	}
}

func validateIndependentInvariants(t *testing.T, got arch.Architecture, inv arch.Inventory, snap arch.CompatibilitySnapshot) {
	t.Helper()
	if got.SchemaVersion != "1.0" || got.FleetTarget != inv.FleetTarget || got.FleetTarget != snap.FleetTarget {
		t.Fatalf("fleet identity mismatch: %#v", got)
	}
	if len(got.Migration.OrderedSteps) != len(inv.Components) {
		t.Fatalf("migration has %d steps for %d components", len(got.Migration.OrderedSteps), len(inv.Components))
	}
	seen := make(map[string]bool)
	completedTypes := make(map[string]bool)
	for i, step := range got.Migration.OrderedSteps {
		if step.Order != i+1 {
			t.Fatalf("migration order is not contiguous at index %d: %d", i, step.Order)
		}
		if seen[step.Component.ID] {
			t.Fatalf("component %q appears more than once", step.Component.ID)
		}
		seen[step.Component.ID] = true
		rule, ok := matchingRule(step.Component.Type, step.Component.Version, step.Component.Architecture, snap)
		if !ok {
			t.Fatalf("step %q has no pinned transition", step.Component.ID)
		}
		for _, predecessor := range rule.AfterComponentTypes {
			if !completedTypes[predecessor] {
				t.Fatalf("step %q ran before required component type %q", step.Component.ID, predecessor)
			}
		}
		for _, gate := range step.Gates {
			if _, ok := snap.GateCatalog[gate]; !ok {
				t.Fatalf("step %q references unknown gate %q", step.Component.ID, gate)
			}
		}
		completedTypes[step.Component.Type] = true
	}

	selected := got.Greenfield.StorageDecision
	if len(selected.Alternatives) != len(inv.Greenfield.AllowedStorageArchitectures) {
		t.Fatalf("storage alternatives = %d, want %d", len(selected.Alternatives), len(inv.Greenfield.AllowedStorageArchitectures))
	}
	hostSpecs, err := arrayAt(got.Greenfield.SDDCSpec, "hostSpecs")
	if err != nil || len(hostSpecs) != selected.HostCount {
		t.Fatalf("SddcSpec hosts = %d (%v), selected host count = %d", len(hostSpecs), err, selected.HostCount)
	}
	esa, err := valueAt(got.Greenfield.SDDCSpec, "datastoreSpec", "vsanSpec", "esaConfig", "enabled")
	if err != nil || esa != (selected.SelectedArchitecture == "ESA") {
		t.Fatalf("SddcSpec ESA flag = %v (%v), selection = %s", esa, err, selected.SelectedArchitecture)
	}
	vsanMTU, err := networkMTU(got.Greenfield.SDDCSpec, "VSAN")
	if err != nil || vsanMTU != selected.Network.VsanMTU {
		t.Fatalf("SddcSpec VSAN MTU = %d (%v), design = %d", vsanMTU, err, selected.Network.VsanMTU)
	}
}

func oracleBuild(inv arch.Inventory, snap arch.CompatibilitySnapshot) (arch.Architecture, error) {
	if inv.FleetTarget == "" || inv.FleetTarget != snap.FleetTarget || inv.Greenfield.Version != snap.FleetTarget {
		return arch.Architecture{}, fmt.Errorf("fleet target mismatch")
	}
	if inv.Greenfield.Optimization != "fewest-hosts" {
		return arch.Architecture{}, fmt.Errorf("unsupported optimization %q", inv.Greenfield.Optimization)
	}
	allowed := make(map[string]bool)
	for _, name := range inv.Greenfield.AllowedStorageArchitectures {
		if allowed[name] {
			return arch.Architecture{}, fmt.Errorf("duplicate allowed architecture %q", name)
		}
		allowed[name] = true
	}
	var alternatives []arch.StorageAlternative
	var selected *arch.StorageOption
	for i := range snap.StorageOptions {
		option := &snap.StorageOptions[i]
		if !allowed[option.Architecture] {
			continue
		}
		uplink, ok := option.UplinkGbpsByProfile[inv.Greenfield.PerformanceProfile]
		if !ok {
			return arch.Architecture{}, fmt.Errorf("storage %s lacks performance profile %s", option.Architecture, inv.Greenfield.PerformanceProfile)
		}
		hosts := option.PolicyMinimumHosts + inv.Greenfield.ReserveHosts
		alternatives = append(alternatives, arch.StorageAlternative{
			Architecture: option.Architecture, Policy: option.Policy, HostCount: hosts,
			UplinksPerHost: option.UplinksPerHost, UplinkGbps: uplink, VsanMTU: option.VsanMTU,
			ESAEnabled: option.ESAEnabled, HardwareRequirement: option.HardwareRequirement,
		})
		if selected == nil {
			selected = option
		} else {
			selectedHosts := selected.PolicyMinimumHosts + inv.Greenfield.ReserveHosts
			if hosts < selectedHosts || (hosts == selectedHosts && option.PreferenceRank < selected.PreferenceRank) {
				selected = option
			}
		}
	}
	if selected == nil || len(alternatives) != len(allowed) {
		return arch.Architecture{}, fmt.Errorf("one or more allowed storage architectures are unsupported")
	}
	selectedHosts := selected.PolicyMinimumHosts + inv.Greenfield.ReserveHosts
	if len(inv.Greenfield.Hostnames) < selectedHosts {
		return arch.Architecture{}, fmt.Errorf("need %d hostnames, have %d", selectedHosts, len(inv.Greenfield.Hostnames))
	}
	selectedUplink := selected.UplinkGbpsByProfile[inv.Greenfield.PerformanceProfile]

	sddc := map[string]any{
		"sddcId":       inv.Greenfield.SDDCID,
		"version":      inv.Greenfield.Version,
		"workflowType": inv.Greenfield.WorkflowType,
		"dnsSpec":      deepCopy(inv.Greenfield.DNSSpec),
		"vcenterSpec":  deepCopy(inv.Greenfield.VcenterSpec),
		"networkSpecs": deepCopy(inv.Greenfield.NetworkSpecs),
		"dvsSpecs":     []any{deepCopy(inv.Greenfield.DvsSpec)},
		"nsxtSpec":     deepCopy(inv.Greenfield.NsxtSpec),
		"datastoreSpec": map[string]any{
			"vsanSpec": map[string]any{
				"datastoreName":      "vsan-" + inv.Greenfield.SDDCID,
				"failuresToTolerate": selected.FailuresToTolerate,
				"esaConfig":          map[string]any{"enabled": selected.ESAEnabled},
			},
		},
	}
	hostSpecs := make([]any, 0, selectedHosts)
	for _, hostname := range inv.Greenfield.Hostnames[:selectedHosts] {
		hostSpecs = append(hostSpecs, map[string]any{"hostname": hostname})
	}
	sddc["hostSpecs"] = hostSpecs

	type planned struct {
		component arch.Component
		rule      arch.MigrationRule
	}
	plannedSteps := make([]planned, 0, len(inv.Components))
	ids := make(map[string]bool)
	for _, component := range inv.Components {
		if component.ID == "" || ids[component.ID] {
			return arch.Architecture{}, fmt.Errorf("empty or duplicate component ID %q", component.ID)
		}
		ids[component.ID] = true
		rule, ok := matchingRule(component.Type, component.Version, component.Architecture, snap)
		if !ok {
			return arch.Architecture{}, fmt.Errorf("unsupported transition for %s %s %s", component.Type, component.Version, component.Architecture)
		}
		plannedSteps = append(plannedSteps, planned{component: component, rule: rule})
	}
	sort.Slice(plannedSteps, func(i, j int) bool {
		if plannedSteps[i].rule.Sequence == plannedSteps[j].rule.Sequence {
			return plannedSteps[i].component.ID < plannedSteps[j].component.ID
		}
		return plannedSteps[i].rule.Sequence < plannedSteps[j].rule.Sequence
	})
	steps := make([]arch.MigrationStep, 0, len(plannedSteps))
	completed := make(map[string]bool)
	for i, item := range plannedSteps {
		for _, predecessor := range item.rule.AfterComponentTypes {
			if !completed[predecessor] {
				return arch.Architecture{}, fmt.Errorf("%s must follow component type %s", item.component.Type, predecessor)
			}
		}
		for _, gate := range item.rule.Gates {
			if _, ok := snap.GateCatalog[gate]; !ok {
				return arch.Architecture{}, fmt.Errorf("unknown gate %q", gate)
			}
		}
		steps = append(steps, arch.MigrationStep{
			Order: i + 1,
			Component: arch.PlannedComponent{
				ID: item.component.ID, Type: item.component.Type, Product: item.component.Product,
				Version: item.component.Version, Architecture: item.component.Architecture,
			},
			Target: arch.PlannedTarget{
				Product: item.rule.TargetProduct, Version: item.rule.TargetVersion,
				Architecture: item.rule.TargetArchitecture,
			},
			Action: item.rule.Action, UpgradePath: clone(item.rule.UpgradePath), Gates: clone(item.rule.Gates),
		})
		completed[item.component.Type] = true
	}

	return arch.Architecture{
		SchemaVersion: "1.0",
		FleetTarget:   inv.FleetTarget,
		Greenfield: arch.GreenfieldDesign{
			StorageDecision: arch.StorageDecision{
				SelectedArchitecture: selected.Architecture,
				SelectionCriterion:   inv.Greenfield.Optimization,
				HostCount:            selectedHosts,
				Policy:               selected.Policy,
				Network: arch.StorageNetwork{
					UplinksPerHost: selected.UplinksPerHost, UplinkGbps: selectedUplink, VsanMTU: selected.VsanMTU,
				},
				Alternatives: alternatives,
			},
			SDDCSpec: sddc,
		},
		Migration: arch.MigrationPlan{Estate: inv.EstateName, OrderedSteps: steps},
	}, nil
}

func matchingRule(componentType, version, architecture string, snap arch.CompatibilitySnapshot) (arch.MigrationRule, bool) {
	for _, rule := range snap.MigrationRules {
		if rule.ComponentType == componentType && rule.FromVersion == version &&
			(rule.FromArchitecture == "" || rule.FromArchitecture == architecture) {
			return rule, true
		}
	}
	return arch.MigrationRule{}, false
}

func loadInventory(t *testing.T, path string) arch.Inventory {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	v, err := arch.LoadInventory(f)
	if err != nil {
		t.Fatal(err)
	}
	return v
}

func loadSnapshot(t *testing.T, path string) arch.CompatibilitySnapshot {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	v, err := arch.LoadCompatibilitySnapshot(f)
	if err != nil {
		t.Fatal(err)
	}
	return v
}

func clone[T any](in T) T {
	b, err := json.Marshal(in)
	if err != nil {
		panic(err)
	}
	var out T
	if err := json.Unmarshal(b, &out); err != nil {
		panic(err)
	}
	return out
}

func deepCopy[T any](in T) T { return clone(in) }

func sameJSON(a, b any) bool {
	ab, errA := json.Marshal(a)
	bb, errB := json.Marshal(b)
	if errA != nil || errB != nil {
		return false
	}
	av, errA := decodeJSON(ab)
	bv, errB := decodeJSON(bb)
	return errA == nil && errB == nil && reflect.DeepEqual(av, bv)
}

func decodeJSON(data []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	if dec.More() {
		return nil, fmt.Errorf("multiple JSON values")
	}
	return v, nil
}

func objectAt(root any, path ...string) (map[string]any, error) {
	v, err := valueAt(root, path...)
	if err != nil {
		return nil, err
	}
	obj, ok := v.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%s is %T, not object", strings.Join(path, "."), v)
	}
	return obj, nil
}

func arrayAt(root any, path ...string) ([]any, error) {
	v, err := valueAt(root, path...)
	if err != nil {
		return nil, err
	}
	items, ok := v.([]any)
	if !ok {
		return nil, fmt.Errorf("%s is %T, not array", strings.Join(path, "."), v)
	}
	return items, nil
}

func valueAt(root any, path ...string) (any, error) {
	cur := root
	for _, key := range path {
		obj, ok := cur.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("parent of %q is %T", key, cur)
		}
		var found bool
		cur, found = obj[key]
		if !found {
			return nil, fmt.Errorf("missing %q", key)
		}
	}
	return cur, nil
}

func networkMTU(sddc map[string]any, networkType string) (int, error) {
	networks, err := arrayAt(sddc, "networkSpecs")
	if err != nil {
		return 0, err
	}
	for _, raw := range networks {
		network, ok := raw.(map[string]any)
		if !ok || network["networkType"] != networkType {
			continue
		}
		switch n := network["mtu"].(type) {
		case float64:
			return int(n), nil
		case json.Number:
			v, err := strconv.Atoi(n.String())
			return v, err
		case int:
			return n, nil
		default:
			return 0, fmt.Errorf("MTU is %T", n)
		}
	}
	return 0, fmt.Errorf("network %s not found", networkType)
}

// validateSchema implements the JSON Schema keywords used by both the pinned
// OpenAPI schemas and the seed's artifact schema. OpenAPI format annotations
// are intentionally not treated as validation assertions.
func validateSchema(schema map[string]any, value any, root map[string]any, path string) error {
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolveRef(root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		return validateSchema(resolved, value, root, path)
	}
	for _, keyword := range []string{"allOf"} {
		if schemas, ok := schema[keyword].([]any); ok {
			for _, raw := range schemas {
				if err := validateSchema(raw.(map[string]any), value, root, path); err != nil {
					return err
				}
			}
		}
	}
	for _, keyword := range []string{"oneOf", "anyOf"} {
		if schemas, ok := schema[keyword].([]any); ok {
			matches := 0
			for _, raw := range schemas {
				if validateSchema(raw.(map[string]any), value, root, path) == nil {
					matches++
				}
			}
			if matches == 0 || (keyword == "oneOf" && matches != 1) {
				return fmt.Errorf("%s: %s matched %d alternatives", path, keyword, matches)
			}
		}
	}
	if want, ok := schema["const"]; ok && !jsonEqual(want, value) {
		return fmt.Errorf("%s: value %v does not equal const %v", path, value, want)
	}
	if choices, ok := schema["enum"].([]any); ok {
		matched := false
		for _, choice := range choices {
			matched = matched || jsonEqual(choice, value)
		}
		if !matched {
			return fmt.Errorf("%s: value %v is not in enum", path, value)
		}
	}

	wantType, _ := schema["type"].(string)
	switch wantType {
	case "object":
		obj, ok := value.(map[string]any)
		if !ok {
			return fmt.Errorf("%s: got %T, want object", path, value)
		}
		if required, ok := schema["required"].([]any); ok {
			for _, raw := range required {
				name := raw.(string)
				if _, exists := obj[name]; !exists {
					return fmt.Errorf("%s: missing required property %q", path, name)
				}
			}
		}
		properties, _ := schema["properties"].(map[string]any)
		for name, child := range obj {
			rawSchema, declared := properties[name]
			if !declared {
				if additional, exists := schema["additionalProperties"]; exists && additional == false {
					return fmt.Errorf("%s: additional property %q", path, name)
				}
				continue
			}
			if err := validateSchema(rawSchema.(map[string]any), child, root, path+"."+name); err != nil {
				return err
			}
		}
	case "array":
		items, ok := value.([]any)
		if !ok {
			return fmt.Errorf("%s: got %T, want array", path, value)
		}
		if min, ok := numberAsInt(schema["minItems"]); ok && len(items) < min {
			return fmt.Errorf("%s: has %d items, minimum %d", path, len(items), min)
		}
		if max, ok := numberAsInt(schema["maxItems"]); ok && len(items) > max {
			return fmt.Errorf("%s: has %d items, maximum %d", path, len(items), max)
		}
		if itemSchema, ok := schema["items"].(map[string]any); ok {
			for i, item := range items {
				if err := validateSchema(itemSchema, item, root, fmt.Sprintf("%s[%d]", path, i)); err != nil {
					return err
				}
			}
		}
	case "string":
		text, ok := value.(string)
		if !ok {
			return fmt.Errorf("%s: got %T, want string", path, value)
		}
		length := utf8.RuneCountInString(text)
		if min, ok := numberAsInt(schema["minLength"]); ok && length < min {
			return fmt.Errorf("%s: length %d, minimum %d", path, length, min)
		}
		if max, ok := numberAsInt(schema["maxLength"]); ok && length > max {
			return fmt.Errorf("%s: length %d, maximum %d", path, length, max)
		}
		if pattern, ok := schema["pattern"].(string); ok {
			re, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: invalid schema pattern %q: %w", path, pattern, err)
			}
			if !re.MatchString(text) {
				return fmt.Errorf("%s: %q does not match %q", path, text, pattern)
			}
		}
	case "integer":
		n, ok := numberAsFloat(value)
		if !ok || n != float64(int64(n)) {
			return fmt.Errorf("%s: got %v (%T), want integer", path, value, value)
		}
		if min, ok := numberAsFloat(schema["minimum"]); ok && n < min {
			return fmt.Errorf("%s: %v is below minimum %v", path, n, min)
		}
		if max, ok := numberAsFloat(schema["maximum"]); ok && n > max {
			return fmt.Errorf("%s: %v is above maximum %v", path, n, max)
		}
	case "number":
		if _, ok := numberAsFloat(value); !ok {
			return fmt.Errorf("%s: got %T, want number", path, value)
		}
	case "boolean":
		if _, ok := value.(bool); !ok {
			return fmt.Errorf("%s: got %T, want boolean", path, value)
		}
	}
	return nil
}

func resolveRef(root map[string]any, ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("unsupported non-local ref %q", ref)
	}
	var cur any = root
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		obj, ok := cur.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("ref %q traverses non-object", ref)
		}
		cur, ok = obj[token]
		if !ok {
			return nil, fmt.Errorf("ref %q missing token %q", ref, token)
		}
	}
	resolved, ok := cur.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("ref %q does not resolve to schema object", ref)
	}
	return resolved, nil
}

func numberAsInt(v any) (int, bool) {
	f, ok := numberAsFloat(v)
	return int(f), ok && f == float64(int(f))
}

func numberAsFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case json.Number:
		f, err := n.Float64()
		return f, err == nil
	case float64:
		return n, true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	default:
		return 0, false
	}
}

func jsonEqual(a, b any) bool {
	if af, ok := numberAsFloat(a); ok {
		bf, bok := numberAsFloat(b)
		return bok && af == bf
	}
	return reflect.DeepEqual(a, b)
}
