package verify

import (
	"encoding/json"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"
	"unicode"

	"github.com/santhosh-tekuri/jsonschema/v6"

	"vcfarch/architecture"
)

var researchURL = regexp.MustCompile(`https?://[^\s<>\[\]()]+`)

func TestResearchArtifact(t *testing.T) {
	root := filepath.Join("..", "..")
	document := strings.TrimSpace(string(readFile(t, filepath.Join(root, "research.md"))))
	sources := map[string]bool{}
	conclusions := map[string]bool{}
	lines := strings.Split(document, "\n")
	for lineIndex, line := range lines {
		for _, matched := range researchURL.FindAllString(line, -1) {
			raw := strings.TrimRight(matched, `.,;:'\"`)
			parsed, err := url.Parse(raw)
			if err != nil || parsed.Hostname() == "" {
				t.Fatalf("research.md contains an invalid source URL %q", raw)
			}
			host := strings.ToLower(parsed.Hostname())
			if isFixtureHost(host) {
				t.Errorf("research.md source %q is not a real public source", raw)
			}
			sources[raw] = true

			context := researchURL.ReplaceAllString(line, "")
			for next := lineIndex + 1; meaningfulRunes(context) < 16 && next < len(lines) && next <= lineIndex+2; next++ {
				if researchURL.MatchString(lines[next]) {
					break
				}
				context += " " + lines[next]
			}
			if meaningfulRunes(context) >= 16 {
				conclusions[raw] = true
			}
		}
	}
	if len(sources) < 2 {
		t.Fatalf("research.md contains %d distinct source URLs, want at least 2", len(sources))
	}
	for source := range sources {
		if !conclusions[source] {
			t.Errorf("research.md does not state a relevant conclusion with source %q", source)
		}
	}
}

func meaningfulRunes(text string) int {
	count := 0
	for _, current := range text {
		if unicode.IsLetter(current) || unicode.IsDigit(current) {
			count++
		}
	}
	return count
}

func isFixtureHost(host string) bool {
	if host == "localhost" || host == "example.com" || host == "example.net" || host == "example.org" {
		return true
	}
	for _, suffix := range []string{".localhost", ".invalid", ".test", ".example"} {
		if strings.HasSuffix(host, suffix) {
			return true
		}
	}
	if ip := net.ParseIP(host); ip != nil {
		return ip.IsLoopback() || ip.IsPrivate() || ip.IsUnspecified()
	}
	return !strings.Contains(host, ".")
}

// TestArtifacts is deliberately one ordered test. Installer-schema validation
// is a parent-level prerequisite, so no migration or semantic assertion runs
// when the SddcSpec does not conform to the pinned upstream component schema.
func TestArtifacts(t *testing.T) {
	root := filepath.Join("..", "..")
	sddc := readJSON(t, filepath.Join(root, "artifacts", "greenfield-sddc.json"))
	validateSddcSpecFirst(t, root, sddc)

	inventoryBytes := readFile(t, filepath.Join(root, "fixtures", "estate.json"))
	snapshotBytes := readFile(t, filepath.Join(root, "compatibility", "vcf-9.1.0.0-snapshot.json"))
	inventory, snapshot, err := architecture.DecodeInputs(inventoryBytes, snapshotBytes)
	if err != nil {
		t.Fatalf("decode protected inputs: %v", err)
	}

	planValue := readJSON(t, filepath.Join(root, "artifacts", "migration-plan.json"))
	validateWithSchema(t, filepath.Join(root, "schemas", "migration-plan.schema.json"), planValue)
	planBytes := readFile(t, filepath.Join(root, "artifacts", "migration-plan.json"))
	var plan architecture.MigrationPlan
	if err := json.Unmarshal(planBytes, &plan); err != nil {
		t.Fatalf("decode migration plan: %v", err)
	}

	t.Run("greenfield contract", func(t *testing.T) {
		checkGreenfield(t, sddc, snapshot)
	})
	t.Run("credential placeholders", func(t *testing.T) {
		checkCredentialPlaceholders(t, sddc)
	})
	t.Run("brownfield contract", func(t *testing.T) {
		checkPlan(t, plan, inventory, snapshot)
	})
	t.Run("package and artifacts agree", func(t *testing.T) {
		builtSddc, err := architecture.GreenfieldSddc(snapshot)
		if err != nil {
			t.Fatalf("GreenfieldSddc() error = %v", err)
		}
		if !reflect.DeepEqual(normalizeJSON(t, builtSddc), sddc) {
			t.Fatal("GreenfieldSddc output is not semantically identical to artifacts/greenfield-sddc.json")
		}
		builtPlan, err := architecture.BrownfieldPlan(inventory, snapshot)
		if err != nil {
			t.Fatalf("BrownfieldPlan() error = %v", err)
		}
		if !reflect.DeepEqual(builtPlan, plan) {
			t.Fatal("BrownfieldPlan output is not semantically identical to artifacts/migration-plan.json")
		}
	})
}

