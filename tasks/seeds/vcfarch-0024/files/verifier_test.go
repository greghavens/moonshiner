// Protected acceptance verifier. It is deliberately hermetic: validation reads
// only the submitted artifact and the pinned files in this repository.
package architecture

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"math"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
)

const installerSchemaPath = "specifications/vcf-installer/vcf-installer-openapi.json"

type designRequirements struct {
	TargetBundle string `json:"targetBundle"`
	Site         struct {
		Code               string   `json:"code"`
		VCFInstanceName    string   `json:"vcfInstanceName"`
		DNSSubdomain       string   `json:"dnsSubdomain"`
		DNSServers         []string `json:"dnsServers"`
		NTPServers         []string `json:"ntpServers"`
		ManagementPoolName string   `json:"managementPoolName"`
	} `json:"site"`
	ManagementDomain struct {
		MinimumHostCount                   int `json:"minimumHostCount"`
		HostFailureTolerance               int `json:"hostFailureTolerance"`
		RequiredNsxManagerCount            int `json:"requiredNsxManagerCount"`
		RequiredOperationsNodeCount        int `json:"requiredOperationsNodeCount"`
		MinimumUsableCapacityAfterFailures struct {
			PhysicalCores int `json:"physicalCores"`
			MemoryGiB     int `json:"memoryGiB"`
			RawStorageTiB int `json:"rawStorageTiB"`
		} `json:"minimumUsableCapacityAfterFailures"`
		HostProfile struct {
			PhysicalCores int `json:"physicalCores"`
			MemoryGiB     int `json:"memoryGiB"`
			RawStorageTiB int `json:"rawStorageTiB"`
		} `json:"hostProfile"`
	} `json:"managementDomain"`
	Appliances struct {
		VcenterFqdn                string   `json:"vcenterFqdn"`
		SddcManagerFqdn            string   `json:"sddcManagerFqdn"`
		NsxManagerFqdns            []string `json:"nsxManagerFqdns"`
		NsxVipFqdn                 string   `json:"nsxVipFqdn"`
		OperationsNodeFqdns        []string `json:"operationsNodeFqdns"`
		OperationsLoadBalancerFqdn string   `json:"operationsLoadBalancerFqdn"`
		LicenseServerFqdn          string   `json:"licenseServerFqdn"`
	} `json:"appliances"`
	Networks []struct {
		Type       string `json:"type"`
		VlanID     int    `json:"vlanId"`
		Subnet     string `json:"subnet"`
		Gateway    string `json:"gateway"`
		SubnetMask string `json:"subnetMask"`
		MTU        int    `json:"mtu"`
		StartIP    string `json:"startIp"`
		EndIP      string `json:"endIp"`
	} `json:"networks"`
	NsxTransport struct {
		VlanID   int    `json:"vlanId"`
		PoolName string `json:"poolName"`
		CIDR     string `json:"cidr"`
		Gateway  string `json:"gateway"`
		StartIP  string `json:"startIp"`
		EndIP    string `json:"endIp"`
	} `json:"nsxTransport"`
	ManagementServicesNetworks struct {
		LocalRegion managementNetwork `json:"localRegion"`
		XRegion     managementNetwork `json:"xRegion"`
	} `json:"managementServicesNetworks"`
}

type managementNetwork struct {
	NetworkName string `json:"networkName"`
	SubnetMask  string `json:"subnetMask"`
	Gateway     string `json:"gateway"`
}

type estateInventory struct {
	EstateID          string `json:"estateId"`
	DestinationSddcID string `json:"destinationSddcId"`
	Components        []struct {
		ID      string `json:"id"`
		Name    string `json:"name"`
		Version string `json:"version"`
		Build   string `json:"build"`
	} `json:"components"`
}

type compatibilitySnapshot struct {
	SnapshotID     string   `json:"snapshotId"`
	TargetBundle   string   `json:"targetBundle"`
	MigrationOrder []string `json:"migrationOrder"`
	Transitions    []struct {
		ComponentID               string   `json:"componentId"`
		TargetName                string   `json:"targetName"`
		TargetVersion             string   `json:"targetVersion"`
		TargetBuild               string   `json:"targetBuild"`
		DirectTransitionSupported bool     `json:"directTransitionSupported"`
		BlockReason               string   `json:"blockReason"`
		RequiredAction            string   `json:"requiredAction"`
		RequiredGates             []string `json:"requiredGates"`
	} `json:"transitions"`
}

type artifactView struct {
	SddcSpec        sddcSpecView     `json:"sddcSpec"`
	MigrationPlan   migrationPlan    `json:"migrationPlan"`
	ResearchSources []researchSource `json:"researchSources"`
}

type researchSource struct {
	Title        string   `json:"title"`
	URL          string   `json:"url"`
	ConsultedFor []string `json:"consultedFor"`
}

