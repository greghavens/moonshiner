package grader_tests

import (
	"bytes"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"vcfarch/migration"
)

type migrationPlan struct {
	SchemaVersion    string          `json:"schemaVersion"`
	EstateID         string          `json:"estateId"`
	SourceVCFVersion string          `json:"sourceVcfVersion"`
	TargetVCFVersion string          `json:"targetVcfVersion"`
	SelectedTopology string          `json:"selectedTopology"`
	Entitlement      planEntitlement `json:"entitlement"`
	UpgradePath      []string        `json:"upgradePath"`
	TargetSpec       map[string]any  `json:"targetSpec"`
	Components       []planComponent `json:"components"`
	Steps            []operation     `json:"steps"`
}

type planEntitlement struct {
	Edition       string `json:"edition"`
	LicensedCores int    `json:"licensedCores"`
	RequiredCores int    `json:"requiredCores"`
}

type planComponent struct {
	ID             string   `json:"id"`
	Name           string   `json:"name"`
	Domain         string   `json:"domain"`
	CurrentVersion string   `json:"currentVersion"`
	Target         string   `json:"target"`
	TargetState    string   `json:"targetState"`
	Gates          []string `json:"gates"`
}

type operation struct {
	Order      int      `json:"order"`
	ID         string   `json:"id"`
	Phase      string   `json:"phase"`
	Action     string   `json:"action"`
	Components []string `json:"components"`
	From       string   `json:"from"`
	To         string   `json:"to"`
	Gates      []string `json:"gates"`
	Requires   []string `json:"requires"`
}

type estate struct {
	EstateID         string `json:"estateId"`
	SourceVCFVersion string `json:"sourceVcfVersion"`
	TargetVCFVersion string `json:"targetVcfVersion"`
	Entitlement      struct {
		Edition       string `json:"edition"`
		LicensedCores int    `json:"licensedCores"`
	} `json:"entitlement"`
	Domains []struct {
		ID    string `json:"id"`
		Hosts []struct {
			ID       string `json:"id"`
			Hostname string `json:"hostname"`
		} `json:"hosts"`
	} `json:"domains"`
	Components []struct {
		ID             string `json:"id"`
		Name           string `json:"name"`
		Domain         string `json:"domain"`
		CurrentVersion string `json:"currentVersion"`
	} `json:"components"`
}

type compatibilitySnapshot struct {
	SourceVCFVersion string `json:"sourceVcfVersion"`
	TargetVCFVersion string `json:"targetVcfVersion"`
	SupportedVCFHops []struct {
		From string `json:"from"`
		To   string `json:"to"`
	} `json:"supportedVcfHops"`
	GateIDs    []string `json:"gateIds"`
	Topologies []struct {
		ID               string   `json:"id"`
		Supported        bool     `json:"supported"`
		RequiredVCFCores int      `json:"requiredVcfCores"`
		HostIDs          []string `json:"hostIds"`
		RetireDomains    []string `json:"retireDomains"`
	} `json:"topologies"`
	ComponentTargets map[string]struct {
		Target        string   `json:"target"`
		TargetState   string   `json:"targetState"`
		RequiredGates []string `json:"requiredGates"`
	} `json:"componentTargets"`
	RequiredOperations []operation `json:"requiredOperations"`
}

