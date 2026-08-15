package verification

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/netip"
	"net/url"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"testing"

	"example.com/vcf-migration-architecture/migrationplan"
)

const (
	artifactPath = "../../migration-plan.json"
	researchPath = "../../research.md"
	schemaPath   = "../../spec/migration-plan.schema.json"
	fixturePath  = "../../fixtures/estate.json"
	snapshotPath = "../../spec/compatibility-snapshot.json"
)

type plan struct {
	SchemaVersion     string            `json:"schema_version"`
	EstateID          string            `json:"estate_id"`
	SnapshotID        string            `json:"snapshot_id"`
	TargetRelease     string            `json:"target_release"`
	SupportBoundaries []supportBoundary `json:"support_boundaries"`
	Architecture      architecture      `json:"architecture"`
	ProductMigrations []migration       `json:"product_migrations"`
	Gates             []gate            `json:"gates"`
	Steps             []step            `json:"steps"`
}

type supportBoundary struct {
	InventoryID       string `json:"inventory_id"`
	Product           string `json:"product"`
	Version           string `json:"version"`
	EndGeneralSupport string `json:"end_of_general_support"`
	StatusAtSnapshot  string `json:"status_at_snapshot"`
}

type architecture struct {
	ManagementDomain managementDomain `json:"management_domain"`
	Components       []component      `json:"components"`
}

type managementDomain struct {
	Name        string   `json:"name"`
	Topology    string   `json:"topology"`
	DataSiteIDs []string `json:"data_site_ids"`
	Witness     struct {
		SiteID                     string `json:"site_id"`
		Type                       string `json:"type"`
		ManagementWorkloadsAllowed bool   `json:"management_workloads_allowed"`
	} `json:"witness"`
}

type component struct {
	ID              string          `json:"id"`
	Product         string          `json:"product"`
	Version         string          `json:"version"`
	DeploymentModel string          `json:"deployment_model"`
	SizeProfile     string          `json:"size_profile"`
	NodeCount       int             `json:"node_count"`
	Nodes           []node          `json:"nodes"`
	PlacementRules  []placementRule `json:"placement_rules"`
	CapacityBasis   []capacityDatum `json:"capacity_basis"`
}

type node struct {
	ID         string `json:"id"`
	SiteID     string `json:"site_id"`
	Role       string `json:"role"`
	VCPU       int    `json:"vcpu"`
	MemoryGiB  int    `json:"memory_gib"`
	StorageGiB int    `json:"storage_gib"`
}

type placementRule struct {
	ID         string `json:"id"`
	Constraint string `json:"constraint"`
}

type capacityDatum struct {
	Metric string `json:"metric"`
	Value  int    `json:"value"`
	Unit   string `json:"unit"`
}

type migration struct {
	Source struct {
		InventoryID string `json:"inventory_id"`
		Product     string `json:"product"`
		Version     string `json:"version"`
	} `json:"source"`
	TargetComponentID string          `json:"target_component_id"`
	TargetProduct     string          `json:"target_product"`
	TargetVersion     string          `json:"target_version"`
	MigrationMode     string          `json:"migration_mode"`
	VersionPath       []string        `json:"version_path"`
	CarryForward      []carriedItem   `json:"carry_forward"`
	Abandoned         []abandonedItem `json:"abandoned"`
	GateIDs           []string        `json:"gate_ids"`
}

type carriedItem struct {
	InventoryID string `json:"inventory_id"`
	Method      string `json:"method"`
	TargetForm  string `json:"target_form"`
	Validation  string `json:"validation"`
}

type abandonedItem struct {
	InventoryID string `json:"inventory_id"`
	Method      string `json:"method"`
	TargetForm  string `json:"target_form"`
	Reason      string `json:"reason"`
}

type gate struct {
	ID        string   `json:"id"`
	Phase     string   `json:"phase"`
	Condition string   `json:"condition"`
	Evidence  string   `json:"evidence"`
	Blocks    []string `json:"blocks"`
}

type step struct {
	Order       int      `json:"order"`
	ID          string   `json:"id"`
	ComponentID string   `json:"component_id"`
	Action      string   `json:"action"`
	DependsOn   []string `json:"depends_on"`
	GateIDs     []string `json:"gate_ids"`
	Rollback    string   `json:"rollback"`
}