type sddcSpecView struct {
	SddcID             string `json:"sddcId"`
	WorkflowType       string `json:"workflowType"`
	Version            string `json:"version"`
	VCFInstanceName    string `json:"vcfInstanceName"`
	ManagementPoolName string `json:"managementPoolName"`
	HostSpecs          []struct {
		Hostname string `json:"hostname"`
	} `json:"hostSpecs"`
	VcenterSpec struct {
		Hostname              string `json:"vcenterHostname"`
		RootPassword          string `json:"rootVcenterPassword"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
	} `json:"vcenterSpec"`
	SddcManagerSpec struct {
		Hostname              string `json:"hostname"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
	} `json:"sddcManagerSpec"`
	ClusterSpec struct {
		ClusterName    string `json:"clusterName"`
		DatacenterName string `json:"datacenterName"`
	} `json:"clusterSpec"`
	DatastoreSpec struct {
		VsanSpec struct {
			FailuresToTolerate int `json:"failuresToTolerate"`
		} `json:"vsanSpec"`
	} `json:"datastoreSpec"`
	DvsSpecs []struct {
		Networks        []string `json:"networks"`
		MTU             int      `json:"mtu"`
		VmnicsToUplinks []struct {
			ID     string `json:"id"`
			Uplink string `json:"uplink"`
		} `json:"vmnicsToUplinks"`
	} `json:"dvsSpecs"`
	NetworkSpecs []struct {
		NetworkType            string `json:"networkType"`
		VlanID                 int    `json:"vlanId"`
		Subnet                 string `json:"subnet"`
		Gateway                string `json:"gateway"`
		SubnetMask             string `json:"subnetMask"`
		MTU                    int    `json:"mtu"`
		IncludeIPAddressRanges []struct {
			StartIPAddress string `json:"startIpAddress"`
			EndIPAddress   string `json:"endIpAddress"`
		} `json:"includeIpAddressRanges"`
	} `json:"networkSpecs"`
	DnsSpec struct {
		Subdomain   string   `json:"subdomain"`
		Nameservers []string `json:"nameservers"`
	} `json:"dnsSpec"`
	NTPServers []string `json:"ntpServers"`
	NsxtSpec   struct {
		Managers []struct {
			Hostname string `json:"hostname"`
		} `json:"nsxtManagers"`
		VIPFqdn               string `json:"vipFqdn"`
		TransportVlanID       int    `json:"transportVlanId"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
		IPAddressPoolSpec     struct {
			Name    string `json:"name"`
			Subnets []struct {
				CIDR                string `json:"cidr"`
				Gateway             string `json:"gateway"`
				IPAddressPoolRanges []struct {
					Start string `json:"start"`
					End   string `json:"end"`
				} `json:"ipAddressPoolRanges"`
			} `json:"subnets"`
		} `json:"ipAddressPoolSpec"`
	} `json:"nsxtSpec"`
	OperationsSpec struct {
		Nodes []struct {
			Hostname string `json:"hostname"`
			Type     string `json:"type"`
		} `json:"nodes"`
		LoadBalancerFqdn      string `json:"loadBalancerFqdn"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
	} `json:"vcfOperationsSpec"`
	ManagementInfrastructure struct {
		LocalRegion managementNetwork `json:"localRegionNetwork"`
		XRegion     managementNetwork `json:"xRegionNetwork"`
	} `json:"vcfManagementComponentsInfrastructureSpec"`
	LicenseServerSpec struct {
		Hostname              string `json:"hostname"`
		UseExistingDeployment bool   `json:"useExistingDeployment"`
	} `json:"licenseServerSpec"`
}

type migrationPlan struct {
	EstateID          string          `json:"estateId"`
	DestinationSddcID string          `json:"destinationSddcId"`
	TargetBundle      string          `json:"targetBundle"`
	Strategy          string          `json:"strategy"`
	Steps             []migrationStep `json:"steps"`
}

type migrationStep struct {
	Order         int      `json:"order"`
	ComponentID   string   `json:"componentId"`
	ComponentName string   `json:"componentName"`
	FromVersion   string   `json:"fromVersion"`
	FromBuild     string   `json:"fromBuild"`
	TargetName    string   `json:"targetName"`
	TargetVersion string   `json:"targetVersion"`
	TargetBuild   string   `json:"targetBuild"`
	Action        string   `json:"action"`
	Gates         []string `json:"gates"`
}