func checkCredentialPlaceholders(t *testing.T, value any) {
	t.Helper()
	credentialCount := 0
	var walk func(any)
	walk = func(current any) {
		switch current := current.(type) {
		case map[string]any:
			for key, child := range current {
				if strings.Contains(strings.ToLower(key), "password") {
					credentialCount++
					if child != "REPLACE_AT_DEPLOY!" {
						t.Errorf("credential field %q must use the literal non-secret deployment placeholder", key)
					}
				}
				walk(child)
			}
		case []any:
			for _, child := range current {
				walk(child)
			}
		}
	}
	walk(value)
	if credentialCount == 0 {
		t.Fatal("greenfield SddcSpec contains no explicit credential placeholder")
	}
}

func validateSddcSpecFirst(t *testing.T, root string, value any) {
	t.Helper()
	openapi := readJSON(t, filepath.Join(root, "specifications", "vcf-installer", "vcf-installer-openapi.json"))
	doc, ok := openapi.(map[string]any)
	if !ok {
		t.Fatal("installer OpenAPI document is not an object")
	}
	components, ok := doc["components"].(map[string]any)
	if !ok {
		t.Fatal("installer OpenAPI document has no components object")
	}
	wrapper := map[string]any{
		"$schema":    "http://json-schema.org/draft-07/schema#",
		"$ref":       "#/components/schemas/SddcSpec",
		"components": components,
	}
	compiler := jsonschema.NewCompiler()
	compiler.DefaultDraft(jsonschema.Draft7)
	if err := compiler.AddResource("installer-sddc.schema.json", wrapper); err != nil {
		t.Fatalf("load installer SddcSpec schema: %v", err)
	}
	schema, err := compiler.Compile("installer-sddc.schema.json")
	if err != nil {
		t.Fatalf("compile installer SddcSpec schema: %v", err)
	}
	if err := schema.Validate(value); err != nil {
		t.Fatalf("artifacts/greenfield-sddc.json does not validate as installer SddcSpec: %v", err)
	}
}

func validateWithSchema(t *testing.T, schemaPath string, value any) {
	t.Helper()
	schemaDoc := readJSON(t, schemaPath)
	compiler := jsonschema.NewCompiler()
	if err := compiler.AddResource("migration-plan.schema.json", schemaDoc); err != nil {
		t.Fatalf("load migration schema: %v", err)
	}
	schema, err := compiler.Compile("migration-plan.schema.json")
	if err != nil {
		t.Fatalf("compile migration schema: %v", err)
	}
	if err := schema.Validate(value); err != nil {
		t.Fatalf("migration plan schema validation failed: %v", err)
	}
}

