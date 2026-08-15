package architecture

import "encoding/json"

type Estate struct {
	EstateID   string     `json:"estateId"`
	Greenfield Greenfield `json:"greenfield"`
	Existing   Existing   `json:"existing"`
}

type Greenfield struct {
	TargetVCFVersion string     `json:"targetVcfVersion"`
	SDDCID           string     `json:"sddcId"`
	VCFInstanceName  string     `json:"vcfInstanceName"`
	Domain           string     `json:"domain"`
	DNS              []string   `json:"dns"`
	NTP              []string   `json:"ntp"`
	VCenterFQDN      string     `json:"vcenterFqdn"`
	SDDCManagerFQDN  string     `json:"sddcManagerFqdn"`
	NSXManagerFQDNs  []string   `json:"nsxManagerFqdns"`
	Management       Management `json:"management"`
	Networks         []Network  `json:"networks"`
	Sites            []Site     `json:"sites"`
	Edge             EdgeDemand `json:"edge"`
}

type Management struct {
	DatacenterName     string   `json:"datacenterName"`
	ClusterName        string   `json:"clusterName"`
	Hosts              []string `json:"hosts"`
	FailuresToTolerate int      `json:"failuresToTolerate"`
	VsanArchitecture   string   `json:"vsanArchitecture"`
	DVSName            string   `json:"dvsName"`
	DVSUplinks         []Uplink `json:"dvsUplinks"`
}

type Network struct {
	Type     string    `json:"type"`
	VLAN     int       `json:"vlan"`
	CIDR     string    `json:"cidr"`
	Gateway  string    `json:"gateway"`
	MTU      int       `json:"mtu"`
	IPRanges []IPRange `json:"ipRanges"`
}

type IPRange struct {
	Start string `json:"start"`
	End   string `json:"end"`
}

type Site struct {
	ID                  string `json:"id"`
	Role                string `json:"role"`
	AvailabilityZones   int    `json:"availabilityZones"`
	ManagementHostCount int    `json:"managementHostCount"`
	WorkloadHostCount   int    `json:"workloadHostCount"`
	ProtectedVMCapacity int    `json:"protectedVmCapacity"`
}

type EdgeDemand struct {
	SiteID                   string   `json:"siteId"`
	NorthSouthGbps           float64  `json:"northSouthGbps"`
	SurviveSingleNodeFailure bool     `json:"surviveSingleNodeFailure"`
	NodeCount                int      `json:"nodeCount"`
	HAMode                   string   `json:"haMode"`
	UplinkProfileID          string   `json:"uplinkProfileId"`
	PhysicalUplinks          []Uplink `json:"physicalUplinks"`
	TEPVLAN                  int      `json:"tepVlan"`
	TEPMTU                   int      `json:"tepMtu"`
	PlacementZones           []string `json:"placementZones"`
}

type Uplink struct {
	Name        string  `json:"name"`
	PhysicalNIC string  `json:"physicalNic"`
	SpeedGbps   float64 `json:"speedGbps"`
	TOR         string  `json:"tor"`
	VLAN        int     `json:"vlan,omitempty"`
}

type Existing struct {
	TargetVCFVersion string      `json:"targetVcfVersion"`
	Components       []Component `json:"components"`
}

type Component struct {
	Name           string `json:"name"`
	CurrentVersion string `json:"currentVersion"`
	TargetVersion  string `json:"targetVersion"`
}

type CompatibilitySnapshot struct {
	SnapshotID           string              `json:"snapshotId"`
	TargetCombination    map[string]string   `json:"targetCombination"`
	EdgeFormFactors      []EdgeFormFactor    `json:"edgeFormFactors"`
	UplinkProfiles       []UplinkProfile     `json:"uplinkProfiles"`
	SupportedUpgradeHops []UpgradeHop        `json:"supportedUpgradeHops"`
	UpgradeDependencies  []UpgradeDependency `json:"upgradeDependencies"`
}

type EdgeFormFactor struct {
	Name             string  `json:"name"`
	MinimumExclusive float64 `json:"minimumExclusiveGbps"`
	MaximumInclusive float64 `json:"maximumInclusiveGbps"`
	VCPU             int     `json:"vCpu"`
	MemoryGiB        int     `json:"memoryGiB"`
}

type UplinkProfile struct {
	ID                     string  `json:"id"`
	MinimumDemandExclusive float64 `json:"minimumDemandExclusiveGbps"`
	MaximumDemandInclusive float64 `json:"maximumDemandInclusiveGbps"`
	MinimumUplinksPerNode  int     `json:"minimumUplinksPerNode"`
	MinimumLinkSpeedGbps   float64 `json:"minimumLinkSpeedGbps"`
	RequireDistinctTORs    bool    `json:"requireDistinctTors"`
	RequireDistinctVLANs   bool    `json:"requireDistinctVlans"`
}

type UpgradeHop struct {
	Component     string   `json:"component"`
	FromVersion   string   `json:"fromVersion"`
	ToVersion     string   `json:"toVersion"`
	RequiredGates []string `json:"requiredGates"`
}

type UpgradeDependency struct {
	Component         string `json:"component"`
	ToVersion         string `json:"toVersion"`
	RequiresComponent string `json:"requiresComponent"`
	MinimumVersion    string `json:"minimumVersion"`
}

type EdgeDesign struct {
	SiteID               string     `json:"siteId"`
	Sites                []Site     `json:"sites"`
	FormFactor           string     `json:"formFactor"`
	VCPUPerNode          int        `json:"vCpuPerNode"`
	MemoryGiBPerNode     int        `json:"memoryGiBPerNode"`
	NodeCount            int        `json:"nodeCount"`
	HAMode               string     `json:"haMode"`
	NorthSouthGbps       float64    `json:"northSouthGbps"`
	PerSurvivingNodeGbps float64    `json:"perSurvivingNodeGbps"`
	UplinkProfileID      string     `json:"uplinkProfileId"`
	Nodes                []EdgeNode `json:"nodes"`
	TEP                  TEPDesign  `json:"tep"`
}

type EdgeNode struct {
	Name             string   `json:"name"`
	AvailabilityZone string   `json:"availabilityZone"`
	Uplinks          []Uplink `json:"uplinks"`
}

type TEPDesign struct {
	VLAN          int    `json:"vlan"`
	MTU           int    `json:"mtu"`
	TeamingPolicy string `json:"teamingPolicy"`
}

type MigrationPlan struct {
	EstateID         string          `json:"estateId"`
	TargetVCFVersion string          `json:"targetVcfVersion"`
	Steps            []MigrationStep `json:"steps"`
}

type MigrationStep struct {
	Order         int             `json:"order"`
	Component     string          `json:"component"`
	FromVersion   string          `json:"fromVersion"`
	TargetVersion string          `json:"targetVersion"`
	Gates         []MigrationGate `json:"gates"`
}

type MigrationGate struct {
	ID        string `json:"id"`
	Condition string `json:"condition"`
}

type Design struct {
	SddcSpec      json.RawMessage `json:"sddcSpec"`
	EdgeDesign    EdgeDesign      `json:"edgeDesign"`
	MigrationPlan MigrationPlan   `json:"migrationPlan"`
}
