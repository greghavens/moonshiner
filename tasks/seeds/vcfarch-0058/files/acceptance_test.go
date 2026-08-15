package vcfarch_test

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
	"time"

	"vcfarch-0058/design"
	"vcfarch-0058/verifier"
)

func TestArchitectureArtifacts(t *testing.T) {
	var sddcBytes, migrationBytes []byte

	// The installer schema is intentionally the first acceptance gate. No
	// compatibility, fixture, package, or migration assertions run if it fails.
	if !t.Run("01_installer_schema", func(t *testing.T) {
		sddcBytes = readFile(t, "artifacts/sddc-spec.json")
		openAPIBytes := readFile(t, "specifications/vcf-installer/vcf-installer-openapi.json")
		openAPI := decodeAny(t, openAPIBytes)
		sddc := decodeAny(t, sddcBytes)
		schema := objectAt(t, openAPI, "components", "schemas", "SddcSpec")
		if problems := verifier.Validate(openAPI, schema, sddc); len(problems) != 0 {
			t.Fatalf("sddc-spec.json does not validate as the tagged SddcSpec:\n%s", strings.Join(problems, "\n"))
		}
	}) {
		t.FailNow()
	}

	if !t.Run("02_migration_plan_schema", func(t *testing.T) {
		migrationBytes = readFile(t, "artifacts/migration-plan.json")
		schema := decodeAny(t, readFile(t, "schemas/migration-plan.schema.json"))
		migration := decodeAny(t, migrationBytes)
		if problems := verifier.Validate(schema, schema, migration); len(problems) != 0 {
			t.Fatalf("migration-plan.json does not validate:\n%s", strings.Join(problems, "\n"))
		}
	}) {
		t.FailNow()
	}

	if !t.Run("03_live_research_provenance", func(t *testing.T) {
		researchBytes := readFile(t, "artifacts/research.json")
		decodeAny(t, researchBytes)
		var research researchRecord
		decodeInto(t, researchBytes, &research)
		if len(research.Consulted) == 0 {
			t.Fatal("research.json records no consulted sources")
		}
		hasBroadcomPublishedSource := false
		for index, source := range research.Consulted {
			if strings.TrimSpace(source.Title) == "" {
				t.Fatalf("research source %d has no title", index)
			}
			parsed, err := url.Parse(source.URL)
			if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" {
				t.Fatalf("research source %d URL %q is not an absolute HTTPS URL", index, source.URL)
			}
			host := strings.ToLower(parsed.Hostname())
			if host == "localhost" || strings.HasSuffix(host, ".invalid") || host == "example.com" || strings.HasSuffix(host, ".example.com") {
				t.Fatalf("research source %d URL %q is not a real public source", index, source.URL)
			}
			if host == "broadcom.com" || strings.HasSuffix(host, ".broadcom.com") || host == "vmware.com" || strings.HasSuffix(host, ".vmware.com") {
				hasBroadcomPublishedSource = true
			}
			if _, err := time.Parse("2006-01-02", source.Accessed); err != nil {
				t.Fatalf("research source %d accessed date %q is not YYYY-MM-DD", index, source.Accessed)
			}
			if len(source.Informed) == 0 {
				t.Fatalf("research source %d records no informed architecture or migration decision", index)
			}
			for decisionIndex, decision := range source.Informed {
				if strings.TrimSpace(decision) == "" {
					t.Fatalf("research source %d decision %d is empty", index, decisionIndex)
				}
			}
		}
		if !hasBroadcomPublishedSource {
			t.Fatal("research.json does not identify any Broadcom-published source")
		}
	}) {
		t.FailNow()
	}

	requirementBytes := readFile(t, "fixtures/design-requirements.json")
	estateBytes := readFile(t, "fixtures/estate.json")
	compatibilityBytes := readFile(t, "compatibility/compatibility-snapshot.json")

	var req requirements
	decodeInto(t, requirementBytes, &req)
	var estate estateInventory
	decodeInto(t, estateBytes, &estate)
	var snapshot compatibilitySnapshot
	decodeInto(t, compatibilityBytes, &snapshot)
	var sddc sddcSpec
	decodeInto(t, sddcBytes, &sddc)
	var migration migrationPlan
	decodeInto(t, migrationBytes, &migration)

	if !t.Run("04_go_package_matches_checked_in_artifacts", func(t *testing.T) {
		built, err := design.Build(requirementBytes, estateBytes, compatibilityBytes)
		if err != nil {
			t.Fatalf("design.Build: %v", err)
		}
		assertJSONEqual(t, built.SddcSpec, sddcBytes, "SddcSpec")
		assertJSONEqual(t, built.MigrationPlan, migrationBytes, "migration plan")
		outputDir := t.TempDir()
		if err := design.Write(outputDir, built); err != nil {
			t.Fatalf("design.Write: %v", err)
		}
		assertJSONEqual(t, readFile(t, outputDir+"/sddc-spec.json"), sddcBytes, "written SddcSpec")
		assertJSONEqual(t, readFile(t, outputDir+"/migration-plan.json"), migrationBytes, "written migration plan")
	}) {
		t.FailNow()
	}

	if !t.Run("05_build_derives_from_each_input", func(t *testing.T) {
		mutatedRequirements := decodeAny(t, requirementBytes).(map[string]any)
		mutatedRequirements["scenarioId"] = "derived-input-check"
		mutatedRequirements["installer"].(map[string]any)["sddcId"] = "derived-m01"
		traffic := mutatedRequirements["northSouthTraffic"].(map[string]any)
		traffic["steadyGbps"] = 8
		traffic["peakGbps"] = 10
		traffic["headroomPercent"] = 0

		mutatedEstate := decodeAny(t, estateBytes).(map[string]any)
		mutatedEstate["estateId"] = "derived-estate-check"

		mutatedCompatibility := decodeAny(t, compatibilityBytes).(map[string]any)
		forms := mutatedCompatibility["edgeFormFactors"].([]any)
		for _, rawForm := range forms {
			form := rawForm.(map[string]any)
			if form["name"] == "LARGE" {
				form["maxValidatedNorthSouthGbps"] = 21
			}
		}

		built, err := design.Build(mustJSON(t, mutatedRequirements), mustJSON(t, mutatedEstate), mustJSON(t, mutatedCompatibility))
		if err != nil {
			t.Fatalf("design.Build with changed inputs: %v", err)
		}
		var derivedSddc sddcSpec
		decodeInto(t, built.SddcSpec, &derivedSddc)
		var derivedMigration migrationPlan
		decodeInto(t, built.MigrationPlan, &derivedMigration)
		if derivedSddc.SddcID != "derived-m01" || derivedSddc.Architecture.ScenarioID != "derived-input-check" {
			t.Fatal("design.Build did not derive installer and architecture identity from requirements")
		}
		if !closeFloat(derivedSddc.Architecture.Traffic.RequiredFailoverThroughputGbps, 10) || derivedSddc.Architecture.Edge.FormFactor != "LARGE" || !closeFloat(derivedSddc.Architecture.Edge.PerNodeValidatedThroughputGbps, 21) || derivedSddc.Architecture.Edge.UplinkLayout != "DUAL_10G_DIVERSE" {
			t.Fatal("design.Build did not recalculate Edge sizing from changed requirements and compatibility data")
		}
		if derivedMigration.EstateID != "derived-estate-check" {
			t.Fatal("design.Build did not derive migration identity from the estate")
		}
	}) {
		t.FailNow()
	}

	if !t.Run("06_command_writes_verified_artifacts", func(t *testing.T) {
		testCommand(t, requirementBytes, estateBytes, compatibilityBytes, sddcBytes, migrationBytes)
	}) {
		t.FailNow()
	}

	checks := []struct {
		name string
		run  func() error
	}{
		{"greenfield identity and installer intent", func() error { return checkInstallerIntent(req, sddc) }},
		{"target component combination", func() error { return checkTargetCombination(req, snapshot, sddc) }},
		{"site and capacity requirements", func() error { return checkSitesAndCapacity(req, sddc) }},
		{"management network failure domains", func() error { return checkManagementNetworking(req, sddc) }},
		{"throughput-derived Edge design", func() error { return checkEdge(req, snapshot, sddc) }},
		{"estate-complete ordered migration", func() error { return checkMigration(estate, snapshot, migration) }},
	}
	for _, check := range checks {
		check := check
		t.Run(check.name, func(t *testing.T) {
			if err := check.run(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

type requirements struct {
	ScenarioID    string       `json:"scenarioId"`
	TargetRelease string       `json:"targetRelease"`
	Sites         []site       `json:"sites"`
	SiteRecovery  siteRecovery `json:"siteRecovery"`
	Availability  availability `json:"availability"`
	Management    struct {
		HostCount                             int     `json:"hostCount"`
		PerHostCPUCores                       int     `json:"perHostCpuCores"`
		PerHostMemoryGiB                      int     `json:"perHostMemoryGiB"`
		PerHostRawStorageTiB                  float64 `json:"perHostRawStorageTiB"`
		PerHostNICCount                       int     `json:"perHostNicCount"`
		PerHostNICSpeedGbps                   int     `json:"perHostNicSpeedGbps"`
		RequiredCPUCoresAfterHostFailure      int     `json:"requiredCpuCoresAfterHostFailure"`
		RequiredMemoryGiBAfterHostFailure     int     `json:"requiredMemoryGiBAfterHostFailure"`
		RequiredRawStorageTiBAfterHostFailure float64 `json:"requiredRawStorageTiBAfterHostFailure"`
		VsanFailuresToTolerate                int     `json:"vsanFailuresToTolerate"`
		VsanESA                               bool    `json:"vsanEsa"`
	} `json:"managementDomain"`
	Traffic struct {
		SteadyGbps      float64 `json:"steadyGbps"`
		PeakGbps        float64 `json:"peakGbps"`
		HeadroomPercent float64 `json:"headroomPercent"`
	} `json:"northSouthTraffic"`
	Installer struct {
		SddcID              string               `json:"sddcId"`
		VCFInstanceName     string               `json:"vcfInstanceName"`
		DNSSubdomain        string               `json:"dnsSubdomain"`
		DNSServers          []string             `json:"dnsServers"`
		NTPServers          []string             `json:"ntpServers"`
		PlaceholderPassword string               `json:"placeholderPassword"`
		Networks            []networkRequirement `json:"networks"`
	} `json:"installer"`
}

type site struct {
	ID               string   `json:"id"`
	Role             string   `json:"role"`
	ManagementDomain bool     `json:"managementDomain"`
	FaultDomains     []string `json:"faultDomains"`
}

type siteRecovery struct {
	StretchedManagementCluster bool `json:"stretchedManagementCluster"`
	InterSiteRTTMs             int  `json:"interSiteRttMs"`
	RPOMinutes                 int  `json:"rpoMinutes"`
	RTOMinutes                 int  `json:"rtoMinutes"`
}

type availability struct {
	HostFailuresToTolerate int `json:"hostFailuresToTolerate"`
	EdgeFailuresToTolerate int `json:"edgeFailuresToTolerate"`
	TorFailuresToTolerate  int `json:"torFailuresToTolerate"`
}

type networkRequirement struct {
	Type      string `json:"type"`
	VLANID    int    `json:"vlanId"`
	Subnet    string `json:"subnet"`
	Gateway   string `json:"gateway"`
	MTU       int    `json:"mtu"`
	PoolStart string `json:"poolStart"`
	PoolEnd   string `json:"poolEnd"`
}

type sddcSpec struct {
	SddcID          string `json:"sddcId"`
	WorkflowType    string `json:"workflowType"`
	Version         string `json:"version"`
	VCFInstanceName string `json:"vcfInstanceName"`
	HostSpecs       []struct {
		Hostname string `json:"hostname"`
	} `json:"hostSpecs"`
	VcenterSpec componentSpec `json:"vcenterSpec"`
	NsxtSpec    struct {
		Version               string `json:"version"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
	} `json:"nsxtSpec"`
	SddcManagerSpec                  componentSpec `json:"sddcManagerSpec"`
	VCFOperationsFleetManagementSpec componentSpec `json:"vcfOperationsFleetManagementSpec"`
	VCFOperationsSpec                struct {
		Version               string `json:"version"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
	} `json:"vcfOperationsSpec"`
	VCFAutomationSpec componentSpec `json:"vcfAutomationSpec"`
	DNS               struct {
		Subdomain   string   `json:"subdomain"`
		Nameservers []string `json:"nameservers"`
	} `json:"dnsSpec"`
	NTPServers                  []string `json:"ntpServers"`
	CEIPEnabled                 bool     `json:"ceipEnabled"`
	SkipEsxThumbprintValidation bool     `json:"skipEsxThumbprintValidation"`
	SkipGatewayPingValidation   bool     `json:"skipGatewayPingValidation"`
	DatastoreSpec               struct {
		VsanSpec struct {
			FailuresToTolerate int `json:"failuresToTolerate"`
			ESAConfig          struct {
				Enabled bool `json:"enabled"`
			} `json:"esaConfig"`
		} `json:"vsanSpec"`
	} `json:"datastoreSpec"`
	NetworkSpecs []struct {
		NetworkType            string `json:"networkType"`
		VLANID                 int    `json:"vlanId"`
		Subnet                 string `json:"subnet"`
		Gateway                string `json:"gateway"`
		MTU                    int    `json:"mtu"`
		IncludeIPAddressRanges []struct {
			Start string `json:"startIpAddress"`
			End   string `json:"endIpAddress"`
		} `json:"includeIpAddressRanges"`
	} `json:"networkSpecs"`
	DVSSpecs []struct {
		DVSName         string `json:"dvsName"`
		MTU             int    `json:"mtu"`
		VmnicsToUplinks []struct {
			ID     string `json:"id"`
			Uplink string `json:"uplink"`
		} `json:"vmnicsToUplinks"`
		NSXTeamings []struct {
			Policy        string   `json:"policy"`
			ActiveUplinks []string `json:"activeUplinks"`
		} `json:"nsxTeamings"`
	} `json:"dvsSpecs"`
	Architecture architecture `json:"x-vcf-architecture"`
}

type componentSpec struct {
	Version               string `json:"version"`
	UseExistingDeployment bool   `json:"useExistingDeployment"`
}

type architecture struct {
	ScenarioID   string       `json:"scenarioId"`
	Sites        []site       `json:"sites"`
	SiteRecovery siteRecovery `json:"siteRecovery"`
	Availability struct {
		HostFailuresToTolerate int    `json:"hostFailuresToTolerate"`
		EdgeFailuresToTolerate int    `json:"edgeFailuresToTolerate"`
		TorFailuresToTolerate  int    `json:"torFailuresToTolerate"`
		EdgePlacement          string `json:"edgePlacement"`
	} `json:"availability"`
	Capacity struct {
		ManagementHostCount          int     `json:"managementHostCount"`
		PerHostCPUCores              int     `json:"perHostCpuCores"`
		PerHostMemoryGiB             int     `json:"perHostMemoryGiB"`
		PerHostRawStorageTiB         float64 `json:"perHostRawStorageTiB"`
		AvailableAfterOneHostFailure struct {
			CPUCores      int     `json:"cpuCores"`
			MemoryGiB     int     `json:"memoryGiB"`
			RawStorageTiB float64 `json:"rawStorageTiB"`
		} `json:"availableAfterOneHostFailure"`
	} `json:"capacity"`
	Traffic struct {
		SteadyGbps                     float64 `json:"steadyGbps"`
		PeakGbps                       float64 `json:"peakGbps"`
		HeadroomPercent                float64 `json:"headroomPercent"`
		RequiredFailoverThroughputGbps float64 `json:"requiredFailoverThroughputGbps"`
	} `json:"traffic"`
	Edge struct {
		ClusterName                    string     `json:"clusterName"`
		NodeCount                      int        `json:"nodeCount"`
		HAMode                         string     `json:"haMode"`
		FormFactor                     string     `json:"formFactor"`
		PerNodeValidatedThroughputGbps float64    `json:"perNodeValidatedThroughputGbps"`
		UplinkLayout                   string     `json:"uplinkLayout"`
		Nodes                          []edgeNode `json:"nodes"`
	} `json:"edge"`
}

type edgeNode struct {
	Name    string `json:"name"`
	Site    string `json:"site"`
	Rack    string `json:"rack"`
	Host    string `json:"host"`
	Uplinks []struct {
		Name      string  `json:"name"`
		VNIC      string  `json:"vnic"`
		SpeedGbps float64 `json:"speedGbps"`
		ToR       string  `json:"tor"`
	} `json:"uplinks"`
}

type compatibilitySnapshot struct {
	SupportedTarget struct {
		Release    string `json:"release"`
		Components []struct {
			Name    string `json:"name"`
			Version string `json:"version"`
		} `json:"components"`
	} `json:"supportedTarget"`
	EdgeFormFactors []struct {
		Name                       string  `json:"name"`
		Supported                  bool    `json:"supported"`
		MaxValidatedNorthSouthGbps float64 `json:"maxValidatedNorthSouthGbps"`
	} `json:"edgeFormFactors"`
	UplinkLayouts []struct {
		Name                   string   `json:"name"`
		SupportedFormFactors   []string `json:"supportedFormFactors"`
		UplinkCount            int      `json:"uplinkCount"`
		LinkSpeedGbps          float64  `json:"linkSpeedGbps"`
		DistinctFailureDomains bool     `json:"distinctFailureDomains"`
	} `json:"uplinkLayouts"`
	MigrationTransitions []transition `json:"migrationTransitions"`
}

type estateInventory struct {
	EstateID   string `json:"estateId"`
	Components []struct {
		Name    string `json:"name"`
		Version string `json:"version"`
	} `json:"components"`
}

type gate struct {
	ID                string   `json:"id"`
	RequiresCompleted []string `json:"requiresCompleted"`
	Checks            []string `json:"checks"`
}

type transition struct {
	Order           int    `json:"order"`
	Component       string `json:"component"`
	FromVersion     string `json:"fromVersion"`
	TargetComponent string `json:"targetComponent"`
	TargetVersion   string `json:"targetVersion"`
	Action          string `json:"action"`
	Gate            gate   `json:"gate"`
}

type migrationPlan struct {
	SchemaVersion string `json:"schemaVersion"`
	EstateID      string `json:"estateId"`
	TargetRelease string `json:"targetRelease"`
	Steps         []struct {
		Order           int    `json:"order"`
		Component       string `json:"component"`
		CurrentVersion  string `json:"currentVersion"`
		TargetComponent string `json:"targetComponent"`
		TargetVersion   string `json:"targetVersion"`
		Action          string `json:"action"`
		Gate            gate   `json:"gate"`
	} `json:"steps"`
}

type researchRecord struct {
	Consulted []struct {
		Title    string   `json:"title"`
		URL      string   `json:"url"`
		Accessed string   `json:"accessed"`
		Informed []string `json:"informed"`
	} `json:"consulted"`
}

func checkInstallerIntent(req requirements, got sddcSpec) error {
	if got.SddcID != req.Installer.SddcID || got.VCFInstanceName != req.Installer.VCFInstanceName {
		return fmt.Errorf("installer identity does not match the requirements")
	}
	if got.WorkflowType != "VCF" {
		return fmt.Errorf("workflowType = %q, want VCF", got.WorkflowType)
	}
	if got.VcenterSpec.UseExistingDeployment || got.NsxtSpec.UseExistingDeployment || got.SddcManagerSpec.UseExistingDeployment {
		return fmt.Errorf("greenfield components must not reuse an existing deployment")
	}
	if got.SkipEsxThumbprintValidation || got.SkipGatewayPingValidation {
		return fmt.Errorf("installer validation may not be skipped")
	}
	if got.DNS.Subdomain != req.Installer.DNSSubdomain || !reflect.DeepEqual(got.DNS.Nameservers, req.Installer.DNSServers) {
		return fmt.Errorf("DNS design does not match the requirements")
	}
	if !reflect.DeepEqual(got.NTPServers, req.Installer.NTPServers) {
		return fmt.Errorf("NTP design does not match the requirements")
	}
	if len(got.HostSpecs) != req.Management.HostCount {
		return fmt.Errorf("hostSpecs has %d hosts, want %d", len(got.HostSpecs), req.Management.HostCount)
	}
	hosts := map[string]bool{}
	for _, host := range got.HostSpecs {
		if host.Hostname == "" || hosts[host.Hostname] {
			return fmt.Errorf("host names must be non-empty and unique")
		}
		hosts[host.Hostname] = true
	}
	if got.DatastoreSpec.VsanSpec.FailuresToTolerate != req.Management.VsanFailuresToTolerate || got.DatastoreSpec.VsanSpec.ESAConfig.Enabled != req.Management.VsanESA {
		return fmt.Errorf("vSAN availability/storage model does not match the requirements")
	}
	return nil
}

func checkTargetCombination(req requirements, snapshot compatibilitySnapshot, got sddcSpec) error {
	if snapshot.SupportedTarget.Release != req.TargetRelease || got.Version != req.TargetRelease {
		return fmt.Errorf("target release is not the pinned supported release")
	}
	versions := map[string]string{}
	for _, component := range snapshot.SupportedTarget.Components {
		versions[component.Name] = component.Version
	}
	actual := map[string]string{
		"VCF_INSTALLER":                   got.Version,
		"VCF_OPERATIONS_FLEET_MANAGEMENT": got.VCFOperationsFleetManagementSpec.Version,
		"VCF_OPERATIONS":                  got.VCFOperationsSpec.Version,
		"VCF_AUTOMATION":                  got.VCFAutomationSpec.Version,
		"SDDC_MANAGER":                    got.SddcManagerSpec.Version,
		"NSX_MANAGER":                     got.NsxtSpec.Version,
		"VCENTER":                         got.VcenterSpec.Version,
	}
	for component, version := range actual {
		if want := versions[component]; want == "" || version != want {
			return fmt.Errorf("%s version = %q, pinned target = %q", component, version, want)
		}
	}
	return nil
}

func checkSitesAndCapacity(req requirements, got sddcSpec) error {
	arch := got.Architecture
	if arch.ScenarioID != req.ScenarioID || !reflect.DeepEqual(arch.Sites, req.Sites) {
		return fmt.Errorf("site topology does not match the stated primary/recovery sites")
	}
	if !reflect.DeepEqual(arch.SiteRecovery, req.SiteRecovery) || arch.SiteRecovery.StretchedManagementCluster {
		return fmt.Errorf("recovery topology, RTT, RPO, or RTO is incorrect")
	}
	cap := arch.Capacity
	if cap.ManagementHostCount != req.Management.HostCount || cap.PerHostCPUCores != req.Management.PerHostCPUCores || cap.PerHostMemoryGiB != req.Management.PerHostMemoryGiB || !closeFloat(cap.PerHostRawStorageTiB, req.Management.PerHostRawStorageTiB) {
		return fmt.Errorf("capacity inputs are not represented exactly")
	}
	survivors := req.Management.HostCount - req.Availability.HostFailuresToTolerate
	wantCPU := survivors * req.Management.PerHostCPUCores
	wantMemory := survivors * req.Management.PerHostMemoryGiB
	wantStorage := float64(survivors) * req.Management.PerHostRawStorageTiB
	after := cap.AvailableAfterOneHostFailure
	if after.CPUCores != wantCPU || after.MemoryGiB != wantMemory || !closeFloat(after.RawStorageTiB, wantStorage) {
		return fmt.Errorf("post-host-failure capacity is not derived from the host design")
	}
	if after.CPUCores < req.Management.RequiredCPUCoresAfterHostFailure || after.MemoryGiB < req.Management.RequiredMemoryGiBAfterHostFailure || after.RawStorageTiB < req.Management.RequiredRawStorageTiBAfterHostFailure {
		return fmt.Errorf("post-host-failure capacity does not meet the requirement")
	}
	return nil
}

func checkManagementNetworking(req requirements, got sddcSpec) error {
	if len(got.DVSSpecs) == 0 {
		return fmt.Errorf("no installer DVS design was supplied")
	}
	dvs := got.DVSSpecs[0]
	if dvs.MTU != 9000 || len(dvs.VmnicsToUplinks) != req.Management.PerHostNICCount {
		return fmt.Errorf("DVS does not carry the required two-NIC design")
	}
	wantMappings := map[string]string{"vmnic0": "uplink1", "vmnic1": "uplink2"}
	for _, mapping := range dvs.VmnicsToUplinks {
		if wantMappings[mapping.ID] != mapping.Uplink {
			return fmt.Errorf("unexpected DVS mapping %s -> %s", mapping.ID, mapping.Uplink)
		}
		delete(wantMappings, mapping.ID)
	}
	if len(wantMappings) != 0 {
		return fmt.Errorf("DVS is missing a physical failure-domain mapping")
	}
	if len(dvs.NSXTeamings) != 1 || dvs.NSXTeamings[0].Policy != "LOADBALANCE_SRCID" || !sameStrings(dvs.NSXTeamings[0].ActiveUplinks, []string{"uplink1", "uplink2"}) {
		return fmt.Errorf("NSX teaming must keep both diverse uplinks active")
	}
	networks := map[string]networkRequirement{}
	for _, network := range req.Installer.Networks {
		networks[network.Type] = network
	}
	for _, network := range got.NetworkSpecs {
		want, ok := networks[network.NetworkType]
		if !ok {
			continue
		}
		if network.VLANID != want.VLANID || network.Subnet != want.Subnet || network.Gateway != want.Gateway || network.MTU != want.MTU || len(network.IncludeIPAddressRanges) != 1 || network.IncludeIPAddressRanges[0].Start != want.PoolStart || network.IncludeIPAddressRanges[0].End != want.PoolEnd {
			return fmt.Errorf("network %s does not match its protected requirement", network.NetworkType)
		}
		delete(networks, network.NetworkType)
	}
	if len(networks) != 0 {
		return fmt.Errorf("installer networkSpecs omit required networks: %v", sortedKeys(networks))
	}
	return nil
}

func checkEdge(req requirements, snapshot compatibilitySnapshot, got sddcSpec) error {
	required := req.Traffic.PeakGbps * (1 + req.Traffic.HeadroomPercent/100)
	arch := got.Architecture
	if arch.Traffic.SteadyGbps != req.Traffic.SteadyGbps || arch.Traffic.PeakGbps != req.Traffic.PeakGbps || arch.Traffic.HeadroomPercent != req.Traffic.HeadroomPercent || !closeFloat(arch.Traffic.RequiredFailoverThroughputGbps, required) {
		return fmt.Errorf("Edge failover throughput is not peak plus headroom")
	}
	wantForm := ""
	wantCapacity := math.MaxFloat64
	for _, form := range snapshot.EdgeFormFactors {
		if form.Supported && form.MaxValidatedNorthSouthGbps >= required && form.MaxValidatedNorthSouthGbps < wantCapacity {
			wantForm, wantCapacity = form.Name, form.MaxValidatedNorthSouthGbps
		}
	}
	if wantForm == "" {
		return fmt.Errorf("pinned snapshot has no Edge form factor for %.2f Gbps", required)
	}
	edge := arch.Edge
	if edge.FormFactor != wantForm || !closeFloat(edge.PerNodeValidatedThroughputGbps, wantCapacity) {
		return fmt.Errorf("Edge form factor %s is not the smallest supported failover size %s", edge.FormFactor, wantForm)
	}
	if edge.HAMode != "ACTIVE_ACTIVE" || edge.NodeCount != req.Availability.EdgeFailuresToTolerate+1 || len(edge.Nodes) != edge.NodeCount {
		return fmt.Errorf("Edge HA design does not tolerate the required node failure")
	}
	wantLayout := ""
	wantLinkSpeed := math.MaxFloat64
	wantUplinks := 0
	for _, layout := range snapshot.UplinkLayouts {
		if layout.DistinctFailureDomains && layout.UplinkCount >= req.Availability.TorFailuresToTolerate+1 && layout.LinkSpeedGbps >= required && contains(layout.SupportedFormFactors, wantForm) && layout.LinkSpeedGbps < wantLinkSpeed {
			wantLayout, wantLinkSpeed, wantUplinks = layout.Name, layout.LinkSpeedGbps, layout.UplinkCount
		}
	}
	if edge.UplinkLayout != wantLayout || wantLayout == "" {
		return fmt.Errorf("Edge uplink layout %s is not the supported failover layout %s", edge.UplinkLayout, wantLayout)
	}
	hosts := map[string]bool{}
	for _, host := range got.HostSpecs {
		hosts[host.Hostname] = true
	}
	usedHosts, usedRacks := map[string]bool{}, map[string]bool{}
	for _, node := range edge.Nodes {
		if node.Site != "CHI01" || !hosts[node.Host] || usedHosts[node.Host] || usedRacks[node.Rack] {
			return fmt.Errorf("Edge nodes must occupy distinct CHI hosts and racks")
		}
		usedHosts[node.Host], usedRacks[node.Rack] = true, true
		if len(node.Uplinks) != wantUplinks {
			return fmt.Errorf("Edge node %s has %d uplinks, want %d", node.Name, len(node.Uplinks), wantUplinks)
		}
		usedToR, usedVNIC := map[string]bool{}, map[string]bool{}
		for _, uplink := range node.Uplinks {
			if uplink.SpeedGbps < required || usedToR[uplink.ToR] || usedVNIC[uplink.VNIC] {
				return fmt.Errorf("each Edge uplink must independently carry failover load on a distinct ToR and vNIC")
			}
			usedToR[uplink.ToR], usedVNIC[uplink.VNIC] = true, true
		}
	}
	if arch.Availability.HostFailuresToTolerate != req.Availability.HostFailuresToTolerate || arch.Availability.EdgeFailuresToTolerate != req.Availability.EdgeFailuresToTolerate || arch.Availability.TorFailuresToTolerate != req.Availability.TorFailuresToTolerate || arch.Availability.EdgePlacement != "ANTI_AFFINITY_ACROSS_RACKS" {
		return fmt.Errorf("architecture availability declaration is incomplete")
	}
	return nil
}

func checkMigration(estate estateInventory, snapshot compatibilitySnapshot, got migrationPlan) error {
	if got.SchemaVersion != "1.0" || got.EstateID != estate.EstateID || got.TargetRelease != snapshot.SupportedTarget.Release {
		return fmt.Errorf("migration plan identity or target is incorrect")
	}
	if len(got.Steps) != len(estate.Components) || len(got.Steps) != len(snapshot.MigrationTransitions) {
		return fmt.Errorf("migration has %d steps for %d inventory components", len(got.Steps), len(estate.Components))
	}
	inventory := map[string]string{}
	for _, component := range estate.Components {
		if _, duplicate := inventory[component.Name]; duplicate {
			return fmt.Errorf("fixture contains duplicate component %s", component.Name)
		}
		inventory[component.Name] = component.Version
	}
	transitions := map[string]transition{}
	for _, transition := range snapshot.MigrationTransitions {
		transitions[transition.Component] = transition
	}
	supportedTargets := map[string]string{}
	for _, component := range snapshot.SupportedTarget.Components {
		supportedTargets[component.Name] = component.Version
	}
	completed := map[string]bool{}
	for index, step := range got.Steps {
		if step.Order != index+1 {
			return fmt.Errorf("migration order at index %d is %d", index, step.Order)
		}
		if completed[step.Component] {
			return fmt.Errorf("component %s appears more than once", step.Component)
		}
		current, exists := inventory[step.Component]
		if !exists || step.CurrentVersion != current {
			return fmt.Errorf("component %s does not preserve its inventory version", step.Component)
		}
		transition, exists := transitions[step.Component]
		if !exists || transition.Order != step.Order || transition.FromVersion != step.CurrentVersion || transition.TargetComponent != step.TargetComponent || transition.TargetVersion != step.TargetVersion || transition.Action != step.Action || !reflect.DeepEqual(transition.Gate, step.Gate) {
			return fmt.Errorf("step %d does not match the pinned supported transition", step.Order)
		}
		if supportedTargets[step.TargetComponent] != step.TargetVersion {
			return fmt.Errorf("step %d target is not in the pinned target combination", step.Order)
		}
		for _, prerequisite := range step.Gate.RequiresCompleted {
			if !completed[prerequisite] {
				return fmt.Errorf("step %d prerequisite %s is not complete", step.Order, prerequisite)
			}
		}
		completed[step.Component] = true
	}
	for component := range inventory {
		if !completed[component] {
			return fmt.Errorf("inventory component %s has no migration step", component)
		}
	}
	return nil
}

func readFile(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return data
}

func decodeAny(t *testing.T, data []byte) any {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		t.Fatalf("decode JSON: %v", err)
	}
	if decoder.More() {
		t.Fatal("JSON contains more than one value")
	}
	return value
}

func decodeInto(t *testing.T, data []byte, target any) {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(data))
	if err := decoder.Decode(target); err != nil {
		t.Fatalf("decode typed JSON: %v", err)
	}
}

func objectAt(t *testing.T, root any, path ...string) any {
	t.Helper()
	current := root
	for _, key := range path {
		object, ok := current.(map[string]any)
		if !ok {
			t.Fatalf("schema path %v traverses a non-object", path)
		}
		current, ok = object[key]
		if !ok {
			t.Fatalf("schema path %v is missing %s", path, key)
		}
	}
	return current
}

func assertJSONEqual(t *testing.T, actual, expected []byte, label string) {
	t.Helper()
	want, got := decodeAny(t, expected), decodeAny(t, actual)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("design.Build %s differs from the checked-in artifact", label)
	}
}

func mustJSON(t *testing.T, value any) []byte {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("encode JSON: %v", err)
	}
	return data
}

