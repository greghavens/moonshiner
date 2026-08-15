package architecture

import "encoding/json"

type Artifact struct {
	SchemaVersion      string             `json:"schemaVersion"`
	ScenarioID         string             `json:"scenarioId"`
	SelectedTopology   TopologyDecision   `json:"selectedTopology"`
	ExcludedTopologies []ExcludedTopology `json:"excludedTopologies"`
	SddcSpec           json.RawMessage    `json:"sddcSpec"`
	DayN               DayNPlan           `json:"dayN"`
	MigrationPlan      MigrationPlan      `json:"migrationPlan"`
}

type TopologyDecision struct {
	ID                    string              `json:"id"`
	VCFInstances          int                 `json:"vcfInstances"`
	ManagementDomains     int                 `json:"managementDomains"`
	DataSites             []string            `json:"dataSites"`
	WitnessSite           string              `json:"witnessSite"`
	HostPlacement         map[string][]string `json:"hostPlacement"`
	CapacityAfterSiteLoss Capacity            `json:"capacityAfterSiteLoss"`
}

type ExcludedTopology struct {
	ID           string `json:"id"`
	ReasonCode   string `json:"reasonCode"`
	ConstraintID string `json:"constraintId"`
}

type Capacity struct {
	PhysicalCores    int     `json:"physicalCores"`
	MemoryGiB        int     `json:"memoryGiB"`
	UsableStorageTiB float64 `json:"usableStorageTiB"`
}

type DayNPlan struct {
	Action      string   `json:"action"`
	AddHosts    []string `json:"addHosts"`
	WitnessHost string   `json:"witnessHost"`
}

type MigrationPlan struct {
	SchemaVersion    string          `json:"schemaVersion"`
	EstateID         string          `json:"estateId"`
	TargetVCFVersion string          `json:"targetVcfVersion"`
	Steps            []MigrationStep `json:"steps"`
}

type MigrationStep struct {
	Order         int      `json:"order"`
	ComponentID   string   `json:"componentId"`
	ComponentName string   `json:"componentName"`
	FromVersion   string   `json:"fromVersion"`
	Action        string   `json:"action"`
	TargetVersion string   `json:"targetVersion"`
	Gates         []string `json:"gates"`
}

type Scenario struct {
	ScenarioID               string                   `json:"scenarioId"`
	TargetRelease            string                   `json:"targetRelease"`
	CapacityRequirements     Capacity                 `json:"capacityRequirements"`
	AvailabilityRequirements AvailabilityRequirements `json:"availabilityRequirements"`
	Sites                    []Site                   `json:"sites"`
	Links                    []Link                   `json:"links"`
	Hosts                    []Host                   `json:"hosts"`
	Entitlement              Entitlement              `json:"entitlement"`
	InstallerInputs          InstallerInputs          `json:"installerInputs"`
	ExistingEstate           ExistingEstate           `json:"existingEstate"`
}

type AvailabilityRequirements struct {
	SurviveCompleteDataSiteLoss  bool `json:"surviveCompleteDataSiteLoss"`
	MaxRPOMinutes                int  `json:"maxRpoMinutes"`
	MaxManagementRecoveryMinutes int  `json:"maxManagementRecoveryMinutes"`
}

type Site struct {
	ID          string `json:"id"`
	Role        string `json:"role"`
	WitnessHost string `json:"witnessHost,omitempty"`
}

type Link struct {
	From          string `json:"from"`
	To            string `json:"to"`
	RTTMillis     int    `json:"rttMillis"`
	BandwidthGbps int    `json:"bandwidthGbps"`
}

type Host struct {
	Hostname      string  `json:"hostname"`
	Site          string  `json:"site"`
	Cores         int     `json:"cores"`
	MemoryGiB     int     `json:"memoryGiB"`
	RawStorageTiB float64 `json:"rawStorageTiB"`
}

type Entitlement struct {
	ConstraintID          string `json:"constraintId"`
	EligibleTargetRelease string `json:"eligibleTargetRelease"`
	MaxVCFInstances       int    `json:"maxVcfInstances"`
	MaxLicensedHosts      int    `json:"maxLicensedHosts"`
	VSANStretchedClusters bool   `json:"vsanStretchedClusters"`
}

