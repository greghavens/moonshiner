package grader_tests

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"

	vcfarch "example.com/vcfarch"
)

func rootPath(parts ...string) string {
	return filepath.Join(append([]string{".."}, parts...)...)
}

func decodeJSONFile(t *testing.T, path string) any {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.UseNumber()
	var value any
	if err := dec.Decode(&value); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
	return value
}

type schemaValidator struct {
	root map[string]any
}

func (v schemaValidator) validate(path string, value any, schema map[string]any) []string {
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := v.resolve(ref)
		if err != nil {
			return []string{fmt.Sprintf("%s: %v", path, err)}
		}
		return v.validate(path, value, resolved)
	}
	if nullable, _ := schema["nullable"].(bool); nullable && value == nil {
		return nil
	}
	for _, keyword := range []string{"allOf", "anyOf", "oneOf"} {
		if raw, ok := schema[keyword].([]any); ok {
			matches := 0
			var combined []string
			for _, item := range raw {
				candidate, ok := item.(map[string]any)
				if !ok {
					continue
				}
				errs := v.validate(path, value, candidate)
				if len(errs) == 0 {
					matches++
				}
				combined = append(combined, errs...)
			}
			switch keyword {
			case "allOf":
				if matches != len(raw) {
					return combined
				}
			case "anyOf":
				if matches == 0 {
					return []string{fmt.Sprintf("%s: does not match anyOf", path)}
				}
			case "oneOf":
				if matches != 1 {
					return []string{fmt.Sprintf("%s: matches %d oneOf branches", path, matches)}
				}
			}
		}
	}

	if expected, ok := schema["const"]; ok && !jsonEqual(value, expected) {
		return []string{fmt.Sprintf("%s: value does not equal const %v", path, expected)}
	}
	if choices, ok := schema["enum"].([]any); ok {
		matched := false
		for _, choice := range choices {
			matched = matched || jsonEqual(value, choice)
		}
		if !matched {
			return []string{fmt.Sprintf("%s: value %v is not in enum", path, value)}
		}
	}

	typeName, _ := schema["type"].(string)
	switch typeName {
	case "object":
		object, ok := value.(map[string]any)
		if !ok {
			return []string{fmt.Sprintf("%s: expected object, got %T", path, value)}
		}
		var errs []string
		if required, ok := schema["required"].([]any); ok {
			for _, rawName := range required {
				name, _ := rawName.(string)
				if _, exists := object[name]; !exists {
					errs = append(errs, fmt.Sprintf("%s: missing required property %q", path, name))
				}
			}
		}
		properties, _ := schema["properties"].(map[string]any)
		for name, childValue := range object {
			childRaw, exists := properties[name]
			if !exists {
				if additional, exists := schema["additionalProperties"].(bool); exists && !additional {
					errs = append(errs, fmt.Sprintf("%s: unexpected property %q", path, name))
				}
				continue
			}
			childSchema, ok := childRaw.(map[string]any)
			if !ok {
				continue
			}
			errs = append(errs, v.validate(path+"."+name, childValue, childSchema)...)
		}
		return errs
	case "array":
		array, ok := value.([]any)
		if !ok {
			return []string{fmt.Sprintf("%s: expected array, got %T", path, value)}
		}
		var errs []string
		if min, ok := number(schema["minItems"]); ok && float64(len(array)) < min {
			errs = append(errs, fmt.Sprintf("%s: has %d items, minimum is %.0f", path, len(array), min))
		}
		if max, ok := number(schema["maxItems"]); ok && float64(len(array)) > max {
			errs = append(errs, fmt.Sprintf("%s: has %d items, maximum is %.0f", path, len(array), max))
		}
		if unique, _ := schema["uniqueItems"].(bool); unique {
			seen := map[string]bool{}
			for _, item := range array {
				key, _ := json.Marshal(item)
				if seen[string(key)] {
					errs = append(errs, fmt.Sprintf("%s: duplicate array item", path))
					break
				}
				seen[string(key)] = true
			}
		}
		if itemSchema, ok := schema["items"].(map[string]any); ok {
			for index, item := range array {
				errs = append(errs, v.validate(fmt.Sprintf("%s[%d]", path, index), item, itemSchema)...)
			}
		}
		return errs
	case "string":
		s, ok := value.(string)
		if !ok {
			return []string{fmt.Sprintf("%s: expected string, got %T", path, value)}
		}
		var errs []string
		if min, ok := number(schema["minLength"]); ok && float64(len([]rune(s))) < min {
			errs = append(errs, fmt.Sprintf("%s: string is shorter than %.0f", path, min))
		}
		if max, ok := number(schema["maxLength"]); ok && float64(len([]rune(s))) > max {
			errs = append(errs, fmt.Sprintf("%s: string is longer than %.0f", path, max))
		}
		if pattern, ok := schema["pattern"].(string); ok {
			re, err := regexp.Compile(pattern)
			if err != nil {
				errs = append(errs, fmt.Sprintf("%s: invalid schema pattern %q: %v", path, pattern, err))
			} else if !re.MatchString(s) {
				errs = append(errs, fmt.Sprintf("%s: %q does not match %q", path, s, pattern))
			}
		}
		return errs
	case "integer":
		n, ok := number(value)
		if !ok || math.Trunc(n) != n {
			return []string{fmt.Sprintf("%s: expected integer, got %v", path, value)}
		}
		return validateNumberBounds(path, n, schema)
	case "number":
		n, ok := number(value)
		if !ok {
			return []string{fmt.Sprintf("%s: expected number, got %v", path, value)}
		}
		return validateNumberBounds(path, n, schema)
	case "boolean":
		if _, ok := value.(bool); !ok {
			return []string{fmt.Sprintf("%s: expected boolean, got %T", path, value)}
		}
	case "null":
		if value != nil {
			return []string{fmt.Sprintf("%s: expected null", path)}
		}
	}
	return nil
}

