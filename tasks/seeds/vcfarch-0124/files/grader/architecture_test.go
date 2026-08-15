package grader

import (
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"math"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"vcfarch-0124/architecture"
)

const (
	planPath      = "../migration-plan.json"
	planSchema    = "../migration-plan.schema.json"
	inventoryPath = "../fixtures/estate_inventory.json"
	snapshotPath  = "../fixtures/compatibility_snapshot.json"
	openAPIPath   = "../specifications/vcf-installer/vcf-installer-openapi.json"
	researchPath  = "../research/consulted.json"
)

type inventory struct {
	EstateID         string               `json:"estateId"`
	TargetVCFVersion string               `json:"targetVcfVersion"`
	Components       []inventoryComponent `json:"components"`
	InstallerInputs  installerInputs      `json:"installerInputs"`
}

type inventoryComponent struct {
	ID      string `json:"id"`
	Kind    string `json:"kind"`
	Product string `json:"product"`
	Version string `json:"version"`
	Site    string `json:"site"`
}

type installerInputs struct {
	SddcID              string   `json:"sddcId"`
	WorkflowType        string   `json:"workflowType"`
	VcenterHostname     string   `json:"vcenterHostname"`
	RootVcenterPassword string   `json:"rootVcenterPassword"`
	DNSSubdomain        string   `json:"dnsSubdomain"`
	Nameservers         []string `json:"nameservers"`
	ManagementVLANID    int      `json:"managementVlanId"`
}

type compatibilitySnapshot struct {
	SnapshotVersion   string            `json:"snapshotVersion"`
	TargetVCFVersion  string            `json:"targetVcfVersion"`
	ComponentPolicies []componentPolicy `json:"componentPolicies"`
	Gates             []gate            `json:"gates"`
	OrderConstraints  []orderConstraint `json:"orderConstraints"`
}

type componentPolicy struct {
	Kind          string       `json:"kind"`
	SourceVersion string       `json:"sourceVersion"`
	TargetProduct string       `json:"targetProduct"`
	TargetVersion string       `json:"targetVersion"`
	Route         []string     `json:"route"`
	Edges         []policyEdge `json:"edges"`
}

type policyEdge struct {
	From      string   `json:"from"`
	To        string   `json:"to"`
	Operation string   `json:"operation"`
	GateIDs   []string `json:"gateIds"`
}

type gate struct {
	ID        string `json:"id"`
	Condition string `json:"condition"`
}

type orderConstraint struct {
	GateID        string `json:"gateId"`
	BeforeKind    string `json:"beforeKind"`
	BeforeVersion string `json:"beforeVersion"`
	AfterKind     string `json:"afterKind"`
	AfterVersion  string `json:"afterVersion"`
}

type migrationPlan struct {
	SchemaVersion    string          `json:"schemaVersion"`
	EstateID         string          `json:"estateId"`
	TargetVCFVersion string          `json:"targetVcfVersion"`
	TargetSddcSpec   json.RawMessage `json:"targetSddcSpec"`
	Components       []planComponent `json:"components"`
	Gates            []gate          `json:"gates"`
	Steps            []planStep      `json:"steps"`
}

type planComponent struct {
	ID             string   `json:"id"`
	Kind           string   `json:"kind"`
	CurrentVersion string   `json:"currentVersion"`
	TargetProduct  string   `json:"targetProduct"`
	TargetVersion  string   `json:"targetVersion"`
	UpgradePath    []string `json:"upgradePath"`
	GateIDs        []string `json:"gateIds"`
}

type planStep struct {
	Order       int      `json:"order"`
	ComponentID string   `json:"componentId"`
	FromVersion string   `json:"fromVersion"`
	ToVersion   string   `json:"toVersion"`
	Operation   string   `json:"operation"`
	GateIDs     []string `json:"gateIds"`
}

type researchLog struct {
	Sources []researchSource `json:"sources"`
}

type researchSource struct {
	Title      string `json:"title"`
	URL        string `json:"url"`
	AccessedAt string `json:"accessedAt"`
	UsedFor    string `json:"usedFor"`
}

