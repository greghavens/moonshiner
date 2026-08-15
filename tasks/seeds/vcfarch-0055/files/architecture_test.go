package architecture_test

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/url"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"testing"
	"unicode/utf8"

	architecture "example.com/vcfarchitecture"
)

type estateFixture struct {
	EstateKind           string `json:"estateKind"`
	CompatibilityProfile string `json:"compatibilityProfile"`
	Site                 struct {
		ID           string   `json:"id"`
		SiteCount    int      `json:"siteCount"`
		SddcID       string   `json:"sddcId"`
		DNSSubdomain string   `json:"dnsSubdomain"`
		NameServers  []string `json:"nameServers"`
		NTPServers   []string `json:"ntpServers"`
	} `json:"site"`
	Availability struct {
		HostFailuresToTolerate int `json:"hostFailuresToTolerate"`
		SurvivingHostCount     int `json:"survivingHostCount"`
	} `json:"availability"`
	Capacity struct {
		WorkloadPhysicalCores int     `json:"workloadPhysicalCores"`
		WorkloadMemoryGiB     int     `json:"workloadMemoryGiB"`
		UsableStorageTiB      float64 `json:"usableStorageTiB"`
	} `json:"capacity"`
	Hosts []struct {
		Hostname      string  `json:"hostname"`
		PhysicalCores int     `json:"physicalCores"`
		MemoryGiB     int     `json:"memoryGiB"`
		RawStorageTiB float64 `json:"rawStorageTiB"`
	} `json:"hosts"`
	Storage struct {
		Type          string `json:"type"`
		Architecture  string `json:"architecture"`
		DatastoreName string `json:"datastoreName"`
	} `json:"storage"`
	Cluster struct {
		DatacenterName string `json:"datacenterName"`
		ClusterName    string `json:"clusterName"`
		ResourcePools  []struct {
			Name                        string `json:"name"`
			Type                        string `json:"type"`
			CPUReservationPercentage    int    `json:"cpuReservationPercentage"`
			MemoryReservationPercentage int    `json:"memoryReservationPercentage"`
		} `json:"resourcePools"`
	} `json:"cluster"`
	Appliances struct {
		Vcenter struct {
			Hostname    string `json:"hostname"`
			VMSize      string `json:"vmSize"`
			StorageSize string `json:"storageSize"`
		} `json:"vcenter"`
		SddcManager struct {
			Hostname string `json:"hostname"`
		} `json:"sddcManager"`
		NSX struct {
			ManagerSize      string   `json:"managerSize"`
			ManagerHostnames []string `json:"managerHostnames"`
			VIPFQDN          string   `json:"vipFqdn"`
		} `json:"nsx"`
	} `json:"appliances"`
	InstallerPlaceholders struct {
		VcenterRootPassword     string `json:"vcenterRootPassword"`
		SddcManagerRootPassword string `json:"sddcManagerRootPassword"`
		SddcManagerSSHPassword  string `json:"sddcManagerSshPassword"`
		SddcLocalUserPassword   string `json:"sddcLocalUserPassword"`
		NSXRootPassword         string `json:"nsxRootPassword"`
		NSXAdminPassword        string `json:"nsxAdminPassword"`
		NSXAuditPassword        string `json:"nsxAuditPassword"`
	} `json:"installerPlaceholders"`
	DistributedSwitch struct {
		Name    string `json:"name"`
		MTU     int    `json:"mtu"`
		Uplinks []struct {
			ID     string `json:"id"`
			Uplink string `json:"uplink"`
		} `json:"uplinks"`
	} `json:"distributedSwitch"`
	Networks    []networkRequirement `json:"networks"`
	CEIPEnabled bool                 `json:"ceipEnabled"`
}

type networkRequirement struct {
	NetworkType    string `json:"networkType"`
	VLANID         int    `json:"vlanId"`
	Subnet         string `json:"subnet"`
	Gateway        string `json:"gateway"`
	MTU            int    `json:"mtu"`
	StartIPAddress string `json:"startIpAddress"`
	EndIPAddress   string `json:"endIpAddress"`
}

type compatibilitySnapshot struct {
	InstallerSpec struct {
		Tag    string `json:"tag"`
		Commit string `json:"commit"`
		Path   string `json:"path"`
		SHA256 string `json:"sha256"`
	} `json:"installerSpec"`
	Profiles []compatibilityProfile `json:"profiles"`
}

