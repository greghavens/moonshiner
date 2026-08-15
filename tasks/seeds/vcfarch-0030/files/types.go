package vcfarch

import (
	"encoding/json"
	"io"
)

type Requirements struct {
	DesignID      string       `json:"designId"`
	TargetVersion string       `json:"targetVersion"`
	Sites         []Site       `json:"sites"`
	Witness       WitnessInput `json:"witness"`
	Availability  Availability `json:"availability"`
	Demand        Demand       `json:"failoverDemand"`
	Network       NetworkInput `json:"network"`
}

type Site struct {
	Name             string `json:"name"`
	HostPrefix       string `json:"hostPrefix"`
	HostCount        int    `json:"hostCount"`
	CoresPerHost     int    `json:"coresPerHost"`
	MemoryGiBPerHost int    `json:"memoryGiBPerHost"`
	UsableTiBPerHost int    `json:"usableTiBPerHost"`
}

type WitnessInput struct {
	FailureDomain string `json:"failureDomain"`
	FQDN          string `json:"fqdn"`
}

type Availability struct {
	SiteFailuresToTolerate        int `json:"siteFailuresToTolerate"`
	HostFailuresToToleratePerSite int `json:"hostFailuresToToleratePerSite"`
}

type Demand struct {
	CPUCores  int `json:"cpuCores"`
	MemoryGiB int `json:"memoryGiB"`
	UsableTiB int `json:"usableTiB"`
}

type NetworkInput struct {
	DNSDomain      string   `json:"dnsDomain"`
	NameServers    []string `json:"nameServers"`
	NTPServers     []string `json:"ntpServers"`
	ManagementVLAN int      `json:"managementVlan"`
	VsanVLAN       int      `json:"vsanVlan"`
	VmotionVLAN    int      `json:"vmotionVlan"`
}

type Estate struct {
	EstateID   string               `json:"estateId"`
	Components []InventoryComponent `json:"components"`
}

type InventoryComponent struct {
	ID      string `json:"id"`
	Name    string `json:"name"`
	Version string `json:"version"`
}

type CompatibilitySnapshot struct {
	SnapshotVersion string           `json:"snapshotVersion"`
	TargetVersion   string           `json:"targetVersion"`
	Architecture    ArchitectureRule `json:"architecture"`
	Migration       []MigrationRule  `json:"migration"`
}

type ArchitectureRule struct {
	MinimumManagementHostsPerSite int  `json:"minimumManagementHostsPerSite"`
	Raid1HostsPerSiteBase         int  `json:"raid1HostsPerSiteBase"`
	Raid1HostsPerAdditionalFTT    int  `json:"raid1HostsPerAdditionalFtt"`
	RequiredDataSiteCount         int  `json:"requiredDataSiteCount"`
	WitnessOutsideDataSites       bool `json:"witnessOutsideDataSites"`
}

type MigrationRule struct {
	Order            int      `json:"order"`
	ComponentID      string   `json:"componentId"`
	SupportedSources []string `json:"supportedSources"`
	TargetVersion    string   `json:"targetVersion"`
	Gates            []Gate   `json:"gates"`
}

type Gate struct {
	ID                   string   `json:"id"`
	RequiresComponentIDs []string `json:"requiresComponentIds"`
	Condition            string   `json:"condition"`
}

type Artifact struct {
	DesignID                string           `json:"designId"`
	Greenfield              GreenfieldDesign `json:"greenfield"`
	ExistingEstateMigration MigrationPlan    `json:"existingEstateMigration"`
}

type GreenfieldDesign struct {
	SddcSpec     json.RawMessage    `json:"sddcSpec"`
	Topology     Topology           `json:"topology"`
	Availability Availability       `json:"availability"`
	Capacity     CapacityAssessment `json:"capacity"`
}

type Topology struct {
	DataSites []DataSitePlacement `json:"dataSites"`
	Witness   WitnessPlacement    `json:"witness"`
}

type DataSitePlacement struct {
	Name  string   `json:"name"`
	Hosts []string `json:"hosts"`
}

type WitnessPlacement struct {
	FQDN                    string `json:"fqdn"`
	FailureDomain           string `json:"failureDomain"`
	RunsOnManagementCluster bool   `json:"runsOnManagementCluster"`
}

type CapacityAssessment struct {
	SurvivingSiteCPUCores  int  `json:"survivingSiteCpuCores"`
	SurvivingSiteMemoryGiB int  `json:"survivingSiteMemoryGiB"`
	SurvivingSiteUsableTiB int  `json:"survivingSiteUsableTiB"`
	RequiredCPUCores       int  `json:"requiredCpuCores"`
	RequiredMemoryGiB      int  `json:"requiredMemoryGiB"`
	RequiredUsableTiB      int  `json:"requiredUsableTiB"`
	MeetsFailoverDemand    bool `json:"meetsFailoverDemand"`
}

type MigrationPlan struct {
	SchemaVersion    string          `json:"schemaVersion"`
	EstateID         string          `json:"estateId"`
	TargetVCFVersion string          `json:"targetVcfVersion"`
	Steps            []MigrationStep `json:"steps"`
}

type MigrationStep struct {
	Order         int    `json:"order"`
	ComponentID   string `json:"componentId"`
	Component     string `json:"component"`
	SourceVersion string `json:"sourceVersion"`
	TargetVersion string `json:"targetVersion"`
	Gates         []Gate `json:"gates"`
}

func LoadInputs(requirements io.Reader, estate io.Reader, compatibility io.Reader) (Requirements, Estate, CompatibilitySnapshot, error) {
	var req Requirements
	if err := json.NewDecoder(requirements).Decode(&req); err != nil {
		return Requirements{}, Estate{}, CompatibilitySnapshot{}, err
	}
	var est Estate
	if err := json.NewDecoder(estate).Decode(&est); err != nil {
		return Requirements{}, Estate{}, CompatibilitySnapshot{}, err
	}
	var snapshot CompatibilitySnapshot
	if err := json.NewDecoder(compatibility).Decode(&snapshot); err != nil {
		return Requirements{}, Estate{}, CompatibilitySnapshot{}, err
	}
	return req, est, snapshot, nil
}