func TestProtectedArchitecture(t *testing.T) {
	artifactBytes, err := os.ReadFile("architecture.json")
	if err != nil {
		t.Fatalf("read architecture.json: %v", err)
	}
	var artifactDocument map[string]any
	if err := json.Unmarshal(artifactBytes, &artifactDocument); err != nil {
		t.Fatalf("decode architecture.json: %v", err)
	}

	// This is intentionally the first validation gate. The SddcSpec is checked
	// directly against the schema embedded in the pinned installer OpenAPI file.
	openAPIRoot := readJSONObject(t, installerSchemaPath)
	sddcSchema, err := jsonPointer(openAPIRoot, "#/components/schemas/SddcSpec")
	if err != nil {
		t.Fatalf("resolve installer SddcSpec schema: %v", err)
	}
	sddcInstance, ok := artifactDocument["sddcSpec"]
	if !ok {
		t.Fatal("installer schema validation: architecture.json has no sddcSpec")
	}
	if err := validateSchema(openAPIRoot, sddcSchema, sddcInstance, "$.sddcSpec"); err != nil {
		t.Fatalf("installer SddcSpec schema validation failed: %v", err)
	}

	// Only after the installer schema succeeds may the fixed migration schema
	// and the deterministic domain checks run.
	migrationSchema := readJSONObject(t, "schemas/migration-plan.schema.json")
	migrationInstance, ok := artifactDocument["migrationPlan"]
	if !ok {
		t.Fatal("migration schema validation: architecture.json has no migrationPlan")
	}
	if err := validateSchema(migrationSchema, migrationSchema, migrationInstance, "$.migrationPlan"); err != nil {
		t.Fatalf("migration plan schema validation failed: %v", err)
	}

	var artifact artifactView
	mustJSON(t, artifactBytes, &artifact)
	var requirements designRequirements
	readJSONFile(t, "fixtures/design_requirements.json", &requirements)
	var inventory estateInventory
	readJSONFile(t, "fixtures/estate_inventory.json", &inventory)
	var snapshot compatibilitySnapshot
	readJSONFile(t, "fixtures/compatibility_snapshot.json", &snapshot)

	checks := []struct {
		name string
		fn   func() error
	}{
		{"pinned installer identity", func() error { return checkInstallerIdentity(openAPIRoot) }},
		{"greenfield target identity", func() error { return checkTargetIdentity(artifact.SddcSpec, requirements) }},
		{"management capacity and host availability", func() error { return checkHostCapacity(artifact.SddcSpec, requirements) }},
		{"appliance availability and site services", func() error { return checkAppliances(artifact.SddcSpec, requirements) }},
		{"site networks", func() error { return checkNetworks(artifact.SddcSpec, requirements) }},
		{"migration coverage order targets and gates", func() error { return checkMigration(artifact.MigrationPlan, inventory, snapshot) }},
		{"live research record", func() error { return checkResearchSources(artifact.ResearchSources) }},
		{"Go builder matches submitted architecture", func() error { return checkBuilder(t, artifactDocument) }},
		{"submitted table-driven Go tests", checkSubmittedTests},
	}
	for _, tc := range checks {
		t.Run(tc.name, func(t *testing.T) {
			if err := tc.fn(); err != nil {
				t.Error(err)
			}
		})
	}
}

func checkInstallerIdentity(openAPI map[string]any) error {
	info, ok := openAPI["info"].(map[string]any)
	if !ok || info["version"] != "9.1.0.0" {
		return fmt.Errorf("installer OpenAPI info.version is %v, want 9.1.0.0", info["version"])
	}
	b, err := os.ReadFile(installerSchemaPath)
	if err != nil {
		return err
	}
	sum := sha256.Sum256(b)
	if got := hex.EncodeToString(sum[:]); got != "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d" {
		return fmt.Errorf("installer schema checksum %s is not the pinned tag 9.1.0.0 file", got)
	}
	return nil
}

func checkTargetIdentity(spec sddcSpecView, req designRequirements) error {
	wantID := req.Site.Code + "-m01"
	if spec.SddcID != wantID || spec.WorkflowType != "VCF" || spec.Version != req.TargetBundle {
		return fmt.Errorf("target identity got sddcId=%q workflow=%q version=%q; want %q, VCF, %q", spec.SddcID, spec.WorkflowType, spec.Version, wantID, req.TargetBundle)
	}
	if spec.VCFInstanceName != req.Site.VCFInstanceName || spec.ManagementPoolName != req.Site.ManagementPoolName {
		return fmt.Errorf("site naming does not match requirements")
	}
	password := strings.ToLower(spec.VcenterSpec.RootPassword)
	markers := []string{"change_me", "change-me", "changeme", "placeholder", "example", "dummy", "not_a_secret", "not-a-secret"}
	placeholder := false
	for _, marker := range markers {
		if strings.Contains(password, marker) {
			placeholder = true
			break
		}
	}
	if !placeholder {
		return fmt.Errorf("vCenter password must be an obvious non-secret placeholder")
	}
	if spec.VcenterSpec.UseExistingDeployment || spec.SddcManagerSpec.UseExistingDeployment || spec.NsxtSpec.UseExistingDeployment || spec.OperationsSpec.UseExistingDeployment || spec.LicenseServerSpec.UseExistingDeployment {
		return fmt.Errorf("SddcSpec reuses an existing deployment; target must be greenfield")
	}
	return nil
}

