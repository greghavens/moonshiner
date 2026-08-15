package grader_tests

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"vcfarch/migrationplan"
)

type document struct {
	SchemaVersion    string           `json:"schema_version"`
	PlanID           string           `json:"plan_id"`
	EstateID         string           `json:"estate_id"`
	SnapshotID       string           `json:"snapshot_id"`
	TargetVCFVersion string           `json:"target_vcf_version"`
	SupportFindings  []supportFinding `json:"support_findings"`
	Placements       []placement      `json:"placements"`
	Steps            []step           `json:"steps"`
}

type supportFinding struct {
	SourceProduct string `json:"source_product"`
	SourceVersion string `json:"source_version"`
	EOGSDate      string `json:"eogs_date"`
	SupportStatus string `json:"support_status"`
	DirectPath    string `json:"direct_path"`
}

type placement struct {
	Component        string   `json:"component"`
	Version          string   `json:"version"`
	DeploymentModel  string   `json:"deployment_model"`
	Site             string   `json:"site"`
	ManagementDomain string   `json:"management_domain"`
	Nodes            []node   `json:"nodes"`
	CapacityBasis    []string `json:"capacity_basis"`
}

type node struct {
	Name          string `json:"name"`
	Role          string `json:"role"`
	FailureDomain string `json:"failure_domain"`
	VCPU          int    `json:"vcpu"`
	MemoryGiB     int    `json:"memory_gib"`
	StorageGiB    int    `json:"storage_gib"`
}

type step struct {
	Order           int      `json:"order"`
	ID              string   `json:"id"`
	SourceProduct   string   `json:"source_product"`
	SourceVersion   string   `json:"source_version"`
	TargetComponent string   `json:"target_component"`
	TargetVersion   string   `json:"target_version"`
	Method          string   `json:"method"`
	Carries         []string `json:"carries"`
	Abandons        []string `json:"abandons"`
	Gates           []string `json:"gates"`
	DependsOn       []string `json:"depends_on"`
}

type inventory struct {
	EstateID         string `json:"estate_id"`
	TargetVCFVersion string `json:"target_vcf_version"`
	ManagementDomain struct {
		Name           string    `json:"name"`
		Site           string    `json:"site"`
		FailureDomains []string  `json:"failure_domains"`
		FreeCapacity   resources `json:"free_capacity"`
		ReservePercent int       `json:"reserve_percent"`
	} `json:"management_domain"`
	Products []struct {
		Product    string `json:"product"`
		Version    string `json:"version"`
		Deployment struct {
			Objects            int `json:"objects"`
			MetricsPerDay      int `json:"metrics_per_day"`
			ManagedDeployments int `json:"managed_deployments"`
			AverageEPS         int `json:"average_eps"`
			PeakEPS            int `json:"peak_eps"`
			RetentionDays      int `json:"retention_days"`
		} `json:"deployment"`
	} `json:"products"`
}

type snapshot struct {
	SnapshotID       string `json:"snapshot_id"`
	TargetVCFVersion string `json:"target_vcf_version"`
	TargetComponents []struct {
		Component string `json:"component"`
		Version   string `json:"version"`
	} `json:"target_components"`
	Lifecycle []struct {
		Product                string `json:"product"`
		VersionFamily          string `json:"version_family"`
		EOGSDate               string `json:"eogs_date"`
		SupportStatusOnCapture string `json:"support_status_on_capture"`
		DirectPathToTarget     string `json:"direct_path_to_target"`
	} `json:"lifecycle"`
	CompatibilityEdges []edge          `json:"compatibility_edges"`
	SizingProfiles     []sizingProfile `json:"sizing_profiles"`
}

