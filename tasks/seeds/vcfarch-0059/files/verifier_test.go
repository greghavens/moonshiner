package vcfarch

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net"
	"net/url"
	"os"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"
	"unicode/utf8"
)

const (
	installerPath = "specifications/vcf-installer/vcf-installer-openapi.json"
	artifactPath  = "architecture.json"

	// These hashes make the local inputs immutable grading authorities. The
	// artifact is still validated against the schema loaded from installerPath
	// before any hash or architecture-specific assertion is evaluated.
	installerSHA          = "a2084a65aab0ac0a5a1625d1a2fdf20b55fc8895ca43fd4389da901d07a4aaef"
	requirementsSHA       = "1ccdc84a07fbb792d10c1f1567c0decb144887ad96f2828827e283dee04c15dc"
	estateSHA             = "5160b33a79a49890920e0ca5184324c0e616c05d6dfa8bc5e65645fbbd1485a6"
	compatibilitySHA      = "6006acf51adcc4a11becf54c9f79a3ba6342cd7dc43f1dbe4c772e1785dd6aa9"
	architectureSchemaSHA = "602896848da2c32b68caba50ef900bbc29900b6d9c1cd9c6fb3d956a8f91ca8e"
)

type requirements struct {
	TargetVersion string `json:"targetVersion"`
	Instance      struct {
		ID               string   `json:"id"`
		Name             string   `json:"name"`
		DNSSubdomain     string   `json:"dnsSubdomain"`
		ManagementSiteID string   `json:"managementSiteId"`
		ManagementHosts  []string `json:"managementHosts"`
		VcenterFQDN      string   `json:"vcenterFqdn"`
		SddcManagerFQDN  string   `json:"sddcManagerFqdn"`
		NsxManagerFQDNs  []string `json:"nsxManagerFqdns"`
		NsxVIPFQDN       string   `json:"nsxVipFqdn"`
		DNSServers       []string `json:"dnsServers"`
		NTPServers       []string `json:"ntpServers"`
	} `json:"instance"`
	Sites        []site `json:"sites"`
	HostProfiles map[string]struct {
		PhysicalCores int     `json:"physicalCores"`
		MemoryGiB     int     `json:"memoryGiB"`
		RawStorageTiB float64 `json:"rawStorageTiB"`
	} `json:"hostProfiles"`
	SizingRules struct {
		ManagementHostCount        int     `json:"managementHostCount"`
		WorkloadMaxHostsPerCluster int     `json:"workloadMaxHostsPerCluster"`
		ReservedHostsPerCluster    int     `json:"reservedHostsPerCluster"`
		MaxVcpuPerPhysicalCore     int     `json:"maxVcpuPerPhysicalCore"`
		StorageDataEfficiency      float64 `json:"storageDataEfficiency"`
		StorageFreeFraction        float64 `json:"storageFreeFraction"`
	} `json:"sizingRules"`
	Availability          availability `json:"availability"`
	ValidationCredentials struct {
		EsxiUsername            string `json:"esxiUsername"`
		EsxiPassword            string `json:"esxiPassword"`
		VcenterRootPassword     string `json:"vcenterRootPassword"`
		VcenterSsoPassword      string `json:"vcenterSsoPassword"`
		SddcManagerRootPassword string `json:"sddcManagerRootPassword"`
		SddcManagerSshPassword  string `json:"sddcManagerSshPassword"`
		SddcLocalUserPassword   string `json:"sddcLocalUserPassword"`
		NsxRootPassword         string `json:"nsxRootPassword"`
		NsxAdminPassword        string `json:"nsxAdminPassword"`
		NsxAuditPassword        string `json:"nsxAuditPassword"`
	} `json:"validationCredentials"`
}

type site struct {
	ID       string `json:"id"`
	Role     string `json:"role"`
	City     string `json:"city,omitempty"`
	Workload struct {
		VCPUs            int     `json:"vcpus"`
		MemoryGiB        int     `json:"memoryGiB"`
		UsableStorageTiB float64 `json:"usableStorageTiB"`
	} `json:"workload,omitempty"`
	Networks []network `json:"networks"`
}

type network struct {
	Type    string `json:"type"`
	VLANID  int    `json:"vlanId"`
	CIDR    string `json:"cidr"`
	Gateway string `json:"gateway"`
	MTU     int    `json:"mtu"`
}

type availability struct {
	ClusterPolicy      string `json:"clusterPolicy"`
	RecoveryRPOMinutes int    `json:"recoveryRpoMinutes"`
	RecoveryRTOMinutes int    `json:"recoveryRtoMinutes"`
}

