package vcfarch

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"
)

const installerSchemaSHA256 = "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"

var readOnlyFixtureSHA256 = map[string]string{
	"migration-plan.schema.json":                              "8d25be10f448379292e03bcd37164a79d821c2785509d7a9153d801e65b03e94",
	"schema_test.go":                                          "e3c881cba3bcabea275911eea4058e712980df2b55fa9c4cb3d5e6016a4c882e",
	"specifications/LICENSE":                                  "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
	"testdata/compatibility-snapshot.json":                    "e320caaeeb320d8a46c1ff7ef179f1d82eba8f7a92a2574202476a5632dbb0ce",
	"testdata/estate.json":                                    "72c88ab1e9854c971009eaa52956e54deb5489a1d5a5126551c28205743d999e",
	"specifications/vcf-installer/vcf-installer-openapi.json": installerSchemaSHA256,
}

func TestSuppliedInputsAreUnmodified(t *testing.T) {
	for path, want := range readOnlyFixtureSHA256 {
		digest := sha256.Sum256(mustRead(t, path))
		if got := hex.EncodeToString(digest[:]); got != want {
			t.Errorf("read-only file %s digest = %s, want %s", path, got, want)
		}
	}
}

func TestResearchArtifact(t *testing.T) {
	research := string(mustRead(t, "research.md"))
	lower := strings.ToLower(research)

	urlPattern := regexp.MustCompile(`https://[^\s\])}>|]+`)
	uniqueURLs := map[string]bool{}
	for _, raw := range urlPattern.FindAllString(research, -1) {
		parsed, err := url.Parse(strings.TrimRight(raw, ".,;"))
		if err != nil {
			t.Errorf("research URL %q is invalid: %v", raw, err)
			continue
		}
		host := strings.ToLower(parsed.Hostname())
		if host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com") {
			t.Errorf("research URL %q is not a Broadcom-published source", raw)
		}
		uniqueURLs[parsed.String()] = true
	}
	if len(uniqueURLs) < 3 {
		t.Errorf("research.md records %d distinct Broadcom URLs, want at least 3", len(uniqueURLs))
	}

	datePattern := regexp.MustCompile(`(?i)access(?:ed)?(?:\s+on)?\s*[:—-]?\s*(\d{4}-\d{2}-\d{2})`)
	dates := datePattern.FindAllStringSubmatch(research, -1)
	if len(dates) < len(uniqueURLs) {
		t.Errorf("research.md has %d access dates for %d sources", len(dates), len(uniqueURLs))
	}
	for _, match := range dates {
		if _, err := time.Parse("2006-01-02", match[1]); err != nil {
			t.Errorf("invalid research access date %q", match[1])
		}
	}

	for _, required := range []string{
		"compatib", "interop", "release", "upgrade",
		"esx", "vcenter", "vsan", "nsx", "live site recovery", "protection and recovery",
	} {
		if !strings.Contains(lower, required) {
			t.Errorf("research.md does not record research coverage for %q", required)
		}
	}
	if !strings.Contains(lower, "confirmed") && !strings.Contains(lower, "checked") && !strings.Contains(lower, "informed") {
		t.Error("research.md does not say which architecture decisions its sources informed")
	}
}

func TestSubmissionIncludesTableDrivenUnitTest(t *testing.T) {
	paths, err := filepath.Glob("*_test.go")
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range paths {
		if path == "schema_test.go" || path == "verifier_test.go" {
			continue
		}
		parsed, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
		if err != nil {
			continue
		}
		for _, declaration := range parsed.Decls {
			function, ok := declaration.(*ast.FuncDecl)
			if !ok || function.Body == nil || !strings.HasPrefix(function.Name.Name, "Test") {
				continue
			}
			callsBuilder := false
			hasSubtest := false
			hasRange := false
			ast.Inspect(function.Body, func(node ast.Node) bool {
				switch typed := node.(type) {
				case *ast.RangeStmt:
					hasRange = true
				case *ast.CallExpr:
					switch called := typed.Fun.(type) {
					case *ast.Ident:
						callsBuilder = callsBuilder || called.Name == "BuildArchitecture"
					case *ast.SelectorExpr:
						hasSubtest = hasSubtest || called.Sel.Name == "Run"
					}
				}
				return true
			})
			if callsBuilder && hasSubtest && hasRange {
				return
			}
		}
	}
	t.Fatal("add a table-driven unit test that exercises BuildArchitecture with subtests")
}

