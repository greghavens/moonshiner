package architecture

// Estate is the fixed source inventory supplied to the planner.
type Estate struct {
	EstateID        string          `json:"estateId"`
	Site            string          `json:"site"`
	DeploymentModel string          `json:"deploymentModel"`
	Cluster         string          `json:"cluster"`
	Hosts           []Host          `json:"hosts"`
	Storage         Storage         `json:"storage"`
	Workload        Workload        `json:"workload"`
	Products        []EstateProduct `json:"products"`
}

type Host struct {
	ID        string `json:"id"`
	CPUCores  int    `json:"cpuCores"`
	MemoryGiB int    `json:"memoryGiB"`
}

type Storage struct {
	Policy             string `json:"policy"`
	FailuresToTolerate int    `json:"failuresToTolerate"`
}

type Workload struct {
	OperationsObjects            int `json:"operationsObjects"`
	OperationsMetrics            int `json:"operationsMetrics"`
	LogIngestGiBPerDay           int `json:"logIngestGiBPerDay"`
	AutomationConcurrentRequests int `json:"automationConcurrentRequests"`
}

type EstateProduct struct {
	InventoryID string   `json:"inventoryId"`
	Name        string   `json:"name"`
	Version     string   `json:"version"`
	Content     []string `json:"content"`
}

// CompatibilitySnapshot is the pinned, offline grading authority.
type CompatibilitySnapshot struct {
	SnapshotID              string            `json:"snapshotId"`
	AsOf                    string            `json:"asOf"`
	TargetFoundationVersion string            `json:"targetFoundationVersion"`
	Architecture            ArchitectureRules `json:"architecture"`
	Products                []ProductRule     `json:"products"`
	Sizing                  []SizingRule      `json:"sizing"`
}

type ArchitectureRules struct {
	SiteCount         int            `json:"siteCount"`
	DeploymentModel   string         `json:"deploymentModel"`
	MinimumHostCount  int            `json:"minimumHostCount"`
	MinimumHostsByFTT map[string]int `json:"minimumHostsByFtt"`
	RequiredStorage   string         `json:"requiredStorage"`
	PlacementCluster  string         `json:"placementCluster"`
}

type ProductRule struct {
	Order           int             `json:"order"`
	InventoryID     string          `json:"inventoryId"`
	SourceName      string          `json:"sourceName"`
	SourceVersion   string          `json:"sourceVersion"`
	TargetComponent string          `json:"targetComponent"`
	TargetVersion   string          `json:"targetVersion"`
	MigrationMethod string          `json:"migrationMethod"`
	SupportedPath   []string        `json:"supportedPath"`
	Content         ContentDecision `json:"content"`
	RequiredGates   []string        `json:"requiredGates"`
	Support         SupportBoundary `json:"support"`
}

type ContentDecision struct {
	Carries  []string `json:"carries"`
	Abandons []string `json:"abandons"`
}

type SupportBoundary struct {
	EndOfGeneralSupport string `json:"endOfGeneralSupport"`
	StatusAtSnapshot    string `json:"statusAtSnapshot"`
}

type SizingRule struct {
	Component        string   `json:"component"`
	Version          string   `json:"version"`
	Profile          string   `json:"profile"`
	NodeCount        int      `json:"nodeCount"`
	VCPUPerNode      int      `json:"vCpuPerNode"`
	MemoryGiBPerNode int      `json:"memoryGiBPerNode"`
	Capacity         Capacity `json:"capacity"`
}

type Capacity struct {
	Objects                      int `json:"objects,omitempty"`
	Metrics                      int `json:"metrics,omitempty"`
	IngestGiBPerDay              int `json:"ingestGiBPerDay,omitempty"`
	AutomationConcurrentRequests int `json:"automationConcurrentRequests,omitempty"`
}

// Plan is the machine-readable migration architecture emitted by BuildPlan.
type Plan struct {
	SchemaVersion string             `json:"schemaVersion"`
	SnapshotID    string             `json:"snapshotId"`
	EstateID      string             `json:"estateId"`
	Architecture  TargetArchitecture `json:"architecture"`
	Migrations    []Migration        `json:"migrations"`
}

type TargetArchitecture struct {
	Site               string      `json:"site"`
	DeploymentModel    string      `json:"deploymentModel"`
	FoundationVersion  string      `json:"foundationVersion"`
	Cluster            string      `json:"cluster"`
	HostCount          int         `json:"hostCount"`
	FailuresToTolerate int         `json:"failuresToTolerate"`
	StoragePolicy      string      `json:"storagePolicy"`
	Placements         []Placement `json:"placements"`
}

type Placement struct {
	Component        string   `json:"component"`
	Version          string   `json:"version"`
	Profile          string   `json:"profile"`
	Cluster          string   `json:"cluster"`
	NodeCount        int      `json:"nodeCount"`
	VCPUPerNode      int      `json:"vCpuPerNode"`
	MemoryGiBPerNode int      `json:"memoryGiBPerNode"`
	HostIDs          []string `json:"hostIds"`
	AntiAffinity     bool     `json:"antiAffinity"`
	Capacity         Capacity `json:"capacity"`
}

type ProductRef struct {
	InventoryID string `json:"inventoryId,omitempty"`
	Name        string `json:"name"`
	Version     string `json:"version"`
}

type Migration struct {
	Order   int             `json:"order"`
	Source  ProductRef      `json:"source"`
	Target  ProductRef      `json:"target"`
	Method  string          `json:"method"`
	Path    []string        `json:"path"`
	Content ContentDecision `json:"content"`
	Gates   []string        `json:"gates"`
	Support SupportBoundary `json:"support"`
}
