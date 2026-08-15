package grader_tests_test

import (
	"bytes"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"

	"vcfarch/architecture"
	"vcfarch/internal/verifier"
)

func TestArchitecture(t *testing.T) {
	root := projectRoot(t)

	// This is intentionally the first acceptance check. All subsequent checks
	// are meaningful only after the submitted greenfield artifact is proven to
	// be an SddcSpec according to the vendored installer specification itself.
	installerSchema := mustRead(t, filepath.Join(root, "specifications/vcf-installer/vcf-installer-openapi.json"))
	sddcArtifact := mustRead(t, filepath.Join(root, "out/sddc-spec.json"))
	if err := verifier.ValidateOpenAPI(installerSchema, sddcArtifact, "SddcSpec"); err != nil {
		t.Fatalf("SddcSpec schema validation failed: %v", err)
	}

	var estate architecture.Estate
	mustUnmarshal(t, mustRead(t, filepath.Join(root, "fixtures/estate.json")), &estate)
	var snapshot architecture.CompatibilitySnapshot
	mustUnmarshal(t, mustRead(t, filepath.Join(root, "authority/compatibility-snapshot.json")), &snapshot)

	migrationArtifact := mustRead(t, filepath.Join(root, "out/migration-plan.json"))
	migrationSchema := mustRead(t, filepath.Join(root, "schemas/migration-plan.schema.json"))
	if err := verifier.ValidateJSONSchema(migrationSchema, migrationArtifact); err != nil {
		t.Fatalf("migration plan schema validation failed: %v", err)
	}

	var edge architecture.EdgeDesign
	mustUnmarshal(t, mustRead(t, filepath.Join(root, "out/edge-design.json")), &edge)
	var migration architecture.MigrationPlan
	mustUnmarshal(t, migrationArtifact, &migration)

	assertSddcSemantics(t, sddcArtifact, estate.Greenfield)
	assertEdgeSemantics(t, edge, estate.Greenfield, snapshot)
	assertMigrationSemantics(t, migration, estate, snapshot)

	design, err := architecture.Build(estate, snapshot)
	if err != nil {
		t.Fatalf("Build returned an error: %v", err)
	}
	assertJSONEqual(t, "Build SddcSpec", sddcArtifact, design.SddcSpec)
	assertJSONEqual(t, "Build EdgeDesign", mustRead(t, filepath.Join(root, "out/edge-design.json")), mustMarshal(t, design.EdgeDesign))
	assertJSONEqual(t, "Build MigrationPlan", migrationArtifact, mustMarshal(t, design.MigrationPlan))

	second, err := architecture.Build(estate, snapshot)
	if err != nil {
		t.Fatalf("second Build returned an error: %v", err)
	}
	assertJSONEqual(t, "deterministic Build", mustMarshal(t, design), mustMarshal(t, second))
}