func TestArchitectureArtifact(t *testing.T) {
	// This is intentionally the first acceptance stage. It only extracts the
	// target object and validates it with SddcSpec from the pinned installer
	// document. No plan schema, fixture, snapshot, coverage, gate, or ordering
	// assertion runs until this succeeds.
	planBytes := mustRead(t, planPath)
	planObject := decodeObject(t, planBytes, "migration plan")
	target, ok := planObject["targetSddcSpec"]
	if !ok {
		t.Fatal("installer SddcSpec validation: targetSddcSpec is missing")
	}
	openAPI := decodeObject(t, mustRead(t, openAPIPath), "installer OpenAPI")
	sddcSchema, err := resolvePointer(openAPI, "#/components/schemas/SddcSpec")
	if err != nil {
		t.Fatalf("installer SddcSpec validation: %v", err)
	}
	if err := validateJSONSchema(openAPI, sddcSchema, target, "targetSddcSpec"); err != nil {
		t.Fatalf("installer SddcSpec validation: %v", err)
	}

	// The installer-facing target is valid. Migration checks may now begin.
	planSchemaObject := decodeObject(t, mustRead(t, planSchema), "migration-plan schema")
	if err := validateJSONSchema(planSchemaObject, planSchemaObject, planObject, "migration-plan.json"); err != nil {
		t.Fatalf("migration plan schema validation: %v", err)
	}

	var plan migrationPlan
	mustDecode(t, planBytes, &plan, "migration plan")
	var estate inventory
	mustDecode(t, mustRead(t, inventoryPath), &estate, "estate inventory")
	var snapshot compatibilitySnapshot
	mustDecode(t, mustRead(t, snapshotPath), &snapshot, "compatibility snapshot")

	checkInstallerInputs(t, target, estate)
	checkPlanAgainstAuthorities(t, plan, estate, snapshot)
	checkResearchArtifact(t)
}

func TestArchitectureHasUnitTests(t *testing.T) {
	entries, err := os.ReadDir("../architecture")
	if err != nil {
		t.Fatalf("read architecture package: %v", err)
	}
	foundTest := false
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), "_test.go") {
			continue
		}
		path := filepath.Join("../architecture", entry.Name())
		file, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", path, err)
		}
		for _, declaration := range file.Decls {
			function, ok := declaration.(*ast.FuncDecl)
			if ok && function.Recv == nil && strings.HasPrefix(function.Name.Name, "Test") {
				foundTest = true
			}
		}
	}
	if !foundTest {
		t.Fatal("architecture package must include Go unit tests")
	}
}

func TestArchitecturePackage(t *testing.T) {
	planBytes := mustRead(t, planPath)
	loadPlan := func(t *testing.T) architecture.Plan {
		t.Helper()
		plan, err := architecture.Load(strings.NewReader(string(planBytes)))
		if err != nil {
			t.Fatalf("architecture.Load(valid plan): %v", err)
		}
		return plan
	}

	plan := loadPlan(t)
	if err := architecture.Validate(plan); err != nil {
		t.Fatalf("architecture.Validate(valid plan): %v", err)
	}

	unknownField := strings.Replace(string(planBytes), `"schemaVersion"`, `"unexpected":true,"schemaVersion"`, 1)
	if _, err := architecture.Load(strings.NewReader(unknownField)); err == nil {
		t.Fatal("architecture.Load must reject unknown fields")
	}
	if _, err := architecture.Load(strings.NewReader(string(planBytes) + " {}")); err == nil {
		t.Fatal("architecture.Load must reject trailing JSON values")
	}

	tests := []struct {
		name   string
		mutate func(*architecture.Plan)
	}{
		{
			name: "duplicate component",
			mutate: func(plan *architecture.Plan) {
				plan.Components = append(plan.Components, plan.Components[0])
			},
		},
		{
			name: "invalid upgrade path",
			mutate: func(plan *architecture.Plan) {
				plan.Components[0].UpgradePath[0] = "not-the-current-version"
			},
		},
		{
			name: "non-contiguous step order",
			mutate: func(plan *architecture.Plan) {
				plan.Steps[0].Order = 2
			},
		},
		{
			name: "unknown gate",
			mutate: func(plan *architecture.Plan) {
				plan.Steps[0].GateIDs = []string{"undefined-gate"}
			},
		},
		{
			name: "missing route edge",
			mutate: func(plan *architecture.Plan) {
				plan.Steps = plan.Steps[1:]
				for index := range plan.Steps {
					plan.Steps[index].Order = index + 1
				}
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			plan := loadPlan(t)
			test.mutate(&plan)
			if err := architecture.Validate(plan); err == nil {
				t.Fatal("architecture.Validate accepted an invalid plan")
			}
		})
	}
}

