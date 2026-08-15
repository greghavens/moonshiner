package architecture_test

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"vcfarch-0087/architecture"
)

const (
	installerSpecPath = "../specifications/vcf-installer/vcf-installer-openapi.json"
	planSchemaPath    = "../fixtures/migration-plan.schema.json"
	inventoryPath     = "../fixtures/estate.json"
	snapshotPath      = "../fixtures/compatibility-snapshot.json"
	artifactPath      = "../migration-plan.json"
)

// TestMigrationPlan is deliberately one ordered verifier. It validates the
// embedded installer input against the upstream SddcSpec before consulting the
// task's plan schema, inventory, or compatibility snapshot.
func TestMigrationPlan(t *testing.T) {
	artifactRaw, err := os.ReadFile(artifactPath)
	if err != nil {
		t.Fatalf("read migration artifact: %v", err)
	}
	artifactDocument, err := decodeDocument(artifactRaw)
	if err != nil {
		t.Fatalf("decode migration artifact: %v", err)
	}
	artifactObject, ok := artifactDocument.(map[string]any)
	if !ok {
		t.Fatal("migration artifact must be a JSON object")
	}
	targetSpec, ok := artifactObject["targetSddcSpec"]
	if !ok {
		t.Fatal("migration artifact has no targetSddcSpec for installer-schema validation")
	}

	// This is the first contract validation. Keep it before plan-schema and
	// compatibility checks so an invalid installer spec cannot be masked by a
	// later migration-plan error.
	installerRoot := mustReadDocument(t, installerSpecPath)
	installerObject, ok := installerRoot.(map[string]any)
	if !ok {
		t.Fatal("protected installer OpenAPI root is not an object")
	}
	sddcSchema, err := objectAt(installerObject, "components", "schemas", "SddcSpec")
	if err != nil {
		t.Fatalf("resolve upstream SddcSpec: %v", err)
	}
	if err := validateSchema(installerObject, sddcSchema, targetSpec, "targetSddcSpec"); err != nil {
		t.Fatalf("targetSddcSpec does not validate against upstream SddcSpec: %v", err)
	}

	planSchemaRoot := mustReadDocument(t, planSchemaPath)
	planSchema, ok := planSchemaRoot.(map[string]any)
	if !ok {
		t.Fatal("protected migration-plan schema root is not an object")
	}
	if err := validateSchema(planSchema, planSchema, artifactDocument, "migration-plan"); err != nil {
		t.Fatalf("migration artifact does not match migration-plan schema: %v", err)
	}

	var plan architecture.Plan
	if err := json.Unmarshal(artifactRaw, &plan); err != nil {
		t.Fatalf("decode typed migration plan: %v", err)
	}
	var inventory architecture.Inventory
	if err := architecture.ReadJSON(inventoryPath, &inventory); err != nil {
		t.Fatal(err)
	}
	var snapshot architecture.CompatibilitySnapshot
	if err := architecture.ReadJSON(snapshotPath, &snapshot); err != nil {
		t.Fatal(err)
	}
	if err := oracleValidate(plan, inventory, snapshot); err != nil {
		t.Fatalf("migration architecture violates pinned authority: %v", err)
	}
	if err := architecture.Validate(plan, inventory, snapshot); err != nil {
		t.Fatalf("architecture.Validate rejected the delivered plan: %v", err)
	}

	built, err := architecture.Build(inventory, snapshot)
	if err != nil {
		t.Fatalf("architecture.Build: %v", err)
	}
	if err := oracleValidate(built, inventory, snapshot); err != nil {
		t.Fatalf("architecture.Build produced an invalid plan: %v", err)
	}
	builtAgain, err := architecture.Build(inventory, snapshot)
	if err != nil {
		t.Fatalf("second architecture.Build: %v", err)
	}
	if !reflect.DeepEqual(built, builtAgain) {
		t.Fatal("architecture.Build is not deterministic")
	}
	canonical, err := json.MarshalIndent(built, "", "  ")
	if err != nil {
		t.Fatalf("marshal built plan: %v", err)
	}
	canonical = append(canonical, '\n')
	if !bytes.Equal(canonical, artifactRaw) {
		t.Fatal("migration-plan.json is not the canonical output of architecture.Build")
	}

	cases := []struct {
		name   string
		mutate func(*architecture.Plan)
	}{
		{
			name: "blocked NSX edge represented as upgrade",
			mutate: func(candidate *architecture.Plan) {
				step := stepByID(candidate, "unprepare-nsx")
				step.Action = "upgrade"
				step.ToVersion = "9.0.2.0-25150386"
			},
		},
		{
			name: "component target drifts from pinned snapshot",
			mutate: func(candidate *architecture.Plan) {
				candidate.Components[0].TargetVersion = "9.0.2.0-made-up"
			},
		},
		{
			name: "installer existing vCenter version drifts from inventory",
			mutate: func(candidate *architecture.Plan) {
				var spec map[string]any
				if err := json.Unmarshal(candidate.TargetSddcSpec, &spec); err != nil {
					panic(err)
				}
				spec["vcenterSpec"].(map[string]any)["version"] = "8.0.3-made-up"
				candidate.TargetSddcSpec, _ = json.Marshal(spec)
			},
		},
		{
			name: "inventoried component omitted",
			mutate: func(candidate *architecture.Plan) {
				candidate.Components = candidate.Components[:len(candidate.Components)-1]
			},
		},
		{
			name: "technical predecessor gate omitted",
			mutate: func(candidate *architecture.Plan) {
				stepByID(candidate, "upgrade-vcenter").Requires = nil
			},
		},
		{
			name: "core component order changed",
			mutate: func(candidate *architecture.Plan) {
				nsx := stepIndex(candidate, "upgrade-nsx")
				vc := stepIndex(candidate, "upgrade-vcenter")
				candidate.Steps[nsx], candidate.Steps[vc] = candidate.Steps[vc], candidate.Steps[nsx]
				candidate.Steps[nsx].Order = nsx + 1
				candidate.Steps[vc].Order = vc + 1
			},
		},
		{
			name: "intermediate NSX deployment skipped",
			mutate: func(candidate *architecture.Plan) {
				step := stepByID(candidate, "deploy-compatible-nsx")
				step.FromVersion = "4.2.3.3.0-25171318"
			},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			candidate := clonePlan(t, plan)
			tc.mutate(&candidate)
			if err := architecture.Validate(candidate, inventory, snapshot); err == nil {
				t.Fatal("architecture.Validate accepted an invalid migration plan")
			}
		})
	}
}

