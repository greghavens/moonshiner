package verifier

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"sort"
	"strings"
)

const (
	protectedSchemaSHA256    = "abb710e38f6df15afae327d106805b0807d3479c6009cca41528fe410d7dbe88"
	protectedInventorySHA256 = "f464abf4122041fb12454c4ccdf39cd3255701584044eca95b8186717ea4ac3a"
	protectedSnapshotSHA256  = "262e1ca922052bc335a3d90550a36aeb505bdd16f6a26834789cc9daac13fa83"
)

type Resources struct {
	VCPU       int `json:"vcpu"`
	MemoryGiB  int `json:"memory_gib"`
	StorageGiB int `json:"storage_gib"`
}

type ProductVersion struct {
	Product string `json:"product"`
	Version string `json:"version"`
}

type Inventory struct {
	EstateID                 string         `json:"estate_id"`
	PlanningDate             string         `json:"planning_date"`
	TargetBundle             ProductVersion `json:"target_bundle"`
	ManagementInfrastructure struct {
		Site            string    `json:"site"`
		WorkloadDomain  string    `json:"workload_domain"`
		Cluster         string    `json:"cluster"`
		FaultDomains    []string  `json:"fault_domains"`
		Datastore       string    `json:"datastore"`
		Networks        []string  `json:"networks"`
		Capacity        Resources `json:"capacity"`
		CurrentlyUsed   Resources `json:"currently_used"`
		RequiredReserve Resources `json:"required_reserve"`
	} `json:"management_infrastructure"`
	LifecycleManager struct {
		Product    string `json:"product"`
		Version    string `json:"version"`
		PatchLevel string `json:"patch_level"`
	} `json:"lifecycle_manager"`
	Components []InventoryComponent `json:"components"`
}

type InventoryComponent struct {
	ID      string          `json:"id"`
	Product string          `json:"product"`
	Version string          `json:"version"`
	Items   []InventoryItem `json:"items"`
}

type InventoryItem struct {
	ID   string `json:"id"`
	Kind string `json:"kind"`
	Name string `json:"name"`
}

type CompatibilitySnapshot struct {
	SnapshotID        string         `json:"snapshot_id"`
	AsOf              string         `json:"as_of"`
	TargetBundle      ProductVersion `json:"target_bundle"`
	FleetPrerequisite struct {
		SourceLifecycleProduct string `json:"source_lifecycle_product"`
		SourceVersion          string `json:"source_version"`
		MinimumPatch           string `json:"minimum_patch"`
		Operation              string `json:"operation"`
	} `json:"fleet_prerequisite"`
	Placement              map[string]PlacementRule `json:"placement"`
	MigrationCompatibility []MigrationRule          `json:"migration_compatibility"`
	SupportBoundaries      []SupportBoundary        `json:"support_boundaries"`
}

type PlacementRule struct {
	Network           string `json:"network"`
	Profile           string `json:"profile"`
	NodeCount         int    `json:"node_count"`
	VCPUPerNode       int    `json:"vcpu_per_node"`
	MemoryGiBPerNode  int    `json:"memory_gib_per_node"`
	StorageGiBPerNode int    `json:"storage_gib_per_node"`
}

type MigrationRule struct {
	SourceProduct          string              `json:"source_product"`
	SourceVersion          string              `json:"source_version"`
	TargetProduct          string              `json:"target_product"`
	TargetVersion          string              `json:"target_version"`
	Strategy               string              `json:"strategy"`
	DirectInPlaceSupported bool                `json:"direct_in_place_supported"`
	RequiredOperations     []string            `json:"required_operations"`
	ItemRules              map[string]ItemRule `json:"item_rules"`
}

type ItemRule struct {
	Disposition string `json:"disposition"`
	Mechanism   string `json:"mechanism"`
}

type SupportBoundary struct {
	Product           string `json:"product"`
	ReleaseLine       string `json:"release_line"`
	EndGeneralSupport string `json:"end_of_general_support"`
}

