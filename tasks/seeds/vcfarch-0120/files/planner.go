package vcfarch

import (
	"encoding/json"
	"errors"
	"io"
)

// Inventory is the supplied current-state estate description.
type Inventory struct {
	EstateID      string               `json:"estateId"`
	Site          Site                 `json:"site"`
	TargetRelease string               `json:"targetRelease"`
	Design        Design               `json:"design"`
	Components    []InventoryComponent `json:"components"`
}

type Site struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

type Design struct {
	Model               string    `json:"model"`
	SddcID              string    `json:"sddcId"`
	VCFInstanceName     string    `json:"vcfInstanceName"`
	Domain              string    `json:"domain"`
	NameServers         []string  `json:"nameServers"`
	NTPServers          []string  `json:"ntpServers"`
	VCenterHostname     string    `json:"vcenterHostname"`
	VCenterRootPassword string    `json:"vcenterRootPassword"`
	DatacenterName      string    `json:"datacenterName"`
	ClusterName         string    `json:"clusterName"`
	Hosts               []string  `json:"hosts"`
	NSXManagers         []string  `json:"nsxManagers"`
	NSXVIPFQDN          string    `json:"nsxVipFqdn"`
	NSXTransportVLAN    int       `json:"nsxTransportVlan"`
	SDDCManagerHostname string    `json:"sddcManagerHostname"`
	ManagementPoolName  string    `json:"managementPoolName"`
	ExistingDatastore   string    `json:"existingDatastore"`
	Networks            []Network `json:"networks"`
}

type Network struct {
	Type       string   `json:"type"`
	VLANID     int      `json:"vlanId"`
	MTU        int      `json:"mtu"`
	Subnet     string   `json:"subnet"`
	Gateway    string   `json:"gateway"`
	SubnetMask string   `json:"subnetMask"`
	Addresses  []string `json:"addresses"`
}

type InventoryComponent struct {
	ID      string `json:"id"`
	Name    string `json:"name"`
	Version string `json:"version"`
}

// CompatibilitySnapshot is the immutable compatibility authority bundled with the task.
type CompatibilitySnapshot struct {
	SnapshotID       string                 `json:"snapshotId"`
	AsOf             string                 `json:"asOf"`
	TargetRelease    string                 `json:"targetRelease"`
	Architecture     ArchitectureRule       `json:"architecture"`
	Components       []ComponentRule        `json:"components"`
	Precedence       []PrecedenceRule       `json:"precedence"`
	Interoperability []InteroperabilityRule `json:"interoperability"`
}

type ArchitectureRule struct {
	SiteCount        int    `json:"siteCount"`
	Model            string `json:"model"`
	Storage          string `json:"storage"`
	MinimumHostCount int    `json:"minimumHostCount"`
}

type ComponentRule struct {
	ID            string        `json:"id"`
	TargetVersion string        `json:"targetVersion"`
	UpgradeEdges  []UpgradeEdge `json:"upgradeEdges"`
}

type UpgradeEdge struct {
	From   string `json:"from"`
	To     string `json:"to"`
	Action string `json:"action"`
}

type PrecedenceRule struct {
	Before string `json:"before"`
	After  string `json:"after"`
}

type InteroperabilityRule struct {
	LeftComponent  string `json:"leftComponent"`
	LeftVersion    string `json:"leftVersion"`
	RightComponent string `json:"rightComponent"`
	RightVersion   string `json:"rightVersion"`
}

