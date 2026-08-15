// Package verify performs deterministic acceptance checks for the migration plan.
// It never performs network access and does not evaluate the research log beyond
// the installer's schema validation.
package verify

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"reflect"
	"sort"

	"example.com/northstar/vcf-migration/internal/jsonschema"
)

// Failure identifies the validation stage that rejected a plan.
type Failure struct {
	Stage string
	Err   error
}

func (f *Failure) Error() string { return fmt.Sprintf("%s validation: %v", f.Stage, f.Err) }
func (f *Failure) Unwrap() error { return f.Err }

// Validate checks schema conformance before it decodes or examines either
// grading fixture. This ordering is part of the installer contract.
func Validate(planData, schemaData, inventoryData, snapshotData []byte) error {
	schema, err := jsonschema.Compile(schemaData)
	if err != nil {
		return &Failure{Stage: "schema", Err: fmt.Errorf("installer schema: %w", err)}
	}
	if err := schema.Validate(planData); err != nil {
		return &Failure{Stage: "schema", Err: err}
	}

	var plan plan
	if err := decodeStrict(planData, &plan); err != nil {
		return &Failure{Stage: "semantic", Err: fmt.Errorf("decode schema-valid plan: %w", err)}
	}
	var inventory inventory
	if err := json.Unmarshal(inventoryData, &inventory); err != nil {
		return &Failure{Stage: "fixture", Err: fmt.Errorf("inventory: %w", err)}
	}
	var snapshot snapshot
	if err := json.Unmarshal(snapshotData, &snapshot); err != nil {
		return &Failure{Stage: "fixture", Err: fmt.Errorf("compatibility snapshot: %w", err)}
	}
	if err := checkPlan(plan, inventory, snapshot); err != nil {
		return &Failure{Stage: "semantic", Err: err}
	}
	return nil
}

func decodeStrict(data []byte, destination any) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); err == nil {
		return fmt.Errorf("multiple JSON values")
	} else if err != io.EOF {
		return err
	}
	return nil
}

type plan struct {
	SchemaVersion           string          `json:"schema_version"`
	DesignID                string          `json:"design_id"`
	InventoryID             string          `json:"inventory_id"`
	CompatibilitySnapshotID string          `json:"compatibility_snapshot_id"`
	Research                json.RawMessage `json:"research"`
	Placements              []placement     `json:"placements"`
	Migrations              []migration     `json:"migrations"`
	Steps                   []step          `json:"steps"`
}

type inventory struct {
	InventoryID string             `json:"inventory_id"`
	Products    []inventoryProduct `json:"products"`
}

type inventoryProduct struct {
	ID      string `json:"id"`
	Product string `json:"product"`
	Version string `json:"version"`
}

type snapshot struct {
	SnapshotID  string           `json:"snapshot_id"`
	Sources     []snapshotSource `json:"sources"`
	Placements  []placement      `json:"placements"`
	OrderedStep []snapshotStep   `json:"ordered_steps"`
	Purpose     string           `json:"purpose"`
	Target      string           `json:"target_version"`
}

type endpoint struct {
	Product   string `json:"product,omitempty"`
	Component string `json:"component,omitempty"`
	Version   string `json:"version"`
}

type carryItem struct {
	Item string `json:"item"`
	Mode string `json:"mode"`
}

type migration struct {
	InventoryProductID string      `json:"inventory_product_id"`
	Source             endpoint    `json:"source"`
	Target             endpoint    `json:"target"`
	EOGS               string      `json:"end_of_general_support"`
	SupportedPath      []string    `json:"supported_path"`
	CarryForward       []carryItem `json:"carry_forward"`
	Abandon            []string    `json:"abandon"`
	StepIDs            []string    `json:"step_ids"`
}

type snapshotSource struct {
	InventoryProductID string      `json:"inventory_product_id"`
	Source             endpoint    `json:"source"`
	Target             endpoint    `json:"target"`
	EOGS               string      `json:"eogs"`
	SupportedPath      []string    `json:"supported_path"`
	CarryForward       []carryItem `json:"carry_forward"`
	Abandon            []string    `json:"abandon"`
	StepIDs            []string    `json:"step_ids"`
}