type Plan struct {
	SchemaVersion      string             `json:"schema_version"`
	EstateID           string             `json:"estate_id"`
	PlanDate           string             `json:"plan_date"`
	TargetBundle       ProductVersion     `json:"target_bundle"`
	Research           Research           `json:"research"`
	TargetArchitecture TargetArchitecture `json:"target_architecture"`
	Migrations         []Migration        `json:"migrations"`
	Steps              []Step             `json:"steps"`
}

type Research struct {
	Sources        []ResearchSource `json:"sources"`
	Reconciliation string           `json:"reconciliation"`
}

type ResearchSource struct {
	Publisher   string   `json:"publisher"`
	Title       string   `json:"title"`
	URL         string   `json:"url"`
	ConsultedOn string   `json:"consulted_on"`
	Supports    []string `json:"supports"`
}

type TargetArchitecture struct {
	Components     []TargetComponent `json:"components"`
	CapacityRollup CapacityRollup    `json:"capacity_rollup"`
}

type TargetComponent struct {
	ID                string   `json:"id"`
	Product           string   `json:"product"`
	Version           string   `json:"version"`
	Site              string   `json:"site"`
	WorkloadDomain    string   `json:"workload_domain"`
	Cluster           string   `json:"cluster"`
	Network           string   `json:"network"`
	Datastore         string   `json:"datastore"`
	VIPFQDN           string   `json:"vip_fqdn"`
	Profile           string   `json:"profile"`
	NodeCount         int      `json:"node_count"`
	FaultDomains      []string `json:"fault_domains"`
	VCPUPerNode       int      `json:"vcpu_per_node"`
	MemoryGiBPerNode  int      `json:"memory_gib_per_node"`
	StorageGiBPerNode int      `json:"storage_gib_per_node"`
	SizingBasis       string   `json:"sizing_basis"`
}

type CapacityRollup struct {
	Planned            Resources `json:"planned"`
	RemainingAfterPlan Resources `json:"remaining_after_plan"`
	RequiredReserve    Resources `json:"required_reserve"`
}

type Migration struct {
	ID                string               `json:"id"`
	SourceComponentID string               `json:"source_component_id"`
	Source            ProductVersion       `json:"source"`
	TargetComponentID string               `json:"target_component_id"`
	Target            ProductVersion       `json:"target"`
	Strategy          string               `json:"strategy"`
	SupportBoundary   SupportBoundary      `json:"support_boundary"`
	Content           []ContentDisposition `json:"content"`
}

type ContentDisposition struct {
	ItemID      string `json:"item_id"`
	Disposition string `json:"disposition"`
	Mechanism   string `json:"mechanism"`
	Destination string `json:"destination"`
	Rationale   string `json:"rationale"`
}

type Step struct {
	Order       int    `json:"order"`
	ID          string `json:"id"`
	MigrationID string `json:"migration_id"`
	Operation   string `json:"operation"`
	Action      string `json:"action"`
	Gates       []Gate `json:"gates"`
}

type Gate struct {
	Type      string `json:"type"`
	Condition string `json:"condition"`
	Evidence  string `json:"evidence"`
}

