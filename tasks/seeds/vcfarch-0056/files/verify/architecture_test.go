package verify

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"

	"example.com/vcfarch"
)

func TestGeneratedArtifactsMatchPackage(t *testing.T) {
	dirs := []string{t.TempDir(), t.TempDir()}
	for _, dir := range dirs {
		if err := vcfarch.GenerateFromFiles(
			"../fixtures/requirements.json",
			"../fixtures/estate.json",
			"../pinned/compatibility.json",
			dir,
		); err != nil {
			t.Fatalf("GenerateFromFiles: %v", err)
		}
	}

	for _, name := range []string{"sddc-spec.json", "migration-plan.json"} {
		want, err := os.ReadFile(filepath.Join("../architecture", name))
		if err != nil {
			t.Fatalf("read committed %s: %v", name, err)
		}
		first, err := os.ReadFile(filepath.Join(dirs[0], name))
		if err != nil {
			t.Fatalf("read generated %s: %v", name, err)
		}
		second, err := os.ReadFile(filepath.Join(dirs[1], name))
		if err != nil {
			t.Fatalf("read second generated %s: %v", name, err)
		}
		if !bytes.Equal(first, second) {
			t.Errorf("%s is not deterministic", name)
		}
		if !bytes.Equal(want, first) {
			t.Errorf("architecture/%s is stale; regenerate it with cmd/vcfarch", name)
		}
	}
}

func TestExportedFunctionPipeline(t *testing.T) {
	inputs, err := vcfarch.LoadInputs(
		"../fixtures/requirements.json",
		"../fixtures/estate.json",
		"../pinned/compatibility.json",
	)
	if err != nil {
		t.Fatalf("LoadInputs: %v", err)
	}
	spec, err := vcfarch.BuildSddcSpec(inputs.Requirements)
	if err != nil {
		t.Fatalf("BuildSddcSpec: %v", err)
	}
	plan, err := vcfarch.BuildMigrationPlan(inputs.Estate, inputs.Compatibility)
	if err != nil {
		t.Fatalf("BuildMigrationPlan: %v", err)
	}
	dir := t.TempDir()
	if err := vcfarch.WriteArtifacts(dir, spec, plan); err != nil {
		t.Fatalf("WriteArtifacts: %v", err)
	}
	for _, name := range []string{"sddc-spec.json", "migration-plan.json"} {
		want, err := os.ReadFile(filepath.Join("../architecture", name))
		if err != nil {
			t.Fatalf("read committed %s: %v", name, err)
		}
		got, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			t.Fatalf("read written %s: %v", name, err)
		}
		if !bytes.Equal(got, want) {
			t.Errorf("direct function pipeline produced stale %s", name)
		}
	}
}

func TestRequiredFailureCases(t *testing.T) {
	var baseRequirements vcfarch.Requirements
	var baseEstate vcfarch.Estate
	var snapshot vcfarch.CompatibilitySnapshot
	decodeStrict(t, "../fixtures/requirements.json", &baseRequirements)
	decodeStrict(t, "../fixtures/estate.json", &baseEstate)
	decodeStrict(t, "../pinned/compatibility.json", &snapshot)

	tests := []struct {
		name string
		run  func() error
	}{
		{
			name: "insufficient capacity",
			run: func() error {
				req := baseRequirements
				req.Capacity.UsableCores = 1 << 30
				_, err := vcfarch.BuildSddcSpec(req)
				return err
			},
		},
		{
			name: "insufficient hosts at each site",
			run: func() error {
				req := baseRequirements
				req.Availability.HostsPerSite = len(req.Hosts) + 1
				_, err := vcfarch.BuildSddcSpec(req)
				return err
			},
		},
		{
			name: "greenfield management-domain scope",
			run: func() error {
				req := baseRequirements
				req.WorkloadDomain = req.ManagementDomain
				_, err := vcfarch.BuildSddcSpec(req)
				return err
			},
		},
		{
			name: "estate component without pinned transition",
			run: func() error {
				estate := baseEstate
				estate.Components = append([]vcfarch.EstateComponent(nil), baseEstate.Components...)
				estate.Components[0].Version = "unsupported-version"
				_, err := vcfarch.BuildMigrationPlan(estate, snapshot)
				return err
			},
		},
		{
			name: "brownfield management-domain scope",
			run: func() error {
				estate := baseEstate
				estate.WorkloadDomain = estate.ManagementDomain
				_, err := vcfarch.BuildMigrationPlan(estate, snapshot)
				return err
			},
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if err := tc.run(); err == nil {
				t.Fatal("got nil error, want failure")
			}
		})
	}
}