// TestArchitectureContract is deliberately sequential. The SddcSpec is
// validated against the pinned installer's own schema before migration,
// topology, compatibility, or generated-output assertions are evaluated.
func TestArchitectureContract(t *testing.T) {
	installerSchemaBytes := mustRead(t, "specifications/vcf-installer/vcf-installer-openapi.json")
	digest := sha256.Sum256(installerSchemaBytes)
	if actual := hex.EncodeToString(digest[:]); actual != installerSchemaSHA256 {
		t.Fatalf("installer specification digest = %s, want %s", actual, installerSchemaSHA256)
	}
	installerSchema := decodeJSONValue(t, installerSchemaBytes)

	artifactBytes := mustRead(t, "architecture.json")
	artifactValue := decodeJSONValue(t, artifactBytes)
	artifactObject, ok := artifactValue.(map[string]any)
	if !ok {
		t.Fatal("architecture.json must contain a JSON object")
	}
	greenfield, ok := artifactObject["greenfieldSddcSpec"]
	if !ok {
		t.Fatal("architecture.json is missing greenfieldSddcSpec")
	}
	sddcSchema, err := resolveLocalRef(installerSchema, "#/components/schemas/SddcSpec")
	if err != nil {
		t.Fatal(err)
	}
	if err := validateJSONSchema(installerSchema, sddcSchema, greenfield, "$.greenfieldSddcSpec"); err != nil {
		t.Fatalf("greenfieldSddcSpec does not validate against VCF Installer 9.1 SddcSpec: %v", err)
	}

	// No architecture concern is checked before the installer-schema validation above.
	migrationSchema := decodeJSONValue(t, mustRead(t, "migration-plan.schema.json"))
	migration, ok := artifactObject["migrationPlan"]
	if !ok {
		t.Fatal("architecture.json is missing migrationPlan")
	}
	if err := validateJSONSchema(migrationSchema, migrationSchema, migration, "$.migrationPlan"); err != nil {
		t.Fatalf("migrationPlan does not validate against migration-plan.schema.json: %v", err)
	}

	var artifact Architecture
	decodeJSONInto(t, artifactBytes, &artifact)
	var inventory Inventory
	decodeJSONInto(t, mustRead(t, "testdata/estate.json"), &inventory)
	var snapshot CompatibilitySnapshot
	decodeJSONInto(t, mustRead(t, "testdata/compatibility-snapshot.json"), &snapshot)

	if err := verifyTopology(artifact, inventory); err != nil {
		t.Fatalf("topology: %v", err)
	}
	if err := verifyMigration(artifact.MigrationPlan, inventory, snapshot); err != nil {
		t.Fatalf("migration plan: %v", err)
	}

	generated, err := BuildArchitecture(inventory, snapshot)
	if err != nil {
		t.Fatalf("BuildArchitecture: %v", err)
	}
	generatedJSON, err := json.MarshalIndent(generated, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	generatedValue := decodeJSONValue(t, generatedJSON)
	if !jsonValuesEqual(generatedValue, artifactValue) {
		t.Fatalf("architecture.json is not semantically identical to BuildArchitecture output\ngenerated:\n%s", generatedJSON)
	}
}

func verifyTopology(architecture Architecture, inventory Inventory) error {
	domain := architecture.Topology.ManagementDomain
	if !domain.Stretched {
		return fmt.Errorf("management domain is not stretched")
	}
	sddcID, _ := architecture.GreenfieldSddcSpec["sddcId"].(string)
	if domain.Name == "" || domain.Name != sddcID {
		return fmt.Errorf("management domain name %q must equal SddcSpec sddcId %q", domain.Name, sddcID)
	}
	version, _ := architecture.GreenfieldSddcSpec["version"].(string)
	if version != inventory.TargetVCFVersion {
		return fmt.Errorf("SddcSpec version %q, want %q", version, inventory.TargetVCFVersion)
	}

	dataSites := map[string]Site{}
	var witnessSite *Site
	for i := range inventory.Sites {
		site := inventory.Sites[i]
		switch site.Role {
		case "data":
			dataSites[site.ID] = site
		case "witness":
			if witnessSite != nil {
				return fmt.Errorf("inventory has more than one witness site")
			}
			witnessSite = &site
		}
	}
	if len(dataSites) != 2 || len(domain.DataSites) != 2 {
		return fmt.Errorf("need exactly two inventory and topology data sites")
	}
	if witnessSite == nil {
		return fmt.Errorf("inventory has no witness site")
	}

	seenHosts := map[string]bool{}
	for _, actual := range domain.DataSites {
		expected, exists := dataSites[actual.SiteID]
		if !exists {
			return fmt.Errorf("unknown data site %q", actual.SiteID)
		}
		if actual.FailureDomain != expected.FailureDomain {
			return fmt.Errorf("site %s failure domain %q, want %q", actual.SiteID, actual.FailureDomain, expected.FailureDomain)
		}
		if !equalStringSets(actual.Hosts, expected.ManagementHosts) {
			return fmt.Errorf("site %s host set does not match inventory", actual.SiteID)
		}
		for _, host := range actual.Hosts {
			if seenHosts[host] {
				return fmt.Errorf("host %s is assigned to both data sites", host)
			}
			seenHosts[host] = true
		}
	}

	witness := domain.Witness
	if witness.SiteID != witnessSite.ID || witness.FailureDomain != witnessSite.FailureDomain || witness.Appliance != witnessSite.WitnessAppliance {
		return fmt.Errorf("witness placement does not match independent inventory site")
	}
	if !witness.IndependentFailureDomain {
		return fmt.Errorf("witness must be marked as an independent failure domain")
	}
	if _, dataSite := dataSites[witness.SiteID]; dataSite {
		return fmt.Errorf("witness is placed in a data site")
	}
	for _, site := range dataSites {
		if site.FailureDomain == witness.FailureDomain {
			return fmt.Errorf("witness shares data-site failure domain %s", site.FailureDomain)
		}
	}
	if seenHosts[witness.Appliance] {
		return fmt.Errorf("witness is a management-domain data host")
	}

	hostSpecs, ok := architecture.GreenfieldSddcSpec["hostSpecs"].([]any)
	if !ok {
		return fmt.Errorf("SddcSpec hostSpecs is missing or not an array")
	}
	specHosts := make([]string, 0, len(hostSpecs))
	for _, item := range hostSpecs {
		host, ok := item.(map[string]any)
		if !ok {
			return fmt.Errorf("SddcSpec hostSpecs contains a non-object")
		}
		hostname, _ := host["hostname"].(string)
		specHosts = append(specHosts, hostname)
	}
	expectedHosts := make([]string, 0, len(seenHosts))
	for host := range seenHosts {
		expectedHosts = append(expectedHosts, host)
	}
	if !equalStringSets(specHosts, expectedHosts) {
		return fmt.Errorf("SddcSpec hostSpecs must be exactly the two data sites' management hosts")
	}
	if stringSliceContains(specHosts, witness.Appliance) {
		return fmt.Errorf("witness appliance appears in SddcSpec hostSpecs")
	}
	return nil
}

func verifyMigration(plan MigrationPlan, inventory Inventory, snapshot CompatibilitySnapshot) error {
	var errs []error
	if plan.EstateID != inventory.EstateID {
		errs = append(errs, fmt.Errorf("estateId %q, want %q", plan.EstateID, inventory.EstateID))
	}
	if plan.TargetVCFVersion != inventory.TargetVCFVersion || plan.TargetVCFVersion != snapshot.TargetVCFVersion {
		errs = append(errs, fmt.Errorf("targetVcfVersion is inconsistent with inputs"))
	}
	if len(plan.Steps) != len(inventory.Components) {
		errs = append(errs, fmt.Errorf("got %d steps for %d components", len(plan.Steps), len(inventory.Components)))
	}

	components := map[string]EstateComponent{}
	for _, component := range inventory.Components {
		components[component.ID] = component
	}
	rules := map[string]ProductRule{}
	for _, rule := range snapshot.ProductRules {
		rules[rule.ComponentType] = rule
	}
	gates := map[string]GateRule{}
	for _, gate := range snapshot.Gates {
		gates[gate.ID] = gate
	}
	stepByComponent := map[string]MigrationStep{}

	for index, step := range plan.Steps {
		if step.Order != index+1 {
			errs = append(errs, fmt.Errorf("step index %d has order %d, want %d", index, step.Order, index+1))
		}
		component, exists := components[step.ComponentID]
		if !exists {
			errs = append(errs, fmt.Errorf("step %d names unknown component %q", step.Order, step.ComponentID))
			continue
		}
		if _, duplicate := stepByComponent[step.ComponentID]; duplicate {
			errs = append(errs, fmt.Errorf("component %s appears more than once", step.ComponentID))
		}
		stepByComponent[step.ComponentID] = step
		if step.ComponentType != component.Type || step.Site != component.Site || step.FromVersion != component.Version {
			errs = append(errs, fmt.Errorf("step for %s does not preserve type, site, and current version", component.ID))
		}
		rule, exists := rules[component.Type]
		if !exists {
			errs = append(errs, fmt.Errorf("no product rule for %s", component.Type))
			continue
		}
		if !stringSliceContains(rule.AllowedFromVersions, component.Version) {
			errs = append(errs, fmt.Errorf("%s version %s has no allowed path", component.ID, component.Version))
		}
		if step.TargetProduct != rule.TargetProduct || step.TargetVersion != rule.TargetVersion {
			errs = append(errs, fmt.Errorf("%s target %s %s, want %s %s", component.ID, step.TargetProduct, step.TargetVersion, rule.TargetProduct, rule.TargetVersion))
		}
		if !stringSliceContains(rule.AllowedActions, step.Action) {
			errs = append(errs, fmt.Errorf("%s action %q is not allowed", component.ID, step.Action))
		}
		if !equalStringSets(step.Gates, rule.RequiredGateIDs) {
			errs = append(errs, fmt.Errorf("%s gates %v, want exactly %v", component.ID, step.Gates, rule.RequiredGateIDs))
		}
		for _, gateID := range step.Gates {
			gate, exists := gates[gateID]
			if !exists {
				errs = append(errs, fmt.Errorf("%s names unknown gate %s", component.ID, gateID))
				continue
			}
			if !stringSliceContains(gate.AppliesToTypes, component.Type) {
				errs = append(errs, fmt.Errorf("gate %s does not apply to %s", gateID, component.Type))
			}
		}
	}
	for componentID := range components {
		if _, exists := stepByComponent[componentID]; !exists {
			errs = append(errs, fmt.Errorf("component %s has no migration step", componentID))
		}
	}

	for _, step := range plan.Steps {
		for _, gateID := range step.Gates {
			gate, exists := gates[gateID]
			if !exists {
				continue
			}
			for _, predecessor := range inventory.Components {
				if !stringSliceContains(gate.PredecessorTypes, predecessor.Type) {
					continue
				}
				if gate.Scope == "same-site" && predecessor.Site != step.Site {
					continue
				}
				predecessorStep, exists := stepByComponent[predecessor.ID]
				if !exists || predecessorStep.Order >= step.Order {
					errs = append(errs, fmt.Errorf("%s gate %s requires predecessor %s", step.ComponentID, gateID, predecessor.ID))
				}
			}
		}
	}
	return joinedErrors(errs)
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func decodeJSONValue(t *testing.T, data []byte) any {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		t.Fatal(err)
	}
	return value
}

func decodeJSONInto(t *testing.T, data []byte, destination any) {
	t.Helper()
	if err := json.Unmarshal(data, destination); err != nil {
		t.Fatal(err)
	}
}

func equalStringSets(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	leftCopy := append([]string(nil), left...)
	rightCopy := append([]string(nil), right...)
	sort.Strings(leftCopy)
	sort.Strings(rightCopy)
	return reflect.DeepEqual(leftCopy, rightCopy)
}

func stringSliceContains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}