type compatibilityProfile struct {
	ID                  string            `json:"id"`
	TargetVersion       string            `json:"targetVersion"`
	WorkflowType        string            `json:"workflowType"`
	Topology            string            `json:"topology"`
	MinimumHosts        int               `json:"minimumHosts"`
	MaximumHostFailures int               `json:"maximumHostFailures"`
	Components          map[string]string `json:"components"`
	Storage             struct {
		Type                 string  `json:"type"`
		Architecture         string  `json:"architecture"`
		MinimumHosts         int     `json:"minimumHosts"`
		UsableCapacityFactor float64 `json:"usableCapacityFactor"`
		Supported            bool    `json:"supported"`
	} `json:"storage"`
}

type sddcSpec struct {
	SddcID       string `json:"sddcId"`
	WorkflowType string `json:"workflowType"`
	Version      string `json:"version"`
	HostSpecs    []struct {
		Hostname string `json:"hostname"`
	} `json:"hostSpecs"`
	VcenterSpec struct {
		VcenterHostname       string `json:"vcenterHostname"`
		RootVcenterPassword   string `json:"rootVcenterPassword"`
		VMSize                string `json:"vmSize"`
		StorageSize           string `json:"storageSize"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
		Version               string `json:"version"`
	} `json:"vcenterSpec"`
	ClusterSpec struct {
		DatacenterName    string `json:"datacenterName"`
		ClusterName       string `json:"clusterName"`
		ResourcePoolSpecs []struct {
			Name                        string `json:"name"`
			Type                        string `json:"type"`
			CPUReservationPercentage    int    `json:"cpuReservationPercentage"`
			MemoryReservationPercentage int    `json:"memoryReservationPercentage"`
		} `json:"resourcePoolSpecs"`
	} `json:"clusterSpec"`
	DvsSpecs []struct {
		DVSName         string   `json:"dvsName"`
		Networks        []string `json:"networks"`
		MTU             int      `json:"mtu"`
		VmnicsToUplinks []struct {
			ID     string `json:"id"`
			Uplink string `json:"uplink"`
		} `json:"vmnicsToUplinks"`
	} `json:"dvsSpecs"`
	NsxtSpec struct {
		NSXTManagers []struct {
			Hostname string `json:"hostname"`
		} `json:"nsxtManagers"`
		NSXTManagerSize         string `json:"nsxtManagerSize"`
		VIPFQDN                 string `json:"vipFqdn"`
		RootNSXTManagerPassword string `json:"rootNsxtManagerPassword"`
		NSXTAdminPassword       string `json:"nsxtAdminPassword"`
		NSXTAuditPassword       string `json:"nsxtAuditPassword"`
		TransportVLANID         int    `json:"transportVlanId"`
		UseExistingDeployment   bool   `json:"useExistingDeployment"`
		Version                 string `json:"version"`
	} `json:"nsxtSpec"`
	NetworkSpecs []struct {
		NetworkType            string `json:"networkType"`
		VLANID                 int    `json:"vlanId"`
		Subnet                 string `json:"subnet"`
		Gateway                string `json:"gateway"`
		MTU                    int    `json:"mtu"`
		IncludeIPAddressRanges []struct {
			StartIPAddress string `json:"startIpAddress"`
			EndIPAddress   string `json:"endIpAddress"`
		} `json:"includeIpAddressRanges"`
	} `json:"networkSpecs"`
	DNSSpec struct {
		Subdomain   string   `json:"subdomain"`
		Nameservers []string `json:"nameservers"`
	} `json:"dnsSpec"`
	NTPServers      []string `json:"ntpServers"`
	SddcManagerSpec struct {
		Hostname              string `json:"hostname"`
		RootPassword          string `json:"rootPassword"`
		SSHPassword           string `json:"sshPassword"`
		LocalUserPassword     string `json:"localUserPassword"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
		Version               string `json:"version"`
	} `json:"sddcManagerSpec"`
	DatastoreSpec struct {
		VsanSpec struct {
			DatastoreName      string `json:"datastoreName"`
			FailuresToTolerate int    `json:"failuresToTolerate"`
			ESAConfig          struct {
				Enabled bool `json:"enabled"`
			} `json:"esaConfig"`
		} `json:"vsanSpec"`
	} `json:"datastoreSpec"`
	CEIPEnabled bool `json:"ceipEnabled"`
}