type estate struct {
	EstateID   string `json:"estateId"`
	Components []struct {
		ID      string `json:"id"`
		Name    string `json:"name"`
		Version string `json:"version"`
	} `json:"components"`
}

type compatibilitySnapshot struct {
	TargetRelease string              `json:"targetRelease"`
	Plan          []compatibilityStep `json:"plan"`
}

type compatibilityStep struct {
	Order           int      `json:"order"`
	ComponentID     string   `json:"componentId"`
	FromVersion     string   `json:"fromVersion"`
	Action          string   `json:"action"`
	TargetComponent string   `json:"targetComponent"`
	TargetVersion   string   `json:"targetVersion"`
	Gates           []string `json:"gates"`
}

type architecture struct {
	SchemaVersion string           `json:"schemaVersion"`
	TargetVersion string           `json:"targetVersion"`
	SddcSpec      sddcSpec         `json:"sddcSpec"`
	Sites         []site           `json:"sites"`
	Clusters      []cluster        `json:"clusters"`
	Availability  availability     `json:"availability"`
	MigrationPlan []migrationStep  `json:"migrationPlan"`
	Research      []researchSource `json:"research"`
}

type researchSource struct {
	Title        string   `json:"title"`
	URL          string   `json:"url"`
	AccessedOn   string   `json:"accessedOn"`
	ComponentIDs []string `json:"componentIds"`
}

type cluster struct {
	Name                string   `json:"name"`
	SiteID              string   `json:"siteId"`
	Role                string   `json:"role"`
	HostProfile         string   `json:"hostProfile"`
	HostCount           int      `json:"hostCount"`
	FailureReserveHosts int      `json:"failureReserveHosts"`
	UsableCapacity      capacity `json:"usableCapacity"`
}

type capacity struct {
	VCPUs      int     `json:"vcpus"`
	MemoryGiB  int     `json:"memoryGiB"`
	StorageTiB float64 `json:"storageTiB"`
}

type migrationStep struct {
	Order           int      `json:"order"`
	ComponentID     string   `json:"componentId"`
	Component       string   `json:"component"`
	FromVersion     string   `json:"fromVersion"`
	Action          string   `json:"action"`
	TargetComponent string   `json:"targetComponent"`
	TargetVersion   string   `json:"targetVersion"`
	Gates           []string `json:"gates"`
}

type sddcSpec struct {
	SddcID          string `json:"sddcId"`
	WorkflowType    string `json:"workflowType"`
	Version         string `json:"version"`
	VCFInstanceName string `json:"vcfInstanceName"`
	HostSpecs       []struct {
		Hostname    string `json:"hostname"`
		Credentials struct {
			Username string `json:"username"`
			Password string `json:"password"`
		} `json:"credentials"`
	} `json:"hostSpecs"`
	VcenterSpec struct {
		VcenterHostname       string `json:"vcenterHostname"`
		RootVcenterPassword   string `json:"rootVcenterPassword"`
		AdminUserSsoPassword  string `json:"adminUserSsoPassword"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
		Version               string `json:"version"`
	} `json:"vcenterSpec"`
	ClusterSpec struct {
		DatacenterName string `json:"datacenterName"`
		ClusterName    string `json:"clusterName"`
	} `json:"clusterSpec"`
	DvsSpecs []struct {
		DvsName         string   `json:"dvsName"`
		Networks        []string `json:"networks"`
		MTU             int      `json:"mtu"`
		VmnicsToUplinks []struct {
			ID     string `json:"id"`
			Uplink string `json:"uplink"`
		} `json:"vmnicsToUplinks"`
	} `json:"dvsSpecs"`
	NetworkSpecs []struct {
		NetworkType string `json:"networkType"`
		Subnet      string `json:"subnet"`
		Gateway     string `json:"gateway"`
		VLANID      int    `json:"vlanId"`
		MTU         int    `json:"mtu"`
	} `json:"networkSpecs"`
	DnsSpec struct {
		Subdomain   string   `json:"subdomain"`
		Nameservers []string `json:"nameservers"`
	} `json:"dnsSpec"`
	NTPServers      []string `json:"ntpServers"`
	SddcManagerSpec struct {
		Hostname              string `json:"hostname"`
		RootPassword          string `json:"rootPassword"`
		SshPassword           string `json:"sshPassword"`
		LocalUserPassword     string `json:"localUserPassword"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
		Version               string `json:"version"`
	} `json:"sddcManagerSpec"`
	NsxtSpec struct {
		NsxtManagers []struct {
			Hostname string `json:"hostname"`
		} `json:"nsxtManagers"`
		VIPFQDN                 string `json:"vipFqdn"`
		RootNsxtManagerPassword string `json:"rootNsxtManagerPassword"`
		NsxtAdminPassword       string `json:"nsxtAdminPassword"`
		NsxtAuditPassword       string `json:"nsxtAuditPassword"`
		TransportVLANID         int    `json:"transportVlanId"`
		UseExistingDeployment   bool   `json:"useExistingDeployment"`
		Version                 string `json:"version"`
	} `json:"nsxtSpec"`
	DatastoreSpec struct {
		VsanSpec struct {
			DatastoreName      string `json:"datastoreName"`
			FailuresToTolerate int    `json:"failuresToTolerate"`
		} `json:"vsanSpec"`
	} `json:"datastoreSpec"`
	ManagementPoolName          string `json:"managementPoolName"`
	CEIPEnabled                 bool   `json:"ceipEnabled"`
	SkipEsxThumbprintValidation bool   `json:"skipEsxThumbprintValidation"`
	SkipGatewayPingValidation   bool   `json:"skipGatewayPingValidation"`
}