type estate struct {
	EstateID         string `json:"estate_id"`
	ManagementDomain struct {
		Name          string   `json:"name"`
		Topology      string   `json:"topology"`
		DataSiteIDs   []string `json:"data_site_ids"`
		WitnessSiteID string   `json:"witness_site_id"`
		Sites         []struct {
			ID              string `json:"id"`
			Role            string `json:"role"`
			ManagementHosts int    `json:"management_hosts"`
		} `json:"sites"`
	} `json:"management_domain"`
	Products []struct {
		ID      string `json:"id"`
		Product string `json:"product"`
		Version string `json:"version"`
		Content []struct {
			ID   string `json:"id"`
			Kind string `json:"kind"`
		} `json:"content"`
	} `json:"products"`
}

type snapshot struct {
	SnapshotID        string            `json:"snapshot_id"`
	TargetRelease     string            `json:"target_release"`
	SupportBoundaries []supportBoundary `json:"support_boundaries"`
	MigrationRules    []struct {
		InventoryID       string   `json:"inventory_id"`
		TargetComponentID string   `json:"target_component_id"`
		TargetProduct     string   `json:"target_product"`
		TargetVersion     string   `json:"target_version"`
		MigrationMode     string   `json:"migration_mode"`
		VersionPath       []string `json:"version_path"`
		RequiredGateIDs   []string `json:"required_gate_ids"`
		ContentRules      []struct {
			InventoryID string `json:"inventory_id"`
			Disposition string `json:"disposition"`
			Method      string `json:"method"`
			TargetForm  string `json:"target_form"`
		} `json:"content_rules"`
	} `json:"migration_rules"`
	RequiredArchitecture struct {
		ManagementDomainTopology          string   `json:"management_domain_topology"`
		DataSiteIDs                       []string `json:"data_site_ids"`
		WitnessSiteID                     string   `json:"witness_site_id"`
		WitnessType                       string   `json:"witness_type"`
		WitnessManagementWorkloadsAllowed bool     `json:"witness_management_workloads_allowed"`
		Components                        []struct {
			ID                   string         `json:"id"`
			Product              string         `json:"product"`
			Version              string         `json:"version"`
			DeploymentModel      string         `json:"deployment_model"`
			SizeProfile          string         `json:"size_profile"`
			NodeCount            int            `json:"node_count"`
			PerSiteNodeCount     map[string]int `json:"per_site_node_count"`
			MinimumNodeResources struct {
				VCPU       int `json:"vcpu"`
				MemoryGiB  int `json:"memory_gib"`
				StorageGiB int `json:"storage_gib"`
			} `json:"minimum_node_resources"`
			RequiredPlacementRuleIDs []string       `json:"required_placement_rule_ids"`
			CapacityMetrics          map[string]int `json:"capacity_metrics"`
		} `json:"components"`
	} `json:"required_architecture"`
	RequiredGates []string `json:"required_gates"`
	RequiredSteps []struct {
		Order       int      `json:"order"`
		ID          string   `json:"id"`
		ComponentID string   `json:"component_id"`
		DependsOn   []string `json:"depends_on"`
		GateIDs     []string `json:"gate_ids"`
	} `json:"required_steps"`
}