func TestDesignLogic(t *testing.T) {
	estate := readEstate(t)
	snapshot := readSnapshot(t)

	tests := []struct {
		name       string
		mutate     func(*architecture.Estate, *architecture.CompatibilitySnapshot)
		wantFactor string
		wantDemand float64
		wantError  bool
	}{
		{
			name: "non-HA demand is shared by both nodes",
			mutate: func(estate *architecture.Estate, _ *architecture.CompatibilitySnapshot) {
				estate.Greenfield.Edge.SurviveSingleNodeFailure = false
				estate.Greenfield.Edge.UplinkProfileID = "DUAL_10G_DISTINCT_TOR"
			},
			wantFactor: "LARGE",
			wantDemand: 9,
		},
		{
			name: "form-factor boundary is inclusive",
			mutate: func(estate *architecture.Estate, _ *architecture.CompatibilitySnapshot) {
				estate.Greenfield.Edge.NorthSouthGbps = 4
				estate.Greenfield.Edge.SurviveSingleNodeFailure = false
				estate.Greenfield.Edge.UplinkProfileID = "DUAL_10G_DISTINCT_TOR"
			},
			wantFactor: "MEDIUM",
			wantDemand: 2,
		},
		{
			name: "HA requires a positive node count",
			mutate: func(estate *architecture.Estate, _ *architecture.CompatibilitySnapshot) {
				estate.Greenfield.Edge.NodeCount = 0
				estate.Greenfield.Edge.PlacementZones = nil
			},
			wantError: true,
		},
		{
			name: "uplinks must use distinct TORs",
			mutate: func(estate *architecture.Estate, _ *architecture.CompatibilitySnapshot) {
				estate.Greenfield.Edge.PhysicalUplinks[1].TOR = estate.Greenfield.Edge.PhysicalUplinks[0].TOR
			},
			wantError: true,
		},
		{
			name: "greenfield and migration targets must match",
			mutate: func(estate *architecture.Estate, _ *architecture.CompatibilitySnapshot) {
				estate.Existing.TargetVCFVersion = "9.0.0.0"
			},
			wantError: true,
		},
		{
			name: "every authority gate needs a condition",
			mutate: func(_ *architecture.Estate, snapshot *architecture.CompatibilitySnapshot) {
				snapshot.SupportedUpgradeHops[0].RequiredGates = append(snapshot.SupportedUpgradeHops[0].RequiredGates, "UNDEFINED_GATE")
			},
			wantError: true,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			candidateEstate := cloneJSON(t, estate)
			candidateSnapshot := cloneJSON(t, snapshot)
			test.mutate(&candidateEstate, &candidateSnapshot)
			design, err := architecture.Build(candidateEstate, candidateSnapshot)
			if test.wantError {
				if err == nil {
					t.Fatal("Build succeeded, want an error")
				}
				return
			}
			if err != nil {
				t.Fatalf("Build failed: %v", err)
			}
			if design.EdgeDesign.FormFactor != test.wantFactor {
				t.Errorf("form factor = %q, want %q", design.EdgeDesign.FormFactor, test.wantFactor)
			}
			if design.EdgeDesign.PerSurvivingNodeGbps != test.wantDemand {
				t.Errorf("per-node demand = %v, want %v", design.EdgeDesign.PerSurvivingNodeGbps, test.wantDemand)
			}
		})
	}
}

func TestResearchRecord(t *testing.T) {
	record := string(mustRead(t, filepath.Join(projectRoot(t), "research.md")))
	lower := strings.ToLower(record)
	if strings.Contains(lower, "replace this placeholder") {
		t.Fatal("research.md still contains the seed placeholder")
	}
	datePatterns := []*regexp.Regexp{
		regexp.MustCompile(`\b20[0-9]{2}-(0[1-9]|1[0-2])-([0-2][0-9]|3[01])\b`),
		regexp.MustCompile(`(?i)\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+[0-3]?[0-9],?\s+20[0-9]{2}\b`),
		regexp.MustCompile(`\b(0?[1-9]|1[0-2])/([0-2]?[0-9]|3[01])/20[0-9]{2}\b`),
	}
	hasDate := false
	for _, pattern := range datePatterns {
		hasDate = hasDate || pattern.MatchString(record)
	}
	if !hasDate {
		t.Error("research.md does not record a retrieval date")
	}

	broadcomURL := regexp.MustCompile(`https://(?:[a-z0-9-]+\.)*broadcom\.com/[^\s|)>]+`)
	sources := map[string]bool{}
	for _, match := range broadcomURL.FindAllString(record, -1) {
		sources[match] = true
	}
	if len(sources) < 2 {
		t.Errorf("research.md cites %d distinct Broadcom sources, want at least 2", len(sources))
	}

	topics := []struct {
		name   string
		groups [][]string
	}{
		{name: "target compatibility/interoperability", groups: [][]string{{"compatib", "interop"}, {"9.1"}}},
		{name: "Edge sizing and uplinks", groups: [][]string{{"edge"}, {"sizing", "form factor", "vcpu"}, {"uplink"}}},
		{name: "upgrade paths", groups: [][]string{{"upgrade"}, {"5.2"}, {"9.1"}}},
	}
	for _, topic := range topics {
		for _, alternatives := range topic.groups {
			found := false
			for _, alternative := range alternatives {
				if strings.Contains(lower, alternative) {
					found = true
					break
				}
			}
			if !found {
				t.Errorf("research.md does not cover %s", topic.name)
				break
			}
		}
	}
}

func TestSubmittedTableDrivenTests(t *testing.T) {
	paths, err := filepath.Glob(filepath.Join(projectRoot(t), "architecture", "*_test.go"))
	if err != nil {
		t.Fatal(err)
	}
	tableDriven := false
	for _, path := range paths {
		parsed, err := parser.ParseFile(token.NewFileSet(), path, mustRead(t, path), 0)
		if err != nil {
			t.Fatalf("parse %s: %v", path, err)
		}
		for _, declaration := range parsed.Decls {
			function, ok := declaration.(*ast.FuncDecl)
			if !ok || function.Body == nil || !strings.HasPrefix(function.Name.Name, "Test") {
				continue
			}
			ast.Inspect(function.Body, func(node ast.Node) bool {
				switch node.(type) {
				case *ast.RangeStmt, *ast.ForStmt:
					tableDriven = true
				}
				return true
			})
		}
	}
	if !tableDriven {
		t.Error("architecture tests do not contain a table-driven test")
	}
}

