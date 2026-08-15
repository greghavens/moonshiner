package vcfarch_test

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"sync"
	"testing"

	"vcfarch"
)

const (
	requirementsPath = "fixtures/requirements.json"
	estatePath       = "fixtures/estate.json"
	snapshotPath     = "fixtures/compatibility-snapshot.json"
	artifactPath     = "architecture.json"
	researchPath     = "research.md"
)

func loadInputs(t *testing.T) (vcfarch.Requirements, vcfarch.Estate, vcfarch.CompatibilitySnapshot) {
	t.Helper()
	requirements, err := os.Open(requirementsPath)
	if err != nil {
		t.Fatal(err)
	}
	defer requirements.Close()
	estate, err := os.Open(estatePath)
	if err != nil {
		t.Fatal(err)
	}
	defer estate.Close()
	snapshot, err := os.Open(snapshotPath)
	if err != nil {
		t.Fatal(err)
	}
	defer snapshot.Close()
	req, est, compat, err := vcfarch.LoadInputs(requirements, estate, snapshot)
	if err != nil {
		t.Fatalf("LoadInputs: %v", err)
	}
	return req, est, compat
}

func loadArtifact(t *testing.T) (vcfarch.Artifact, []byte) {
	t.Helper()
	b, err := os.ReadFile(artifactPath)
	if err != nil {
		t.Fatalf("read %s: %v", artifactPath, err)
	}
	var artifact vcfarch.Artifact
	if err := json.Unmarshal(b, &artifact); err != nil {
		t.Fatalf("decode %s: %v", artifactPath, err)
	}
	return artifact, b
}