func TestResearchRecord(t *testing.T) {
	raw, err := os.ReadFile("../research.md")
	if err != nil {
		t.Fatalf("read research record: %v", err)
	}

	seenURLs := map[string]bool{}
	var decisions strings.Builder
	rows := 0
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if !strings.HasPrefix(line, "|") || !strings.HasSuffix(line, "|") {
			continue
		}
		cells := strings.Split(line, "|")
		if len(cells) != 6 {
			continue
		}
		for index := 1; index <= 4; index++ {
			cells[index] = strings.TrimSpace(cells[index])
		}
		if strings.EqualFold(cells[1], "Source title") || strings.Trim(cells[1], "-: ") == "" {
			continue
		}

		rows++
		if utf8.RuneCountInString(cells[1]) < 5 {
			t.Errorf("research row %d has no useful source title", rows)
		}
		sourceURL, err := url.ParseRequestURI(cells[2])
		if err != nil || sourceURL.Scheme != "https" || sourceURL.Host == "" {
			t.Errorf("research row %d has invalid HTTPS URL %q", rows, cells[2])
		} else {
			host := strings.ToLower(sourceURL.Hostname())
			if host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com") {
				t.Errorf("research row %d is not a published Broadcom source: %q", rows, cells[2])
			}
			if seenURLs[sourceURL.String()] {
				t.Errorf("research source URL is duplicated: %q", cells[2])
			}
			seenURLs[sourceURL.String()] = true
		}
		if _, err := time.Parse("2006-01-02", cells[3]); err != nil {
			t.Errorf("research row %d has invalid YYYY-MM-DD access date %q", rows, cells[3])
		}
		if utf8.RuneCountInString(cells[4]) < 20 {
			t.Errorf("research row %d has no useful decision record", rows)
		}
		decisions.WriteString(" ")
		decisions.WriteString(strings.ToLower(cells[4]))
	}
	if rows == 0 {
		t.Fatal("research.md has no four-column source records")
	}

	decisionText := decisions.String()
	if !strings.Contains(decisionText, "compatib") && !strings.Contains(decisionText, "interoperab") {
		t.Error("research decisions do not cover supported component combinations")
	}
	if !strings.Contains(decisionText, "upgrade") && !strings.Contains(decisionText, "back-in-time") {
		t.Error("research decisions do not cover upgrade paths")
	}
	if !strings.Contains(decisionText, "order") && !strings.Contains(decisionText, "sequence") {
		t.Error("research decisions do not cover component order")
	}
}

