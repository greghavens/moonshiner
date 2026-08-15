package vcfarch

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"sort"
	"testing"

	"example.com/vcfarch/internal/jsonschema"
)

const installerSchemaSHA256 = "9295f4d07b46343600da2e4a609e166ec48feabcf2189bc20c2f90c9f4174b72"

var protectedInputDigests = []struct {
	path   string
	digest string
}{
	{"testdata/estate-inventory.json", "428d9abe16dfd337aa7b2a1103ad40c27a8f9f69a248a9db3d7380a33f2ea7af"},
	{"testdata/compatibility-snapshot.json", "c13797ded7224c3b16a07512473ea51df9e2c0819a9b69c98d0748a48465317a"},
	{"schemas/migration-plan.schema.json", "80a8e400305397f013c7c585ed96d68bb65043dca8e2d56afa4973305b8f711b"},
}

func TestArchitecture(t *testing.T) {
	inventory := readFixture[Inventory](t, "testdata/estate-inventory.json")
	compatibility := readFixture[CompatibilitySnapshot](t, "testdata/compatibility-snapshot.json")

	artifact, err := Build(inventory, compatibility)
	if err != nil {
		t.Fatalf("Build returned an error: %v", err)
	}

	// This is intentionally the first artifact assertion. All architectural and
	// migration checks below are gated on the SddcSpec passing its own vendored
	// VCF Installer schema.
	validateInstallerSpecFirst(t, artifact.Greenfield.SddcSpec)

	t.Run("protected verifier inputs", func(t *testing.T) {
		for _, input := range protectedInputDigests {
			assertFileDigest(t, input.path, input.digest)
		}
	})
	t.Run("greenfield inputs", func(t *testing.T) {
		checkGreenfieldInputs(t, artifact.Greenfield.SddcSpec, inventory, compatibility)
	})
	t.Run("stretched topology", func(t *testing.T) {
		checkTopology(t, artifact.Greenfield, inventory, compatibility)
	})
	t.Run("migration plan schema", func(t *testing.T) {
		checkMigrationSchema(t, artifact.MigrationPlan)
	})
	t.Run("migration inventory transitions and order", func(t *testing.T) {
		checkMigrationPlan(t, artifact.MigrationPlan, inventory, compatibility)
	})
	t.Run("deterministic result", func(t *testing.T) {
		again, err := Build(inventory, compatibility)
		if err != nil {
			t.Fatalf("second Build returned an error: %v", err)
		}
		if !reflect.DeepEqual(artifact, again) {
			t.Fatal("Build returned different artifacts for identical inputs")
		}
	})
}

func assertFileDigest(t *testing.T, path, want string) {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read protected input %s: %v", path, err)
	}
	digest := sha256.Sum256(b)
	if got := hex.EncodeToString(digest[:]); got != want {
		t.Fatalf("protected input %s has digest %s, want %s", path, got, want)
	}
}

func validateInstallerSpecFirst(t *testing.T, raw json.RawMessage) {
	t.Helper()
	path := "specifications/vcf-installer/vcf-installer-openapi.json"
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read vendored installer schema: %v", err)
	}
	digest := sha256.Sum256(b)
	if got := hex.EncodeToString(digest[:]); got != installerSchemaSHA256 {
		t.Fatalf("vendored installer schema digest is %s, want %s", got, installerSchemaSHA256)
	}
	document, err := jsonschema.Decode(b)
	if err != nil {
		t.Fatalf("decode vendored installer schema: %v", err)
	}
	instance, err := jsonschema.Decode(raw)
	if err != nil {
		t.Fatalf("greenfield SddcSpec is not JSON: %v", err)
	}
	if err := jsonschema.New(document).ValidateAt("#/components/schemas/SddcSpec", instance); err != nil {
		t.Fatalf("greenfield SddcSpec does not validate against the VCF Installer 9.1.0.0 schema: %v", err)
	}
}

