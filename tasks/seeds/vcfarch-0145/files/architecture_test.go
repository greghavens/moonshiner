package architecture

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"testing"
	"unicode/utf8"
)

type rawArtifact struct {
	Greenfield struct {
		Topology map[string]any  `json:"topology"`
		SddcSpec json.RawMessage `json:"sddcSpec"`
	} `json:"greenfield"`
	ExistingEstate json.RawMessage `json:"existingEstate"`
}

type inventory struct {
	InventoryID string `json:"inventoryId"`
	Topology    struct {
		Sites             int      `json:"sites"`
		AvailabilityZones int      `json:"availabilityZones"`
		ClusterModel      string   `json:"clusterModel"`
		ManagementDomain  string   `json:"managementDomain"`
		Storage           string   `json:"storage"`
		Hosts             []string `json:"hosts"`
	} `json:"topology"`
	Networks []struct {
		Type    string `json:"type"`
		VLANID  int    `json:"vlanId"`
		Subnet  string `json:"subnet"`
		Gateway string `json:"gateway"`
		MTU     int    `json:"mtu"`
	} `json:"networks"`
	Components []struct {
		ID      string `json:"id"`
		Version string `json:"version"`
	} `json:"components"`
}

type compatibilitySnapshot struct {
	SchemaVersion   int        `json:"schemaVersion"`
	CapturedAt      string     `json:"capturedAt"`
	MinimumTopology Topology   `json:"minimumTopology"`
	SourceVersion   string     `json:"sourceVersion"`
	TargetVersion   string     `json:"targetVersion"`
	AllowedPaths    [][]string `json:"allowedPaths"`
	ForbiddenHops   []struct {
		Component string `json:"component"`
		From      string `json:"from"`
		To        string `json:"to"`
		Gate      string `json:"gate"`
	} `json:"forbiddenHops"`
	ComponentRules []struct {
		Component          string   `json:"component"`
		ComponentPattern   string   `json:"componentPattern"`
		TargetVersion      string   `json:"targetVersion"`
		Gates              []string `json:"gates"`
		RequiresComponents []string `json:"requiresComponents"`
	} `json:"componentRules"`
	Provenance json.RawMessage `json:"provenance"`
}

func TestArchitectureArtifact(t *testing.T) {
	artifactBytes, err := os.ReadFile("architecture.json")
	if err != nil {
		t.Fatalf("read architecture.json: %v", err)
	}

	// The installer-owned SddcSpec schema is deliberately the first validation.
	// Do not add topology, migration, fixture, or package checks above this block.
	var raw rawArtifact
	decodeJSON(t, "architecture wrapper", artifactBytes, &raw, false)
	if len(raw.Greenfield.SddcSpec) == 0 {
		t.Fatal("installer schema: greenfield.sddcSpec is missing")
	}
	openAPI := decodeAnyFile(t, "specifications/vcf-installer/vcf-installer-openapi.json")
	root, ok := openAPI.(map[string]any)
	if !ok {
		t.Fatal("installer schema root is not an object")
	}
	sddcSchema, err := jsonPointer(root, "#/components/schemas/SddcSpec")
	if err != nil {
		t.Fatalf("find installer SddcSpec schema: %v", err)
	}
	sddcValue := decodeAny(t, "greenfield.sddcSpec", raw.Greenfield.SddcSpec)
	if problems := validateJSONSchema(sddcValue, sddcSchema, root, "$.greenfield.sddcSpec"); len(problems) != 0 {
		t.Fatalf("installer SddcSpec schema validation failed:\n  %s", strings.Join(problems, "\n  "))
	}

	// Only after SddcSpec succeeds may the verifier inspect migration/schema and
	// architectural semantics. All remaining authority is checked-in fixture data.
	migrationSchema := decodeAnyFile(t, "testdata/migration_plan.schema.json")
	migrationValue := decodeAny(t, "existingEstate", raw.ExistingEstate)
	if problems := validateJSONSchema(migrationValue, migrationSchema, migrationSchema, "$.existingEstate"); len(problems) != 0 {
		t.Fatalf("migration plan schema validation failed:\n  %s", strings.Join(problems, "\n  "))
	}

	var artifact Architecture
	decodeJSON(t, "architecture.json", artifactBytes, &artifact, true)
	var inv inventory
	decodeJSONFile(t, "testdata/estate_inventory.json", &inv)
	var snapshot compatibilitySnapshot
	decodeJSONFile(t, "testdata/compatibility_snapshot.json", &snapshot)

	t.Run("greenfield topology and installer intent", func(t *testing.T) {
		checkGreenfield(t, artifact.Greenfield, inv, snapshot)
	})
	t.Run("migration follows pinned compatibility", func(t *testing.T) {
		checkMigration(t, artifact.ExistingEstate, inv, snapshot)
	})
	t.Run("package embeds the committed artifact", func(t *testing.T) {
		loaded, err := Load()
		if err != nil {
			t.Fatalf("Load: %v", err)
		}
		want := canonicalJSON(t, artifact)
		got := canonicalJSON(t, loaded)
		if !bytes.Equal(got, want) {
			t.Fatalf("Load() differs from architecture.json\n got: %s\nwant: %s", got, want)
		}
	})
}

