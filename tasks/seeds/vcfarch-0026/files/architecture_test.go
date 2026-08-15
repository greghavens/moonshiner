package architecture

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"net"
	"net/url"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
)

const installerSpecPath = "specifications/vcf-installer/vcf-installer-openapi.json"

type inventoryFixture struct {
	EstateID   string `json:"estateId"`
	Components []struct {
		ID      string `json:"id"`
		Name    string `json:"name"`
		Version string `json:"version"`
	} `json:"components"`
}

type compatibilityFixture struct {
	TargetVCFVersion  string `json:"targetVcfVersion"`
	MigrationStrategy string `json:"migrationStrategy"`
	SupportBoundary   struct {
		InPlaceTargetSupported bool   `json:"inPlaceTargetSupported"`
		BoundaryGate           string `json:"boundaryGate"`
	} `json:"supportBoundary"`
	Targets map[string]struct {
		Target        string   `json:"target"`
		Action        string   `json:"action"`
		RequiredGates []string `json:"requiredGates"`
	} `json:"targets"`
	MustPrecede [][2]string `json:"mustPrecede"`
	Design      struct {
		Sites []struct {
			Name                   string  `json:"name"`
			Role                   string  `json:"role"`
			DemandVCPU             int     `json:"demandVcpu"`
			VCPUPerCore            int     `json:"vcpuPerCore"`
			DemandMemoryTiB        float64 `json:"demandMemoryTiB"`
			DemandUsableStorageTB  float64 `json:"demandUsableStorageTB"`
			ReservePercent         int     `json:"reservePercent"`
			ManagementHosts        int     `json:"managementHosts"`
			MinimumWorkloadHosts   int     `json:"minimumWorkloadHosts"`
			WorkloadClusters       int     `json:"workloadClusters"`
			CoresPerHost           int     `json:"coresPerHost"`
			MemoryTiBPerHost       float64 `json:"memoryTiBPerHost"`
			UsableStorageTBPerHost float64 `json:"usableStorageTBPerHost"`
			FailureToleranceHosts  int     `json:"failureToleranceHosts"`
		} `json:"sites"`
		NetworkVLANs map[string]int `json:"networkVlans"`
	} `json:"design"`
}

