// The seed verifier is intentionally offline so validation stays deterministic.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"vcfarch/architecture"
)

const (
	schemaPath    = "spec/installer.schema.json"
	planPath      = "migration_plan.json"
	inventoryPath = "fixtures/estate.json"
	snapshotPath  = "spec/compatibility_snapshot.json"
)

type capacity struct {
	VCPU      int `json:"vcpu"`
	MemoryGiB int `json:"memory_gib"`
	DiskGiB   int `json:"disk_gib"`
}

type placement struct {
	ID        string   `json:"id"`
	Component string   `json:"component"`
	Version   string   `json:"version"`
	Role      string   `json:"role"`
	Site      string   `json:"site"`
	VCenter   string   `json:"vcenter"`
	Cluster   string   `json:"cluster"`
	Network   string   `json:"network"`
	Profile   string   `json:"profile"`
	Nodes     int      `json:"nodes"`
	Each      capacity `json:"each"`
}

type disposition struct {
	ID          string `json:"id"`
	Disposition string `json:"disposition"`
	Quantity    int    `json:"quantity"`
	Unit        string `json:"unit"`
}

type gate struct {
	ID           string `json:"id"`
	BeforeAction string `json:"before_action"`
}

type supportBoundary struct {
	SourceEOGS string `json:"source_eogs"`
	CompleteBy string `json:"complete_by"`
}

type step struct {
	Order           int             `json:"order"`
	ID              string          `json:"id"`
	SourceID        string          `json:"source_id"`
	SourceProduct   string          `json:"source_product"`
	SourceVersion   string          `json:"source_version"`
	TargetComponent string          `json:"target_component"`
	TargetVersion   string          `json:"target_version"`
	Method          string          `json:"method"`
	PlacementRefs   []string        `json:"placement_refs"`
	DependsOn       []string        `json:"depends_on"`
	Actions         []string        `json:"actions"`
	CarryForward    []disposition   `json:"carry_forward"`
	Abandon         []disposition   `json:"abandon"`
	Gates           []gate          `json:"gates"`
	SupportBoundary supportBoundary `json:"support_boundary"`
}

type topologyDecision struct {
	Selected               string   `json:"selected"`
	VCFOperationsInstances int      `json:"vcf_operations_instances"`
	AllocationID           string   `json:"allocation_id"`
	ManagedVCenters        []string `json:"managed_vcenters"`
	Rejected               struct {
		Topology   string `json:"topology"`
		ReasonCode string `json:"reason_code"`
	} `json:"rejected"`
}

type plan struct {
	SchemaVersion    string           `json:"schema_version"`
	InventoryID      string           `json:"inventory_id"`
	TargetRelease    string           `json:"target_release"`
	CompletionDate   string           `json:"completion_date"`
	TopologyDecision topologyDecision `json:"topology_decision"`
	Placements       []placement      `json:"placements"`
	Steps            []step           `json:"steps"`
}

type entitlementPolicy struct {
	AllocationID                            string   `json:"allocation_id"`
	MaximumRegisteredVCFOperationsInstances int      `json:"maximum_registered_vcf_operations_instances"`
	SelectedTopology                        string   `json:"selected_topology"`
	DisallowedTopology                      string   `json:"disallowed_topology"`
	DisallowedReasonCode                    string   `json:"disallowed_reason_code"`
	ManagedVCenters                         []string `json:"managed_vcenters"`
}

type migrationRule struct {
	Order            int           `json:"order"`
	ID               string        `json:"id"`
	SourceID         string        `json:"source_id"`
	SourceProduct    string        `json:"source_product"`
	SourceVersion    string        `json:"source_version"`
	TargetComponent  string        `json:"target_component"`
	TargetVersion    string        `json:"target_version"`
	Method           string        `json:"method"`
	InPlaceSupported bool          `json:"in_place_supported"`
	PlacementRefs    []string      `json:"placement_refs"`
	DependsOn        []string      `json:"depends_on"`
	Actions          []string      `json:"actions"`
	CarryForward     []disposition `json:"carry_forward"`
	Abandon          []disposition `json:"abandon"`
	Gates            []gate        `json:"gates"`
	EOGS             string        `json:"eogs"`
}

type snapshot struct {
	SnapshotID          string            `json:"snapshot_id"`
	CapturedOn          string            `json:"captured_on"`
	TargetRelease       string            `json:"target_release"`
	InventoryID         string            `json:"inventory_id"`
	CompletionDeadline  string            `json:"completion_deadline"`
	EntitlementPolicy   entitlementPolicy `json:"entitlement_policy"`
	PlacementSelections []placement       `json:"placement_selections"`
	MigrationRules      []migrationRule   `json:"migration_rules"`
}

