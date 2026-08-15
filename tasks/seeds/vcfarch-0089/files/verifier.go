package vcfplan

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
)

const (
	installerSchemaPath = "specifications/vcf-installer/vcf-installer-openapi.json"
	planSchemaPath      = "migration-plan.schema.json"
	planPath            = "migration-plan.json"
	estatePath          = "fixtures/estate.json"
	snapshotPath        = "fixtures/compatibility-snapshot.json"
)

type StageError struct {
	Stage string
	Err   error
}

func (e *StageError) Error() string { return e.Stage + ": " + e.Err.Error() }
func (e *StageError) Unwrap() error { return e.Err }

type Estate struct {
	EstateID         string `json:"estateId"`
	Scope            string `json:"scope"`
	VCFVersion       string `json:"vcfVersion"`
	TargetVCFVersion string `json:"targetVcfVersion"`
	Components       []struct {
		ID      string `json:"id"`
		Name    string `json:"name"`
		Version string `json:"version"`
		Role    string `json:"role"`
	} `json:"components"`
	TargetSpecInputs struct {
		SDDCID            string   `json:"sddcId"`
		WorkflowType      string   `json:"workflowType"`
		VcenterHostname   string   `json:"vcenterHostname"`
		DNSSubdomain      string   `json:"dnsSubdomain"`
		NameServers       []string `json:"nameServers"`
		ManagementNetwork struct {
			NetworkType string `json:"networkType"`
			VLANID      int    `json:"vlanId"`
			Subnet      string `json:"subnet"`
			Gateway     string `json:"gateway"`
			SubnetMask  string `json:"subnetMask"`
			MTU         int    `json:"mtu"`
		} `json:"managementNetwork"`
	} `json:"targetSpecInputs"`
}

type CompatibilitySnapshot struct {
	SnapshotVersion  string `json:"snapshotVersion"`
	EstateVCFVersion string `json:"estateVcfVersion"`
	TargetVCFVersion string `json:"targetVcfVersion"`
	InstallerSchema  struct {
		Repository string `json:"repository"`
		Tag        string `json:"tag"`
		Component  string `json:"component"`
		SHA256     string `json:"sha256"`
	} `json:"installerSchema"`
	Components []SnapshotComponent `json:"components"`
}

type SnapshotComponent struct {
	ID            string   `json:"id"`
	TargetProduct string   `json:"targetProduct"`
	TargetVersion string   `json:"targetVersion"`
	Transition    string   `json:"transition"`
	OrderRank     int      `json:"orderRank"`
	RequiredGates []string `json:"requiredGates"`
	DependsOn     []string `json:"dependsOn"`
	AllowedEdges  []Edge   `json:"allowedEdges"`
}

type Edge struct {
	FromVersion   string           `json:"fromVersion"`
	ToVersion     string           `json:"toVersion"`
	Action        string           `json:"action"`
	OrderRank     int              `json:"orderRank"`
	RequiredGates []string         `json:"requiredGates"`
	DependsOn     []EdgeDependency `json:"dependsOn"`
}

type EdgeDependency struct {
	ComponentID string `json:"componentId"`
	ToVersion   string `json:"toVersion"`
}

type Plan struct {
	APIVersion string `json:"apiVersion"`
	Kind       string `json:"kind"`
	Metadata   struct {
		EstateID         string `json:"estateId"`
		SourceVCFVersion string `json:"sourceVcfVersion"`
		TargetVCFVersion string `json:"targetVcfVersion"`
	} `json:"metadata"`
	ConsultedSources json.RawMessage `json:"consultedSources,omitempty"`
	TargetSDDCSpec   map[string]any  `json:"targetSddcSpec"`
	Components       []PlanComponent `json:"components"`
	Steps            []PlanStep      `json:"steps"`
}

type PlanComponent struct {
	ID             string `json:"id"`
	Name           string `json:"name"`
	CurrentVersion string `json:"currentVersion"`
	Target         struct {
		Product    string `json:"product"`
		Version    string `json:"version"`
		Transition string `json:"transition"`
	} `json:"target"`
	Gates []string `json:"gates"`
}