func TestMigrationArchitecture(t *testing.T) {
	// Schema validation is deliberately completed before the fixture or pinned
	// compatibility authority is opened. Semantic checks must never accept a
	// document that is outside the installer specification's own schema.
	schemaValue, err := readJSONValue(schemaPath)
	if err != nil {
		t.Fatalf("read schema: %v", err)
	}
	artifactValue, err := readJSONValue(artifactPath)
	if err != nil {
		t.Fatalf("read migration-plan.json: %v", err)
	}
	schemaObject, ok := schemaValue.(map[string]any)
	if !ok {
		t.Fatal("schema root is not an object")
	}
	if err := validateJSONSchema(schemaObject, schemaObject, artifactValue, "$"); err != nil {
		t.Fatalf("migration-plan.json does not validate against spec/migration-plan.schema.json: %v", err)
	}

	var got plan
	if err := decodeJSON(artifactPath, &got); err != nil {
		t.Fatalf("decode schema-valid plan: %v", err)
	}
	var inv estate
	if err := decodeJSON(fixturePath, &inv); err != nil {
		t.Fatalf("decode estate fixture: %v", err)
	}
	var authority snapshot
	if err := decodeJSON(snapshotPath, &authority); err != nil {
		t.Fatalf("decode compatibility snapshot: %v", err)
	}

	checks := []struct {
		name string
		fn   func() error
	}{
		{"identity and support boundaries", func() error { return checkIdentityAndSupport(got, inv, authority) }},
		{"stretched-domain and witness placement", func() error { return checkManagementDomain(got, inv, authority) }},
		{"target component placement and sizing", func() error { return checkComponents(got, authority) }},
		{"migration routes and content disposition", func() error { return checkMigrations(got, inv, authority) }},
		{"blocking gates", func() error { return checkGates(got, authority) }},
		{"ordered dependency-consistent execution", func() error { return checkSteps(got, authority) }},
	}
	for _, tc := range checks {
		t.Run(tc.name, func(t *testing.T) {
			if err := tc.fn(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestExportedAPIRebuildsAndWritesArtifact(t *testing.T) {
	committed, err := readJSONValue(artifactPath)
	if err != nil {
		t.Fatalf("read committed artifact: %v", err)
	}
	builtBytes, err := json.Marshal(migrationplan.Build())
	if err != nil {
		t.Fatalf("marshal Build result: %v", err)
	}
	built, err := decodeJSONValue(builtBytes)
	if err != nil {
		t.Fatalf("decode Build result: %v", err)
	}
	if !reflect.DeepEqual(built, committed) {
		t.Fatal("migrationplan.Build() does not reproduce migration-plan.json")
	}

	outputPath := t.TempDir() + "/migration-plan.json"
	if err := migrationplan.Write(outputPath); err != nil {
		t.Fatalf("migrationplan.Write: %v", err)
	}
	written, err := readJSONValue(outputPath)
	if err != nil {
		t.Fatalf("read Write output: %v", err)
	}
	if !reflect.DeepEqual(written, committed) {
		t.Fatal("migrationplan.Write() does not reproduce migration-plan.json")
	}
}

func TestResearchBibliography(t *testing.T) {
	raw, err := os.ReadFile(researchPath)
	if err != nil {
		t.Fatalf("read research.md: %v", err)
	}
	content := string(raw)
	if strings.TrimSpace(content) == "" {
		t.Fatal("research.md is empty")
	}
	datePattern := regexp.MustCompile(`\b20[0-9]{2}-(0[1-9]|1[0-2])-([0-2][0-9]|3[01])\b`)
	urlPattern := regexp.MustCompile(`https://[^\s|)>]+`)
	urls := urlPattern.FindAllString(content, -1)
	if len(urls) == 0 {
		t.Fatal("research.md contains no bibliography sources")
	}
	if dates := datePattern.FindAllString(content, -1); len(dates) < len(urls) {
		t.Errorf("research.md records %d source URLs but only %d ISO access dates", len(urls), len(dates))
	}
	for _, rawURL := range urls {
		parsed, parseErr := url.Parse(strings.TrimRight(rawURL, ".,;"))
		if parseErr != nil || parsed.Scheme != "https" || parsed.User != nil {
			t.Errorf("invalid public HTTPS research URL %q", rawURL)
			continue
		}
		host := strings.ToLower(parsed.Hostname())
		if host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com") {
			t.Errorf("research URL is not on a Broadcom-published site: %q", rawURL)
		}
		if address, parseAddressErr := netip.ParseAddr(host); parseAddressErr == nil && (!address.IsGlobalUnicast() || address.IsPrivate()) {
			t.Errorf("research URL uses a non-public address: %q", rawURL)
		}
	}
	lower := strings.ToLower(content)
	if !strings.Contains(lower, "broadcom") {
		t.Error("research.md does not identify the source publisher/site as Broadcom")
	}
	for _, subject := range []string{"vrealize operations", "aria automation", "operations for logs", "support", "upgrade", "compatib"} {
		if !strings.Contains(lower, subject) {
			t.Errorf("research.md does not cover required research subject %q", subject)
		}
	}
}

func TestPackageIncludesTableDrivenTests(t *testing.T) {
	entries, err := os.ReadDir("../../migrationplan")
	if err != nil {
		t.Fatalf("read migrationplan package: %v", err)
	}
	var tests strings.Builder
	hasTable := false
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), "_test.go") {
			continue
		}
		raw, readErr := os.ReadFile("../../migrationplan/" + entry.Name())
		if readErr != nil {
			t.Fatalf("read %s: %v", entry.Name(), readErr)
		}
		content := string(raw)
		tests.WriteString(content)
		if regexp.MustCompile(`func\s+Test[A-Za-z0-9_]*\s*\(`).MatchString(content) &&
			regexp.MustCompile(`(?s)\bfor\b.*\brange\b`).MatchString(content) {
			hasTable = true
		}
	}
	if !hasTable {
		t.Fatal("add table-driven tests under migrationplan/")
	}
	lower := strings.ToLower(tests.String())
	subjects := []struct {
		name     string
		patterns []string
	}{
		{"version routes", []string{"versionpath", "version_path"}},
		{"content disposition", []string{"carryforward", "carry_forward"}},
		{"abandoned content", []string{"abandoned"}},
		{"component placement", []string{"siteid", "site_id"}},
		{"witness placement", []string{"witness"}},
		{"vCPU sizing", []string{"vcpu"}},
		{"memory sizing", []string{"memorygib", "memory_gib"}},
		{"storage sizing", []string{"storagegib", "storage_gib"}},
	}
	for _, subject := range subjects {
		matched := false
		for _, pattern := range subject.patterns {
			matched = matched || strings.Contains(lower, pattern)
		}
		if !matched {
			t.Errorf("package tests do not cover %s", subject.name)
		}
	}
}

func checkIdentityAndSupport(got plan, inv estate, authority snapshot) error {
	if got.SchemaVersion != "1.0.0" {
		return fmt.Errorf("schema_version = %q, want 1.0.0", got.SchemaVersion)
	}
	if got.EstateID != inv.EstateID {
		return fmt.Errorf("estate_id = %q, want %q", got.EstateID, inv.EstateID)
	}
	if got.SnapshotID != authority.SnapshotID || got.TargetRelease != authority.TargetRelease {
		return fmt.Errorf("plan must bind snapshot %q and target release %q", authority.SnapshotID, authority.TargetRelease)
	}
	want := make(map[string]supportBoundary, len(authority.SupportBoundaries))
	for _, boundary := range authority.SupportBoundaries {
		want[boundary.InventoryID] = boundary
	}
	if len(got.SupportBoundaries) != len(want) {
		return fmt.Errorf("support boundaries: got %d, want %d", len(got.SupportBoundaries), len(want))
	}
	seen := map[string]bool{}
	for _, boundary := range got.SupportBoundaries {
		expected, ok := want[boundary.InventoryID]
		if !ok || seen[boundary.InventoryID] {
			return fmt.Errorf("unexpected or duplicate support boundary %q", boundary.InventoryID)
		}
		seen[boundary.InventoryID] = true
		if boundary != expected {
			return fmt.Errorf("support boundary %q = %+v, want %+v", boundary.InventoryID, boundary, expected)
		}
	}
	return nil
}

func checkManagementDomain(got plan, inv estate, authority snapshot) error {
	md := got.Architecture.ManagementDomain
	required := authority.RequiredArchitecture
	if md.Name != inv.ManagementDomain.Name || md.Topology != inv.ManagementDomain.Topology || md.Topology != required.ManagementDomainTopology {
		return fmt.Errorf("management-domain identity/topology does not match the fixture and snapshot")
	}
	if !sameStrings(md.DataSiteIDs, inv.ManagementDomain.DataSiteIDs) || !sameStrings(md.DataSiteIDs, required.DataSiteIDs) {
		return fmt.Errorf("data_site_ids = %v, want %v", md.DataSiteIDs, required.DataSiteIDs)
	}
	if md.Witness.SiteID != inv.ManagementDomain.WitnessSiteID || md.Witness.SiteID != required.WitnessSiteID {
		return fmt.Errorf("witness site = %q, want third site %q", md.Witness.SiteID, required.WitnessSiteID)
	}
	if contains(md.DataSiteIDs, md.Witness.SiteID) {
		return errors.New("witness must not be placed at either data site")
	}
	if md.Witness.Type != required.WitnessType || md.Witness.ManagementWorkloadsAllowed != required.WitnessManagementWorkloadsAllowed {
		return errors.New("witness type or management-workload placement does not match the pinned architecture")
	}
	foundWitness := false
	for _, site := range inv.ManagementDomain.Sites {
		if site.ID == md.Witness.SiteID {
			foundWitness = site.Role == "witness" && site.ManagementHosts == 0
		}
	}
	if !foundWitness {
		return errors.New("chosen witness site is not the inventory's workload-free witness fault domain")
	}
	return nil
}

func checkComponents(got plan, authority snapshot) error {
	want := authority.RequiredArchitecture.Components
	if len(got.Architecture.Components) != len(want) {
		return fmt.Errorf("components: got %d, want %d", len(got.Architecture.Components), len(want))
	}
	gotByID := map[string]component{}
	allNodeIDs := map[string]bool{}
	for _, c := range got.Architecture.Components {
		if _, duplicate := gotByID[c.ID]; duplicate {
			return fmt.Errorf("duplicate component %q", c.ID)
		}
		gotByID[c.ID] = c
		for _, n := range c.Nodes {
			if allNodeIDs[n.ID] {
				return fmt.Errorf("duplicate target node id %q", n.ID)
			}
			allNodeIDs[n.ID] = true
			if n.SiteID == authority.RequiredArchitecture.WitnessSiteID {
				return fmt.Errorf("management component node %q is placed at witness site", n.ID)
			}
		}
	}
	for _, expected := range want {
		actual, ok := gotByID[expected.ID]
		if !ok {
			return fmt.Errorf("missing target component %q", expected.ID)
		}
		if actual.Product != expected.Product || actual.Version != expected.Version || actual.DeploymentModel != expected.DeploymentModel || actual.SizeProfile != expected.SizeProfile {
			return fmt.Errorf("component %q product/version/model/profile does not match snapshot", expected.ID)
		}
		if actual.NodeCount != expected.NodeCount || len(actual.Nodes) != expected.NodeCount {
			return fmt.Errorf("component %q has declared/actual nodes %d/%d, want %d", expected.ID, actual.NodeCount, len(actual.Nodes), expected.NodeCount)
		}
		siteCounts := map[string]int{}
		for _, n := range actual.Nodes {
			siteCounts[n.SiteID]++
			if n.VCPU < expected.MinimumNodeResources.VCPU || n.MemoryGiB < expected.MinimumNodeResources.MemoryGiB || n.StorageGiB < expected.MinimumNodeResources.StorageGiB {
				return fmt.Errorf("component %q node %q is below minimum resources", expected.ID, n.ID)
			}
		}
		if !reflect.DeepEqual(siteCounts, expected.PerSiteNodeCount) {
			return fmt.Errorf("component %q site spread = %v, want %v", expected.ID, siteCounts, expected.PerSiteNodeCount)
		}
		placementIDs := make([]string, 0, len(actual.PlacementRules))
		for _, rule := range actual.PlacementRules {
			placementIDs = append(placementIDs, rule.ID)
		}
		if !containsAll(placementIDs, expected.RequiredPlacementRuleIDs) {
			return fmt.Errorf("component %q placement rules = %v, require %v", expected.ID, placementIDs, expected.RequiredPlacementRuleIDs)
		}
		capacity := map[string]int{}
		for _, datum := range actual.CapacityBasis {
			if _, duplicate := capacity[datum.Metric]; duplicate {
				return fmt.Errorf("component %q duplicates capacity metric %q", expected.ID, datum.Metric)
			}
			capacity[datum.Metric] = datum.Value
		}
		if !reflect.DeepEqual(capacity, expected.CapacityMetrics) {
			return fmt.Errorf("component %q capacity basis = %v, want %v", expected.ID, capacity, expected.CapacityMetrics)
		}
	}
	return nil
}

func checkMigrations(got plan, inv estate, authority snapshot) error {
	if len(got.ProductMigrations) != len(inv.Products) || len(got.ProductMigrations) != len(authority.MigrationRules) {
		return fmt.Errorf("product migrations: got %d, want one for each of %d products", len(got.ProductMigrations), len(inv.Products))
	}
	products := map[string]struct {
		Product string
		Version string
		IDs     []string
	}{}
	for _, product := range inv.Products {
		entry := struct {
			Product string
			Version string
			IDs     []string
		}{Product: product.Product, Version: product.Version}
		for _, item := range product.Content {
			entry.IDs = append(entry.IDs, item.ID)
		}
		products[product.ID] = entry
	}
	rules := map[string]struct {
		TargetComponentID string
		TargetProduct     string
		TargetVersion     string
		MigrationMode     string
		VersionPath       []string
		RequiredGateIDs   []string
		Content           map[string][3]string
	}{}
	for _, rule := range authority.MigrationRules {
		entry := struct {
			TargetComponentID string
			TargetProduct     string
			TargetVersion     string
			MigrationMode     string
			VersionPath       []string
			RequiredGateIDs   []string
			Content           map[string][3]string
		}{rule.TargetComponentID, rule.TargetProduct, rule.TargetVersion, rule.MigrationMode, rule.VersionPath, rule.RequiredGateIDs, map[string][3]string{}}
		for _, item := range rule.ContentRules {
			entry.Content[item.InventoryID] = [3]string{item.Disposition, item.Method, item.TargetForm}
		}
		rules[rule.InventoryID] = entry
	}
	seenMigrations := map[string]bool{}
	for _, actual := range got.ProductMigrations {
		id := actual.Source.InventoryID
		product, ok := products[id]
		if !ok || seenMigrations[id] {
			return fmt.Errorf("unexpected or duplicate source migration %q", id)
		}
		seenMigrations[id] = true
		rule := rules[id]
		if actual.Source.Product != product.Product || actual.Source.Version != product.Version {
			return fmt.Errorf("migration %q does not name the inventoried source product and version", id)
		}
		if actual.TargetComponentID != rule.TargetComponentID || actual.TargetProduct != rule.TargetProduct || actual.TargetVersion != rule.TargetVersion || actual.MigrationMode != rule.MigrationMode {
			return fmt.Errorf("migration %q target or mode does not match snapshot", id)
		}
		if !reflect.DeepEqual(actual.VersionPath, rule.VersionPath) {
			return fmt.Errorf("migration %q path = %v, want %v", id, actual.VersionPath, rule.VersionPath)
		}
		if !containsAll(actual.GateIDs, rule.RequiredGateIDs) {
			return fmt.Errorf("migration %q gates = %v, require %v", id, actual.GateIDs, rule.RequiredGateIDs)
		}
		dispositions := map[string][3]string{}
		for _, item := range actual.CarryForward {
			if _, duplicate := dispositions[item.InventoryID]; duplicate {
				return fmt.Errorf("migration %q classifies %q more than once", id, item.InventoryID)
			}
			dispositions[item.InventoryID] = [3]string{"carry_forward", item.Method, item.TargetForm}
		}
		for _, item := range actual.Abandoned {
			if _, duplicate := dispositions[item.InventoryID]; duplicate {
				return fmt.Errorf("migration %q classifies %q more than once", id, item.InventoryID)
			}
			dispositions[item.InventoryID] = [3]string{"abandon", item.Method, item.TargetForm}
		}
		if len(dispositions) != len(product.IDs) {
			return fmt.Errorf("migration %q classifies %d items, inventory has %d", id, len(dispositions), len(product.IDs))
		}
		for _, contentID := range product.IDs {
			if dispositions[contentID] != rule.Content[contentID] {
				return fmt.Errorf("migration %q disposition for %q = %v, want %v", id, contentID, dispositions[contentID], rule.Content[contentID])
			}
		}
	}
	return nil
}

func checkGates(got plan, authority snapshot) error {
	gateIDs := map[string]bool{}
	for _, actual := range got.Gates {
		if gateIDs[actual.ID] {
			return fmt.Errorf("duplicate gate %q", actual.ID)
		}
		gateIDs[actual.ID] = true
	}
	for _, id := range authority.RequiredGates {
		if !gateIDs[id] {
			return fmt.Errorf("missing required blocking gate %q", id)
		}
	}
	for _, migration := range got.ProductMigrations {
		for _, id := range migration.GateIDs {
			if !gateIDs[id] {
				return fmt.Errorf("migration %q references undefined gate %q", migration.Source.InventoryID, id)
			}
		}
	}
	return nil
}

func checkSteps(got plan, authority snapshot) error {
	if len(got.Steps) != len(authority.RequiredSteps) {
		return fmt.Errorf("steps: got %d, want %d", len(got.Steps), len(authority.RequiredSteps))
	}
	gateIDs := map[string]bool{}
	for _, g := range got.Gates {
		gateIDs[g.ID] = true
	}
	seen := map[string]int{}
	previousOrder := 0
	for i, actual := range got.Steps {
		expected := authority.RequiredSteps[i]
		if actual.Order <= previousOrder {
			return fmt.Errorf("step order is not strictly increasing at %q", actual.ID)
		}
		previousOrder = actual.Order
		if actual.Order != expected.Order || actual.ID != expected.ID || actual.ComponentID != expected.ComponentID || !sameStrings(actual.DependsOn, expected.DependsOn) || !sameStrings(actual.GateIDs, expected.GateIDs) {
			return fmt.Errorf("step %d does not match pinned order/dependencies/gates for %q", i, expected.ID)
		}
		if _, duplicate := seen[actual.ID]; duplicate {
			return fmt.Errorf("duplicate step id %q", actual.ID)
		}
		for _, dependency := range actual.DependsOn {
			dependencyOrder, ok := seen[dependency]
			if !ok || dependencyOrder >= actual.Order {
				return fmt.Errorf("step %q dependency %q is absent or not earlier", actual.ID, dependency)
			}
		}
		for _, gateID := range actual.GateIDs {
			if !gateIDs[gateID] {
				return fmt.Errorf("step %q references undefined gate %q", actual.ID, gateID)
			}
		}
		seen[actual.ID] = actual.Order
	}
	for _, g := range got.Gates {
		for _, blockedStep := range g.Blocks {
			if _, ok := seen[blockedStep]; !ok {
				return fmt.Errorf("gate %q blocks undefined step %q", g.ID, blockedStep)
			}
			attached := false
			for _, candidate := range got.Steps {
				if candidate.ID == blockedStep && contains(candidate.GateIDs, g.ID) {
					attached = true
				}
			}
			if !attached {
				return fmt.Errorf("gate %q claims to block %q but that step does not attach the gate", g.ID, blockedStep)
			}
		}
	}
	return nil
}

func decodeJSON(path string, target any) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	if err := decoder.Decode(target); err != nil {
		return err
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		if err == nil {
			return errors.New("unexpected trailing JSON value")
		}
		return fmt.Errorf("trailing JSON: %w", err)
	}
	return nil
}