type site struct {
	ID              string   `json:"id"`
	VCenter         string   `json:"vcenter"`
	Cluster         string   `json:"cluster"`
	Network         string   `json:"network"`
	SubscribedCores int      `json:"subscribed_cores"`
	FreeCapacity    capacity `json:"free_capacity"`
}

type entitlement struct {
	ID              string `json:"id"`
	CoreCapacity    int    `json:"core_capacity"`
	AllocationCount int    `json:"allocation_count"`
	Splittable      bool   `json:"splittable"`
}

type source struct {
	ID      string `json:"id"`
	Product string `json:"product"`
	Version string `json:"version"`
}

type inventory struct {
	InventoryID            string        `json:"inventory_id"`
	TargetRelease          string        `json:"target_release"`
	RequiredCompletionDate string        `json:"required_completion_date"`
	Sites                  []site        `json:"sites"`
	Entitlements           []entitlement `json:"entitlements"`
	Sources                []source      `json:"sources"`
}

func main() {
	// Contract validation is deliberately first. No fixture or compatibility data is
	// opened until the submitted artifact has passed the installer's own schema.
	schemaDoc := decodeJSON(schemaPath)
	planDoc := decodeJSON(planPath)
	problems := validateSchema(schemaDoc, schemaDoc, planDoc, "$")
	if len(problems) != 0 {
		sort.Strings(problems)
		failf("installer schema validation failed:\n  - %s", strings.Join(problems, "\n  - "))
	}

	var got plan
	decodeInto(planPath, &got)
	var inv inventory
	decodeInto(inventoryPath, &inv)
	var snap snapshot
	decodeInto(snapshotPath, &snap)

	if err := verifySemantics(got, inv, snap); err != nil {
		failf("architecture verification failed: %v", err)
	}
	if err := verifyPackage(); err != nil {
		failf("Go package verification failed: %v", err)
	}
	if err := verifyAuthoredTests(); err != nil {
		failf("authored test verification failed: %v", err)
	}
	if err := verifyResearch(); err != nil {
		failf("research provenance verification failed: %v", err)
	}
	fmt.Println("VCF migration architecture verified")
}

func verifyPackage() error {
	planData, err := os.ReadFile(planPath)
	if err != nil {
		return err
	}
	loaded, err := architecture.LoadPlan(bytes.NewReader(planData))
	if err != nil {
		return fmt.Errorf("LoadPlan rejected migration_plan.json: %w", err)
	}

	unknownRoot := bytes.Replace(planData, []byte(`{`), []byte(`{"unexpected":true,`), 1)
	if _, err := architecture.LoadPlan(bytes.NewReader(unknownRoot)); err == nil {
		return fmt.Errorf("LoadPlan accepted an unknown root field")
	}
	unknownNested := bytes.Replace(planData, []byte(`"each": {`), []byte(`"unexpected":true,"each": {`), 1)
	if _, err := architecture.LoadPlan(bytes.NewReader(unknownNested)); err == nil {
		return fmt.Errorf("LoadPlan accepted an unknown nested field")
	}
	trailing := append(append([]byte(nil), planData...), []byte("\n{}")...)
	if _, err := architecture.LoadPlan(bytes.NewReader(trailing)); err == nil {
		return fmt.Errorf("LoadPlan accepted a trailing JSON value")
	}
	if _, err := architecture.LoadPlan(strings.NewReader(`{"schema_version":`)); err == nil {
		return fmt.Errorf("LoadPlan accepted malformed JSON")
	}

	ordered, err := architecture.OrderedSteps(loaded)
	if err != nil {
		return fmt.Errorf("OrderedSteps rejected the valid plan: %w", err)
	}
	if !reflect.DeepEqual(ordered, loaded.Steps) {
		return fmt.Errorf("OrderedSteps changed an already ordered plan")
	}
	orderingCases := []struct {
		name  string
		steps []architecture.Step
	}{
		{name: "duplicate order", steps: []architecture.Step{{Order: 10, ID: "a"}, {Order: 10, ID: "b"}}},
		{name: "decreasing order", steps: []architecture.Step{{Order: 20, ID: "a"}, {Order: 10, ID: "b"}}},
		{name: "forward dependency", steps: []architecture.Step{{Order: 10, ID: "a", DependsOn: []string{"b"}}, {Order: 20, ID: "b"}}},
		{name: "missing dependency", steps: []architecture.Step{{Order: 10, ID: "a"}, {Order: 20, ID: "b", DependsOn: []string{"missing"}}}},
	}
	for _, test := range orderingCases {
		if _, err := architecture.OrderedSteps(architecture.Plan{Steps: test.steps}); err == nil {
			return fmt.Errorf("OrderedSteps accepted %s", test.name)
		}
	}

	totalCases := architecture.Plan{Placements: []architecture.Placement{
		{Site: "chi", Nodes: 3, Each: architecture.Capacity{VCPU: 8, MemoryGiB: 32, DiskGiB: 1024}},
		{Site: "chi", Nodes: 1, Each: architecture.Capacity{VCPU: 4, MemoryGiB: 16, DiskGiB: 256}},
		{Site: "dfw", Nodes: 2, Each: architecture.Capacity{VCPU: 4, MemoryGiB: 32, DiskGiB: 200}},
	}}
	wantTotals := map[string]architecture.Capacity{
		"chi": {VCPU: 28, MemoryGiB: 112, DiskGiB: 3328},
		"dfw": {VCPU: 8, MemoryGiB: 64, DiskGiB: 400},
	}
	if got := architecture.PlacementTotals(totalCases); !reflect.DeepEqual(got, wantTotals) {
		return fmt.Errorf("PlacementTotals returned %#v, want %#v", got, wantTotals)
	}
	return nil
}