func checkGreenfield(t *testing.T, value any, snapshot architecture.Snapshot) {
	t.Helper()
	doc := value.(map[string]any)
	want := snapshot.Greenfield
	checkString(t, doc, "sddcId", want.SddcID)
	checkString(t, doc, "workflowType", want.WorkflowType)
	checkString(t, doc, "version", snapshot.TargetRelease)
	checkString(t, doc, "vcfInstanceName", snapshot.Fleet.PrimaryInstance)
	for _, section := range want.RequiredSections {
		if _, ok := doc[section].(map[string]any); !ok {
			t.Errorf("greenfield SddcSpec section %q is missing or not an object", section)
		}
	}

	hosts, ok := doc["hostSpecs"].([]any)
	if !ok || len(hosts) != want.HostCount {
		t.Errorf("hostSpecs count = %d, want %d", len(hosts), want.HostCount)
	}
	seenHosts := map[string]bool{}
	for _, item := range hosts {
		host, ok := item.(map[string]any)
		if !ok {
			t.Error("hostSpecs contains a non-object entry")
			continue
		}
		hostname, _ := host["hostname"].(string)
		if strings.TrimSpace(hostname) == "" {
			t.Error("hostSpecs contains a blank hostname")
		} else if seenHosts[hostname] {
			t.Errorf("hostSpecs repeats hostname %q", hostname)
		}
		seenHosts[hostname] = true
	}
	networks, ok := doc["networkSpecs"].([]any)
	if !ok {
		t.Fatal("networkSpecs is not an array")
	}
	gotNetworks := map[string]bool{}
	for _, item := range networks {
		if obj, ok := item.(map[string]any); ok {
			if networkType, ok := obj["networkType"].(string); ok {
				gotNetworks[networkType] = true
			}
		}
	}
	for _, networkType := range want.RequiredNetworkTypes {
		if !gotNetworks[networkType] {
			t.Errorf("required network type %q is absent", networkType)
		}
	}

	operations := objectAt(t, doc, "vcfOperationsSpec")
	checkString(t, operations, "applianceSize", want.Operations.Size)
	nodes, ok := operations["nodes"].([]any)
	if !ok || len(nodes) != want.Operations.NodeCount {
		t.Errorf("VCF Operations node count = %d, want %d", len(nodes), want.Operations.NodeCount)
	}
	if want.Operations.DeploymentModel == "high-availability" {
		checkOperationsHARoles(t, nodes)
	}
	automation := objectAt(t, doc, "vcfAutomationSpec")
	checkString(t, automation, "size", want.Automation.Size)
	if datastore := objectAt(t, doc, "datastoreSpec"); datastore["vsanSpec"] == nil {
		t.Error("datastoreSpec must contain vsanSpec")
	}
	for _, section := range []string{"vcenterSpec", "nsxtSpec", "sddcManagerSpec", "vcfOperationsSpec", "vcfAutomationSpec"} {
		deployment := objectAt(t, doc, section)
		if _, present := deployment["version"]; present {
			checkString(t, deployment, "version", snapshot.TargetRelease)
		}
		if existing, present := deployment["useExistingDeployment"]; present {
			if enabled, ok := existing.(bool); !ok || enabled {
				t.Errorf("%s must not import an existing deployment", section)
			}
		}
	}
}

func checkOperationsHARoles(t *testing.T, nodes []any) {
	t.Helper()
	wantRoles := map[string]int{"master": 1, "replica": 1, "data": len(nodes) - 2}
	gotRoles := map[string]int{}
	seenHosts := map[string]bool{}
	for _, item := range nodes {
		node, ok := item.(map[string]any)
		if !ok {
			t.Error("VCF Operations nodes contains a non-object entry")
			continue
		}
		hostname, _ := node["hostname"].(string)
		if strings.TrimSpace(hostname) == "" {
			t.Error("VCF Operations node has a blank hostname")
		} else if seenHosts[hostname] {
			t.Errorf("VCF Operations repeats node hostname %q", hostname)
		}
		seenHosts[hostname] = true
		role, _ := node["type"].(string)
		gotRoles[role]++
	}
	if !reflect.DeepEqual(gotRoles, wantRoles) {
		t.Errorf("VCF Operations HA node roles = %v, want %v", gotRoles, wantRoles)
	}
}