func assertSddcSemantics(t *testing.T, raw []byte, green architecture.Greenfield) {
	t.Helper()
	var spec map[string]any
	mustUnmarshal(t, raw, &spec)
	equal := func(field string, got, want any) {
		t.Helper()
		if !reflect.DeepEqual(got, want) {
			t.Errorf("SddcSpec %s = %#v, want %#v", field, got, want)
		}
	}
	equal("sddcId", spec["sddcId"], green.SDDCID)
	equal("workflowType", spec["workflowType"], "VCF")
	equal("version", spec["version"], green.TargetVCFVersion)
	equal("vcfInstanceName", spec["vcfInstanceName"], green.VCFInstanceName)
	equal("skipEsxThumbprintValidation", spec["skipEsxThumbprintValidation"], false)
	equal("skipGatewayPingValidation", spec["skipGatewayPingValidation"], false)

	dns := mustObject(t, spec, "dnsSpec")
	equal("dnsSpec.subdomain", dns["subdomain"], green.Domain)
	equal("dnsSpec.nameservers", stringSlice(dns["nameservers"]), green.DNS)
	equal("ntpServers", stringSlice(spec["ntpServers"]), green.NTP)

	vcenter := mustObject(t, spec, "vcenterSpec")
	equal("vcenterSpec.vcenterHostname", vcenter["vcenterHostname"], green.VCenterFQDN)
	equal("vcenterSpec.useExistingDeployment", vcenter["useExistingDeployment"], false)
	sddcManager := mustObject(t, spec, "sddcManagerSpec")
	equal("sddcManagerSpec.hostname", sddcManager["hostname"], green.SDDCManagerFQDN)
	equal("sddcManagerSpec.useExistingDeployment", sddcManager["useExistingDeployment"], false)

	nsx := mustObject(t, spec, "nsxtSpec")
	equal("nsxtSpec.version", nsx["version"], green.TargetVCFVersion)
	equal("nsxtSpec.useExistingDeployment", nsx["useExistingDeployment"], false)
	managerNames := []string{}
	for _, item := range mustArray(t, nsx, "nsxtManagers") {
		managerNames = append(managerNames, mustObjectValue(t, item)["hostname"].(string))
	}
	equal("nsxtSpec.nsxtManagers", managerNames, green.NSXManagerFQDNs)

	hosts := []string{}
	for _, item := range mustArray(t, spec, "hostSpecs") {
		hosts = append(hosts, mustObjectValue(t, item)["hostname"].(string))
	}
	equal("hostSpecs hostnames", hosts, green.Management.Hosts)

	cluster := mustObject(t, spec, "clusterSpec")
	equal("clusterSpec.datacenterName", cluster["datacenterName"], green.Management.DatacenterName)
	equal("clusterSpec.clusterName", cluster["clusterName"], green.Management.ClusterName)
	datastore := mustObject(t, spec, "datastoreSpec")
	vsan := mustObject(t, datastore, "vsanSpec")
	equal("datastoreSpec.vsanSpec.failuresToTolerate", intValue(vsan["failuresToTolerate"]), green.Management.FailuresToTolerate)
	esa := mustObject(t, vsan, "esaConfig")
	equal("datastoreSpec.vsanSpec.esaConfig.enabled", esa["enabled"], green.Management.VsanArchitecture == "ESA")

	dvsItems := mustArray(t, spec, "dvsSpecs")
	if len(dvsItems) != 1 {
		t.Fatalf("SddcSpec dvsSpecs has %d entries, want 1", len(dvsItems))
	}
	dvs := mustObjectValue(t, dvsItems[0])
	equal("dvsSpecs[0].dvsName", dvs["dvsName"], green.Management.DVSName)
	mappings := mustArray(t, dvs, "vmnicsToUplinks")
	if len(mappings) != len(green.Management.DVSUplinks) {
		t.Fatalf("DVS mappings count = %d, want %d", len(mappings), len(green.Management.DVSUplinks))
	}
	for i, expected := range green.Management.DVSUplinks {
		mapping := mustObjectValue(t, mappings[i])
		equal(fmt.Sprintf("dvs mapping %d id", i), mapping["id"], expected.PhysicalNIC)
		equal(fmt.Sprintf("dvs mapping %d uplink", i), mapping["uplink"], expected.Name)
	}

	networks := map[string]map[string]any{}
	for _, item := range mustArray(t, spec, "networkSpecs") {
		network := mustObjectValue(t, item)
		networks[network["networkType"].(string)] = network
	}
	if len(networks) != len(green.Networks) {
		t.Errorf("networkSpecs count = %d, want %d", len(networks), len(green.Networks))
	}
	for _, expected := range green.Networks {
		actual, ok := networks[expected.Type]
		if !ok {
			t.Errorf("networkSpecs missing %s", expected.Type)
			continue
		}
		equal(expected.Type+" vlanId", intValue(actual["vlanId"]), expected.VLAN)
		equal(expected.Type+" subnet", actual["subnet"], expected.CIDR)
		equal(expected.Type+" gateway", actual["gateway"], expected.Gateway)
		equal(expected.Type+" mtu", intValue(actual["mtu"]), expected.MTU)
		ranges := mustArray(t, actual, "includeIpAddressRanges")
		if len(ranges) != len(expected.IPRanges) {
			t.Errorf("%s ranges = %d, want %d", expected.Type, len(ranges), len(expected.IPRanges))
			continue
		}
		for index, expectedRange := range expected.IPRanges {
			actualRange := mustObjectValue(t, ranges[index])
			equal(expected.Type+" start range", actualRange["startIpAddress"], expectedRange.Start)
			equal(expected.Type+" end range", actualRange["endIpAddress"], expectedRange.End)
		}
	}
}