// VerifyFiles verifies a candidate artifact. The artifact is always validated
// against the installer schema before protected-input or semantic checks.
func VerifyFiles(planPath, schemaPath, inventoryPath, snapshotPath string) error {
	schemaBytes, err := os.ReadFile(schemaPath)
	if err != nil {
		return fmt.Errorf("schema: read installer schema: %w", err)
	}
	planBytes, err := os.ReadFile(planPath)
	if err != nil {
		return fmt.Errorf("schema: read migration_plan.json: %w", err)
	}
	if err := ValidateAgainstInstallerSchema(schemaBytes, planBytes); err != nil {
		return fmt.Errorf("schema: %w", err)
	}

	if err := verifyDigest("installer_spec.schema.json", schemaBytes, protectedSchemaSHA256); err != nil {
		return err
	}
	inventoryBytes, err := os.ReadFile(inventoryPath)
	if err != nil {
		return fmt.Errorf("protected input: read estate inventory: %w", err)
	}
	if err := verifyDigest("estate_inventory.json", inventoryBytes, protectedInventorySHA256); err != nil {
		return err
	}
	snapshotBytes, err := os.ReadFile(snapshotPath)
	if err != nil {
		return fmt.Errorf("protected input: read compatibility snapshot: %w", err)
	}
	if err := verifyDigest("compatibility_snapshot.json", snapshotBytes, protectedSnapshotSHA256); err != nil {
		return err
	}

	var inventory Inventory
	var snapshot CompatibilitySnapshot
	var plan Plan
	if err := json.Unmarshal(inventoryBytes, &inventory); err != nil {
		return fmt.Errorf("protected input: decode estate inventory: %w", err)
	}
	if err := json.Unmarshal(snapshotBytes, &snapshot); err != nil {
		return fmt.Errorf("protected input: decode compatibility snapshot: %w", err)
	}
	if err := json.Unmarshal(planBytes, &plan); err != nil {
		return fmt.Errorf("semantic: decode plan: %w", err)
	}
	if err := verifyPlan(plan, inventory, snapshot); err != nil {
		return fmt.Errorf("semantic: %w", err)
	}
	return nil
}

func verifyDigest(name string, content []byte, expected string) error {
	digest := sha256.Sum256(content)
	actual := hex.EncodeToString(digest[:])
	if actual != expected {
		return fmt.Errorf("protected input: %s digest mismatch: got %s", name, actual)
	}
	return nil
}

func verifyPlan(plan Plan, inventory Inventory, snapshot CompatibilitySnapshot) error {
	if plan.SchemaVersion != "1.0" {
		return fmt.Errorf("schema version is not 1.0")
	}
	if plan.EstateID != inventory.EstateID {
		return fmt.Errorf("estate_id %q does not match inventory", plan.EstateID)
	}
	if plan.PlanDate != inventory.PlanningDate {
		return fmt.Errorf("plan_date %q does not match inventory planning date", plan.PlanDate)
	}
	if plan.TargetBundle != inventory.TargetBundle || plan.TargetBundle != snapshot.TargetBundle {
		return fmt.Errorf("target bundle does not match inventory and snapshot")
	}
	if err := verifyResearch(plan.Research); err != nil {
		return err
	}

	targets, planned, err := verifyTargets(plan.TargetArchitecture.Components, inventory, snapshot)
	if err != nil {
		return err
	}
	if err := verifyCapacity(plan.TargetArchitecture.CapacityRollup, planned, inventory); err != nil {
		return err
	}

	migrations, err := verifyMigrations(plan.Migrations, inventory, snapshot, targets)
	if err != nil {
		return err
	}
	if err := verifySteps(plan.Steps, migrations, snapshot); err != nil {
		return err
	}
	return nil
}

func verifyResearch(research Research) error {
	requiredClaims := map[string]bool{
		"upgrade_path":          false,
		"content_compatibility": false,
		"product_lifecycle":     false,
		"sizing":                false,
	}
	seenURLs := map[string]bool{}
	for index, source := range research.Sources {
		publisher := strings.ToLower(strings.TrimSpace(source.Publisher))
		if !strings.Contains(publisher, "broadcom") {
			return fmt.Errorf("research source %d is not identified as Broadcom-published", index+1)
		}
		parsed, err := url.Parse(source.URL)
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" {
			return fmt.Errorf("research source %d must use an absolute Broadcom HTTPS URL", index+1)
		}
		host := strings.ToLower(parsed.Hostname())
		if host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com") {
			return fmt.Errorf("research source %d URL is not on a Broadcom-published host", index+1)
		}
		if seenURLs[source.URL] {
			return fmt.Errorf("research source URL %q is repeated", source.URL)
		}
		seenURLs[source.URL] = true
		for _, claim := range source.Supports {
			if _, required := requiredClaims[claim]; required {
				requiredClaims[claim] = true
			}
		}
	}
	for claim, covered := range requiredClaims {
		if !covered {
			return fmt.Errorf("research sources do not cover required claim %q", claim)
		}
	}
	return nil
}