func TestGreenfieldArchitecture(t *testing.T) {
	var req vcfarch.Requirements
	decodeStrict(t, "../fixtures/requirements.json", &req)
	spec := readJSONObject(t, "../architecture/sddc-spec.json")

	tests := []struct {
		name  string
		check func(*testing.T, map[string]any, vcfarch.Requirements)
	}{
		{"identity and isolation", checkIdentityAndIsolation},
		{"host placement and capacity", checkHostsAndCapacity},
		{"network design", checkNetworks},
		{"appliance availability and storage", checkAppliancesAndStorage},
		{"secret references", checkSecretReferences},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) { tc.check(t, spec, req) })
	}
}

func checkIdentityAndIsolation(t *testing.T, spec map[string]any, req vcfarch.Requirements) {
	t.Helper()
	equalString(t, spec, "sddcId", req.DesignID)
	equalString(t, spec, "workflowType", req.WorkflowType)
	equalString(t, spec, "version", req.TargetVersion)

	dns := mustObject(t, spec["dnsSpec"], "dnsSpec")
	equalString(t, dns, "subdomain", req.DomainSuffix)
	equalStrings(t, mustStringSlice(t, dns["nameservers"], "dnsSpec.nameservers"), req.DNS, "dnsSpec.nameservers")
	equalStrings(t, mustStringSlice(t, spec["ntpServers"], "ntpServers"), req.NTP, "ntpServers")

	cluster := mustObject(t, spec["clusterSpec"], "clusterSpec")
	equalString(t, cluster, "datacenterName", req.Names.Datacenter)
	equalString(t, cluster, "clusterName", req.Names.Cluster)
	vcenter := mustObject(t, spec["vcenterSpec"], "vcenterSpec")
	equalString(t, vcenter, "vcenterHostname", req.Names.VCenter)
	sddcManager := mustObject(t, spec["sddcManagerSpec"], "sddcManagerSpec")
	equalString(t, sddcManager, "hostname", req.Names.SDDCManager)

	if findBoolKey(spec, "useExistingDeployment", true) {
		t.Error("greenfield SddcSpec contains useExistingDeployment=true")
	}
	encoded, _ := json.Marshal(spec)
	for _, protected := range []string{"central-sddc01", "central-vc01", "central-nsx", "central-ops"} {
		if bytes.Contains(encoded, []byte(protected)) {
			t.Errorf("SddcSpec references protected management system %q", protected)
		}
	}
}