func assertEdgeSemantics(t *testing.T, edge architecture.EdgeDesign, green architecture.Greenfield, snapshot architecture.CompatibilitySnapshot) {
	t.Helper()
	if !reflect.DeepEqual(edge.Sites, green.Sites) {
		t.Errorf("edge artifact site capacity/availability = %+v, want %+v", edge.Sites, green.Sites)
	}
	demand := green.Edge.NorthSouthGbps
	if !green.Edge.SurviveSingleNodeFailure {
		demand /= float64(green.Edge.NodeCount)
	}
	var factor *architecture.EdgeFormFactor
	for i := range snapshot.EdgeFormFactors {
		candidate := &snapshot.EdgeFormFactors[i]
		if demand > candidate.MinimumExclusive && demand <= candidate.MaximumInclusive {
			factor = candidate
			break
		}
	}
	if factor == nil {
		t.Fatalf("snapshot has no form factor for %.1f Gbps", demand)
	}
	if edge.SiteID != green.Edge.SiteID || edge.FormFactor != factor.Name || edge.VCPUPerNode != factor.VCPU || edge.MemoryGiBPerNode != factor.MemoryGiB {
		t.Errorf("edge sizing does not match selected factor %+v: %+v", *factor, edge)
	}
	if edge.NodeCount != green.Edge.NodeCount || edge.HAMode != green.Edge.HAMode || edge.NorthSouthGbps != green.Edge.NorthSouthGbps || edge.PerSurvivingNodeGbps != demand {
		t.Errorf("edge capacity/HA fields do not realize fixture demand: %+v", edge)
	}
	var profile *architecture.UplinkProfile
	for i := range snapshot.UplinkProfiles {
		if snapshot.UplinkProfiles[i].ID == green.Edge.UplinkProfileID {
			profile = &snapshot.UplinkProfiles[i]
		}
	}
	if profile == nil {
		t.Fatalf("fixture profile %s is absent from snapshot", green.Edge.UplinkProfileID)
	}
	if edge.UplinkProfileID != profile.ID || !(demand > profile.MinimumDemandExclusive && demand <= profile.MaximumDemandInclusive) {
		t.Errorf("edge uplink profile is incompatible with demand: %+v", edge)
	}
	if len(edge.Nodes) != green.Edge.NodeCount {
		t.Fatalf("edge node count = %d, want %d", len(edge.Nodes), green.Edge.NodeCount)
	}
	for index, node := range edge.Nodes {
		if node.Name != fmt.Sprintf("%s-edge%02d", green.Edge.SiteID, index+1) {
			t.Errorf("edge node %d name = %q", index, node.Name)
		}
		if node.AvailabilityZone != green.Edge.PlacementZones[index] {
			t.Errorf("edge node %d availability zone = %q, want %q", index, node.AvailabilityZone, green.Edge.PlacementZones[index])
		}
		if !reflect.DeepEqual(node.Uplinks, green.Edge.PhysicalUplinks) {
			t.Errorf("edge node %d uplinks = %+v, want %+v", index, node.Uplinks, green.Edge.PhysicalUplinks)
		}
		if len(node.Uplinks) < profile.MinimumUplinksPerNode {
			t.Errorf("edge node %d has too few uplinks", index)
		}
		tors, vlans := map[string]bool{}, map[int]bool{}
		for _, uplink := range node.Uplinks {
			if uplink.SpeedGbps < profile.MinimumLinkSpeedGbps {
				t.Errorf("edge node %d uplink %s is too slow", index, uplink.Name)
			}
			tors[uplink.TOR], vlans[uplink.VLAN] = true, true
		}
		if profile.RequireDistinctTORs && len(tors) != len(node.Uplinks) {
			t.Errorf("edge node %d does not use distinct TORs", index)
		}
		if profile.RequireDistinctVLANs && len(vlans) != len(node.Uplinks) {
			t.Errorf("edge node %d does not use distinct VLANs", index)
		}
	}
	if edge.TEP.VLAN != green.Edge.TEPVLAN || edge.TEP.MTU != green.Edge.TEPMTU || edge.TEP.TeamingPolicy != "LOADBALANCE_SRCID" {
		t.Errorf("edge TEP design = %+v, want VLAN %d MTU %d LOADBALANCE_SRCID", edge.TEP, green.Edge.TEPVLAN, green.Edge.TEPMTU)
	}
}