func checkPlan(t *testing.T, plan architecture.MigrationPlan, inventory architecture.Inventory, snapshot architecture.Snapshot) {
	t.Helper()
	if plan.SchemaVersion != "1.0" {
		t.Errorf("schemaVersion = %q, want 1.0", plan.SchemaVersion)
	}
	wantFleet := architecture.TargetFleet{
		Name: snapshot.Fleet.Name, Release: snapshot.TargetRelease,
		PrimaryInstance: snapshot.Fleet.PrimaryInstance, ManagementDomain: snapshot.Fleet.ManagementDomain,
	}
	if plan.TargetFleet != wantFleet {
		t.Errorf("targetFleet = %+v, want %+v", plan.TargetFleet, wantFleet)
	}

	domainRules := map[string]architecture.DomainRule{}
	for _, rule := range snapshot.Domains {
		domainRules[rule.ID] = rule
	}
	wantDomainByComponent := map[string]string{}
	for _, component := range inventory.Components {
		domain, ok := snapshot.ScopeTargets[component.Scope]
		if !ok {
			t.Errorf("snapshot has no target domain for scope %q", component.Scope)
		}
		wantDomainByComponent[component.ID] = domain
	}
	checkDomains(t, plan.Domains, inventory.Components, snapshot.Domains, wantDomainByComponent)
	checkServices(t, plan.Services, snapshot.ServiceSizing)
	checkSteps(t, plan.Steps, inventory.Components, snapshot, wantDomainByComponent, domainRules)
}

func checkDomains(t *testing.T, got []architecture.Domain, components []architecture.Component, rules []architecture.DomainRule, wantDomain map[string]string) {
	t.Helper()
	byID := map[string]architecture.Domain{}
	seenComponents := map[string]int{}
	for _, domain := range got {
		if _, duplicate := byID[domain.ID]; duplicate {
			t.Errorf("domain %q appears more than once", domain.ID)
		}
		byID[domain.ID] = domain
		for _, id := range domain.ComponentIDs {
			seenComponents[id]++
			if wantDomain[id] != domain.ID {
				t.Errorf("component %q placed in domain %q, want %q", id, domain.ID, wantDomain[id])
			}
		}
	}
	for _, rule := range rules {
		domain, ok := byID[rule.ID]
		if !ok {
			t.Errorf("required domain %q is absent", rule.ID)
			continue
		}
		if domain.Kind != rule.Kind || domain.Site != rule.Site {
			t.Errorf("domain %q kind/site = %s/%s, want %s/%s", rule.ID, domain.Kind, domain.Site, rule.Kind, rule.Site)
		}
	}
	if len(byID) != len(rules) {
		t.Errorf("domain count = %d, want %d pinned domains", len(byID), len(rules))
	}
	for _, component := range components {
		if seenComponents[component.ID] != 1 {
			t.Errorf("component %q occurs in %d domains, want exactly one", component.ID, seenComponents[component.ID])
		}
	}
}

func checkServices(t *testing.T, got, want []architecture.ServicePlacement) {
	t.Helper()
	gotByName := map[string]architecture.ServicePlacement{}
	for _, service := range got {
		if _, duplicate := gotByName[service.Service]; duplicate {
			t.Errorf("service placement %q appears more than once", service.Service)
		}
		gotByName[service.Service] = service
	}
	if len(gotByName) != len(want) {
		t.Errorf("service placement count = %d, want %d", len(gotByName), len(want))
	}
	for _, expected := range want {
		if actual, ok := gotByName[expected.Service]; !ok {
			t.Errorf("service placement %q is absent", expected.Service)
		} else if actual != expected {
			t.Errorf("service placement %q = %+v, want %+v", expected.Service, actual, expected)
		}
	}
}

