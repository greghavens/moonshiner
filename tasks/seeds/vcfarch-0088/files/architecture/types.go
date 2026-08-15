package architecture

import (
	"encoding/json"
	"errors"
	"io"
)

var ErrNotImplemented = errors.New("architecture validation not implemented")

type Plan struct {
	SddcID         string          `json:"sddcId"`
	Version        string          `json:"version"`
	WorkflowType   string          `json:"workflowType"`
	DNS            DNSConfig       `json:"dnsSpec"`
	Networks       []NetworkConfig `json:"networkSpecs"`
	VCenter        VCenterConfig   `json:"vcenterSpec"`
	EstateID       string          `json:"estateId"`
	TargetVCF      string          `json:"targetVcfVersion"`
	Components     []PlanComponent `json:"components"`
	EdgeDesign     EdgeDesign      `json:"edgeDesign"`
	MigrationSteps []MigrationStep `json:"migrationSteps"`
}

type DNSConfig struct {
	Subdomain   string   `json:"subdomain"`
	Nameservers []string `json:"nameservers,omitempty"`
}

type NetworkConfig struct {
	NetworkType string `json:"networkType"`
	VLANID      int    `json:"vlanId"`
	MTU         int    `json:"mtu,omitempty"`
}

type VCenterConfig struct {
	Hostname              string `json:"vcenterHostname"`
	RootVCenterPassword   string `json:"rootVcenterPassword"`
	UseExistingDeployment bool   `json:"useExistingDeployment"`
}

type PlanComponent struct {
	ID             string   `json:"id"`
	CurrentVersion string   `json:"currentVersion"`
	TargetVersion  string   `json:"targetVersion"`
	Gates          []string `json:"gates"`
}

type EdgeDesign struct {
	RequiredThroughputGbps int          `json:"requiredThroughputGbps"`
	FormFactor             string       `json:"formFactor"`
	NodeCount              int          `json:"nodeCount"`
	HAMode                 string       `json:"haMode"`
	UplinksPerNode         int          `json:"uplinksPerNode"`
	Uplinks                []EdgeUplink `json:"uplinks"`
}

type EdgeUplink struct {
	Name        string   `json:"name"`
	PhysicalNIC string   `json:"physicalNic"`
	Fabric      string   `json:"fabric"`
	Switch      string   `json:"switch"`
	SpeedGbps   int      `json:"speedGbps"`
	Roles       []string `json:"roles"`
}

type MigrationStep struct {
	Order       int      `json:"order"`
	VCFRelease  string   `json:"vcfRelease"`
	Component   string   `json:"component"`
	FromVersion string   `json:"fromVersion"`
	ToVersion   string   `json:"toVersion"`
	Gates       []string `json:"gates"`
}

type Inventory struct {
	EstateID       string               `json:"estateId"`
	VCFVersion     string               `json:"vcfVersion"`
	TargetVCF      string               `json:"targetVcfVersion"`
	SddcID         string               `json:"sddcId"`
	DNSSubdomain   string               `json:"dnsSubdomain"`
	Nameservers    []string             `json:"nameservers"`
	ManagementVLAN int                  `json:"managementVlanId"`
	VCenter        InventoryVCenter     `json:"vcenter"`
	Components     []InventoryComponent `json:"components"`
	DesignInputs   DesignInputs         `json:"designInputs"`
}

type InventoryVCenter struct {
	Hostname string `json:"hostname"`
	Password string `json:"password"`
}

type InventoryComponent struct {
	ID      string `json:"id"`
	Version string `json:"version"`
}

type DesignInputs struct {
	RequiredThroughputGbps int               `json:"requiredNorthSouthThroughputGbps"`
	Availability           string            `json:"availability"`
	AvailableUplinks       []AvailableUplink `json:"availableUplinksPerEdgeNode"`
}

type AvailableUplink struct {
	PhysicalNIC string `json:"physicalNic"`
	Fabric      string `json:"fabric"`
	Switch      string `json:"switch"`
	SpeedGbps   int    `json:"speedGbps"`
}

type Snapshot struct {
	SchemaVersion        string                `json:"schemaVersion"`
	TargetVCF            string                `json:"targetVcfVersion"`
	VCFHops              []VCFHop              `json:"vcfHops"`
	Gates                []Gate                `json:"gates"`
	ComponentTargets     []ComponentTarget     `json:"componentTargets"`
	ComponentTransitions []ComponentTransition `json:"componentTransitions"`
	EdgeSizing           []EdgeSizingBand      `json:"edgeSizing"`
	EdgeConstraints      EdgeConstraints       `json:"edgeConstraints"`
}

type VCFHop struct {
	From string `json:"from"`
	To   string `json:"to"`
}

type Gate struct {
	ID          string `json:"id"`
	Description string `json:"description"`
}

type ComponentTarget struct {
	ID            string   `json:"id"`
	TargetVersion string   `json:"targetVersion"`
	RequiredGates []string `json:"requiredGates"`
}

type ComponentTransition struct {
	Order         int      `json:"order"`
	VCFRelease    string   `json:"vcfRelease"`
	Component     string   `json:"component"`
	FromVersion   string   `json:"fromVersion"`
	ToVersion     string   `json:"toVersion"`
	RequiredGates []string `json:"requiredGates"`
}

type EdgeSizingBand struct {
	FormFactor        string `json:"formFactor"`
	MaxThroughputGbps int    `json:"maxThroughputGbps"`
}

type EdgeConstraints struct {
	NodeCount              int      `json:"nodeCount"`
	HAMode                 string   `json:"haMode"`
	UplinksPerNode         int      `json:"uplinksPerNode"`
	DistinctFabrics        bool     `json:"distinctFabrics"`
	DistinctSwitches       bool     `json:"distinctSwitches"`
	SingleUplinkSurvivable bool     `json:"singleUplinkSurvivable"`
	RequiredRoles          []string `json:"requiredRolesPerUplink"`
}

func LoadPlan(r io.Reader) (Plan, error) {
	var plan Plan
	decoder := json.NewDecoder(r)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&plan); err != nil {
		return Plan{}, err
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		return Plan{}, errors.New("architecture contains trailing JSON")
	}
	return plan, nil
}

func Validate(Plan, Inventory, Snapshot) error {
	return ErrNotImplemented
}
