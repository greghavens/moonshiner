package migrationplan

type Plan struct {
	SchemaVersion string            `json:"schema_version"`
	PlanID        string            `json:"plan_id"`
	InventoryID   string            `json:"inventory_id"`
	SnapshotID    string            `json:"snapshot_id"`
	Objective     Objective         `json:"objective"`
	Sources       []SourceMigration `json:"sources"`
	Placements    []Placement       `json:"placements"`
	Gates         []Gate            `json:"gates"`
	Steps         []Step            `json:"steps"`
}

type Objective struct {
	ManagementDomainID     string `json:"management_domain_id"`
	ManagementDomainChange string `json:"management_domain_change"`
	WorkloadDomainID       string `json:"workload_domain_id"`
}

type SourceMigration struct {
	SourceID        string            `json:"source_id"`
	SourceProduct   string            `json:"source_product"`
	SourceVersion   string            `json:"source_version"`
	TargetComponent string            `json:"target_component"`
	TargetVersion   string            `json:"target_version"`
	MigrationMode   string            `json:"migration_mode"`
	Support         SupportBoundary   `json:"support"`
	Items           []ItemDisposition `json:"items"`
}

type SupportBoundary struct {
	EndOfGeneralSupport string `json:"end_of_general_support"`
	StatusAtSnapshot    string `json:"status_at_snapshot"`
	Boundary            string `json:"boundary"`
}

type ItemDisposition struct {
	ItemID      string `json:"item_id"`
	Disposition string `json:"disposition"`
	Method      string `json:"method"`
}

type Placement struct {
	Component     string   `json:"component"`
	Version       string   `json:"version"`
	DomainID      string   `json:"domain_id"`
	ClusterID     string   `json:"cluster_id"`
	NetworkID     string   `json:"network_id"`
	Topology      Topology `json:"topology"`
	CapacityBasis string   `json:"capacity_basis"`
}

type Topology struct {
	Nodes            int    `json:"nodes"`
	Size             string `json:"size"`
	VCPUPerNode      int    `json:"vcpu_per_node"`
	MemoryGiBPerNode int    `json:"memory_gib_per_node"`
	DiskGiBPerNode   int    `json:"disk_gib_per_node"`
}

type Gate struct {
	ID        string `json:"id"`
	Kind      string `json:"kind"`
	Assertion string `json:"assertion"`
	Evidence  string `json:"evidence"`
}

type Step struct {
	Order     int      `json:"order"`
	ID        string   `json:"id"`
	Component string   `json:"component"`
	Action    string   `json:"action"`
	Requires  []string `json:"requires"`
	Produces  []string `json:"produces"`
	Rollback  string   `json:"rollback"`
}