type PlanStep struct {
	Order       int      `json:"order"`
	ID          string   `json:"id"`
	ComponentID string   `json:"componentId"`
	Action      string   `json:"action"`
	FromVersion string   `json:"fromVersion"`
	ToVersion   string   `json:"toVersion"`
	Gates       []string `json:"gates"`
	DependsOn   []string `json:"dependsOn"`
}

// Verify validates the submitted architecture. Artifact JSON decoding is the
// only operation before the embedded desired state is validated against the
// pinned installer specification's own SddcSpec schema.
func Verify(root string) error {
	planDocument, err := readJSONMap(filepath.Join(root, planPath))
	if err != nil {
		return &StageError{Stage: "artifact-decode", Err: err}
	}

	openAPIBytes, err := os.ReadFile(filepath.Join(root, installerSchemaPath))
	if err != nil {
		return &StageError{Stage: "installer-schema", Err: err}
	}
	openAPI, err := decodeJSONMap(openAPIBytes)
	if err != nil {
		return &StageError{Stage: "installer-schema", Err: err}
	}
	targetSpec := planDocument["targetSddcSpec"]
	if err := validateOpenAPIComponent(openAPI, "SddcSpec", targetSpec); err != nil {
		return &StageError{Stage: "installer-schema", Err: err}
	}

	planSchema, err := readJSONMap(filepath.Join(root, planSchemaPath))
	if err != nil {
		return &StageError{Stage: "plan-schema", Err: err}
	}
	if err := ValidateJSONSchema(planSchema, planSchema, planDocument); err != nil {
		return &StageError{Stage: "plan-schema", Err: err}
	}

	var plan Plan
	if err := remarshal(planDocument, &plan); err != nil {
		return &StageError{Stage: "plan-schema", Err: err}
	}
	var estate Estate
	if err := readJSON(filepath.Join(root, estatePath), &estate); err != nil {
		return &StageError{Stage: "fixture", Err: err}
	}
	var snapshot CompatibilitySnapshot
	if err := readJSON(filepath.Join(root, snapshotPath), &snapshot); err != nil {
		return &StageError{Stage: "snapshot", Err: err}
	}
	actualHash := sha256.Sum256(openAPIBytes)
	if hex.EncodeToString(actualHash[:]) != snapshot.InstallerSchema.SHA256 {
		return &StageError{Stage: "snapshot", Err: fmt.Errorf("installer schema checksum differs from pinned tag %s", snapshot.InstallerSchema.Tag)}
	}
	if err := validatePlanSemantics(plan, estate, snapshot); err != nil {
		return &StageError{Stage: "compatibility", Err: err}
	}
	return nil
}

func validateOpenAPIComponent(openAPI map[string]any, component string, value any) error {
	components, ok := openAPI["components"].(map[string]any)
	if !ok {
		return fmt.Errorf("OpenAPI document has no components object")
	}
	schemas, ok := components["schemas"].(map[string]any)
	if !ok {
		return fmt.Errorf("OpenAPI document has no components.schemas object")
	}
	schema, ok := schemas[component].(map[string]any)
	if !ok {
		return fmt.Errorf("OpenAPI component %q is absent", component)
	}
	return ValidateJSONSchema(openAPI, schema, value)
}