func TestMigrationPlanAcceptance(t *testing.T) {
	root := ".."
	planBytes, err := os.ReadFile(root + "/migration_plan.json")
	if err != nil {
		t.Fatalf("read migration_plan.json: %v", err)
	}

	// The first validation is deliberately the targetSpec against the SddcSpec
	// schema in the pinned, unmodified VCF Installer OpenAPI document. Do not
	// load the fixture or compatibility authority before this succeeds.
	var rawPlan map[string]any
	if err := decodeJSON(planBytes, &rawPlan); err != nil {
		t.Fatalf("decode migration_plan.json for installer-schema validation: %v", err)
	}
	targetSpec, ok := rawPlan["targetSpec"]
	if !ok {
		t.Fatal("installer schema validation: targetSpec is missing")
	}
	openAPI := mustReadJSON(t, root+"/specifications/vcf-installer/vcf-installer-openapi.json")
	sddcSchema, err := jsonPointer(openAPI, "#/components/schemas/SddcSpec")
	if err != nil {
		t.Fatalf("locate SddcSpec in installer specification: %v", err)
	}
	if err := validateSchema(targetSpec, sddcSchema, openAPI, "$.targetSpec"); err != nil {
		t.Fatalf("installer SddcSpec validation failed: %v", err)
	}

	// Only after installer-schema validation passes may the plan schema and the
	// pinned scenario authority be consulted.
	planSchema := mustReadJSON(t, root+"/schemas/migration-plan.schema.json")
	if err := validateSchema(rawPlan, planSchema, planSchema, "$"); err != nil {
		t.Fatalf("migration-plan schema validation failed: %v", err)
	}

	var got migrationPlan
	if err := decodeJSON(planBytes, &got); err != nil {
		t.Fatalf("decode typed migration plan: %v", err)
	}
	estateBytes := mustReadFile(t, root+"/fixtures/estate.json")
	compatibilityBytes := mustReadFile(t, root+"/compatibility/vcf-compatibility-2026-08-14.json")
	var inv estate
	mustDecode(t, estateBytes, root+"/fixtures/estate.json", &inv)
	var compat compatibilitySnapshot
	mustDecode(t, compatibilityBytes, root+"/compatibility/vcf-compatibility-2026-08-14.json", &compat)

	checkIdentityAndPath(t, got, inv, compat)
	selected := checkEntitledTopology(t, got, inv, compat)
	checkTargetSpecSemantics(t, got, inv, selected.HostIDs)
	checkComponents(t, got, inv, compat)
	checkOperations(t, got, compat)
	checkRetiredDomains(t, got, inv, selected.RetireDomains)
	checkPlannerGeneration(t, rawPlan, estateBytes, compatibilityBytes)
	checkPlannerInputConsumption(t, estateBytes, compatibilityBytes)
	checkPlannerDecisionFailures(t, estateBytes, compatibilityBytes)
	checkMigrationPackageTests(t, root+"/migration")
}

func TestResearchRecord(t *testing.T) {
	root := ".."
	research := string(mustReadFile(t, root+"/RESEARCH.md"))
	entryPattern := regexp.MustCompile(`^- \*\*(.+)\*\* — (https://\S+) — Accessed: (\d{4}-\d{2}-\d{2}) UTC — Decision: (.+)$`)
	seenURLs := map[string]bool{}
	entries := 0
	coversCompatibility := false
	coversSequencing := false
	for _, rawLine := range strings.Split(research, "\n") {
		line := strings.TrimSuffix(rawLine, "\r")
		if !strings.HasPrefix(line, "- ") {
			continue
		}
		match := entryPattern.FindStringSubmatch(line)
		if match == nil {
			t.Fatalf("research source entry does not use the required format: %q", line)
		}
		title, rawURL, accessed, decision := match[1], match[2], match[3], match[4]
		if strings.TrimSpace(title) == "" || strings.TrimSpace(decision) == "" {
			t.Fatal("research source title and decision must be nonempty")
		}
		parsed, err := url.ParseRequestURI(rawURL)
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" {
			t.Fatalf("research source URL must be an absolute HTTPS URL: %q", rawURL)
		}
		host := strings.ToLower(parsed.Hostname())
		if host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com") {
			t.Fatalf("research source is not Broadcom-published: %q", rawURL)
		}
		if _, err := time.Parse("2006-01-02", accessed); err != nil {
			t.Fatalf("research source has invalid UTC access date %q", accessed)
		}
		if seenURLs[rawURL] {
			t.Fatalf("duplicate research source URL %q", rawURL)
		}
		seenURLs[rawURL] = true
		evidence := strings.ToLower(title + " " + decision)
		coversCompatibility = coversCompatibility || containsSubstring(evidence, "compatib", "interop", "supported", "bom", "bill of materials", "constituent", "combination")
		coversSequencing = coversSequencing || containsSubstring(evidence, "upgrade", "sequence", "order", "before", "after", "preced", "hop", "path", "first", "later")
		entries++
	}
	if entries < 2 {
		t.Fatalf("RESEARCH.md records %d Broadcom sources, want at least 2", entries)
	}
	if !coversCompatibility || !coversSequencing {
		t.Fatalf("RESEARCH.md must record concrete compatibility/interoperability and upgrade/sequencing findings (compatibility=%t sequencing=%t)", coversCompatibility, coversSequencing)
	}
}