func checkInstallerInputs(t *testing.T, target any, estate inventory) {
	t.Helper()
	spec, ok := target.(map[string]any)
	if !ok {
		t.Fatal("targetSddcSpec must be an object")
	}
	want := estate.InstallerInputs
	requireString(t, spec, "sddcId", want.SddcID)
	requireString(t, spec, "workflowType", want.WorkflowType)
	requireString(t, spec, "version", estate.TargetVCFVersion)

	vc := childObject(t, spec, "vcenterSpec")
	requireString(t, vc, "vcenterHostname", want.VcenterHostname)
	requireString(t, vc, "rootVcenterPassword", want.RootVcenterPassword)
	requireString(t, vc, "version", estate.TargetVCFVersion)
	if existing, ok := vc["useExistingDeployment"].(bool); !ok || !existing {
		t.Fatal("targetSddcSpec.vcenterSpec.useExistingDeployment must be true")
	}

	dns := childObject(t, spec, "dnsSpec")
	requireString(t, dns, "subdomain", want.DNSSubdomain)
	gotNameservers := stringArray(t, dns["nameservers"], "targetSddcSpec.dnsSpec.nameservers")
	if !reflect.DeepEqual(gotNameservers, want.Nameservers) {
		t.Fatalf("targetSddcSpec nameservers = %v, want %v", gotNameservers, want.Nameservers)
	}

	networks, ok := spec["networkSpecs"].([]any)
	if !ok {
		t.Fatal("targetSddcSpec.networkSpecs must be an array")
	}
	foundManagement := false
	for _, item := range networks {
		network, ok := item.(map[string]any)
		if !ok || network["networkType"] != "MANAGEMENT" {
			continue
		}
		vlan, ok := network["vlanId"].(float64)
		if ok && int(vlan) == want.ManagementVLANID {
			foundManagement = true
		}
	}
	if !foundManagement {
		t.Fatalf("targetSddcSpec needs MANAGEMENT network VLAN %d", want.ManagementVLANID)
	}
}

