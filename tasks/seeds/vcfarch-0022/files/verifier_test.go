package vcfarchitecture

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"net/netip"
	"net/url"
	"os"
	"os/exec"
	"reflect"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"
	"unicode/utf8"
)

const (
	installerSchemaSHA256 = "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"
	scenarioSHA256        = "495c654b7477f9084d98a3a11d58fe7465fdd41f8d534f46d51608497c4cf2dd"
	estateSHA256          = "c86452b1bace7fd77fb5843b269e4c50917ef20b89caaa48df3df7f5cffa277e"
	compatibilitySHA256   = "c390e7f9aa1684243fd1cc14afbb38b54006fb602aced5488272c402efff79c9"
	migrationSchemaSHA256 = "9b47495316d26a14d32baa82cd5b0a63f5070eca2e7806ad9fe688c63ed1736c"
)

type sddcView struct {
	SDDCID          string `json:"sddcId"`
	WorkflowType    string `json:"workflowType"`
	Version         string `json:"version"`
	VCFInstanceName string `json:"vcfInstanceName"`
	HostSpecs       []struct {
		Hostname string `json:"hostname"`
	} `json:"hostSpecs"`
	VCenterSpec struct {
		Hostname              string `json:"vcenterHostname"`
		RootPassword          string `json:"rootVcenterPassword"`
		Version               string `json:"version"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
	} `json:"vcenterSpec"`
	ClusterSpec struct {
		DatacenterName string `json:"datacenterName"`
		ClusterName    string `json:"clusterName"`
	} `json:"clusterSpec"`
	DVSSpecs []struct {
		Name            string   `json:"dvsName"`
		Networks        []string `json:"networks"`
		MTU             int      `json:"mtu"`
		VmnicsToUplinks []struct {
			ID     string `json:"id"`
			Uplink string `json:"uplink"`
		} `json:"vmnicsToUplinks"`
	} `json:"dvsSpecs"`
	NSXTSpec struct {
		Managers []struct {
			Hostname string `json:"hostname"`
		} `json:"nsxtManagers"`
		VIP                   string `json:"vipFqdn"`
		TransportVLAN         int    `json:"transportVlanId"`
		Version               string `json:"version"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
	} `json:"nsxtSpec"`
	NetworkSpecs []struct {
		Type       string `json:"networkType"`
		VLAN       int    `json:"vlanId"`
		CIDR       string `json:"subnet"`
		Gateway    string `json:"gateway"`
		SubnetMask string `json:"subnetMask"`
		MTU        int    `json:"mtu"`
		Ranges     []struct {
			Start string `json:"startIpAddress"`
			End   string `json:"endIpAddress"`
		} `json:"includeIpAddressRanges"`
	} `json:"networkSpecs"`
	DNSSpec struct {
		Subdomain   string   `json:"subdomain"`
		Nameservers []string `json:"nameservers"`
	} `json:"dnsSpec"`
	NTPServers      []string `json:"ntpServers"`
	SDDCManagerSpec struct {
		Hostname              string `json:"hostname"`
		Version               string `json:"version"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
	} `json:"sddcManagerSpec"`
	DatastoreSpec struct {
		VSANSpec struct {
			DatastoreName      string `json:"datastoreName"`
			FailuresToTolerate int    `json:"failuresToTolerate"`
			ESAConfig          struct {
				Enabled bool `json:"enabled"`
			} `json:"esaConfig"`
		} `json:"vsanSpec"`
	} `json:"datastoreSpec"`
	VSPClusterSpec struct {
		PlatformFQDN          string `json:"platformFqdn"`
		InstanceFQDN          string `json:"instanceFqdn"`
		FleetFQDN             string `json:"fleetFqdn"`
		Size                  string `json:"size"`
		Version               string `json:"version"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
		InternalClusterCIDR   string `json:"internalClusterCidrIpv4"`
		IPv4Pool              struct {
			CIDR      string   `json:"cidr"`
			Addresses []string `json:"addresses"`
		} `json:"ipv4Pool"`
	} `json:"vspClusterSpec"`
	FleetLCMSpec struct {
		Version string `json:"version"`
	} `json:"fleetLcmSpec"`
	SDDCLCMSpec struct {
		Version string `json:"version"`
	} `json:"sddcLcmSpec"`
	FleetDepotSpec struct {
		Version string `json:"version"`
	} `json:"fleetDepotSpec"`
	VCFOperationsSpec struct {
		Version               string `json:"version"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
		LoadBalancer          string `json:"loadBalancerFqdn"`
		Nodes                 []struct {
			Hostname string `json:"hostname"`
			Type     string `json:"type"`
		} `json:"nodes"`
	} `json:"vcfOperationsSpec"`
	LicenseServerSpec struct {
		Hostname              string `json:"hostname"`
		Version               string `json:"version"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
	} `json:"licenseServerSpec"`
	ManagementInfrastructure struct {
		LocalRegionNetwork struct {
			NetworkName string `json:"networkName"`
			SubnetMask  string `json:"subnetMask"`
			Gateway     string `json:"gateway"`
		} `json:"localRegionNetwork"`
	} `json:"vcfManagementComponentsInfrastructureSpec"`
}

