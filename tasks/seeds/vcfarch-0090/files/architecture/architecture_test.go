package architecture_test

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"sync"
	"testing"

	"vcfarch/architecture"
)

const (
	installerSpecSHA256 = "9295f4d07b46343600da2e4a609e166ec48feabcf2189bc20c2f90c9f4174b72"
	estateSHA256        = "c47deaeaf34e0d070617b97fad8889b0f296ee3006bcef0a6b3de9273e515328"
	snapshotSHA256      = "1290080ee00ebc149a33b337a71c618cb18ea50611087003ce2baeb7f14bd8b5"
)

type estateFixture struct {
	SchemaVersion string            `json:"schemaVersion"`
	EstateID      string            `json:"estateId"`
	VCFVersion    string            `json:"vcfVersion"`
	Components    []estateComponent `json:"components"`
	Workload      workloadCluster   `json:"workloadCluster"`
}

type estateComponent struct {
	ID      string `json:"id"`
	Name    string `json:"name"`
	Domain  string `json:"domain"`
	Version string `json:"version"`
}

type workloadCluster struct {
	StorageArchitecture      string `json:"storageArchitecture"`
	HostCount                int    `json:"hostCount"`
	RAIDLayout               string `json:"raidLayout"`
	FailuresToTolerate       int    `json:"failuresToTolerate"`
	StorageDecisionCriterion string `json:"storageDecisionCriterion"`
	VsanNetwork              struct {
		VLANID                  int `json:"vlanId"`
		MTU                     int `json:"mtu"`
		AggregateNICGbpsPerHost int `json:"aggregateNicGbpsPerHost"`
	} `json:"vsanNetwork"`
	HardwareCandidates struct {
		ExistingOSA hardwareCandidate `json:"existingOsa"`
		NewESA      hardwareCandidate `json:"newEsa"`
	} `json:"hardwareCandidates"`
}

type hardwareCandidate struct {
	HostCount    int  `json:"hostCount"`
	OSACertified bool `json:"osaCertified"`
	ESACertified bool `json:"esaCertified"`
}

type compatibilitySnapshot struct {
	SchemaVersion        string                `json:"schemaVersion"`
	SourceVCFVersion     string                `json:"sourceVcfVersion"`
	TargetVCFVersion     string                `json:"targetVcfVersion"`
	GateCatalog          map[string]string     `json:"gateCatalog"`
	ComponentTransitions []componentTransition `json:"componentTransitions"`
	StorageOptions       []storageOption       `json:"storageOptions"`
}

type componentTransition struct {
	Order         int      `json:"order"`
	ComponentID   string   `json:"componentId"`
	Component     string   `json:"component"`
	From          string   `json:"from"`
	To            string   `json:"to"`
	Action        string   `json:"action"`
	RequiredGates []string `json:"requiredGates"`
}

type storageOption struct {
	Architecture                   string `json:"architecture"`
	SourceArchitecture             string `json:"sourceArchitecture"`
	MigrationMode                  string `json:"migrationMode"`
	MinimumHostCount               int    `json:"minimumHostCount"`
	RAIDLayout                     string `json:"raidLayout"`
	FailuresToTolerate             int    `json:"failuresToTolerate"`
	MinimumAggregateNICGbpsPerHost int    `json:"minimumAggregateNicGbpsPerHost"`
}