func readJSONValue(path string) (any, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return decodeJSONValue(raw)
}

func decodeJSONValue(raw []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		if err == nil {
			return nil, errors.New("unexpected trailing JSON value")
		}
		return nil, fmt.Errorf("trailing JSON: %w", err)
	}
	return value, nil
}

func sameStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	aa, bb := append([]string(nil), a...), append([]string(nil), b...)
	sort.Strings(aa)
	sort.Strings(bb)
	return reflect.DeepEqual(aa, bb)
}

func contains(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

func containsAll(values, wanted []string) bool {
	for _, value := range wanted {
		if !contains(values, value) {
			return false
		}
	}
	return true
}

// validateJSONSchema implements the deliberately small Draft 2020-12 keyword
// set used by migration-plan.schema.json. Unknown schema keywords are annotations;
// every assertion keyword used by the installer schema is handled below.
func validateJSONSchema(root, schema map[string]any, value any, path string) error {
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolveLocalRef(root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		return validateJSONSchema(root, resolved, value, path)
	}
	if expected, ok := schema["const"]; ok && !jsonEqual(expected, value) {
		return fmt.Errorf("%s: value does not equal const %v", path, expected)
	}
	if enum, ok := schema["enum"].([]any); ok {
		matched := false
		for _, candidate := range enum {
			matched = matched || jsonEqual(candidate, value)
		}
		if !matched {
			return fmt.Errorf("%s: value is not in enum", path)
		}
	}
	if typeName, ok := schema["type"].(string); ok && !matchesJSONType(typeName, value) {
		return fmt.Errorf("%s: got %T, want JSON %s", path, value, typeName)
	}

	switch typed := value.(type) {
	case map[string]any:
		if err := validateObject(root, schema, typed, path); err != nil {
			return err
		}
	case []any:
		if err := validateArray(root, schema, typed, path); err != nil {
			return err
		}
	case string:
		if min, ok := schemaInteger(schema["minLength"]); ok && len([]rune(typed)) < min {
			return fmt.Errorf("%s: string is shorter than minLength %d", path, min)
		}
		if pattern, ok := schema["pattern"].(string); ok {
			re, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("invalid schema pattern %q: %w", pattern, err)
			}
			if !re.MatchString(typed) {
				return fmt.Errorf("%s: string does not match %q", path, pattern)
			}
		}
	case json.Number:
		if minimum, ok := schemaNumber(schema["minimum"]); ok {
			actual, err := strconv.ParseFloat(typed.String(), 64)
			if err != nil || actual < minimum {
				return fmt.Errorf("%s: number is below minimum %v", path, minimum)
			}
		}
	}
	return nil
}