type edge struct {
	ID              string   `json:"id"`
	RequiredInPlan  bool     `json:"required_in_plan"`
	Supported       bool     `json:"supported"`
	Order           int      `json:"order"`
	SourceProduct   string   `json:"source_product"`
	SourceVersion   string   `json:"source_version"`
	TargetComponent string   `json:"target_component"`
	TargetVersion   string   `json:"target_version"`
	Method          string   `json:"method"`
	Carries         []string `json:"carries"`
	Abandons        []string `json:"abandons"`
	Gates           []string `json:"gates"`
	DependsOn       []string `json:"depends_on"`
}

type sizingProfile struct {
	Component            string    `json:"component"`
	Version              string    `json:"version"`
	DeploymentModel      string    `json:"deployment_model"`
	SelectionMetric      string    `json:"selection_metric"`
	MaximumSupportedLoad int       `json:"maximum_supported_load"`
	CapacityBasis        []string  `json:"capacity_basis"`
	NodeRoles            []string  `json:"node_roles"`
	PerNodeMinimum       resources `json:"per_node_minimum"`
}

type resources struct {
	VCPU       int `json:"vcpu"`
	MemoryGiB  int `json:"memory_gib"`
	StorageGiB int `json:"storage_gib"`
}

func TestMigrationArchitecture(t *testing.T) {
	root := filepath.Clean("..")

	// Contract validation is intentionally first. No fixture, compatibility
	// authority, package output, or semantic assertion is touched until the
	// submitted artifact has passed the installer's own JSON schema.
	schemaRaw := mustRead(t, filepath.Join(root, "installer", "migration-plan.schema.json"))
	artifactRaw := mustRead(t, filepath.Join(root, "migration-plan.json"))
	schemaValue := decodeAny(t, schemaRaw, "installer schema")
	artifactValue := decodeAny(t, artifactRaw, "migration-plan.json")
	if err := validateJSONSchema(schemaValue, artifactValue, "$"); err != nil {
		t.Fatalf("migration-plan.json fails installer/migration-plan.schema.json: %v", err)
	}

	var got document
	mustDecode(t, artifactRaw, &got, "migration-plan.json")
	var inv inventory
	mustDecode(t, mustRead(t, filepath.Join(root, "fixtures", "estate.json")), &inv, "estate fixture")
	var snap snapshot
	mustDecode(t, mustRead(t, filepath.Join(root, "installer", "compatibility-snapshot.json")), &snap, "compatibility snapshot")

	t.Run("package-builds-the-materialized-artifact", func(t *testing.T) {
		inventoryFile, err := os.Open(filepath.Join(root, "fixtures", "estate.json"))
		if err != nil {
			t.Fatal(err)
		}
		defer inventoryFile.Close()
		snapshotFile, err := os.Open(filepath.Join(root, "installer", "compatibility-snapshot.json"))
		if err != nil {
			t.Fatal(err)
		}
		defer snapshotFile.Close()
		built, err := migrationplan.Build(inventoryFile, snapshotFile)
		if err != nil {
			t.Fatalf("migrationplan.Build returned an error: %v", err)
		}
		builtValue := decodeAny(t, built, "migrationplan.Build output")
		if err := validateJSONSchema(schemaValue, builtValue, "$"); err != nil {
			t.Fatalf("migrationplan.Build output fails installer schema: %v", err)
		}
		if !reflect.DeepEqual(artifactValue, builtValue) {
			t.Fatal("migrationplan.Build output differs from migration-plan.json")
		}
	})

	t.Run("metadata", func(t *testing.T) {
		tests := []struct {
			name string
			got  string
			want string
		}{
			{"schema version", got.SchemaVersion, "1.0.0"},
			{"plan id", got.PlanID, "vcfarch-northstar-vcf9"},
			{"estate id", got.EstateID, inv.EstateID},
			{"snapshot id", got.SnapshotID, snap.SnapshotID},
			{"target VCF version from fixture", got.TargetVCFVersion, inv.TargetVCFVersion},
			{"target VCF version from snapshot", got.TargetVCFVersion, snap.TargetVCFVersion},
		}
		for _, tc := range tests {
			t.Run(tc.name, func(t *testing.T) {
				if tc.got != tc.want {
					t.Errorf("got %q, want %q", tc.got, tc.want)
				}
			})
		}
	})

	t.Run("support-boundaries", func(t *testing.T) {
		if len(got.SupportFindings) != len(inv.Products) {
			t.Fatalf("got %d support findings, want one for each of %d products", len(got.SupportFindings), len(inv.Products))
		}
		findings := make(map[string]supportFinding, len(got.SupportFindings))
		for _, finding := range got.SupportFindings {
			key := finding.SourceProduct + "\x00" + finding.SourceVersion
			if _, exists := findings[key]; exists {
				t.Fatalf("duplicate support finding for %s %s", finding.SourceProduct, finding.SourceVersion)
			}
			findings[key] = finding
		}
		for _, product := range inv.Products {
			finding, ok := findings[product.Product+"\x00"+product.Version]
			if !ok {
				t.Errorf("missing support finding for %s %s", product.Product, product.Version)
				continue
			}
			life, ok := lifecycleFor(snap, product.Product)
			if !ok {
				t.Fatalf("pinned snapshot has no lifecycle row for %s", product.Product)
			}
			tests := []struct {
				name string
				got  string
				want string
			}{
				{"EOGS", finding.EOGSDate, life.EOGSDate},
				{"support status", finding.SupportStatus, life.SupportStatusOnCapture},
				{"direct path", finding.DirectPath, life.DirectPathToTarget},
			}
			for _, tc := range tests {
				if tc.got != tc.want {
					t.Errorf("%s %s: %s got %q, want %q", product.Product, product.Version, tc.name, tc.got, tc.want)
				}
			}
		}
	})

	t.Run("ordered-supported-path", func(t *testing.T) {
		required := make([]edge, 0)
		unsupported := make(map[string]bool)
		for _, candidate := range snap.CompatibilityEdges {
			if candidate.RequiredInPlan {
				if !candidate.Supported {
					t.Fatalf("snapshot marks required edge %s unsupported", candidate.ID)
				}
				required = append(required, candidate)
			}
			if !candidate.Supported {
				unsupported[candidate.ID] = true
			}
		}
		if len(got.Steps) != len(required) {
			t.Fatalf("got %d steps, want %d required supported edges", len(got.Steps), len(required))
		}
		stepsByID := make(map[string]step, len(got.Steps))
		for index, actual := range got.Steps {
			if actual.Order != index+1 {
				t.Errorf("steps[%d].order=%d, want %d", index, actual.Order, index+1)
			}
			if unsupported[actual.ID] {
				t.Errorf("plan includes unsupported edge %s", actual.ID)
			}
			if _, exists := stepsByID[actual.ID]; exists {
				t.Fatalf("duplicate step id %s", actual.ID)
			}
			stepsByID[actual.ID] = actual
		}
		for _, expected := range required {
			actual, ok := stepsByID[expected.ID]
			if !ok {
				t.Errorf("missing required compatibility edge %s", expected.ID)
				continue
			}
			assertStepMatchesEdge(t, actual, expected)
			for _, dependency := range actual.DependsOn {
				prior, ok := stepsByID[dependency]
				if !ok {
					t.Errorf("step %s depends on absent step %s", actual.ID, dependency)
				} else if prior.Order >= actual.Order {
					t.Errorf("step %s depends on non-prior step %s", actual.ID, dependency)
				}
			}
		}
		for _, product := range inv.Products {
			covered := false
			for _, actual := range got.Steps {
				if actual.SourceProduct == product.Product && actual.SourceVersion == product.Version {
					covered = true
					break
				}
			}
			if !covered {
				t.Errorf("initial source %s %s is not named by any migration step", product.Product, product.Version)
			}
		}
	})

	t.Run("placement-and-sizing", func(t *testing.T) {
		if len(got.Placements) != len(snap.TargetComponents) {
			t.Fatalf("got %d placements, want %d final target components", len(got.Placements), len(snap.TargetComponents))
		}
		profiles := make(map[string]sizingProfile, len(snap.SizingProfiles))
		for _, profile := range snap.SizingProfiles {
			profiles[profile.Component] = profile
		}
		placements := make(map[string]placement, len(got.Placements))
		total := resources{}
		allNodeNames := map[string]bool{}
		for _, actual := range got.Placements {
			if _, exists := placements[actual.Component]; exists {
				t.Fatalf("duplicate placement for %s", actual.Component)
			}
			placements[actual.Component] = actual
			profile, ok := profiles[actual.Component]
			if !ok {
				t.Errorf("placement %s has no pinned sizing profile", actual.Component)
				continue
			}
			if actual.Version != profile.Version {
				t.Errorf("%s version=%q, want %q", actual.Component, actual.Version, profile.Version)
			}
			if actual.DeploymentModel != profile.DeploymentModel {
				t.Errorf("%s model=%q, want %q", actual.Component, actual.DeploymentModel, profile.DeploymentModel)
			}
			if actual.Site != inv.ManagementDomain.Site || actual.ManagementDomain != inv.ManagementDomain.Name {
				t.Errorf("%s must be placed in %s/%s", actual.Component, inv.ManagementDomain.Site, inv.ManagementDomain.Name)
			}
			if !sameStrings(actual.CapacityBasis, profile.CapacityBasis) {
				t.Errorf("%s capacity_basis=%v, want %v", actual.Component, actual.CapacityBasis, profile.CapacityBasis)
			}
			if len(actual.Nodes) != len(profile.NodeRoles) {
				t.Errorf("%s has %d nodes, want %d", actual.Component, len(actual.Nodes), len(profile.NodeRoles))
				continue
			}
			roles, domains := make([]string, 0, len(actual.Nodes)), map[string]bool{}
			for _, n := range actual.Nodes {
				roles = append(roles, n.Role)
				if allNodeNames[n.Name] {
					t.Errorf("node name %q is reused", n.Name)
				}
				allNodeNames[n.Name] = true
				if !contains(inv.ManagementDomain.FailureDomains, n.FailureDomain) {
					t.Errorf("%s node %s uses unknown failure domain %q", actual.Component, n.Name, n.FailureDomain)
				}
				domains[n.FailureDomain] = true
				if n.VCPU < profile.PerNodeMinimum.VCPU || n.MemoryGiB < profile.PerNodeMinimum.MemoryGiB || n.StorageGiB < profile.PerNodeMinimum.StorageGiB {
					t.Errorf("%s node %s resources %d/%d/%d are below pinned minimum %d/%d/%d", actual.Component, n.Name, n.VCPU, n.MemoryGiB, n.StorageGiB, profile.PerNodeMinimum.VCPU, profile.PerNodeMinimum.MemoryGiB, profile.PerNodeMinimum.StorageGiB)
				}
				total.VCPU += n.VCPU
				total.MemoryGiB += n.MemoryGiB
				total.StorageGiB += n.StorageGiB
			}
			if !sameStrings(roles, profile.NodeRoles) {
				t.Errorf("%s roles=%v, want %v", actual.Component, roles, profile.NodeRoles)
			}
			if len(domains) != len(actual.Nodes) {
				t.Errorf("%s nodes must occupy distinct failure domains", actual.Component)
			}
			assertProfileCoversFixture(t, profile, inv)
		}
		for _, target := range snap.TargetComponents {
			actual, ok := placements[target.Component]
			if !ok {
				t.Errorf("missing placement for %s", target.Component)
			} else if actual.Version != target.Version {
				t.Errorf("%s placement version=%s, want %s", target.Component, actual.Version, target.Version)
			}
		}
		availablePercent := 100 - inv.ManagementDomain.ReservePercent
		limits := resources{
			VCPU:       inv.ManagementDomain.FreeCapacity.VCPU * availablePercent / 100,
			MemoryGiB:  inv.ManagementDomain.FreeCapacity.MemoryGiB * availablePercent / 100,
			StorageGiB: inv.ManagementDomain.FreeCapacity.StorageGiB * availablePercent / 100,
		}
		checks := []struct {
			name  string
			used  int
			limit int
		}{
			{"vcpu", total.VCPU, limits.VCPU},
			{"memory_gib", total.MemoryGiB, limits.MemoryGiB},
			{"storage_gib", total.StorageGiB, limits.StorageGiB},
		}
		for _, check := range checks {
			if check.used > check.limit {
				t.Errorf("placements consume %d %s, exceeding post-reserve limit %d", check.used, check.name, check.limit)
			}
		}
	})
}