type resources struct {
	VCPU        int `json:"vcpu"`
	MemoryGiB   int `json:"memory_gib"`
	DataDiskTiB int `json:"data_disk_tib"`
}

type capacityBasis struct {
	Metric    string `json:"metric"`
	Required  int    `json:"required"`
	Supported int    `json:"supported"`
}

type placement struct {
	Component       string          `json:"component"`
	Version         string          `json:"version"`
	Domain          string          `json:"domain"`
	Cluster         string          `json:"cluster"`
	Network         string          `json:"network"`
	DeploymentModel string          `json:"deployment_model"`
	Size            string          `json:"size"`
	Quantity        int             `json:"quantity"`
	PerNode         *resources      `json:"per_node,omitempty"`
	Basis           []capacityBasis `json:"basis"`
}

type gate struct {
	ID       string `json:"id"`
	Evidence string `json:"evidence"`
}

type step struct {
	Order      int    `json:"order"`
	ID         string `json:"id"`
	Action     string `json:"action"`
	EntryGates []gate `json:"entry_gates"`
	ExitGates  []gate `json:"exit_gates"`
}

type snapshotStep struct {
	ID                 string   `json:"id"`
	RequiredEntryGates []string `json:"required_entry_gates"`
	RequiredExitGates  []string `json:"required_exit_gates"`
}

func checkPlan(actual plan, inventory inventory, snapshot snapshot) error {
	if actual.SchemaVersion != "1.0" {
		return fmt.Errorf("schema_version = %q, want 1.0", actual.SchemaVersion)
	}
	if actual.InventoryID != inventory.InventoryID {
		return fmt.Errorf("inventory_id = %q, want %q", actual.InventoryID, inventory.InventoryID)
	}
	if actual.CompatibilitySnapshotID != snapshot.SnapshotID {
		return fmt.Errorf("compatibility_snapshot_id = %q, want %q", actual.CompatibilitySnapshotID, snapshot.SnapshotID)
	}
	if err := checkInventoryMatchesSnapshot(inventory, snapshot); err != nil {
		return fmt.Errorf("pinned inputs disagree: %w", err)
	}
	if err := checkMigrations(actual.Migrations, snapshot.Sources); err != nil {
		return err
	}
	if err := checkPlacements(actual.Placements, snapshot.Placements); err != nil {
		return err
	}
	if err := checkSteps(actual.Steps, snapshot.OrderedStep); err != nil {
		return err
	}
	return nil
}

func checkInventoryMatchesSnapshot(inventory inventory, snapshot snapshot) error {
	products := make(map[string]inventoryProduct, len(inventory.Products))
	for _, product := range inventory.Products {
		if _, duplicate := products[product.ID]; duplicate {
			return fmt.Errorf("duplicate inventory product %q", product.ID)
		}
		products[product.ID] = product
	}
	if len(products) != len(snapshot.Sources) {
		return fmt.Errorf("inventory has %d products but snapshot has %d", len(products), len(snapshot.Sources))
	}
	for _, source := range snapshot.Sources {
		product, ok := products[source.InventoryProductID]
		if !ok {
			return fmt.Errorf("snapshot product %q is absent from inventory", source.InventoryProductID)
		}
		if product.Product != source.Source.Product || product.Version != source.Source.Version {
			return fmt.Errorf("source identity for %q does not match inventory", source.InventoryProductID)
		}
	}
	return nil
}