func checkGreenfieldInputs(t *testing.T, raw json.RawMessage, inventory Inventory, compatibility CompatibilitySnapshot) {
	t.Helper()
	value, err := jsonschema.Decode(raw)
	if err != nil {
		t.Fatal(err)
	}
	spec := value.(map[string]any)

	tests := []struct {
		name string
		got  any
		want any
	}{
		{"sddc id", spec["sddcId"], inventory.Greenfield.SddcID},
		{"release", spec["version"], compatibility.TargetRelease},
		{"workflow", spec["workflowType"], "VCF"},
		{"management pool", spec["managementPoolName"], inventory.Greenfield.ManagementPoolName},
		{"dns subdomain", nested(spec, "dnsSpec", "subdomain"), inventory.Greenfield.Subdomain},
		{"vCenter hostname", nested(spec, "vcenterSpec", "vcenterHostname"), inventory.Greenfield.VcenterHostname},
		{"vCenter password reference", nested(spec, "vcenterSpec", "rootVcenterPassword"), inventory.Greenfield.VcenterRootPasswordRef},
		{"SDDC Manager hostname", nested(spec, "sddcManagerSpec", "hostname"), inventory.Greenfield.SddcManagerHostname},
		{"SDDC Manager password reference", nested(spec, "sddcManagerSpec", "rootPassword"), inventory.Greenfield.SddcManagerPasswordRef},
		{"SDDC Manager local password reference", nested(spec, "sddcManagerSpec", "localUserPassword"), inventory.Greenfield.SddcManagerLocalUserRef},
		{"cluster name", nested(spec, "clusterSpec", "clusterName"), inventory.Greenfield.ClusterName},
		{"datacenter name", nested(spec, "clusterSpec", "datacenterName"), inventory.Greenfield.DatacenterName},
		{"vSAN datastore", nested(spec, "datastoreSpec", "vsanSpec", "datastoreName"), inventory.Greenfield.DatastoreName},
		{"vSAN FTT", nested(spec, "datastoreSpec", "vsanSpec", "failuresToTolerate"), json.Number("1")},
		{"NSX VIP", nested(spec, "nsxtSpec", "vipFqdn"), inventory.Greenfield.NSXVIP},
		{"NSX root password reference", nested(spec, "nsxtSpec", "rootNsxtManagerPassword"), inventory.Greenfield.NSXManagerPasswordRef},
		{"NSX admin password reference", nested(spec, "nsxtSpec", "nsxtAdminPassword"), inventory.Greenfield.NSXManagerPasswordRef},
		{"NSX audit password reference", nested(spec, "nsxtSpec", "nsxtAuditPassword"), inventory.Greenfield.NSXManagerPasswordRef},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if !reflect.DeepEqual(test.got, test.want) {
				t.Errorf("got %#v, want %#v", test.got, test.want)
			}
		})
	}

	checkStringArray(t, "DNS servers", nested(spec, "dnsSpec", "nameservers"), inventory.Greenfield.NameServers)
	checkStringArray(t, "NTP servers", spec["ntpServers"], inventory.Greenfield.NTPServers)

	hosts := objectArray(t, spec["hostSpecs"], "hostSpecs")
	if len(hosts) != inventory.Greenfield.ExpectedManagementHosts {
		t.Fatalf("hostSpecs has %d hosts, want %d", len(hosts), inventory.Greenfield.ExpectedManagementHosts)
	}
	gotHosts := make([]string, 0, len(hosts))
	for _, host := range hosts {
		gotHosts = append(gotHosts, stringValue(t, host["hostname"], "hostSpecs.hostname"))
	}
	wantHosts := dataHostnames(inventory)
	assertStringSet(t, "SddcSpec hosts", gotHosts, wantHosts)

	networks := objectArray(t, spec["networkSpecs"], "networkSpecs")
	if len(networks) != len(inventory.Networks) {
		t.Fatalf("networkSpecs has %d entries, want %d", len(networks), len(inventory.Networks))
	}
	byType := map[string]map[string]any{}
	for _, network := range networks {
		byType[stringValue(t, network["networkType"], "networkType")] = network
	}
	for _, want := range inventory.Networks {
		want := want
		t.Run("network "+want.Type, func(t *testing.T) {
			got, exists := byType[want.Type]
			if !exists {
				t.Fatalf("network %s is missing", want.Type)
			}
			checks := []struct {
				field string
				want  any
			}{
				{"vlanId", json.Number(fmt.Sprint(want.VLAN))},
				{"subnet", want.CIDR},
				{"gateway", want.Gateway},
				{"mtu", json.Number(fmt.Sprint(want.MTU))},
			}
			for _, check := range checks {
				if !reflect.DeepEqual(got[check.field], check.want) {
					t.Errorf("%s = %#v, want %#v", check.field, got[check.field], check.want)
				}
			}
		})
	}

	dvsSpecs := objectArray(t, spec["dvsSpecs"], "dvsSpecs")
	if len(dvsSpecs) != 1 || dvsSpecs[0]["dvsName"] != inventory.Greenfield.DVSName {
		t.Fatalf("dvsSpecs must define the supplied single DVS %q", inventory.Greenfield.DVSName)
	}
	nsxManagers := objectArray(t, nested(spec, "nsxtSpec", "nsxtManagers"), "nsxtManagers")
	gotManagers := make([]string, 0, len(nsxManagers))
	for _, manager := range nsxManagers {
		gotManagers = append(gotManagers, stringValue(t, manager["hostname"], "nsxtManagers.hostname"))
	}
	assertStringSet(t, "NSX managers", gotManagers, inventory.Greenfield.NSXManagerHostnames)
}