func clonePlan(t *testing.T, source architecture.Plan) architecture.Plan {
	t.Helper()
	raw, err := json.Marshal(source)
	if err != nil {
		t.Fatal(err)
	}
	var clone architecture.Plan
	if err := json.Unmarshal(raw, &clone); err != nil {
		t.Fatal(err)
	}
	return clone
}

func stepIndex(plan *architecture.Plan, id string) int {
	for index := range plan.Steps {
		if plan.Steps[index].ID == id {
			return index
		}
	}
	panic("protected test expected step " + id)
}

func stepByID(plan *architecture.Plan, id string) *architecture.Step {
	return &plan.Steps[stepIndex(plan, id)]
}

func oracleValidate(plan architecture.Plan, inventory architecture.Inventory, snapshot architecture.CompatibilitySnapshot) error {
	if plan.SchemaVersion != "1.0" || plan.EstateID != inventory.EstateID {
		return fmt.Errorf("plan identity does not match inventory")
	}
	if plan.SourceRelease != inventory.SourceRelease || plan.TargetRelease != inventory.TargetRelease {
		return fmt.Errorf("plan releases do not match inventory")
	}
	if snapshot.SourceRelease != inventory.SourceRelease || snapshot.TargetRelease != inventory.TargetRelease {
		return fmt.Errorf("protected fixture releases disagree")
	}
	if plan.SnapshotID != snapshot.SnapshotID {
		return fmt.Errorf("plan snapshotId %q does not select pinned snapshot %q", plan.SnapshotID, snapshot.SnapshotID)
	}
	if err := validateInstallerAlignment(plan.TargetSddcSpec, inventory); err != nil {
		return err
	}

	inventoryByID := make(map[string]architecture.InventoryItem, len(inventory.Components))
	for _, item := range inventory.Components {
		if _, exists := inventoryByID[item.ID]; exists {
			return fmt.Errorf("duplicate inventory component %q", item.ID)
		}
		inventoryByID[item.ID] = item
	}
	contractByID := make(map[string]architecture.ComponentContract, len(snapshot.Components))
	for _, component := range snapshot.Components {
		contractByID[component.ID] = component
	}
	if len(plan.Components) != len(inventoryByID) || len(contractByID) != len(inventoryByID) {
		return fmt.Errorf("component coverage is incomplete")
	}
	seenComponents := map[string]bool{}
	for _, component := range plan.Components {
		if seenComponents[component.ID] {
			return fmt.Errorf("duplicate plan component %q", component.ID)
		}
		seenComponents[component.ID] = true
		item, ok := inventoryByID[component.ID]
		if !ok {
			return fmt.Errorf("plan contains unknown component %q", component.ID)
		}
		contract, ok := contractByID[component.ID]
		if !ok {
			return fmt.Errorf("snapshot has no component %q", component.ID)
		}
		if component.Name != item.Name || component.CurrentVersion != item.Version {
			return fmt.Errorf("component %q current inventory drift", component.ID)
		}
		if component.CurrentVersion != contract.CurrentVersion || component.TargetVersion != contract.TargetVersion {
			return fmt.Errorf("component %q versions violate snapshot", component.ID)
		}
		if !reflect.DeepEqual(component.GatedBy, []string{contract.FinalGate}) {
			return fmt.Errorf("component %q must be gated by %q", component.ID, contract.FinalGate)
		}
	}

	gateByID := make(map[string]architecture.GateContract, len(snapshot.Gates))
	for _, gate := range snapshot.Gates {
		gateByID[gate.ID] = gate
	}
	if len(plan.Steps) != len(gateByID) {
		return fmt.Errorf("plan has %d steps; snapshot defines %d", len(plan.Steps), len(gateByID))
	}
	state := make(map[string]string, len(inventory.Components))
	for _, item := range inventory.Components {
		state[item.ID] = item.Version
	}
	produced := map[string]bool{}
	seenSteps := map[string]bool{}
	for index, step := range plan.Steps {
		if step.Order != index+1 {
			return fmt.Errorf("step %q has non-contiguous order %d", step.ID, step.Order)
		}
		if seenSteps[step.ID] {
			return fmt.Errorf("duplicate step %q", step.ID)
		}
		seenSteps[step.ID] = true
		gate, ok := gateByID[step.ID]
		if !ok {
			return fmt.Errorf("step %q is not defined by the snapshot", step.ID)
		}
		if step.Action != gate.Action || step.ComponentID != gate.ComponentID || step.FromVersion != gate.FromVersion || step.ToVersion != gate.ToVersion {
			return fmt.Errorf("step %q does not match its pinned action or transition", step.ID)
		}
		if !reflect.DeepEqual(step.Requires, gate.Requires) || step.Produces != gate.Produces {
			return fmt.Errorf("step %q does not match its pinned gates", step.ID)
		}
		if utf8.RuneCountInString(step.Rationale) < gate.MinimumRationale {
			return fmt.Errorf("step %q rationale is too short", step.ID)
		}
		for _, requirement := range step.Requires {
			if !produced[requirement] {
				return fmt.Errorf("step %q runs before gate %q", step.ID, requirement)
			}
		}
		if produced[step.Produces] {
			return fmt.Errorf("gate %q produced more than once", step.Produces)
		}
		if step.ComponentID != "" {
			if state[step.ComponentID] != step.FromVersion {
				return fmt.Errorf("step %q expects %q at %q, found %q", step.ID, step.ComponentID, step.FromVersion, state[step.ComponentID])
			}
			contract := contractByID[step.ComponentID]
			candidate := architecture.Transition{From: step.FromVersion, To: step.ToVersion, Operation: step.Action}
			for _, blocked := range contract.BlockedTransitions {
				if candidate == blocked {
					return fmt.Errorf("step %q selects blocked transition", step.ID)
				}
			}
			if !containsTransition(contract.AllowedTransitions, candidate) {
				return fmt.Errorf("step %q transition is not supported", step.ID)
			}
			state[step.ComponentID] = step.ToVersion
		}
		produced[step.Produces] = true
	}
	for _, component := range snapshot.Components {
		if state[component.ID] != component.TargetVersion {
			return fmt.Errorf("component %q finishes at %q, want %q", component.ID, state[component.ID], component.TargetVersion)
		}
		if !produced[component.FinalGate] {
			return fmt.Errorf("component %q final gate was not produced", component.ID)
		}
	}
	return nil
}

