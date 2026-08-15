package vcfarchitecture

// Scenario is the fixed greenfield capacity, topology, naming, and network input.
type Scenario struct {
	ID           string       `json:"id"`
	VCFVersion   string       `json:"vcfVersion"`
	Site         Site         `json:"site"`
	Topology     Topology     `json:"topology"`
	HostProfile  HostProfile  `json:"hostProfile"`
	Capacity     Capacity     `json:"capacity"`
	Availability Availability `json:"availability"`
	Names        Names        `json:"names"`
	Network      Network      `json:"network"`
	Storage      Storage      `json:"storage"`
	VSP          VSP          `json:"vsp"`
	Placeholder  Placeholder  `json:"placeholderCredentials"`
}

type Site struct {
	ID             string `json:"id"`
	FailureDomains int    `json:"failureDomains"`
}

type Topology struct {
	Architecture   string `json:"architecture"`
	AvailableHosts int    `json:"availableHosts"`
	Stretched      bool   `json:"stretched"`
}

type HostProfile struct {
	CPUCores           int `json:"cpuCores"`
	MemoryGiB          int `json:"memoryGiB"`
	ProtectedUsableTiB int `json:"protectedUsableTiB"`
}

type Capacity struct {
	WorkloadCPUCoreMinimum       int `json:"workloadCpuCoreMinimum"`
	WorkloadMemoryGiBMinimum     int `json:"workloadMemoryGiBMinimum"`
	WorkloadStorageTiBMinimum    int `json:"workloadStorageTiBMinimum"`
	ManagementReservedCores      int `json:"managementReservedCores"`
	ManagementReservedMemoryGiB  int `json:"managementReservedMemoryGiB"`
	ManagementReservedStorageTiB int `json:"managementReservedStorageTiB"`
}

type Availability struct {
	HostFailuresToTolerate int `json:"hostFailuresToTolerate"`
	SiteFailuresToTolerate int `json:"siteFailuresToTolerate"`
}

type Names struct {
	Domain                    string   `json:"domain"`
	SDDCID                    string   `json:"sddcId"`
	InstanceName              string   `json:"instanceName"`
	Hostnames                 []string `json:"hostnames"`
	VCenter                   string   `json:"vcenter"`
	SDDCManager               string   `json:"sddcManager"`
	NSXManagers               []string `json:"nsxManagers"`
	NSXVIP                    string   `json:"nsxVip"`
	VSPPlatform               string   `json:"vspPlatform"`
	VSPInstance               string   `json:"vspInstance"`
	VSPFleet                  string   `json:"vspFleet"`
	VCFOperationsNodes        []string `json:"vcfOperationsNodes"`
	VCFOperationsLoadBalancer string   `json:"vcfOperationsLoadBalancer"`
	LicenseServer             string   `json:"licenseServer"`
	Datacenter                string   `json:"datacenter"`
	Cluster                   string   `json:"cluster"`
	DVS                       string   `json:"dvs"`
	Datastore                 string   `json:"datastore"`
}

type Network struct {
	DNS           []string         `json:"dns"`
	NTP           []string         `json:"ntp"`
	TransportVLAN int              `json:"transportVlan"`
	Segments      []NetworkSegment `json:"segments"`
	Uplinks       []Uplink         `json:"uplinks"`
}

type NetworkSegment struct {
	Type       string `json:"type"`
	VLAN       int    `json:"vlan"`
	CIDR       string `json:"cidr"`
	Gateway    string `json:"gateway"`
	SubnetMask string `json:"subnetMask"`
	MTU        int    `json:"mtu"`
	StartIP    string `json:"startIp"`
	EndIP      string `json:"endIp"`
}

type Uplink struct {
	VMNIC  string `json:"vmnic"`
	Uplink string `json:"uplink"`
}

type Storage struct {
	Type               string `json:"type"`
	ESA                bool   `json:"esa"`
	FailuresToTolerate int    `json:"failuresToTolerate"`
}

type VSP struct {
	PoolCIDR                string   `json:"poolCidr"`
	Addresses               []string `json:"addresses"`
	Size                    string   `json:"size"`
	InternalClusterCIDRIPv4 string   `json:"internalClusterCidrIpv4"`
}

type Placeholder struct {
	VCenterRoot string `json:"vcenterRoot"`
}

type Estate struct {
	EstateID   string            `json:"estateId"`
	Components []EstateComponent `json:"components"`
}

type EstateComponent struct {
	ID      string `json:"id"`
	Name    string `json:"name"`
	Version string `json:"version"`
}

type CompatibilitySnapshot struct {
	SnapshotID            string            `json:"snapshotId"`
	TargetVCFVersion      string            `json:"targetVcfVersion"`
	MinimumSupportedHosts int               `json:"minimumSupportedHosts"`
	SupportedCombination  map[string]string `json:"supportedCombination"`
	MigrationPaths        []MigrationPath   `json:"migrationPaths"`
}

type MigrationPath struct {
	Order           int      `json:"order"`
	ComponentID     string   `json:"componentId"`
	FromVersion     string   `json:"fromVersion"`
	TargetComponent string   `json:"targetComponent"`
	TargetVersion   string   `json:"targetVersion"`
	Action          string   `json:"action"`
	Gates           []string `json:"gates"`
}

type MigrationPlan struct {
	EstateID         string          `json:"estateId"`
	TargetVCFVersion string          `json:"targetVcfVersion"`
	Steps            []MigrationStep `json:"steps"`
}

type MigrationStep struct {
	Order           int      `json:"order"`
	ComponentID     string   `json:"componentId"`
	ComponentName   string   `json:"componentName"`
	FromVersion     string   `json:"fromVersion"`
	TargetComponent string   `json:"targetComponent"`
	TargetVersion   string   `json:"targetVersion"`
	Action          string   `json:"action"`
	Gates           []string `json:"gates"`
}