func TestLoadRejectsInvalidEmbeddedJSON(t *testing.T) {
	original := artifactJSON
	t.Cleanup(func() { artifactJSON = original })
	cases := []struct {
		name string
		data string
	}{
		{"malformed", `{"greenfield":`},
		{"unknown field", `{"greenfield":{},"existingEstate":{},"surprise":true}`},
		{"trailing value", `{} {}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			artifactJSON = []byte(tc.data)
			if _, err := Load(); err == nil {
				t.Fatal("Load unexpectedly accepted invalid embedded JSON")
			}
		})
	}
}

func checkGreenfield(t *testing.T, design GreenfieldDesign, inv inventory, snapshot compatibilitySnapshot) {
	t.Helper()
	want := snapshot.MinimumTopology
	checks := []struct {
		name string
		got  any
		want any
	}{
		{"sites", design.Topology.Sites, want.Sites},
		{"availability zones", design.Topology.AvailabilityZones, want.AvailabilityZones},
		{"cluster model", design.Topology.ClusterModel, want.ClusterModel},
		{"management domain", design.Topology.ManagementDomain, inv.Topology.ManagementDomain},
		{"host count", design.Topology.HostCount, want.HostCount},
		{"storage", design.Topology.Storage, want.Storage},
	}
	for _, tc := range checks {
		t.Run(tc.name, func(t *testing.T) {
			if !reflect.DeepEqual(tc.got, tc.want) {
				t.Errorf("got %v, want %v", tc.got, tc.want)
			}
		})
	}

	var spec struct {
		SddcID       string `json:"sddcId"`
		WorkflowType string `json:"workflowType"`
		Version      string `json:"version"`
		HostSpecs    []struct {
			Hostname string `json:"hostname"`
		} `json:"hostSpecs"`
		ClusterSpec struct {
			ResourcePools []struct {
				Type string `json:"type"`
			} `json:"resourcePoolSpecs"`
		} `json:"clusterSpec"`
		NetworkSpecs []struct {
			NetworkType string `json:"networkType"`
			VLANID      int    `json:"vlanId"`
			Subnet      string `json:"subnet"`
			Gateway     string `json:"gateway"`
			MTU         int    `json:"mtu"`
		} `json:"networkSpecs"`
		DatastoreSpec struct {
			VsanSpec struct {
				ESA struct {
					Enabled bool `json:"enabled"`
				} `json:"esaConfig"`
			} `json:"vsanSpec"`
		} `json:"datastoreSpec"`
	}
	decodeJSON(t, "greenfield.sddcSpec", design.SddcSpec, &spec, false)
	if spec.SddcID != inv.Topology.ManagementDomain || spec.WorkflowType != "VCF" || spec.Version != snapshot.TargetVersion {
		t.Errorf("SddcSpec identity/version = %q/%q/%q", spec.SddcID, spec.WorkflowType, spec.Version)
	}
	if len(spec.HostSpecs) != snapshot.MinimumTopology.HostCount {
		t.Fatalf("SddcSpec has %d hosts, want exactly %d", len(spec.HostSpecs), snapshot.MinimumTopology.HostCount)
	}
	gotHosts := make([]string, len(spec.HostSpecs))
	for i, host := range spec.HostSpecs {
		gotHosts[i] = host.Hostname
	}
	if !sameStringSet(gotHosts, inv.Topology.Hosts) {
		t.Errorf("SddcSpec hosts = %v, want %v", gotHosts, inv.Topology.Hosts)
	}
	poolTypes := make([]string, len(spec.ClusterSpec.ResourcePools))
	for i, pool := range spec.ClusterSpec.ResourcePools {
		poolTypes[i] = pool.Type
	}
	if !contains(poolTypes, "management") || !contains(poolTypes, "compute") {
		t.Errorf("consolidated cluster needs management and compute resource pools, got %v", poolTypes)
	}
	if !spec.DatastoreSpec.VsanSpec.ESA.Enabled {
		t.Error("greenfield storage must enable vSAN ESA")
	}

	gotNetworks := make(map[string]string, len(spec.NetworkSpecs))
	for _, network := range spec.NetworkSpecs {
		gotNetworks[network.NetworkType] = fmt.Sprintf("%d|%s|%s|%d", network.VLANID, network.Subnet, network.Gateway, network.MTU)
	}
	for _, network := range inv.Networks {
		wantNetwork := fmt.Sprintf("%d|%s|%s|%d", network.VLANID, network.Subnet, network.Gateway, network.MTU)
		if gotNetworks[network.Type] != wantNetwork {
			t.Errorf("network %s = %q, want %q", network.Type, gotNetworks[network.Type], wantNetwork)
		}
	}
}

func checkMigration(t *testing.T, plan MigrationPlan, inv inventory, snapshot compatibilitySnapshot) {
	t.Helper()
	if plan.InventoryID != inv.InventoryID {
		t.Errorf("inventoryId = %q, want %q", plan.InventoryID, inv.InventoryID)
	}
	if plan.SourceVersion != snapshot.SourceVersion || plan.TargetVersion != snapshot.TargetVersion {
		t.Errorf("source/target = %q/%q, want %q/%q", plan.SourceVersion, plan.TargetVersion, snapshot.SourceVersion, snapshot.TargetVersion)
	}
	pathAllowed := false
	for _, allowed := range snapshot.AllowedPaths {
		if reflect.DeepEqual(plan.SelectedPath, allowed) {
			pathAllowed = true
			break
		}
	}
	if !pathAllowed {
		t.Errorf("selected path %v is not in pinned allowed paths %v", plan.SelectedPath, snapshot.AllowedPaths)
	}

	versions := make(map[string]string, len(inv.Components))
	for _, component := range inv.Components {
		versions[component.ID] = component.Version
	}
	orders := make(map[string]int, len(plan.Steps))
	seenOrder := make(map[int]string, len(plan.Steps))
	for _, step := range plan.Steps {
		if _, duplicate := orders[step.Component]; duplicate {
			t.Errorf("component %q appears more than once", step.Component)
			continue
		}
		orders[step.Component] = step.Order
		if other, duplicate := seenOrder[step.Order]; duplicate {
			t.Errorf("order %d is shared by %q and %q", step.Order, other, step.Component)
		}
		seenOrder[step.Order] = step.Component
		current, exists := versions[step.Component]
		if !exists {
			t.Errorf("step names component %q absent from inventory", step.Component)
			continue
		}
		if step.CurrentVersion != current {
			t.Errorf("%s current version = %q, want %q", step.Component, step.CurrentVersion, current)
		}
		rule, ok := ruleFor(step.Component, snapshot)
		if !ok {
			t.Errorf("no pinned compatibility rule for %q", step.Component)
			continue
		}
		if step.TargetVersion != rule.TargetVersion {
			t.Errorf("%s target = %q, want %q", step.Component, step.TargetVersion, rule.TargetVersion)
		}
		if !sameStringSet(step.GatedBy, rule.Gates) {
			t.Errorf("%s gates = %v, want %v", step.Component, step.GatedBy, rule.Gates)
		}
	}
	if len(orders) != len(inv.Components) {
		t.Errorf("plan covers %d unique components, inventory has %d", len(orders), len(inv.Components))
	}
	for order := 1; order <= len(inv.Components); order++ {
		if _, ok := seenOrder[order]; !ok {
			t.Errorf("ordered plan has no step %d", order)
		}
	}
	for _, component := range inv.Components {
		if _, ok := orders[component.ID]; !ok {
			t.Errorf("inventory component %q has no step", component.ID)
		}
	}
	for _, rule := range snapshot.ComponentRules {
		if rule.Component == "" {
			continue
		}
		for _, prerequisite := range rule.RequiresComponents {
			if orders[prerequisite] >= orders[rule.Component] {
				t.Errorf("%s must precede %s", prerequisite, rule.Component)
			}
		}
	}
	for _, component := range inv.Components {
		rule, ok := ruleFor(component.ID, snapshot)
		if !ok || rule.ComponentPattern == "" {
			continue
		}
		for _, prerequisite := range rule.RequiresComponents {
			if orders[prerequisite] >= orders[component.ID] {
				t.Errorf("%s must precede %s", prerequisite, component.ID)
			}
		}
	}

	avoided := make(map[string]bool, len(plan.AvoidedHops))
	for _, hop := range plan.AvoidedHops {
		key := hop.Component + "|" + hop.From + "|" + hop.To + "|" + hop.Gate
		if avoided[key] {
			t.Errorf("avoided hop appears more than once: %s", key)
		}
		avoided[key] = true
	}
	requiredAvoided := make(map[string]bool)
	for _, hop := range snapshot.ForbiddenHops {
		if versions[hop.Component] != hop.From {
			continue
		}
		key := hop.Component + "|" + hop.From + "|" + hop.To + "|" + hop.Gate
		requiredAvoided[key] = true
		if !avoided[key] {
			t.Errorf("applicable forbidden hop not recorded: %s", key)
		}
		for _, step := range plan.Steps {
			if step.Component == hop.Component && step.TargetVersion == hop.To {
				t.Errorf("plan uses forbidden hop %s", key)
			}
		}
	}
	for key := range avoided {
		if !requiredAvoided[key] {
			t.Errorf("avoided hop is not applicable under the pinned snapshot: %s", key)
		}
	}
}

func ruleFor(component string, snapshot compatibilitySnapshot) (struct {
	Component          string   `json:"component"`
	ComponentPattern   string   `json:"componentPattern"`
	TargetVersion      string   `json:"targetVersion"`
	Gates              []string `json:"gates"`
	RequiresComponents []string `json:"requiresComponents"`
}, bool) {
	for _, rule := range snapshot.ComponentRules {
		if rule.Component == component {
			return rule, true
		}
		if rule.ComponentPattern != "" && regexp.MustCompile(rule.ComponentPattern).MatchString(component) {
			return rule, true
		}
	}
	return struct {
		Component          string   `json:"component"`
		ComponentPattern   string   `json:"componentPattern"`
		TargetVersion      string   `json:"targetVersion"`
		Gates              []string `json:"gates"`
		RequiresComponents []string `json:"requiresComponents"`
	}{}, false
}

func decodeJSONFile(t *testing.T, path string, target any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	decodeJSON(t, path, data, target, true)
}

func decodeJSON(t *testing.T, name string, data []byte, target any, strict bool) {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(data))
	if strict {
		decoder.DisallowUnknownFields()
	}
	if err := decoder.Decode(target); err != nil {
		t.Fatalf("decode %s: %v", name, err)
	}
	if decoder.More() {
		t.Fatalf("decode %s: trailing JSON values", name)
	}
}

func decodeAnyFile(t *testing.T, path string) any {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return decodeAny(t, path, data)
}

func decodeAny(t *testing.T, name string, data []byte) any {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		t.Fatalf("decode %s: %v", name, err)
	}
	return value
}

func canonicalJSON(t *testing.T, value any) []byte {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("marshal canonical JSON: %v", err)
	}
	return data
}

func contains(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}

func sameStringSet(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	ac := append([]string(nil), a...)
	bc := append([]string(nil), b...)
	sort.Strings(ac)
	sort.Strings(bc)
	return reflect.DeepEqual(ac, bc)
}

// validateJSONSchema implements the JSON Schema/OpenAPI keywords exercised by
// the pinned installer schema and migration contract. Unknown annotation-only
// keywords are ignored, but validation keywords below are always enforced.
func validateJSONSchema(value, schema, root any, path string) []string {
	s, ok := schema.(map[string]any)
	if !ok {
		return []string{path + ": schema is not an object"}
	}
	if ref, ok := s["$ref"].(string); ok {
		resolved, err := jsonPointer(root, ref)
		if err != nil {
			return []string{fmt.Sprintf("%s: resolve %s: %v", path, ref, err)}
		}
		return validateJSONSchema(value, resolved, root, path)
	}
	if nullable, _ := s["nullable"].(bool); nullable && value == nil {
		return nil
	}
	if value == nil {
		if schemaType, _ := s["type"].(string); schemaType == "null" || schemaType == "" {
			return nil
		}
		return []string{path + ": null does not satisfy schema type"}
	}

	var problems []string
	if allOf, ok := s["allOf"].([]any); ok {
		for _, child := range allOf {
			problems = append(problems, validateJSONSchema(value, child, root, path)...)
		}
	}
	if anyOf, ok := s["anyOf"].([]any); ok {
		matched := false
		for _, child := range anyOf {
			if len(validateJSONSchema(value, child, root, path)) == 0 {
				matched = true
				break
			}
		}
		if !matched {
			problems = append(problems, path+": does not satisfy anyOf")
		}
	}
	if oneOf, ok := s["oneOf"].([]any); ok {
		matches := 0
		for _, child := range oneOf {
			if len(validateJSONSchema(value, child, root, path)) == 0 {
				matches++
			}
		}
		if matches != 1 {
			problems = append(problems, fmt.Sprintf("%s: satisfies %d oneOf branches", path, matches))
		}
	}
	if enum, ok := s["enum"].([]any); ok {
		matched := false
		for _, candidate := range enum {
			if reflect.DeepEqual(value, candidate) || fmt.Sprint(value) == fmt.Sprint(candidate) {
				matched = true
				break
			}
		}
		if !matched {
			problems = append(problems, fmt.Sprintf("%s: %v is not in enum", path, value))
		}
	}
	if constant, ok := s["const"]; ok && !reflect.DeepEqual(value, constant) {
		problems = append(problems, fmt.Sprintf("%s: %v does not equal const %v", path, value, constant))
	}

	schemaType, _ := s["type"].(string)
	switch schemaType {
	case "object":
		object, ok := value.(map[string]any)
		if !ok {
			return append(problems, fmt.Sprintf("%s: got %T, want object", path, value))
		}
		if required, ok := s["required"].([]any); ok {
			for _, item := range required {
				key, _ := item.(string)
				if _, exists := object[key]; !exists {
					problems = append(problems, fmt.Sprintf("%s: missing required property %q", path, key))
				}
			}
		}
		properties, _ := s["properties"].(map[string]any)
		for key, childValue := range object {
			childSchema, known := properties[key]
			if !known {
				if allowed, isBool := s["additionalProperties"].(bool); isBool && !allowed {
					problems = append(problems, fmt.Sprintf("%s: unknown property %q", path, key))
				}
				continue
			}
			problems = append(problems, validateJSONSchema(childValue, childSchema, root, path+"."+key)...)
		}
	case "array":
		array, ok := value.([]any)
		if !ok {
			return append(problems, fmt.Sprintf("%s: got %T, want array", path, value))
		}
		if minimum, ok := numberKeyword(s, "minItems"); ok && float64(len(array)) < minimum {
			problems = append(problems, fmt.Sprintf("%s: has %d items, minimum is %v", path, len(array), minimum))
		}
		if maximum, ok := numberKeyword(s, "maxItems"); ok && float64(len(array)) > maximum {
			problems = append(problems, fmt.Sprintf("%s: has %d items, maximum is %v", path, len(array), maximum))
		}
		if unique, _ := s["uniqueItems"].(bool); unique {
			seen := map[string]bool{}
			for _, item := range array {
				encoded, _ := json.Marshal(item)
				if seen[string(encoded)] {
					problems = append(problems, path+": items are not unique")
					break
				}
				seen[string(encoded)] = true
			}
		}
		if items, exists := s["items"]; exists {
			for i, item := range array {
				problems = append(problems, validateJSONSchema(item, items, root, fmt.Sprintf("%s[%d]", path, i))...)
			}
		}
	case "string":
		text, ok := value.(string)
		if !ok {
			return append(problems, fmt.Sprintf("%s: got %T, want string", path, value))
		}
		length := float64(utf8.RuneCountInString(text))
		if minimum, ok := numberKeyword(s, "minLength"); ok && length < minimum {
			problems = append(problems, fmt.Sprintf("%s: string is shorter than %v", path, minimum))
		}
		if maximum, ok := numberKeyword(s, "maxLength"); ok && length > maximum {
			problems = append(problems, fmt.Sprintf("%s: string is longer than %v", path, maximum))
		}
		if pattern, ok := s["pattern"].(string); ok {
			re, err := regexp.Compile(pattern)
			if err != nil {
				problems = append(problems, fmt.Sprintf("%s: invalid schema pattern %q", path, pattern))
			} else if !re.MatchString(text) {
				problems = append(problems, fmt.Sprintf("%s: %q does not match %q", path, text, pattern))
			}
		}
	case "integer":
		number, ok := asFloat(value)
		if !ok || math.Trunc(number) != number {
			return append(problems, fmt.Sprintf("%s: got %v, want integer", path, value))
		}
		problems = append(problems, validateNumber(number, s, path)...)
	case "number":
		number, ok := asFloat(value)
		if !ok {
			return append(problems, fmt.Sprintf("%s: got %v, want number", path, value))
		}
		problems = append(problems, validateNumber(number, s, path)...)
	case "boolean":
		if _, ok := value.(bool); !ok {
			problems = append(problems, fmt.Sprintf("%s: got %T, want boolean", path, value))
		}
	case "null":
		problems = append(problems, path+": value is not null")
	}
	return problems
}

func validateNumber(value float64, schema map[string]any, path string) []string {
	var problems []string
	if minimum, ok := numberKeyword(schema, "minimum"); ok && value < minimum {
		problems = append(problems, fmt.Sprintf("%s: %v is below minimum %v", path, value, minimum))
	}
	if maximum, ok := numberKeyword(schema, "maximum"); ok && value > maximum {
		problems = append(problems, fmt.Sprintf("%s: %v is above maximum %v", path, value, maximum))
	}
	return problems
}

func numberKeyword(schema map[string]any, key string) (float64, bool) {
	value, exists := schema[key]
	if !exists {
		return 0, false
	}
	return asFloat(value)
}

func asFloat(value any) (float64, bool) {
	switch number := value.(type) {
	case json.Number:
		parsed, err := strconv.ParseFloat(string(number), 64)
		return parsed, err == nil
	case float64:
		return number, true
	case float32:
		return float64(number), true
	case int:
		return float64(number), true
	case int64:
		return float64(number), true
	default:
		return 0, false
	}
}

func jsonPointer(root any, pointer string) (any, error) {
	if pointer == "#" {
		return root, nil
	}
	if !strings.HasPrefix(pointer, "#/") {
		return nil, fmt.Errorf("only local JSON pointers are supported")
	}
	current := root
	for _, encoded := range strings.Split(strings.TrimPrefix(pointer, "#/"), "/") {
		key := strings.ReplaceAll(strings.ReplaceAll(encoded, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%q traverses a non-object", key)
		}
		next, exists := object[key]
		if !exists {
			return nil, fmt.Errorf("property %q not found", key)
		}
		current = next
	}
	return current, nil
}
