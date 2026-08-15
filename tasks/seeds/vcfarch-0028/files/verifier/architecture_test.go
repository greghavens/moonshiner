package verifier

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"testing"

	"vcfarch/architecture"
	"vcfarch/internal/jsonschema"
)

func TestResearchArtifact(t *testing.T) {
	root := projectRoot(t)
	content := string(mustRead(t, filepath.Join(root, "research.md")))
	if strings.TrimSpace(content) == "" {
		t.Fatal("research.md is empty")
	}

	urlPattern := regexp.MustCompile(`https?://[^\s<>()]+`)
	datePattern := regexp.MustCompile(`\b20[0-9]{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])\b`)
	seen := map[string]bool{}
	for _, raw := range urlPattern.FindAllString(content, -1) {
		raw = strings.TrimRight(raw, ".,;:")
		parsed, err := url.Parse(raw)
		if err != nil || parsed.Hostname() == "" {
			t.Fatalf("research.md contains an invalid source URL %q", raw)
		}
		host := strings.ToLower(parsed.Hostname())
		if host == "localhost" || host == "127.0.0.1" || strings.HasSuffix(host, ".invalid") {
			t.Fatalf("research.md contains a non-live source URL %q", raw)
		}
		seen[raw] = true
	}
	if len(seen) < 2 {
		t.Fatalf("research.md contains %d distinct source URLs, want at least 2", len(seen))
	}
	if len(datePattern.FindAllString(content, -1)) < len(seen) {
		t.Fatal("research.md must record a consultation date for each source")
	}
	if !strings.Contains(strings.ToLower(content), "broadcom") {
		t.Fatal("research.md does not identify the requested publisher")
	}
}