func verifyTargets(components []TargetComponent, inventory Inventory, snapshot CompatibilitySnapshot) (map[string]TargetComponent, Resources, error) {
	if len(components) != len(snapshot.Placement) {
		return nil, Resources{}, fmt.Errorf("target architecture must contain exactly %d components", len(snapshot.Placement))
	}
	byID := make(map[string]TargetComponent, len(components))
	seenProducts := map[string]bool{}
	planned := Resources{}
	for _, component := range components {
		if _, duplicate := byID[component.ID]; duplicate {
			return nil, Resources{}, fmt.Errorf("duplicate target component id %q", component.ID)
		}
		rule, ok := snapshot.Placement[component.Product]
		if !ok {
			return nil, Resources{}, fmt.Errorf("target product %q is not in snapshot", component.Product)
		}
		if seenProducts[component.Product] {
			return nil, Resources{}, fmt.Errorf("target product %q occurs more than once", component.Product)
		}
		seenProducts[component.Product] = true
		if component.Version != snapshot.TargetBundle.Version {
			return nil, Resources{}, fmt.Errorf("%s version %q does not match target bundle", component.Product, component.Version)
		}
		infra := inventory.ManagementInfrastructure
		if component.Site != infra.Site || component.WorkloadDomain != infra.WorkloadDomain || component.Cluster != infra.Cluster || component.Datastore != infra.Datastore {
			return nil, Resources{}, fmt.Errorf("%s placement does not match the available management infrastructure", component.Product)
		}
		if component.Network != rule.Network || component.Profile != rule.Profile || component.NodeCount != rule.NodeCount || component.VCPUPerNode != rule.VCPUPerNode || component.MemoryGiBPerNode != rule.MemoryGiBPerNode || component.StorageGiBPerNode != rule.StorageGiBPerNode {
			return nil, Resources{}, fmt.Errorf("%s sizing does not match pinned profile %q", component.Product, rule.Profile)
		}
		if !sameStrings(component.FaultDomains, infra.FaultDomains) || len(component.FaultDomains) != component.NodeCount {
			return nil, Resources{}, fmt.Errorf("%s nodes must span all inventory fault domains", component.Product)
		}
		byID[component.ID] = component
		planned.VCPU += component.NodeCount * component.VCPUPerNode
		planned.MemoryGiB += component.NodeCount * component.MemoryGiBPerNode
		planned.StorageGiB += component.NodeCount * component.StorageGiBPerNode
	}
	for product := range snapshot.Placement {
		if !seenProducts[product] {
			return nil, Resources{}, fmt.Errorf("missing target product %q", product)
		}
	}
	return byID, planned, nil
}

func verifyCapacity(rollup CapacityRollup, planned Resources, inventory Inventory) error {
	if rollup.Planned != planned {
		return fmt.Errorf("capacity planned roll-up is incorrect: got %+v want %+v", rollup.Planned, planned)
	}
	infra := inventory.ManagementInfrastructure
	remaining := Resources{
		VCPU:       infra.Capacity.VCPU - infra.CurrentlyUsed.VCPU - planned.VCPU,
		MemoryGiB:  infra.Capacity.MemoryGiB - infra.CurrentlyUsed.MemoryGiB - planned.MemoryGiB,
		StorageGiB: infra.Capacity.StorageGiB - infra.CurrentlyUsed.StorageGiB - planned.StorageGiB,
	}
	if rollup.RemainingAfterPlan != remaining {
		return fmt.Errorf("remaining_after_plan is incorrect: got %+v want %+v", rollup.RemainingAfterPlan, remaining)
	}
	if rollup.RequiredReserve != infra.RequiredReserve {
		return fmt.Errorf("required_reserve does not match inventory")
	}
	if remaining.VCPU < infra.RequiredReserve.VCPU || remaining.MemoryGiB < infra.RequiredReserve.MemoryGiB || remaining.StorageGiB < infra.RequiredReserve.StorageGiB {
		return fmt.Errorf("target placement violates the inventory resource reserve")
	}
	return nil
}