type InstallerInputs struct {
	SddcID                string    `json:"sddcId"`
	VCFInstanceName       string    `json:"vcfInstanceName"`
	InitialSite           string    `json:"initialSite"`
	Subdomain             string    `json:"subdomain"`
	NameServers           []string  `json:"nameServers"`
	NTPServers            []string  `json:"ntpServers"`
	VCenterHostname       string    `json:"vcenterHostname"`
	SDDCManagerHostname   string    `json:"sddcManagerHostname"`
	NSXManagerHostnames   []string  `json:"nsxManagerHostnames"`
	NSXVIPFQDN            string    `json:"nsxVipFqdn"`
	LicenseServerHostname string    `json:"licenseServerHostname"`
	VSPPlatformFQDN       string    `json:"vspPlatformFqdn"`
	VSPInstanceFQDN       string    `json:"vspInstanceFqdn"`
	VSPFleetFQDN          string    `json:"vspFleetFqdn"`
	ManagementServiceIPs  []string  `json:"managementServiceIps"`
	DatacenterName        string    `json:"datacenterName"`
	ClusterName           string    `json:"clusterName"`
	ManagementPoolName    string    `json:"managementPoolName"`
	Networks              []Network `json:"networks"`
}

type Network struct {
	Type       string    `json:"type"`
	VLAN       int       `json:"vlan"`
	CIDR       string    `json:"cidr"`
	Gateway    string    `json:"gateway"`
	SubnetMask string    `json:"subnetMask"`
	MTU        int       `json:"mtu"`
	IPRanges   []IPRange `json:"ipRanges"`
}

type IPRange struct {
	Start string `json:"start"`
	End   string `json:"end"`
}

type ExistingEstate struct {
	EstateID   string              `json:"estateId"`
	Components []ExistingComponent `json:"components"`
}

type ExistingComponent struct {
	ID      string `json:"id"`
	Name    string `json:"name"`
	Version string `json:"version"`
}

type CompatibilitySnapshot struct {
	SnapshotVersion  string              `json:"snapshotVersion"`
	AsOf             string              `json:"asOf"`
	TargetRelease    string              `json:"targetRelease"`
	Topologies       []SupportedTopology `json:"topologies"`
	InstallerProfile InstallerProfile    `json:"installerProfile"`
	Migration        PinnedMigration     `json:"migration"`
}

type SupportedTopology struct {
	ID                             string  `json:"id"`
	VCFInstances                   int     `json:"vcfInstances"`
	ManagementDomains              int     `json:"managementDomains"`
	DataSites                      int     `json:"dataSites"`
	WitnessSites                   int     `json:"witnessSites"`
	MinHostsPerDataSite            int     `json:"minHostsPerDataSite"`
	RequiresVSANStretchEntitlement bool    `json:"requiresVsanStretchEntitlement"`
	MaxDataSiteRTTMillis           int     `json:"maxDataSiteRttMillis"`
	MaxWitnessRTTMillis            int     `json:"maxWitnessRttMillis"`
	SurvivesCompleteDataSiteLoss   bool    `json:"survivesCompleteDataSiteLoss"`
	RPOMinutes                     int     `json:"rpoMinutes"`
	ManagementRecoveryMinutes      int     `json:"managementRecoveryMinutes"`
	UsableStorageTiB               float64 `json:"usableStorageTiB"`
}

type InstallerProfile struct {
	WorkflowType            string   `json:"workflowType"`
	InitialHostCount        int      `json:"initialHostCount"`
	RequiredNetworkTypes    []string `json:"requiredNetworkTypes"`
	MinManagementServiceIPs int      `json:"minManagementServiceIps"`
	NSXManagerCount         int      `json:"nsxManagerCount"`
	VCFOperationsNodeCount  int      `json:"vcfOperationsNodeCount"`
	VSANFailuresToTolerate  int      `json:"vsanFailuresToTolerate"`
}

type PinnedMigration struct {
	SourceVCFVersion string                `json:"sourceVcfVersion"`
	TargetVCFVersion string                `json:"targetVcfVersion"`
	Steps            []PinnedMigrationStep `json:"steps"`
}

type PinnedMigrationStep struct {
	Order         int      `json:"order"`
	ComponentID   string   `json:"componentId"`
	TargetVersion string   `json:"targetVersion"`
	Action        string   `json:"action"`
	Gates         []string `json:"gates"`
}