func validateObject(root, schema, value map[string]any, path string) error {
	if required, ok := schema["required"].([]any); ok {
		for _, rawName := range required {
			name, _ := rawName.(string)
			if _, present := value[name]; !present {
				return fmt.Errorf("%s: missing required property %q", path, name)
			}
		}
	}
	properties, _ := schema["properties"].(map[string]any)
	if additional, present := schema["additionalProperties"].(bool); present && !additional {
		for name := range value {
			if _, declared := properties[name]; !declared {
				return fmt.Errorf("%s: additional property %q is not allowed", path, name)
			}
		}
	}
	for name, propertySchemaValue := range properties {
		propertyValue, present := value[name]
		if !present {
			continue
		}
		propertySchema, ok := propertySchemaValue.(map[string]any)
		if !ok {
			return fmt.Errorf("schema property %q is not an object", name)
		}
		if err := validateJSONSchema(root, propertySchema, propertyValue, path+"."+name); err != nil {
			return err
		}
	}
	return nil
}

func validateArray(root, schema map[string]any, value []any, path string) error {
	if min, ok := schemaInteger(schema["minItems"]); ok && len(value) < min {
		return fmt.Errorf("%s: array has %d items, minItems is %d", path, len(value), min)
	}
	if max, ok := schemaInteger(schema["maxItems"]); ok && len(value) > max {
		return fmt.Errorf("%s: array has %d items, maxItems is %d", path, len(value), max)
	}
	if unique, _ := schema["uniqueItems"].(bool); unique {
		seen := map[string]bool{}
		for _, item := range value {
			encoded, _ := json.Marshal(item)
			key := string(encoded)
			if seen[key] {
				return fmt.Errorf("%s: array items are not unique", path)
			}
			seen[key] = true
		}
	}
	itemSchema, ok := schema["items"].(map[string]any)
	if !ok {
		return nil
	}
	for index, item := range value {
		if err := validateJSONSchema(root, itemSchema, item, fmt.Sprintf("%s[%d]", path, index)); err != nil {
			return err
		}
	}
	return nil
}