func verifyAuthoredTests() error {
	parsed, err := parser.ParseFile(token.NewFileSet(), "architecture/plan_test.go", nil, 0)
	if err != nil {
		return fmt.Errorf("parse architecture/plan_test.go: %w", err)
	}
	testFunctions := 0
	hasTable := false
	ast.Inspect(parsed, func(node ast.Node) bool {
		switch value := node.(type) {
		case *ast.FuncDecl:
			if strings.HasPrefix(value.Name.Name, "Test") {
				testFunctions++
			}
		case *ast.RangeStmt:
			hasTable = true
		}
		return true
	})
	if testFunctions == 0 || !hasTable {
		return fmt.Errorf("plan_test.go must contain table-driven tests for the requested behaviors")
	}
	return nil
}

func verifyResearch() error {
	data, err := os.ReadFile("research.md")
	if err != nil {
		return err
	}
	text := string(data)
	lower := strings.ToLower(text)
	if strings.Contains(lower, ".invalid") {
		return fmt.Errorf("contains a placeholder URL")
	}
	if !strings.Contains(lower, "consult") || !regexp.MustCompile(`20\d{2}-\d{2}-\d{2}`).MatchString(text) {
		return fmt.Errorf("missing an ISO consultation date")
	}
	urlPattern := regexp.MustCompile(`https://(?:[a-z0-9-]+\.)*broadcom\.com/[^\s|)]+`)
	uniqueURLs := map[string]struct{}{}
	lines := strings.Split(text, "\n")
	for i, line := range lines {
		urls := urlPattern.FindAllString(line, -1)
		if len(urls) == 0 {
			continue
		}
		start := i
		if start > 0 {
			start--
		}
		end := i + 2
		if end > len(lines) {
			end = len(lines)
		}
		context := strings.Join(lines[start:end], " ")
		context = strings.TrimSpace(urlPattern.ReplaceAllString(context, ""))
		if len(context) < 40 {
			return fmt.Errorf("each Broadcom source must include a nearby title and architectural fact")
		}
		for _, url := range urls {
			uniqueURLs[url] = struct{}{}
		}
	}
	if len(uniqueURLs) < 3 {
		return fmt.Errorf("must cite distinct Broadcom publications for the three source migrations")
	}
	requiredTerms := []string{
		"8.18.6", "8.18.0", "8.18.2", "vcf operations", "vcf automation",
		"vcf operations for logs", "licens",
	}
	missing := make([]string, 0)
	for _, term := range requiredTerms {
		if !strings.Contains(lower, term) {
			missing = append(missing, term)
		}
	}
	if len(missing) != 0 {
		sort.Strings(missing)
		return fmt.Errorf("missing required coverage: %s", strings.Join(missing, ", "))
	}
	if !strings.Contains(lower, "allocation") && !strings.Contains(lower, "entitlement") {
		return fmt.Errorf("missing license-topology coverage")
	}
	if !strings.Contains(lower, "end of general support") && !strings.Contains(lower, "eogs") && !strings.Contains(lower, "end-of-support") {
		return fmt.Errorf("missing end-of-support coverage")
	}
	return nil
}

