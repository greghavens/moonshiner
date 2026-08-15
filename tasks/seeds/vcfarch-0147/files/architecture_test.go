package architecture

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"

	"vcfarchitecture/internal/verify"
)

const (
	artifactPath  = "architecture.json"
	researchPath  = "research.md"
	installerPath = "specifications/vcf-installer/vcf-installer-openapi.json"
	planSchema    = "schemas/migration-plan.schema.json"
	estatePath    = "fixtures/estate.json"
	snapshotPath  = "fixtures/compatibility-snapshot.json"
)

var isoDate = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)

type estate struct {
	EstateID   string      `json:"estateId"`
	Components []component `json:"components"`
}

type component struct {
	ID      string `json:"id"`
	Kind    string `json:"kind"`
	FQDN    string `json:"fqdn"`
	Version string `json:"version"`
}

type compatibilitySnapshot struct {
	TargetBundle struct {
		Version    string            `json:"version"`
		Components map[string]string `json:"components"`
	} `json:"targetBundle"`
	GreenfieldRequirements struct {
		WorkflowType         string   `json:"workflowType"`
		MinimumHosts         int      `json:"minimumHosts"`
		MinimumNsxManagers   int      `json:"minimumNsxManagers"`
		RequiredNetworkTypes []string `json:"requiredNetworkTypes"`
	} `json:"greenfieldRequirements"`
	ComponentRules map[string]struct {
		TargetKind                string `json:"targetKind"`
		PlanMethod                string `json:"planMethod"`
		DirectTransitionSupported *bool  `json:"directTransitionSupported"`
	} `json:"componentRules"`
}

type migrationPlan struct {
	SchemaVersion string     `json:"schemaVersion"`
	EstateID      string     `json:"estateId"`
	TargetBundle  string     `json:"targetBundle"`
	Strategy      string     `json:"strategy"`
	Steps         []planStep `json:"steps"`
}

type planStep struct {
	Sequence          int      `json:"sequence"`
	ComponentID       string   `json:"componentId"`
	CurrentVersion    string   `json:"currentVersion"`
	TargetComponentID string   `json:"targetComponentId"`
	TargetVersion     string   `json:"targetVersion"`
	Method            string   `json:"method"`
	Gates             []string `json:"gates"`
}