func checkHostCapacity(spec sddcSpecView, req designRequirements) error {
	hosts := len(spec.HostSpecs)
	if hosts < req.ManagementDomain.MinimumHostCount {
		return fmt.Errorf("management domain has %d hosts, need at least %d", hosts, req.ManagementDomain.MinimumHostCount)
	}
	seen := map[string]bool{}
	prefix := req.Site.Code + "-esx"
	for _, host := range spec.HostSpecs {
		if !strings.HasPrefix(host.Hostname, prefix) || seen[host.Hostname] {
			return fmt.Errorf("host %q violates site naming or is duplicated", host.Hostname)
		}
		seen[host.Hostname] = true
	}
	remaining := hosts - req.ManagementDomain.HostFailureTolerance
	minimum := req.ManagementDomain.MinimumUsableCapacityAfterFailures
	profile := req.ManagementDomain.HostProfile
	if remaining*profile.PhysicalCores < minimum.PhysicalCores || remaining*profile.MemoryGiB < minimum.MemoryGiB || remaining*profile.RawStorageTiB < minimum.RawStorageTiB {
		return fmt.Errorf("capacity after %d host failure(s) is %d cores/%d GiB/%d TiB, below %d/%d/%d", req.ManagementDomain.HostFailureTolerance, remaining*profile.PhysicalCores, remaining*profile.MemoryGiB, remaining*profile.RawStorageTiB, minimum.PhysicalCores, minimum.MemoryGiB, minimum.RawStorageTiB)
	}
	if spec.DatastoreSpec.VsanSpec.FailuresToTolerate < req.ManagementDomain.HostFailureTolerance {
		return fmt.Errorf("vSAN failuresToTolerate=%d, want at least %d", spec.DatastoreSpec.VsanSpec.FailuresToTolerate, req.ManagementDomain.HostFailureTolerance)
	}
	return nil
}

func checkAppliances(spec sddcSpecView, req designRequirements) error {
	if spec.VcenterSpec.Hostname != req.Appliances.VcenterFqdn || spec.SddcManagerSpec.Hostname != req.Appliances.SddcManagerFqdn {
		return fmt.Errorf("vCenter or SDDC Manager FQDN does not match site requirements")
	}
	if len(spec.NsxtSpec.Managers) < req.ManagementDomain.RequiredNsxManagerCount {
		return fmt.Errorf("NSX has %d managers, need %d", len(spec.NsxtSpec.Managers), req.ManagementDomain.RequiredNsxManagerCount)
	}
	var nsxNames []string
	for _, node := range spec.NsxtSpec.Managers {
		nsxNames = append(nsxNames, node.Hostname)
	}
	if !sameStrings(nsxNames, req.Appliances.NsxManagerFqdns) || spec.NsxtSpec.VIPFqdn != req.Appliances.NsxVipFqdn {
		return fmt.Errorf("NSX HA node or VIP naming does not match requirements")
	}
	if len(spec.OperationsSpec.Nodes) < req.ManagementDomain.RequiredOperationsNodeCount {
		return fmt.Errorf("VCF Operations has %d nodes, need %d", len(spec.OperationsSpec.Nodes), req.ManagementDomain.RequiredOperationsNodeCount)
	}
	var opsNames []string
	masterCount, replicaCount := 0, 0
	for _, node := range spec.OperationsSpec.Nodes {
		opsNames = append(opsNames, node.Hostname)
		switch node.Type {
		case "master":
			masterCount++
		case "replica":
			replicaCount++
		case "data":
		default:
			return fmt.Errorf("VCF Operations node %q has invalid HA role %q", node.Hostname, node.Type)
		}
	}
	if !sameStrings(opsNames, req.Appliances.OperationsNodeFqdns) || spec.OperationsSpec.LoadBalancerFqdn != req.Appliances.OperationsLoadBalancerFqdn {
		return fmt.Errorf("VCF Operations HA node or load-balancer naming does not match requirements")
	}
	if masterCount != 1 || replicaCount < 1 {
		return fmt.Errorf("VCF Operations HA needs exactly one master and at least one replica; got %d master/%d replica", masterCount, replicaCount)
	}
	if spec.LicenseServerSpec.Hostname != req.Appliances.LicenseServerFqdn {
		return fmt.Errorf("license server FQDN %q does not match %q", spec.LicenseServerSpec.Hostname, req.Appliances.LicenseServerFqdn)
	}
	if spec.DnsSpec.Subdomain != req.Site.DNSSubdomain || !reflect.DeepEqual(spec.DnsSpec.Nameservers, req.Site.DNSServers) || !reflect.DeepEqual(spec.NTPServers, req.Site.NTPServers) {
		return fmt.Errorf("DNS or NTP services do not match site requirements")
	}
	if !reflect.DeepEqual(spec.ManagementInfrastructure.LocalRegion, req.ManagementServicesNetworks.LocalRegion) || !reflect.DeepEqual(spec.ManagementInfrastructure.XRegion, req.ManagementServicesNetworks.XRegion) {
		return fmt.Errorf("VCF management-services networks do not match requirements")
	}
	return nil
}