func validatePlanSemantics(plan Plan, estate Estate, snapshot CompatibilitySnapshot) error {
	if estate.VCFVersion != snapshot.EstateVCFVersion || estate.TargetVCFVersion != snapshot.TargetVCFVersion {
		return fmt.Errorf("estate and compatibility snapshot VCF versions disagree")
	}
	if plan.Metadata.EstateID != estate.EstateID || plan.Metadata.SourceVCFVersion != estate.VCFVersion || plan.Metadata.TargetVCFVersion != estate.TargetVCFVersion {
		return fmt.Errorf("plan metadata does not identify the fixture estate and VCF versions")
	}
	if err := validateTargetSpecInputs(plan.TargetSDDCSpec, estate, snapshot.TargetVCFVersion); err != nil {
		return err
	}

	inventory := make(map[string]struct{ Name, Version string }, len(estate.Components))
	for _, component := range estate.Components {
		if _, duplicate := inventory[component.ID]; duplicate {
			return fmt.Errorf("duplicate inventory component %q", component.ID)
		}
		inventory[component.ID] = struct{ Name, Version string }{component.Name, component.Version}
	}
	authority := make(map[string]SnapshotComponent, len(snapshot.Components))
	for _, component := range snapshot.Components {
		authority[component.ID] = component
	}
	if len(inventory) != len(authority) {
		return fmt.Errorf("inventory and snapshot component counts differ")
	}

	plannedComponents := make(map[string]PlanComponent, len(plan.Components))
	for _, component := range plan.Components {
		if _, duplicate := plannedComponents[component.ID]; duplicate {
			return fmt.Errorf("component %q is named more than once", component.ID)
		}
		current, exists := inventory[component.ID]
		if !exists {
			return fmt.Errorf("component %q is not in the estate", component.ID)
		}
		expected, exists := authority[component.ID]
		if !exists {
			return fmt.Errorf("component %q has no compatibility authority", component.ID)
		}
		if component.Name != current.Name || component.CurrentVersion != current.Version {
			return fmt.Errorf("component %q does not preserve its inventory name and version", component.ID)
		}
		if component.Target.Product != expected.TargetProduct || component.Target.Version != expected.TargetVersion || component.Target.Transition != expected.Transition {
			return fmt.Errorf("component %q has a target outside the pinned snapshot", component.ID)
		}
		if !sameStrings(component.Gates, expected.RequiredGates) {
			return fmt.Errorf("component %q gates differ from the pinned snapshot", component.ID)
		}
		plannedComponents[component.ID] = component
	}
	if len(plannedComponents) != len(inventory) {
		return fmt.Errorf("plan must name every inventory component exactly once")
	}

	expectedStepCount := 0
	for _, component := range authority {
		expectedStepCount += len(component.AllowedEdges)
	}
	if len(plan.Steps) != expectedStepCount {
		return fmt.Errorf("plan must realize every edge in the pinned component paths")
	}
	type matchedStep struct {
		step PlanStep
		edge Edge
	}
	matchedByEdge := make(map[string]matchedStep, len(plan.Steps))
	stepsByComponent := make(map[string][]PlanStep, len(inventory))
	stepIDs := make(map[string]struct{}, len(plan.Steps))
	lastRank := -1
	for index, step := range plan.Steps {
		if step.Order != index+1 {
			return fmt.Errorf("steps must have contiguous order values beginning at 1")
		}
		if _, duplicate := stepIDs[step.ID]; duplicate {
			return fmt.Errorf("step id %q is duplicated", step.ID)
		}
		expected, exists := authority[step.ComponentID]
		if !exists {
			return fmt.Errorf("step %q names unknown component %q", step.ID, step.ComponentID)
		}
		matchedEdge, exists := findAllowedEdge(expected.AllowedEdges, step)
		if !exists {
			return fmt.Errorf("step %q uses an unsupported version edge", step.ID)
		}
		key := edgeKey(step.ComponentID, matchedEdge.ToVersion)
		if _, duplicate := matchedByEdge[key]; duplicate {
			return fmt.Errorf("step %q duplicates a pinned path edge", step.ID)
		}
		if matchedEdge.OrderRank < lastRank {
			return fmt.Errorf("step %q violates the pinned component order", step.ID)
		}
		lastRank = matchedEdge.OrderRank
		if !sameStrings(step.Gates, matchedEdge.RequiredGates) {
			return fmt.Errorf("step %q gates differ from the pinned snapshot", step.ID)
		}
		matchedByEdge[key] = matchedStep{step: step, edge: matchedEdge}
		stepsByComponent[step.ComponentID] = append(stepsByComponent[step.ComponentID], step)
		stepIDs[step.ID] = struct{}{}
	}

	for componentID, expected := range authority {
		componentSteps := stepsByComponent[componentID]
		if len(componentSteps) != len(expected.AllowedEdges) {
			return fmt.Errorf("component %q does not realize its complete pinned path", componentID)
		}
		currentVersion := inventory[componentID].Version
		for _, step := range componentSteps {
			if step.FromVersion != currentVersion {
				return fmt.Errorf("component %q path is not contiguous from inventory version %q", componentID, currentVersion)
			}
			currentVersion = step.ToVersion
		}
		if currentVersion != expected.TargetVersion {
			return fmt.Errorf("component %q path does not reach target %q", componentID, expected.TargetVersion)
		}
	}

	for _, submittedStep := range plan.Steps {
		matched := matchedByEdge[edgeKey(submittedStep.ComponentID, submittedStep.ToVersion)]
		requiredStepIDs := make([]string, 0, len(matched.edge.DependsOn))
		for _, dependency := range matched.edge.DependsOn {
			predecessor, exists := matchedByEdge[edgeKey(dependency.ComponentID, dependency.ToVersion)]
			if !exists || predecessor.step.Order >= matched.step.Order {
				return fmt.Errorf("step %q does not follow pinned dependency %s@%s", matched.step.ID, dependency.ComponentID, dependency.ToVersion)
			}
			requiredStepIDs = append(requiredStepIDs, predecessor.step.ID)
		}
		if !sameStrings(matched.step.DependsOn, requiredStepIDs) {
			return fmt.Errorf("step %q dependency ids do not express its pinned gates", matched.step.ID)
		}
	}
	return nil
}