func TestBuildInputValidationAndDeterminism(t *testing.T) {
	root := filepath.Clean("..")
	inventoryRaw := mustRead(t, filepath.Join(root, "fixtures", "estate.json"))
	snapshotRaw := mustRead(t, filepath.Join(root, "installer", "compatibility-snapshot.json"))

	unknownVersion := bytes.Replace(inventoryRaw, []byte(`"version": "8.18.6"`), []byte(`"version": "8.17.0"`), 1)
	targetMismatch := bytes.Replace(inventoryRaw, []byte(`"target_vcf_version": "9.0.2"`), []byte(`"target_vcf_version": "9.1.0"`), 1)
	tests := []struct {
		name      string
		inventory []byte
		snapshot  []byte
	}{
		{name: "malformed inventory", inventory: []byte(`{"estate_id":`), snapshot: snapshotRaw},
		{name: "malformed snapshot", inventory: inventoryRaw, snapshot: []byte(`{"snapshot_id":`)},
		{name: "target versions disagree", inventory: targetMismatch, snapshot: snapshotRaw},
		{name: "inventory version absent from lifecycle and graph", inventory: unknownVersion, snapshot: snapshotRaw},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := migrationplan.Build(bytes.NewReader(tc.inventory), bytes.NewReader(tc.snapshot)); err == nil {
				t.Fatal("migrationplan.Build returned nil error for invalid inputs")
			}
		})
	}

	first, err := migrationplan.Build(bytes.NewReader(inventoryRaw), bytes.NewReader(snapshotRaw))
	if err != nil {
		t.Fatalf("first migrationplan.Build call failed: %v", err)
	}
	second, err := migrationplan.Build(bytes.NewReader(inventoryRaw), bytes.NewReader(snapshotRaw))
	if err != nil {
		t.Fatalf("second migrationplan.Build call failed: %v", err)
	}
	if !bytes.Equal(first, second) {
		t.Fatal("migrationplan.Build is not byte-deterministic for identical inputs")
	}
}