func checkPlanAgainstAuthorities(t *testing.T, plan migrationPlan, estate inventory, snapshot compatibilitySnapshot) {
	t.Helper()
	if plan.SchemaVersion != "1.0" {
		t.Fatalf("schemaVersion = %q, want 1.0", plan.SchemaVersion)
	}
	if plan.EstateID != estate.EstateID {
		t.Fatalf("estateId = %q, want %q", plan.EstateID, estate.EstateID)
	}
	if plan.TargetVCFVersion != estate.TargetVCFVersion || plan.TargetVCFVersion != snapshot.TargetVCFVersion {
		t.Fatalf("targetVcfVersion %q does not match inventory and snapshot", plan.TargetVCFVersion)
	}

	policies := make(map[string]componentPolicy)
	for _, policy := range snapshot.ComponentPolicies {
		key := policy.Kind + "\x00" + policy.SourceVersion
		if _, duplicate := policies[key]; duplicate {
			t.Fatalf("snapshot has duplicate component policy %q", key)
		}
		policies[key] = policy
	}

	inventoryByID := make(map[string]inventoryComponent)
	for _, component := range estate.Components {
		if _, duplicate := inventoryByID[component.ID]; duplicate {
			t.Fatalf("inventory has duplicate component %q", component.ID)
		}
		inventoryByID[component.ID] = component
	}
	if len(plan.Components) != len(inventoryByID) {
		t.Fatalf("plan names %d components, inventory has %d", len(plan.Components), len(inventoryByID))
	}

	planByID := make(map[string]planComponent)
	expectedEdges := make(map[string]policyEdge)
	for _, component := range plan.Components {
		if _, duplicate := planByID[component.ID]; duplicate {
			t.Fatalf("plan has duplicate component %q", component.ID)
		}
		inv, ok := inventoryByID[component.ID]
		if !ok {
			t.Fatalf("plan invents component %q", component.ID)
		}
		if component.Kind != inv.Kind || component.CurrentVersion != inv.Version {
			t.Fatalf("component %q current identity/version does not match inventory", component.ID)
		}
		policy, ok := policies[inv.Kind+"\x00"+inv.Version]
		if !ok {
			t.Fatalf("no pinned policy for %s %s", inv.Kind, inv.Version)
		}
		if component.TargetProduct != policy.TargetProduct || component.TargetVersion != policy.TargetVersion {
			t.Fatalf("component %q target does not match pinned policy", component.ID)
		}
		if !reflect.DeepEqual(component.UpgradePath, policy.Route) {
			t.Fatalf("component %q upgradePath = %v, want %v", component.ID, component.UpgradePath, policy.Route)
		}
		wantGateIDs := unionEdgeGates(policy.Edges)
		if !sameStrings(component.GateIDs, wantGateIDs) {
			t.Fatalf("component %q gateIds = %v, want %v", component.ID, component.GateIDs, wantGateIDs)
		}
		if len(policy.Edges) != len(policy.Route)-1 {
			t.Fatalf("pinned policy for %q has inconsistent route and edges", component.ID)
		}
		for index, edge := range policy.Edges {
			if edge.From != policy.Route[index] || edge.To != policy.Route[index+1] {
				t.Fatalf("pinned policy for %q has a non-route edge", component.ID)
			}
			expectedEdges[edgeKey(component.ID, edge.From, edge.To)] = edge
		}
		planByID[component.ID] = component
	}
	for id := range inventoryByID {
		if _, ok := planByID[id]; !ok {
			t.Fatalf("plan omits inventory component %q", id)
		}
	}

	checkGates(t, plan.Gates, snapshot.Gates)
	seenEdges := make(map[string]bool)
	for index, step := range plan.Steps {
		if step.Order != index+1 {
			t.Fatalf("step index %d has order %d, want %d", index, step.Order, index+1)
		}
		if _, ok := planByID[step.ComponentID]; !ok {
			t.Fatalf("step %d names unknown component %q", step.Order, step.ComponentID)
		}
		key := edgeKey(step.ComponentID, step.FromVersion, step.ToVersion)
		edge, ok := expectedEdges[key]
		if !ok {
			t.Fatalf("step %d is not a pinned route edge for component %q", step.Order, step.ComponentID)
		}
		if seenEdges[key] {
			t.Fatalf("route edge %q is repeated", key)
		}
		if step.Operation != edge.Operation || !sameStrings(step.GateIDs, edge.GateIDs) {
			t.Fatalf("step %d operation/gates do not match the pinned edge", step.Order)
		}
		seenEdges[key] = true
	}
	if len(seenEdges) != len(expectedEdges) {
		missing := make([]string, 0)
		for key := range expectedEdges {
			if !seenEdges[key] {
				missing = append(missing, key)
			}
		}
		sort.Strings(missing)
		t.Fatalf("plan omits route edges: %v", missing)
	}

	checkOrdering(t, plan.Steps, planByID, plan.Gates, snapshot.OrderConstraints)
}

func checkGates(t *testing.T, got, want []gate) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("plan defines %d gates, pinned snapshot defines %d", len(got), len(want))
	}
	wantByID := make(map[string]string)
	for _, item := range want {
		wantByID[item.ID] = item.Condition
	}
	seen := make(map[string]bool)
	for _, item := range got {
		condition, ok := wantByID[item.ID]
		if !ok || condition != item.Condition {
			t.Fatalf("gate %q is absent from or differs from the pinned snapshot", item.ID)
		}
		if seen[item.ID] {
			t.Fatalf("gate %q is duplicated", item.ID)
		}
		seen[item.ID] = true
	}
}