type selectedTopology struct {
	HostIDs       []string
	RetireDomains []string
}

func checkIdentityAndPath(t *testing.T, got migrationPlan, inv estate, compat compatibilitySnapshot) {
	t.Helper()
	if got.SchemaVersion != "1.0.0" || got.EstateID != inv.EstateID {
		t.Fatalf("plan identity does not match schema/fixture: schema=%q estate=%q", got.SchemaVersion, got.EstateID)
	}
	if got.SourceVCFVersion != inv.SourceVCFVersion || got.SourceVCFVersion != compat.SourceVCFVersion {
		t.Fatalf("source VCF version mismatch: %q", got.SourceVCFVersion)
	}
	if got.TargetVCFVersion != inv.TargetVCFVersion || got.TargetVCFVersion != compat.TargetVCFVersion {
		t.Fatalf("target VCF version mismatch: %q", got.TargetVCFVersion)
	}
	if len(got.UpgradePath) < 2 || got.UpgradePath[0] != got.SourceVCFVersion || got.UpgradePath[len(got.UpgradePath)-1] != got.TargetVCFVersion {
		t.Fatalf("upgradePath must run from %s to %s", got.SourceVCFVersion, got.TargetVCFVersion)
	}
	seen := map[string]bool{}
	for i, version := range got.UpgradePath {
		if seen[version] {
			t.Fatalf("upgradePath repeats version %q", version)
		}
		seen[version] = true
		if i == 0 {
			continue
		}
		if !supportedHop(compat, got.UpgradePath[i-1], version) {
			t.Fatalf("unsupported VCF hop %s -> %s", got.UpgradePath[i-1], version)
		}
	}
}

func supportedHop(compat compatibilitySnapshot, from, to string) bool {
	for _, hop := range compat.SupportedVCFHops {
		if hop.From == from && hop.To == to {
			return true
		}
	}
	return false
}

func checkEntitledTopology(t *testing.T, got migrationPlan, inv estate, compat compatibilitySnapshot) selectedTopology {
	t.Helper()
	if got.Entitlement.Edition != inv.Entitlement.Edition || got.Entitlement.LicensedCores != inv.Entitlement.LicensedCores {
		t.Fatalf("artifact entitlement does not match fixture: %+v", got.Entitlement)
	}
	excludedSupported := 0
	var selected *selectedTopology
	for _, topology := range compat.Topologies {
		if topology.Supported && topology.RequiredVCFCores > inv.Entitlement.LicensedCores {
			excludedSupported++
		}
		if topology.ID == got.SelectedTopology {
			if !topology.Supported {
				t.Fatalf("selected topology %q is not supported", topology.ID)
			}
			if topology.RequiredVCFCores > inv.Entitlement.LicensedCores {
				t.Fatalf("selected topology %q requires %d VCF cores but only %d are entitled", topology.ID, topology.RequiredVCFCores, inv.Entitlement.LicensedCores)
			}
			if got.Entitlement.RequiredCores != topology.RequiredVCFCores {
				t.Fatalf("required core count %d does not match topology %d", got.Entitlement.RequiredCores, topology.RequiredVCFCores)
			}
			selected = &selectedTopology{HostIDs: topology.HostIDs, RetireDomains: topology.RetireDomains}
		}
	}
	if excludedSupported == 0 {
		t.Fatal("fixture does not demonstrate an otherwise supported topology removed by entitlement")
	}
	if selected == nil {
		t.Fatalf("selected topology %q is absent from compatibility snapshot", got.SelectedTopology)
	}
	return *selected
}