func checkTopology(t *testing.T, design GreenfieldDesign, inventory Inventory, compatibility CompatibilitySnapshot) {
	t.Helper()
	if !design.Topology.Stretched {
		t.Error("management topology is not marked stretched")
	}
	if len(design.Topology.DataSites) != 2 {
		t.Fatalf("dataSites has %d entries, want 2", len(design.Topology.DataSites))
	}

	sites := map[string]InventorySite{}
	for _, site := range inventory.Sites {
		sites[site.ID] = site
	}
	components := componentsByID(inventory)
	seenSites := map[string]bool{}
	for _, placement := range design.Topology.DataSites {
		site, exists := sites[placement.SiteID]
		if !exists || site.Role != "data" {
			t.Errorf("data-site placement %q is not an inventory data site", placement.SiteID)
			continue
		}
		if seenSites[placement.SiteID] {
			t.Errorf("data site %q appears more than once", placement.SiteID)
		}
		seenSites[placement.SiteID] = true
		if placement.FailureDomain != site.FailureDomain {
			t.Errorf("site %s failure domain = %q, want %q", site.ID, placement.FailureDomain, site.FailureDomain)
		}
		var expected []string
		for _, component := range inventory.Components {
			if component.Type == "esxi" && component.Site == site.ID {
				expected = append(expected, component.Members...)
			}
		}
		assertStringSet(t, "hosts for "+site.ID, placement.Hosts, expected)
	}

	witnessInventory, ok := components[design.Topology.Witness.ComponentID]
	if !ok || witnessInventory.Type != "vsan-witness" {
		t.Fatalf("witness component %q is not the inventory vSAN witness", design.Topology.Witness.ComponentID)
	}
	witnessSite, ok := sites[design.Topology.Witness.SiteID]
	if !ok || witnessSite.Role != "witness" {
		t.Fatalf("witness site %q is not the inventory witness site", design.Topology.Witness.SiteID)
	}
	witnessChecks := []struct {
		name string
		got  any
		want any
	}{
		{"inventory site", design.Topology.Witness.SiteID, witnessInventory.Site},
		{"third failure domain", design.Topology.Witness.FailureDomain, witnessSite.FailureDomain},
		{"hostname", design.Topology.Witness.Hostname, witnessInventory.Members[0]},
		{"target ESXi build", design.Topology.Witness.Version, compatibility.TargetVersions["vsan-witness"]},
		{"placement type", design.Topology.Witness.PlacementType, "dedicated-virtual-appliance"},
		{"dedicated", design.Topology.Witness.Dedicated, true},
		{"outside management domain", design.Topology.Witness.RunsOnManagementDomain, false},
	}
	for _, check := range witnessChecks {
		if !reflect.DeepEqual(check.got, check.want) {
			t.Errorf("witness %s = %#v, want %#v", check.name, check.got, check.want)
		}
	}
	if seenSites[design.Topology.Witness.SiteID] {
		t.Error("witness was placed in a data site instead of the third site")
	}
	for _, host := range dataHostnames(inventory) {
		if host == design.Topology.Witness.Hostname {
			t.Error("witness is listed as a management-domain data host")
		}
	}
}

func checkMigrationSchema(t *testing.T, plan MigrationPlan) {
	t.Helper()
	document, err := jsonschema.ReadFile("schemas/migration-plan.schema.json")
	if err != nil {
		t.Fatalf("read migration schema: %v", err)
	}
	b, err := json.Marshal(plan)
	if err != nil {
		t.Fatalf("marshal migration plan: %v", err)
	}
	instance, err := jsonschema.Decode(b)
	if err != nil {
		t.Fatalf("decode migration plan: %v", err)
	}
	if err := jsonschema.New(document).Validate(instance); err != nil {
		t.Fatalf("migration plan does not match its fixed schema: %v", err)
	}
}

