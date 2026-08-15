package planner

import "encoding/json"

type VersionRef struct {
	Product string `json:"product,omitempty"`
	Version string `json:"version"`
	Build   string `json:"build"`
}

type Fleet struct {
	ID               string `json:"id"`
	Version          string `json:"version"`
	ManagementDomain struct {
		ID        string `json:"id"`
		Version   string `json:"version"`
		Immutable bool   `json:"immutable"`
	} `json:"managementDomain"`
}

type Component struct {
	ID       string `json:"id"`
	Kind     string `json:"kind"`
	Name     string `json:"name"`
	Version  string `json:"version"`
	Build    string `json:"build"`
	Hostname string `json:"hostname,omitempty"`
}

type Network struct {
	Type     string    `json:"type"`
	VLAN     int       `json:"vlan"`
	Subnet   string    `json:"subnet"`
	Gateway  string    `json:"gateway"`
	Mask     string    `json:"mask"`
	MTU      int       `json:"mtu"`
	IPRanges []IPRange `json:"ipRanges,omitempty"`
}

type IPRange struct {
	Start string `json:"start"`
	End   string `json:"end"`
}

type Inventory struct {
	SchemaVersion string      `json:"schemaVersion"`
	EstateID      string      `json:"estateId"`
	DesiredDomain string      `json:"desiredDomainId"`
	Fleet         Fleet       `json:"fleet"`
	Components    []Component `json:"components"`
	Topology      struct {
		ClusterName          string            `json:"clusterName"`
		DatacenterName       string            `json:"datacenterName"`
		DatastoreName        string            `json:"datastoreName"`
		DNSDomain            string            `json:"dnsDomain"`
		NameServers          []string          `json:"nameServers"`
		NTPServers           []string          `json:"ntpServers"`
		Networks             []Network         `json:"networks"`
		VCenterHostname      string            `json:"vcenterHostname"`
		VCenterSSLThumbprint string            `json:"vcenterSslThumbprint"`
		NSXVIPFQDN           string            `json:"nsxVipFqdn"`
		NSXManagers          []string          `json:"nsxManagers"`
		NSXSSLThumbprint     string            `json:"nsxSslThumbprint"`
		ESXiSSLThumbprints   map[string]string `json:"esxiSslThumbprints"`
	} `json:"topology"`
}

type TargetRule struct {
	Kind    string `json:"kind"`
	Product string `json:"product"`
	Version string `json:"version"`
	Build   string `json:"build"`
}

type TransitionRule struct {
	Kind          string       `json:"kind"`
	FromVersion   string       `json:"fromVersion"`
	FromBuild     string       `json:"fromBuild"`
	Disposition   string       `json:"disposition"`
	Via           []VersionRef `json:"via"`
	RequiredGates []string     `json:"requiredGates"`
}

type GateRule struct {
	ID        string `json:"id"`
	Condition string `json:"condition"`
	Evidence  string `json:"evidence"`
}

type OperationRule struct {
	Order          int      `json:"order"`
	ID             string   `json:"id"`
	Operation      string   `json:"operation"`
	ComponentKinds []string `json:"componentKinds"`
	RequiredGates  []string `json:"requiredGates"`
	DependsOn      []string `json:"dependsOn"`
	Scope          string   `json:"scope"`
}

type OrderRule struct {
	Before string `json:"before"`
	After  string `json:"after"`
}

type Snapshot struct {
	SchemaVersion string           `json:"schemaVersion"`
	TargetVCF     string           `json:"targetVcf"`
	Targets       []TargetRule     `json:"targets"`
	Transitions   []TransitionRule `json:"transitions"`
	Gates         []GateRule       `json:"gates"`
	Operations    []OperationRule  `json:"operations"`
	OrderRules    []OrderRule      `json:"orderRules"`
}

type ComponentPlan struct {
	ID          string       `json:"id"`
	Kind        string       `json:"kind"`
	Name        string       `json:"name"`
	Source      VersionRef   `json:"source"`
	Target      VersionRef   `json:"target"`
	Disposition string       `json:"disposition"`
	Via         []VersionRef `json:"via"`
	Gates       []string     `json:"gates"`
}

type Gate struct {
	ID        string `json:"id"`
	Condition string `json:"condition"`
	Evidence  string `json:"evidence"`
}

type Step struct {
	Order        int      `json:"order"`
	ID           string   `json:"id"`
	Operation    string   `json:"operation"`
	Scope        string   `json:"scope"`
	ComponentIDs []string `json:"componentIds"`
	Gates        []string `json:"gates"`
	DependsOn    []string `json:"dependsOn"`
}

type Plan struct {
	SchemaVersion          string          `json:"schemaVersion"`
	DesignType             string          `json:"designType"`
	EstateID               string          `json:"estateId"`
	Fleet                  FleetPlan       `json:"fleet"`
	ManagementDomainImpact Impact          `json:"managementDomainImpact"`
	TargetSddcSpec         json.RawMessage `json:"targetSddcSpec"`
	Components             []ComponentPlan `json:"components"`
	Gates                  []Gate          `json:"gates"`
	Steps                  []Step          `json:"steps"`
}

type FleetPlan struct {
	ID             string `json:"id"`
	CurrentVersion string `json:"currentVersion"`
	TargetVersion  string `json:"targetVersion"`
}

type Impact struct {
	Change             string `json:"change"`
	ManagementDomainID string `json:"managementDomainId"`
}
