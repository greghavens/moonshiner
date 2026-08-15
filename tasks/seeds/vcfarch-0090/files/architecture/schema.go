package architecture

import "encoding/json"

// Plan is the machine-readable brownfield architecture contract.
type Plan struct {
	SchemaVersion    string          `json:"schemaVersion"`
	EstateID         string          `json:"estateId"`
	SourceVCFVersion string          `json:"sourceVcfVersion"`
	TargetVCFVersion string          `json:"targetVcfVersion"`
	TargetSddcSpec   json.RawMessage `json:"targetSddcSpec"`
	StorageDecision  StorageDecision `json:"storageDecision"`
	MigrationSteps   []MigrationStep `json:"migrationSteps"`
}

type StorageDecision struct {
	SourceArchitecture         string `json:"sourceArchitecture"`
	SelectedArchitecture       string `json:"selectedArchitecture"`
	MigrationMode              string `json:"migrationMode"`
	SourceHostCount            int    `json:"sourceHostCount"`
	TargetHostCount            int    `json:"targetHostCount"`
	RAIDLayout                 string `json:"raidLayout"`
	FailuresToTolerate         int    `json:"failuresToTolerate"`
	VsanVLANID                 int    `json:"vsanVlanId"`
	VsanMTU                    int    `json:"vsanMtu"`
	MinAggregateNICGbpsPerHost int    `json:"minAggregateNicGbpsPerHost"`
}

type MigrationStep struct {
	Order          int    `json:"order"`
	ComponentID    string `json:"componentId"`
	Component      string `json:"component"`
	CurrentVersion string `json:"currentVersion"`
	TargetVersion  string `json:"targetVersion"`
	Action         string `json:"action"`
	Gates          []Gate `json:"gates"`
}

type Gate struct {
	ID        string `json:"id"`
	Condition string `json:"condition"`
}