func checkHostsAndCapacity(t *testing.T, spec map[string]any, req vcfarch.Requirements) {
	t.Helper()
	available := make(map[string]vcfarch.HostCandidate, len(req.Hosts))
	for _, host := range req.Hosts {
		available[host.Hostname] = host
	}

	hostSpecs := mustObjectSlice(t, spec["hostSpecs"], "hostSpecs")
	wantCount := len(req.Availability.Sites) * req.Availability.HostsPerSite
	if len(hostSpecs) != wantCount {
		t.Fatalf("hostSpecs has %d hosts, want %d", len(hostSpecs), wantCount)
	}

	seen := map[string]bool{}
	siteCount := map[string]int{}
	siteCores := map[string]int{}
	siteMemory := map[string]int{}
	siteStorage := map[string]float64{}
	for _, hostSpec := range hostSpecs {
		name, _ := hostSpec["hostname"].(string)
		host, ok := available[name]
		if !ok {
			t.Errorf("hostSpecs selects unknown host %q", name)
			continue
		}
		if seen[name] {
			t.Errorf("hostSpecs selects %q more than once", name)
		}
		seen[name] = true
		siteCount[host.Site]++
		siteCores[host.Site] += host.Cores
		siteMemory[host.Site] += host.MemoryGiB
		siteStorage[host.Site] += host.RawStorageTiB
	}

	for _, site := range req.Availability.Sites {
		if siteCount[site] != req.Availability.HostsPerSite {
			t.Errorf("site %s has %d selected hosts, want %d", site, siteCount[site], req.Availability.HostsPerSite)
		}
		if req.Availability.SurviveSiteFailure {
			if siteCores[site] < req.Capacity.UsableCores {
				t.Errorf("site %s has %d cores after peer-site failure, want at least %d", site, siteCores[site], req.Capacity.UsableCores)
			}
			if siteMemory[site] < req.Capacity.UsableMemoryGiB {
				t.Errorf("site %s has %d GiB after peer-site failure, want at least %d", site, siteMemory[site], req.Capacity.UsableMemoryGiB)
			}
			usable := siteStorage[site] * float64(100-req.Capacity.FreeSpacePercent) / 100
			if usable+1e-9 < req.Capacity.UsableStorageTiB {
				t.Errorf("site %s has %.2f TiB usable replica capacity, want at least %.2f", site, usable, req.Capacity.UsableStorageTiB)
			}
		}
	}

	totalRaw := 0.0
	for _, raw := range siteStorage {
		totalRaw += raw
	}
	usable := totalRaw / float64(req.Capacity.StorageCopies) * float64(100-req.Capacity.FreeSpacePercent) / 100
	if usable+1e-9 < req.Capacity.UsableStorageTiB {
		t.Errorf("selected storage yields %.2f TiB usable, want at least %.2f", usable, req.Capacity.UsableStorageTiB)
	}
}

func checkNetworks(t *testing.T, spec map[string]any, req vcfarch.Requirements) {
	t.Helper()
	networks := mustObjectSlice(t, spec["networkSpecs"], "networkSpecs")
	if len(networks) != len(req.Networks) {
		t.Fatalf("networkSpecs has %d entries, want %d", len(networks), len(req.Networks))
	}
	byType := map[string]map[string]any{}
	for _, network := range networks {
		name, _ := network["networkType"].(string)
		if _, exists := byType[name]; exists {
			t.Errorf("duplicate network type %q", name)
		}
		byType[name] = network
	}
	for _, want := range req.Networks {
		got, ok := byType[want.Type]
		if !ok {
			t.Errorf("missing network %s", want.Type)
			continue
		}
		equalNumber(t, got, "vlanId", float64(want.VLANID))
		equalNumber(t, got, "mtu", float64(want.MTU))
		equalString(t, got, "subnet", want.Subnet)
		equalString(t, got, "gateway", want.Gateway)
		ranges := mustObjectSlice(t, got["includeIpAddressRanges"], want.Type+".includeIpAddressRanges")
		if len(ranges) != 1 {
			t.Errorf("%s has %d address ranges, want 1", want.Type, len(ranges))
			continue
		}
		equalString(t, ranges[0], "startIpAddress", want.Start)
		equalString(t, ranges[0], "endIpAddress", want.End)
	}

	dvsSpecs := mustObjectSlice(t, spec["dvsSpecs"], "dvsSpecs")
	if len(dvsSpecs) != 2 {
		t.Fatalf("dvsSpecs has %d switches, want 2", len(dvsSpecs))
	}
	byName := map[string]map[string]any{}
	for _, dvs := range dvsSpecs {
		name, _ := dvs["dvsName"].(string)
		byName[name] = dvs
	}
	checks := []struct {
		name  string
		label string
	}{
		{req.Names.SystemDVS, "system"},
		{req.Names.OverlayDVS, "overlay"},
	}
	for _, check := range checks {
		dvs, ok := byName[check.name]
		if !ok {
			t.Errorf("missing DVS %s", check.name)
			continue
		}
		equalNumber(t, dvs, "mtu", 9000)
		wantNetworks := []string{}
		for _, network := range req.Networks {
			if network.Switch == check.label {
				wantNetworks = append(wantNetworks, network.Type)
			}
		}
		gotNetworks := mustStringSlice(t, dvs["networks"], check.name+".networks")
		sort.Strings(wantNetworks)
		sort.Strings(gotNetworks)
		equalStrings(t, gotNetworks, wantNetworks, check.name+".networks")
	}
}