func TestArchitectureArtifact(t *testing.T) {
	root := projectRoot(t)

	// SddcSpec schema validation intentionally happens before the scenario,
	// snapshot, migration schema, or Build result is read or checked.
	artifactBytes := mustRead(t, filepath.Join(root, "architecture.json"))
	var envelope struct {
		SddcSpec any `json:"sddcSpec"`
	}
	mustUnmarshal(t, artifactBytes, &envelope)
	openAPI := readObject(t, filepath.Join(root, "specifications", "vcf-installer", "vcf-installer-openapi.json"))
	if err := jsonschema.ValidateReference(openAPI, "#/components/schemas/SddcSpec", envelope.SddcSpec); err != nil {
		t.Fatalf("sddcSpec does not validate against installer SddcSpec: %v", err)
	}

	var artifact architecture.Artifact
	mustUnmarshal(t, artifactBytes, &artifact)
	var scenario architecture.Scenario
	readTyped(t, filepath.Join(root, "testdata", "estate.json"), &scenario)
	var snapshot architecture.CompatibilitySnapshot
	readTyped(t, filepath.Join(root, "testdata", "compatibility-snapshot.json"), &snapshot)

	migrationSchema := readObject(t, filepath.Join(root, "schemas", "migration-plan.schema.json"))
	migrationValue := asJSONValue(t, artifact.MigrationPlan)
	if err := jsonschema.Validate(migrationSchema, migrationSchema, migrationValue); err != nil {
		t.Fatalf("migrationPlan does not validate against migration-plan.schema.json: %v", err)
	}

	checks := []struct {
		name string
		fn   func() error
	}{
		{"builder-matches-artifact", func() error {
			built, err := architecture.Build(filepath.Join(root, "testdata", "estate.json"), filepath.Join(root, "testdata", "compatibility-snapshot.json"))
			if err != nil {
				return fmt.Errorf("Build returned an error: %w", err)
			}
			if !reflect.DeepEqual(asJSONValue(t, built), asJSONValue(t, artifact)) {
				return fmt.Errorf("Build result differs from architecture.json")
			}
			return nil
		}},
		{"release-and-identity", func() error { return checkIdentity(artifact, scenario, snapshot) }},
		{"entitlement-selects-topology", func() error { return checkTopology(artifact, scenario, snapshot) }},
		{"host-placement-and-capacity", func() error { return checkPlacementAndCapacity(artifact, scenario, snapshot) }},
		{"installer-inputs", func() error { return checkInstallerInputs(artifact, scenario, snapshot) }},
		{"day-n-stretch", func() error { return checkDayN(artifact, scenario) }},
		{"migration-components-order-targets-and-gates", func() error { return checkMigration(artifact, scenario, snapshot) }},
	}
	for _, check := range checks {
		t.Run(check.name, func(t *testing.T) {
			if err := check.fn(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func checkIdentity(artifact architecture.Artifact, scenario architecture.Scenario, snapshot architecture.CompatibilitySnapshot) error {
	if artifact.SchemaVersion != "1.0" {
		return fmt.Errorf("schemaVersion = %q, want 1.0", artifact.SchemaVersion)
	}
	if artifact.ScenarioID != scenario.ScenarioID {
		return fmt.Errorf("scenarioId = %q, want %q", artifact.ScenarioID, scenario.ScenarioID)
	}
	if scenario.TargetRelease != snapshot.TargetRelease || scenario.Entitlement.EligibleTargetRelease != scenario.TargetRelease {
		return fmt.Errorf("fixture and snapshot target releases are inconsistent")
	}
	return nil
}

func checkTopology(artifact architecture.Artifact, scenario architecture.Scenario, snapshot architecture.CompatibilitySnapshot) error {
	var selected, excluded *architecture.SupportedTopology
	for i := range snapshot.Topologies {
		topology := &snapshot.Topologies[i]
		switch topology.ID {
		case "single-vcf-instance-stretched-management-domain":
			selected = topology
		case "dual-vcf-independent-instances":
			excluded = topology
		}
	}
	if selected == nil || excluded == nil {
		return fmt.Errorf("pinned topology definitions are incomplete")
	}
	if artifact.SelectedTopology.ID != selected.ID {
		return fmt.Errorf("selected topology = %q, want %q", artifact.SelectedTopology.ID, selected.ID)
	}
	if selected.VCFInstances > scenario.Entitlement.MaxVCFInstances || selected.RequiresVSANStretchEntitlement && !scenario.Entitlement.VSANStretchedClusters {
		return fmt.Errorf("selected topology violates entitlement")
	}
	if len(scenario.Hosts) > scenario.Entitlement.MaxLicensedHosts {
		return fmt.Errorf("selected hosts exceed licensed-host entitlement")
	}
	if excluded.VCFInstances <= scenario.Entitlement.MaxVCFInstances {
		return fmt.Errorf("dual-instance topology is not excluded by fixture entitlement")
	}
	if artifact.SelectedTopology.VCFInstances != selected.VCFInstances || artifact.SelectedTopology.ManagementDomains != selected.ManagementDomains {
		return fmt.Errorf("selected instance or management-domain count does not match pinned topology")
	}
	if !selected.SurvivesCompleteDataSiteLoss || !scenario.AvailabilityRequirements.SurviveCompleteDataSiteLoss ||
		selected.RPOMinutes > scenario.AvailabilityRequirements.MaxRPOMinutes ||
		selected.ManagementRecoveryMinutes > scenario.AvailabilityRequirements.MaxManagementRecoveryMinutes {
		return fmt.Errorf("selected topology does not meet availability requirements")
	}
	if len(artifact.ExcludedTopologies) != 1 {
		return fmt.Errorf("excludedTopologies has %d entries, want 1", len(artifact.ExcludedTopologies))
	}
	rejected := artifact.ExcludedTopologies[0]
	if rejected.ID != excluded.ID || rejected.ReasonCode != "ENTITLEMENT_MAX_VCF_INSTANCES" || rejected.ConstraintID != scenario.Entitlement.ConstraintID {
		return fmt.Errorf("excluded topology does not identify the instance entitlement constraint")
	}
	return checkLinks(scenario, *selected)
}

func checkLinks(scenario architecture.Scenario, topology architecture.SupportedTopology) error {
	dataSites := siteIDs(scenario.Sites, "data")
	witnessSites := siteIDs(scenario.Sites, "witness")
	if len(dataSites) != topology.DataSites || len(witnessSites) != topology.WitnessSites {
		return fmt.Errorf("fixture site counts do not meet topology")
	}
	for _, link := range scenario.Links {
		fromData := contains(dataSites, link.From)
		toData := contains(dataSites, link.To)
		if fromData && toData && link.RTTMillis > topology.MaxDataSiteRTTMillis {
			return fmt.Errorf("data-site RTT exceeds pinned topology maximum")
		}
		if (contains(witnessSites, link.From) || contains(witnessSites, link.To)) && link.RTTMillis > topology.MaxWitnessRTTMillis {
			return fmt.Errorf("witness RTT exceeds pinned topology maximum")
		}
	}
	return nil
}

func checkPlacementAndCapacity(artifact architecture.Artifact, scenario architecture.Scenario, snapshot architecture.CompatibilitySnapshot) error {
	dataSites := siteIDs(scenario.Sites, "data")
	if !reflect.DeepEqual(artifact.SelectedTopology.DataSites, dataSites) {
		return fmt.Errorf("dataSites = %v, want %v", artifact.SelectedTopology.DataSites, dataSites)
	}
	witnessSites := siteIDs(scenario.Sites, "witness")
	if len(witnessSites) != 1 || artifact.SelectedTopology.WitnessSite != witnessSites[0] {
		return fmt.Errorf("witnessSite is incorrect")
	}

	minCores, minMemory := int(^uint(0)>>1), int(^uint(0)>>1)
	for _, site := range dataSites {
		var expected []string
		cores, memory := 0, 0
		for _, host := range scenario.Hosts {
			if host.Site == site {
				expected = append(expected, host.Hostname)
				cores += host.Cores
				memory += host.MemoryGiB
			}
		}
		if len(expected) < pinnedTopology(snapshot, artifact.SelectedTopology.ID).MinHostsPerDataSite {
			return fmt.Errorf("site %s has too few hosts", site)
		}
		actual := append([]string(nil), artifact.SelectedTopology.HostPlacement[site]...)
		sort.Strings(actual)
		sort.Strings(expected)
		if !reflect.DeepEqual(actual, expected) {
			return fmt.Errorf("host placement for %s = %v, want %v", site, actual, expected)
		}
		if cores < minCores {
			minCores = cores
		}
		if memory < minMemory {
			minMemory = memory
		}
	}
	want := architecture.Capacity{
		PhysicalCores:    minCores,
		MemoryGiB:        minMemory,
		UsableStorageTiB: pinnedTopology(snapshot, artifact.SelectedTopology.ID).UsableStorageTiB,
	}
	if artifact.SelectedTopology.CapacityAfterSiteLoss != want {
		return fmt.Errorf("capacityAfterSiteLoss = %+v, want %+v", artifact.SelectedTopology.CapacityAfterSiteLoss, want)
	}
	if want.PhysicalCores < scenario.CapacityRequirements.PhysicalCores || want.MemoryGiB < scenario.CapacityRequirements.MemoryGiB || want.UsableStorageTiB < scenario.CapacityRequirements.UsableStorageTiB {
		return fmt.Errorf("post-site-loss capacity does not meet requirements")
	}
	return nil
}

func checkInstallerInputs(artifact architecture.Artifact, scenario architecture.Scenario, snapshot architecture.CompatibilitySnapshot) error {
	spec := rawObject(artifact.SddcSpec)
	input := scenario.InstallerInputs
	profile := snapshot.InstallerProfile
	checks := []struct {
		path string
		want any
	}{
		{"sddcId", input.SddcID},
		{"workflowType", profile.WorkflowType},
		{"version", scenario.TargetRelease},
		{"vcfInstanceName", input.VCFInstanceName},
		{"dnsSpec.subdomain", input.Subdomain},
		{"dnsSpec.nameservers", stringsAny(input.NameServers)},
		{"ntpServers", stringsAny(input.NTPServers)},
		{"vcenterSpec.vcenterHostname", input.VCenterHostname},
		{"vcenterSpec.useExistingDeployment", false},
		{"clusterSpec.datacenterName", input.DatacenterName},
		{"clusterSpec.clusterName", input.ClusterName},
		{"sddcManagerSpec.hostname", input.SDDCManagerHostname},
		{"managementPoolName", input.ManagementPoolName},
		{"nsxtSpec.vipFqdn", input.NSXVIPFQDN},
		{"licenseServerSpec.hostname", input.LicenseServerHostname},
		{"vspClusterSpec.platformFqdn", input.VSPPlatformFQDN},
		{"vspClusterSpec.instanceFqdn", input.VSPInstanceFQDN},
		{"vspClusterSpec.fleetFqdn", input.VSPFleetFQDN},
		{"datastoreSpec.vsanSpec.failuresToTolerate", float64(profile.VSANFailuresToTolerate)},
		{"datastoreSpec.vsanSpec.esaConfig.enabled", true},
	}
	for _, check := range checks {
		got, ok := lookup(spec, check.path)
		if !ok || !reflect.DeepEqual(got, check.want) {
			return fmt.Errorf("sddcSpec.%s = %#v, want %#v", check.path, got, check.want)
		}
	}

	hosts, ok := spec["hostSpecs"].([]any)
	if !ok || len(hosts) != profile.InitialHostCount {
		return fmt.Errorf("initial host count is incorrect")
	}
	var gotHosts []string
	for _, raw := range hosts {
		gotHosts = append(gotHosts, raw.(map[string]any)["hostname"].(string))
	}
	wantHosts := hostsAtSite(scenario.Hosts, input.InitialSite)
	sort.Strings(gotHosts)
	sort.Strings(wantHosts)
	if !reflect.DeepEqual(gotHosts, wantHosts) {
		return fmt.Errorf("initial installer hosts = %v, want %v", gotHosts, wantHosts)
	}

	networks, ok := spec["networkSpecs"].([]any)
	if !ok || len(networks) != len(input.Networks) {
		return fmt.Errorf("networkSpecs count is incorrect")
	}
	for _, expected := range input.Networks {
		var found map[string]any
		for _, raw := range networks {
			candidate := raw.(map[string]any)
			if candidate["networkType"] == expected.Type {
				found = candidate
				break
			}
		}
		if found == nil || found["vlanId"] != float64(expected.VLAN) || found["subnet"] != expected.CIDR || found["gateway"] != expected.Gateway || found["subnetMask"] != expected.SubnetMask || found["mtu"] != float64(expected.MTU) {
			return fmt.Errorf("network %s does not match fixture", expected.Type)
		}
	}
	for _, required := range profile.RequiredNetworkTypes {
		found := false
		for _, network := range input.Networks {
			found = found || network.Type == required
		}
		if !found {
			return fmt.Errorf("required network %s is missing", required)
		}
	}

	managers, _ := lookup(spec, "nsxtSpec.nsxtManagers")
	if len(managers.([]any)) != profile.NSXManagerCount {
		return fmt.Errorf("NSX manager count is incorrect")
	}
	nodes, _ := lookup(spec, "vcfOperationsSpec.nodes")
	if len(nodes.([]any)) != profile.VCFOperationsNodeCount {
		return fmt.Errorf("VCF Operations node count is incorrect")
	}
	addresses, _ := lookup(spec, "vspClusterSpec.ipv4Pool.addresses")
	if !reflect.DeepEqual(addresses, stringsAny(input.ManagementServiceIPs)) || len(addresses.([]any)) < profile.MinManagementServiceIPs {
		return fmt.Errorf("management service IP pool is incorrect")
	}
	password, ok := lookup(spec, "vcenterSpec.rootVcenterPassword")
	if !ok || password == "" || password == "Sample_Password123" {
		return fmt.Errorf("vCenter password must be a non-example placeholder")
	}
	return nil
}

func checkDayN(artifact architecture.Artifact, scenario architecture.Scenario) error {
	if artifact.DayN.Action != "stretch-management-domain" {
		return fmt.Errorf("dayN action = %q", artifact.DayN.Action)
	}
	dataSites := siteIDs(scenario.Sites, "data")
	if len(dataSites) != 2 {
		return fmt.Errorf("fixture does not have two data sites")
	}
	wantHosts := hostsAtSite(scenario.Hosts, dataSites[1])
	gotHosts := append([]string(nil), artifact.DayN.AddHosts...)
	sort.Strings(wantHosts)
	sort.Strings(gotHosts)
	if !reflect.DeepEqual(gotHosts, wantHosts) {
		return fmt.Errorf("dayN addHosts = %v, want %v", gotHosts, wantHosts)
	}
	for _, site := range scenario.Sites {
		if site.Role == "witness" && artifact.DayN.WitnessHost != site.WitnessHost {
			return fmt.Errorf("dayN witnessHost = %q, want %q", artifact.DayN.WitnessHost, site.WitnessHost)
		}
	}
	return nil
}

func checkMigration(artifact architecture.Artifact, scenario architecture.Scenario, snapshot architecture.CompatibilitySnapshot) error {
	plan := artifact.MigrationPlan
	if plan.SchemaVersion != "1.0" || plan.EstateID != scenario.ExistingEstate.EstateID || plan.TargetVCFVersion != snapshot.Migration.TargetVCFVersion {
		return fmt.Errorf("migration plan identity is incorrect")
	}
	if len(plan.Steps) != len(scenario.ExistingEstate.Components) || len(plan.Steps) != len(snapshot.Migration.Steps) {
		return fmt.Errorf("migration plan must contain one step per inventory component")
	}
	inventory := make(map[string]architecture.ExistingComponent, len(scenario.ExistingEstate.Components))
	for _, component := range scenario.ExistingEstate.Components {
		inventory[component.ID] = component
	}
	seen := map[string]bool{}
	for i, pinned := range snapshot.Migration.Steps {
		step := plan.Steps[i]
		component, ok := inventory[pinned.ComponentID]
		if !ok {
			return fmt.Errorf("snapshot component %s is absent from inventory", pinned.ComponentID)
		}
		if seen[step.ComponentID] {
			return fmt.Errorf("component %s appears more than once", step.ComponentID)
		}
		seen[step.ComponentID] = true
		if step.Order != pinned.Order || step.Order != i+1 || step.ComponentID != pinned.ComponentID || step.ComponentName != component.Name || step.FromVersion != component.Version || step.Action != pinned.Action || step.TargetVersion != pinned.TargetVersion || !reflect.DeepEqual(step.Gates, pinned.Gates) {
			return fmt.Errorf("migration step %d does not match inventory and pinned authority", i+1)
		}
	}
	return nil
}

func projectRoot(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate verifier source")
	}
	return filepath.Dir(filepath.Dir(file))
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return b
}

func mustUnmarshal(t *testing.T, b []byte, dst any) {
	t.Helper()
	if err := json.Unmarshal(b, dst); err != nil {
		t.Fatalf("decode JSON: %v", err)
	}
}

func readObject(t *testing.T, path string) map[string]any {
	t.Helper()
	var value map[string]any
	mustUnmarshal(t, mustRead(t, path), &value)
	return value
}

func readTyped(t *testing.T, path string, dst any) {
	t.Helper()
	mustUnmarshal(t, mustRead(t, path), dst)
}

func asJSONValue(t *testing.T, value any) any {
	t.Helper()
	b, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("marshal JSON value: %v", err)
	}
	var result any
	mustUnmarshal(t, b, &result)
	return result
}

func rawObject(raw json.RawMessage) map[string]any {
	var result map[string]any
	if err := json.Unmarshal(raw, &result); err != nil {
		panic(err)
	}
	return result
}

func lookup(object map[string]any, path string) (any, bool) {
	var current any = object
	for _, part := range splitPath(path) {
		m, ok := current.(map[string]any)
		if !ok {
			return nil, false
		}
		current, ok = m[part]
		if !ok {
			return nil, false
		}
	}
	return current, true
}

func splitPath(path string) []string {
	var parts []string
	start := 0
	for i, r := range path {
		if r == '.' {
			parts = append(parts, path[start:i])
			start = i + 1
		}
	}
	return append(parts, path[start:])
}

func siteIDs(sites []architecture.Site, role string) []string {
	var ids []string
	for _, site := range sites {
		if site.Role == role {
			ids = append(ids, site.ID)
		}
	}
	return ids
}

func hostsAtSite(hosts []architecture.Host, site string) []string {
	var names []string
	for _, host := range hosts {
		if host.Site == site {
			names = append(names, host.Hostname)
		}
	}
	return names
}

func pinnedTopology(snapshot architecture.CompatibilitySnapshot, id string) architecture.SupportedTopology {
	for _, topology := range snapshot.Topologies {
		if topology.ID == id {
			return topology
		}
	}
	panic("topology not found: " + id)
}

func contains(values []string, want string) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}

func stringsAny(values []string) []any {
	result := make([]any, len(values))
	for i, value := range values {
		result[i] = value
	}
	return result
}
