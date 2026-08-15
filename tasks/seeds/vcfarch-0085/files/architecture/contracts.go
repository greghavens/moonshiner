package vcfarchitecture

// Inventory is the fixed description of the estate being migrated.
type Inventory struct {
	SchemaVersion  string               `json:"schemaVersion"`
	EstateID       string               `json:"estateId"`
	SDDCID         string               `json:"sddcId"`
	SourceRelease  string               `json:"sourceRelease"`
	TargetRelease  string               `json:"targetRelease"`
	Site           SiteInventory        `json:"site"`
	Cluster        ClusterInventory     `json:"cluster"`
	Hosts          []HostInventory      `json:"hosts"`
	DNS            DNSInventory         `json:"dns"`
	NTPServers     []string             `json:"ntpServers"`
	Networks       []NetworkInventory   `json:"networks"`
	Components     []InventoryComponent `json:"components"`
	FixtureSecrets FixtureSecrets       `json:"fixtureSecrets"`
}

type SiteInventory struct {
	Name                 string `json:"name"`
	SiteCount            int    `json:"siteCount"`
	AvailabilityZoneMode string `json:"availabilityZoneMode"`
	DesignModel          string `json:"designModel"`
}

type ClusterInventory struct {
	DatacenterName string `json:"datacenterName"`
	ClusterName    string `json:"clusterName"`
	DatastoreName  string `json:"datastoreName"`
	StorageType    string `json:"storageType"`
}

type HostInventory struct {
	ID            string `json:"id"`
	ShortHostname string `json:"shortHostname"`
	FQDN          string `json:"fqdn"`
	ESXVersion    string `json:"esxVersion"`
}

type DNSInventory struct {
	Subdomain   string   `json:"subdomain"`
	Nameservers []string `json:"nameservers"`
}

type NetworkInventory struct {
	NetworkType            string    `json:"networkType"`
	VLANID                 int       `json:"vlanId"`
	MTU                    int       `json:"mtu"`
	Subnet                 string    `json:"subnet"`
	Gateway                string    `json:"gateway"`
	IncludeIPAddressRanges []IPRange `json:"includeIpAddressRanges,omitempty"`
}

type IPRange struct {
	StartIPAddress string `json:"startIpAddress"`
	EndIPAddress   string `json:"endIpAddress"`
}

type InventoryComponent struct {
	ID             string   `json:"id"`
	Product        string   `json:"product"`
	CurrentVersion string   `json:"currentVersion"`
	CurrentState   string   `json:"currentState"`
	Endpoints      []string `json:"endpoints,omitempty"`
	VIPFQDN        string   `json:"vipFqdn,omitempty"`
}

type FixtureSecrets struct {
	VCenterRootPassword string `json:"vcenterRootPassword"`
}

// CompatibilitySnapshot is the pinned, deterministic compatibility authority.
type CompatibilitySnapshot struct {
	SchemaVersion          string               `json:"schemaVersion"`
	SnapshotDate           string               `json:"snapshotDate"`
	MinimumHostConstraints []HostConstraint     `json:"minimumHostConstraints"`
	ReleaseHops            []ReleaseHop         `json:"releaseHops"`
	GateCatalog            []GateDefinition     `json:"gateCatalog"`
	Stages                 []CompatibilityStage `json:"stages"`
}

type HostConstraint struct {
	DesignModel          string `json:"designModel"`
	SiteCount            int    `json:"siteCount"`
	AvailabilityZoneMode string `json:"availabilityZoneMode"`
	StorageType          string `json:"storageType"`
	MinimumHosts         int    `json:"minimumHosts"`
}

type ReleaseHop struct {
	From      string `json:"from"`
	To        string `json:"to"`
	Supported bool   `json:"supported"`
	Rationale string `json:"rationale"`
}

type CompatibilityStage struct {
	Sequence  int                   `json:"sequence"`
	ID        string                `json:"id"`
	Mechanism string                `json:"mechanism"`
	Changes   []CompatibilityChange `json:"changes"`
}

type CompatibilityChange struct {
	ComponentID   string   `json:"componentId"`
	FromVersion   string   `json:"fromVersion"`
	FromState     string   `json:"fromState"`
	TargetVersion string   `json:"targetVersion"`
	TargetState   string   `json:"targetState"`
	Gates         []string `json:"gates"`
}

// MigrationPlan is the machine-readable architecture emitted by BuildPlan.
type MigrationPlan struct {
	SchemaVersion   string           `json:"schemaVersion"`
	EstateID        string           `json:"estateId"`
	SourceRelease   string           `json:"sourceRelease"`
	TargetRelease   string           `json:"targetRelease"`
	Topology        PlanTopology     `json:"topology"`
	ReleaseHops     []ReleaseHop     `json:"releaseHops"`
	GateDefinitions []GateDefinition `json:"gateDefinitions"`
	Stages          []MigrationStage `json:"stages"`
	TargetSddcSpec  map[string]any   `json:"targetSddcSpec"`
}

type PlanTopology struct {
	SiteCount            int    `json:"siteCount"`
	AvailabilityZoneMode string `json:"availabilityZoneMode"`
	DesignModel          string `json:"designModel"`
	StorageType          string `json:"storageType"`
	HostCount            int    `json:"hostCount"`
}

type GateDefinition struct {
	ID        string `json:"id"`
	Condition string `json:"condition"`
}

type MigrationStage struct {
	Sequence  int               `json:"sequence"`
	ID        string            `json:"id"`
	Mechanism string            `json:"mechanism"`
	Changes   []ComponentChange `json:"changes"`
}

type ComponentChange struct {
	ComponentID   string   `json:"componentId"`
	FromVersion   string   `json:"fromVersion"`
	FromState     string   `json:"fromState"`
	TargetVersion string   `json:"targetVersion"`
	TargetState   string   `json:"targetState"`
	Gates         []string `json:"gates"`
}