func checkMigrations(actual []migration, expected []snapshotSource) error {
	byID := make(map[string]migration, len(actual))
	for _, candidate := range actual {
		if _, duplicate := byID[candidate.InventoryProductID]; duplicate {
			return fmt.Errorf("duplicate migration for %q", candidate.InventoryProductID)
		}
		byID[candidate.InventoryProductID] = candidate
	}
	if len(byID) != len(expected) {
		return fmt.Errorf("migrations cover %d products, want %d", len(byID), len(expected))
	}
	for _, want := range expected {
		got, ok := byID[want.InventoryProductID]
		if !ok {
			return fmt.Errorf("missing migration for %q", want.InventoryProductID)
		}
		if got.Source != want.Source || got.Target != want.Target || got.EOGS != want.EOGS {
			return fmt.Errorf("migration endpoints or support boundary for %q do not match snapshot", want.InventoryProductID)
		}
		if !reflect.DeepEqual(got.SupportedPath, want.SupportedPath) {
			return fmt.Errorf("supported path for %q = %v, want %v", want.InventoryProductID, got.SupportedPath, want.SupportedPath)
		}
		if !equalCarrySet(got.CarryForward, want.CarryForward) {
			return fmt.Errorf("carry-forward content for %q does not match snapshot", want.InventoryProductID)
		}
		if !equalStringSet(got.Abandon, want.Abandon) {
			return fmt.Errorf("abandoned content for %q does not match snapshot", want.InventoryProductID)
		}
		if !reflect.DeepEqual(got.StepIDs, want.StepIDs) {
			return fmt.Errorf("step_ids for %q = %v, want %v", want.InventoryProductID, got.StepIDs, want.StepIDs)
		}
	}
	return nil
}

func checkPlacements(actual, expected []placement) error {
	key := func(item placement) string { return item.Component + "\x00" + item.Domain }
	got := make(map[string]placement, len(actual))
	for _, item := range actual {
		placementKey := key(item)
		if _, duplicate := got[placementKey]; duplicate {
			return fmt.Errorf("duplicate placement for %q in %q", item.Component, item.Domain)
		}
		for _, basis := range item.Basis {
			if basis.Supported < basis.Required {
				return fmt.Errorf("placement %q in %q has insufficient %s capacity", item.Component, item.Domain, basis.Metric)
			}
		}
		got[placementKey] = item
	}
	if len(got) != len(expected) {
		return fmt.Errorf("placements contain %d components, want %d", len(got), len(expected))
	}
	for _, want := range expected {
		item, ok := got[key(want)]
		if !ok {
			return fmt.Errorf("missing placement for %q in %q", want.Component, want.Domain)
		}
		if !reflect.DeepEqual(item, want) {
			return fmt.Errorf("placement or sizing for %q in %q does not match snapshot", want.Component, want.Domain)
		}
	}
	return nil
}

func checkSteps(actual []step, expected []snapshotStep) error {
	if len(actual) != len(expected) {
		return fmt.Errorf("steps contain %d entries, want %d", len(actual), len(expected))
	}
	seen := make(map[string]bool, len(actual))
	for index, want := range expected {
		got := actual[index]
		if got.Order != index+1 {
			return fmt.Errorf("step %q order = %d, want %d", got.ID, got.Order, index+1)
		}
		if got.ID != want.ID {
			return fmt.Errorf("step %d id = %q, want %q", index+1, got.ID, want.ID)
		}
		if seen[got.ID] {
			return fmt.Errorf("duplicate step id %q", got.ID)
		}
		seen[got.ID] = true
		if !equalStringSet(gateIDs(got.EntryGates), want.RequiredEntryGates) {
			return fmt.Errorf("entry gates for step %q do not match snapshot", got.ID)
		}
		if !equalStringSet(gateIDs(got.ExitGates), want.RequiredExitGates) {
			return fmt.Errorf("exit gates for step %q do not match snapshot", got.ID)
		}
	}
	return nil
}

func gateIDs(gates []gate) []string {
	ids := make([]string, len(gates))
	for index, item := range gates {
		ids[index] = item.ID
	}
	return ids
}

func equalCarrySet(left, right []carryItem) bool {
	canonical := func(items []carryItem) []string {
		values := make([]string, len(items))
		for index, item := range items {
			values[index] = item.Item + "\x00" + item.Mode
		}
		sort.Strings(values)
		return values
	}
	return reflect.DeepEqual(canonical(left), canonical(right))
}

func equalStringSet(left, right []string) bool {
	left = append([]string(nil), left...)
	right = append([]string(nil), right...)
	sort.Strings(left)
	sort.Strings(right)
	return reflect.DeepEqual(left, right)
}