func TestBuildArchitecture(t *testing.T) {
	estateJSON := mustRead(t, "fixtures/estate.json")
	compatibilityJSON := mustRead(t, "fixtures/compatibility-snapshot.json")
	artifactJSON, err := architecture.Build(estateJSON, compatibilityJSON)
	if err != nil {
		t.Fatalf("Build returned an error: %v", err)
	}

	// The installer schema is deliberately the first acceptance check. No
	// fixture or compatibility semantics are examined until this succeeds.
	schemaJSON := mustRead(t, "specifications/vcf-installer/vcf-installer-openapi.json")
	schemaDocument := decodeJSON(t, schemaJSON, "installer OpenAPI document")
	artifactDocument := decodeJSON(t, artifactJSON, "Build artifact")
	schema, err := schemaAt(schemaDocument, "#/components/schemas/SddcSpec")
	if err != nil {
		t.Fatalf("load SddcSpec from installer OpenAPI document: %v", err)
	}
	if validationErrors := validateSchema(schemaDocument, schema, artifactDocument, "$", nil); len(validationErrors) > 0 {
		t.Fatalf("artifact does not validate as installer SddcSpec:\n  %s", strings.Join(validationErrors, "\n  "))
	}

	var estate estateFixture
	mustUnmarshal(t, estateJSON, &estate, "estate fixture")
	var snapshot compatibilitySnapshot
	mustUnmarshal(t, compatibilityJSON, &snapshot, "compatibility snapshot")
	var artifact sddcSpec
	mustUnmarshal(t, artifactJSON, &artifact, "SddcSpec artifact")
	profile := selectedProfile(t, snapshot, estate.CompatibilityProfile)

	checks := []struct {
		name  string
		check func(*testing.T)
	}{
		{"pinned installer specification", func(t *testing.T) {
			digest := sha256.Sum256(schemaJSON)
			if got := hex.EncodeToString(digest[:]); got != snapshot.InstallerSpec.SHA256 {
				t.Errorf("installer schema SHA-256 = %s, snapshot pins %s", got, snapshot.InstallerSpec.SHA256)
			}
			if snapshot.InstallerSpec.Tag != "9.0.0.0" || snapshot.InstallerSpec.Commit == "" {
				t.Errorf("installer spec is not pinned to tag 9.0.0.0 and a commit")
			}
		}},
		{"greenfield consolidated identity", func(t *testing.T) {
			if estate.EstateKind != "greenfield" || estate.Site.SiteCount != 1 {
				t.Fatalf("protected fixture is not the required one-site greenfield estate")
			}
			if profile.Topology != "single-site-consolidated" {
				t.Fatalf("selected profile topology = %q", profile.Topology)
			}
			if artifact.SddcID != estate.Site.SddcID || artifact.WorkflowType != profile.WorkflowType || artifact.Version != profile.TargetVersion {
				t.Errorf("identity/version = (%q, %q, %q), want (%q, %q, %q)", artifact.SddcID, artifact.WorkflowType, artifact.Version, estate.Site.SddcID, profile.WorkflowType, profile.TargetVersion)
			}
		}},
		{"minimum supported inventoried hosts", func(t *testing.T) {
			if len(artifact.HostSpecs) != profile.MinimumHosts || len(artifact.HostSpecs) != profile.Storage.MinimumHosts {
				t.Fatalf("host count = %d, management/storage minimum = %d/%d", len(artifact.HostSpecs), profile.MinimumHosts, profile.Storage.MinimumHosts)
			}
			want := make([]string, 0, len(estate.Hosts))
			for _, host := range estate.Hosts {
				want = append(want, host.Hostname)
			}
			got := make([]string, 0, len(artifact.HostSpecs))
			for _, host := range artifact.HostSpecs {
				got = append(got, host.Hostname)
			}
			sort.Strings(want)
			sort.Strings(got)
			if !reflect.DeepEqual(got, want) {
				t.Errorf("selected hosts = %v, want inventory %v", got, want)
			}
		}},
		{"N-1 capacity", func(t *testing.T) {
			if estate.Availability.HostFailuresToTolerate != profile.MaximumHostFailures {
				t.Fatalf("fixture FTT %d differs from profile maximum %d", estate.Availability.HostFailuresToTolerate, profile.MaximumHostFailures)
			}
			selected := map[string]bool{}
			for _, host := range artifact.HostSpecs {
				selected[host.Hostname] = true
			}
			cores, memory, raw := 0, 0, float64(0)
			maxCores, maxMemory, maxRaw := 0, 0, float64(0)
			for _, host := range estate.Hosts {
				if !selected[host.Hostname] {
					continue
				}
				cores += host.PhysicalCores
				memory += host.MemoryGiB
				raw += host.RawStorageTiB
				if host.PhysicalCores > maxCores {
					maxCores = host.PhysicalCores
				}
				if host.MemoryGiB > maxMemory {
					maxMemory = host.MemoryGiB
				}
				if host.RawStorageTiB > maxRaw {
					maxRaw = host.RawStorageTiB
				}
			}
			if len(selected)-estate.Availability.HostFailuresToTolerate != estate.Availability.SurvivingHostCount {
				t.Errorf("surviving host count does not meet fixture")
			}
			managementCPUReservation, managementMemoryReservation := 0, 0
			for _, pool := range estate.Cluster.ResourcePools {
				if pool.Type == "management" {
					managementCPUReservation = pool.CPUReservationPercentage
					managementMemoryReservation = pool.MemoryReservationPercentage
				}
			}
			workloadCores := int(float64(cores-maxCores) * (1 - float64(managementCPUReservation)/100))
			workloadMemory := int(float64(memory-maxMemory) * (1 - float64(managementMemoryReservation)/100))
			if workloadCores < estate.Capacity.WorkloadPhysicalCores {
				t.Errorf("N-1 workload cores after management reservation = %d, need %d", workloadCores, estate.Capacity.WorkloadPhysicalCores)
			}
			if workloadMemory < estate.Capacity.WorkloadMemoryGiB {
				t.Errorf("N-1 workload memory after management reservation = %d GiB, need %d", workloadMemory, estate.Capacity.WorkloadMemoryGiB)
			}
			usable := (raw - maxRaw) * profile.Storage.UsableCapacityFactor
			if usable+1e-9 < estate.Capacity.UsableStorageTiB {
				t.Errorf("N-1 usable storage = %.1f TiB, need %.1f", usable, estate.Capacity.UsableStorageTiB)
			}
		}},
		{"vSAN ESA availability", func(t *testing.T) {
			vsan := artifact.DatastoreSpec.VsanSpec
			if !profile.Storage.Supported || estate.Storage.Type != profile.Storage.Type || estate.Storage.Architecture != profile.Storage.Architecture {
				t.Fatalf("fixture requests storage outside selected compatibility profile")
			}
			if !vsan.ESAConfig.Enabled || vsan.DatastoreName != estate.Storage.DatastoreName || vsan.FailuresToTolerate != estate.Availability.HostFailuresToTolerate {
				t.Errorf("vSAN design = %+v, want ESA datastore %q FTT %d", vsan, estate.Storage.DatastoreName, estate.Availability.HostFailuresToTolerate)
			}
		}},
		{"consolidated resource pools", func(t *testing.T) {
			if artifact.ClusterSpec.DatacenterName != estate.Cluster.DatacenterName || artifact.ClusterSpec.ClusterName != estate.Cluster.ClusterName {
				t.Errorf("cluster placement = %q/%q, want %q/%q", artifact.ClusterSpec.DatacenterName, artifact.ClusterSpec.ClusterName, estate.Cluster.DatacenterName, estate.Cluster.ClusterName)
			}
			got := map[string][3]any{}
			for _, pool := range artifact.ClusterSpec.ResourcePoolSpecs {
				if _, exists := got[pool.Type]; exists {
					t.Errorf("duplicate resource pool type %q", pool.Type)
				}
				got[pool.Type] = [3]any{pool.Name, pool.CPUReservationPercentage, pool.MemoryReservationPercentage}
			}
			if len(artifact.ClusterSpec.ResourcePoolSpecs) != len(estate.Cluster.ResourcePools) {
				t.Errorf("resource pool count = %d, want %d", len(artifact.ClusterSpec.ResourcePoolSpecs), len(estate.Cluster.ResourcePools))
			}
			for _, want := range estate.Cluster.ResourcePools {
				if value, ok := got[want.Type]; !ok || value != [3]any{want.Name, want.CPUReservationPercentage, want.MemoryReservationPercentage} {
					t.Errorf("resource pool %q = %v, want (%q, %d, %d)", want.Type, value, want.Name, want.CPUReservationPercentage, want.MemoryReservationPercentage)
				}
			}
		}},
		{"DNS and NTP", func(t *testing.T) {
			if artifact.DNSSpec.Subdomain != estate.Site.DNSSubdomain || !sameStrings(artifact.DNSSpec.Nameservers, estate.Site.NameServers) || !sameStrings(artifact.NTPServers, estate.Site.NTPServers) {
				t.Errorf("DNS/NTP does not match site requirements")
			}
		}},
		{"required networks and redundant uplinks", func(t *testing.T) {
			got := map[string]networkRequirement{}
			for _, network := range artifact.NetworkSpecs {
				if _, exists := got[network.NetworkType]; exists {
					t.Errorf("duplicate network type %q", network.NetworkType)
				}
				requirement := networkRequirement{NetworkType: network.NetworkType, VLANID: network.VLANID, Subnet: network.Subnet, Gateway: network.Gateway, MTU: network.MTU}
				if len(network.IncludeIPAddressRanges) == 1 {
					requirement.StartIPAddress = network.IncludeIPAddressRanges[0].StartIPAddress
					requirement.EndIPAddress = network.IncludeIPAddressRanges[0].EndIPAddress
				}
				got[network.NetworkType] = requirement
			}
			for _, want := range estate.Networks {
				if got[want.NetworkType] != want {
					t.Errorf("network %s = %+v, want %+v", want.NetworkType, got[want.NetworkType], want)
				}
			}
			if len(artifact.NetworkSpecs) != len(estate.Networks) {
				t.Errorf("network count = %d, want %d", len(artifact.NetworkSpecs), len(estate.Networks))
			}
			if len(artifact.DvsSpecs) != 1 {
				t.Fatalf("DVS count = %d, want 1", len(artifact.DvsSpecs))
			}
			dvs := artifact.DvsSpecs[0]
			if dvs.DVSName != estate.DistributedSwitch.Name || dvs.MTU != estate.DistributedSwitch.MTU {
				t.Errorf("DVS identity/MTU does not match fixture")
			}
			if len(dvs.VmnicsToUplinks) != len(estate.DistributedSwitch.Uplinks) {
				t.Fatalf("DVS uplink count = %d, want %d", len(dvs.VmnicsToUplinks), len(estate.DistributedSwitch.Uplinks))
			}
			gotUplinks := map[string]string{}
			for _, uplink := range dvs.VmnicsToUplinks {
				if _, exists := gotUplinks[uplink.ID]; exists {
					t.Errorf("duplicate DVS vmnic %q", uplink.ID)
				}
				gotUplinks[uplink.ID] = uplink.Uplink
			}
			for _, want := range estate.DistributedSwitch.Uplinks {
				if got := gotUplinks[want.ID]; got != want.Uplink {
					t.Errorf("DVS uplink for %s = %q, want %q", want.ID, got, want.Uplink)
				}
			}
			wantTypes, gotTypes := []string{}, append([]string(nil), dvs.Networks...)
			for _, network := range estate.Networks {
				wantTypes = append(wantTypes, network.NetworkType)
			}
			sort.Strings(wantTypes)
			sort.Strings(gotTypes)
			if !reflect.DeepEqual(gotTypes, wantTypes) {
				t.Errorf("DVS networks = %v, want %v", gotTypes, wantTypes)
			}
		}},
		{"appliances and pinned component combination", func(t *testing.T) {
			vc := artifact.VcenterSpec
			if vc.UseExistingDeployment || vc.VcenterHostname != estate.Appliances.Vcenter.Hostname || vc.RootVcenterPassword != estate.InstallerPlaceholders.VcenterRootPassword || vc.VMSize != estate.Appliances.Vcenter.VMSize || vc.StorageSize != estate.Appliances.Vcenter.StorageSize || vc.Version != profile.Components["VCENTER"] {
				t.Errorf("vCenter design is not the required new pinned deployment")
			}
			sddc := artifact.SddcManagerSpec
			if sddc.UseExistingDeployment || sddc.Hostname != estate.Appliances.SddcManager.Hostname || sddc.RootPassword != estate.InstallerPlaceholders.SddcManagerRootPassword || sddc.SSHPassword != estate.InstallerPlaceholders.SddcManagerSSHPassword || sddc.LocalUserPassword != estate.InstallerPlaceholders.SddcLocalUserPassword || sddc.Version != profile.Components["SDDC_MANAGER"] {
				t.Errorf("SDDC Manager design is not the required new pinned deployment")
			}
			nsx := artifact.NsxtSpec
			if nsx.UseExistingDeployment || nsx.NSXTManagerSize != estate.Appliances.NSX.ManagerSize || nsx.VIPFQDN != estate.Appliances.NSX.VIPFQDN || nsx.RootNSXTManagerPassword != estate.InstallerPlaceholders.NSXRootPassword || nsx.NSXTAdminPassword != estate.InstallerPlaceholders.NSXAdminPassword || nsx.NSXTAuditPassword != estate.InstallerPlaceholders.NSXAuditPassword || nsx.Version != profile.Components["NSX"] {
				t.Errorf("NSX design is not the required new pinned deployment")
			}
			gotManagers := []string{}
			for _, manager := range nsx.NSXTManagers {
				gotManagers = append(gotManagers, manager.Hostname)
			}
			if !sameStrings(gotManagers, estate.Appliances.NSX.ManagerHostnames) {
				t.Errorf("NSX managers = %v, want %v", gotManagers, estate.Appliances.NSX.ManagerHostnames)
			}
			for _, network := range estate.Networks {
				if network.NetworkType == "HOST_TEP" && nsx.TransportVLANID != network.VLANID {
					t.Errorf("NSX transport VLAN = %d, want %d", nsx.TransportVLANID, network.VLANID)
				}
			}
		}},
		{"CEIP preference", func(t *testing.T) {
			if artifact.CEIPEnabled != estate.CEIPEnabled {
				t.Errorf("ceipEnabled = %v, want %v", artifact.CEIPEnabled, estate.CEIPEnabled)
			}
		}},
	}

	for _, check := range checks {
		t.Run(check.name, check.check)
	}
}