func checkSteps(t *testing.T, steps []architecture.MigrationStep, components []architecture.Component, snapshot architecture.Snapshot, wantDomain map[string]string, domainRules map[string]architecture.DomainRule) {
	t.Helper()
	componentByID := map[string]architecture.Component{}
	for _, component := range components {
		componentByID[component.ID] = component
	}
	plannedByID := map[string]architecture.PlannedComponent{}
	for _, component := range snapshot.PlannedComponents {
		plannedByID[component.ID] = component
	}

	seen := map[string]int{}
	lastSequence, lastPhase := 0, 0
	for _, step := range steps {
		if step.Sequence <= lastSequence {
			t.Errorf("step sequence %d is not strictly greater than %d", step.Sequence, lastSequence)
		}
		lastSequence = step.Sequence
		seen[step.ComponentID]++

		phase := 0
		if component, ok := componentByID[step.ComponentID]; ok {
			rule, found := findPath(snapshot.Paths, component)
			if !found {
				t.Errorf("no pinned path for inventory component %q", component.ID)
				continue
			}
			phase = rule.Phase
			if step.ComponentType != component.Type || step.FromVersion != component.Version || step.ToVersion != rule.To || step.Action != rule.Action {
				t.Errorf("step %q type/from/to/action = %s/%s/%s/%s, want %s/%s/%s/%s", step.ComponentID, step.ComponentType, step.FromVersion, step.ToVersion, step.Action, component.Type, component.Version, rule.To, rule.Action)
			}
			checkTarget(t, step, snapshot, wantDomain[component.ID], domainRules)
			checkGates(t, step, rule.RequiredGates)
		} else if planned, ok := plannedByID[step.ComponentID]; ok {
			phase = planned.Phase
			if step.ComponentType != planned.Type || step.FromVersion != planned.FromVersion || step.ToVersion != planned.ToVersion || step.Action != planned.Action {
				t.Errorf("planned step %q does not match pinned component", step.ComponentID)
			}
			domain := snapshot.ScopeTargets[planned.Scope]
			checkTarget(t, step, snapshot, domain, domainRules)
			checkGates(t, step, planned.RequiredGates)
		} else {
			t.Errorf("step %q is not an inventory or pinned planned component", step.ComponentID)
			continue
		}
		if phase < lastPhase {
			t.Errorf("step %q phase %d occurs after phase %d", step.ComponentID, phase, lastPhase)
		}
		lastPhase = phase
	}
	for _, component := range components {
		if seen[component.ID] != 1 {
			t.Errorf("inventory component %q has %d migration steps, want exactly one", component.ID, seen[component.ID])
		}
	}
	for _, component := range snapshot.PlannedComponents {
		if seen[component.ID] != 1 {
			t.Errorf("planned component %q has %d migration steps, want exactly one", component.ID, seen[component.ID])
		}
	}
}

func findPath(paths []architecture.PathRule, component architecture.Component) (architecture.PathRule, bool) {
	for _, path := range paths {
		if path.Type == component.Type && path.From == component.Version && path.Scope == component.Scope {
			return path, true
		}
	}
	return architecture.PathRule{}, false
}

func checkTarget(t *testing.T, step architecture.MigrationStep, snapshot architecture.Snapshot, domain string, rules map[string]architecture.DomainRule) {
	t.Helper()
	rule, ok := rules[domain]
	if !ok {
		t.Errorf("step %q targets unpinned domain %q", step.ComponentID, domain)
		return
	}
	want := architecture.StepTarget{Fleet: snapshot.Fleet.Name, Instance: rule.Instance, Domain: domain}
	if step.Target != want {
		t.Errorf("step %q target = %+v, want %+v", step.ComponentID, step.Target, want)
	}
}

func checkGates(t *testing.T, step architecture.MigrationStep, required []string) {
	t.Helper()
	got := map[string]bool{}
	for _, gate := range step.Gates {
		if got[gate.ID] {
			t.Errorf("step %q repeats gate %q", step.ComponentID, gate.ID)
		}
		if strings.TrimSpace(gate.Condition) == "" {
			t.Errorf("step %q gate %q has no technical condition", step.ComponentID, gate.ID)
		}
		got[gate.ID] = true
	}
	for _, id := range required {
		if !got[id] {
			t.Errorf("step %q lacks required gate %q", step.ComponentID, id)
		}
	}
}

func objectAt(t *testing.T, parent map[string]any, key string) map[string]any {
	t.Helper()
	value, ok := parent[key].(map[string]any)
	if !ok {
		t.Fatalf("%s is not an object", key)
	}
	return value
}

func checkString(t *testing.T, parent map[string]any, key, want string) {
	t.Helper()
	if got, _ := parent[key].(string); got != want {
		t.Errorf("%s = %q, want %q", key, got, want)
	}
}

func readJSON(t *testing.T, path string) any {
	t.Helper()
	data := readFile(t, path)
	var value any
	if err := json.Unmarshal(data, &value); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
	return value
}

func normalizeJSON(t *testing.T, value any) any {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("normalize JSON: %v", err)
	}
	var normalized any
	if err := json.Unmarshal(data, &normalized); err != nil {
		t.Fatalf("normalize JSON: %v", err)
	}
	return normalized
}

func readFile(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return data
}