func TestArchitecture(t *testing.T) {
	artifactBytes, err := os.ReadFile(artifactPath)
	if err != nil {
		t.Fatalf("read %s: %v", artifactPath, err)
	}

	// Required first validation stage: extract sddcSpec and validate it against
	// the SddcSpec definition in the installer OpenAPI document. No fixture,
	// compatibility, topology, capacity, or migration assertion precedes this.
	var rawArtifact map[string]json.RawMessage
	mustUnmarshal(t, artifactBytes, &rawArtifact)
	rawSddc, ok := rawArtifact["sddcSpec"]
	if !ok {
		t.Fatal("installer schema validation: missing sddcSpec")
	}
	installerBytes := mustRead(t, installerPath)
	installerDoc := decodeJSON(t, installerBytes)
	sddcValue := decodeJSON(t, rawSddc)
	rootSchema, err := resolvePointer(installerDoc, "#/components/schemas/SddcSpec")
	if err != nil {
		t.Fatalf("installer schema validation: %v", err)
	}
	if err := validateJSON(installerDoc, rootSchema, sddcValue, "$.sddcSpec"); err != nil {
		t.Fatalf("installer schema validation: %v", err)
	}

	// The seed's fixed artifact schema is evaluated only after SddcSpec passes.
	architectureSchemaBytes := mustRead(t, "schema/architecture.schema.json")
	architectureSchema := decodeJSON(t, architectureSchemaBytes)
	if err := validateJSON(architectureSchema, architectureSchema, decodeJSON(t, artifactBytes), "$"); err != nil {
		t.Fatalf("architecture schema validation: %v", err)
	}

	inputChecks := []struct {
		name string
		path string
		want string
	}{
		{"installer OpenAPI", installerPath, installerSHA},
		{"requirements", "fixtures/requirements.json", requirementsSHA},
		{"estate", "fixtures/estate.json", estateSHA},
		{"compatibility snapshot", "fixtures/compatibility-snapshot.json", compatibilitySHA},
		{"architecture schema", "schema/architecture.schema.json", architectureSchemaSHA},
	}
	for _, tc := range inputChecks {
		t.Run("pinned/"+tc.name, func(t *testing.T) {
			got := fmt.Sprintf("%x", sha256.Sum256(mustRead(t, tc.path)))
			if got != tc.want {
				t.Fatalf("%s changed: sha256 %s, want %s", tc.path, got, tc.want)
			}
		})
	}

	var got architecture
	var req requirements
	var old estate
	var compat compatibilitySnapshot
	mustUnmarshal(t, artifactBytes, &got)
	mustUnmarshal(t, mustRead(t, "fixtures/requirements.json"), &req)
	mustUnmarshal(t, mustRead(t, "fixtures/estate.json"), &old)
	mustUnmarshal(t, mustRead(t, "fixtures/compatibility-snapshot.json"), &compat)

	checks := []struct {
		name string
		fn   func() error
	}{
		{"release and identity", func() error { return checkIdentity(got, req) }},
		{"greenfield services", func() error { return checkGreenfieldServices(got.SddcSpec, req) }},
		{"management hosts", func() error { return checkManagementHosts(got.SddcSpec, req) }},
		{"sddc networks", func() error { return checkSddcNetworks(got.SddcSpec, req) }},
		{"site topology", func() error { return checkSites(got.Sites, req.Sites) }},
		{"capacity and clusters", func() error { return checkClusters(got.Clusters, req) }},
		{"availability objectives", func() error { return checkAvailability(got.Availability, req.Availability) }},
		{"migration coverage and order", func() error { return checkMigration(got, old, compat) }},
		{"research record", func() error { return checkResearch(got.Research, old) }},
	}
	for _, tc := range checks {
		t.Run(tc.name, func(t *testing.T) {
			if err := tc.fn(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func checkIdentity(got architecture, req requirements) error {
	if got.SchemaVersion != "1.0" {
		return fmt.Errorf("schemaVersion = %q, want 1.0", got.SchemaVersion)
	}
	if got.TargetVersion != req.TargetVersion || got.SddcSpec.Version != req.TargetVersion {
		return fmt.Errorf("target and SddcSpec versions must both be %s", req.TargetVersion)
	}
	if got.SddcSpec.SddcID != req.Instance.ID || got.SddcSpec.VCFInstanceName != req.Instance.Name {
		return fmt.Errorf("SDDC identity does not match requirements")
	}
	if got.SddcSpec.WorkflowType != "VCF" {
		return fmt.Errorf("workflowType = %q, want VCF", got.SddcSpec.WorkflowType)
	}
	return nil
}

func checkGreenfieldServices(got sddcSpec, req requirements) error {
	if got.VcenterSpec.UseExistingDeployment || got.SddcManagerSpec.UseExistingDeployment || got.NsxtSpec.UseExistingDeployment {
		return errors.New("SddcSpec must describe greenfield, not existing, management components")
	}
	if got.VcenterSpec.VcenterHostname != req.Instance.VcenterFQDN || got.SddcManagerSpec.Hostname != req.Instance.SddcManagerFQDN {
		return errors.New("vCenter or SDDC Manager FQDN differs from requirements")
	}
	if got.VcenterSpec.Version != req.TargetVersion || got.SddcManagerSpec.Version != req.TargetVersion || got.NsxtSpec.Version != req.TargetVersion {
		return errors.New("greenfield component versions must equal the target release")
	}
	if got.DnsSpec.Subdomain != req.Instance.DNSSubdomain || !reflect.DeepEqual(got.DnsSpec.Nameservers, req.Instance.DNSServers) || !reflect.DeepEqual(got.NTPServers, req.Instance.NTPServers) {
		return errors.New("DNS or NTP design differs from requirements")
	}
	creds := req.ValidationCredentials
	if got.VcenterSpec.RootVcenterPassword != creds.VcenterRootPassword || got.VcenterSpec.AdminUserSsoPassword != creds.VcenterSsoPassword ||
		got.SddcManagerSpec.RootPassword != creds.SddcManagerRootPassword || got.SddcManagerSpec.SshPassword != creds.SddcManagerSshPassword || got.SddcManagerSpec.LocalUserPassword != creds.SddcLocalUserPassword ||
		got.NsxtSpec.RootNsxtManagerPassword != creds.NsxRootPassword || got.NsxtSpec.NsxtAdminPassword != creds.NsxAdminPassword || got.NsxtSpec.NsxtAuditPassword != creds.NsxAuditPassword {
		return errors.New("SddcSpec must use the fixture's non-production validation credentials exactly")
	}
	if got.NsxtSpec.VIPFQDN != req.Instance.NsxVIPFQDN || got.NsxtSpec.TransportVLANID != networkByType(req.Sites[0].Networks, "HOST_OVERLAY").VLANID {
		return errors.New("NSX VIP or transport VLAN differs from requirements")
	}
	var nsxHosts []string
	for _, manager := range got.NsxtSpec.NsxtManagers {
		nsxHosts = append(nsxHosts, manager.Hostname)
	}
	if !reflect.DeepEqual(nsxHosts, req.Instance.NsxManagerFQDNs) {
		return errors.New("NSX manager set differs from requirements")
	}
	if got.ClusterSpec.DatacenterName == "" || got.ClusterSpec.ClusterName == "" || got.ManagementPoolName == "" || got.DatastoreSpec.VsanSpec.DatastoreName == "" {
		return errors.New("management datacenter, cluster, pool, and datastore names are required")
	}
	if got.DatastoreSpec.VsanSpec.FailuresToTolerate != 1 {
		return errors.New("management vSAN failuresToTolerate must be 1 for N+1")
	}
	if got.SkipEsxThumbprintValidation || got.SkipGatewayPingValidation {
		return errors.New("greenfield validation bypasses must remain false")
	}
	if len(got.DvsSpecs) != 1 || got.DvsSpecs[0].MTU != 9000 || len(got.DvsSpecs[0].VmnicsToUplinks) != 2 {
		return errors.New("management design requires one 9000-byte-MTU DVS with two uplinks")
	}
	return nil
}

func checkManagementHosts(got sddcSpec, req requirements) error {
	if len(got.HostSpecs) != req.SizingRules.ManagementHostCount {
		return fmt.Errorf("management host count = %d, want %d", len(got.HostSpecs), req.SizingRules.ManagementHostCount)
	}
	for i, want := range req.Instance.ManagementHosts {
		host := got.HostSpecs[i]
		if host.Hostname != want {
			return fmt.Errorf("management host %d = %q, want %q", i, host.Hostname, want)
		}
		if host.Credentials.Username != req.ValidationCredentials.EsxiUsername || host.Credentials.Password != req.ValidationCredentials.EsxiPassword {
			return fmt.Errorf("management host %q does not use fixture validation credentials", host.Hostname)
		}
	}
	return nil
}

func checkSddcNetworks(got sddcSpec, req requirements) error {
	var primary site
	for _, s := range req.Sites {
		if s.ID == req.Instance.ManagementSiteID {
			primary = s
		}
	}
	if len(got.NetworkSpecs) != len(primary.Networks) {
		return fmt.Errorf("SddcSpec network count = %d, want %d", len(got.NetworkSpecs), len(primary.Networks))
	}
	want := make(map[string]network, len(primary.Networks))
	for _, n := range primary.Networks {
		want[n.Type] = n
	}
	seen := map[string]bool{}
	for _, n := range got.NetworkSpecs {
		expected, ok := want[n.NetworkType]
		if !ok || seen[n.NetworkType] {
			return fmt.Errorf("unexpected or duplicate SddcSpec network %q", n.NetworkType)
		}
		seen[n.NetworkType] = true
		if n.VLANID != expected.VLANID || n.Subnet != expected.CIDR || n.Gateway != expected.Gateway || n.MTU != expected.MTU {
			return fmt.Errorf("SddcSpec network %s differs from primary-site requirements", n.NetworkType)
		}
		if _, ipnet, err := net.ParseCIDR(n.Subnet); err != nil || !ipnet.Contains(net.ParseIP(n.Gateway)) {
			return fmt.Errorf("gateway for %s is not in its subnet", n.NetworkType)
		}
	}
	return nil
}

func checkSites(got []site, want []site) error {
	if len(got) != len(want) {
		return fmt.Errorf("site count = %d, want %d", len(got), len(want))
	}
	byID := make(map[string]site, len(got))
	for _, s := range got {
		if _, duplicate := byID[s.ID]; duplicate {
			return fmt.Errorf("duplicate site %q", s.ID)
		}
		byID[s.ID] = s
	}
	for _, expected := range want {
		actual, ok := byID[expected.ID]
		if !ok || actual.Role != expected.Role {
			return fmt.Errorf("site %s role/topology missing", expected.ID)
		}
		if !reflect.DeepEqual(actual.Networks, expected.Networks) {
			return fmt.Errorf("site %s networks differ from requirements", expected.ID)
		}
	}
	return nil
}

func checkClusters(got []cluster, req requirements) error {
	management := 0
	workloadBySite := map[string][]cluster{}
	names := map[string]bool{}
	for _, c := range got {
		if names[c.Name] {
			return fmt.Errorf("duplicate cluster name %q", c.Name)
		}
		names[c.Name] = true
		profile, ok := req.HostProfiles[c.HostProfile]
		if !ok {
			return fmt.Errorf("cluster %s uses unknown host profile %q", c.Name, c.HostProfile)
		}
		if c.FailureReserveHosts != req.SizingRules.ReservedHostsPerCluster {
			return fmt.Errorf("cluster %s reserve = %d, want %d", c.Name, c.FailureReserveHosts, req.SizingRules.ReservedHostsPerCluster)
		}
		expected := usableCapacity(c.HostCount, c.FailureReserveHosts, profile.PhysicalCores, profile.MemoryGiB, profile.RawStorageTiB, req)
		if c.UsableCapacity.VCPUs != expected.VCPUs || c.UsableCapacity.MemoryGiB != expected.MemoryGiB || math.Abs(c.UsableCapacity.StorageTiB-expected.StorageTiB) > 0.01 {
			return fmt.Errorf("cluster %s usableCapacity is not derived from its hosts, reserve, and sizing rules", c.Name)
		}
		switch c.Role {
		case "MANAGEMENT":
			management++
			if c.SiteID != req.Instance.ManagementSiteID || c.HostProfile != "management-32" || c.HostCount != req.SizingRules.ManagementHostCount {
				return fmt.Errorf("management cluster placement or sizing differs from requirements")
			}
		case "WORKLOAD":
			if c.HostProfile != "workload-64" || c.HostCount < 4 || c.HostCount > req.SizingRules.WorkloadMaxHostsPerCluster {
				return fmt.Errorf("workload cluster %s violates host-profile or cluster-size constraints", c.Name)
			}
			workloadBySite[c.SiteID] = append(workloadBySite[c.SiteID], c)
		default:
			return fmt.Errorf("cluster %s has unknown role %q", c.Name, c.Role)
		}
	}
	if management != 1 {
		return fmt.Errorf("management cluster count = %d, want 1", management)
	}
	for _, s := range req.Sites {
		clusters := workloadBySite[s.ID]
		if len(clusters) == 0 {
			return fmt.Errorf("site %s has no workload cluster", s.ID)
		}
		var total capacity
		hosts := 0
		for _, c := range clusters {
			total.VCPUs += c.UsableCapacity.VCPUs
			total.MemoryGiB += c.UsableCapacity.MemoryGiB
			total.StorageTiB += c.UsableCapacity.StorageTiB
			hosts += c.HostCount
		}
		if total.VCPUs < s.Workload.VCPUs || total.MemoryGiB < s.Workload.MemoryGiB || total.StorageTiB+0.001 < s.Workload.UsableStorageTiB {
			return fmt.Errorf("site %s capacity does not meet stated workload", s.ID)
		}
		minClusters, minHosts := minimumWorkloadPlan(s, req)
		if len(clusters) != minClusters || hosts != minHosts {
			return fmt.Errorf("site %s uses %d clusters/%d hosts, capacity-derived minimum is %d/%d", s.ID, len(clusters), hosts, minClusters, minHosts)
		}
	}
	if len(workloadBySite) != len(req.Sites) {
		return errors.New("workload cluster assigned to an unknown site")
	}
	return nil
}

func usableCapacity(hosts, reserve, cores, memory int, rawStorage float64, req requirements) capacity {
	active := hosts - reserve
	return capacity{
		VCPUs:      active * cores * req.SizingRules.MaxVcpuPerPhysicalCore,
		MemoryGiB:  active * memory,
		StorageTiB: float64(active) * rawStorage * req.SizingRules.StorageDataEfficiency * (1 - req.SizingRules.StorageFreeFraction),
	}
}

func minimumWorkloadPlan(s site, req requirements) (int, int) {
	p := req.HostProfiles["workload-64"]
	for clusterCount := 1; clusterCount <= 16; clusterCount++ {
		minHosts := clusterCount * 4
		maxHosts := clusterCount * req.SizingRules.WorkloadMaxHostsPerCluster
		for hosts := minHosts; hosts <= maxHosts; hosts++ {
			c := usableCapacity(hosts, clusterCount*req.SizingRules.ReservedHostsPerCluster, p.PhysicalCores, p.MemoryGiB, p.RawStorageTiB, req)
			if c.VCPUs >= s.Workload.VCPUs && c.MemoryGiB >= s.Workload.MemoryGiB && c.StorageTiB+0.001 >= s.Workload.UsableStorageTiB {
				return clusterCount, hosts
			}
		}
	}
	return 0, 0
}

func checkAvailability(got, want availability) error {
	if !reflect.DeepEqual(got, want) {
		return fmt.Errorf("availability = %+v, want %+v", got, want)
	}
	return nil
}

func checkMigration(got architecture, old estate, compat compatibilitySnapshot) error {
	if got.TargetVersion != compat.TargetRelease {
		return errors.New("architecture target differs from compatibility snapshot")
	}
	if len(got.MigrationPlan) != len(old.Components) || len(got.MigrationPlan) != len(compat.Plan) {
		return fmt.Errorf("migration steps = %d; every one of %d estate components needs exactly one snapshot-backed step", len(got.MigrationPlan), len(old.Components))
	}
	estateByID := make(map[string]struct{ Name, Version string }, len(old.Components))
	for _, c := range old.Components {
		estateByID[c.ID] = struct{ Name, Version string }{c.Name, c.Version}
	}
	seen := map[string]bool{}
	for i, expected := range compat.Plan {
		step := got.MigrationPlan[i]
		if step.Order != i+1 || step.Order != expected.Order {
			return fmt.Errorf("migration step index %d order = %d, want %d", i, step.Order, expected.Order)
		}
		source, ok := estateByID[step.ComponentID]
		if !ok || seen[step.ComponentID] {
			return fmt.Errorf("migration step %d has unknown or duplicate componentId %q", step.Order, step.ComponentID)
		}
		seen[step.ComponentID] = true
		if step.Component != source.Name || step.FromVersion != source.Version {
			return fmt.Errorf("migration step %d source name/version differs from estate fixture", step.Order)
		}
		if step.ComponentID != expected.ComponentID || step.FromVersion != expected.FromVersion || step.Action != expected.Action || step.TargetComponent != expected.TargetComponent || step.TargetVersion != expected.TargetVersion || !reflect.DeepEqual(step.Gates, expected.Gates) {
			return fmt.Errorf("migration step %d differs from pinned compatibility decision", step.Order)
		}
	}
	return nil
}

func checkResearch(sources []researchSource, old estate) error {
	if len(sources) == 0 {
		return errors.New("research must record at least one consulted source")
	}
	wantComponents := make(map[string]bool, len(old.Components))
	for _, component := range old.Components {
		wantComponents[component.ID] = true
	}
	covered := make(map[string]bool, len(wantComponents))
	seenURLs := make(map[string]bool, len(sources))
	for i, source := range sources {
		if strings.TrimSpace(source.Title) == "" {
			return fmt.Errorf("research source %d has a blank title", i+1)
		}
		parsed, err := url.Parse(source.URL)
		if err != nil || parsed.Scheme != "https" || parsed.User != nil || parsed.Hostname() == "" {
			return fmt.Errorf("research source %d must use a valid public HTTPS URL", i+1)
		}
		host := strings.ToLower(parsed.Hostname())
		if host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com") {
			return fmt.Errorf("research source %d is not Broadcom-published", i+1)
		}
		if seenURLs[source.URL] {
			return fmt.Errorf("research source %d duplicates URL %q", i+1, source.URL)
		}
		seenURLs[source.URL] = true
		accessed, err := time.Parse("2006-01-02", source.AccessedOn)
		if err != nil || accessed.Format("2006-01-02") != source.AccessedOn {
			return fmt.Errorf("research source %d has invalid accessedOn date %q", i+1, source.AccessedOn)
		}
		if len(source.ComponentIDs) == 0 {
			return fmt.Errorf("research source %d does not identify an informed component", i+1)
		}
		seenComponents := map[string]bool{}
		for _, componentID := range source.ComponentIDs {
			if !wantComponents[componentID] {
				return fmt.Errorf("research source %d names unknown componentId %q", i+1, componentID)
			}
			if seenComponents[componentID] {
				return fmt.Errorf("research source %d repeats componentId %q", i+1, componentID)
			}
			seenComponents[componentID] = true
			covered[componentID] = true
		}
	}
	for componentID := range wantComponents {
		if !covered[componentID] {
			return fmt.Errorf("research does not cover estate component %q", componentID)
		}
	}
	return nil
}

func networkByType(networks []network, kind string) network {
	for _, n := range networks {
		if n.Type == kind {
			return n
		}
	}
	return network{}
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

func decodeJSON(t *testing.T, b []byte) any {
	t.Helper()
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		t.Fatalf("decode JSON: %v", err)
	}
	return v
}

func validateJSON(root, schema, value any, path string) error {
	s, ok := schema.(map[string]any)
	if !ok {
		return fmt.Errorf("%s: schema is not an object", path)
	}
	if nullable, _ := s["nullable"].(bool); nullable && value == nil {
		return nil
	}
	if ref, ok := s["$ref"].(string); ok {
		resolved, err := resolvePointer(root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		if err := validateJSON(root, resolved, value, path); err != nil {
			return err
		}
	}
	for _, keyword := range []string{"allOf", "anyOf", "oneOf"} {
		branches, ok := s[keyword].([]any)
		if !ok {
			continue
		}
		matches := 0
		var lastErr error
		for _, branch := range branches {
			if err := validateJSON(root, branch, value, path); err == nil {
				matches++
			} else {
				lastErr = err
			}
		}
		if keyword == "allOf" && matches != len(branches) {
			return fmt.Errorf("%s: allOf failed: %v", path, lastErr)
		}
		if keyword == "anyOf" && matches == 0 {
			return fmt.Errorf("%s: anyOf failed: %v", path, lastErr)
		}
		if keyword == "oneOf" && matches != 1 {
			return fmt.Errorf("%s: oneOf matched %d branches", path, matches)
		}
	}
	if c, ok := s["const"]; ok && !jsonEqual(c, value) {
		return fmt.Errorf("%s: value does not equal const", path)
	}
	if enum, ok := s["enum"].([]any); ok {
		matched := false
		for _, allowed := range enum {
			matched = matched || jsonEqual(allowed, value)
		}
		if !matched {
			return fmt.Errorf("%s: value is not in enum", path)
		}
	}
	if typ, ok := s["type"].(string); ok {
		if err := validateType(typ, value, path); err != nil {
			return err
		}
	}
	switch v := value.(type) {
	case map[string]any:
		if required, ok := s["required"].([]any); ok {
			for _, item := range required {
				name, _ := item.(string)
				if _, exists := v[name]; !exists {
					return fmt.Errorf("%s: missing required property %q", path, name)
				}
			}
		}
		properties, _ := s["properties"].(map[string]any)
		for name, childSchema := range properties {
			if child, exists := v[name]; exists {
				if err := validateJSON(root, childSchema, child, path+"."+name); err != nil {
					return err
				}
			}
		}
		if additional, exists := s["additionalProperties"]; exists {
			for name, child := range v {
				if _, declared := properties[name]; declared {
					continue
				}
				switch a := additional.(type) {
				case bool:
					if !a {
						return fmt.Errorf("%s: unexpected property %q", path, name)
					}
				case map[string]any:
					if err := validateJSON(root, a, child, path+"."+name); err != nil {
						return err
					}
				}
			}
		}
	case []any:
		if n, ok := intKeyword(s, "minItems"); ok && len(v) < n {
			return fmt.Errorf("%s: has %d items, minimum is %d", path, len(v), n)
		}
		if n, ok := intKeyword(s, "maxItems"); ok && len(v) > n {
			return fmt.Errorf("%s: has %d items, maximum is %d", path, len(v), n)
		}
		if unique, _ := s["uniqueItems"].(bool); unique {
			seen := map[string]bool{}
			for _, item := range v {
				b, _ := json.Marshal(item)
				if seen[string(b)] {
					return fmt.Errorf("%s: contains duplicate items", path)
				}
				seen[string(b)] = true
			}
		}
		if itemSchema, exists := s["items"]; exists {
			for i, item := range v {
				if err := validateJSON(root, itemSchema, item, fmt.Sprintf("%s[%d]", path, i)); err != nil {
					return err
				}
			}
		}
	case string:
		if n, ok := intKeyword(s, "minLength"); ok && utf8.RuneCountInString(v) < n {
			return fmt.Errorf("%s: string is shorter than %d", path, n)
		}
		if n, ok := intKeyword(s, "maxLength"); ok && utf8.RuneCountInString(v) > n {
			return fmt.Errorf("%s: string is longer than %d", path, n)
		}
		if pattern, ok := s["pattern"].(string); ok {
			re, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: invalid schema pattern %q: %v", path, pattern, err)
			}
			if !re.MatchString(v) {
				return fmt.Errorf("%s: string does not match %q", path, pattern)
			}
		}
	case json.Number:
		n, err := strconv.ParseFloat(string(v), 64)
		if err != nil {
			return fmt.Errorf("%s: invalid number", path)
		}
		if minimum, ok := numberKeyword(s, "minimum"); ok && n < minimum {
			return fmt.Errorf("%s: number %v is below minimum %v", path, n, minimum)
		}
		if maximum, ok := numberKeyword(s, "maximum"); ok && n > maximum {
			return fmt.Errorf("%s: number %v is above maximum %v", path, n, maximum)
		}
	}
	return nil
}

func validateType(typ string, value any, path string) error {
	valid := false
	switch typ {
	case "object":
		_, valid = value.(map[string]any)
	case "array":
		_, valid = value.([]any)
	case "string":
		_, valid = value.(string)
	case "boolean":
		_, valid = value.(bool)
	case "number":
		_, valid = value.(json.Number)
	case "integer":
		if n, ok := value.(json.Number); ok {
			_, err := strconv.ParseInt(string(n), 10, 64)
			valid = err == nil
		}
	case "null":
		valid = value == nil
	default:
		return fmt.Errorf("%s: unsupported schema type %q", path, typ)
	}
	if !valid {
		return fmt.Errorf("%s: value is not type %s", path, typ)
	}
	return nil
}

func resolvePointer(root any, ref string) (any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("only local schema references are supported: %s", ref)
	}
	current := root
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		obj, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("invalid JSON pointer %s", ref)
		}
		current, ok = obj[token]
		if !ok {
			return nil, fmt.Errorf("unresolved JSON pointer %s", ref)
		}
	}
	return current, nil
}

func intKeyword(schema map[string]any, key string) (int, bool) {
	n, ok := schema[key].(json.Number)
	if !ok {
		return 0, false
	}
	v, err := strconv.Atoi(string(n))
	return v, err == nil
}

func numberKeyword(schema map[string]any, key string) (float64, bool) {
	n, ok := schema[key].(json.Number)
	if !ok {
		return 0, false
	}
	v, err := strconv.ParseFloat(string(n), 64)
	return v, err == nil
}

func jsonEqual(a, b any) bool {
	left, _ := json.Marshal(a)
	right, _ := json.Marshal(b)
	return bytes.Equal(left, right)
}