// TestArchitectureArtifacts is deliberately one ordered verifier. The installer
// schema validation is completed before the verifier examines any fixture,
// compatibility authority, migration plan, or semantic design property.
func TestArchitectureArtifacts(t *testing.T) {
	openAPIRaw := mustRead(t, "specifications/vcf-installer/vcf-installer-openapi.json")
	sddcRaw := mustRead(t, "artifacts/sddc-spec.json")
	openAPI := decodeJSON(t, openAPIRaw)
	sddcValue := decodeJSON(t, sddcRaw)
	sddcSchema, err := resolvePointer(openAPI, "#/components/schemas/SddcSpec")
	if err != nil {
		t.Fatalf("resolve OpenAPI SddcSpec: %v", err)
	}
	if problems := validateJSONSchema(openAPI, sddcSchema, sddcValue, "$", 0); len(problems) != 0 {
		sort.Strings(problems)
		t.Fatalf("artifacts/sddc-spec.json does not validate against the installer specification's SddcSpec schema:\n%s", strings.Join(problems, "\n"))
	}

	// Everything below is intentionally after the installer schema validation.
	digest := sha256.Sum256(openAPIRaw)
	if got := hex.EncodeToString(digest[:]); got != installerSchemaSHA256 {
		t.Fatalf("installer specification was modified: got sha256 %s", got)
	}

	var scenario Scenario
	var estate Estate
	var authority CompatibilitySnapshot
	mustPinnedLoad(t, "fixtures/scenario.json", scenarioSHA256, &scenario)
	mustPinnedLoad(t, "fixtures/estate.json", estateSHA256, &estate)
	mustPinnedLoad(t, "fixtures/compatibility-snapshot.json", compatibilitySHA256, &authority)

	var view sddcView
	if err := json.Unmarshal(sddcRaw, &view); err != nil {
		t.Fatalf("decode typed SddcSpec view: %v", err)
	}

	planRaw := mustRead(t, "artifacts/migration-plan.json")
	planValue := decodeJSON(t, planRaw)
	planSchemaRaw := mustRead(t, "schemas/migration-plan.schema.json")
	assertSHA256(t, "schemas/migration-plan.schema.json", planSchemaRaw, migrationSchemaSHA256)
	planSchema := decodeJSON(t, planSchemaRaw)
	if problems := validateJSONSchema(planSchema, planSchema, planValue, "$", 0); len(problems) != 0 {
		sort.Strings(problems)
		t.Fatalf("artifacts/migration-plan.json does not validate against schemas/migration-plan.schema.json:\n%s", strings.Join(problems, "\n"))
	}
	var plan MigrationPlan
	if err := json.Unmarshal(planRaw, &plan); err != nil {
		t.Fatalf("decode migration plan: %v", err)
	}

	cases := []struct {
		name  string
		check func() error
	}{
		{"release and supported component combination", func() error {
			want := authority.SupportedCombination
			actual := map[string]string{
				"VCF_INSTALLER":  view.Version,
				"SDDC_MANAGER":   view.SDDCManagerSpec.Version,
				"VCENTER":        view.VCenterSpec.Version,
				"NSX":            view.NSXTSpec.Version,
				"VSP":            view.VSPClusterSpec.Version,
				"VCF_FLEET_LCM":  view.FleetLCMSpec.Version,
				"VCF_SDDC_LCM":   view.SDDCLCMSpec.Version,
				"DEPOT_SERVICE":  view.FleetDepotSpec.Version,
				"VCF_OPERATIONS": view.VCFOperationsSpec.Version,
				"LICENSE_SERVER": view.LicenseServerSpec.Version,
			}
			for component, got := range actual {
				if got != want[component] {
					return fmt.Errorf("%s version = %q, want %q", component, got, want[component])
				}
			}
			if view.WorkflowType != "VCF" || view.SDDCID != scenario.Names.SDDCID {
				return fmt.Errorf("workflow/sddc identity is %q/%q", view.WorkflowType, view.SDDCID)
			}
			return nil
		}},
		{"minimum consolidated host set", func() error {
			if scenario.Topology.Architecture != "CONSOLIDATED" || scenario.Topology.Stretched || scenario.Site.FailureDomains != 1 {
				return fmt.Errorf("fixture topology is not single-site consolidated")
			}
			if len(view.HostSpecs) != authority.MinimumSupportedHosts || len(view.HostSpecs) != scenario.Topology.AvailableHosts {
				return fmt.Errorf("host count = %d, minimum/available = %d/%d", len(view.HostSpecs), authority.MinimumSupportedHosts, scenario.Topology.AvailableHosts)
			}
			got := make([]string, len(view.HostSpecs))
			seen := map[string]bool{}
			for i, host := range view.HostSpecs {
				if seen[host.Hostname] {
					return fmt.Errorf("duplicate host %q", host.Hostname)
				}
				seen[host.Hostname] = true
				got[i] = host.Hostname
			}
			if !slices.Equal(got, scenario.Names.Hostnames) {
				return fmt.Errorf("hostnames = %v, want %v", got, scenario.Names.Hostnames)
			}
			return nil
		}},
		{"single-host-failure capacity", func() error {
			survivors := len(view.HostSpecs) - scenario.Availability.HostFailuresToTolerate
			cpu := survivors*scenario.HostProfile.CPUCores - scenario.Capacity.ManagementReservedCores
			memory := survivors*scenario.HostProfile.MemoryGiB - scenario.Capacity.ManagementReservedMemoryGiB
			storage := survivors*scenario.HostProfile.ProtectedUsableTiB - scenario.Capacity.ManagementReservedStorageTiB
			if cpu < scenario.Capacity.WorkloadCPUCoreMinimum || memory < scenario.Capacity.WorkloadMemoryGiBMinimum || storage < scenario.Capacity.WorkloadStorageTiBMinimum {
				return fmt.Errorf("surviving workload capacity %d cores/%d GiB/%d TiB is below %d/%d/%d", cpu, memory, storage, scenario.Capacity.WorkloadCPUCoreMinimum, scenario.Capacity.WorkloadMemoryGiBMinimum, scenario.Capacity.WorkloadStorageTiBMinimum)
			}
			return nil
		}},
		{"availability and greenfield appliances", func() error {
			if view.DatastoreSpec.VSANSpec.FailuresToTolerate != scenario.Availability.HostFailuresToTolerate || !view.DatastoreSpec.VSANSpec.ESAConfig.Enabled {
				return fmt.Errorf("vSAN policy does not encode ESA FTT=%d", scenario.Availability.HostFailuresToTolerate)
			}
			gotNSXManagers := make([]string, len(view.NSXTSpec.Managers))
			for i, manager := range view.NSXTSpec.Managers {
				gotNSXManagers[i] = manager.Hostname
			}
			if !slices.Equal(gotNSXManagers, scenario.Names.NSXManagers) {
				return fmt.Errorf("NSX managers = %v, want %v", gotNSXManagers, scenario.Names.NSXManagers)
			}
			gotOperationsNodes := make([]string, len(view.VCFOperationsSpec.Nodes))
			gotOperationsTypes := make([]string, len(view.VCFOperationsSpec.Nodes))
			for i, node := range view.VCFOperationsSpec.Nodes {
				gotOperationsNodes[i] = node.Hostname
				gotOperationsTypes[i] = node.Type
			}
			sort.Strings(gotOperationsTypes)
			if !slices.Equal(gotOperationsNodes, scenario.Names.VCFOperationsNodes) || !slices.Equal(gotOperationsTypes, []string{"data", "master", "replica"}) || view.VCFOperationsSpec.LoadBalancer != scenario.Names.VCFOperationsLoadBalancer {
				return fmt.Errorf("VCF Operations names do not match the scenario")
			}
			if view.VSPClusterSpec.PlatformFQDN != scenario.Names.VSPPlatform || view.VSPClusterSpec.InstanceFQDN != scenario.Names.VSPInstance || view.VSPClusterSpec.FleetFQDN != scenario.Names.VSPFleet || view.VSPClusterSpec.Size != scenario.VSP.Size || view.VSPClusterSpec.InternalClusterCIDR != scenario.VSP.InternalClusterCIDRIPv4 || view.VSPClusterSpec.IPv4Pool.CIDR != scenario.VSP.PoolCIDR || !slices.Equal(view.VSPClusterSpec.IPv4Pool.Addresses, scenario.VSP.Addresses) {
				return fmt.Errorf("VSP names, sizing, or address pools do not match the scenario")
			}
			if view.VCenterSpec.UseExistingDeployment || view.NSXTSpec.UseExistingDeployment || view.SDDCManagerSpec.UseExistingDeployment || view.VSPClusterSpec.UseExistingDeployment || view.VCFOperationsSpec.UseExistingDeployment || view.LicenseServerSpec.UseExistingDeployment {
				return fmt.Errorf("a greenfield component is marked useExistingDeployment")
			}
			return nil
		}},
		{"names DNS and NTP", func() error {
			if view.VCenterSpec.Hostname != scenario.Names.VCenter || view.SDDCManagerSpec.Hostname != scenario.Names.SDDCManager || view.NSXTSpec.VIP != scenario.Names.NSXVIP {
				return fmt.Errorf("management appliance names do not match the scenario")
			}
			if view.VCFInstanceName != scenario.Names.InstanceName || view.ClusterSpec.DatacenterName != scenario.Names.Datacenter || view.ClusterSpec.ClusterName != scenario.Names.Cluster || view.DatastoreSpec.VSANSpec.DatastoreName != scenario.Names.Datastore {
				return fmt.Errorf("inventory names do not match the scenario")
			}
			if view.VCenterSpec.RootPassword != scenario.Placeholder.VCenterRoot {
				return fmt.Errorf("vCenter placeholder credential does not match the scenario")
			}
			if view.LicenseServerSpec.Hostname != scenario.Names.LicenseServer {
				return fmt.Errorf("license server name does not match the scenario")
			}
			if view.DNSSpec.Subdomain != scenario.Names.Domain || !slices.Equal(view.DNSSpec.Nameservers, scenario.Network.DNS) || !slices.Equal(view.NTPServers, scenario.Network.NTP) {
				return fmt.Errorf("DNS/NTP settings do not match the scenario")
			}
			return nil
		}},
		{"network VLAN MTU ranges and uplinks", func() error {
			if len(view.NetworkSpecs) != len(scenario.Network.Segments) || len(view.DVSSpecs) != 1 {
				return fmt.Errorf("network/DVS counts are %d/%d", len(view.NetworkSpecs), len(view.DVSSpecs))
			}
			byType := map[string]NetworkSegment{}
			for _, segment := range scenario.Network.Segments {
				byType[segment.Type] = segment
			}
			seenTypes := map[string]bool{}
			for _, got := range view.NetworkSpecs {
				want, ok := byType[got.Type]
				if !ok || seenTypes[got.Type] || got.VLAN != want.VLAN || got.CIDR != want.CIDR || got.Gateway != want.Gateway || got.SubnetMask != want.SubnetMask || got.MTU != want.MTU || len(got.Ranges) != 1 || got.Ranges[0].Start != want.StartIP || got.Ranges[0].End != want.EndIP {
					return fmt.Errorf("network %s does not match its fixed segment", got.Type)
				}
				seenTypes[got.Type] = true
			}
			dvs := view.DVSSpecs[0]
			wantDVSNetworks := make([]string, len(scenario.Network.Segments))
			wantDVSMTU := 0
			for i, segment := range scenario.Network.Segments {
				wantDVSNetworks[i] = segment.Type
				wantDVSMTU = max(wantDVSMTU, segment.MTU)
			}
			if dvs.Name != scenario.Names.DVS || dvs.MTU != wantDVSMTU || !slices.Equal(dvs.Networks, wantDVSNetworks) || len(dvs.VmnicsToUplinks) != len(scenario.Network.Uplinks) {
				return fmt.Errorf("DVS name, MTU, or uplink count is incorrect")
			}
			if view.NSXTSpec.TransportVLAN != scenario.Network.TransportVLAN {
				return fmt.Errorf("NSX transport VLAN = %d, want %d", view.NSXTSpec.TransportVLAN, scenario.Network.TransportVLAN)
			}
			for i, want := range scenario.Network.Uplinks {
				if dvs.VmnicsToUplinks[i].ID != want.VMNIC || dvs.VmnicsToUplinks[i].Uplink != want.Uplink {
					return fmt.Errorf("uplink %d does not match the scenario", i)
				}
			}
			management := byType["VM_MANAGEMENT"]
			localRegion := view.ManagementInfrastructure.LocalRegionNetwork
			if localRegion.NetworkName != management.Type || localRegion.SubnetMask != management.SubnetMask || localRegion.Gateway != management.Gateway {
				return fmt.Errorf("management services network does not match VM_MANAGEMENT")
			}
			return nil
		}},
		{"migration coverage order targets and gates", func() error {
			if plan.EstateID != estate.EstateID || plan.TargetVCFVersion != authority.TargetVCFVersion || len(plan.Steps) != len(estate.Components) || len(plan.Steps) != len(authority.MigrationPaths) {
				return fmt.Errorf("plan header or step count does not cover the estate")
			}
			estateByID := map[string]EstateComponent{}
			for _, component := range estate.Components {
				estateByID[component.ID] = component
			}
			seen := map[string]bool{}
			for i, step := range plan.Steps {
				path := authority.MigrationPaths[i]
				component, ok := estateByID[step.ComponentID]
				if !ok || seen[step.ComponentID] {
					return fmt.Errorf("step %d has missing or duplicate component %q", i+1, step.ComponentID)
				}
				seen[step.ComponentID] = true
				if step.Order != i+1 || step.Order != path.Order || step.ComponentID != path.ComponentID || step.ComponentName != component.Name || step.FromVersion != component.Version || step.FromVersion != path.FromVersion || step.TargetComponent != path.TargetComponent || step.TargetVersion != path.TargetVersion || step.Action != path.Action || !slices.Equal(step.Gates, path.Gates) {
					return fmt.Errorf("step %d does not match estate inventory and pinned path", i+1)
				}
			}
			return nil
		}},
		{"renderer reproduces committed artifacts", func() error {
			gotSpec, gotPlan, err := Build(scenario, estate, authority)
			if err != nil {
				return err
			}
			gotSpecJSON, _ := json.Marshal(gotSpec)
			wantSpecJSON, _ := json.Marshal(sddcValue)
			gotPlanJSON, _ := json.Marshal(gotPlan)
			wantPlanJSON, _ := json.Marshal(plan)
			if !bytes.Equal(gotSpecJSON, wantSpecJSON) || !bytes.Equal(gotPlanJSON, wantPlanJSON) {
				return fmt.Errorf("Build output differs from committed artifacts")
			}
			command := exec.Command("go", "run", "./cmd/render")
			if output, err := command.CombinedOutput(); err != nil {
				return fmt.Errorf("go run ./cmd/render: %w\n%s", err, output)
			}
			renderedSpec, err := os.ReadFile("artifacts/sddc-spec.json")
			if err != nil {
				return err
			}
			renderedPlan, err := os.ReadFile("artifacts/migration-plan.json")
			if err != nil {
				return err
			}
			if !bytes.Equal(renderedSpec, sddcRaw) || !bytes.Equal(renderedPlan, planRaw) {
				return fmt.Errorf("go run ./cmd/render did not reproduce the committed artifact bytes")
			}
			return nil
		}},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if err := tc.check(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestResearchSources(t *testing.T) {
	content := string(mustRead(t, "artifacts/research-sources.md"))
	if strings.TrimSpace(content) == "" {
		t.Fatal("artifacts/research-sources.md is empty")
	}
	validDate := false
	for _, candidate := range regexp.MustCompile(`\b20[0-9]{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])\b`).FindAllString(content, -1) {
		if _, err := time.Parse("2006-01-02", candidate); err == nil {
			validDate = true
			break
		}
	}
	if !validDate {
		t.Fatal("research record does not contain a valid ISO access date")
	}

	rawURLs := regexp.MustCompile(`https?://[^\s|)>]+`).FindAllString(content, -1)
	if len(rawURLs) == 0 {
		t.Fatal("research record contains no web source URL")
	}
	uniqueBroadcom := map[string]bool{}
	for _, candidate := range rawURLs {
		raw := strings.TrimRight(candidate, ".,;")
		parsed, err := url.Parse(raw)
		if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil || parsed.Hostname() == "" {
			t.Fatalf("research source is not a valid web URL: %q", candidate)
		}
		host := strings.ToLower(parsed.Hostname())
		if host == "localhost" || strings.HasSuffix(host, ".localhost") || strings.HasSuffix(host, ".invalid") || strings.HasSuffix(host, ".test") || strings.HasSuffix(host, ".example") {
			t.Fatalf("research source is not a public host: %q", candidate)
		}
		if address, err := netip.ParseAddr(host); err == nil && (!address.IsGlobalUnicast() || address.IsPrivate()) {
			t.Fatalf("research source uses a non-public address: %q", candidate)
		}
		if !hasResearchTitleNear(content, raw) {
			t.Fatalf("research source has no nearby title: %q", candidate)
		}
		if host == "broadcom.com" || strings.HasSuffix(host, ".broadcom.com") {
			uniqueBroadcom[raw] = true
		}
	}
	if len(uniqueBroadcom) == 0 {
		t.Fatal("research record contains no Broadcom-published source")
	}

	lower := strings.ToLower(content)
	for _, subject := range []string{"compatib", "interoperab", "upgrade", "vcenter", "esx", "nsx", "management", "9.1"} {
		if !strings.Contains(lower, subject) {
			t.Errorf("research record does not cover %q", subject)
		}
	}
	if !strings.Contains(lower, "informed") && !strings.Contains(lower, "learned") && !strings.Contains(lower, "decision") {
		t.Error("research record does not explain a design or migration fact learned from the sources")
	}
}

func TestPackageIncludesTableDrivenTests(t *testing.T) {
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), "_test.go") || entry.Name() == "verifier_test.go" {
			continue
		}
		content := string(mustRead(t, entry.Name()))
		if regexp.MustCompile(`func\s+Test[A-Za-z0-9_]*\s*\(`).MatchString(content) && regexp.MustCompile(`(?s)\bfor\b.*\brange\b`).MatchString(content) {
			return
		}
	}
	t.Fatal("add a table-driven package test for the design logic")
}