func checkNetworks(spec sddcSpecView, req designRequirements) error {
	actual := map[string]struct {
		vlan, mtu                     int
		subnet, gateway, mask, lo, hi string
	}{}
	for _, network := range spec.NetworkSpecs {
		lo, hi := "", ""
		if len(network.IncludeIPAddressRanges) == 1 {
			lo = network.IncludeIPAddressRanges[0].StartIPAddress
			hi = network.IncludeIPAddressRanges[0].EndIPAddress
		}
		actual[network.NetworkType] = struct {
			vlan, mtu                     int
			subnet, gateway, mask, lo, hi string
		}{network.VlanID, network.MTU, network.Subnet, network.Gateway, network.SubnetMask, lo, hi}
	}
	if len(actual) != len(req.Networks) {
		return fmt.Errorf("networkSpecs has %d unique networks, want %d", len(actual), len(req.Networks))
	}
	for _, want := range req.Networks {
		got, ok := actual[want.Type]
		if !ok || got.vlan != want.VlanID || got.mtu != want.MTU || got.subnet != want.Subnet || got.gateway != want.Gateway || got.mask != want.SubnetMask || got.lo != want.StartIP || got.hi != want.EndIP {
			return fmt.Errorf("network %s does not match required VLAN/subnet/MTU/range", want.Type)
		}
	}
	transport := req.NsxTransport
	pool := spec.NsxtSpec.IPAddressPoolSpec
	if spec.NsxtSpec.TransportVlanID != transport.VlanID || pool.Name != transport.PoolName || len(pool.Subnets) != 1 || pool.Subnets[0].CIDR != transport.CIDR || pool.Subnets[0].Gateway != transport.Gateway || len(pool.Subnets[0].IPAddressPoolRanges) != 1 || pool.Subnets[0].IPAddressPoolRanges[0].Start != transport.StartIP || pool.Subnets[0].IPAddressPoolRanges[0].End != transport.EndIP {
		return fmt.Errorf("NSX transport VLAN or TEP pool does not match requirements")
	}
	if len(spec.DvsSpecs) == 0 {
		return fmt.Errorf("no distributed switch is defined")
	}
	wantTraffic := map[string]bool{"MANAGEMENT": true, "VMOTION": true, "VSAN": true}
	wantNics := map[string]string{"vmnic0": "uplink1", "vmnic1": "uplink2"}
	for _, dvs := range spec.DvsSpecs {
		for _, n := range dvs.Networks {
			delete(wantTraffic, n)
		}
		for _, pair := range dvs.VmnicsToUplinks {
			if wantNics[pair.ID] == pair.Uplink {
				delete(wantNics, pair.ID)
			}
		}
	}
	if len(wantTraffic) != 0 || len(wantNics) != 0 {
		return fmt.Errorf("distributed switch design is missing required traffic types or redundant uplinks")
	}
	return nil
}

func checkMigration(plan migrationPlan, inventory estateInventory, snapshot compatibilitySnapshot) error {
	if plan.EstateID != inventory.EstateID || plan.DestinationSddcID != inventory.DestinationSddcID || plan.TargetBundle != snapshot.TargetBundle || plan.Strategy != "parallel-greenfield" {
		return fmt.Errorf("migration plan identity, destination, bundle, or strategy is wrong")
	}
	if len(plan.Steps) != len(inventory.Components) || len(plan.Steps) != len(snapshot.MigrationOrder) || len(plan.Steps) != len(snapshot.Transitions) {
		return fmt.Errorf("migration steps=%d, inventory=%d, order=%d, transitions=%d", len(plan.Steps), len(inventory.Components), len(snapshot.MigrationOrder), len(snapshot.Transitions))
	}
	inv := map[string]struct{ name, version, build string }{}
	for _, c := range inventory.Components {
		if _, exists := inv[c.ID]; exists {
			return fmt.Errorf("duplicate component %q in inventory", c.ID)
		}
		inv[c.ID] = struct{ name, version, build string }{c.Name, c.Version, c.Build}
	}
	transitions := map[string]struct {
		name, version, build, action string
		supported                    bool
		gates                        []string
	}{}
	for _, tr := range snapshot.Transitions {
		transitions[tr.ComponentID] = struct {
			name, version, build, action string
			supported                    bool
			gates                        []string
		}{tr.TargetName, tr.TargetVersion, tr.TargetBuild, tr.RequiredAction, tr.DirectTransitionSupported, tr.RequiredGates}
	}
	seen := map[string]bool{}
	for i, step := range plan.Steps {
		wantID := snapshot.MigrationOrder[i]
		if step.Order != i+1 || step.ComponentID != wantID || seen[step.ComponentID] {
			return fmt.Errorf("step %d has order/id %d/%q, want %d/%q exactly once", i, step.Order, step.ComponentID, i+1, wantID)
		}
		seen[step.ComponentID] = true
		source, ok := inv[step.ComponentID]
		if !ok {
			return fmt.Errorf("step %q is not in inventory", step.ComponentID)
		}
		target, ok := transitions[step.ComponentID]
		if !ok {
			return fmt.Errorf("step %q has no pinned transition", step.ComponentID)
		}
		if step.ComponentName != source.name || step.FromVersion != source.version || step.FromBuild != source.build {
			return fmt.Errorf("step %q does not preserve exact inventory name/version/build", step.ComponentID)
		}
		if step.TargetName != target.name || step.TargetVersion != target.version || step.TargetBuild != target.build || step.Action != target.action {
			return fmt.Errorf("step %q target or action differs from compatibility snapshot", step.ComponentID)
		}
		if !sameStrings(step.Gates, target.gates) {
			return fmt.Errorf("step %q gates %v differ from required %v", step.ComponentID, step.Gates, target.gates)
		}
		if !target.supported && step.Action == "upgrade" {
			return fmt.Errorf("step %q incorrectly models a blocked back-in-time transition as an upgrade", step.ComponentID)
		}
	}
	return nil
}