func resolveLocalRef(root map[string]any, ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("only local JSON pointers are supported, got %q", ref)
	}
	var current any = root
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("cannot resolve %q", ref)
		}
		current, ok = object[token]
		if !ok {
			return nil, fmt.Errorf("cannot resolve %q", ref)
		}
	}
	resolved, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("reference %q is not a schema object", ref)
	}
	return resolved, nil
}

func matchesJSONType(name string, value any) bool {
	switch name {
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
	case "integer":
		number, ok := value.(json.Number)
		if !ok {
			return false
		}
		parsed, err := strconv.ParseFloat(number.String(), 64)
		return err == nil && parsed == math.Trunc(parsed)
	case "number":
		_, ok := value.(json.Number)
		return ok
	case "null":
		return value == nil
	default:
		return false
	}
}

func schemaInteger(value any) (int, bool) {
	switch typed := value.(type) {
	case json.Number:
		parsed, err := strconv.Atoi(typed.String())
		return parsed, err == nil
	case float64:
		return int(typed), typed == math.Trunc(typed)
	case int:
		return typed, true
	default:
		return 0, false
	}
}

func schemaNumber(value any) (float64, bool) {
	switch typed := value.(type) {
	case json.Number:
		parsed, err := strconv.ParseFloat(typed.String(), 64)
		return parsed, err == nil
	case float64:
		return typed, true
	case int:
		return float64(typed), true
	default:
		return 0, false
	}
}

func jsonEqual(a, b any) bool {
	left, _ := json.Marshal(a)
	right, _ := json.Marshal(b)
	return bytes.Equal(left, right)
}