func testCommand(t *testing.T, requirements, estate, compatibility, wantSddc, wantMigration []byte) {
	t.Helper()
	tempDir := t.TempDir()
	binary := filepath.Join(tempDir, "vcfdesign")
	build := exec.Command("go", "build", "-o", binary, "./cmd/vcfdesign")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build vcfdesign command: %v\n%s", err, output)
	}
	inputs := []struct {
		path string
		data []byte
	}{
		{path: "fixtures/design-requirements.json", data: requirements},
		{path: "fixtures/estate.json", data: estate},
		{path: "compatibility/compatibility-snapshot.json", data: compatibility},
	}
	for _, input := range inputs {
		path := filepath.Join(tempDir, input.path)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatalf("create command fixture directory: %v", err)
		}
		if err := os.WriteFile(path, input.data, 0o644); err != nil {
			t.Fatalf("write command fixture %s: %v", input.path, err)
		}
	}
	run := exec.Command(binary)
	run.Dir = tempDir
	if output, err := run.CombinedOutput(); err != nil {
		t.Fatalf("run vcfdesign command: %v\n%s", err, output)
	}
	assertJSONEqual(t, readFile(t, filepath.Join(tempDir, "artifacts/sddc-spec.json")), wantSddc, "command SddcSpec")
	assertJSONEqual(t, readFile(t, filepath.Join(tempDir, "artifacts/migration-plan.json")), wantMigration, "command migration plan")
}

func closeFloat(a, b float64) bool { return math.Abs(a-b) < 0.000001 }

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func sameStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	aa, bb := append([]string(nil), a...), append([]string(nil), b...)
	sort.Strings(aa)
	sort.Strings(bb)
	return reflect.DeepEqual(aa, bb)
}

func sortedKeys[V any](values map[string]V) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}
