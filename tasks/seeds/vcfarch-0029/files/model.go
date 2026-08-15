package vcfarch

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

type Inputs struct {
	Requirements Requirements
	Estate       Estate
	Snapshot     CompatibilitySnapshot
}

type Requirements struct {
	DesignID        string                  `json:"designId"`
	VCFInstance     string                  `json:"vcfInstanceName"`
	TargetVersion   string                  `json:"targetVersion"`
	Sites           []SiteRequirement       `json:"sites"`
	Capacity        CapacityRequirement     `json:"capacity"`
	Availability    AvailabilityRequirement `json:"availability"`
	Networks        []NetworkRequirement    `json:"networks"`
	DNS             DNSRequirement          `json:"dns"`
	NTPServers      []string                `json:"ntpServers"`
	ManagementFQDNs ManagementFQDNs         `json:"managementFqdns"`
}

type SiteRequirement struct {
	ID               string   `json:"id"`
	Role             string   `json:"role"`
	ManagementHosts  []string `json:"managementHosts"`
	WorkloadHosts    []string `json:"workloadHosts"`
	HostCores        int      `json:"hostCores"`
	HostMemoryGiB    int      `json:"hostMemoryGiB"`
	UsableStorageTiB int      `json:"usableStorageTiB"`
}

type CapacityRequirement struct {
	ManagedVMs                  int `json:"managedVMs"`
	MonitoredObjects            int `json:"monitoredObjects"`
	AutomationManagedMachines   int `json:"automationManagedMachines"`
	AutomationConcurrentDeploys int `json:"automationConcurrentDeployments"`
	LogIngestGiBPerDay          int `json:"logIngestGiBPerDay"`
	LogHotRetentionDays         int `json:"logHotRetentionDays"`
}

type AvailabilityRequirement struct {
	ManagementHostFailures int `json:"managementHostFailures"`
	PrimaryHostFailures    int `json:"primaryWorkloadHostFailures"`
	RecoveryHostFailures   int `json:"recoveryWorkloadHostFailures"`
	InterSiteRTTMs         int `json:"interSiteRttMs"`
	RPOSeconds             int `json:"rpoSeconds"`
	RTOMinutes             int `json:"rtoMinutes"`
}

type NetworkRequirement struct {
	Type       string `json:"type"`
	VLAN       int    `json:"vlan"`
	CIDR       string `json:"cidr"`
	Gateway    string `json:"gateway"`
	SubnetMask string `json:"subnetMask"`
	MTU        int    `json:"mtu"`
}

type DNSRequirement struct {
	Subdomain   string   `json:"subdomain"`
	Nameservers []string `json:"nameservers"`
}

type ManagementFQDNs struct {
	VCenter             string   `json:"vcenter"`
	SDDCManager         string   `json:"sddcManager"`
	NSXManagers         []string `json:"nsxManagers"`
	NSXVIP              string   `json:"nsxVip"`
	OperationsNodes     []string `json:"operationsNodes"`
	OperationsVIP       string   `json:"operationsVip"`
	OperationsCollector string   `json:"operationsCollector"`
	Automation          string   `json:"automation"`
	AutomationPlatform  string   `json:"automationPlatform"`
	ManagementPlatform  string   `json:"managementPlatform"`
	VCFInstance         string   `json:"vcfInstance"`
}

type Estate struct {
	EstateID   string            `json:"estateId"`
	Components []EstateComponent `json:"components"`
}

type EstateComponent struct {
	Name    string `json:"name"`
	Version string `json:"version"`
	Site    string `json:"site"`
}

type CompatibilitySnapshot struct {
	SnapshotID           string          `json:"snapshotId"`
	AsOf                 string          `json:"asOf"`
	SupportedCombination TargetVersions  `json:"supportedCombination"`
	Sizing               SizingAuthority `json:"sizing"`
	UpgradePlan          []UpgradeRule   `json:"upgradePlan"`
}

type TargetVersions struct {
	VCF                   string `json:"vcf"`
	VCenter               string `json:"vcenter"`
	ESXi                  string `json:"esxi"`
	NSX                   string `json:"nsx"`
	Operations            string `json:"vcfOperations"`
	Automation            string `json:"vcfAutomation"`
	LogManagement         string `json:"vcfLogManagement"`
	OperationsForNetworks string `json:"vcfOperationsForNetworks"`
}

type SizingAuthority struct {
	MinimumManagementHosts int                 `json:"minimumManagementHosts"`
	MinimumPrimaryHosts    int                 `json:"minimumPrimaryWorkloadHosts"`
	MinimumRecoveryHosts   int                 `json:"minimumRecoveryWorkloadHosts"`
	MaximumInterSiteRTTMs  int                 `json:"maximumInterSiteRttMs"`
	Operations             ProductSizing       `json:"vcfOperations"`
	Automation             ProductSizing       `json:"vcfAutomation"`
	LogManagement          LogManagementSizing `json:"vcfLogManagement"`
	ManagementPlatformIPs  int                 `json:"managementPlatformIpCount"`
	AutomationIPs          int                 `json:"automationIpCount"`
}