func checkResearchSources(sources []researchSource) error {
	if len(sources) == 0 {
		return fmt.Errorf("researchSources must record at least one Broadcom page")
	}
	seen := map[string]bool{}
	var informed []string
	for i, source := range sources {
		if strings.TrimSpace(source.Title) == "" || strings.TrimSpace(source.URL) == "" || len(source.ConsultedFor) == 0 {
			return fmt.Errorf("researchSources[%d] must have nonempty title, url, and consultedFor", i)
		}
		parsed, err := url.Parse(source.URL)
		if err != nil || parsed.Scheme != "https" || parsed.User != nil {
			return fmt.Errorf("researchSources[%d] URL must be an HTTPS Broadcom page", i)
		}
		host := strings.ToLower(parsed.Hostname())
		if host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com") {
			return fmt.Errorf("researchSources[%d] host %q is not Broadcom-published", i, host)
		}
		canonical := parsed.String()
		if seen[canonical] {
			return fmt.Errorf("researchSources repeats URL %q", canonical)
		}
		seen[canonical] = true
		for j, topic := range source.ConsultedFor {
			if strings.TrimSpace(topic) == "" {
				return fmt.Errorf("researchSources[%d].consultedFor[%d] is empty", i, j)
			}
			informed = append(informed, strings.ToLower(topic))
		}
	}
	coverage := strings.Join(informed, " ")
	if !containsAny(coverage, "compatib", "interoperab", "supported product", "matrix", "combination") {
		return fmt.Errorf("researchSources does not record compatibility or interoperability research")
	}
	if !containsAny(coverage, "upgrade", "sequence", "transition", "path", "order") {
		return fmt.Errorf("researchSources does not record upgrade sequencing or path research")
	}
	if !containsAny(coverage, "back-in-time", "chronolog", "newer", "downgrade", "older than", "regression", "exceed", "later build", "target older") {
		return fmt.Errorf("researchSources does not record the newer-installed-build restriction")
	}
	return nil
}

func containsAny(s string, terms ...string) bool {
	for _, term := range terms {
		if strings.Contains(s, term) {
			return true
		}
	}
	return false
}