func TestResearchRecord(t *testing.T) {
	research := string(mustRead(t, filepath.Join("..", "research.md")))
	checks := []struct {
		name       string
		expression string
	}{
		{name: "dated consultation record", expression: `\b20[0-9]{2}\b`},
		{name: "source URL", expression: `https?://[^\s>)]+`},
		{name: "Broadcom publisher", expression: `(?i)\bBroadcom\b`},
		{name: "Operations research", expression: `(?i)(?:Aria|VCF|vRealize|VMware)[^\n]{0,20}Operations`},
		{name: "Automation research", expression: `(?i)(?:Automation|\bvRA\b)`},
		{name: "Logs research", expression: `(?i)(?:Logs|Log Insight|\bvRLI\b)`},
	}
	for _, check := range checks {
		t.Run(check.name, func(t *testing.T) {
			if !regexp.MustCompile(check.expression).MatchString(research) {
				t.Errorf("research.md does not contain %s", check.name)
			}
		})
	}
}

func assertStepMatchesEdge(t *testing.T, got step, want edge) {
	t.Helper()
	fields := []struct {
		name string
		got  any
		want any
	}{
		{"order", got.Order, want.Order},
		{"source_product", got.SourceProduct, want.SourceProduct},
		{"source_version", got.SourceVersion, want.SourceVersion},
		{"target_component", got.TargetComponent, want.TargetComponent},
		{"target_version", got.TargetVersion, want.TargetVersion},
		{"method", got.Method, want.Method},
	}
	for _, field := range fields {
		if !reflect.DeepEqual(field.got, field.want) {
			t.Errorf("step %s %s=%v, want %v", got.ID, field.name, field.got, field.want)
		}
	}
	lists := []struct {
		name string
		got  []string
		want []string
	}{
		{"carries", got.Carries, want.Carries},
		{"abandons", got.Abandons, want.Abandons},
		{"gates", got.Gates, want.Gates},
		{"depends_on", got.DependsOn, want.DependsOn},
	}
	for _, list := range lists {
		if !sameStrings(list.got, list.want) {
			t.Errorf("step %s %s=%v, want canonical ids %v", got.ID, list.name, list.got, list.want)
		}
	}
}