func verifySemantics(got plan, inv inventory, snap snapshot) error {
	if got.SchemaVersion != "1.0.0" {
		return fmt.Errorf("schema_version = %q", got.SchemaVersion)
	}
	if got.InventoryID != inv.InventoryID || got.InventoryID != snap.InventoryID {
		return fmt.Errorf("inventory_id does not bind fixture and snapshot")
	}
	if got.TargetRelease != inv.TargetRelease || got.TargetRelease != snap.TargetRelease {
		return fmt.Errorf("target_release does not bind fixture and snapshot")
	}
	if got.CompletionDate != inv.RequiredCompletionDate || got.CompletionDate != snap.CompletionDeadline {
		return fmt.Errorf("completion_date must be %s", snap.CompletionDeadline)
	}

	policy := snap.EntitlementPolicy
	top := got.TopologyDecision
	if top.Selected != policy.SelectedTopology ||
		top.VCFOperationsInstances != policy.MaximumRegisteredVCFOperationsInstances ||
		top.AllocationID != policy.AllocationID ||
		top.Rejected.Topology != policy.DisallowedTopology ||
		top.Rejected.ReasonCode != policy.DisallowedReasonCode ||
		!sameStrings(top.ManagedVCenters, policy.ManagedVCenters) {
		return fmt.Errorf("license topology does not satisfy the pinned entitlement policy")
	}

	ent, ok := findEntitlement(inv.Entitlements, policy.AllocationID)
	if !ok {
		return fmt.Errorf("allocation %q is absent from inventory", policy.AllocationID)
	}
	cores := 0
	for _, s := range inv.Sites {
		cores += s.SubscribedCores
	}
	if cores > ent.CoreCapacity {
		return fmt.Errorf("%d subscribed cores exceed allocation capacity %d", cores, ent.CoreCapacity)
	}
	if ent.AllocationCount != 1 || ent.Splittable {
		return fmt.Errorf("fixture no longer expresses the single non-shareable allocation constraint")
	}

	if err := verifyPlacements(got.Placements, inv.Sites, snap.PlacementSelections); err != nil {
		return err
	}
	if err := verifySteps(got.Steps, inv.Sources, snap.MigrationRules, snap.CompletionDeadline); err != nil {
		return err
	}
	return nil
}

func verifyPlacements(got []placement, sites []site, want []placement) error {
	if len(got) != len(want) {
		return fmt.Errorf("placements: got %d, want %d", len(got), len(want))
	}
	gotByID := make(map[string]placement, len(got))
	for _, p := range got {
		if _, exists := gotByID[p.ID]; exists {
			return fmt.Errorf("duplicate placement %q", p.ID)
		}
		gotByID[p.ID] = p
	}
	for _, expected := range want {
		actual, ok := gotByID[expected.ID]
		if !ok {
			return fmt.Errorf("missing placement %q", expected.ID)
		}
		if !reflect.DeepEqual(actual, expected) {
			return fmt.Errorf("placement %q does not match the pinned component, location, topology, and size", expected.ID)
		}
	}

	used := map[string]capacity{}
	for _, p := range got {
		total := used[p.Site]
		total.VCPU += p.Nodes * p.Each.VCPU
		total.MemoryGiB += p.Nodes * p.Each.MemoryGiB
		total.DiskGiB += p.Nodes * p.Each.DiskGiB
		used[p.Site] = total
	}
	for _, s := range sites {
		u := used[s.ID]
		if u.VCPU > s.FreeCapacity.VCPU || u.MemoryGiB > s.FreeCapacity.MemoryGiB || u.DiskGiB > s.FreeCapacity.DiskGiB {
			return fmt.Errorf("placements exceed free capacity at site %q", s.ID)
		}
	}
	return nil
}