func TestArchitecture(t *testing.T) {
	plan := architecture.Build()
	verifyResearchRecord(t)

	// The installer schema validation is intentionally the first artifact check.
	specBytes := readProtected(t, repoPath("specifications", "vcf-installer", "vcf-installer-openapi.json"), installerSpecSHA256)
	var openAPI map[string]any
	mustJSON(t, specBytes, &openAPI)
	sddcSchema, err := objectAt(openAPI, "components", "schemas", "SddcSpec")
	if err != nil {
		t.Fatalf("installer specification does not expose SddcSpec: %v", err)
	}
	var targetSddcSpec any
	if err := json.Unmarshal(plan.TargetSddcSpec, &targetSddcSpec); err != nil {
		t.Fatalf("TargetSddcSpec must be JSON before it can be schema-validated: %v", err)
	}
	if err := validateOpenAPISchema(openAPI, sddcSchema, targetSddcSpec, "TargetSddcSpec"); err != nil {
		t.Fatalf("TargetSddcSpec does not validate as the installer specification's SddcSpec: %v", err)
	}

	var estate estateFixture
	mustJSON(t, readProtected(t, repoPath("fixtures", "estate.json"), estateSHA256), &estate)
	var snapshot compatibilitySnapshot
	mustJSON(t, readProtected(t, repoPath("fixtures", "compatibility-snapshot.json"), snapshotSHA256), &snapshot)

	checks := []struct {
		name string
		fn   func() error
	}{
		{name: "estate identity and supported endpoint", fn: func() error {
			if plan.SchemaVersion != "1.0" {
				return fmt.Errorf("schemaVersion = %q, want 1.0", plan.SchemaVersion)
			}
			if plan.EstateID != estate.EstateID {
				return fmt.Errorf("estateId = %q, want %q", plan.EstateID, estate.EstateID)
			}
			if plan.SourceVCFVersion != estate.VCFVersion || plan.SourceVCFVersion != snapshot.SourceVCFVersion {
				return fmt.Errorf("source VCF version %q does not match fixture and snapshot", plan.SourceVCFVersion)
			}
			if plan.TargetVCFVersion != snapshot.TargetVCFVersion {
				return fmt.Errorf("target VCF version = %q, want %q", plan.TargetVCFVersion, snapshot.TargetVCFVersion)
			}
			return nil
		}},
		{name: "ordered component-complete migration", fn: func() error {
			return verifyComponentPlan(plan, estate, snapshot)
		}},
		{name: "storage architecture decision", fn: func() error {
			return verifyStorageDecision(plan.StorageDecision, estate.Workload, snapshot.StorageOptions)
		}},
		{name: "installer design matches storage decision", fn: func() error {
			return verifySddcSemantics(targetSddcSpec, plan, estate)
		}},
		{name: "fresh values for concurrent callers", fn: func() error {
			return verifyFreshConcurrentPlans(plan)
		}},
	}

	for _, check := range checks {
		check := check
		t.Run(check.name, func(t *testing.T) {
			if err := check.fn(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func verifyComponentPlan(plan architecture.Plan, estate estateFixture, snapshot compatibilitySnapshot) error {
	if len(plan.MigrationSteps) != len(estate.Components) {
		return fmt.Errorf("migration has %d steps for %d inventoried components", len(plan.MigrationSteps), len(estate.Components))
	}
	if len(plan.MigrationSteps) != len(snapshot.ComponentTransitions) {
		return fmt.Errorf("migration has %d steps, snapshot defines %d", len(plan.MigrationSteps), len(snapshot.ComponentTransitions))
	}
	inventory := make(map[string]estateComponent, len(estate.Components))
	for _, component := range estate.Components {
		inventory[component.ID] = component
	}
	seen := make(map[string]bool, len(plan.MigrationSteps))
	for i, step := range plan.MigrationSteps {
		want := snapshot.ComponentTransitions[i]
		if step.Order != i+1 || step.Order != want.Order {
			return fmt.Errorf("step %d has order %d, want %d", i, step.Order, i+1)
		}
		if seen[step.ComponentID] {
			return fmt.Errorf("component %q appears more than once", step.ComponentID)
		}
		seen[step.ComponentID] = true
		inv, ok := inventory[step.ComponentID]
		if !ok {
			return fmt.Errorf("step %d names unknown component %q", step.Order, step.ComponentID)
		}
		if step.Component != inv.Name || step.Component != want.Component || step.ComponentID != want.ComponentID {
			return fmt.Errorf("step %d component identity does not match inventory and snapshot", step.Order)
		}
		if step.CurrentVersion != inv.Version || step.CurrentVersion != want.From {
			return fmt.Errorf("step %d current version = %q, want %q", step.Order, step.CurrentVersion, want.From)
		}
		if step.TargetVersion != want.To || step.TargetVersion != snapshot.TargetVCFVersion {
			return fmt.Errorf("step %d target version = %q, want %q", step.Order, step.TargetVersion, want.To)
		}
		if strings.HasPrefix(step.TargetVersion, "9.0.1") || strings.HasPrefix(step.TargetVersion, "9.0.2") {
			return fmt.Errorf("step %d uses a prohibited back-in-time target %q", step.Order, step.TargetVersion)
		}
		if step.Action != want.Action {
			return fmt.Errorf("step %d action = %q, want %q", step.Order, step.Action, want.Action)
		}
		if err := verifyGates(step, want.RequiredGates, snapshot.GateCatalog); err != nil {
			return fmt.Errorf("step %d: %w", step.Order, err)
		}
	}
	for id := range inventory {
		if !seen[id] {
			return fmt.Errorf("inventoried component %q is missing", id)
		}
	}
	return nil
}

func verifyGates(step architecture.MigrationStep, required []string, catalog map[string]string) error {
	if len(step.Gates) != len(required) {
		return fmt.Errorf("has %d gates, want %d", len(step.Gates), len(required))
	}
	got := make(map[string]string, len(step.Gates))
	for _, gate := range step.Gates {
		if _, duplicate := got[gate.ID]; duplicate {
			return fmt.Errorf("duplicates gate %q", gate.ID)
		}
		got[gate.ID] = gate.Condition
	}
	for _, id := range required {
		condition, ok := catalog[id]
		if !ok {
			return fmt.Errorf("snapshot references unknown gate %q", id)
		}
		if got[id] != condition {
			return fmt.Errorf("gate %q condition = %q, want %q", id, got[id], condition)
		}
	}
	return nil
}

func verifyStorageDecision(got architecture.StorageDecision, workload workloadCluster, options []storageOption) error {
	if workload.StorageDecisionCriterion != "MINIMUM_SUPPORTED_HOST_COUNT" {
		return fmt.Errorf("unsupported decision criterion %q", workload.StorageDecisionCriterion)
	}
	if len(options) == 0 {
		return fmt.Errorf("snapshot has no storage options")
	}
	sorted := append([]storageOption(nil), options...)
	sort.Slice(sorted, func(i, j int) bool {
		if sorted[i].MinimumHostCount == sorted[j].MinimumHostCount {
			return sorted[i].Architecture < sorted[j].Architecture
		}
		return sorted[i].MinimumHostCount < sorted[j].MinimumHostCount
	})
	want := sorted[0]
	if want.Architecture != "ESA" || !workload.HardwareCandidates.NewESA.ESACertified {
		return fmt.Errorf("pinned minimum-host design is not backed by the ESA hardware candidate")
	}
	if got.SourceArchitecture != workload.StorageArchitecture || got.SourceArchitecture != want.SourceArchitecture {
		return fmt.Errorf("source storage architecture = %q, want %q", got.SourceArchitecture, want.SourceArchitecture)
	}
	if got.SelectedArchitecture != want.Architecture || got.MigrationMode != want.MigrationMode {
		return fmt.Errorf("selected storage %q/%q, want %q/%q", got.SelectedArchitecture, got.MigrationMode, want.Architecture, want.MigrationMode)
	}
	if got.SourceHostCount != workload.HostCount || got.TargetHostCount != want.MinimumHostCount {
		return fmt.Errorf("host-count transition %d -> %d, want %d -> %d", got.SourceHostCount, got.TargetHostCount, workload.HostCount, want.MinimumHostCount)
	}
	if got.TargetHostCount != workload.HardwareCandidates.NewESA.HostCount {
		return fmt.Errorf("target host count does not match available certified ESA hardware")
	}
	if got.RAIDLayout != want.RAIDLayout || got.FailuresToTolerate != want.FailuresToTolerate {
		return fmt.Errorf("storage protection %q/FTT=%d, want %q/FTT=%d", got.RAIDLayout, got.FailuresToTolerate, want.RAIDLayout, want.FailuresToTolerate)
	}
	if got.VsanVLANID != workload.VsanNetwork.VLANID || got.VsanMTU != workload.VsanNetwork.MTU {
		return fmt.Errorf("vSAN network VLAN/MTU = %d/%d, want %d/%d", got.VsanVLANID, got.VsanMTU, workload.VsanNetwork.VLANID, workload.VsanNetwork.MTU)
	}
	if got.MinAggregateNICGbpsPerHost != want.MinimumAggregateNICGbpsPerHost {
		return fmt.Errorf("vSAN NIC requirement = %d Gbps, want %d Gbps", got.MinAggregateNICGbpsPerHost, want.MinimumAggregateNICGbpsPerHost)
	}
	return nil
}

func verifySddcSemantics(raw any, plan architecture.Plan, estate estateFixture) error {
	sddc, ok := raw.(map[string]any)
	if !ok {
		return fmt.Errorf("TargetSddcSpec is not an object")
	}
	if stringValue(sddc["version"]) != plan.TargetVCFVersion {
		return fmt.Errorf("SddcSpec version = %q, want %q", stringValue(sddc["version"]), plan.TargetVCFVersion)
	}
	if stringValue(sddc["workflowType"]) != "VCF" {
		return fmt.Errorf("SddcSpec workflowType must be VCF")
	}
	hosts, ok := sddc["hostSpecs"].([]any)
	if !ok || len(hosts) != plan.StorageDecision.TargetHostCount {
		return fmt.Errorf("SddcSpec has %d hosts, want %d", len(hosts), plan.StorageDecision.TargetHostCount)
	}
	datastore, err := nestedObject(sddc, "datastoreSpec", "vsanSpec")
	if err != nil {
		return err
	}
	esa, err := nestedObject(datastore, "esaConfig")
	if err != nil {
		return err
	}
	if enabled, ok := esa["enabled"].(bool); !ok || !enabled || plan.StorageDecision.SelectedArchitecture != "ESA" {
		return fmt.Errorf("SddcSpec must enable ESA for the selected storage design")
	}
	if intValue(datastore["failuresToTolerate"]) != plan.StorageDecision.FailuresToTolerate {
		return fmt.Errorf("SddcSpec failuresToTolerate does not match the storage decision")
	}
	networks, ok := sddc["networkSpecs"].([]any)
	if !ok {
		return fmt.Errorf("SddcSpec networkSpecs is not an array")
	}
	var foundVSAN bool
	for _, item := range networks {
		network, ok := item.(map[string]any)
		if !ok || stringValue(network["networkType"]) != "VSAN" {
			continue
		}
		foundVSAN = true
		if intValue(network["vlanId"]) != estate.Workload.VsanNetwork.VLANID || intValue(network["mtu"]) != estate.Workload.VsanNetwork.MTU {
			return fmt.Errorf("SddcSpec VSAN VLAN/MTU does not match the estate design")
		}
	}
	if !foundVSAN {
		return fmt.Errorf("SddcSpec does not define a VSAN network")
	}
	dvsSpecs, ok := sddc["dvsSpecs"].([]any)
	if !ok || len(dvsSpecs) == 0 {
		return fmt.Errorf("SddcSpec does not define a distributed switch")
	}
	for i, item := range dvsSpecs {
		dvs, ok := item.(map[string]any)
		if !ok || intValue(dvs["mtu"]) != estate.Workload.VsanNetwork.MTU {
			return fmt.Errorf("SddcSpec dvsSpecs[%d] MTU does not match the vSAN design", i)
		}
	}
	return nil
}

func verifyFreshConcurrentPlans(baseline architecture.Plan) error {
	want, err := json.Marshal(baseline)
	if err != nil {
		return err
	}
	left := architecture.Build()
	right := architecture.Build()
	if len(left.TargetSddcSpec) == 0 || len(right.TargetSddcSpec) == 0 || len(left.MigrationSteps) == 0 || len(right.MigrationSteps) == 0 || len(left.MigrationSteps[0].Gates) == 0 || len(right.MigrationSteps[0].Gates) == 0 {
		return fmt.Errorf("Build returned an incomplete plan")
	}
	left.TargetSddcSpec[0] = '['
	left.MigrationSteps[0].Action = "mutated by one caller"
	left.MigrationSteps[0].Gates[0].Condition = "mutated by one caller"
	rightEncoded, err := json.Marshal(right)
	if err != nil {
		return err
	}
	if !reflect.DeepEqual(rightEncoded, want) {
		return fmt.Errorf("mutating one Build result changed another caller's plan")
	}
	freshEncoded, err := json.Marshal(architecture.Build())
	if err != nil {
		return err
	}
	if !reflect.DeepEqual(freshEncoded, want) {
		return fmt.Errorf("mutating one Build result changed a later caller's plan")
	}
	var wg sync.WaitGroup
	errs := make(chan error, 24)
	for i := 0; i < 24; i++ {
		i := i
		wg.Add(1)
		go func() {
			defer wg.Done()
			got := architecture.Build()
			encoded, err := json.Marshal(got)
			if err != nil {
				errs <- err
				return
			}
			if !reflect.DeepEqual(encoded, want) {
				errs <- fmt.Errorf("concurrent Build returned a different plan")
				return
			}
			if len(got.MigrationSteps) == 0 || len(got.MigrationSteps[0].Gates) == 0 {
				errs <- fmt.Errorf("concurrent Build returned an incomplete plan")
				return
			}
			got.MigrationSteps[0].Gates[0].Condition = fmt.Sprintf("caller-%d", i)
		}()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			return err
		}
	}
	return nil
}

func verifyResearchRecord(t *testing.T) {
	t.Helper()
	data, err := os.ReadFile(repoPath("research.md"))
	if err != nil {
		t.Fatalf("read required research record: %v", err)
	}
	datePattern := regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)
	urlPattern := regexp.MustCompile(`^https://[^\s|]+$`)
	rows := 0
	var hasMatrix, hasUpgrade, hasVSAN bool
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if !strings.HasPrefix(line, "|") || !strings.HasSuffix(line, "|") {
			continue
		}
		parts := strings.Split(strings.Trim(line, "|"), "|")
		if len(parts) != 4 {
			continue
		}
		for i := range parts {
			parts[i] = strings.TrimSpace(parts[i])
		}
		if strings.EqualFold(parts[0], "Source title") || strings.Trim(parts[0], "-: ") == "" {
			continue
		}
		if parts[0] == "" || parts[3] == "" || !urlPattern.MatchString(parts[1]) || !datePattern.MatchString(parts[2]) {
			t.Fatalf("research.md row must contain a title, HTTPS URL, YYYY-MM-DD date, and claim: %q", line)
		}
		hostAndPath := strings.TrimPrefix(parts[1], "https://")
		host := strings.ToLower(strings.SplitN(hostAndPath, "/", 2)[0])
		if host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com") {
			t.Fatalf("research source is not a Broadcom HTTPS URL: %q", parts[1])
		}
		if strings.Contains(host, "localhost") || strings.HasSuffix(host, ".invalid") {
			t.Fatalf("research source is not reachable: %q", parts[1])
		}
		combined := strings.ToLower(parts[0] + " " + parts[3])
		hasMatrix = hasMatrix || host == "interopmatrix.broadcom.com" || host == "compatibilityguide.broadcom.com"
		hasUpgrade = hasUpgrade || strings.Contains(combined, "upgrade")
		hasVSAN = hasVSAN || strings.Contains(combined, "vsan")
		rows++
	}
	if rows == 0 {
		t.Fatal("research.md has no source rows")
	}
	if !hasMatrix || !hasUpgrade || !hasVSAN {
		t.Fatalf("research.md must cover the Broadcom compatibility matrix, upgrade guidance, and vSAN guidance")
	}
}

func validateOpenAPISchema(document map[string]any, schema map[string]any, value any, path string) error {
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolveRef(document, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		return validateOpenAPISchema(document, resolved, value, path)
	}
	if value == nil {
		if nullable, _ := schema["nullable"].(bool); nullable {
			return nil
		}
	}
	for _, keyword := range []string{"allOf"} {
		if branches, ok := schema[keyword].([]any); ok {
			for i, branch := range branches {
				branchSchema, ok := branch.(map[string]any)
				if !ok {
					return fmt.Errorf("%s: %s[%d] is not a schema", path, keyword, i)
				}
				if err := validateOpenAPISchema(document, branchSchema, value, path); err != nil {
					return err
				}
			}
		}
	}
	for _, keyword := range []string{"oneOf", "anyOf"} {
		if branches, ok := schema[keyword].([]any); ok {
			matches := 0
			for _, branch := range branches {
				branchSchema, ok := branch.(map[string]any)
				if ok && validateOpenAPISchema(document, branchSchema, value, path) == nil {
					matches++
				}
			}
			if matches == 0 || (keyword == "oneOf" && matches != 1) {
				return fmt.Errorf("%s: does not satisfy %s", path, keyword)
			}
		}
	}
	if enum, ok := schema["enum"].([]any); ok {
		matched := false
		for _, candidate := range enum {
			if reflect.DeepEqual(candidate, value) {
				matched = true
				break
			}
		}
		if !matched {
			return fmt.Errorf("%s: value is not in enum", path)
		}
	}
	typeName, _ := schema["type"].(string)
	switch typeName {
	case "object":
		object, ok := value.(map[string]any)
		if !ok {
			return fmt.Errorf("%s: want object, got %T", path, value)
		}
		if required, ok := schema["required"].([]any); ok {
			for _, field := range required {
				name, _ := field.(string)
				if _, present := object[name]; !present {
					return fmt.Errorf("%s.%s: required property is missing", path, name)
				}
			}
		}
		if properties, ok := schema["properties"].(map[string]any); ok {
			for name, property := range properties {
				child, present := object[name]
				if !present {
					continue
				}
				propertySchema, ok := property.(map[string]any)
				if !ok {
					return fmt.Errorf("%s.%s: property schema is invalid", path, name)
				}
				if err := validateOpenAPISchema(document, propertySchema, child, path+"."+name); err != nil {
					return err
				}
			}
		}
	case "array":
		array, ok := value.([]any)
		if !ok {
			return fmt.Errorf("%s: want array, got %T", path, value)
		}
		if min, ok := number(schema["minItems"]); ok && float64(len(array)) < min {
			return fmt.Errorf("%s: has fewer than %v items", path, min)
		}
		if max, ok := number(schema["maxItems"]); ok && float64(len(array)) > max {
			return fmt.Errorf("%s: has more than %v items", path, max)
		}
		if itemSchema, ok := schema["items"].(map[string]any); ok {
			for i, item := range array {
				if err := validateOpenAPISchema(document, itemSchema, item, fmt.Sprintf("%s[%d]", path, i)); err != nil {
					return err
				}
			}
		}
	case "string":
		text, ok := value.(string)
		if !ok {
			return fmt.Errorf("%s: want string, got %T", path, value)
		}
		if min, ok := number(schema["minLength"]); ok && float64(len([]rune(text))) < min {
			return fmt.Errorf("%s: is shorter than %v", path, min)
		}
		if max, ok := number(schema["maxLength"]); ok && float64(len([]rune(text))) > max {
			return fmt.Errorf("%s: is longer than %v", path, max)
		}
		if pattern, ok := schema["pattern"].(string); ok {
			re, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: installer schema pattern is invalid: %w", path, err)
			}
			if !re.MatchString(text) {
				return fmt.Errorf("%s: does not match %q", path, pattern)
			}
		}
	case "integer":
		valueNumber, ok := number(value)
		if !ok || valueNumber != float64(int64(valueNumber)) {
			return fmt.Errorf("%s: want integer, got %T", path, value)
		}
		if err := validateNumberBounds(schema, valueNumber, path); err != nil {
			return err
		}
	case "number":
		valueNumber, ok := number(value)
		if !ok {
			return fmt.Errorf("%s: want number, got %T", path, value)
		}
		if err := validateNumberBounds(schema, valueNumber, path); err != nil {
			return err
		}
	case "boolean":
		if _, ok := value.(bool); !ok {
			return fmt.Errorf("%s: want boolean, got %T", path, value)
		}
	}
	return nil
}