// TestArchitecture performs validation in a deliberate order. In particular,
// no compatibility, inventory, capacity, or migration assertion is evaluated
// until the candidate's SddcSpec passes the vendored installer schema.
func TestArchitecture(t *testing.T) {
	artifactRaw := mustDecodeJSONFile(t, "architecture.json")
	artifactObject, ok := artifactRaw.(map[string]any)
	if !ok {
		t.Fatal("architecture.json: root must be an object")
	}
	greenfield, ok := artifactObject["greenfield"].(map[string]any)
	if !ok {
		t.Fatal("architecture.json: greenfield must be an object")
	}
	sddcSpec, ok := greenfield["sddcSpec"].(map[string]any)
	if !ok {
		t.Fatal("architecture.json: greenfield.sddcSpec must be an object")
	}

	// This is intentionally the first substantive verifier operation.
	installerDocument := mustDecodeJSONFile(t, installerSpecPath)
	installerSchema, err := jsonPointer(installerDocument, "#/components/schemas/SddcSpec")
	if err != nil {
		t.Fatalf("vendored installer schema: %v", err)
	}
	if err := validateJSONSchema(installerDocument, installerSchema, sddcSpec, "greenfield.sddcSpec"); err != nil {
		t.Fatalf("installer SddcSpec schema validation failed: %v", err)
	}

	// The seed-fixed migration schema is checked only after the installer schema.
	migrationRaw, ok := artifactObject["migrationPlan"]
	if !ok {
		t.Fatal("architecture.json: migrationPlan is required")
	}
	migrationSchema := mustDecodeJSONFile(t, "schemas/migration-plan.schema.json")
	if err := validateJSONSchema(migrationSchema, migrationSchema, migrationRaw, "migrationPlan"); err != nil {
		t.Fatalf("migration plan schema validation failed: %v", err)
	}

	artifactBytes, err := os.ReadFile("architecture.json")
	if err != nil {
		t.Fatal(err)
	}
	var architecture Architecture
	if err := json.Unmarshal(artifactBytes, &architecture); err != nil {
		t.Fatalf("decode typed architecture: %v", err)
	}
	var inventory inventoryFixture
	mustUnmarshalFile(t, "fixtures/estate-inventory.json", &inventory)
	var compatibility compatibilityFixture
	mustUnmarshalFile(t, "testdata/compatibility-snapshot.json", &compatibility)

	checks := []struct {
		name string
		run  func() error
	}{
		{"live research record", func() error {
			return checkResearchRecord()
		}},
		{"package output matches artifact and is race-safe", func() error {
			return checkBuildOutput(artifactRaw)
		}},
		{"target and availability", func() error {
			return checkAvailability(architecture, compatibility)
		}},
		{"capacity and site layout", func() error {
			return checkSites(architecture.Greenfield.Sites, compatibility)
		}},
		{"installer deployment details", func() error {
			return checkInstallerDetails(sddcSpec, compatibility)
		}},
		{"inventory-complete migration", func() error {
			return checkMigration(architecture.MigrationPlan, inventory, compatibility)
		}},
	}
	for _, check := range checks {
		t.Run(check.name, func(t *testing.T) {
			if err := check.run(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func checkResearchRecord() error {
	data, err := os.ReadFile("research.md")
	if err != nil {
		return fmt.Errorf("read research.md: %w", err)
	}
	record := string(data)
	if regexp.MustCompile(`(?i)(?:\.invalid\b|https?://(?:localhost|127\.0\.0\.1)(?:[:/]|$))`).MatchString(record) {
		return fmt.Errorf("research.md must not use fixture or reserved-invalid source URLs")
	}
	if !regexp.MustCompile(`(?i)access(?:ed| date)?[^0-9]{0,12}20[0-9]{2}-[01][0-9]-[0-3][0-9]`).MatchString(record) {
		return fmt.Errorf("research.md must record an ISO access date")
	}
	lowerRecord := strings.ToLower(record)
	for _, term := range []string{"9.1", "VxRail", "stretched"} {
		if !strings.Contains(lowerRecord, strings.ToLower(term)) {
			return fmt.Errorf("research.md does not record a design-relevant finding about %q", term)
		}
	}
	if !strings.Contains(lowerRecord, "upgrade") && !strings.Contains(lowerRecord, "migration") {
		return fmt.Errorf("research.md does not record an upgrade or migration boundary")
	}

	urlExpression := regexp.MustCompile(`https://[^\s<>()]+`)
	uniqueBroadcomSources := map[string]bool{}
	for _, rawURL := range urlExpression.FindAllString(record, -1) {
		rawURL = strings.TrimRight(rawURL, ".,;:!?'\"")
		parsed, err := url.Parse(rawURL)
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" {
			continue
		}
		host := strings.ToLower(parsed.Hostname())
		if host == "broadcom.com" || strings.HasSuffix(host, ".broadcom.com") || host == "vmware.com" || strings.HasSuffix(host, ".vmware.com") {
			uniqueBroadcomSources[parsed.String()] = true
		}
	}
	if len(uniqueBroadcomSources) < 2 {
		return fmt.Errorf("research.md must cite at least two distinct live Broadcom or VMware compatibility/documentation sources")
	}
	if !strings.Contains(lowerRecord, "broadcom") && !strings.Contains(lowerRecord, "vmware") {
		return fmt.Errorf("research.md must identify source publishers")
	}
	return nil
}

func checkBuildOutput(want any) error {
	wantCanonical, err := canonicalJSON(want)
	if err != nil {
		return err
	}
	const callers = 8
	results := make([][]byte, callers)
	errors := make([]error, callers)
	var wg sync.WaitGroup
	for index := 0; index < callers; index++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			built, err := Build("fixtures/estate-inventory.json")
			if err != nil {
				errors[index] = err
				return
			}
			encoded, err := json.Marshal(built)
			if err != nil {
				errors[index] = err
				return
			}
			var generic any
			decoder := json.NewDecoder(bytes.NewReader(encoded))
			decoder.UseNumber()
			if err := decoder.Decode(&generic); err != nil {
				errors[index] = err
				return
			}
			results[index], errors[index] = canonicalJSON(generic)
		}(index)
	}
	wg.Wait()
	for index := range results {
		if errors[index] != nil {
			return fmt.Errorf("Build call %d: %w", index, errors[index])
		}
		if !bytes.Equal(results[index], wantCanonical) {
			return fmt.Errorf("Build call %d does not match architecture.json", index)
		}
	}

	fixtureData, err := os.ReadFile("fixtures/estate-inventory.json")
	if err != nil {
		return err
	}
	var altered inventoryFixture
	if err := json.Unmarshal(fixtureData, &altered); err != nil {
		return err
	}
	altered.EstateID = "inventory-path-check"
	for index := range altered.Components {
		altered.Components[index].Name += " (inventory path check)"
		altered.Components[index].Version += "-inventory-path-check"
	}
	temporary, err := os.CreateTemp("", "vcfarchitecture-inventory-*.json")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if err := json.NewEncoder(temporary).Encode(altered); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	built, err := Build(temporaryPath)
	if err != nil {
		return fmt.Errorf("Build with alternate inventory: %w", err)
	}
	if built.MigrationPlan.EstateID != altered.EstateID || len(built.MigrationPlan.Steps) != len(altered.Components) {
		return fmt.Errorf("Build does not derive its migration plan from inventoryPath")
	}
	components := make(map[string]struct{ name, version string }, len(altered.Components))
	for _, component := range altered.Components {
		components[component.ID] = struct{ name, version string }{component.Name, component.Version}
	}
	for _, step := range built.MigrationPlan.Steps {
		component, ok := components[step.ComponentID]
		if !ok || step.Component != component.name || step.CurrentVersion != component.version {
			return fmt.Errorf("Build does not preserve inventory identity/version for component %q", step.ComponentID)
		}
	}
	return nil
}

func checkAvailability(got Architecture, snapshot compatibilityFixture) error {
	if got.SchemaVersion != "1.0" {
		return fmt.Errorf("schemaVersion = %q, want 1.0", got.SchemaVersion)
	}
	if got.TargetVCFVersion != snapshot.TargetVCFVersion {
		return fmt.Errorf("targetVcfVersion = %q, want %q", got.TargetVCFVersion, snapshot.TargetVCFVersion)
	}
	want := Availability{
		Topology:         "two-independent-vcf-instances",
		InterSiteRTTMS:   24,
		StretchedCluster: false,
		RecoveryMode:     "asynchronous",
		RPOMinutes:       15,
		RTOMinutes:       120,
	}
	if !reflect.DeepEqual(got.Greenfield.Availability, want) {
		return fmt.Errorf("availability = %+v, want %+v", got.Greenfield.Availability, want)
	}
	if got.Greenfield.ManagementServicesIPReserve != 30 {
		return fmt.Errorf("managementServicesIpReserve = %d, want 30", got.Greenfield.ManagementServicesIPReserve)
	}
	if got.Greenfield.InternalServicesCIDR != "198.18.0.0/15" {
		return fmt.Errorf("internalServicesCidr = %q", got.Greenfield.InternalServicesCIDR)
	}
	return nil
}

func checkSites(got []SiteDesign, snapshot compatibilityFixture) error {
	if len(got) != len(snapshot.Design.Sites) {
		return fmt.Errorf("sites count = %d, want %d", len(got), len(snapshot.Design.Sites))
	}
	byName := make(map[string]SiteDesign, len(got))
	for _, site := range got {
		if _, exists := byName[site.Name]; exists {
			return fmt.Errorf("duplicate site %q", site.Name)
		}
		byName[site.Name] = site
	}
	for _, want := range snapshot.Design.Sites {
		site, ok := byName[want.Name]
		if !ok {
			return fmt.Errorf("missing site %q", want.Name)
		}
		comparisons := []struct {
			name string
			got  any
			want any
		}{
			{"role", site.Role, want.Role},
			{"demandVcpu", site.DemandVCPU, want.DemandVCPU},
			{"vcpuPerCore", site.VCPUPerCore, want.VCPUPerCore},
			{"demandMemoryTiB", site.DemandMemoryTiB, want.DemandMemoryTiB},
			{"demandUsableStorageTB", site.DemandUsableStorageTB, want.DemandUsableStorageTB},
			{"managementHosts", site.ManagementHosts, want.ManagementHosts},
			{"workloadClusters", site.WorkloadClusters, want.WorkloadClusters},
			{"coresPerHost", site.CoresPerHost, want.CoresPerHost},
			{"memoryTiBPerHost", site.MemoryTiBPerHost, want.MemoryTiBPerHost},
			{"usableStorageTBPerHost", site.UsableStorageTBPerHost, want.UsableStorageTBPerHost},
		}
		for _, comparison := range comparisons {
			if !reflect.DeepEqual(comparison.got, comparison.want) {
				return fmt.Errorf("site %s %s = %v, want %v", site.Name, comparison.name, comparison.got, comparison.want)
			}
		}
		if site.ReservePercent < want.ReservePercent {
			return fmt.Errorf("site %s reservePercent = %d, need at least %d", site.Name, site.ReservePercent, want.ReservePercent)
		}
		if site.WorkloadHosts < want.MinimumWorkloadHosts {
			return fmt.Errorf("site %s workloadHosts = %d, need at least %d", site.Name, site.WorkloadHosts, want.MinimumWorkloadHosts)
		}
		if site.FailureToleranceHosts < want.FailureToleranceHosts {
			return fmt.Errorf("site %s failureToleranceHosts = %d, need at least %d", site.Name, site.FailureToleranceHosts, want.FailureToleranceHosts)
		}
		if site.WorkloadClusters != 2 || site.WorkloadHosts%site.WorkloadClusters != 0 || site.WorkloadHosts/site.WorkloadClusters < 6 {
			return fmt.Errorf("site %s workload hosts are not split into two equal clusters of at least six", site.Name)
		}
		reserve := 1 + float64(site.ReservePercent)/100
		availableHosts := site.WorkloadHosts - site.FailureToleranceHosts
		capacityChecks := []struct {
			name      string
			available float64
			required  float64
		}{
			{"physical cores", float64(availableHosts * site.CoresPerHost), float64(site.DemandVCPU) / float64(site.VCPUPerCore) * reserve},
			{"memory TiB", float64(availableHosts) * site.MemoryTiBPerHost, site.DemandMemoryTiB * reserve},
			{"usable storage TB", float64(availableHosts) * site.UsableStorageTBPerHost, site.DemandUsableStorageTB * reserve},
		}
		for _, capacity := range capacityChecks {
			if capacity.available+1e-9 < capacity.required {
				return fmt.Errorf("site %s %s after failures = %.2f, need %.2f", site.Name, capacity.name, capacity.available, capacity.required)
			}
		}
	}
	return nil
}

func checkInstallerDetails(spec map[string]any, snapshot compatibilityFixture) error {
	stringsWanted := map[string]string{
		"sddcId":          "dfw01-m01",
		"workflowType":    "VCF",
		"version":         snapshot.TargetVCFVersion,
		"vcfInstanceName": "TX-DFW1-VCF",
	}
	for field, want := range stringsWanted {
		if got, _ := spec[field].(string); got != want {
			return fmt.Errorf("sddcSpec.%s = %q, want %q", field, got, want)
		}
	}
	hosts, _ := spec["hostSpecs"].([]any)
	wantHosts := []string{"dfw-m01-esx01", "dfw-m01-esx02", "dfw-m01-esx03", "dfw-m01-esx04", "dfw-m01-esx05", "dfw-m01-esx06"}
	if len(hosts) != len(wantHosts) {
		return fmt.Errorf("management host count = %d, want %d", len(hosts), len(wantHosts))
	}
	hostNames := make(map[string]bool, len(hosts))
	for _, raw := range hosts {
		host, _ := raw.(map[string]any)
		hostName, _ := host["hostname"].(string)
		hostNames[hostName] = true
	}
	for _, want := range wantHosts {
		if !hostNames[want] {
			return fmt.Errorf("hostSpecs is missing protected management host %q", want)
		}
	}
	vcenter, _ := spec["vcenterSpec"].(map[string]any)
	if vcenter["vcenterHostname"] != "dfw-m01-vc01.mgmt.dfw1.example.com" || vcenter["useExistingDeployment"] != false {
		return fmt.Errorf("vcenterSpec must describe the new DFW vCenter")
	}
	if vcenter["version"] != snapshot.Targets["vcenter"].Target {
		return fmt.Errorf("vcenterSpec.version = %v, want %q", vcenter["version"], snapshot.Targets["vcenter"].Target)
	}
	cluster, _ := spec["clusterSpec"].(map[string]any)
	if cluster["datacenterName"] != "dfw01-m01-dc" || cluster["clusterName"] != "dfw01-m01-cl01" {
		return fmt.Errorf("clusterSpec must use the protected DFW datacenter and cluster names")
	}

	networks, _ := spec["networkSpecs"].([]any)
	if len(networks) != len(snapshot.Design.NetworkVLANs) {
		return fmt.Errorf("networkSpecs count = %d, want %d", len(networks), len(snapshot.Design.NetworkVLANs))
	}
	byType := map[string]map[string]any{}
	for _, raw := range networks {
		network, _ := raw.(map[string]any)
		kind, _ := network["networkType"].(string)
		byType[kind] = network
	}
	type networkAddressing struct {
		subnet, gateway, mask, start, end string
	}
	wantAddressing := map[string]networkAddressing{
		"MANAGEMENT":       {"10.10.10.0/24", "10.10.10.1", "255.255.255.0", "10.10.10.20", "10.10.10.89"},
		"VMOTION":          {"10.10.20.0/24", "10.10.20.1", "255.255.255.0", "10.10.20.20", "10.10.20.39"},
		"VSAN":             {"10.10.30.0/24", "10.10.30.1", "255.255.255.0", "10.10.30.20", "10.10.30.39"},
		"VM_MANAGEMENT":    {"10.10.40.0/24", "10.10.40.1", "255.255.255.0", "10.10.40.20", "10.10.40.69"},
		"FLEET_MANAGEMENT": {"10.10.50.0/24", "10.10.50.1", "255.255.255.0", "10.10.50.20", "10.10.50.69"},
	}
	for kind, vlan := range snapshot.Design.NetworkVLANs {
		network, ok := byType[kind]
		if !ok {
			return fmt.Errorf("missing %s network", kind)
		}
		if got, ok := integerValue(network["vlanId"]); !ok || got != int64(vlan) {
			return fmt.Errorf("%s vlanId = %v, want %d", kind, network["vlanId"], vlan)
		}
		if got, ok := integerValue(network["mtu"]); !ok || got != 9000 {
			return fmt.Errorf("%s mtu = %v, want 9000", kind, network["mtu"])
		}
		addressing := wantAddressing[kind]
		for field, want := range map[string]string{
			"subnet": addressing.subnet, "gateway": addressing.gateway, "subnetMask": addressing.mask,
		} {
			if network[field] != want {
				return fmt.Errorf("%s %s = %v, want %q", kind, field, network[field], want)
			}
		}
		ranges, _ := network["includeIpAddressRanges"].([]any)
		if len(ranges) != 1 {
			return fmt.Errorf("%s includeIpAddressRanges must contain the protected range", kind)
		}
		ipRange, _ := ranges[0].(map[string]any)
		if ipRange["startIpAddress"] != addressing.start || ipRange["endIpAddress"] != addressing.end {
			return fmt.Errorf("%s address range = %v, want %s-%s", kind, ipRange, addressing.start, addressing.end)
		}
	}
	managementRanges, _ := byType["MANAGEMENT"]["includeIpAddressRanges"].([]any)
	if ipv4RangeSize(managementRanges) < 30 {
		return fmt.Errorf("MANAGEMENT network must contain at least 30 reserved addresses")
	}

	dvsSpecs, _ := spec["dvsSpecs"].([]any)
	if len(dvsSpecs) != 1 {
		return fmt.Errorf("dvsSpecs count = %d, want 1", len(dvsSpecs))
	}
	dvs, _ := dvsSpecs[0].(map[string]any)
	if dvs["dvsName"] != "dfw01-m01-vds01" {
		return fmt.Errorf("dvsName = %v, want dfw01-m01-vds01", dvs["dvsName"])
	}
	if mtu, ok := integerValue(dvs["mtu"]); !ok || mtu != 9000 {
		return fmt.Errorf("dvsSpecs[0].mtu = %v, want 9000", dvs["mtu"])
	}
	wantDVSNetworks := []string{"MANAGEMENT", "VMOTION", "VSAN", "VM_MANAGEMENT", "FLEET_MANAGEMENT"}
	if !sameStringSet(dvs["networks"], wantDVSNetworks) {
		return fmt.Errorf("dvsSpecs[0].networks = %v, want %v", dvs["networks"], wantDVSNetworks)
	}
	uplinks, _ := dvs["vmnicsToUplinks"].([]any)
	wantUplinks := map[string]string{"vmnic0": "uplink1", "vmnic1": "uplink2"}
	if len(uplinks) != len(wantUplinks) {
		return fmt.Errorf("vmnicsToUplinks must contain dual uplinks")
	}
	seenUplinks := make(map[string]bool, len(uplinks))
	for _, raw := range uplinks {
		uplink, _ := raw.(map[string]any)
		id, _ := uplink["id"].(string)
		want, exists := wantUplinks[id]
		if !exists || seenUplinks[id] || uplink["uplink"] != want {
			return fmt.Errorf("unexpected uplink mapping %v", uplink)
		}
		seenUplinks[id] = true
	}
	dns, _ := spec["dnsSpec"].(map[string]any)
	if dns["subdomain"] != "mgmt.dfw1.example.com" || !sameStringSet(dns["nameservers"], []string{"10.10.0.10", "10.10.0.11"}) {
		return fmt.Errorf("dnsSpec must use the protected DFW DNS domain and servers")
	}
	if !sameStringSet(spec["ntpServers"], []string{"10.10.0.20", "10.10.0.21"}) {
		return fmt.Errorf("ntpServers = %v, want protected DFW NTP servers", spec["ntpServers"])
	}
	sddcManager, _ := spec["sddcManagerSpec"].(map[string]any)
	if sddcManager["hostname"] != "dfw-m01-sddc01.mgmt.dfw1.example.com" || sddcManager["version"] != snapshot.Targets["sddc-manager"].Target || sddcManager["useExistingDeployment"] != false {
		return fmt.Errorf("sddcManagerSpec must describe the pinned new DFW SDDC Manager")
	}
	if spec["managementPoolName"] != "dfw01-m01-network-pool" {
		return fmt.Errorf("managementPoolName = %v, want dfw01-m01-network-pool", spec["managementPoolName"])
	}

	nsx, _ := spec["nsxtSpec"].(map[string]any)
	nsxManagers, _ := nsx["nsxtManagers"].([]any)
	if len(nsxManagers) != 3 || nsx["useExistingDeployment"] != false {
		return fmt.Errorf("nsxtSpec must deploy three new managers")
	}
	wantNSXManagers := []string{
		"dfw-m01-nsx01.mgmt.dfw1.example.com",
		"dfw-m01-nsx02.mgmt.dfw1.example.com",
		"dfw-m01-nsx03.mgmt.dfw1.example.com",
	}
	gotNSXManagers := make(map[string]bool, len(nsxManagers))
	for _, raw := range nsxManagers {
		manager, _ := raw.(map[string]any)
		hostname, _ := manager["hostname"].(string)
		gotNSXManagers[hostname] = true
	}
	for _, want := range wantNSXManagers {
		if !gotNSXManagers[want] {
			return fmt.Errorf("nsxtManagers is missing protected manager %q", want)
		}
	}
	if nsx["vipFqdn"] != "dfw-m01-nsx.mgmt.dfw1.example.com" || nsx["version"] != snapshot.Targets["nsx"].Target {
		return fmt.Errorf("nsxtSpec must use the protected DFW VIP and pinned NSX target")
	}
	operations, _ := spec["vcfOperationsSpec"].(map[string]any)
	operationsNodes, _ := operations["nodes"].([]any)
	if len(operationsNodes) != 3 || operations["useExistingDeployment"] != false {
		return fmt.Errorf("vcfOperationsSpec must deploy three new nodes")
	}
	wantOperationsNodes := []string{
		"dfw-m01-ops01.mgmt.dfw1.example.com",
		"dfw-m01-ops02.mgmt.dfw1.example.com",
		"dfw-m01-ops03.mgmt.dfw1.example.com",
	}
	gotOperationsNodes := make(map[string]bool, len(operationsNodes))
	for _, raw := range operationsNodes {
		node, _ := raw.(map[string]any)
		hostname, _ := node["hostname"].(string)
		gotOperationsNodes[hostname] = true
	}
	for _, want := range wantOperationsNodes {
		if !gotOperationsNodes[want] {
			return fmt.Errorf("vcfOperationsSpec.nodes is missing protected node %q", want)
		}
	}
	if operations["loadBalancerFqdn"] != "dfw-m01-ops.mgmt.dfw1.example.com" || operations["version"] != snapshot.TargetVCFVersion {
		return fmt.Errorf("vcfOperationsSpec must use the protected DFW load balancer and pinned version")
	}
	license, _ := spec["licenseServerSpec"].(map[string]any)
	if license["hostname"] != "dfw-m01-lic01.mgmt.dfw1.example.com" || license["useExistingDeployment"] != false {
		return fmt.Errorf("licenseServerSpec must deploy the new DFW license server")
	}
	datastore, _ := spec["datastoreSpec"].(map[string]any)
	vsan, _ := datastore["vsanSpec"].(map[string]any)
	if ftt, ok := integerValue(vsan["failuresToTolerate"]); !ok || ftt != 2 {
		return fmt.Errorf("vSAN failuresToTolerate = %v, want 2", vsan["failuresToTolerate"])
	}
	if vsan["datastoreName"] != "dfw01-m01-vsan01" {
		return fmt.Errorf("vSAN datastoreName = %v, want dfw01-m01-vsan01", vsan["datastoreName"])
	}
	return nil
}

func sameStringSet(value any, want []string) bool {
	values, ok := value.([]any)
	if !ok || len(values) != len(want) {
		return false
	}
	got := make(map[string]bool, len(values))
	for _, raw := range values {
		text, ok := raw.(string)
		if !ok || got[text] {
			return false
		}
		got[text] = true
	}
	for _, text := range want {
		if !got[text] {
			return false
		}
	}
	return true
}

func checkMigration(plan MigrationPlan, inventory inventoryFixture, snapshot compatibilityFixture) error {
	if plan.SchemaVersion != "1.0" || plan.EstateID != inventory.EstateID || plan.Strategy != snapshot.MigrationStrategy || plan.TargetVCFVersion != snapshot.TargetVCFVersion {
		return fmt.Errorf("migration plan header does not match inventory and snapshot")
	}
	if !snapshot.SupportBoundary.InPlaceTargetSupported && plan.Strategy == "in-place-upgrade" {
		return fmt.Errorf("unsupported in-place strategy selected")
	}
	if len(plan.Steps) != len(inventory.Components) {
		return fmt.Errorf("migration steps = %d, inventory components = %d", len(plan.Steps), len(inventory.Components))
	}
	inventoryByID := make(map[string]struct{ Name, Version string }, len(inventory.Components))
	for _, component := range inventory.Components {
		inventoryByID[component.ID] = struct{ Name, Version string }{component.Name, component.Version}
	}
	orderByID := make(map[string]int, len(plan.Steps))
	seenOrders := make(map[int]bool, len(plan.Steps))
	for _, step := range plan.Steps {
		current, exists := inventoryByID[step.ComponentID]
		if !exists {
			return fmt.Errorf("unknown or duplicate component %q", step.ComponentID)
		}
		if _, duplicate := orderByID[step.ComponentID]; duplicate {
			return fmt.Errorf("duplicate component %q", step.ComponentID)
		}
		if step.Component != current.Name || step.CurrentVersion != current.Version {
			return fmt.Errorf("component %s current identity/version does not match inventory", step.ComponentID)
		}
		want, exists := snapshot.Targets[step.ComponentID]
		if !exists || step.Target != want.Target || step.Action != want.Action {
			return fmt.Errorf("component %s target/action = %q/%q, want %q/%q", step.ComponentID, step.Target, step.Action, want.Target, want.Action)
		}
		gateSet := make(map[string]bool, len(step.Gates))
		for _, gate := range step.Gates {
			gateSet[gate] = true
		}
		if len(step.Gates) != len(want.RequiredGates) {
			return fmt.Errorf("component %s gates = %v, want exactly %v", step.ComponentID, step.Gates, want.RequiredGates)
		}
		for _, gate := range want.RequiredGates {
			if !gateSet[gate] {
				return fmt.Errorf("component %s missing gate %q", step.ComponentID, gate)
			}
		}
		orderByID[step.ComponentID] = step.Order
		if seenOrders[step.Order] {
			return fmt.Errorf("duplicate migration order %d", step.Order)
		}
		seenOrders[step.Order] = true
		delete(inventoryByID, step.ComponentID)
	}
	if len(inventoryByID) != 0 {
		missing := make([]string, 0, len(inventoryByID))
		for id := range inventoryByID {
			missing = append(missing, id)
		}
		sort.Strings(missing)
		return fmt.Errorf("missing inventory components: %v", missing)
	}
	for order := 1; order <= len(plan.Steps); order++ {
		if !seenOrders[order] {
			return fmt.Errorf("migration orders must be contiguous; missing %d", order)
		}
	}
	for _, edge := range snapshot.MustPrecede {
		if orderByID[edge[0]] >= orderByID[edge[1]] {
			return fmt.Errorf("%s must precede %s", edge[0], edge[1])
		}
	}
	return nil
}

func ipv4RangeSize(ranges []any) int {
	total := 0
	for _, raw := range ranges {
		rangeObject, _ := raw.(map[string]any)
		start := net.ParseIP(fmt.Sprint(rangeObject["startIpAddress"])).To4()
		end := net.ParseIP(fmt.Sprint(rangeObject["endIpAddress"])).To4()
		if start == nil || end == nil {
			continue
		}
		startValue := uint32(start[0])<<24 | uint32(start[1])<<16 | uint32(start[2])<<8 | uint32(start[3])
		endValue := uint32(end[0])<<24 | uint32(end[1])<<16 | uint32(end[2])<<8 | uint32(end[3])
		if endValue >= startValue {
			total += int(endValue-startValue) + 1
		}
	}
	return total
}

func mustDecodeJSONFile(t *testing.T, path string) any {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var result any
	if err := decoder.Decode(&result); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
	if decoder.More() {
		t.Fatalf("decode %s: trailing JSON", path)
	}
	return result
}

func mustUnmarshalFile(t *testing.T, path string, target any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func canonicalJSON(value any) ([]byte, error) {
	return json.Marshal(value)
}

func integerValue(value any) (int64, bool) {
	switch number := value.(type) {
	case json.Number:
		parsed, err := strconv.ParseInt(number.String(), 10, 64)
		return parsed, err == nil
	case float64:
		if number != math.Trunc(number) {
			return 0, false
		}
		return int64(number), true
	case int:
		return int64(number), true
	default:
		return 0, false
	}
}

func numberValue(value any) (float64, bool) {
	switch number := value.(type) {
	case json.Number:
		parsed, err := number.Float64()
		return parsed, err == nil
	case float64:
		return number, true
	case int:
		return float64(number), true
	default:
		return 0, false
	}
}

func jsonPointer(document any, pointer string) (any, error) {
	if pointer == "#" || pointer == "" {
		return document, nil
	}
	if !strings.HasPrefix(pointer, "#/") {
		return nil, fmt.Errorf("only local JSON pointers are supported: %q", pointer)
	}
	current := document
	for _, token := range strings.Split(strings.TrimPrefix(pointer, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("pointer %q traverses a non-object at %q", pointer, token)
		}
		current, ok = object[token]
		if !ok {
			return nil, fmt.Errorf("pointer %q has no token %q", pointer, token)
		}
	}
	return current, nil
}

// validateJSONSchema implements the assertion vocabulary used by the vendored
// OpenAPI component schemas and the local migration schema. References are
// resolved against the complete source document, so SddcSpec is checked using
// its own transitive installer-schema definitions.
func validateJSONSchema(document, schema, value any, path string) error {
	object, ok := schema.(map[string]any)
	if !ok {
		return fmt.Errorf("%s: schema is not an object", path)
	}
	if reference, ok := object["$ref"].(string); ok {
		resolved, err := jsonPointer(document, reference)
		if err != nil {
			return err
		}
		return validateJSONSchema(document, resolved, value, path)
	}
	if constant, exists := object["const"]; exists && !reflect.DeepEqual(constant, value) {
		return fmt.Errorf("%s: value %v does not equal const %v", path, value, constant)
	}
	if choices, ok := object["enum"].([]any); ok {
		matched := false
		for _, choice := range choices {
			matched = matched || reflect.DeepEqual(choice, value)
		}
		if !matched {
			return fmt.Errorf("%s: value %v is not in enum", path, value)
		}
	}
	if alternatives, ok := object["allOf"].([]any); ok {
		for _, alternative := range alternatives {
			if err := validateJSONSchema(document, alternative, value, path); err != nil {
				return err
			}
		}
	}
	if alternatives, ok := object["anyOf"].([]any); ok {
		matched := false
		for _, alternative := range alternatives {
			matched = matched || validateJSONSchema(document, alternative, value, path) == nil
		}
		if !matched {
			return fmt.Errorf("%s: no anyOf schema matched", path)
		}
	}
	if alternatives, ok := object["oneOf"].([]any); ok {
		matches := 0
		for _, alternative := range alternatives {
			if validateJSONSchema(document, alternative, value, path) == nil {
				matches++
			}
		}
		if matches != 1 {
			return fmt.Errorf("%s: matched %d oneOf schemas", path, matches)
		}
	}
	if schemaType, ok := object["type"].(string); ok {
		if err := validateType(schemaType, value, path); err != nil {
			return err
		}
	}
	switch typed := value.(type) {
	case map[string]any:
		if required, ok := object["required"].([]any); ok {
			for _, rawName := range required {
				name, _ := rawName.(string)
				if _, exists := typed[name]; !exists {
					return fmt.Errorf("%s: required property %q is missing", path, name)
				}
			}
		}
		properties, _ := object["properties"].(map[string]any)
		for name, propertyValue := range typed {
			propertySchema, exists := properties[name]
			if exists {
				if err := validateJSONSchema(document, propertySchema, propertyValue, path+"."+name); err != nil {
					return err
				}
				continue
			}
			if allowed, exists := object["additionalProperties"].(bool); exists && !allowed {
				return fmt.Errorf("%s: additional property %q is not allowed", path, name)
			}
		}
	case []any:
		if minimum, ok := integerValue(object["minItems"]); ok && int64(len(typed)) < minimum {
			return fmt.Errorf("%s: has %d items, minimum %d", path, len(typed), minimum)
		}
		if maximum, ok := integerValue(object["maxItems"]); ok && int64(len(typed)) > maximum {
			return fmt.Errorf("%s: has %d items, maximum %d", path, len(typed), maximum)
		}
		if unique, _ := object["uniqueItems"].(bool); unique {
			seen := map[string]bool{}
			for _, item := range typed {
				encoded, _ := canonicalJSON(item)
				if seen[string(encoded)] {
					return fmt.Errorf("%s: array items are not unique", path)
				}
				seen[string(encoded)] = true
			}
		}
		if itemSchema, exists := object["items"]; exists {
			for index, item := range typed {
				if err := validateJSONSchema(document, itemSchema, item, fmt.Sprintf("%s[%d]", path, index)); err != nil {
					return err
				}
			}
		}
	case string:
		if minimum, ok := integerValue(object["minLength"]); ok && int64(len([]rune(typed))) < minimum {
			return fmt.Errorf("%s: string is shorter than %d", path, minimum)
		}
		if maximum, ok := integerValue(object["maxLength"]); ok && int64(len([]rune(typed))) > maximum {
			return fmt.Errorf("%s: string is longer than %d", path, maximum)
		}
		if pattern, ok := object["pattern"].(string); ok {
			expression, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: schema pattern %q is unsupported: %w", path, pattern, err)
			}
			if !expression.MatchString(typed) {
				return fmt.Errorf("%s: %q does not match %q", path, typed, pattern)
			}
		}
	}
	if number, ok := numberValue(value); ok {
		if minimum, ok := numberValue(object["minimum"]); ok && number < minimum {
			return fmt.Errorf("%s: %v is below minimum %v", path, number, minimum)
		}
		if maximum, ok := numberValue(object["maximum"]); ok && number > maximum {
			return fmt.Errorf("%s: %v is above maximum %v", path, number, maximum)
		}
	}
	return nil
}

func validateType(want string, value any, path string) error {
	valid := false
	switch want {
	case "object":
		_, valid = value.(map[string]any)
	case "array":
		_, valid = value.([]any)
	case "string":
		_, valid = value.(string)
	case "boolean":
		_, valid = value.(bool)
	case "number":
		_, valid = numberValue(value)
	case "integer":
		_, valid = integerValue(value)
	case "null":
		valid = value == nil
	default:
		return fmt.Errorf("%s: unsupported schema type %q", path, want)
	}
	if !valid {
		return fmt.Errorf("%s: value has type %T, want %s", path, value, want)
	}
	return nil
}