func verifySteps(got []step, sources []source, rules []migrationRule, deadline string) error {
	if len(got) != len(rules) || len(got) != len(sources) {
		return fmt.Errorf("steps must map every and only inventoried source")
	}
	seenSteps := map[string]bool{}
	seenSources := map[string]bool{}
	previousOrder := 0
	for i, rule := range rules {
		actual := got[i]
		if actual.Order <= previousOrder {
			return fmt.Errorf("steps are not strictly ordered at %q", actual.ID)
		}
		previousOrder = actual.Order
		if actual.Order != rule.Order || actual.ID != rule.ID || actual.SourceID != rule.SourceID ||
			actual.SourceProduct != rule.SourceProduct || actual.SourceVersion != rule.SourceVersion ||
			actual.TargetComponent != rule.TargetComponent || actual.TargetVersion != rule.TargetVersion ||
			actual.Method != rule.Method {
			return fmt.Errorf("step %d does not match pinned source/target compatibility rule %q", i, rule.ID)
		}
		if !reflect.DeepEqual(actual.PlacementRefs, rule.PlacementRefs) ||
			!reflect.DeepEqual(actual.DependsOn, rule.DependsOn) ||
			!reflect.DeepEqual(actual.Actions, rule.Actions) {
			return fmt.Errorf("step %q has incorrect placement, dependency, or action ordering", rule.ID)
		}
		if !sameDispositions(actual.CarryForward, rule.CarryForward) {
			return fmt.Errorf("step %q does not account for all supported carried content/configuration", rule.ID)
		}
		if !sameDispositions(actual.Abandon, rule.Abandon) {
			return fmt.Errorf("step %q does not account for all abandoned content/configuration", rule.ID)
		}
		if !sameGates(actual.Gates, rule.Gates) {
			return fmt.Errorf("step %q has incorrect technical gates", rule.ID)
		}
		for _, g := range actual.Gates {
			if !contains(actual.Actions, g.BeforeAction) {
				return fmt.Errorf("gate %q refers to absent action %q", g.ID, g.BeforeAction)
			}
		}
		if actual.SupportBoundary.SourceEOGS != rule.EOGS || actual.SupportBoundary.CompleteBy != deadline {
			return fmt.Errorf("step %q has incorrect support boundary", rule.ID)
		}
		completeBy, _ := time.Parse("2006-01-02", actual.SupportBoundary.CompleteBy)
		eogs, _ := time.Parse("2006-01-02", actual.SupportBoundary.SourceEOGS)
		if !completeBy.Before(eogs) {
			return fmt.Errorf("step %q completion is not before source EOGS", rule.ID)
		}
		for _, dep := range actual.DependsOn {
			if !seenSteps[dep] {
				return fmt.Errorf("step %q depends on non-earlier step %q", rule.ID, dep)
			}
		}
		if seenSteps[actual.ID] || seenSources[actual.SourceID] {
			return fmt.Errorf("duplicate step or source mapping at %q", actual.ID)
		}
		seenSteps[actual.ID] = true
		seenSources[actual.SourceID] = true
	}
	for _, src := range sources {
		if !seenSources[src.ID] {
			return fmt.Errorf("source %q is not mapped", src.ID)
		}
	}
	return nil
}

func sameDispositions(a, b []disposition) bool {
	if len(a) != len(b) {
		return false
	}
	am := make(map[string]disposition, len(a))
	for _, item := range a {
		if _, exists := am[item.ID]; exists {
			return false
		}
		am[item.ID] = item
	}
	for _, item := range b {
		if !reflect.DeepEqual(am[item.ID], item) {
			return false
		}
	}
	return true
}

func sameGates(a, b []gate) bool {
	if len(a) != len(b) {
		return false
	}
	am := make(map[string]gate, len(a))
	for _, item := range a {
		if _, exists := am[item.ID]; exists {
			return false
		}
		am[item.ID] = item
	}
	for _, item := range b {
		if !reflect.DeepEqual(am[item.ID], item) {
			return false
		}
	}
	return true
}