func TestResearchRecord(t *testing.T) {
	reportBytes := mustRead(t, "research.md")
	if !utf8.Valid(reportBytes) {
		t.Fatal("research.md is not valid UTF-8")
	}
	report := string(reportBytes)
	lower := strings.ToLower(report)
	for _, required := range []string{"broadcom", "compatib", "upgrade"} {
		if !strings.Contains(lower, required) {
			t.Errorf("research.md does not discuss %s", required)
		}
	}
	if strings.Contains(lower, ".invalid") {
		t.Error("research.md contains a non-reachable .invalid URL")
	}
	if !regexp.MustCompile(`\b20[0-9]{2}-[01][0-9]-[0-3][0-9]\b`).MatchString(report) {
		t.Error("research.md does not record an ISO-format access date")
	}
	urls := map[string]bool{}
	for _, match := range regexp.MustCompile(`https://[^\s)>]+`).FindAllString(report, -1) {
		match = strings.TrimRight(match, ".,;:")
		parsed, err := url.Parse(match)
		if err != nil {
			continue
		}
		hostname := strings.ToLower(parsed.Hostname())
		if hostname == "broadcom.com" || strings.HasSuffix(hostname, ".broadcom.com") {
			urls[match] = true
		}
	}
	if len(urls) < 2 {
		t.Errorf("research.md records %d distinct Broadcom source URLs, want at least 2", len(urls))
	}
}