func validateNumberBounds(schema map[string]any, value float64, path string) error {
	if min, ok := number(schema["minimum"]); ok && value < min {
		return fmt.Errorf("%s: %v is below minimum %v", path, value, min)
	}
	if max, ok := number(schema["maximum"]); ok && value > max {
		return fmt.Errorf("%s: %v is above maximum %v", path, value, max)
	}
	return nil
}

func resolveRef(document map[string]any, ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("external schema reference %q is not self-contained", ref)
	}
	var current any = document
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("reference %q traverses a non-object", ref)
		}
		current, ok = object[token]
		if !ok {
			return nil, fmt.Errorf("reference %q is missing token %q", ref, token)
		}
	}
	resolved, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("reference %q does not resolve to a schema", ref)
	}
	return resolved, nil
}

func objectAt(root map[string]any, path ...string) (map[string]any, error) {
	var current any = root
	for _, key := range path {
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%q traverses a non-object", strings.Join(path, "."))
		}
		current, ok = object[key]
		if !ok {
			return nil, fmt.Errorf("missing %q", strings.Join(path, "."))
		}
	}
	object, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%q is not an object", strings.Join(path, "."))
	}
	return object, nil
}

func nestedObject(root map[string]any, path ...string) (map[string]any, error) {
	return objectAt(root, path...)
}

func readProtected(t *testing.T, path, wantSHA string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read protected input %s: %v", path, err)
	}
	sum := sha256.Sum256(data)
	if got := hex.EncodeToString(sum[:]); got != wantSHA {
		t.Fatalf("protected input %s has SHA-256 %s, want %s", path, got, wantSHA)
	}
	return data
}

func mustJSON(t *testing.T, data []byte, target any) {
	t.Helper()
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatalf("decode protected JSON: %v", err)
	}
}

func repoPath(elements ...string) string {
	return filepath.Join(append([]string{".."}, elements...)...)
}

func number(value any) (float64, bool) {
	switch value := value.(type) {
	case float64:
		return value, true
	case float32:
		return float64(value), true
	case int:
		return float64(value), true
	case int64:
		return float64(value), true
	case json.Number:
		n, err := value.Float64()
		return n, err == nil
	default:
		return 0, false
	}
}

func intValue(value any) int {
	n, _ := number(value)
	return int(n)
}

func stringValue(value any) string {
	text, _ := value.(string)
	return text
}