func findEntitlement(all []entitlement, id string) (entitlement, bool) {
	for _, item := range all {
		if item.ID == id {
			return item, true
		}
	}
	return entitlement{}, false
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

func contains(all []string, want string) bool {
	for _, item := range all {
		if item == want {
			return true
		}
	}
	return false
}

func decodeJSON(path string) any {
	f, err := os.Open(path)
	if err != nil {
		failf("read %s: %v", path, err)
	}
	defer f.Close()
	dec := json.NewDecoder(f)
	dec.UseNumber()
	var value any
	if err := dec.Decode(&value); err != nil {
		failf("decode %s: %v", path, err)
	}
	var trailing any
	if err := dec.Decode(&trailing); err != io.EOF {
		if err == nil {
			failf("decode %s: trailing JSON value", path)
		}
		failf("decode %s trailer: %v", path, err)
	}
	return value
}

func decodeInto(path string, out any) {
	data, err := os.ReadFile(path)
	if err != nil {
		failf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, out); err != nil {
		failf("decode %s: %v", path, err)
	}
}

func validateSchema(root, schema, value any, path string) []string {
	s, ok := schema.(map[string]any)
	if !ok {
		return []string{path + ": schema node is not an object"}
	}
	if ref, ok := s["$ref"].(string); ok {
		resolved, err := resolveRef(root, ref)
		if err != nil {
			return []string{path + ": " + err.Error()}
		}
		return validateSchema(root, resolved, value, path)
	}

	var problems []string
	if expected, ok := s["type"].(string); ok && !matchesType(value, expected) {
		return []string{fmt.Sprintf("%s: expected %s", path, expected)}
	}
	if constant, ok := s["const"]; ok && !reflect.DeepEqual(value, constant) {
		problems = append(problems, path+": value does not match const")
	}
	if choices, ok := s["enum"].([]any); ok {
		matched := false
		for _, choice := range choices {
			matched = matched || reflect.DeepEqual(value, choice)
		}
		if !matched {
			problems = append(problems, path+": value is not in enum")
		}
	}

	if text, ok := value.(string); ok {
		if min, ok := numberAsInt(s["minLength"]); ok && len([]rune(text)) < min {
			problems = append(problems, path+": string is shorter than minLength")
		}
		if format, _ := s["format"].(string); format == "date" {
			if _, err := time.Parse("2006-01-02", text); err != nil {
				problems = append(problems, path+": invalid date")
			}
		}
	}
	if n, ok := value.(json.Number); ok {
		if min, exists := numberAsFloat(s["minimum"]); exists {
			actual, _ := strconv.ParseFloat(n.String(), 64)
			if actual < min {
				problems = append(problems, path+": number is below minimum")
			}
		}
	}

	if object, ok := value.(map[string]any); ok {
		properties, _ := s["properties"].(map[string]any)
		if required, ok := s["required"].([]any); ok {
			for _, raw := range required {
				name, _ := raw.(string)
				if _, exists := object[name]; !exists {
					problems = append(problems, path+": missing required property "+name)
				}
			}
		}
		for name, child := range object {
			childSchema, exists := properties[name]
			if !exists {
				if allowed, isBool := s["additionalProperties"].(bool); isBool && !allowed {
					problems = append(problems, path+": unknown property "+name)
				}
				continue
			}
			problems = append(problems, validateSchema(root, childSchema, child, path+"."+name)...)
		}
	}

	if array, ok := value.([]any); ok {
		if min, ok := numberAsInt(s["minItems"]); ok && len(array) < min {
			problems = append(problems, path+": array is shorter than minItems")
		}
		if unique, _ := s["uniqueItems"].(bool); unique {
			seen := map[string]bool{}
			for _, item := range array {
				encoded, _ := json.Marshal(item)
				key := string(encoded)
				if seen[key] {
					problems = append(problems, path+": array items are not unique")
					break
				}
				seen[key] = true
			}
		}
		if itemSchema, exists := s["items"]; exists {
			for i, item := range array {
				problems = append(problems, validateSchema(root, itemSchema, item, fmt.Sprintf("%s[%d]", path, i))...)
			}
		}
	}
	return problems
}

func resolveRef(root any, ref string) (any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("unsupported schema reference %q", ref)
	}
	current := root
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("invalid schema reference %q", ref)
		}
		current, ok = object[token]
		if !ok {
			return nil, fmt.Errorf("unresolved schema reference %q", ref)
		}
	}
	return current, nil
}

func matchesType(value any, expected string) bool {
	switch expected {
	case "object":
		_, ok := value.(map[string]any)
		return ok
	case "array":
		_, ok := value.([]any)
		return ok
	case "string":
		_, ok := value.(string)
		return ok
	case "integer":
		n, ok := value.(json.Number)
		if !ok {
			return false
		}
		_, err := strconv.ParseInt(n.String(), 10, 64)
		return err == nil
	case "number":
		_, ok := value.(json.Number)
		return ok
	case "boolean":
		_, ok := value.(bool)
		return ok
	case "null":
		return value == nil
	default:
		return false
	}
}

func numberAsInt(value any) (int, bool) {
	n, ok := value.(json.Number)
	if !ok {
		return 0, false
	}
	v, err := strconv.Atoi(n.String())
	return v, err == nil
}

func numberAsFloat(value any) (float64, bool) {
	n, ok := value.(json.Number)
	if !ok {
		return 0, false
	}
	v, err := strconv.ParseFloat(n.String(), 64)
	return v, err == nil
}

func failf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}