func checkTargetSpecSemantics(t *testing.T, got migrationPlan, inv estate, hostIDs []string) {
	t.Helper()
	if got.TargetSpec["version"] != got.TargetVCFVersion {
		t.Fatalf("targetSpec.version must be %q", got.TargetVCFVersion)
	}
	if got.TargetSpec["workflowType"] != "VCF" {
		t.Fatal("targetSpec.workflowType must be VCF")
	}
	vc, ok := got.TargetSpec["vcenterSpec"].(map[string]any)
	if !ok || vc["useExistingDeployment"] != true {
		t.Fatal("targetSpec must describe reuse of the existing vCenter deployment")
	}
	hostnameByID := map[string]string{}
	for _, domain := range inv.Domains {
		for _, host := range domain.Hosts {
			hostnameByID[host.ID] = host.Hostname
		}
	}
	wantHosts := make([]string, 0, len(hostIDs))
	for _, id := range hostIDs {
		hostname, ok := hostnameByID[id]
		if !ok {
			t.Fatalf("snapshot topology references unknown host %q", id)
		}
		wantHosts = append(wantHosts, hostname)
	}
	rawHosts, ok := got.TargetSpec["hostSpecs"].([]any)
	if !ok {
		t.Fatal("targetSpec.hostSpecs must list the retained topology hosts")
	}
	gotHosts := make([]string, 0, len(rawHosts))
	for _, raw := range rawHosts {
		host, ok := raw.(map[string]any)
		if !ok {
			t.Fatal("targetSpec.hostSpecs contains a non-object")
		}
		hostname, ok := host["hostname"].(string)
		if !ok {
			t.Fatal("targetSpec host lacks hostname")
		}
		gotHosts = append(gotHosts, hostname)
	}
	sort.Strings(wantHosts)
	sort.Strings(gotHosts)
	if !reflect.DeepEqual(gotHosts, wantHosts) {
		t.Fatalf("targetSpec hosts %v do not match selected topology hosts %v", gotHosts, wantHosts)
	}
}

func checkComponents(t *testing.T, got migrationPlan, inv estate, compat compatibilitySnapshot) {
	t.Helper()
	validGates := stringSet(compat.GateIDs)
	if len(got.Components) != len(inv.Components) || len(compat.ComponentTargets) != len(inv.Components) {
		t.Fatalf("components must name every fixture component exactly once")
	}
	gotByID := map[string]planComponent{}
	for _, component := range got.Components {
		if _, duplicate := gotByID[component.ID]; duplicate {
			t.Fatalf("duplicate component %q", component.ID)
		}
		gotByID[component.ID] = component
		for _, gate := range component.Gates {
			if !validGates[gate] {
				t.Fatalf("component %q uses unknown gate %q", component.ID, gate)
			}
		}
	}
	for _, source := range inv.Components {
		component, ok := gotByID[source.ID]
		if !ok {
			t.Fatalf("fixture component %q is missing", source.ID)
		}
		if component.Name != source.Name || component.Domain != source.Domain || component.CurrentVersion != source.CurrentVersion {
			t.Fatalf("component %q does not preserve fixture name/domain/version", source.ID)
		}
		rule, ok := compat.ComponentTargets[source.ID]
		if !ok {
			t.Fatalf("compatibility snapshot lacks target for %q", source.ID)
		}
		if component.Target != rule.Target || component.TargetState != rule.TargetState {
			t.Fatalf("component %q target is %q/%q, want %q/%q", source.ID, component.Target, component.TargetState, rule.Target, rule.TargetState)
		}
		if !containsAll(component.Gates, rule.RequiredGates) {
			t.Fatalf("component %q gates %v omit required gates %v", source.ID, component.Gates, rule.RequiredGates)
		}
	}
}