func checkOrdering(t *testing.T, steps []planStep, components map[string]planComponent, gates []gate, constraints []orderConstraint) {
	t.Helper()
	knownGates := make(map[string]bool)
	for _, item := range gates {
		knownGates[item.ID] = true
	}
	for _, constraint := range constraints {
		if !knownGates[constraint.GateID] {
			t.Fatalf("ordering constraint references undefined gate %q", constraint.GateID)
		}
		before := targetOrders(steps, components, constraint.BeforeKind, constraint.BeforeVersion)
		after := targetOrders(steps, components, constraint.AfterKind, constraint.AfterVersion)
		if len(before) == 0 || len(after) == 0 {
			t.Fatalf("ordering gate %q has no matching before/after steps", constraint.GateID)
		}
		if maxInt(before) >= minInt(after) {
			t.Fatalf("ordering gate %q violated: %s %s must finish before %s %s", constraint.GateID, constraint.BeforeKind, constraint.BeforeVersion, constraint.AfterKind, constraint.AfterVersion)
		}
	}
}

func targetOrders(steps []planStep, components map[string]planComponent, kind, version string) []int {
	var orders []int
	for _, step := range steps {
		component, ok := components[step.ComponentID]
		if ok && component.Kind == kind && step.ToVersion == version {
			orders = append(orders, step.Order)
		}
	}
	return orders
}

func checkResearchArtifact(t *testing.T) {
	t.Helper()
	var research researchLog
	mustDecode(t, mustRead(t, researchPath), &research, "research record")
	if len(research.Sources) == 0 {
		t.Fatal("research/consulted.json must contain at least one source")
	}

	seenURLs := make(map[string]bool)
	foundMatrix := false
	foundReleaseNotes := false
	foundKnowledgeArticle := false
	for index, source := range research.Sources {
		if strings.TrimSpace(source.Title) == "" || strings.TrimSpace(source.URL) == "" ||
			strings.TrimSpace(source.AccessedAt) == "" || strings.TrimSpace(source.UsedFor) == "" {
			t.Fatalf("research source %d has an empty required field", index)
		}
		if _, err := time.Parse("2006-01-02", source.AccessedAt); err != nil {
			t.Fatalf("research source %d accessedAt must use YYYY-MM-DD: %v", index, err)
		}
		parsed, err := url.Parse(source.URL)
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil {
			t.Fatalf("research source %d must use a valid public HTTPS URL", index)
		}
		host := strings.ToLower(parsed.Hostname())
		if host == "localhost" || strings.HasSuffix(host, ".localhost") || strings.HasSuffix(host, ".invalid") ||
			strings.HasSuffix(host, ".test") || strings.HasSuffix(host, ".example") {
			t.Fatalf("research source %d does not use a public documentation hostname", index)
		}
		canonicalURL := parsed.String()
		if seenURLs[canonicalURL] {
			t.Fatalf("research source %d duplicates URL %q", index, canonicalURL)
		}
		seenURLs[canonicalURL] = true

		switch host {
		case "interopmatrix.broadcom.com":
			foundMatrix = true
		case "techdocs.broadcom.com":
			if strings.Contains(strings.ToLower(parsed.Path), "/release-notes/") {
				foundReleaseNotes = true
			}
		case "knowledge.broadcom.com":
			if strings.HasPrefix(strings.ToLower(parsed.Path), "/external/article") {
				foundKnowledgeArticle = true
			}
		}
	}
	if !foundMatrix {
		t.Fatal("research record must include Broadcom's Product Interoperability Matrix")
	}
	if !foundReleaseNotes {
		t.Fatal("research record must include a relevant Broadcom Technical Documentation release-notes page")
	}
	if !foundKnowledgeArticle {
		t.Fatal("research record must include a relevant Broadcom knowledge article")
	}
}