func assertProfileCoversFixture(t *testing.T, profile sizingProfile, inv inventory) {
	t.Helper()
	load := -1
	for _, product := range inv.Products {
		switch profile.Component {
		case "VMware Cloud Foundation Operations":
			if product.Product == "VMware Aria Operations" {
				load = product.Deployment.Objects
			}
		case "VMware Cloud Foundation Automation":
			if product.Product == "VMware vRealize Automation" {
				load = product.Deployment.ManagedDeployments
			}
		case "VMware Cloud Foundation Operations for Logs":
			if product.Product == "VMware Aria Operations for Logs" {
				load = product.Deployment.PeakEPS
			}
		}
	}
	if load < 0 {
		t.Fatalf("cannot find fixture load for %s", profile.Component)
	}
	if load > profile.MaximumSupportedLoad {
		t.Errorf("selected profile for %s supports %d %s but fixture requires %d", profile.Component, profile.MaximumSupportedLoad, profile.SelectionMetric, load)
	}
}

func lifecycleFor(snap snapshot, product string) (struct {
	Product                string `json:"product"`
	VersionFamily          string `json:"version_family"`
	EOGSDate               string `json:"eogs_date"`
	SupportStatusOnCapture string `json:"support_status_on_capture"`
	DirectPathToTarget     string `json:"direct_path_to_target"`
}, bool) {
	for _, row := range snap.Lifecycle {
		if row.Product == product {
			return row, true
		}
	}
	return struct {
		Product                string `json:"product"`
		VersionFamily          string `json:"version_family"`
		EOGSDate               string `json:"eogs_date"`
		SupportStatusOnCapture string `json:"support_status_on_capture"`
		DirectPathToTarget     string `json:"direct_path_to_target"`
	}{}, false
}

func sameStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	left, right := append([]string(nil), a...), append([]string(nil), b...)
	sort.Strings(left)
	sort.Strings(right)
	return reflect.DeepEqual(left, right)
}

func contains(values []string, needle string) bool {
	for _, value := range values {
		if value == needle {
			return true
		}
	}
	return false
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	value, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return value
}

func mustDecode(t *testing.T, raw []byte, target any, label string) {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(raw))
	if err := decoder.Decode(target); err != nil {
		t.Fatalf("decode %s: %v", label, err)
	}
}

func decodeAny(t *testing.T, raw []byte, label string) any {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		t.Fatalf("decode %s: %v", label, err)
	}
	if decoder.More() {
		t.Fatalf("decode %s: trailing JSON value", label)
	}
	return value
}

// validateJSONSchema implements exactly the Draft 2020-12 vocabulary used by
// the protected installer schema. Keeping this small subset in the standard
// library makes verification deterministic and offline while still making the
// schema file, rather than Go structs, the first artifact contract.
func validateJSONSchema(schema, value any, path string) error {
	s, ok := schema.(map[string]any)
	if !ok {
		return fmt.Errorf("%s: schema node is not an object", path)
	}
	if expected, exists := s["const"]; exists && !reflect.DeepEqual(expected, value) {
		return fmt.Errorf("%s: got %v, want const %v", path, value, expected)
	}
	if choices, exists := s["enum"].([]any); exists {
		matched := false
		for _, choice := range choices {
			if reflect.DeepEqual(choice, value) {
				matched = true
				break
			}
		}
		if !matched {
			return fmt.Errorf("%s: %v is not in enum %v", path, value, choices)
		}
	}
	if typeName, exists := s["type"].(string); exists {
		if err := requireJSONType(typeName, value, path); err != nil {
			return err
		}
	}
	switch typed := value.(type) {
	case map[string]any:
		if required, exists := s["required"].([]any); exists {
			for _, rawName := range required {
				name, ok := rawName.(string)
				if !ok {
					return fmt.Errorf("%s: schema required entry is not a string", path)
				}
				if _, ok := typed[name]; !ok {
					return fmt.Errorf("%s: missing required property %q", path, name)
				}
			}
		}
		properties, _ := s["properties"].(map[string]any)
		if additional, exists := s["additionalProperties"].(bool); exists && !additional {
			for name := range typed {
				if _, declared := properties[name]; !declared {
					return fmt.Errorf("%s: additional property %q is forbidden", path, name)
				}
			}
		}
		for name, childSchema := range properties {
			child, exists := typed[name]
			if !exists {
				continue
			}
			if err := validateJSONSchema(childSchema, child, path+"."+name); err != nil {
				return err
			}
		}
	case []any:
		if minimum, ok := schemaInt(s["minItems"]); ok && len(typed) < minimum {
			return fmt.Errorf("%s: has %d items, minimum is %d", path, len(typed), minimum)
		}
		if itemSchema, exists := s["items"]; exists {
			for index, child := range typed {
				if err := validateJSONSchema(itemSchema, child, fmt.Sprintf("%s[%d]", path, index)); err != nil {
					return err
				}
			}
		}
	case string:
		if minimum, ok := schemaInt(s["minLength"]); ok && len([]rune(typed)) < minimum {
			return fmt.Errorf("%s: string length is below %d", path, minimum)
		}
		if expression, ok := s["pattern"].(string); ok {
			compiled, err := regexp.Compile(expression)
			if err != nil {
				return fmt.Errorf("%s: invalid schema pattern: %w", path, err)
			}
			if !compiled.MatchString(typed) {
				return fmt.Errorf("%s: %q does not match %q", path, typed, expression)
			}
		}
		if format, ok := s["format"].(string); ok && format == "date" {
			parsed, err := time.Parse("2006-01-02", typed)
			if err != nil || parsed.Format("2006-01-02") != typed {
				return fmt.Errorf("%s: %q is not an RFC 3339 full-date", path, typed)
			}
		}
	case json.Number:
		if minimum, ok := schemaFloat(s["minimum"]); ok {
			actual, err := strconv.ParseFloat(string(typed), 64)
			if err != nil || actual < minimum {
				return fmt.Errorf("%s: number %s is below minimum %v", path, typed, minimum)
			}
		}
	}
	return nil
}

func requireJSONType(name string, value any, path string) error {
	valid := false
	switch name {
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
			_, err := strconv.ParseInt(string(number), 10, 64)
			valid = err == nil && !strings.ContainsAny(string(number), ".eE")
		}
	case "null":
		valid = value == nil
	default:
		return fmt.Errorf("%s: unsupported schema type %q", path, name)
	}
	if !valid {
		return fmt.Errorf("%s: value %v is not type %s", path, value, name)
	}
	return nil
}

func schemaInt(value any) (int, bool) {
	number, ok := value.(json.Number)
	if !ok {
		return 0, false
	}
	parsed, err := strconv.Atoi(string(number))
	return parsed, err == nil
}

func schemaFloat(value any) (float64, bool) {
	number, ok := value.(json.Number)
	if !ok {
		return 0, false
	}
	parsed, err := strconv.ParseFloat(string(number), 64)
	return parsed, err == nil
}