func checkOperations(t *testing.T, got migrationPlan, compat compatibilitySnapshot) {
	t.Helper()
	if len(got.Steps) != len(compat.RequiredOperations) {
		t.Fatalf("got %d migration steps, want %d pinned operations", len(got.Steps), len(compat.RequiredOperations))
	}
	validGates := stringSet(compat.GateIDs)
	wantByID := map[string]operation{}
	for _, op := range compat.RequiredOperations {
		wantByID[op.ID] = op
	}
	seen := map[string]int{}
	covered := map[string]bool{}
	for i, step := range got.Steps {
		if step.Order != i+1 {
			t.Fatalf("step %q has order %d, want %d", step.ID, step.Order, i+1)
		}
		if _, duplicate := seen[step.ID]; duplicate {
			t.Fatalf("duplicate step %q", step.ID)
		}
		want, ok := wantByID[step.ID]
		if !ok {
			t.Fatalf("step %q is not a pinned operation", step.ID)
		}
		if step.Phase != want.Phase || step.Action != want.Action || step.From != want.From || step.To != want.To {
			t.Fatalf("step %q operation does not match pinned compatibility rule", step.ID)
		}
		if !sameStrings(step.Components, want.Components) || !containsAll(step.Gates, want.Gates) || !containsAll(step.Requires, want.Requires) {
			t.Fatalf("step %q components, gates, or prerequisites do not match pinned rule", step.ID)
		}
		for _, gate := range step.Gates {
			if !validGates[gate] {
				t.Fatalf("step %q uses unknown gate %q", step.ID, gate)
			}
		}
		for _, prerequisite := range step.Requires {
			position, exists := seen[prerequisite]
			if !exists || position >= i {
				t.Fatalf("step %q prerequisite %q is not an earlier step", step.ID, prerequisite)
			}
		}
		seen[step.ID] = i
		if step.ID != "preflight" && step.ID != "validate-target" {
			for _, component := range step.Components {
				covered[component] = true
			}
		}
	}
	for component := range compat.ComponentTargets {
		if !covered[component] {
			t.Fatalf("component %q has no migration operation", component)
		}
	}
}

func checkRetiredDomains(t *testing.T, got migrationPlan, inv estate, retired []string) {
	t.Helper()
	retiredSet := stringSet(retired)
	components := map[string]planComponent{}
	for _, component := range got.Components {
		components[component.ID] = component
	}
	for _, source := range inv.Components {
		if retiredSet[source.Domain] && components[source.ID].TargetState != "decommissioned" {
			t.Fatalf("component %q remains present in retired domain %q", source.ID, source.Domain)
		}
	}
}

func checkPlannerGeneration(t *testing.T, committed map[string]any, estateJSON, compatibilityJSON []byte) {
	t.Helper()
	generated, err := migration.BuildPlan(estateJSON, compatibilityJSON)
	if err != nil {
		t.Fatalf("BuildPlan with the protected inputs failed: %v", err)
	}
	var generatedPlan map[string]any
	if err := decodeJSON(generated, &generatedPlan); err != nil {
		t.Fatalf("decode BuildPlan output: %v", err)
	}
	if !jsonEqual(generatedPlan, committed) {
		t.Fatal("migration_plan.json is not the plan produced by BuildPlan from the protected inputs")
	}
}

func checkPlannerInputConsumption(t *testing.T, estateJSON, compatibilityJSON []byte) {
	t.Helper()
	var inv map[string]any
	if err := decodeJSON(estateJSON, &inv); err != nil {
		t.Fatal(err)
	}
	var compat map[string]any
	if err := decodeJSON(compatibilityJSON, &compat); err != nil {
		t.Fatal(err)
	}
	inv["estateId"] = "chi01-vcf-input-check"
	topologies := compat["topologies"].([]any)
	entitled := topologies[1].(map[string]any)
	entitled["id"] = "consolidated-input-check"
	mutatedEstate, err := json.Marshal(inv)
	if err != nil {
		t.Fatal(err)
	}
	mutatedCompatibility, err := json.Marshal(compat)
	if err != nil {
		t.Fatal(err)
	}
	generated, err := migration.BuildPlan(mutatedEstate, mutatedCompatibility)
	if err != nil {
		t.Fatalf("BuildPlan rejected valid input changes: %v", err)
	}
	var got migrationPlan
	if err := decodeJSON(generated, &got); err != nil {
		t.Fatalf("decode BuildPlan output for input-consumption check: %v", err)
	}
	if got.EstateID != "chi01-vcf-input-check" || got.SelectedTopology != "consolidated-input-check" {
		t.Fatalf("BuildPlan did not consume estate/topology input changes: estate=%q topology=%q", got.EstateID, got.SelectedTopology)
	}
}