func hasResearchTitleNear(content, rawURL string) bool {
	index := strings.Index(content, rawURL)
	if index < 0 {
		return false
	}
	lineStart := strings.LastIndex(content[:index], "\n") + 1
	lineEnd := strings.Index(content[index:], "\n")
	if lineEnd < 0 {
		lineEnd = len(content)
	} else {
		lineEnd += index
	}
	if plausibleResearchTitle(strings.Replace(content[lineStart:lineEnd], rawURL, "", 1)) {
		return true
	}
	for _, line := range slices.Backward(strings.Split(content[:lineStart], "\n")) {
		if strings.TrimSpace(line) == "" {
			continue
		}
		return plausibleResearchTitle(line)
	}
	return false
}

func plausibleResearchTitle(value string) bool {
	value = strings.Trim(value, " \t|[]()<>*`#-:;,")
	lower := strings.ToLower(value)
	if strings.HasPrefix(lower, "accessed") || strings.HasPrefix(lower, "access date") {
		return false
	}
	return utf8.RuneCountInString(value) >= 4 && regexp.MustCompile(`[[:alpha:]]`).MatchString(value)
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return b
}

func mustPinnedLoad(t *testing.T, path, wantSHA256 string, dst any) {
	t.Helper()
	raw := mustRead(t, path)
	assertSHA256(t, path, raw, wantSHA256)
	if err := json.Unmarshal(raw, dst); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func assertSHA256(t *testing.T, path string, raw []byte, want string) {
	t.Helper()
	digest := sha256.Sum256(raw)
	if got := hex.EncodeToString(digest[:]); got != want {
		t.Fatalf("%s was modified: got sha256 %s", path, got)
	}
}

func decodeJSON(t *testing.T, raw []byte) any {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		t.Fatalf("decode JSON: %v", err)
	}
	return value
}