func (v schemaValidator) resolve(ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("unsupported non-local schema reference %q", ref)
	}
	var current any = v.root
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("schema reference %q traverses a non-object", ref)
		}
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		current, ok = object[token]
		if !ok {
			return nil, fmt.Errorf("schema reference %q is missing", ref)
		}
	}
	resolved, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("schema reference %q is not an object", ref)
	}
	return resolved, nil
}

func validateNumberBounds(path string, value float64, schema map[string]any) []string {
	var errs []string
	if min, ok := number(schema["minimum"]); ok && value < min {
		errs = append(errs, fmt.Sprintf("%s: %.4g is below minimum %.4g", path, value, min))
	}
	if max, ok := number(schema["maximum"]); ok && value > max {
		errs = append(errs, fmt.Sprintf("%s: %.4g exceeds maximum %.4g", path, value, max))
	}
	return errs
}

func number(value any) (float64, bool) {
	switch n := value.(type) {
	case json.Number:
		f, err := n.Float64()
		return f, err == nil
	case float64:
		return n, true
	case int:
		return float64(n), true
	default:
		return 0, false
	}
}

func jsonEqual(left, right any) bool {
	l, _ := json.Marshal(left)
	r, _ := json.Marshal(right)
	return bytes.Equal(l, r)
}

func installerSchema(t *testing.T) (schemaValidator, map[string]any) {
	t.Helper()
	raw := decodeJSONFile(t, rootPath("specifications", "vcf-installer", "vcf-installer-openapi.json"))
	root, ok := raw.(map[string]any)
	if !ok {
		t.Fatal("installer OpenAPI document is not an object")
	}
	components, _ := root["components"].(map[string]any)
	schemas, _ := components["schemas"].(map[string]any)
	sddc, ok := schemas["SddcSpec"].(map[string]any)
	if !ok {
		t.Fatal("installer OpenAPI document has no SddcSpec component")
	}
	return schemaValidator{root: root}, sddc
}

// TestArtifactSddcSchema is invoked alone as the first verifier command. It may
// only consult the artifact and the tagged installer document.
func TestArtifactSddcSchema(t *testing.T) {
	validator, sddcSchema := installerSchema(t)
	artifact := decodeJSONFile(t, rootPath("architecture.json"))
	if errs := validator.validate("$", artifact, sddcSchema); len(errs) != 0 {
		t.Fatalf("architecture.json is not a valid installer SddcSpec:\n%s", strings.Join(errs, "\n"))
	}
}

type inventory struct {
	EstateID      string `json:"estateId"`
	TargetRelease string `json:"targetRelease"`
	Site          struct {
		ID string `json:"id"`
	} `json:"site"`
	Design struct {
		Model           string   `json:"model"`
		SddcID          string   `json:"sddcId"`
		Hosts           []string `json:"hosts"`
		VCenterHostname string   `json:"vcenterHostname"`
		NSXManagers     []string `json:"nsxManagers"`
	} `json:"design"`
	Components []inventoryComponent `json:"components"`
}

type inventoryComponent struct {
	ID      string `json:"id"`
	Name    string `json:"name"`
	Version string `json:"version"`
}