func selectedProfile(t *testing.T, snapshot compatibilitySnapshot, id string) compatibilityProfile {
	t.Helper()
	for _, profile := range snapshot.Profiles {
		if profile.ID == id {
			return profile
		}
	}
	t.Fatalf("compatibility profile %q is absent from snapshot", id)
	return compatibilityProfile{}
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return data
}

func mustUnmarshal(t *testing.T, data []byte, target any, name string) {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(data))
	if err := decoder.Decode(target); err != nil {
		t.Fatalf("decode %s: %v", name, err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		t.Fatalf("decode %s: %v", name, err)
	}
}

func decodeJSON(t *testing.T, data []byte, name string) any {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		t.Fatalf("decode %s: %v", name, err)
	}
	if err := requireJSONEOF(decoder); err != nil {
		t.Fatalf("decode %s: %v", name, err)
	}
	return value
}

func requireJSONEOF(decoder *json.Decoder) error {
	var trailing any
	err := decoder.Decode(&trailing)
	if err == io.EOF {
		return nil
	}
	if err == nil {
		return fmt.Errorf("trailing JSON value")
	}
	return fmt.Errorf("trailing content: %w", err)
}

func sameStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	leftCopy := append([]string(nil), left...)
	rightCopy := append([]string(nil), right...)
	sort.Strings(leftCopy)
	sort.Strings(rightCopy)
	return reflect.DeepEqual(leftCopy, rightCopy)
}