func checkPlannerDecisionFailures(t *testing.T, estateJSON, compatibilityJSON []byte) {
	t.Helper()
	tests := []struct {
		name   string
		mutate func(map[string]any, map[string]any)
	}{
		{
			name: "no topology fits entitlement",
			mutate: func(inv, _ map[string]any) {
				inv["entitlement"].(map[string]any)["licensedCores"] = json.Number("32")
			},
		},
		{
			name: "supported hop is absent",
			mutate: func(_ map[string]any, compat map[string]any) {
				compat["supportedVcfHops"] = []any{}
			},
		},
	}
	for _, tt := range tests {
		t.Run("BuildPlan rejects "+tt.name, func(t *testing.T) {
			var inv map[string]any
			if err := decodeJSON(estateJSON, &inv); err != nil {
				t.Fatal(err)
			}
			var compat map[string]any
			if err := decodeJSON(compatibilityJSON, &compat); err != nil {
				t.Fatal(err)
			}
			tt.mutate(inv, compat)
			mutatedEstate, err := json.Marshal(inv)
			if err != nil {
				t.Fatal(err)
			}
			mutatedCompatibility, err := json.Marshal(compat)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := migration.BuildPlan(mutatedEstate, mutatedCompatibility); err == nil {
				t.Fatalf("BuildPlan accepted %s", tt.name)
			}
		})
	}
}

func checkMigrationPackageTests(t *testing.T, migrationDir string) {
	t.Helper()
	paths, err := filepath.Glob(filepath.Join(migrationDir, "*_test.go"))
	if err != nil {
		t.Fatal(err)
	}
	if len(paths) == 0 {
		t.Fatal("migration package has no Go test file")
	}
	fset := token.NewFileSet()
	foundTest := false
	foundTableDrivenTest := false
	var source strings.Builder
	for _, path := range paths {
		contents := mustReadFile(t, path)
		source.Write(contents)
		parsed, err := parser.ParseFile(fset, path, contents, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", path, err)
		}
		for _, declaration := range parsed.Decls {
			function, ok := declaration.(*ast.FuncDecl)
			if !ok || !strings.HasPrefix(function.Name.Name, "Test") || function.Body == nil {
				continue
			}
			foundTest = true
			hasLoop := false
			hasCaseTable := false
			ast.Inspect(function.Body, func(node ast.Node) bool {
				switch node.(type) {
				case *ast.RangeStmt, *ast.ForStmt:
					hasLoop = true
				case *ast.CompositeLit:
					hasCaseTable = true
				}
				return true
			})
			foundTableDrivenTest = foundTableDrivenTest || (hasLoop && hasCaseTable)
		}
	}
	if !foundTest || !foundTableDrivenTest {
		t.Fatal("migration package must include a table-driven Go test with a case table and loop")
	}
	lowerSource := strings.ToLower(source.String())
	if !strings.Contains(lowerSource, "entitlement") || (!strings.Contains(lowerSource, "hop") && !strings.Contains(lowerSource, "path")) {
		t.Fatal("migration package tests do not identify both entitlement and supported-hop/path coverage")
	}
}

func mustReadJSON(t *testing.T, path string) any {
	t.Helper()
	b := mustReadFile(t, path)
	var value any
	if err := decodeJSON(b, &value); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
	return value
}

func mustReadFile(t *testing.T, path string) []byte {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return b
}

func mustDecode(t *testing.T, b []byte, name string, dst any) {
	t.Helper()
	if err := decodeJSON(b, dst); err != nil {
		t.Fatalf("decode %s: %v", name, err)
	}
}