func assertMigrationSemantics(t *testing.T, plan architecture.MigrationPlan, estate architecture.Estate, snapshot architecture.CompatibilitySnapshot) {
	t.Helper()
	if plan.EstateID != estate.EstateID || plan.TargetVCFVersion != estate.Existing.TargetVCFVersion {
		t.Errorf("migration plan identity/target does not match fixture: %+v", plan)
	}
	current := map[string]string{}
	target := map[string]string{}
	seenComponent := map[string]bool{}
	ranks := map[string]map[string]int{}
	for _, component := range estate.Existing.Components {
		current[component.Name] = component.CurrentVersion
		target[component.Name] = component.TargetVersion
		ranks[component.Name] = versionRanks(component, snapshot.SupportedUpgradeHops)
		if snapshot.TargetCombination[component.Name] != component.TargetVersion {
			t.Fatalf("fixture target for %s is outside pinned combination", component.Name)
		}
	}
	if len(plan.Steps) != len(snapshot.SupportedUpgradeHops) {
		t.Errorf("migration has %d steps, want one for each of %d supported hops", len(plan.Steps), len(snapshot.SupportedUpgradeHops))
	}
	usedHop := map[string]bool{}
	for index, step := range plan.Steps {
		if step.Order != index+1 {
			t.Errorf("migration step %d order = %d, want %d", index, step.Order, index+1)
		}
		if _, exists := current[step.Component]; !exists {
			t.Fatalf("migration step %d names unknown component %s", index+1, step.Component)
		}
		if step.FromVersion != current[step.Component] {
			t.Fatalf("migration step %d starts %s at %s, current is %s", index+1, step.Component, step.FromVersion, current[step.Component])
		}
		hop, ok := findHop(snapshot.SupportedUpgradeHops, step.Component, step.FromVersion, step.TargetVersion)
		if !ok {
			t.Fatalf("migration step %d is not a pinned supported hop: %+v", index+1, step)
		}
		hopKey := step.Component + "\x00" + step.FromVersion + "\x00" + step.TargetVersion
		if usedHop[hopKey] {
			t.Fatalf("migration repeats hop %q", hopKey)
		}
		usedHop[hopKey] = true
		actualGates := make([]string, 0, len(step.Gates))
		for _, gate := range step.Gates {
			actualGates = append(actualGates, gate.ID)
		}
		sort.Strings(actualGates)
		expectedGates := append([]string(nil), hop.RequiredGates...)
		sort.Strings(expectedGates)
		if !reflect.DeepEqual(actualGates, expectedGates) {
			t.Errorf("migration step %d gates = %v, want %v", index+1, actualGates, expectedGates)
		}
		for _, dependency := range snapshot.UpgradeDependencies {
			if dependency.Component != step.Component || dependency.ToVersion != step.TargetVersion {
				continue
			}
			actualRank, actualOK := ranks[dependency.RequiresComponent][current[dependency.RequiresComponent]]
			minimumRank, minimumOK := ranks[dependency.RequiresComponent][dependency.MinimumVersion]
			if !actualOK || !minimumOK || actualRank < minimumRank {
				t.Fatalf("migration step %d violates dependency: %s must be at least %s (is %s)", index+1, dependency.RequiresComponent, dependency.MinimumVersion, current[dependency.RequiresComponent])
			}
		}
		current[step.Component] = step.TargetVersion
		seenComponent[step.Component] = true
	}
	for name, want := range target {
		if !seenComponent[name] {
			t.Errorf("migration does not name inventory component %s", name)
		}
		if current[name] != want {
			t.Errorf("migration leaves %s at %s, want %s", name, current[name], want)
		}
	}
}

