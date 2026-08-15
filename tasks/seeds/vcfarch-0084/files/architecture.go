package vcfarch

// Inventory is the fixed brownfield estate supplied in testdata/estate.json.
type Inventory struct {
	EstateID           string           `json:"estateId"`
	VCFVersion         string           `json:"vcfVersion"`
	ManagementDomain   ManagementDomain `json:"managementDomain"`
	Sites              []Site           `json:"sites"`
	IndependentWitness string           `json:"independentWitnessSite"`
	Components         []Component      `json:"components"`
	DNS                DNS              `json:"dns"`
	NTPServers         []string         `json:"ntpServers"`
	Networks           []Network        `json:"networks"`
	VCenter            VCenterInventory `json:"vcenter"`
}

type ManagementDomain struct {
	ID                 string   `json:"id"`
	ClusterID          string   `json:"clusterId"`
	Stretched          bool     `json:"stretched"`
	DataSites          []string `json:"dataSites"`
	WitnessComponentID string   `json:"witnessComponentId"`
	CurrentWitnessSite string   `json:"currentWitnessSite"`
	DatastoreName      string   `json:"datastoreName"`
}

type Site struct {
	ID            string `json:"id"`
	Role          string `json:"role"`
	FailureDomain string `json:"failureDomain"`
}

type Component struct {
	ID      string `json:"id"`
	Type    string `json:"type"`
	Version string `json:"version"`
	Site    string `json:"site"`
}

type DNS struct {
	Subdomain   string   `json:"subdomain"`
	Nameservers []string `json:"nameservers"`
}

type Network struct {
	NetworkType string `json:"networkType"`
	VLANID      int    `json:"vlanId"`
	Subnet      string `json:"subnet"`
	Gateway     string `json:"gateway"`
	SubnetMask  string `json:"subnetMask"`
	MTU         int    `json:"mtu"`
}

type VCenterInventory struct {
	Hostname      string `json:"hostname"`
	SSLThumbprint string `json:"sslThumbprint"`
}

// CompatibilitySnapshot is the complete, pinned offline grading authority.
type CompatibilitySnapshot struct {
	SchemaVersion int           `json:"schemaVersion"`
	CapturedOn    string        `json:"capturedOn"`
	SourceVCF     string        `json:"sourceVcfVersion"`
	TargetVCF     string        `json:"targetVcfVersion"`
	UpgradeEdges  []UpgradeEdge `json:"upgradeEdges"`
}

type UpgradeEdge struct {
	FromVCF          string            `json:"fromVcfVersion"`
	ToVCF            string            `json:"toVcfVersion"`
	ComponentTargets map[string]string `json:"componentTargets"`
	Sequence         []SequenceRule    `json:"sequence"`
}

type SequenceRule struct {
	ComponentType string   `json:"componentType"`
	Rank          int      `json:"rank"`
	Gates         []string `json:"gates"`
}

// Architecture is an SddcSpec with the seed-defined migration-plan extension.
type Architecture struct {
	SddcID        string            `json:"sddcId"`
	WorkflowType  string            `json:"workflowType"`
	Version       string            `json:"version"`
	VCenterSpec   SddcVCenterSpec   `json:"vcenterSpec"`
	ClusterSpec   SddcClusterSpec   `json:"clusterSpec"`
	NetworkSpecs  []Network         `json:"networkSpecs"`
	DNSSpec       DNS               `json:"dnsSpec"`
	NTPServers    []string          `json:"ntpServers"`
	DatastoreSpec SddcDatastoreSpec `json:"datastoreSpec"`
	MigrationPlan MigrationPlan     `json:"x-migrationPlan"`
}

type SddcVCenterSpec struct {
	VCenterHostname       string `json:"vcenterHostname"`
	RootVCenterPassword   string `json:"rootVcenterPassword"`
	Version               string `json:"version"`
	UseExistingDeployment bool   `json:"useExistingDeployment"`
	SSLThumbprint         string `json:"sslThumbprint"`
}

type SddcClusterSpec struct {
	DatacenterName string `json:"datacenterName,omitempty"`
	ClusterName    string `json:"clusterName"`
}

type SddcDatastoreSpec struct {
	ExistingDatastoreName string `json:"existingDatastoreName"`
}

type MigrationPlan struct {
	EstateID         string         `json:"estateId"`
	SourceVCFVersion string         `json:"sourceVcfVersion"`
	TargetVCFVersion string         `json:"targetVcfVersion"`
	UpgradePath      []string       `json:"upgradePath"`
	TargetTopology   TargetTopology `json:"targetTopology"`
	Steps            []PlanStep     `json:"steps"`
}

type TargetTopology struct {
	ManagementDomainID string        `json:"managementDomainId"`
	ClusterID          string        `json:"clusterId"`
	Stretched          bool          `json:"stretched"`
	DataSites          []string      `json:"dataSites"`
	Witness            TargetWitness `json:"witness"`
}

type TargetWitness struct {
	ComponentID   string `json:"componentId"`
	SiteID        string `json:"siteId"`
	FailureDomain string `json:"failureDomain"`
}

type PlanStep struct {
	Order         int      `json:"order"`
	Phase         int      `json:"phase"`
	ComponentID   string   `json:"componentId"`
	ComponentType string   `json:"componentType"`
	FromVersion   string   `json:"fromVersion"`
	ToVersion     string   `json:"toVersion"`
	FromSite      string   `json:"fromSite"`
	ToSite        string   `json:"toSite"`
	Gates         []string `json:"gates"`
}