func validateTargetSpecInputs(spec map[string]any, estate Estate, targetVersion string) error {
	input := estate.TargetSpecInputs
	if spec["sddcId"] != input.SDDCID || spec["workflowType"] != input.WorkflowType || spec["version"] != targetVersion {
		return fmt.Errorf("targetSddcSpec does not retain the fixture identity, workflow, and target version")
	}
	vcenter, _ := spec["vcenterSpec"].(map[string]any)
	if vcenter["vcenterHostname"] != input.VcenterHostname || vcenter["useExistingDeployment"] != true || vcenter["version"] != targetVersion {
		return fmt.Errorf("targetSddcSpec does not retain the existing target vCenter")
	}
	dns, _ := spec["dnsSpec"].(map[string]any)
	if dns["subdomain"] != input.DNSSubdomain || !sameJSONStrings(dns["nameservers"], input.NameServers) {
		return fmt.Errorf("targetSddcSpec DNS differs from the fixture")
	}
	networks, _ := spec["networkSpecs"].([]any)
	if len(networks) != 1 {
		return fmt.Errorf("targetSddcSpec must contain the fixture management network")
	}
	network, _ := networks[0].(map[string]any)
	expected := input.ManagementNetwork
	if network["networkType"] != expected.NetworkType || !numberEquals(network["vlanId"], expected.VLANID) || network["subnet"] != expected.Subnet || network["gateway"] != expected.Gateway || network["subnetMask"] != expected.SubnetMask || !numberEquals(network["mtu"], expected.MTU) {
		return fmt.Errorf("targetSddcSpec management network differs from the fixture")
	}
	return nil
}

func findAllowedEdge(edges []Edge, step PlanStep) (Edge, bool) {
	for _, edge := range edges {
		if edge.FromVersion == step.FromVersion && edge.ToVersion == step.ToVersion && edge.Action == step.Action {
			return edge, true
		}
	}
	return Edge{}, false
}

func edgeKey(componentID, toVersion string) string {
	return componentID + "\x00" + toVersion
}

func sameStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	a, b := append([]string(nil), left...), append([]string(nil), right...)
	sort.Strings(a)
	sort.Strings(b)
	for index := range a {
		if a[index] != b[index] {
			return false
		}
	}
	return true
}

func sameJSONStrings(value any, expected []string) bool {
	raw, ok := value.([]any)
	if !ok || len(raw) != len(expected) {
		return false
	}
	actual := make([]string, len(raw))
	for index, entry := range raw {
		actual[index], ok = entry.(string)
		if !ok {
			return false
		}
	}
	return sameStrings(actual, expected)
}

func numberEquals(value any, expected int) bool {
	number, ok := schemaNumber(value)
	return ok && number == float64(expected)
}

func readJSONMap(path string) (map[string]any, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return decodeJSONMap(data)
}

func decodeJSONMap(data []byte) (map[string]any, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var document map[string]any
	if err := decoder.Decode(&document); err != nil {
		return nil, err
	}
	if decoder.More() {
		return nil, fmt.Errorf("multiple JSON documents are not allowed")
	}
	return document, nil
}

func readJSON(path string, target any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	return decoder.Decode(target)
}

func remarshal(value any, target any) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	return decoder.Decode(target)
}