func decodeJSON(b []byte, dst any) error {
	decoder := json.NewDecoder(bytes.NewReader(b))
	decoder.UseNumber()
	if err := decoder.Decode(dst); err != nil {
		return err
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		if err == nil {
			return fmt.Errorf("multiple JSON values")
		}
		return fmt.Errorf("trailing data: %w", err)
	}
	return nil
}

func validateSchema(instance, rawSchema, root any, path string) error {
	if booleanSchema, ok := rawSchema.(bool); ok {
		if booleanSchema {
			return nil
		}
		return fmt.Errorf("%s rejected by false schema", path)
	}
	schema, ok := rawSchema.(map[string]any)
	if !ok {
		return fmt.Errorf("%s: schema is not an object", path)
	}
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := jsonPointer(root, ref)
		if err != nil {
			return fmt.Errorf("%s: resolve %s: %w", path, ref, err)
		}
		if err := validateSchema(instance, resolved, root, path); err != nil {
			return err
		}
	}
	if expected, exists := schema["const"]; exists && !jsonEqual(instance, expected) {
		return fmt.Errorf("%s must equal %v", path, expected)
	}
	if enum, ok := schema["enum"].([]any); ok {
		matched := false
		for _, allowed := range enum {
			matched = matched || jsonEqual(instance, allowed)
		}
		if !matched {
			return fmt.Errorf("%s is not one of %v", path, enum)
		}
	}
	for _, keyword := range []string{"allOf"} {
		if branches, ok := schema[keyword].([]any); ok {
			for _, branch := range branches {
				if err := validateSchema(instance, branch, root, path); err != nil {
					return err
				}
			}
		}
	}
	for _, keyword := range []string{"anyOf", "oneOf"} {
		if branches, ok := schema[keyword].([]any); ok {
			matches := 0
			for _, branch := range branches {
				if validateSchema(instance, branch, root, path) == nil {
					matches++
				}
			}
			if (keyword == "anyOf" && matches == 0) || (keyword == "oneOf" && matches != 1) {
				return fmt.Errorf("%s fails %s", path, keyword)
			}
		}
	}
	typeName, _ := schema["type"].(string)
	if typeName != "" && !matchesType(instance, typeName) {
		return fmt.Errorf("%s must be %s", path, typeName)
	}
	switch value := instance.(type) {
	case map[string]any:
		if err := validateObject(value, schema, root, path); err != nil {
			return err
		}
	case []any:
		if err := validateArray(value, schema, root, path); err != nil {
			return err
		}
	case string:
		if err := validateString(value, schema, path); err != nil {
			return err
		}
	case json.Number:
		if err := validateNumber(value, schema, path); err != nil {
			return err
		}
	}
	return nil
}