func containsTransition(haystack []architecture.Transition, needle architecture.Transition) bool {
	for _, candidate := range haystack {
		if candidate == needle {
			return true
		}
	}
	return false
}

func validateInstallerAlignment(raw json.RawMessage, inventory architecture.Inventory) error {
	var spec struct {
		SddcID       string `json:"sddcId"`
		WorkflowType string `json:"workflowType"`
		Version      string `json:"version"`
		VcenterSpec  struct {
			VcenterHostname     string `json:"vcenterHostname"`
			RootVcenterPassword string `json:"rootVcenterPassword"`
			Version             string `json:"version"`
			UseExisting         bool   `json:"useExistingDeployment"`
		} `json:"vcenterSpec"`
		DNS struct {
			Subdomain   string   `json:"subdomain"`
			Nameservers []string `json:"nameservers"`
		} `json:"dnsSpec"`
		Networks []struct {
			Type string `json:"networkType"`
			VLAN int    `json:"vlanId"`
		} `json:"networkSpecs"`
	}
	if err := json.Unmarshal(raw, &spec); err != nil {
		return fmt.Errorf("decode targetSddcSpec alignment fields: %w", err)
	}
	inputs := inventory.InstallerInputs
	if spec.SddcID != inputs.SddcID || spec.WorkflowType != "VCF" || spec.Version != inventory.TargetRelease {
		return fmt.Errorf("targetSddcSpec identity or release does not match inventory")
	}
	if spec.VcenterSpec.VcenterHostname != inputs.VcenterHostname || spec.VcenterSpec.RootVcenterPassword != inputs.RootVcenterPassword || spec.VcenterSpec.Version != inventoryVersion(inventory.Components, "vcenter") || !spec.VcenterSpec.UseExisting {
		return fmt.Errorf("targetSddcSpec must re-import the inventoried existing vCenter")
	}
	if spec.DNS.Subdomain != inputs.Subdomain || !reflect.DeepEqual(spec.DNS.Nameservers, inputs.Nameservers) {
		return fmt.Errorf("targetSddcSpec DNS does not match inventory")
	}
	if len(spec.Networks) != 1 || spec.Networks[0].Type != "MANAGEMENT" || spec.Networks[0].VLAN != inputs.ManagementVLAN {
		return fmt.Errorf("targetSddcSpec management network does not match inventory")
	}
	return nil
}