func checkMigrationPlan(t *testing.T, plan MigrationPlan, inventory Inventory, compatibility CompatibilitySnapshot) {
	t.Helper()
	if plan.EstateID != inventory.EstateID {
		t.Errorf("estateId = %q, want %q", plan.EstateID, inventory.EstateID)
	}
	if plan.TargetRelease != compatibility.TargetRelease {
		t.Errorf("targetRelease = %q, want %q", plan.TargetRelease, compatibility.TargetRelease)
	}
	if plan.Strategy != "parallel-target-and-swing" {
		t.Errorf("strategy = %q, want parallel-target-and-swing", plan.Strategy)
	}
	if len(plan.Steps) != len(inventory.Components) {
		t.Fatalf("plan has %d steps, want one for each of %d components", len(plan.Steps), len(inventory.Components))
	}

	transitions := map[string]TransitionRule{}
	for _, transition := range compatibility.Transitions {
		key := transition.ComponentType + "\x00" + transition.FromVersion
		transitions[key] = transition
	}
	dependencies := map[string][]string{}
	for _, dependency := range compatibility.Dependencies {
		dependencies[dependency.ComponentID] = dependency.DependsOn
	}
	steps := map[string]MigrationStep{}
	positions := map[string]int{}
	for index, step := range plan.Steps {
		if step.Order != index+1 {
			t.Errorf("step index %d has order %d, want %d", index, step.Order, index+1)
		}
		if _, duplicate := steps[step.ComponentID]; duplicate {
			t.Errorf("component %q appears more than once", step.ComponentID)
		}
		steps[step.ComponentID] = step
		positions[step.ComponentID] = index
	}

	for _, component := range inventory.Components {
		component := component
		t.Run(component.ID, func(t *testing.T) {
			step, exists := steps[component.ID]
			if !exists {
				t.Fatalf("component is missing from migration plan")
			}
			transition, exists := transitions[component.Type+"\x00"+component.Version]
			if !exists {
				t.Fatalf("pinned snapshot has no transition for %s at %s", component.Type, component.Version)
			}
			checks := []struct {
				name string
				got  any
				want any
			}{
				{"name", step.ComponentName, component.Name},
				{"type", step.ComponentType, component.Type},
				{"source version", step.FromVersion, component.Version},
				{"target version", step.TargetVersion, compatibility.TargetVersions[component.Type]},
				{"transition target", step.TargetVersion, transition.ToVersion},
				{"action", step.Action, transition.AllowedAction},
			}
			for _, check := range checks {
				if !reflect.DeepEqual(check.got, check.want) {
					t.Errorf("%s = %#v, want %#v", check.name, check.got, check.want)
				}
			}
			assertStringSet(t, "gates", step.Gates, transition.RequiredGates)
			assertStringSet(t, "dependencies", step.DependsOn, dependencies[component.ID])
			for _, forbidden := range transition.Forbidden {
				if step.Action == forbidden {
					t.Errorf("action %q is forbidden by the pinned transition", step.Action)
				}
			}
		})
	}
	for componentID, required := range dependencies {
		for _, predecessor := range required {
			if positions[predecessor] >= positions[componentID] {
				t.Errorf("component %s must follow dependency %s", componentID, predecessor)
			}
		}
	}
	for _, blocker := range compatibility.Restrictions {
		if plan.Strategy != blocker.Route {
			t.Errorf("restriction %s requires route %q", blocker.ID, blocker.Route)
		}
		for _, component := range inventory.Components {
			if contains(blocker.AffectedTypes, component.Type) && contains(blocker.AffectedVersions, component.Version) {
				if steps[component.ID].Action == blocker.ForbiddenAction {
					t.Errorf("restriction %s forbids %s for %s", blocker.ID, blocker.ForbiddenAction, component.ID)
				}
			}
		}
	}
}

func readFixture[T any](t *testing.T, path string) T {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	var value T
	if err := json.Unmarshal(b, &value); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
	return value
}

func nested(root map[string]any, keys ...string) any {
	var current any = root
	for _, key := range keys {
		object, ok := current.(map[string]any)
		if !ok {
			return nil
		}
		current = object[key]
	}
	return current
}

func objectArray(t *testing.T, raw any, name string) []map[string]any {
	t.Helper()
	items, ok := raw.([]any)
	if !ok {
		t.Fatalf("%s is %T, want array", name, raw)
	}
	result := make([]map[string]any, 0, len(items))
	for index, item := range items {
		object, ok := item.(map[string]any)
		if !ok {
			t.Fatalf("%s[%d] is %T, want object", name, index, item)
		}
		result = append(result, object)
	}
	return result
}

func stringValue(t *testing.T, raw any, name string) string {
	t.Helper()
	value, ok := raw.(string)
	if !ok {
		t.Fatalf("%s is %T, want string", name, raw)
	}
	return value
}

func checkStringArray(t *testing.T, name string, raw any, want []string) {
	t.Helper()
	items, ok := raw.([]any)
	if !ok {
		t.Fatalf("%s is %T, want array", name, raw)
	}
	got := make([]string, 0, len(items))
	for _, item := range items {
		got = append(got, stringValue(t, item, name))
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("%s = %v, want %v", name, got, want)
	}
}

func dataHostnames(inventory Inventory) []string {
	var result []string
	for _, component := range inventory.Components {
		if component.Type == "esxi" {
			result = append(result, component.Members...)
		}
	}
	return result
}

func componentsByID(inventory Inventory) map[string]InventoryComponent {
	result := make(map[string]InventoryComponent, len(inventory.Components))
	for _, component := range inventory.Components {
		result[component.ID] = component
	}
	return result
}

func assertStringSet(t *testing.T, name string, got, want []string) {
	t.Helper()
	got = append([]string(nil), got...)
	want = append([]string(nil), want...)
	sort.Strings(got)
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Errorf("%s = %v, want %v", name, got, want)
	}
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}