func schemaAt(document any, pointer string) (map[string]any, error) {
	value, err := resolvePointer(document, pointer)
	if err != nil {
		return nil, err
	}
	schema, ok := value.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%s is %T, not a schema object", pointer, value)
	}
	return schema, nil
}

func resolvePointer(document any, pointer string) (any, error) {
	if !strings.HasPrefix(pointer, "#/") {
		return nil, fmt.Errorf("unsupported non-local reference %q", pointer)
	}
	current := document
	for _, token := range strings.Split(strings.TrimPrefix(pointer, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("reference %q traverses non-object at %q", pointer, token)
		}
		current, ok = object[token]
		if !ok {
			return nil, fmt.Errorf("reference %q has no token %q", pointer, token)
		}
	}
	return current, nil
}

// validateSchema evaluates the structural OpenAPI 3.0 schema keywords used by
// the pinned installer document. References are always resolved from that same
// document, so SddcSpec and every nested component use the vendor's schemas.
func validateSchema(document any, schema map[string]any, value any, path string, seen map[string]bool) []string {
	if seen == nil {
		seen = map[string]bool{}
	}
	errors := []string{}
	if reference, ok := schema["$ref"].(string); ok {
		resolved, err := schemaAt(document, reference)
		if err != nil {
			return []string{fmt.Sprintf("%s: %v", path, err)}
		}
		key := reference + "@" + path
		if seen[key] {
			return nil
		}
		nextSeen := cloneSeen(seen)
		nextSeen[key] = true
		errors = append(errors, validateSchema(document, resolved, value, path, nextSeen)...)
	}
	if value == nil {
		if nullable, _ := schema["nullable"].(bool); nullable {
			return errors
		}
		if schema["type"] != nil {
			return append(errors, path+": null is not allowed")
		}
		return errors
	}
	for _, keyword := range []string{"allOf", "anyOf", "oneOf"} {
		alternatives, ok := schema[keyword].([]any)
		if !ok {
			continue
		}
		passes := 0
		for _, alternative := range alternatives {
			candidate, ok := alternative.(map[string]any)
			if !ok {
				continue
			}
			if len(validateSchema(document, candidate, value, path, cloneSeen(seen))) == 0 {
				passes++
			}
		}
		switch keyword {
		case "allOf":
			if passes != len(alternatives) {
				errors = append(errors, fmt.Sprintf("%s: matches %d/%d allOf schemas", path, passes, len(alternatives)))
			}
		case "anyOf":
			if passes == 0 {
				errors = append(errors, path+": matches no anyOf schema")
			}
		case "oneOf":
			if passes != 1 {
				errors = append(errors, fmt.Sprintf("%s: matches %d oneOf schemas", path, passes))
			}
		}
	}
	if enum, ok := schema["enum"].([]any); ok {
		matched := false
		for _, allowed := range enum {
			if reflect.DeepEqual(allowed, value) {
				matched = true
				break
			}
		}
		if !matched {
			errors = append(errors, fmt.Sprintf("%s: %v is not in enum", path, value))
		}
	}
	typeName, _ := schema["type"].(string)
	switch typeName {
	case "object":
		object, ok := value.(map[string]any)
		if !ok {
			return append(errors, fmt.Sprintf("%s: expected object, got %T", path, value))
		}
		if required, ok := schema["required"].([]any); ok {
			for _, item := range required {
				name, _ := item.(string)
				if _, exists := object[name]; !exists {
					errors = append(errors, fmt.Sprintf("%s: missing required property %q", path, name))
				}
			}
		}
		properties, _ := schema["properties"].(map[string]any)
		for name, propertyValue := range object {
			propertySchemaValue, known := properties[name]
			if known {
				propertySchema, ok := propertySchemaValue.(map[string]any)
				if !ok {
					errors = append(errors, fmt.Sprintf("%s.%s: invalid property schema", path, name))
					continue
				}
				errors = append(errors, validateSchema(document, propertySchema, propertyValue, path+"."+name, cloneSeen(seen))...)
				continue
			}
			if additional, ok := schema["additionalProperties"].(bool); ok && !additional {
				errors = append(errors, fmt.Sprintf("%s: additional property %q is forbidden", path, name))
			}
			if additional, ok := schema["additionalProperties"].(map[string]any); ok {
				errors = append(errors, validateSchema(document, additional, propertyValue, path+"."+name, cloneSeen(seen))...)
			}
		}
	case "array":
		array, ok := value.([]any)
		if !ok {
			return append(errors, fmt.Sprintf("%s: expected array, got %T", path, value))
		}
		if minimum, ok := integerKeyword(schema, "minItems"); ok && len(array) < minimum {
			errors = append(errors, fmt.Sprintf("%s: has %d items, minimum %d", path, len(array), minimum))
		}
		if maximum, ok := integerKeyword(schema, "maxItems"); ok && len(array) > maximum {
			errors = append(errors, fmt.Sprintf("%s: has %d items, maximum %d", path, len(array), maximum))
		}
		if itemSchema, ok := schema["items"].(map[string]any); ok {
			for index, item := range array {
				errors = append(errors, validateSchema(document, itemSchema, item, fmt.Sprintf("%s[%d]", path, index), cloneSeen(seen))...)
			}
		}
	case "string":
		text, ok := value.(string)
		if !ok {
			return append(errors, fmt.Sprintf("%s: expected string, got %T", path, value))
		}
		length := utf8.RuneCountInString(text)
		if minimum, ok := integerKeyword(schema, "minLength"); ok && length < minimum {
			errors = append(errors, fmt.Sprintf("%s: length %d is below %d", path, length, minimum))
		}
		if maximum, ok := integerKeyword(schema, "maxLength"); ok && length > maximum {
			errors = append(errors, fmt.Sprintf("%s: length %d exceeds %d", path, length, maximum))
		}
		if pattern, ok := schema["pattern"].(string); ok {
			expression, err := regexp.Compile(pattern)
			if err != nil {
				errors = append(errors, fmt.Sprintf("%s: invalid schema pattern %q: %v", path, pattern, err))
			} else if !expression.MatchString(text) {
				errors = append(errors, fmt.Sprintf("%s: %q does not match %q", path, text, pattern))
			}
		}
	case "integer":
		number, ok := jsonNumeric(value)
		if !ok || math.Trunc(number) != number {
			return append(errors, fmt.Sprintf("%s: expected integer, got %v", path, value))
		}
		errors = append(errors, validateNumberBounds(schema, number, path)...)
	case "number":
		number, ok := jsonNumeric(value)
		if !ok {
			return append(errors, fmt.Sprintf("%s: expected number, got %T", path, value))
		}
		errors = append(errors, validateNumberBounds(schema, number, path)...)
	case "boolean":
		if _, ok := value.(bool); !ok {
			errors = append(errors, fmt.Sprintf("%s: expected boolean, got %T", path, value))
		}
	}
	return errors
}