func validateJSONSchema(root map[string]any, rawSchema, value any, path string) error {
	schema, ok := rawSchema.(map[string]any)
	if !ok {
		return fmt.Errorf("%s: schema is not an object", path)
	}
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolvePointer(root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		return validateJSONSchema(root, resolved, value, path)
	}
	if value == nil {
		if nullable, _ := schema["nullable"].(bool); nullable {
			return nil
		}
	}
	for _, keyword := range []string{"allOf", "anyOf", "oneOf"} {
		alternatives, ok := schema[keyword].([]any)
		if !ok {
			continue
		}
		matches := 0
		for _, alternative := range alternatives {
			if validateJSONSchema(root, alternative, value, path) == nil {
				matches++
			}
		}
		switch keyword {
		case "allOf":
			if matches != len(alternatives) {
				return fmt.Errorf("%s: does not satisfy allOf", path)
			}
		case "anyOf":
			if matches == 0 {
				return fmt.Errorf("%s: does not satisfy anyOf", path)
			}
		case "oneOf":
			if matches != 1 {
				return fmt.Errorf("%s: satisfies %d oneOf branches", path, matches)
			}
		}
	}
	if enumValues, ok := schema["enum"].([]any); ok {
		matched := false
		for _, allowed := range enumValues {
			if reflect.DeepEqual(allowed, value) {
				matched = true
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
			return fmt.Errorf("%s: expected object", path)
		}
		if required, ok := schema["required"].([]any); ok {
			for _, item := range required {
				name, _ := item.(string)
				if _, exists := object[name]; !exists {
					return fmt.Errorf("%s: missing required property %q", path, name)
				}
			}
		}
		properties, _ := schema["properties"].(map[string]any)
		for name, child := range object {
			if childSchema, exists := properties[name]; exists {
				if err := validateJSONSchema(root, childSchema, child, path+"."+name); err != nil {
					return err
				}
				continue
			}
			switch additional := schema["additionalProperties"].(type) {
			case bool:
				if !additional {
					return fmt.Errorf("%s: additional property %q is not allowed", path, name)
				}
			case map[string]any:
				if err := validateJSONSchema(root, additional, child, path+"."+name); err != nil {
					return err
				}
			}
		}
	case "array":
		array, ok := value.([]any)
		if !ok {
			return fmt.Errorf("%s: expected array", path)
		}
		if minimum, ok := number(schema["minItems"]); ok && float64(len(array)) < minimum {
			return fmt.Errorf("%s: has fewer than %g items", path, minimum)
		}
		if maximum, ok := number(schema["maxItems"]); ok && float64(len(array)) > maximum {
			return fmt.Errorf("%s: has more than %g items", path, maximum)
		}
		if unique, _ := schema["uniqueItems"].(bool); unique {
			for left := range array {
				for right := left + 1; right < len(array); right++ {
					if reflect.DeepEqual(array[left], array[right]) {
						return fmt.Errorf("%s: contains duplicate items", path)
					}
				}
			}
		}
		if itemSchema, exists := schema["items"]; exists {
			for index, item := range array {
				if err := validateJSONSchema(root, itemSchema, item, fmt.Sprintf("%s[%d]", path, index)); err != nil {
					return err
				}
			}
		}
	case "string":
		text, ok := value.(string)
		if !ok {
			return fmt.Errorf("%s: expected string", path)
		}
		length := float64(utf8.RuneCountInString(text))
		if minimum, ok := number(schema["minLength"]); ok && length < minimum {
			return fmt.Errorf("%s: shorter than minLength %g", path, minimum)
		}
		if maximum, ok := number(schema["maxLength"]); ok && length > maximum {
			return fmt.Errorf("%s: longer than maxLength %g", path, maximum)
		}
		if pattern, ok := schema["pattern"].(string); ok {
			expression, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: invalid schema pattern: %w", path, err)
			}
			if !expression.MatchString(text) {
				return fmt.Errorf("%s: does not match pattern %q", path, pattern)
			}
		}
	case "integer":
		numeric, ok := number(value)
		if !ok || math.Trunc(numeric) != numeric {
			return fmt.Errorf("%s: expected integer", path)
		}
		if err := validateNumberBounds(schema, numeric, path); err != nil {
			return err
		}
	case "number":
		numeric, ok := number(value)
		if !ok {
			return fmt.Errorf("%s: expected number", path)
		}
		if err := validateNumberBounds(schema, numeric, path); err != nil {
			return err
		}
	case "boolean":
		if _, ok := value.(bool); !ok {
			return fmt.Errorf("%s: expected boolean", path)
		}
	}
	return nil
}