func resolvePointer(root any, ref string) (any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("unsupported non-local ref %q", ref)
	}
	current := root
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%q traverses a non-object", ref)
		}
		current, ok = object[token]
		if !ok {
			return nil, fmt.Errorf("%q does not exist", ref)
		}
	}
	return current, nil
}

// validateJSONSchema implements the validation keywords used by the pinned
// OpenAPI component graph and the fixed migration-plan schema. Annotation-only
// OpenAPI keywords are intentionally ignored as JSON Schema requires.
func validateJSONSchema(root, rawSchema, value any, path string, depth int) []string {
	if depth > 100 {
		return []string{path + ": schema recursion limit exceeded"}
	}
	schema, ok := rawSchema.(map[string]any)
	if !ok {
		return []string{path + ": schema is not an object"}
	}
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolvePointer(root, ref)
		if err != nil {
			return []string{path + ": " + err.Error()}
		}
		return validateJSONSchema(root, resolved, value, path, depth+1)
	}
	var problems []string
	for _, keyword := range []string{"allOf"} {
		if branches, ok := schema[keyword].([]any); ok {
			for _, branch := range branches {
				problems = append(problems, validateJSONSchema(root, branch, value, path, depth+1)...)
			}
		}
	}
	for _, keyword := range []string{"anyOf", "oneOf"} {
		if branches, ok := schema[keyword].([]any); ok {
			matches := 0
			for _, branch := range branches {
				if len(validateJSONSchema(root, branch, value, path, depth+1)) == 0 {
					matches++
				}
			}
			if (keyword == "anyOf" && matches == 0) || (keyword == "oneOf" && matches != 1) {
				problems = append(problems, fmt.Sprintf("%s: %s matched %d branches", path, keyword, matches))
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
			problems = append(problems, fmt.Sprintf("%s: value is not in enum", path))
		}
	}
	if schemaType, ok := schema["type"].(string); ok && !matchesType(schemaType, value) {
		return append(problems, fmt.Sprintf("%s: expected %s, got %T", path, schemaType, value))
	}
	switch typed := value.(type) {
	case map[string]any:
		if required, ok := schema["required"].([]any); ok {
			for _, item := range required {
				name, _ := item.(string)
				if _, exists := typed[name]; !exists {
					problems = append(problems, fmt.Sprintf("%s: missing required property %q", path, name))
				}
			}
		}
		properties, _ := schema["properties"].(map[string]any)
		for name, child := range typed {
			if childSchema, exists := properties[name]; exists {
				problems = append(problems, validateJSONSchema(root, childSchema, child, path+"."+name, depth+1)...)
				continue
			}
			if allowed, ok := schema["additionalProperties"].(bool); ok && !allowed {
				problems = append(problems, fmt.Sprintf("%s: additional property %q is forbidden", path, name))
			}
		}
	case []any:
		if min, ok := number(schema["minItems"]); ok && float64(len(typed)) < min {
			problems = append(problems, fmt.Sprintf("%s: array length %d is below %v", path, len(typed), min))
		}
		if max, ok := number(schema["maxItems"]); ok && float64(len(typed)) > max {
			problems = append(problems, fmt.Sprintf("%s: array length %d is above %v", path, len(typed), max))
		}
		if itemSchema, exists := schema["items"]; exists {
			for i, item := range typed {
				problems = append(problems, validateJSONSchema(root, itemSchema, item, fmt.Sprintf("%s[%d]", path, i), depth+1)...)
			}
		}
	case string:
		length := utf8.RuneCountInString(typed)
		if min, ok := number(schema["minLength"]); ok && float64(length) < min {
			problems = append(problems, fmt.Sprintf("%s: string length %d is below %v", path, length, min))
		}
		if max, ok := number(schema["maxLength"]); ok && float64(length) > max {
			problems = append(problems, fmt.Sprintf("%s: string length %d is above %v", path, length, max))
		}
		if pattern, ok := schema["pattern"].(string); ok {
			rx, err := regexp.Compile(pattern)
			if err != nil {
				problems = append(problems, fmt.Sprintf("%s: invalid schema pattern %q", path, pattern))
			} else if !rx.MatchString(typed) {
				problems = append(problems, fmt.Sprintf("%s: string does not match %q", path, pattern))
			}
		}
	case json.Number:
		valueNumber, _ := typed.Float64()
		if min, ok := number(schema["minimum"]); ok && valueNumber < min {
			problems = append(problems, fmt.Sprintf("%s: number %s is below %v", path, typed, min))
		}
		if max, ok := number(schema["maximum"]); ok && valueNumber > max {
			problems = append(problems, fmt.Sprintf("%s: number %s is above %v", path, typed, max))
		}
	}
	return problems
}

func matchesType(schemaType string, value any) bool {
	switch schemaType {
	case "object":
		_, ok := value.(map[string]any)
		return ok
	case "array":
		_, ok := value.([]any)
		return ok
	case "string":
		_, ok := value.(string)
		return ok
	case "boolean":
		_, ok := value.(bool)
		return ok
	case "number":
		_, ok := value.(json.Number)
		return ok
	case "integer":
		n, ok := value.(json.Number)
		if !ok {
			return false
		}
		f, err := strconv.ParseFloat(string(n), 64)
		return err == nil && !math.IsInf(f, 0) && math.Trunc(f) == f
	case "null":
		return value == nil
	default:
		return true
	}
}

func number(value any) (float64, bool) {
	switch n := value.(type) {
	case json.Number:
		v, err := n.Float64()
		return v, err == nil
	case float64:
		return n, true
	case int:
		return float64(n), true
	default:
		return 0, false
	}
}