func TestArchitecture(t *testing.T) {
	artifact := decodeFile(t, artifactPath)
	envelope, ok := artifact.(map[string]any)
	if !ok {
		t.Fatalf("%s must contain a JSON object", artifactPath)
	}
	greenfield, exists := envelope["greenfieldSddcSpec"]
	if !exists {
		t.Fatalf("%s: greenfieldSddcSpec is required", artifactPath)
	}

	// This is deliberately the first artifact validation. The candidate SddcSpec
	// is checked against the schema in VMware's pinned installer document before
	// the migration plan, inventory, or compatibility authority is inspected.
	installer := decodeFile(t, installerPath)
	if violations := verify.ValidateRef(installer, "#/components/schemas/SddcSpec", greenfield); len(violations) != 0 {
		t.Fatalf("greenfieldSddcSpec does not satisfy the pinned installer SddcSpec schema:\n%s", formatViolations(violations))
	}

	migration, exists := envelope["migrationPlan"]
	if !exists {
		t.Fatalf("%s: migrationPlan is required", artifactPath)
	}
	migrationSchema := decodeFile(t, planSchema)
	if violations := verify.Validate(migrationSchema, migrationSchema, migration); len(violations) != 0 {
		t.Fatalf("migrationPlan does not satisfy %s:\n%s", planSchema, formatViolations(violations))
	}
	if len(envelope) != 2 {
		t.Fatalf("%s must contain exactly greenfieldSddcSpec and migrationPlan", artifactPath)
	}

	var inventory estate
	decodeInto(t, decodeFile(t, estatePath), &inventory)
	var snapshot compatibilitySnapshot
	decodeInto(t, decodeFile(t, snapshotPath), &snapshot)
	var plan migrationPlan
	decodeInto(t, migration, &plan)
	spec, ok := greenfield.(map[string]any)
	if !ok {
		t.Fatal("greenfieldSddcSpec must be an object")
	}

	t.Run("greenfield target", func(t *testing.T) {
		checks := []struct {
			name string
			got  string
			want string
		}{
			{"bundle version", stringAt(spec, "version"), snapshot.TargetBundle.Version},
			{"workflow type", stringAt(spec, "workflowType"), snapshot.GreenfieldRequirements.WorkflowType},
			{"vCenter version", nestedString(spec, "vcenterSpec", "version"), snapshot.TargetBundle.Components["VCENTER"]},
			{"NSX version", nestedString(spec, "nsxtSpec", "version"), snapshot.TargetBundle.Components["NSX_MANAGER"]},
			{"SDDC Manager version", nestedString(spec, "sddcManagerSpec", "version"), snapshot.TargetBundle.Components["SDDC_MANAGER"]},
		}
		for _, check := range checks {
			check := check
			t.Run(check.name, func(t *testing.T) {
				if check.got != check.want {
					t.Errorf("got %q, want %q", check.got, check.want)
				}
			})
		}
		for _, objectName := range []string{"vcenterSpec", "nsxtSpec", "sddcManagerSpec"} {
			object, _ := spec[objectName].(map[string]any)
			value, present := object["useExistingDeployment"]
			if !present || value != false {
				t.Errorf("%s.useExistingDeployment must be explicitly false for the parallel greenfield target", objectName)
			}
		}
	})

	t.Run("greenfield capacity and networks", func(t *testing.T) {
		hosts, _ := spec["hostSpecs"].([]any)
		if len(hosts) < snapshot.GreenfieldRequirements.MinimumHosts {
			t.Errorf("hostSpecs has %d hosts, need at least %d", len(hosts), snapshot.GreenfieldRequirements.MinimumHosts)
		}
		nsxt, _ := spec["nsxtSpec"].(map[string]any)
		managers, _ := nsxt["nsxtManagers"].([]any)
		if len(managers) < snapshot.GreenfieldRequirements.MinimumNsxManagers {
			t.Errorf("nsxtSpec.nsxtManagers has %d managers, need at least %d", len(managers), snapshot.GreenfieldRequirements.MinimumNsxManagers)
		}
		dvs, _ := spec["dvsSpecs"].([]any)
		if len(dvs) == 0 {
			t.Error("dvsSpecs must describe the greenfield distributed switch")
		}
		networks, _ := spec["networkSpecs"].([]any)
		seen := make(map[string]bool)
		for _, raw := range networks {
			network, _ := raw.(map[string]any)
			seen[stringAt(network, "networkType")] = true
		}
		for _, required := range snapshot.GreenfieldRequirements.RequiredNetworkTypes {
			if !seen[required] {
				t.Errorf("networkSpecs is missing %s", required)
			}
		}
	})

	t.Run("plan identity", func(t *testing.T) {
		checks := []struct {
			name string
			got  string
			want string
		}{
			{"estate", plan.EstateID, inventory.EstateID},
			{"target bundle", plan.TargetBundle, snapshot.TargetBundle.Version},
			{"strategy", plan.Strategy, "parallel-greenfield"},
		}
		for _, check := range checks {
			if check.got != check.want {
				t.Errorf("%s: got %q, want %q", check.name, check.got, check.want)
			}
		}
	})

	t.Run("ordered component coverage", func(t *testing.T) {
		if len(plan.Steps) != len(inventory.Components) {
			t.Fatalf("plan has %d steps; every one of the %d inventory components must appear exactly once", len(plan.Steps), len(inventory.Components))
		}
		inventoryByID := make(map[string]component, len(inventory.Components))
		for _, item := range inventory.Components {
			inventoryByID[item.ID] = item
		}
		allowedTargets := targetIDsByKind(spec)
		legacyNames := make(map[string]bool, len(inventory.Components)*2)
		for _, item := range inventory.Components {
			legacyNames[item.ID] = true
			legacyNames[item.FQDN] = true
		}
		seenSource := make(map[string]bool)
		seenTarget := make(map[string]bool)
		for index, step := range plan.Steps {
			if step.Sequence != index+1 {
				t.Errorf("steps[%d].sequence=%d, want %d", index, step.Sequence, index+1)
			}
			item, exists := inventoryByID[step.ComponentID]
			if !exists {
				t.Errorf("steps[%d] names unknown component %q", index, step.ComponentID)
				continue
			}
			if seenSource[item.ID] {
				t.Errorf("component %q appears more than once", item.ID)
			}
			seenSource[item.ID] = true
			if step.CurrentVersion != item.Version {
				t.Errorf("component %q currentVersion=%q, want inventory version %q", item.ID, step.CurrentVersion, item.Version)
			}
			rule, exists := snapshot.ComponentRules[item.Kind]
			if !exists {
				t.Errorf("compatibility snapshot has no rule for %s", item.Kind)
				continue
			}
			if step.TargetVersion != snapshot.TargetBundle.Components[rule.TargetKind] {
				t.Errorf("component %q targetVersion=%q, want %q", item.ID, step.TargetVersion, snapshot.TargetBundle.Components[rule.TargetKind])
			}
			if step.Method != rule.PlanMethod {
				t.Errorf("component %q method=%q, want %q", item.ID, step.Method, rule.PlanMethod)
			}
			if rule.DirectTransitionSupported != nil && !*rule.DirectTransitionSupported && step.Method != "parallel-replace" {
				t.Errorf("component %q uses %q despite a forbidden direct transition", item.ID, step.Method)
			}
			if !allowedTargets[item.Kind][step.TargetComponentID] {
				t.Errorf("component %q target %q is not named by greenfieldSddcSpec", item.ID, step.TargetComponentID)
			}
			if legacyNames[step.TargetComponentID] {
				t.Errorf("component %q target %q reuses a legacy component name", item.ID, step.TargetComponentID)
			}
			if seenTarget[step.TargetComponentID] {
				t.Errorf("target component %q is assigned more than once", step.TargetComponentID)
			}
			seenTarget[step.TargetComponentID] = true
			for _, gate := range step.Gates {
				if strings.TrimSpace(gate) == "" {
					t.Errorf("component %q has a blank gate", item.ID)
				}
			}
		}
		missing := make([]string, 0)
		for _, item := range inventory.Components {
			if !seenSource[item.ID] {
				missing = append(missing, item.ID)
			}
		}
		sort.Strings(missing)
		if len(missing) != 0 {
			t.Errorf("components missing from plan: %v", missing)
		}
	})
}