func validateObject(value map[string]any, schema map[string]any, root any, path string) error {
	if required, ok := schema["required"].([]any); ok {
		for _, raw := range required {
			name, _ := raw.(string)
			if _, exists := value[name]; !exists {
				return fmt.Errorf("%s.%s is required", path, name)
			}
		}
	}
	properties, _ := schema["properties"].(map[string]any)
	for name, child := range value {
		if childSchema, exists := properties[name]; exists {
			if err := validateSchema(child, childSchema, root, path+"."+name); err != nil {
				return err
			}
			continue
		}
		if additional, exists := schema["additionalProperties"]; exists {
			switch rule := additional.(type) {
			case bool:
				if !rule {
					return fmt.Errorf("%s.%s is not allowed", path, name)
				}
			case map[string]any:
				if err := validateSchema(child, rule, root, path+"."+name); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

func validateArray(value []any, schema map[string]any, root any, path string) error {
	if min, ok := asInt(schema["minItems"]); ok && len(value) < min {
		return fmt.Errorf("%s has %d items, minimum is %d", path, len(value), min)
	}
	if max, ok := asInt(schema["maxItems"]); ok && len(value) > max {
		return fmt.Errorf("%s has %d items, maximum is %d", path, len(value), max)
	}
	if unique, _ := schema["uniqueItems"].(bool); unique {
		seen := map[string]bool{}
		for _, item := range value {
			encoded, _ := json.Marshal(item)
			key := string(encoded)
			if seen[key] {
				return fmt.Errorf("%s contains duplicate items", path)
			}
			seen[key] = true
		}
	}
	if itemSchema, exists := schema["items"]; exists {
		for i, item := range value {
			if err := validateSchema(item, itemSchema, root, fmt.Sprintf("%s[%d]", path, i)); err != nil {
				return err
			}
		}
	}
	return nil
}

func validateString(value string, schema map[string]any, path string) error {
	length := utf8.RuneCountInString(value)
	if min, ok := asInt(schema["minLength"]); ok && length < min {
		return fmt.Errorf("%s is shorter than %d characters", path, min)
	}
	if max, ok := asInt(schema["maxLength"]); ok && length > max {
		return fmt.Errorf("%s is longer than %d characters", path, max)
	}
	if pattern, ok := schema["pattern"].(string); ok {
		re, err := regexp.Compile(pattern)
		if err != nil {
			return fmt.Errorf("%s has invalid schema pattern: %w", path, err)
		}
		if !re.MatchString(value) {
			return fmt.Errorf("%s does not match %q", path, pattern)
		}
	}
	return nil
}

func validateNumber(value json.Number, schema map[string]any, path string) error {
	n, err := strconv.ParseFloat(value.String(), 64)
	if err != nil {
		return fmt.Errorf("%s is not numeric", path)
	}
	if min, ok := asFloat(schema["minimum"]); ok && n < min {
		return fmt.Errorf("%s is below minimum %v", path, min)
	}
	if max, ok := asFloat(schema["maximum"]); ok && n > max {
		return fmt.Errorf("%s is above maximum %v", path, max)
	}
	return nil
}

func matchesType(value any, typeName string) bool {
	switch typeName {
	case "object":
		_, ok := value.(map[string]any)
		return ok
	case "array":
		_, ok := value.([]any)
		return ok
	case "string":
		_, ok := value.(string)
		return ok
	case "boolean":
		_, ok := value.(bool)
		return ok
	case "null":
		return value == nil
	case "number":
		_, ok := value.(json.Number)
		return ok
	case "integer":
		n, ok := value.(json.Number)
		if !ok {
			return false
		}
		_, err := n.Int64()
		return err == nil
	default:
		return true
	}
}

func jsonPointer(root any, ref string) (any, error) {
	if ref == "#" {
		return root, nil
	}
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("only local references are supported: %q", ref)
	}
	current := root
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%q traverses a non-object", ref)
		}
		current, ok = object[token]
		if !ok {
			return nil, fmt.Errorf("%q not found", ref)
		}
	}
	return current, nil
}

func asInt(value any) (int, bool) {
	switch n := value.(type) {
	case json.Number:
		v, err := n.Int64()
		return int(v), err == nil
	case float64:
		return int(n), true
	default:
		return 0, false
	}
}

func asFloat(value any) (float64, bool) {
	switch n := value.(type) {
	case json.Number:
		v, err := n.Float64()
		return v, err == nil
	case float64:
		return n, true
	default:
		return 0, false
	}
}

func jsonEqual(a, b any) bool {
	ab, _ := json.Marshal(a)
	bb, _ := json.Marshal(b)
	return bytes.Equal(ab, bb)
}

func stringSet(values []string) map[string]bool {
	set := make(map[string]bool, len(values))
	for _, value := range values {
		set[value] = true
	}
	return set
}

func containsAll(values, required []string) bool {
	set := stringSet(values)
	for _, value := range required {
		if !set[value] {
			return false
		}
	}
	return true
}

func containsSubstring(value string, candidates ...string) bool {
	for _, candidate := range candidates {
		if strings.Contains(value, candidate) {
			return true
		}
	}
	return false
}

func sameStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	aa := append([]string(nil), a...)
	bb := append([]string(nil), b...)
	sort.Strings(aa)
	sort.Strings(bb)
	return reflect.DeepEqual(aa, bb)
}