type ProductSizing struct {
	Size                     string `json:"size"`
	Nodes                    int    `json:"nodes"`
	MaximumManagedObjects    int    `json:"maximumManagedObjects,omitempty"`
	MaximumManagedMachines   int    `json:"maximumManagedMachines,omitempty"`
	MaximumConcurrentDeploys int    `json:"maximumConcurrentDeployments,omitempty"`
}

type LogManagementSizing struct {
	Size                    string `json:"size"`
	Replicas                int    `json:"replicas"`
	DeploymentModel         string `json:"deploymentModel"`
	MaximumIngestGiBPerDay  int    `json:"maximumIngestGiBPerDay"`
	MaximumHotRetentionDays int    `json:"maximumHotRetentionDays"`
}

type UpgradeRule struct {
	Order          int      `json:"order"`
	Component      string   `json:"component"`
	CurrentVersion string   `json:"currentVersion"`
	Target         string   `json:"target"`
	Action         string   `json:"action"`
	Gates          []string `json:"gates"`
}

type SddcSpec map[string]any

type GreenfieldArchitecture struct {
	SchemaVersion string                  `json:"schemaVersion"`
	DesignID      string                  `json:"designId"`
	Target        TargetVersions          `json:"target"`
	Sites         []ArchitectureSite      `json:"sites"`
	Domains       []DomainPlacement       `json:"domains"`
	Products      []ProductPlacement      `json:"products"`
	Capacity      CapacityRequirement     `json:"capacity"`
	Availability  AvailabilityRequirement `json:"availability"`
}

type ArchitectureSite struct {
	ID               string `json:"id"`
	Role             string `json:"role"`
	ManagementHosts  int    `json:"managementHosts"`
	WorkloadHosts    int    `json:"workloadHosts"`
	HostCores        int    `json:"hostCores"`
	HostMemoryGiB    int    `json:"hostMemoryGiB"`
	UsableStorageTiB int    `json:"usableStorageTiB"`
}

type DomainPlacement struct {
	Name                   string `json:"name"`
	Kind                   string `json:"kind"`
	Site                   string `json:"site"`
	HostCount              int    `json:"hostCount"`
	HostFailuresToTolerate int    `json:"hostFailuresToTolerate"`
}

type ProductPlacement struct {
	Name                  string   `json:"name"`
	Version               string   `json:"version"`
	Size                  string   `json:"size"`
	Nodes                 int      `json:"nodes"`
	Site                  string   `json:"site"`
	Domain                string   `json:"domain"`
	DeploymentModel       string   `json:"deploymentModel"`
	RemoteCollectors      []string `json:"remoteCollectors,omitempty"`
	ManagedObjects        int      `json:"managedObjects,omitempty"`
	ManagedMachines       int      `json:"managedMachines,omitempty"`
	ConcurrentDeployments int      `json:"concurrentDeployments,omitempty"`
	IngestGiBPerDay       int      `json:"ingestGiBPerDay,omitempty"`
	HotRetentionDays      int      `json:"hotRetentionDays,omitempty"`
}

type MigrationPlan struct {
	SchemaVersion  string          `json:"schemaVersion"`
	EstateID       string          `json:"estateId"`
	TargetVCF      string          `json:"targetVcfVersion"`
	SourceSnapshot string          `json:"sourceSnapshot"`
	Steps          []MigrationStep `json:"steps"`
}

type MigrationStep struct {
	Order          int      `json:"order"`
	Component      string   `json:"component"`
	CurrentVersion string   `json:"currentVersion"`
	Target         string   `json:"target"`
	Action         string   `json:"action"`
	Gates          []string `json:"gates"`
}

func LoadInputs(requirementsPath, estatePath, snapshotPath string) (Inputs, error) {
	var in Inputs
	for _, item := range []struct {
		path string
		dst  any
	}{
		{requirementsPath, &in.Requirements},
		{estatePath, &in.Estate},
		{snapshotPath, &in.Snapshot},
	} {
		b, err := os.ReadFile(item.path)
		if err != nil {
			return Inputs{}, fmt.Errorf("read %s: %w", item.path, err)
		}
		if err := json.Unmarshal(b, item.dst); err != nil {
			return Inputs{}, fmt.Errorf("decode %s: %w", item.path, err)
		}
	}
	return in, nil
}

func WriteArtifacts(outputDir string, sddc SddcSpec, architecture GreenfieldArchitecture, plan MigrationPlan) error {
	if err := os.MkdirAll(outputDir, 0o755); err != nil {
		return err
	}
	for _, item := range []struct {
		name  string
		value any
	}{
		{"greenfield-sddc.json", sddc},
		{"greenfield-architecture.json", architecture},
		{"migration-plan.json", plan},
	} {
		b, err := json.MarshalIndent(item.value, "", "  ")
		if err != nil {
			return fmt.Errorf("encode %s: %w", item.name, err)
		}
		b = append(b, '\n')
		if err := os.WriteFile(filepath.Join(outputDir, item.name), b, 0o644); err != nil {
			return fmt.Errorf("write %s: %w", item.name, err)
		}
	}
	return nil
}