func TestResearchSources(t *testing.T) {
	data, err := os.ReadFile(researchPath)
	if err != nil {
		t.Fatalf("read %s: %v", researchPath, err)
	}
	content := string(data)
	if strings.Contains(content, "compatibility-snapshot.json") {
		t.Fatalf("%s must not cite the local compatibility snapshot as research", researchPath)
	}

	rows := markdownTableRows(content)
	if len(rows) < 2 {
		t.Fatalf("%s must contain an official compatibility/upgrade source and the tagged installer specification", researchPath)
	}

	seenURLs := make(map[string]bool)
	productSources := 0
	foundTaggedSpec := false
	var researchedFacts strings.Builder
	for index, row := range rows {
		for column, value := range row {
			if strings.TrimSpace(value) == "" {
				t.Errorf("research source row %d has a blank %s", index+1, []string{"Title", "Publisher", "URL", "Access date", "Fact used"}[column])
			}
		}
		if len(strings.TrimSpace(row[4])) < 20 {
			t.Errorf("research source row %d must state a substantive fact used", index+1)
		}
		researchedFacts.WriteString(row[4])
		researchedFacts.WriteByte('\n')
		if !isoDate.MatchString(row[3]) {
			t.Errorf("research source row %d access date %q must use YYYY-MM-DD", index+1, row[3])
		} else if _, err := time.Parse("2006-01-02", row[3]); err != nil {
			t.Errorf("research source row %d has invalid access date %q", index+1, row[3])
		}

		parsed, err := url.ParseRequestURI(row[2])
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil {
			t.Errorf("research source row %d URL %q must be an absolute credential-free HTTPS URL", index+1, row[2])
			continue
		}
		if seenURLs[parsed.String()] {
			t.Errorf("research source row %d duplicates URL %q", index+1, parsed.String())
		}
		seenURLs[parsed.String()] = true

		host := strings.ToLower(parsed.Hostname())
		switch {
		case host == "broadcom.com" || strings.HasSuffix(host, ".broadcom.com") || host == "vmware.com" || strings.HasSuffix(host, ".vmware.com"):
			productSources++
		case isTaggedInstallerSpecURL(parsed):
			foundTaggedSpec = true
		default:
			t.Errorf("research source row %d URL %q is not an official Broadcom/VMware source or the tagged vmware/vcf-api-specs installer specification", index+1, row[2])
		}
	}
	if productSources < 1 {
		t.Errorf("%s must record an official Broadcom/VMware compatibility or upgrade source", researchPath)
	}
	if !foundTaggedSpec {
		t.Errorf("%s must record the vmware/vcf-api-specs installer specification at tag 9.1.0.0", researchPath)
	}

	requiredVersions := make(map[string]bool)
	var inventory estate
	decodeInto(t, decodeFile(t, estatePath), &inventory)
	for _, item := range inventory.Components {
		requiredVersions[item.Version] = true
	}
	var snapshot compatibilitySnapshot
	decodeInto(t, decodeFile(t, snapshotPath), &snapshot)
	requiredVersions[snapshot.TargetBundle.Version] = true
	for _, version := range snapshot.TargetBundle.Components {
		requiredVersions[version] = true
	}
	facts := researchedFacts.String()
	for version := range requiredVersions {
		if !strings.Contains(facts, version) {
			t.Errorf("%s does not document researched inventory/target version %q", researchPath, version)
		}
	}
}