func TestBuildArchitectureInputValidation(t *testing.T) {
	req, estate, snapshot := loadInputs(t)
	tests := []struct {
		name    string
		mutate  func(*vcfarch.Requirements, *vcfarch.Estate, *vcfarch.CompatibilitySnapshot)
		wantErr bool
	}{
		{name: "valid pinned scenario"},
		{
			name: "host count contradicts failures to tolerate",
			mutate: func(r *vcfarch.Requirements, _ *vcfarch.Estate, _ *vcfarch.CompatibilitySnapshot) {
				r.Availability.HostFailuresToToleratePerSite = 2
			},
			wantErr: true,
		},
		{
			name: "management minimum not met",
			mutate: func(r *vcfarch.Requirements, _ *vcfarch.Estate, _ *vcfarch.CompatibilitySnapshot) {
				r.Sites[0].HostCount = 3
			},
			wantErr: true,
		},
		{
			name: "failover capacity not met after tolerated host loss",
			mutate: func(r *vcfarch.Requirements, _ *vcfarch.Estate, _ *vcfarch.CompatibilitySnapshot) {
				r.Demand.MemoryGiB++
			},
			wantErr: true,
		},
		{
			name: "witness in a data site",
			mutate: func(r *vcfarch.Requirements, _ *vcfarch.Estate, _ *vcfarch.CompatibilitySnapshot) {
				r.Witness.FailureDomain = r.Sites[1].Name
			},
			wantErr: true,
		},
		{
			name: "witness in a data site even if snapshot flag is relaxed",
			mutate: func(r *vcfarch.Requirements, _ *vcfarch.Estate, c *vcfarch.CompatibilitySnapshot) {
				r.Witness.FailureDomain = r.Sites[0].Name
				c.Architecture.WitnessOutsideDataSites = false
			},
			wantErr: true,
		},
		{
			name: "unsupported estate source",
			mutate: func(_ *vcfarch.Requirements, e *vcfarch.Estate, _ *vcfarch.CompatibilitySnapshot) {
				e.Components[0].Version = "8.18.0"
			},
			wantErr: true,
		},
		{
			name: "duplicate migration rule omits an inventory component",
			mutate: func(_ *vcfarch.Requirements, _ *vcfarch.Estate, c *vcfarch.CompatibilitySnapshot) {
				c.Migration[6].ComponentID = c.Migration[5].ComponentID
				c.Migration[6].SupportedSources = append([]string(nil), c.Migration[5].SupportedSources...)
				c.Migration[6].Gates = clone(t, c.Migration[5].Gates)
			},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r := clone(t, req)
			e := clone(t, estate)
			c := clone(t, snapshot)
			if tt.mutate != nil {
				tt.mutate(&r, &e, &c)
			}
			_, err := vcfarch.BuildArchitecture(r, e, c)
			if (err != nil) != tt.wantErr {
				t.Fatalf("BuildArchitecture() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

func clone[T any](t *testing.T, in T) T {
	t.Helper()
	b, err := json.Marshal(in)
	if err != nil {
		t.Fatal(err)
	}
	var out T
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestCommittedArtifactIsGeneratedByPackage(t *testing.T) {
	req, estate, snapshot := loadInputs(t)
	want, err := vcfarch.BuildArchitecture(req, estate, snapshot)
	if err != nil {
		t.Fatalf("BuildArchitecture: %v", err)
	}
	got, raw := loadArtifact(t)
	gotJSON, err := json.Marshal(got)
	if err != nil {
		t.Fatal(err)
	}
	wantJSON, err := json.Marshal(want)
	if err != nil {
		t.Fatal(err)
	}
	var gotValue, wantValue any
	if err := json.Unmarshal(gotJSON, &gotValue); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(wantJSON, &wantValue); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(gotValue, wantValue) {
		t.Fatalf("%s does not match BuildArchitecture output\n got: %#v\nwant: %#v", artifactPath, got, want)
	}
	var rendered bytes.Buffer
	if err := vcfarch.WriteArtifact(&rendered, want); err != nil {
		t.Fatalf("WriteArtifact: %v", err)
	}
	if !bytes.Equal(raw, rendered.Bytes()) {
		t.Fatalf("%s is not the stable WriteArtifact encoding", artifactPath)
	}
	if len(raw) == 0 || raw[len(raw)-1] != '\n' {
		t.Fatalf("%s must end in a newline", artifactPath)
	}
}

func TestBuildArchitectureIsConcurrentAndDeterministic(t *testing.T) {
	req, estate, snapshot := loadInputs(t)
	want, err := vcfarch.BuildArchitecture(req, estate, snapshot)
	if err != nil {
		t.Fatalf("BuildArchitecture: %v", err)
	}
	var wantJSON bytes.Buffer
	if err := vcfarch.WriteArtifact(&wantJSON, want); err != nil {
		t.Fatalf("WriteArtifact: %v", err)
	}

	const workers = 16
	errors := make(chan error, workers)
	var wait sync.WaitGroup
	wait.Add(workers)
	for worker := 0; worker < workers; worker++ {
		go func() {
			defer wait.Done()
			got, err := vcfarch.BuildArchitecture(req, estate, snapshot)
			if err != nil {
				errors <- fmt.Errorf("BuildArchitecture: %w", err)
				return
			}
			var gotJSON bytes.Buffer
			if err := vcfarch.WriteArtifact(&gotJSON, got); err != nil {
				errors <- fmt.Errorf("WriteArtifact: %w", err)
				return
			}
			if !bytes.Equal(gotJSON.Bytes(), wantJSON.Bytes()) {
				errors <- fmt.Errorf("concurrent build produced different artifact encoding")
			}
		}()
	}
	wait.Wait()
	close(errors)
	for err := range errors {
		t.Error(err)
	}
}

func TestGreenfieldSddcSpecValidatesAgainstPinnedOpenAPI(t *testing.T) {
	artifact, _ := loadArtifact(t)
	var spec any
	if err := json.Unmarshal(artifact.Greenfield.SddcSpec, &spec); err != nil {
		t.Fatalf("sddcSpec: %v", err)
	}
	openAPI := readJSON(t, "specifications/vcf-installer/vcf-installer-openapi.json")
	root := object(t, openAPI, "OpenAPI document")
	components := object(t, root["components"], "components")
	schemas := object(t, components["schemas"], "components.schemas")
	sddcSchema, ok := schemas["SddcSpec"]
	if !ok {
		t.Fatal("pinned OpenAPI has no SddcSpec schema")
	}
	if err := validateSchema(root, sddcSchema, spec, "sddcSpec"); err != nil {
		t.Fatal(err)
	}
}

func TestGreenfieldTopologyCapacityAndFTT(t *testing.T) {
	req, _, snapshot := loadInputs(t)
	artifact, _ := loadArtifact(t)
	greenfield := artifact.Greenfield
	if artifact.DesignID != req.DesignID {
		t.Fatalf("designId = %q, want %q", artifact.DesignID, req.DesignID)
	}
	if !reflect.DeepEqual(greenfield.Availability, req.Availability) {
		t.Fatalf("availability = %#v, want %#v", greenfield.Availability, req.Availability)
	}
	if len(greenfield.Topology.DataSites) != snapshot.Architecture.RequiredDataSiteCount {
		t.Fatalf("data site count = %d, want %d", len(greenfield.Topology.DataSites), snapshot.Architecture.RequiredDataSiteCount)
	}

	sites := make(map[string]vcfarch.Site, len(req.Sites))
	for _, site := range req.Sites {
		sites[site.Name] = site
	}
	minimumForFTT := snapshot.Architecture.Raid1HostsPerSiteBase +
		snapshot.Architecture.Raid1HostsPerAdditionalFTT*req.Availability.HostFailuresToToleratePerSite
	if minimumForFTT < snapshot.Architecture.MinimumManagementHostsPerSite {
		minimumForFTT = snapshot.Architecture.MinimumManagementHostsPerSite
	}
	allTopologyHosts := map[string]bool{}
	seenTopologySites := map[string]bool{}
	wantTopologyHosts := map[string]bool{}
	for _, site := range req.Sites {
		for index := 1; index <= site.HostCount; index++ {
			wantTopologyHosts[fmt.Sprintf("%s%02d", site.HostPrefix, index)] = true
		}
	}
	for _, placement := range greenfield.Topology.DataSites {
		site, ok := sites[placement.Name]
		if !ok {
			t.Fatalf("unexpected topology site %q", placement.Name)
		}
		if seenTopologySites[placement.Name] {
			t.Fatalf("topology site %q appears more than once", placement.Name)
		}
		seenTopologySites[placement.Name] = true
		// This is the protected FTT/host-count oracle: a plan that states an FTT
		// but supplies too few hosts fails even if its own capacity flag says true.
		if len(placement.Hosts) < minimumForFTT {
			t.Fatalf("site %s has %d hosts but FTT=%d requires at least %d", placement.Name, len(placement.Hosts), req.Availability.HostFailuresToToleratePerSite, minimumForFTT)
		}
		if len(placement.Hosts) != site.HostCount {
			t.Fatalf("site %s has %d topology hosts, fixture requires %d", placement.Name, len(placement.Hosts), site.HostCount)
		}
		for _, host := range placement.Hosts {
			if !wantTopologyHosts[host] {
				t.Fatalf("site %s contains non-fixture host %q", placement.Name, host)
			}
			if !strings.HasPrefix(host, site.HostPrefix) {
				t.Fatalf("host %q is assigned to site %s instead of its fixture-derived site", host, placement.Name)
			}
			if allTopologyHosts[host] {
				t.Fatalf("host %q assigned more than once", host)
			}
			allTopologyHosts[host] = true
		}
	}
	if len(seenTopologySites) != len(sites) {
		t.Fatalf("topology covers sites %v, want %v", sortedKeys(seenTopologySites), sortedKeys(sitesToBool(sites)))
	}
	if !reflect.DeepEqual(allTopologyHosts, wantTopologyHosts) {
		t.Fatalf("topology hosts %v, want fixture-derived hosts %v", sortedKeys(allTopologyHosts), sortedKeys(wantTopologyHosts))
	}

	witness := greenfield.Topology.Witness
	if witness.FQDN != req.Witness.FQDN || witness.FailureDomain != req.Witness.FailureDomain {
		t.Fatalf("witness placement = %#v, want fixture %#v", witness, req.Witness)
	}
	if witness.RunsOnManagementCluster {
		t.Fatal("witness must not run on the management cluster it witnesses")
	}
	for _, site := range req.Sites {
		if witness.FailureDomain == site.Name {
			t.Fatalf("witness failure domain %q is a data site", witness.FailureDomain)
		}
	}

	worstCPU, worstMemory, worstStorage := math.MaxInt, math.MaxInt, math.MaxInt
	for _, site := range req.Sites {
		survivors := site.HostCount - req.Availability.HostFailuresToToleratePerSite
		worstCPU = min(worstCPU, survivors*site.CoresPerHost)
		worstMemory = min(worstMemory, survivors*site.MemoryGiBPerHost)
		worstStorage = min(worstStorage, survivors*site.UsableTiBPerHost)
	}
	wantCapacity := vcfarch.CapacityAssessment{
		SurvivingSiteCPUCores:  worstCPU,
		SurvivingSiteMemoryGiB: worstMemory,
		SurvivingSiteUsableTiB: worstStorage,
		RequiredCPUCores:       req.Demand.CPUCores,
		RequiredMemoryGiB:      req.Demand.MemoryGiB,
		RequiredUsableTiB:      req.Demand.UsableTiB,
		MeetsFailoverDemand:    worstCPU >= req.Demand.CPUCores && worstMemory >= req.Demand.MemoryGiB && worstStorage >= req.Demand.UsableTiB,
	}
	if !reflect.DeepEqual(greenfield.Capacity, wantCapacity) {
		t.Fatalf("capacity = %#v, want derived %#v", greenfield.Capacity, wantCapacity)
	}
	if !greenfield.Capacity.MeetsFailoverDemand {
		t.Fatal("greenfield design does not meet failover demand")
	}

	var sddc map[string]any
	if err := json.Unmarshal(greenfield.SddcSpec, &sddc); err != nil {
		t.Fatal(err)
	}
	if sddc["sddcId"] != req.DesignID || sddc["version"] != req.TargetVersion || sddc["workflowType"] != "VCF" {
		t.Fatalf("SddcSpec identity/version/workflow are wrong: %#v", sddc)
	}
	hosts := array(t, sddc["hostSpecs"], "sddcSpec.hostSpecs")
	specHosts := map[string]bool{}
	for i, rawHost := range hosts {
		host := object(t, rawHost, fmt.Sprintf("hostSpecs[%d]", i))
		name, _ := host["hostname"].(string)
		if name == "" || specHosts[name] {
			t.Fatalf("invalid or duplicate SddcSpec hostname %q", name)
		}
		specHosts[name] = true
	}
	if !reflect.DeepEqual(specHosts, allTopologyHosts) {
		t.Fatalf("SddcSpec hosts %v do not equal topology hosts %v", sortedKeys(specHosts), sortedKeys(allTopologyHosts))
	}
	if specHosts[req.Witness.FQDN] {
		t.Fatal("witness must not appear in SddcSpec hostSpecs")
	}
	datastore := object(t, sddc["datastoreSpec"], "sddcSpec.datastoreSpec")
	vsan := object(t, datastore["vsanSpec"], "sddcSpec.datastoreSpec.vsanSpec")
	if ftt, _ := vsan["failuresToTolerate"].(float64); int(ftt) != req.Availability.HostFailuresToToleratePerSite {
		t.Fatalf("SddcSpec vSAN FTT = %v, want %d", vsan["failuresToTolerate"], req.Availability.HostFailuresToToleratePerSite)
	}

	wantVLANs := map[string]int{
		"MANAGEMENT": req.Network.ManagementVLAN,
		"VSAN":       req.Network.VsanVLAN,
		"VMOTION":    req.Network.VmotionVLAN,
	}
	gotVLANs := map[string]int{}
	for i, rawNetwork := range array(t, sddc["networkSpecs"], "sddcSpec.networkSpecs") {
		network := object(t, rawNetwork, fmt.Sprintf("networkSpecs[%d]", i))
		kind, _ := network["networkType"].(string)
		vlan, _ := network["vlanId"].(float64)
		gotVLANs[kind] = int(vlan)
	}
	if !reflect.DeepEqual(gotVLANs, wantVLANs) {
		t.Fatalf("network VLANs = %#v, want %#v", gotVLANs, wantVLANs)
	}
}

func TestResearchRecordDocumentsLiveBroadcomSources(t *testing.T) {
	b, err := os.ReadFile(researchPath)
	if err != nil {
		t.Fatalf("read %s: %v", researchPath, err)
	}
	record := string(b)
	lower := strings.ToLower(record)
	if !regexp.MustCompile(`(?im)\baccess(?:ed| date)?\s*:?\s*\d{4}-\d{2}-\d{2}\b`).MatchString(record) {
		t.Fatal("research.md must record an access date in YYYY-MM-DD form")
	}
	for _, required := range []string{"9.1", "pinned"} {
		if !strings.Contains(lower, required) {
			t.Fatalf("research.md does not discuss %q", required)
		}
	}
	if !strings.Contains(lower, "compatib") && !strings.Contains(lower, "interop") {
		t.Fatal("research.md does not record a compatibility/interoperability fact")
	}
	if !strings.Contains(lower, "upgrad") && !strings.Contains(lower, "sequenc") {
		t.Fatal("research.md does not record an upgrade-path or sequencing fact")
	}
	if !strings.Contains(lower, "disagree") && !strings.Contains(lower, "contradict") {
		t.Fatal("research.md must state whether live material disagrees with the pinned snapshot")
	}

	urlPattern := regexp.MustCompile(`https://[^\s)>]+`)
	sources := map[string]bool{}
	for _, raw := range urlPattern.FindAllString(record, -1) {
		raw = strings.TrimRight(raw, ".,;]")
		parsed, err := url.Parse(raw)
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" {
			t.Fatalf("research.md contains an invalid HTTPS source URL %q", raw)
		}
		host := strings.ToLower(parsed.Hostname())
		if host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com") {
			t.Fatalf("research source %q is not a Broadcom URL", raw)
		}
		sources[raw] = true
	}
	if len(sources) < 2 {
		t.Fatalf("research.md records %d distinct Broadcom source URLs, want at least two", len(sources))
	}
}

func TestMigrationPlanSchemaCoverageOrderAndGates(t *testing.T) {
	_, estate, snapshot := loadInputs(t)
	artifact, _ := loadArtifact(t)
	plan := artifact.ExistingEstateMigration
	planSchema := readJSON(t, "schemas/migration-plan.schema.json")
	planJSON, err := json.Marshal(plan)
	if err != nil {
		t.Fatal(err)
	}
	var planValue any
	if err := json.Unmarshal(planJSON, &planValue); err != nil {
		t.Fatal(err)
	}
	if err := validateSchema(object(t, planSchema, "migration schema"), planSchema, planValue, "existingEstateMigration"); err != nil {
		t.Fatal(err)
	}
	if plan.EstateID != estate.EstateID || plan.TargetVCFVersion != snapshot.TargetVersion {
		t.Fatalf("migration identity/target = %q/%q, want %q/%q", plan.EstateID, plan.TargetVCFVersion, estate.EstateID, snapshot.TargetVersion)
	}
	if len(plan.Steps) != len(estate.Components) || len(plan.Steps) != len(snapshot.Migration) {
		t.Fatalf("migration has %d steps for %d inventory components and %d rules", len(plan.Steps), len(estate.Components), len(snapshot.Migration))
	}
	inventory := map[string]vcfarch.InventoryComponent{}
	for _, component := range estate.Components {
		if _, exists := inventory[component.ID]; exists {
			t.Fatalf("duplicate fixture component %q", component.ID)
		}
		inventory[component.ID] = component
	}
	seen := map[string]int{}
	completed := map[string]bool{}
	for i, step := range plan.Steps {
		rule := snapshot.Migration[i]
		component, ok := inventory[step.ComponentID]
		if !ok {
			t.Fatalf("step %d names component %q absent from inventory", i+1, step.ComponentID)
		}
		seen[step.ComponentID]++
		if step.Order != i+1 || step.Order != rule.Order || step.ComponentID != rule.ComponentID {
			t.Fatalf("step %d order/component = %d/%q, pinned rule = %d/%q", i+1, step.Order, step.ComponentID, rule.Order, rule.ComponentID)
		}
		if step.Component != component.Name || step.SourceVersion != component.Version || step.TargetVersion != rule.TargetVersion {
			t.Fatalf("step %d version/name tuple = %#v, inventory/rule mismatch", i+1, step)
		}
		if !contains(rule.SupportedSources, step.SourceVersion) {
			t.Fatalf("step %d source %q is not supported by pinned rule", i+1, step.SourceVersion)
		}
		if !reflect.DeepEqual(step.Gates, rule.Gates) {
			t.Fatalf("step %d gates = %#v, want pinned %#v", i+1, step.Gates, rule.Gates)
		}
		for _, gate := range step.Gates {
			for _, dependency := range gate.RequiresComponentIDs {
				if !completed[dependency] {
					t.Fatalf("step %d gate %q depends on component %q that is not earlier", i+1, gate.ID, dependency)
				}
			}
		}
		completed[step.ComponentID] = true
	}
	for id := range inventory {
		if seen[id] != 1 {
			t.Fatalf("inventory component %q appears %d times, want exactly once", id, seen[id])
		}
	}
}

func readJSON(t *testing.T, path string) any {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var value any
	if err := json.Unmarshal(b, &value); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
	return value
}

func object(t *testing.T, value any, path string) map[string]any {
	t.Helper()
	result, ok := value.(map[string]any)
	if !ok {
		t.Fatalf("%s is %T, want object", path, value)
	}
	return result
}

func array(t *testing.T, value any, path string) []any {
	t.Helper()
	result, ok := value.([]any)
	if !ok {
		t.Fatalf("%s is %T, want array", path, value)
	}
	return result
}

func validateSchema(root map[string]any, rawSchema, value any, path string) error {
	schema, ok := rawSchema.(map[string]any)
	if !ok {
		return fmt.Errorf("%s: schema is %T, want object", path, rawSchema)
	}
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolveLocalRef(root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		return validateSchema(root, resolved, value, path)
	}
	if value == nil {
		if nullable, _ := schema["nullable"].(bool); nullable {
			return nil
		}
	}
	for _, keyword := range []string{"allOf", "anyOf", "oneOf"} {
		if branches, ok := schema[keyword].([]any); ok {
			passes := 0
			var lastErr error
			for _, branch := range branches {
				if err := validateSchema(root, branch, value, path); err == nil {
					passes++
				} else {
					lastErr = err
				}
			}
			switch keyword {
			case "allOf":
				if passes != len(branches) {
					return fmt.Errorf("%s: allOf failed: %v", path, lastErr)
				}
			case "anyOf":
				if passes == 0 {
					return fmt.Errorf("%s: anyOf failed: %v", path, lastErr)
				}
			case "oneOf":
				if passes != 1 {
					return fmt.Errorf("%s: oneOf matched %d branches", path, passes)
				}
			}
		}
	}
	if constant, exists := schema["const"]; exists && !reflect.DeepEqual(constant, value) {
		return fmt.Errorf("%s: value %#v does not equal const %#v", path, value, constant)
	}
	if enum, ok := schema["enum"].([]any); ok {
		matched := false
		for _, candidate := range enum {
			matched = matched || reflect.DeepEqual(candidate, value)
		}
		if !matched {
			return fmt.Errorf("%s: value %#v is not in enum", path, value)
		}
	}
	typeName, _ := schema["type"].(string)
	switch typeName {
	case "object":
		obj, ok := value.(map[string]any)
		if !ok {
			return fmt.Errorf("%s: got %T, want object", path, value)
		}
		properties, _ := schema["properties"].(map[string]any)
		if required, ok := schema["required"].([]any); ok {
			for _, rawName := range required {
				name, _ := rawName.(string)
				if _, exists := obj[name]; !exists {
					return fmt.Errorf("%s: missing required property %q", path, name)
				}
			}
		}
		if additional, ok := schema["additionalProperties"].(bool); ok && !additional {
			for name := range obj {
				if _, declared := properties[name]; !declared {
					return fmt.Errorf("%s: additional property %q", path, name)
				}
			}
		}
		for name, childSchema := range properties {
			if child, exists := obj[name]; exists {
				if err := validateSchema(root, childSchema, child, path+"."+name); err != nil {
					return err
				}
			}
		}
	case "array":
		items, ok := value.([]any)
		if !ok {
			return fmt.Errorf("%s: got %T, want array", path, value)
		}
		if minimum, ok := schema["minItems"].(float64); ok && len(items) < int(minimum) {
			return fmt.Errorf("%s: has %d items, minimum %d", path, len(items), int(minimum))
		}
		if maximum, ok := schema["maxItems"].(float64); ok && len(items) > int(maximum) {
			return fmt.Errorf("%s: has %d items, maximum %d", path, len(items), int(maximum))
		}
		if childSchema, exists := schema["items"]; exists {
			for i, item := range items {
				if err := validateSchema(root, childSchema, item, fmt.Sprintf("%s[%d]", path, i)); err != nil {
					return err
				}
			}
		}
	case "string":
		text, ok := value.(string)
		if !ok {
			return fmt.Errorf("%s: got %T, want string", path, value)
		}
		length := len([]rune(text))
		if minimum, ok := schema["minLength"].(float64); ok && length < int(minimum) {
			return fmt.Errorf("%s: length %d, minimum %d", path, length, int(minimum))
		}
		if maximum, ok := schema["maxLength"].(float64); ok && length > int(maximum) {
			return fmt.Errorf("%s: length %d, maximum %d", path, length, int(maximum))
		}
		if pattern, ok := schema["pattern"].(string); ok {
			re, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: invalid schema pattern %q: %w", path, pattern, err)
			}
			if !re.MatchString(text) {
				return fmt.Errorf("%s: %q does not match %q", path, text, pattern)
			}
		}
	case "integer", "number":
		number, ok := value.(float64)
		if !ok || typeName == "integer" && math.Trunc(number) != number {
			return fmt.Errorf("%s: got %#v, want %s", path, value, typeName)
		}
		if minimum, ok := schema["minimum"].(float64); ok && number < minimum {
			return fmt.Errorf("%s: %v is below minimum %v", path, number, minimum)
		}
		if maximum, ok := schema["maximum"].(float64); ok && number > maximum {
			return fmt.Errorf("%s: %v is above maximum %v", path, number, maximum)
		}
	case "boolean":
		if _, ok := value.(bool); !ok {
			return fmt.Errorf("%s: got %T, want boolean", path, value)
		}
	case "":
		// OpenAPI composition nodes and permissive schemas need no direct type check.
	default:
		return fmt.Errorf("%s: verifier does not support schema type %q", path, typeName)
	}
	return nil
}

func resolveLocalRef(root map[string]any, ref string) (any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("non-local schema ref %q", ref)
	}
	var current any = root
	for _, encoded := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		part := strings.ReplaceAll(strings.ReplaceAll(encoded, "~1", "/"), "~0", "~")
		obj, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("cannot resolve %q at %q", ref, part)
		}
		current, ok = obj[part]
		if !ok {
			return nil, fmt.Errorf("unresolved schema ref %q", ref)
		}
	}
	return current, nil
}

func contains(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}

func sortedKeys(values map[string]bool) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func sitesToBool(values map[string]vcfarch.Site) map[string]bool {
	result := make(map[string]bool, len(values))
	for key := range values {
		result[key] = true
	}
	return result
}