func checkAppliancesAndStorage(t *testing.T, spec map[string]any, req vcfarch.Requirements) {
	t.Helper()
	nsx := mustObject(t, spec["nsxtSpec"], "nsxtSpec")
	equalString(t, nsx, "vipFqdn", req.Names.NSXVIP)
	managers := mustObjectSlice(t, nsx["nsxtManagers"], "nsxtSpec.nsxtManagers")
	if len(managers) != req.Availability.NSXManagerCount {
		t.Errorf("nsxtManagers has %d entries, want %d", len(managers), req.Availability.NSXManagerCount)
	}
	gotManagers := make([]string, 0, len(managers))
	for _, manager := range managers {
		gotManagers = append(gotManagers, fmt.Sprint(manager["hostname"]))
	}
	equalStrings(t, gotManagers, req.Names.NSXManagers, "NSX manager hostnames")

	var tep *vcfarch.NetworkRequirement
	for i := range req.Networks {
		if req.Networks[i].Type == "NSX_TEP" {
			tep = &req.Networks[i]
		}
	}
	if tep == nil {
		t.Fatal("requirements have no NSX_TEP network")
	}
	equalNumber(t, nsx, "transportVlanId", float64(tep.VLANID))
	pool := mustObject(t, nsx["ipAddressPoolSpec"], "nsxtSpec.ipAddressPoolSpec")
	equalString(t, pool, "name", req.Names.NSXTEPPoolName)
	subnets := mustObjectSlice(t, pool["subnets"], "ipAddressPoolSpec.subnets")
	if len(subnets) != 1 {
		t.Fatalf("NSX TEP pool has %d subnets, want 1", len(subnets))
	}
	equalString(t, subnets[0], "cidr", tep.Subnet)
	equalString(t, subnets[0], "gateway", tep.Gateway)

	datastore := mustObject(t, spec["datastoreSpec"], "datastoreSpec")
	vsan := mustObject(t, datastore["vsanSpec"], "datastoreSpec.vsanSpec")
	equalString(t, vsan, "datastoreName", req.Names.VSANDatastore)
	equalNumber(t, vsan, "failuresToTolerate", float64(req.Availability.FailuresToTolerate))
	esa := mustObject(t, vsan["esaConfig"], "vsanSpec.esaConfig")
	if enabled, _ := esa["enabled"].(bool); !enabled {
		t.Error("vSAN ESA is not enabled")
	}
}

func checkSecretReferences(t *testing.T, spec map[string]any, _ vcfarch.Requirements) {
	t.Helper()
	var walk func(any, string)
	walk = func(value any, path string) {
		switch node := value.(type) {
		case map[string]any:
			for key, child := range node {
				childPath := path + "." + key
				if strings.Contains(strings.ToLower(key), "password") {
					s, ok := child.(string)
					if !ok || !secretReferencePattern.MatchString(s) {
						t.Errorf("%s must be a ${...} secret reference", childPath)
					}
				}
				walk(child, childPath)
			}
		case []any:
			for i, child := range node {
				walk(child, fmt.Sprintf("%s[%d]", path, i))
			}
		}
	}
	walk(spec, "$sddcSpec")
}

var secretReferencePattern = regexp.MustCompile(`^\$\{[A-Za-z_][A-Za-z0-9_]*\}$`)