func verifyMigrations(migrations []Migration, inventory Inventory, snapshot CompatibilitySnapshot, targets map[string]TargetComponent) (map[string]Migration, error) {
	if len(migrations) != len(inventory.Components) {
		return nil, fmt.Errorf("migrations must cover exactly %d source components", len(inventory.Components))
	}
	rules := map[string]MigrationRule{}
	for _, rule := range snapshot.MigrationCompatibility {
		rules[rule.SourceProduct+"@"+rule.SourceVersion] = rule
	}
	boundaries := map[string]SupportBoundary{}
	for _, boundary := range snapshot.SupportBoundaries {
		boundaries[boundary.Product] = boundary
	}
	inventoryByID := map[string]InventoryComponent{}
	for _, component := range inventory.Components {
		inventoryByID[component.ID] = component
	}

	byID := make(map[string]Migration, len(migrations))
	seenSource := map[string]bool{}
	for _, migration := range migrations {
		if _, duplicate := byID[migration.ID]; duplicate {
			return nil, fmt.Errorf("duplicate migration id %q", migration.ID)
		}
		source, ok := inventoryByID[migration.SourceComponentID]
		if !ok {
			return nil, fmt.Errorf("migration %q names unknown source component %q", migration.ID, migration.SourceComponentID)
		}
		if seenSource[source.ID] {
			return nil, fmt.Errorf("source component %q is migrated more than once", source.ID)
		}
		seenSource[source.ID] = true
		if migration.Source.Product != source.Product || migration.Source.Version != source.Version {
			return nil, fmt.Errorf("migration %q source product/version does not match inventory", migration.ID)
		}
		rule, ok := rules[source.Product+"@"+source.Version]
		if !ok {
			return nil, fmt.Errorf("source %s %s has no compatibility rule", source.Product, source.Version)
		}
		target, ok := targets[migration.TargetComponentID]
		if !ok {
			return nil, fmt.Errorf("migration %q names unknown target component %q", migration.ID, migration.TargetComponentID)
		}
		if migration.Target.Product != target.Product || migration.Target.Version != target.Version || target.Product != rule.TargetProduct || target.Version != rule.TargetVersion {
			return nil, fmt.Errorf("migration %q target does not match target architecture and snapshot", migration.ID)
		}
		if migration.Strategy != rule.Strategy {
			return nil, fmt.Errorf("migration %q strategy %q is incompatible; want %q", migration.ID, migration.Strategy, rule.Strategy)
		}
		if expected := boundaries[source.Product]; migration.SupportBoundary != expected {
			return nil, fmt.Errorf("migration %q support boundary does not match snapshot", migration.ID)
		}
		if err := verifyContent(migration, source, rule); err != nil {
			return nil, err
		}
		byID[migration.ID] = migration
	}
	for _, source := range inventory.Components {
		if !seenSource[source.ID] {
			return nil, fmt.Errorf("source component %q is not covered", source.ID)
		}
	}
	return byID, nil
}

func verifyContent(migration Migration, source InventoryComponent, rule MigrationRule) error {
	if len(migration.Content) != len(source.Items) {
		return fmt.Errorf("migration %q content must cover exactly %d inventory items", migration.ID, len(source.Items))
	}
	items := map[string]InventoryItem{}
	for _, item := range source.Items {
		items[item.ID] = item
	}
	seen := map[string]bool{}
	for _, disposition := range migration.Content {
		item, ok := items[disposition.ItemID]
		if !ok {
			return fmt.Errorf("migration %q references unknown content item %q", migration.ID, disposition.ItemID)
		}
		if seen[item.ID] {
			return fmt.Errorf("migration %q repeats content item %q", migration.ID, item.ID)
		}
		seen[item.ID] = true
		expected, ok := rule.ItemRules[item.Kind]
		if !ok {
			return fmt.Errorf("snapshot has no rule for inventory kind %q", item.Kind)
		}
		if disposition.Disposition != expected.Disposition || disposition.Mechanism != expected.Mechanism {
			return fmt.Errorf("migration %q item %q disposition/mechanism is incompatible", migration.ID, item.ID)
		}
		if disposition.Disposition == "abandon" && disposition.Destination != "none" {
			return fmt.Errorf("migration %q abandoned item %q must use destination none", migration.ID, item.ID)
		}
		if disposition.Disposition != "abandon" && disposition.Destination != migration.TargetComponentID {
			return fmt.Errorf("migration %q item %q must name target component %q as destination", migration.ID, item.ID, migration.TargetComponentID)
		}
	}
	return nil
}