type snapshot struct {
	TargetRelease string `json:"targetRelease"`
	Architecture  struct {
		SiteCount        int    `json:"siteCount"`
		Model            string `json:"model"`
		Storage          string `json:"storage"`
		MinimumHostCount int    `json:"minimumHostCount"`
	} `json:"architecture"`
	Components       []componentRule        `json:"components"`
	Precedence       []precedenceRule       `json:"precedence"`
	Interoperability []interoperabilityRule `json:"interoperability"`
}

type componentRule struct {
	ID            string        `json:"id"`
	TargetVersion string        `json:"targetVersion"`
	UpgradeEdges  []upgradeEdge `json:"upgradeEdges"`
}

type upgradeEdge struct {
	From   string `json:"from"`
	To     string `json:"to"`
	Action string `json:"action"`
}

type precedenceRule struct {
	Before string `json:"before"`
	After  string `json:"after"`
}

type interoperabilityRule struct {
	LeftComponent  string `json:"leftComponent"`
	LeftVersion    string `json:"leftVersion"`
	RightComponent string `json:"rightComponent"`
	RightVersion   string `json:"rightVersion"`
}

type artifact struct {
	SddcID       string `json:"sddcId"`
	WorkflowType string `json:"workflowType"`
	Version      string `json:"version"`
	HostSpecs    []struct {
		Hostname string `json:"hostname"`
	} `json:"hostSpecs"`
	VCenterSpec struct {
		Hostname    string `json:"vcenterHostname"`
		Version     string `json:"version"`
		UseExisting bool   `json:"useExistingDeployment"`
	} `json:"vcenterSpec"`
	NSXTSpec struct {
		Managers []struct {
			Hostname string `json:"hostname"`
		} `json:"nsxtManagers"`
		Version     string `json:"version"`
		UseExisting bool   `json:"useExistingDeployment"`
	} `json:"nsxtSpec"`
	Architecture struct {
		SiteCount        int    `json:"siteCount"`
		SiteID           string `json:"siteId"`
		Model            string `json:"model"`
		ManagementDomain string `json:"managementDomain"`
		HostCount        int    `json:"hostCount"`
		Storage          string `json:"storage"`
	} `json:"x-architecture"`
	MigrationPlan migrationPlan `json:"x-migrationPlan"`
}

type migrationPlan struct {
	SchemaVersion string          `json:"schemaVersion"`
	EstateID      string          `json:"estateId"`
	TargetRelease string          `json:"targetRelease"`
	SiteID        string          `json:"siteId"`
	Model         string          `json:"model"`
	Steps         []migrationStep `json:"steps"`
}

type migrationStep struct {
	Order          int      `json:"order"`
	StepID         string   `json:"stepId"`
	ComponentID    string   `json:"componentId"`
	Component      string   `json:"component"`
	CurrentVersion string   `json:"currentVersion"`
	TargetVersion  string   `json:"targetVersion"`
	Action         string   `json:"action"`
	Gates          []string `json:"gates"`
}