// Artifact is serialized directly as an installer SddcSpec. The X-prefixed
// fields carry the architecture facts and brownfield plan that OpenAPI permits
// as extensions.
type Artifact struct {
	SddcID                      string              `json:"sddcId"`
	WorkflowType                string              `json:"workflowType"`
	Version                     string              `json:"version"`
	HostSpecs                   []HostSpec          `json:"hostSpecs"`
	VCenterSpec                 VCenterSpec         `json:"vcenterSpec"`
	ClusterSpec                 ClusterSpec         `json:"clusterSpec"`
	NSXTSpec                    NSXTSpec            `json:"nsxtSpec"`
	NetworkSpecs                []NetworkSpec       `json:"networkSpecs"`
	DNSSpec                     DNSSpec             `json:"dnsSpec"`
	NTPServers                  []string            `json:"ntpServers"`
	SDDCManagerSpec             SDDCManagerSpec     `json:"sddcManagerSpec"`
	ManagementPoolName          string              `json:"managementPoolName"`
	DatastoreSpec               DatastoreSpec       `json:"datastoreSpec"`
	VCFInstanceName             string              `json:"vcfInstanceName"`
	SkipESXThumbprintValidation bool                `json:"skipEsxThumbprintValidation"`
	SkipGatewayPingValidation   bool                `json:"skipGatewayPingValidation"`
	Architecture                ArchitectureSummary `json:"x-architecture"`
	MigrationPlan               MigrationPlan       `json:"x-migrationPlan"`
}

type HostSpec struct {
	Hostname string `json:"hostname"`
}

type VCenterSpec struct {
	VCenterHostname     string `json:"vcenterHostname"`
	RootVCenterPassword string `json:"rootVcenterPassword"`
	Version             string `json:"version"`
	UseExisting         bool   `json:"useExistingDeployment"`
}

type ClusterSpec struct {
	DatacenterName string `json:"datacenterName"`
	ClusterName    string `json:"clusterName"`
}

type NSXTSpec struct {
	Managers        []NSXTManagerSpec `json:"nsxtManagers"`
	VIPFQDN         string            `json:"vipFqdn"`
	TransportVLANID int               `json:"transportVlanId"`
	Version         string            `json:"version"`
	UseExisting     bool              `json:"useExistingDeployment"`
}

type NSXTManagerSpec struct {
	Hostname string `json:"hostname"`
}

type NetworkSpec struct {
	NetworkType      string   `json:"networkType"`
	VLANID           int      `json:"vlanId"`
	MTU              int      `json:"mtu"`
	Subnet           string   `json:"subnet"`
	Gateway          string   `json:"gateway"`
	SubnetMask       string   `json:"subnetMask"`
	IncludeAddresses []string `json:"includeIpAddress"`
}

type DNSSpec struct {
	Subdomain   string   `json:"subdomain"`
	Nameservers []string `json:"nameservers"`
}

type SDDCManagerSpec struct {
	Hostname    string `json:"hostname"`
	Version     string `json:"version"`
	UseExisting bool   `json:"useExistingDeployment"`
}

type DatastoreSpec struct {
	ExistingDatastoreName string `json:"existingDatastoreName"`
}

type ArchitectureSummary struct {
	SiteCount        int    `json:"siteCount"`
	SiteID           string `json:"siteId"`
	Model            string `json:"model"`
	ManagementDomain string `json:"managementDomain"`
	HostCount        int    `json:"hostCount"`
	Storage          string `json:"storage"`
}

type MigrationPlan struct {
	SchemaVersion string          `json:"schemaVersion"`
	EstateID      string          `json:"estateId"`
	TargetRelease string          `json:"targetRelease"`
	SiteID        string          `json:"siteId"`
	Model         string          `json:"model"`
	Steps         []MigrationStep `json:"steps"`
}

type MigrationStep struct {
	Order          int      `json:"order"`
	StepID         string   `json:"stepId"`
	ComponentID    string   `json:"componentId"`
	Component      string   `json:"component"`
	CurrentVersion string   `json:"currentVersion"`
	TargetVersion  string   `json:"targetVersion"`
	Action         string   `json:"action"`
	Gates          []string `json:"gates"`
}

func LoadInventory(r io.Reader) (Inventory, error) {
	var inventory Inventory
	err := json.NewDecoder(r).Decode(&inventory)
	return inventory, err
}

func LoadCompatibility(r io.Reader) (CompatibilitySnapshot, error) {
	var snapshot CompatibilitySnapshot
	err := json.NewDecoder(r).Decode(&snapshot)
	return snapshot, err
}

// Build constructs the SddcSpec and ordered migration plan.
func Build(Inventory, CompatibilitySnapshot) (Artifact, error) {
	return Artifact{}, errors.New("vcf architecture planner is not implemented")
}