func verifySteps(steps []Step, migrations map[string]Migration, snapshot CompatibilitySnapshot) error {
	rules := map[string]MigrationRule{}
	for _, rule := range snapshot.MigrationCompatibility {
		rules[rule.SourceProduct+"@"+rule.SourceVersion] = rule
	}
	stepIDs := map[string]bool{}
	positions := map[string]map[string][]int{}
	preparePositions := []int{}
	for index, step := range steps {
		if step.Order != index+1 {
			return fmt.Errorf("steps must be globally ordered 1..N; index %d has order %d", index, step.Order)
		}
		if stepIDs[step.ID] {
			return fmt.Errorf("duplicate step id %q", step.ID)
		}
		stepIDs[step.ID] = true
		hasEntry, hasExit := false, false
		for _, gate := range step.Gates {
			hasEntry = hasEntry || gate.Type == "entry"
			hasExit = hasExit || gate.Type == "exit"
		}
		if !hasEntry || !hasExit {
			return fmt.Errorf("step %q must have concrete entry and exit gates", step.ID)
		}
		if step.Operation == snapshot.FleetPrerequisite.Operation {
			for _, prerequisite := range []string{
				snapshot.FleetPrerequisite.SourceLifecycleProduct,
				snapshot.FleetPrerequisite.SourceVersion,
				snapshot.FleetPrerequisite.MinimumPatch,
			} {
				if !strings.Contains(strings.ToLower(step.Action), strings.ToLower(prerequisite)) {
					return fmt.Errorf("prepare_fleet action must identify prerequisite %q", prerequisite)
				}
			}
			preparePositions = append(preparePositions, index)
			continue
		}
		migration, ok := migrations[step.MigrationID]
		if !ok {
			return fmt.Errorf("step %q names unknown migration %q", step.ID, step.MigrationID)
		}
		rule := rules[migration.Source.Product+"@"+migration.Source.Version]
		allowed := false
		for _, operation := range rule.RequiredOperations {
			allowed = allowed || operation == step.Operation
		}
		if !allowed {
			return fmt.Errorf("migration %q operation %s is not part of its pinned strategy", step.MigrationID, step.Operation)
		}
		if positions[step.MigrationID] == nil {
			positions[step.MigrationID] = map[string][]int{}
		}
		positions[step.MigrationID][step.Operation] = append(positions[step.MigrationID][step.Operation], index)
	}
	if len(preparePositions) != 1 || preparePositions[0] != 0 {
		return fmt.Errorf("exactly one prepare_fleet step must be first")
	}

	for migrationID, migration := range migrations {
		rule := rules[migration.Source.Product+"@"+migration.Source.Version]
		previous := preparePositions[0]
		for _, operation := range rule.RequiredOperations {
			found := positions[migrationID][operation]
			if len(found) != 1 {
				return fmt.Errorf("migration %q requires exactly one %s step", migrationID, operation)
			}
			if found[0] <= previous {
				return fmt.Errorf("migration %q operation %s is out of supported order", migrationID, operation)
			}
			previous = found[0]
		}
		if !rule.DirectInPlaceSupported && len(positions[migrationID]["upgrade_in_place"]) != 0 {
			return fmt.Errorf("migration %q uses unsupported direct in-place upgrade", migrationID)
		}
	}
	return nil
}

func sameStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	a := append([]string(nil), left...)
	b := append([]string(nil), right...)
	sort.Strings(a)
	sort.Strings(b)
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