func validateNumberBounds(schema map[string]any, number float64, path string) []string {
	errors := []string{}
	if minimum, ok := numericKeyword(schema, "minimum"); ok && number < minimum {
		errors = append(errors, fmt.Sprintf("%s: %g is below %g", path, number, minimum))
	}
	if maximum, ok := numericKeyword(schema, "maximum"); ok && number > maximum {
		errors = append(errors, fmt.Sprintf("%s: %g exceeds %g", path, number, maximum))
	}
	return errors
}

func integerKeyword(schema map[string]any, name string) (int, bool) {
	value, ok := numericKeyword(schema, name)
	return int(value), ok
}

func numericKeyword(schema map[string]any, name string) (float64, bool) {
	value, exists := schema[name]
	if !exists {
		return 0, false
	}
	return jsonNumeric(value)
}

func jsonNumeric(value any) (float64, bool) {
	switch number := value.(type) {
	case json.Number:
		parsed, err := strconv.ParseFloat(string(number), 64)
		return parsed, err == nil
	case float64:
		return number, true
	case float32:
		return float64(number), true
	case int:
		return float64(number), true
	case int64:
		return float64(number), true
	default:
		return 0, false
	}
}

func cloneSeen(source map[string]bool) map[string]bool {
	copy := make(map[string]bool, len(source))
	for key, value := range source {
		copy[key] = value
	}
	return copy
}