func inventoryVersion(components []architecture.InventoryItem, id string) string {
	for _, component := range components {
		if component.ID == id {
			return component.Version
		}
	}
	return ""
}

func mustReadDocument(t *testing.T, path string) any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read protected document %s: %v", path, err)
	}
	document, err := decodeDocument(raw)
	if err != nil {
		t.Fatalf("decode protected document %s: %v", path, err)
	}
	return document
}

func decodeDocument(raw []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var document any
	if err := decoder.Decode(&document); err != nil {
		return nil, err
	}
	return document, nil
}

func objectAt(root map[string]any, path ...string) (map[string]any, error) {
	var current any = root
	for _, part := range path {
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%s is not an object", strings.Join(path, "/"))
		}
		current, ok = object[part]
		if !ok {
			return nil, fmt.Errorf("missing %s", strings.Join(path, "/"))
		}
	}
	object, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%s is not an object", strings.Join(path, "/"))
	}
	return object, nil
}

func validateSchema(root map[string]any, schema map[string]any, value any, path string) error {
	if reference, ok := schema["$ref"].(string); ok {
		resolved, err := resolveReference(root, reference)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		return validateSchema(root, resolved, value, path)
	}
	if constant, ok := schema["const"]; ok && !reflect.DeepEqual(constant, value) {
		return fmt.Errorf("%s: value does not equal const", path)
	}
	if choices, ok := schema["enum"].([]any); ok {
		matched := false
		for _, choice := range choices {
			matched = matched || reflect.DeepEqual(choice, value)
		}
		if !matched {
			return fmt.Errorf("%s: value is not in enum", path)
		}
	}
	if expected, ok := schema["type"].(string); ok {
		if err := validateType(expected, value, path); err != nil {
			return err
		}
	}
	for _, keyword := range []string{"allOf"} {
		if branches, ok := schema[keyword].([]any); ok {
			for index, branch := range branches {
				branchSchema, ok := branch.(map[string]any)
				if !ok {
					return fmt.Errorf("%s: %s[%d] is not an object", path, keyword, index)
				}
				if err := validateSchema(root, branchSchema, value, path); err != nil {
					return err
				}
			}
		}
	}
	switch typed := value.(type) {
	case map[string]any:
		if required, ok := schema["required"].([]any); ok {
			for _, entry := range required {
				name, ok := entry.(string)
				if !ok {
					continue
				}
				if _, exists := typed[name]; !exists {
					return fmt.Errorf("%s: missing required property %q", path, name)
				}
			}
		}
		properties, _ := schema["properties"].(map[string]any)
		for name, child := range typed {
			childSchemaValue, declared := properties[name]
			if !declared {
				if additional, exists := schema["additionalProperties"]; exists && additional == false {
					return fmt.Errorf("%s: additional property %q", path, name)
				}
				continue
			}
			childSchema, ok := childSchemaValue.(map[string]any)
			if !ok {
				return fmt.Errorf("%s.%s: property schema is not an object", path, name)
			}
			if err := validateSchema(root, childSchema, child, path+"."+name); err != nil {
				return err
			}
		}
	case []any:
		if minimum, ok := numericKeyword(schema, "minItems"); ok && float64(len(typed)) < minimum {
			return fmt.Errorf("%s: has fewer than %.0f items", path, minimum)
		}
		if itemValue, ok := schema["items"]; ok {
			itemSchema, ok := itemValue.(map[string]any)
			if !ok {
				return fmt.Errorf("%s: items schema is not an object", path)
			}
			for index, child := range typed {
				if err := validateSchema(root, itemSchema, child, fmt.Sprintf("%s[%d]", path, index)); err != nil {
					return err
				}
			}
		}
	case string:
		length := float64(utf8.RuneCountInString(typed))
		if minimum, ok := numericKeyword(schema, "minLength"); ok && length < minimum {
			return fmt.Errorf("%s: string is shorter than %.0f", path, minimum)
		}
		if maximum, ok := numericKeyword(schema, "maxLength"); ok && length > maximum {
			return fmt.Errorf("%s: string is longer than %.0f", path, maximum)
		}
		if pattern, ok := schema["pattern"].(string); ok {
			expression, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: protected schema has invalid pattern: %w", path, err)
			}
			if !expression.MatchString(typed) {
				return fmt.Errorf("%s: string does not match %q", path, pattern)
			}
		}
	case json.Number:
		number, err := strconv.ParseFloat(typed.String(), 64)
		if err != nil {
			return fmt.Errorf("%s: invalid JSON number", path)
		}
		if minimum, ok := numericKeyword(schema, "minimum"); ok && number < minimum {
			return fmt.Errorf("%s: number is below %v", path, minimum)
		}
		if maximum, ok := numericKeyword(schema, "maximum"); ok && number > maximum {
			return fmt.Errorf("%s: number is above %v", path, maximum)
		}
	}
	return nil
}