func TestMigrationPlan(t *testing.T) {
	var estate vcfarch.Estate
	var snapshot vcfarch.CompatibilitySnapshot
	var plan vcfarch.MigrationPlan
	decodeStrict(t, "../fixtures/estate.json", &estate)
	decodeStrict(t, "../pinned/compatibility.json", &snapshot)
	decodeStrict(t, "../architecture/migration-plan.json", &plan)

	if plan.SchemaVersion != "1.0" {
		t.Errorf("schemaVersion = %q, want 1.0", plan.SchemaVersion)
	}
	if plan.EstateID != estate.EstateID {
		t.Errorf("estateId = %q, want %q", plan.EstateID, estate.EstateID)
	}
	wantScope := vcfarch.MigrationScope{
		Fleet:                  estate.Fleet,
		WorkloadDomain:         estate.WorkloadDomain,
		ManagementDomain:       estate.ManagementDomain,
		ManagementDomainAction: "none",
	}
	if !reflect.DeepEqual(plan.Scope, wantScope) {
		t.Errorf("scope = %+v, want %+v", plan.Scope, wantScope)
	}
	if len(plan.Steps) != len(estate.Components) {
		t.Fatalf("migration plan has %d steps, want one for each of %d estate components", len(plan.Steps), len(estate.Components))
	}

	transitions := map[string]vcfarch.Transition{}
	for _, transition := range snapshot.Transitions {
		transitions[transition.Component+"\x00"+transition.FromVersion] = transition
	}
	seen := map[string]bool{}
	lastOrder := -1 << 60
	for i, step := range plan.Steps {
		key := step.Component + "\x00" + step.CurrentVersion
		transition, ok := transitions[key]
		if !ok {
			t.Errorf("step %d has no pinned transition for %s %s", i, step.Component, step.CurrentVersion)
			continue
		}
		if seen[key] {
			t.Errorf("component %s %s appears more than once", step.Component, step.CurrentVersion)
		}
		seen[key] = true
		if step.Order <= lastOrder {
			t.Errorf("step order is not strictly increasing at %d", step.Order)
		}
		lastOrder = step.Order
		if step.Order != transition.Order || step.Action != transition.Action || !reflect.DeepEqual(step.Target, transition.Target) || !reflect.DeepEqual(step.Gates, transition.Gates) {
			t.Errorf("step for %s %s does not exactly match pinned target/action/order/gates", step.Component, step.CurrentVersion)
		}
	}
	for _, component := range estate.Components {
		if !seen[component.Name+"\x00"+component.Version] {
			t.Errorf("estate component %s %s is missing", component.Name, component.Version)
		}
	}

	raw, err := os.ReadFile("../architecture/migration-plan.json")
	if err != nil {
		t.Fatal(err)
	}
	for _, protected := range estate.ProtectedSystems {
		if bytes.Contains(raw, []byte(protected)) {
			t.Errorf("migration plan names protected management system %q", protected)
		}
	}
}

func decodeStrict(t *testing.T, path string, out any) {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatalf("open %s: %v", path, err)
	}
	defer f.Close()
	dec := json.NewDecoder(f)
	dec.DisallowUnknownFields()
	if err := dec.Decode(out); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func mustObject(t *testing.T, value any, path string) map[string]any {
	t.Helper()
	obj, ok := value.(map[string]any)
	if !ok {
		t.Fatalf("%s is %T, want object", path, value)
	}
	return obj
}

func mustObjectSlice(t *testing.T, value any, path string) []map[string]any {
	t.Helper()
	values, ok := value.([]any)
	if !ok {
		t.Fatalf("%s is %T, want array", path, value)
	}
	objects := make([]map[string]any, len(values))
	for i, value := range values {
		objects[i] = mustObject(t, value, fmt.Sprintf("%s[%d]", path, i))
	}
	return objects
}

func mustStringSlice(t *testing.T, value any, path string) []string {
	t.Helper()
	values, ok := value.([]any)
	if !ok {
		t.Fatalf("%s is %T, want array", path, value)
	}
	out := make([]string, len(values))
	for i, value := range values {
		var ok bool
		out[i], ok = value.(string)
		if !ok {
			t.Fatalf("%s[%d] is %T, want string", path, i, value)
		}
	}
	return out
}

func equalString(t *testing.T, object map[string]any, key, want string) {
	t.Helper()
	if got, ok := object[key].(string); !ok || got != want {
		t.Errorf("%s = %v, want %q", key, object[key], want)
	}
}

func equalStrings(t *testing.T, got, want []string, label string) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Errorf("%s = %v, want %v", label, got, want)
	}
}

func equalNumber(t *testing.T, object map[string]any, key string, want float64) {
	t.Helper()
	got, ok := numberValue(object[key])
	if !ok || got != want {
		t.Errorf("%s = %v, want %v", key, object[key], want)
	}
}

func findBoolKey(value any, key string, want bool) bool {
	switch node := value.(type) {
	case map[string]any:
		for name, child := range node {
			if name == key {
				if got, ok := child.(bool); ok && got == want {
					return true
				}
			}
			if findBoolKey(child, key, want) {
				return true
			}
		}
	case []any:
		for _, child := range node {
			if findBoolKey(child, key, want) {
				return true
			}
		}
	}
	return false
}