func isTaggedInstallerSpecURL(parsed *url.URL) bool {
	const installer = "specifications/vcf-installer/vcf-installer-openapi.json"
	host := strings.ToLower(parsed.Hostname())
	return (host == "github.com" && parsed.Path == "/vmware/vcf-api-specs/blob/9.1.0.0/"+installer) ||
		(host == "raw.githubusercontent.com" && parsed.Path == "/vmware/vcf-api-specs/9.1.0.0/"+installer)
}

func markdownTableRows(content string) [][5]string {
	lines := strings.Split(content, "\n")
	for i, line := range lines {
		columns := splitMarkdownRow(line)
		if len(columns) != 5 || columns[0] != "Title" || columns[1] != "Publisher" || columns[2] != "URL" || columns[3] != "Access date" || columns[4] != "Fact used" {
			continue
		}
		if i+1 >= len(lines) || !isMarkdownSeparator(splitMarkdownRow(lines[i+1])) {
			return nil
		}
		var rows [][5]string
		for _, candidate := range lines[i+2:] {
			values := splitMarkdownRow(candidate)
			if len(values) != 5 {
				break
			}
			var row [5]string
			copy(row[:], values)
			rows = append(rows, row)
		}
		return rows
	}
	return nil
}

func splitMarkdownRow(line string) []string {
	line = strings.TrimSpace(line)
	if !strings.HasPrefix(line, "|") || !strings.HasSuffix(line, "|") {
		return nil
	}
	parts := strings.Split(strings.Trim(line, "|"), "|")
	for i := range parts {
		parts[i] = strings.TrimSpace(parts[i])
	}
	return parts
}

func isMarkdownSeparator(columns []string) bool {
	if len(columns) != 5 {
		return false
	}
	for _, column := range columns {
		if strings.Trim(column, ":-") != "" || strings.Count(column, "-") < 3 {
			return false
		}
	}
	return true
}

func decodeFile(t *testing.T, path string) any {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	value, err := verify.Decode(data)
	if err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
	return value
}

func decodeInto(t *testing.T, value any, target any) {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatal(err)
	}
}

func formatViolations(violations []verify.Violation) string {
	lines := make([]string, len(violations))
	for i, violation := range violations {
		lines[i] = "- " + violation.Error()
	}
	return strings.Join(lines, "\n")
}

func stringAt(object map[string]any, key string) string {
	value, _ := object[key].(string)
	return value
}

func nestedString(object map[string]any, objectKey, key string) string {
	nested, _ := object[objectKey].(map[string]any)
	return stringAt(nested, key)
}

func targetIDsByKind(spec map[string]any) map[string]map[string]bool {
	result := map[string]map[string]bool{
		"SDDC_MANAGER": {},
		"VCENTER":      {},
		"NSX_MANAGER":  {},
		"ESXI":         {},
	}
	for kind, id := range map[string]string{
		"SDDC_MANAGER": nestedString(spec, "sddcManagerSpec", "hostname"),
		"VCENTER":      nestedString(spec, "vcenterSpec", "vcenterHostname"),
		"NSX_MANAGER":  nestedString(spec, "nsxtSpec", "vipFqdn"),
	} {
		if id != "" {
			result[kind][id] = true
		}
	}
	hosts, _ := spec["hostSpecs"].([]any)
	for _, raw := range hosts {
		host, _ := raw.(map[string]any)
		if id := stringAt(host, "hostname"); id != "" {
			result["ESXI"][id] = true
		}
	}
	return result
}

func Example_artifactShape() {
	fmt.Println("architecture.json contains greenfieldSddcSpec and migrationPlan")
	// Output: architecture.json contains greenfieldSddcSpec and migrationPlan
}