func validateType(expected string, value any, path string) error {
	valid := false
	switch expected {
	case "object":
		_, valid = value.(map[string]any)
	case "array":
		_, valid = value.([]any)
	case "string":
		_, valid = value.(string)
	case "boolean":
		_, valid = value.(bool)
	case "number":
		_, valid = value.(json.Number)
	case "integer":
		if number, ok := value.(json.Number); ok {
			_, err := strconv.ParseInt(number.String(), 10, 64)
			valid = err == nil
		}
	default:
		return fmt.Errorf("%s: unsupported protected schema type %q", path, expected)
	}
	if !valid {
		return fmt.Errorf("%s: want %s", path, expected)
	}
	return nil
}

func numericKeyword(schema map[string]any, name string) (float64, bool) {
	value, exists := schema[name]
	if !exists {
		return 0, false
	}
	switch typed := value.(type) {
	case json.Number:
		number, err := strconv.ParseFloat(typed.String(), 64)
		return number, err == nil
	case float64:
		return typed, true
	default:
		return 0, false
	}
}

func resolveReference(root map[string]any, reference string) (map[string]any, error) {
	if !strings.HasPrefix(reference, "#/") {
		return nil, fmt.Errorf("only local schema references are supported: %q", reference)
	}
	var current any = root
	for _, encoded := range strings.Split(strings.TrimPrefix(reference, "#/"), "/") {
		part := strings.ReplaceAll(strings.ReplaceAll(encoded, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("reference %q traverses a non-object", reference)
		}
		current, ok = object[part]
		if !ok {
			return nil, fmt.Errorf("reference %q is missing %q", reference, part)
		}
	}
	resolved, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("reference %q is not a schema object", reference)
	}
	return resolved, nil
}