func checkBuilder(t *testing.T, submitted map[string]any) error {
	builtBytes, err := BuildArchitecture("fixtures/design_requirements.json", "fixtures/estate_inventory.json", "fixtures/compatibility_snapshot.json")
	if err != nil {
		return fmt.Errorf("BuildArchitecture: %w", err)
	}
	var built map[string]any
	if err := json.Unmarshal(builtBytes, &built); err != nil {
		return fmt.Errorf("BuildArchitecture returned invalid JSON: %w", err)
	}
	var builtView artifactView
	if err := json.Unmarshal(builtBytes, &builtView); err != nil {
		return fmt.Errorf("decode BuildArchitecture result: %w", err)
	}
	if err := checkResearchSources(builtView.ResearchSources); err != nil {
		return fmt.Errorf("BuildArchitecture researchSources: %w", err)
	}
	builtProducts := map[string]any{
		"sddcSpec":      built["sddcSpec"],
		"migrationPlan": built["migrationPlan"],
	}
	copySubmitted := map[string]any{
		"sddcSpec":      submitted["sddcSpec"],
		"migrationPlan": submitted["migrationPlan"],
	}
	if !reflect.DeepEqual(builtProducts, copySubmitted) {
		return fmt.Errorf("BuildArchitecture output differs from architecture.json")
	}

	var requirements designRequirements
	if err := decodeProtectedJSONFile("fixtures/design_requirements.json", &requirements); err != nil {
		return err
	}
	var inventory estateInventory
	if err := decodeProtectedJSONFile("fixtures/estate_inventory.json", &inventory); err != nil {
		return err
	}
	var snapshot compatibilitySnapshot
	if err := decodeProtectedJSONFile("fixtures/compatibility_snapshot.json", &snapshot); err != nil {
		return err
	}
	requirements.Site.VCFInstanceName = "CHI01 Builder Probe"
	inventory.DestinationSddcID = "chi01-probe"
	inventory.Components[0].Version = "8.18.3-probe"
	snapshot.Transitions[0].TargetBuild = "probe-build"
	snapshot.Transitions[0].RequiredGates = []string{"probe-gate"}
	probeDir := t.TempDir()
	requirementsPath := filepath.Join(probeDir, "requirements.json")
	inventoryPath := filepath.Join(probeDir, "inventory.json")
	compatibilityPath := filepath.Join(probeDir, "compatibility.json")
	for path, value := range map[string]any{
		requirementsPath:  requirements,
		inventoryPath:     inventory,
		compatibilityPath: snapshot,
	} {
		encoded, err := json.Marshal(value)
		if err != nil {
			return fmt.Errorf("encode builder probe: %w", err)
		}
		if err := os.WriteFile(path, encoded, 0o600); err != nil {
			return fmt.Errorf("write builder probe: %w", err)
		}
	}
	probeBytes, err := BuildArchitecture(requirementsPath, inventoryPath, compatibilityPath)
	if err != nil {
		return fmt.Errorf("BuildArchitecture with alternate inputs: %w", err)
	}
	var probe artifactView
	if err := json.Unmarshal(probeBytes, &probe); err != nil {
		return fmt.Errorf("decode alternate BuildArchitecture result: %w", err)
	}
	if probe.SddcSpec.VCFInstanceName != requirements.Site.VCFInstanceName || probe.SddcSpec.SddcID != inventory.DestinationSddcID {
		return fmt.Errorf("BuildArchitecture does not derive the target from its requirements and inventory paths")
	}
	if probe.MigrationPlan.DestinationSddcID != inventory.DestinationSddcID || len(probe.MigrationPlan.Steps) == 0 {
		return fmt.Errorf("BuildArchitecture does not derive the migration from its inventory path")
	}
	first := probe.MigrationPlan.Steps[0]
	if first.FromVersion != inventory.Components[0].Version || first.TargetBuild != snapshot.Transitions[0].TargetBuild || !reflect.DeepEqual(first.Gates, snapshot.Transitions[0].RequiredGates) {
		return fmt.Errorf("BuildArchitecture does not derive migration steps from inventory and compatibility inputs")
	}
	return nil
}

func decodeProtectedJSONFile(path string, out any) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read protected input %s: %w", path, err)
	}
	if err := json.Unmarshal(b, out); err != nil {
		return fmt.Errorf("decode protected input %s: %w", path, err)
	}
	return nil
}

func checkSubmittedTests() error {
	paths, err := filepath.Glob("*_test.go")
	if err != nil {
		return fmt.Errorf("locate Go tests: %w", err)
	}
	for _, path := range paths {
		if filepath.Base(path) == "verifier_test.go" {
			continue
		}
		file, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
		if err != nil {
			return fmt.Errorf("parse %s: %w", path, err)
		}
		for _, declaration := range file.Decls {
			function, ok := declaration.(*ast.FuncDecl)
			if !ok || function.Body == nil || !strings.HasPrefix(function.Name.Name, "Test") {
				continue
			}
			hasTableRange, callsBuilder := false, false
			ast.Inspect(function.Body, func(node ast.Node) bool {
				switch value := node.(type) {
				case *ast.RangeStmt:
					hasTableRange = true
				case *ast.CallExpr:
					identifier, direct := value.Fun.(*ast.Ident)
					selector, qualified := value.Fun.(*ast.SelectorExpr)
					if (direct && identifier.Name == "BuildArchitecture") || (qualified && selector.Sel.Name == "BuildArchitecture") {
						callsBuilder = true
					}
				}
				return true
			})
			if hasTableRange && callsBuilder {
				return nil
			}
		}
	}
	return fmt.Errorf("add a table-driven Go test that exercises BuildArchitecture")
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

func readJSONObject(t *testing.T, path string) map[string]any {
	t.Helper()
	var out map[string]any
	readJSONFile(t, path, &out)
	return out
}

func readJSONFile(t *testing.T, path string, out any) {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	mustJSON(t, b, out)
}

func mustJSON(t *testing.T, b []byte, out any) {
	t.Helper()
	if err := json.Unmarshal(b, out); err != nil {
		t.Fatalf("decode JSON: %v", err)
	}
}

func jsonPointer(root any, pointer string) (any, error) {
	if pointer == "#" || pointer == "" {
		return root, nil
	}
	if !strings.HasPrefix(pointer, "#/") {
		return nil, fmt.Errorf("only local JSON pointers are supported: %q", pointer)
	}
	cur := root
	for _, raw := range strings.Split(strings.TrimPrefix(pointer, "#/"), "/") {
		key := strings.ReplaceAll(strings.ReplaceAll(raw, "~1", "/"), "~0", "~")
		obj, ok := cur.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%q traverses a non-object", pointer)
		}
		cur, ok = obj[key]
		if !ok {
			return nil, fmt.Errorf("%q does not exist", pointer)
		}
	}
	return cur, nil
}