func decodeInto(t *testing.T, path string, target any) {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func TestArtifactPlanAgainstPinnedAuthority(t *testing.T) {
	// The migration schema is checked before fixture/snapshot semantics.
	planSchemaRaw := decodeJSONFile(t, rootPath("schemas", "migration-plan.schema.json"))
	planSchema, ok := planSchemaRaw.(map[string]any)
	if !ok {
		t.Fatal("migration plan schema is not an object")
	}
	artifactRaw := decodeJSONFile(t, rootPath("architecture.json"))
	artifactObject, ok := artifactRaw.(map[string]any)
	if !ok {
		t.Fatal("artifact is not an object")
	}
	planRaw, ok := artifactObject["x-migrationPlan"]
	if !ok {
		t.Fatal("artifact has no x-migrationPlan")
	}
	if errs := (schemaValidator{root: planSchema}).validate("$.x-migrationPlan", planRaw, planSchema); len(errs) != 0 {
		t.Fatalf("migration plan schema validation failed:\n%s", strings.Join(errs, "\n"))
	}

	var inv inventory
	var snap snapshot
	var art artifact
	decodeInto(t, rootPath("fixtures", "estate_inventory.json"), &inv)
	decodeInto(t, rootPath("fixtures", "compatibility_snapshot.json"), &snap)
	decodeInto(t, rootPath("architecture.json"), &art)
	checkArchitecture(t, art, inv, snap)
	checkPlan(t, art.MigrationPlan, inv, snap)
}

func checkArchitecture(t *testing.T, art artifact, inv inventory, snap snapshot) {
	t.Helper()
	if art.SddcID != inv.Design.SddcID || art.WorkflowType != "VCF" || art.Version != snap.TargetRelease {
		t.Errorf("SddcSpec identity/workflow/version does not match inventory and snapshot")
	}
	if art.Architecture.SiteCount != snap.Architecture.SiteCount ||
		art.Architecture.SiteID != inv.Site.ID ||
		art.Architecture.Model != snap.Architecture.Model ||
		art.Architecture.ManagementDomain != inv.Design.SddcID ||
		art.Architecture.HostCount != snap.Architecture.MinimumHostCount ||
		art.Architecture.Storage != snap.Architecture.Storage {
		t.Errorf("x-architecture does not describe the pinned minimum consolidated design")
	}
	if len(art.HostSpecs) != snap.Architecture.MinimumHostCount || len(inv.Design.Hosts) != snap.Architecture.MinimumHostCount {
		t.Fatalf("host count is not the pinned minimum of %d", snap.Architecture.MinimumHostCount)
	}
	wantedHosts := make(map[string]int, len(inv.Design.Hosts))
	for _, hostname := range inv.Design.Hosts {
		wantedHosts[hostname]++
	}
	for _, host := range art.HostSpecs {
		wantedHosts[host.Hostname]--
	}
	for hostname, difference := range wantedHosts {
		if difference != 0 {
			t.Errorf("hostSpecs do not preserve inventory host %q", hostname)
		}
	}
	if !art.VCenterSpec.UseExisting || art.VCenterSpec.Hostname != inv.Design.VCenterHostname {
		t.Errorf("vCenter must reuse inventory deployment %q", inv.Design.VCenterHostname)
	}
	if !art.NSXTSpec.UseExisting || len(art.NSXTSpec.Managers) != len(inv.Design.NSXManagers) {
		t.Errorf("NSX must reuse all inventory managers")
	} else {
		wantedManagers := make(map[string]int, len(inv.Design.NSXManagers))
		for _, hostname := range inv.Design.NSXManagers {
			wantedManagers[hostname]++
		}
		for _, manager := range art.NSXTSpec.Managers {
			wantedManagers[manager.Hostname]--
		}
		for hostname, difference := range wantedManagers {
			if difference != 0 {
				t.Errorf("NSX managers do not preserve inventory manager %q", hostname)
			}
		}
	}
	targets := make(map[string]string, len(snap.Components))
	for _, rule := range snap.Components {
		targets[rule.ID] = rule.TargetVersion
	}
	if art.VCenterSpec.Version != targets["vcenter"] || art.NSXTSpec.Version != targets["nsx"] {
		t.Errorf("SddcSpec existing component versions do not match pinned targets")
	}
}

func checkPlan(t *testing.T, plan migrationPlan, inv inventory, snap snapshot) {
	t.Helper()
	if plan.SchemaVersion != "1.0" || plan.EstateID != inv.EstateID ||
		plan.TargetRelease != snap.TargetRelease || plan.SiteID != inv.Site.ID ||
		plan.Model != snap.Architecture.Model {
		t.Fatalf("migration plan metadata does not match inventory/snapshot")
	}
	inventoryByID := map[string]inventoryComponent{}
	for _, component := range inv.Components {
		if _, exists := inventoryByID[component.ID]; exists {
			t.Fatalf("duplicate inventory component %q", component.ID)
		}
		inventoryByID[component.ID] = component
	}
	rulesByID := map[string]componentRule{}
	for _, rule := range snap.Components {
		rulesByID[rule.ID] = rule
	}

	seenStep := map[string]int{}
	stepsByComponent := map[string][]migrationStep{}
	for index, step := range plan.Steps {
		if step.Order != index+1 {
			t.Fatalf("step %q order = %d, want %d", step.StepID, step.Order, index+1)
		}
		if _, duplicate := seenStep[step.StepID]; duplicate {
			t.Fatalf("duplicate stepId %q", step.StepID)
		}
		seenStep[step.StepID] = step.Order
		component, exists := inventoryByID[step.ComponentID]
		if !exists {
			t.Fatalf("step %q names component %q not present in inventory", step.StepID, step.ComponentID)
		}
		if step.Component != component.Name {
			t.Errorf("step %q component name = %q, want %q", step.StepID, step.Component, component.Name)
		}
		for _, gate := range step.Gates {
			gateOrder, exists := seenStep[gate]
			if !exists || gateOrder >= step.Order {
				t.Errorf("step %q gate %q does not refer to an earlier step", step.StepID, gate)
			}
		}
		stepsByComponent[step.ComponentID] = append(stepsByComponent[step.ComponentID], step)
	}
	if len(stepsByComponent) != len(inventoryByID) {
		t.Fatalf("plan covers %d components, inventory has %d", len(stepsByComponent), len(inventoryByID))
	}

	firstOrder := map[string]int{}
	lastOrder := map[string]int{}
	lastStepID := map[string]string{}
	finalVersions := map[string]string{}
	for id, component := range inventoryByID {
		steps := stepsByComponent[id]
		if len(steps) == 0 {
			t.Fatalf("inventory component %q is absent from plan", id)
		}
		rule, exists := rulesByID[id]
		if !exists {
			t.Fatalf("snapshot has no rule for inventory component %q", id)
		}
		currentVersion := component.Version
		for i, step := range steps {
			if step.CurrentVersion != currentVersion {
				t.Errorf("component %q transition %d starts at %q, want %q", id, i, step.CurrentVersion, currentVersion)
			}
			supported := false
			for _, edge := range rule.UpgradeEdges {
				if edge.From == step.CurrentVersion && edge.To == step.TargetVersion && edge.Action == step.Action {
					supported = true
					break
				}
			}
			if !supported {
				t.Errorf("component %q transition %d is not a pinned upgrade edge: %s -> %s (%s)",
					id, i, step.CurrentVersion, step.TargetVersion, step.Action)
			}
			if i > 0 && !contains(step.Gates, steps[i-1].StepID) {
				t.Errorf("component %q transition %d is not gated by its prior transition %q", id, i, steps[i-1].StepID)
			}
			currentVersion = step.TargetVersion
		}
		firstOrder[id] = steps[0].Order
		lastOrder[id] = steps[len(steps)-1].Order
		lastStepID[id] = steps[len(steps)-1].StepID
		finalVersions[id] = currentVersion
		if finalVersions[id] != rule.TargetVersion {
			t.Errorf("component %q finishes at %q, want %q", id, finalVersions[id], rule.TargetVersion)
		}
	}

	for _, precedence := range snap.Precedence {
		if lastOrder[precedence.Before] >= firstOrder[precedence.After] {
			t.Errorf("component %q must finish before %q starts", precedence.Before, precedence.After)
		}
		firstAfter := stepsByComponent[precedence.After][0]
		if !contains(firstAfter.Gates, lastStepID[precedence.Before]) {
			t.Errorf("first %q step must be explicitly gated by final %q step %q", precedence.After, precedence.Before, lastStepID[precedence.Before])
		}
	}
	for _, pair := range snap.Interoperability {
		if finalVersions[pair.LeftComponent] != pair.LeftVersion || finalVersions[pair.RightComponent] != pair.RightVersion {
			t.Errorf("final versions violate pinned interoperability pair %s/%s", pair.LeftComponent, pair.RightComponent)
		}
	}
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

func TestBuildIsDeterministicAndProducesArtifact(t *testing.T) {
	invFile, err := os.Open(rootPath("fixtures", "estate_inventory.json"))
	if err != nil {
		t.Fatal(err)
	}
	inv, err := vcfarch.LoadInventory(invFile)
	invFile.Close()
	if err != nil {
		t.Fatal(err)
	}
	snapshotFile, err := os.Open(rootPath("fixtures", "compatibility_snapshot.json"))
	if err != nil {
		t.Fatal(err)
	}
	snap, err := vcfarch.LoadCompatibility(snapshotFile)
	snapshotFile.Close()
	if err != nil {
		t.Fatal(err)
	}
	first, err := vcfarch.Build(inv, snap)
	if err != nil {
		t.Fatalf("Build returned error: %v", err)
	}
	second, err := vcfarch.Build(inv, snap)
	if err != nil {
		t.Fatalf("second Build returned error: %v", err)
	}
	if !reflect.DeepEqual(first, second) {
		t.Fatal("Build is nondeterministic for identical inputs")
	}
	generated, err := json.Marshal(first)
	if err != nil {
		t.Fatal(err)
	}
	committed, err := os.ReadFile(rootPath("architecture.json"))
	if err != nil {
		t.Fatal(err)
	}
	var generatedValue, committedValue any
	if err := json.Unmarshal(generated, &generatedValue); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(committed, &committedValue); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(generatedValue, committedValue) {
		t.Fatal("committed architecture.json was not produced by Build")
	}
}