func validateNumberBounds(schema map[string]any, value float64, path string) error {
	if minimum, ok := number(schema["minimum"]); ok && value < minimum {
		return fmt.Errorf("%s: %g is below minimum %g", path, value, minimum)
	}
	if maximum, ok := number(schema["maximum"]); ok && value > maximum {
		return fmt.Errorf("%s: %g is above maximum %g", path, value, maximum)
	}
	return nil
}

func resolvePointer(root map[string]any, ref string) (any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("unsupported non-local schema reference %q", ref)
	}
	var current any = root
	for _, encoded := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		name := strings.ReplaceAll(strings.ReplaceAll(encoded, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("schema reference %q traverses a non-object", ref)
		}
		next, ok := object[name]
		if !ok {
			return nil, fmt.Errorf("schema reference %q is missing %q", ref, name)
		}
		current = next
	}
	return current, nil
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	contents, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return contents
}

func decodeObject(t *testing.T, contents []byte, label string) map[string]any {
	t.Helper()
	var object map[string]any
	mustDecode(t, contents, &object, label)
	return object
}

func mustDecode(t *testing.T, contents []byte, destination any, label string) {
	t.Helper()
	if err := json.Unmarshal(contents, destination); err != nil {
		t.Fatalf("decode %s: %v", label, err)
	}
}

func number(value any) (float64, bool) {
	switch typed := value.(type) {
	case float64:
		return typed, true
	case int:
		return float64(typed), true
	default:
		return 0, false
	}
}

func childObject(t *testing.T, parent map[string]any, name string) map[string]any {
	t.Helper()
	child, ok := parent[name].(map[string]any)
	if !ok {
		t.Fatalf("targetSddcSpec.%s must be an object", name)
	}
	return child
}

func requireString(t *testing.T, object map[string]any, name, want string) {
	t.Helper()
	if got, ok := object[name].(string); !ok || got != want {
		t.Fatalf("%s = %v, want %q", name, object[name], want)
	}
}

func stringArray(t *testing.T, value any, label string) []string {
	t.Helper()
	items, ok := value.([]any)
	if !ok {
		t.Fatalf("%s must be an array", label)
	}
	result := make([]string, len(items))
	for index, item := range items {
		text, ok := item.(string)
		if !ok {
			t.Fatalf("%s[%d] must be a string", label, index)
		}
		result[index] = text
	}
	return result
}

func unionEdgeGates(edges []policyEdge) []string {
	var result []string
	seen := make(map[string]bool)
	for _, edge := range edges {
		for _, id := range edge.GateIDs {
			if !seen[id] {
				seen[id] = true
				result = append(result, id)
			}
		}
	}
	return result
}

func sameStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	counts := make(map[string]int)
	for _, item := range left {
		counts[item]++
	}
	for _, item := range right {
		counts[item]--
	}
	for _, count := range counts {
		if count != 0 {
			return false
		}
	}
	return true
}

func edgeKey(componentID, from, to string) string {
	return componentID + "\x00" + from + "\x00" + to
}

func minInt(values []int) int {
	minimum := values[0]
	for _, value := range values[1:] {
		if value < minimum {
			minimum = value
		}
	}
	return minimum
}

func maxInt(values []int) int {
	maximum := values[0]
	for _, value := range values[1:] {
		if value > maximum {
			maximum = value
		}
	}
	return maximum
}