// validateSchema implements the JSON Schema/OpenAPI keywords exercised by the
// pinned installer graph and the fixed migration schema. References always
// resolve against root, so SddcSpec is validated using its own nested schemas.
func validateSchema(root any, rawSchema any, value any, path string) error {
	schema, ok := rawSchema.(map[string]any)
	if !ok {
		return fmt.Errorf("%s: schema is not an object", path)
	}
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := jsonPointer(root, ref)
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
	if c, exists := schema["const"]; exists && !reflect.DeepEqual(c, value) {
		return fmt.Errorf("%s: value %v does not equal const %v", path, value, c)
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
			return fmt.Errorf("%s: value %v is not in enum", path, value)
		}
	}
	for _, keyword := range []string{"allOf"} {
		if schemas, ok := schema[keyword].([]any); ok {
			for _, part := range schemas {
				if err := validateSchema(root, part, value, path); err != nil {
					return err
				}
			}
		}
	}
	for _, keyword := range []string{"anyOf", "oneOf"} {
		if schemas, ok := schema[keyword].([]any); ok {
			matches := 0
			for _, part := range schemas {
				if validateSchema(root, part, value, path) == nil {
					matches++
				}
			}
			if matches == 0 || (keyword == "oneOf" && matches != 1) {
				return fmt.Errorf("%s: %s matched %d branches", path, keyword, matches)
			}
		}
	}
	typeName, _ := schema["type"].(string)
	switch typeName {
	case "object":
		obj, ok := value.(map[string]any)
		if !ok {
			return fmt.Errorf("%s: expected object, got %T", path, value)
		}
		if required, ok := schema["required"].([]any); ok {
			for _, item := range required {
				name, _ := item.(string)
				if _, exists := obj[name]; !exists {
					return fmt.Errorf("%s: missing required property %q", path, name)
				}
			}
		}
		properties, _ := schema["properties"].(map[string]any)
		for name, child := range obj {
			if childSchema, exists := properties[name]; exists {
				if err := validateSchema(root, childSchema, child, path+"."+name); err != nil {
					return err
				}
				continue
			}
			if additional, exists := schema["additionalProperties"]; exists {
				switch v := additional.(type) {
				case bool:
					if !v {
						return fmt.Errorf("%s: additional property %q is not allowed", path, name)
					}
				case map[string]any:
					if err := validateSchema(root, v, child, path+"."+name); err != nil {
						return err
					}
				}
			}
		}
	case "array":
		items, ok := value.([]any)
		if !ok {
			return fmt.Errorf("%s: expected array, got %T", path, value)
		}
		if err := checkCount(path, len(items), schema, "minItems", "maxItems"); err != nil {
			return err
		}
		if unique, _ := schema["uniqueItems"].(bool); unique {
			seen := map[string]bool{}
			for _, item := range items {
				encoded, _ := json.Marshal(item)
				key := string(encoded)
				if seen[key] {
					return fmt.Errorf("%s: array items are not unique", path)
				}
				seen[key] = true
			}
		}
		if itemSchema, exists := schema["items"]; exists {
			for i, item := range items {
				if err := validateSchema(root, itemSchema, item, fmt.Sprintf("%s[%d]", path, i)); err != nil {
					return err
				}
			}
		}
	case "string":
		s, ok := value.(string)
		if !ok {
			return fmt.Errorf("%s: expected string, got %T", path, value)
		}
		if err := checkCount(path, len([]rune(s)), schema, "minLength", "maxLength"); err != nil {
			return err
		}
		if pattern, ok := schema["pattern"].(string); ok {
			re, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: invalid schema pattern %q: %v", path, pattern, err)
			}
			if !re.MatchString(s) {
				return fmt.Errorf("%s: %q does not match %q", path, s, pattern)
			}
		}
	case "integer", "number":
		n, ok := value.(float64)
		if !ok || (typeName == "integer" && math.Trunc(n) != n) {
			return fmt.Errorf("%s: expected %s, got %T(%v)", path, typeName, value, value)
		}
		if min, ok := schema["minimum"].(float64); ok && n < min {
			return fmt.Errorf("%s: %v is below minimum %v", path, n, min)
		}
		if max, ok := schema["maximum"].(float64); ok && n > max {
			return fmt.Errorf("%s: %v is above maximum %v", path, n, max)
		}
	case "boolean":
		if _, ok := value.(bool); !ok {
			return fmt.Errorf("%s: expected boolean, got %T", path, value)
		}
	case "":
		// Schemas containing only combiners, const, or annotations are valid.
	default:
		return fmt.Errorf("%s: unsupported schema type %q", path, typeName)
	}
	return nil
}

func checkCount(path string, got int, schema map[string]any, minKey, maxKey string) error {
	if min, ok := schema[minKey].(float64); ok && got < int(min) {
		return fmt.Errorf("%s: length %d is below %s %d", path, got, minKey, int(min))
	}
	if max, ok := schema[maxKey].(float64); ok && got > int(max) {
		return fmt.Errorf("%s: length %d is above %s %d", path, got, maxKey, int(max))
	}
	return nil
}