func versionRanks(component architecture.Component, hops []architecture.UpgradeHop) map[string]int {
	ranks := map[string]int{component.CurrentVersion: 0}
	current := component.CurrentVersion
	for rank := 1; rank <= len(hops); rank++ {
		advanced := false
		for _, hop := range hops {
			if hop.Component == component.Name && hop.FromVersion == current {
				ranks[hop.ToVersion] = rank
				current = hop.ToVersion
				advanced = true
				break
			}
		}
		if !advanced {
			break
		}
	}
	return ranks
}

func findHop(hops []architecture.UpgradeHop, component, from, to string) (architecture.UpgradeHop, bool) {
	for _, hop := range hops {
		if hop.Component == component && hop.FromVersion == from && hop.ToVersion == to {
			return hop, true
		}
	}
	return architecture.UpgradeHop{}, false
}

func projectRoot(t *testing.T) string {
	t.Helper()
	working, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	return filepath.Clean(filepath.Join(working, ".."))
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	value, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return value
}

func mustUnmarshal(t *testing.T, data []byte, destination any) {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := decoder.Decode(destination); err != nil {
		t.Fatal(err)
	}
}

func mustMarshal(t *testing.T, value any) []byte {
	t.Helper()
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return encoded
}

func readEstate(t *testing.T) architecture.Estate {
	t.Helper()
	var estate architecture.Estate
	mustUnmarshal(t, mustRead(t, filepath.Join(projectRoot(t), "fixtures/estate.json")), &estate)
	return estate
}

func readSnapshot(t *testing.T) architecture.CompatibilitySnapshot {
	t.Helper()
	var snapshot architecture.CompatibilitySnapshot
	mustUnmarshal(t, mustRead(t, filepath.Join(projectRoot(t), "authority/compatibility-snapshot.json")), &snapshot)
	return snapshot
}

func cloneJSON[T any](t *testing.T, value T) T {
	t.Helper()
	var cloned T
	mustUnmarshal(t, mustMarshal(t, value), &cloned)
	return cloned
}

func assertJSONEqual(t *testing.T, name string, left, right []byte) {
	t.Helper()
	var l, r any
	mustUnmarshal(t, left, &l)
	mustUnmarshal(t, right, &r)
	if !reflect.DeepEqual(l, r) {
		t.Errorf("%s differs from checked-in artifact", name)
	}
}

func mustObject(t *testing.T, object map[string]any, key string) map[string]any {
	t.Helper()
	value, ok := object[key].(map[string]any)
	if !ok {
		t.Fatalf("%s is %T, want object", key, object[key])
	}
	return value
}

func mustObjectValue(t *testing.T, value any) map[string]any {
	t.Helper()
	object, ok := value.(map[string]any)
	if !ok {
		t.Fatalf("value is %T, want object", value)
	}
	return object
}

func mustArray(t *testing.T, object map[string]any, key string) []any {
	t.Helper()
	value, ok := object[key].([]any)
	if !ok {
		t.Fatalf("%s is %T, want array", key, object[key])
	}
	return value
}

func stringSlice(value any) []string {
	items, _ := value.([]any)
	result := make([]string, 0, len(items))
	for _, item := range items {
		result = append(result, item.(string))
	}
	return result
}

func intValue(value any) int {
	switch typed := value.(type) {
	case json.Number:
		parsed, _ := typed.Int64()
		return int(parsed)
	case float64:
		return int(typed)
	case int:
		return typed
	default:
		return 0
	}
}
