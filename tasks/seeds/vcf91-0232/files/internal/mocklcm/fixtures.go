package mocklcm

// InventoryComponent is one entry of the getComponents response.
type InventoryComponent struct {
	ID             string `json:"id"`
	ComponentType  string `json:"componentType"`
	DeploymentType string `json:"deploymentType"`
	Version        string `json:"version"`
	FQDN           string `json:"fqdn"`
	Scope          string `json:"scope"`
}

// DepotEntry is what the depot resolves one requested component to. An empty
// BinaryURL means the depot knows the component but published no binary for it,
// so the resolved entry carries no binaryUrl at all.
type DepotEntry struct {
	Component string
	Version   string
	BinaryURL string
}

// TaskError is one ERROR level message attached to a task or a task stage.
type TaskError struct {
	ID             string `json:"id"`
	DefaultMessage string `json:"defaultMessage"`
}

// TaskFailure describes how a task failed.
type TaskFailure struct {
	// Stage is the name of the stage that failed. Empty means no stage is
	// marked failed and the errors sit on the task itself.
	Stage  string
	Errors []TaskError
}

// TaskScript drives one task through the statuses the client will observe.
//
// Accepted is the status carried by the response that raised the task, Poll
// holds the statuses successive getTask calls return, and the last entry of Poll
// repeats for any further poll. OnRetry, when set, is the script the task
// switches to when retryTask is called; a nil OnRetry means the service refuses
// the retry.
type TaskScript struct {
	Accepted  string
	Poll      []string
	Retriable bool
	Failure   *TaskFailure
	OnRetry   *TaskScript
}

// Terminal task statuses, as the specification's TaskStatus enum spells them.
const (
	StatusPending   = "PENDING"
	StatusScheduled = "SCHEDULED"
	StatusRunning   = "RUNNING"
	StatusSucceeded = "SUCCEEDED"
	StatusFailed    = "FAILED"
	StatusCanceled  = "CANCELED"
)

// DefaultInventory is the component inventory the mock serves unless a test
// overrides it. VCF_OPERATIONS_FLEET_MANAGEMENT is already installed, so a plan
// naming it has nothing to do for it.
func DefaultInventory() []InventoryComponent {
	return []InventoryComponent{
		{
			ID:             "2f0f2a2c-6c1f-4a3e-9a1d-0a5b6c7d8e90",
			ComponentType:  "VCF_OPERATIONS_FLEET_MANAGEMENT",
			DeploymentType: "OVA",
			Version:        "9.1.0.0",
			FQDN:           "fleet-mgmt.vcf.example.com",
			Scope:          "FLEET",
		},
		{
			ID:             "8d3b1f44-2ec5-4bb8-9f0a-1c2d3e4f5061",
			ComponentType:  "VCF_IDENTITY_BROKER",
			DeploymentType: "OVA",
			Version:        "9.1.0.0",
			FQDN:           "idb.vcf.example.com",
			Scope:          "FLEET",
		},
	}
}

// DefaultDepot is the depot catalogue the mock resolves against. VCF_AUTOMATION
// resolves to a version but to no binary, which is what makes the resolved entry
// carry no binaryUrl.
func DefaultDepot() []DepotEntry {
	return []DepotEntry{
		{
			Component: "VCF_OPERATIONS",
			Version:   "9.1.0.0",
			BinaryURL: "https://depot.vcf.example.com/PROD/COMP/VCF_OPERATIONS/9.1.0.0/vcf-operations-9.1.0.0.ova",
		},
		{
			Component: "VCF_AUTOMATION",
			Version:   "9.1.0.0",
		},
		{
			Component: "VCF_OPERATIONS_FLEET_MANAGEMENT",
			Version:   "9.1.0.0",
			BinaryURL: "https://depot.vcf.example.com/PROD/COMP/VCF_OPERATIONS_FLEET_MANAGEMENT/9.1.0.0/vcf-fleet-management-9.1.0.0.ova",
		},
	}
}

// DefaultTaskScript is a task that runs for a while and then succeeds.
func DefaultTaskScript() TaskScript {
	return TaskScript{
		Accepted: StatusPending,
		Poll: []string{
			StatusPending,
			StatusRunning,
			StatusRunning,
			StatusSucceeded,
		},
	}
}

// FailThenSucceedScript fails the first attempt in a retriable way and succeeds
// after retryTask.
func FailThenSucceedScript() TaskScript {
	return TaskScript{
		Accepted:  StatusPending,
		Poll:      []string{StatusRunning, StatusFailed},
		Retriable: true,
		Failure: &TaskFailure{
			Stage: "package-deploy",
			Errors: []TaskError{
				{
					ID:             "com.broadcom.lcm.ops.component.deploy.transfer_failed",
					DefaultMessage: "Transfer of the component binary from the depot timed out.",
				},
			},
		},
		OnRetry: &TaskScript{
			Accepted: StatusScheduled,
			Poll:     []string{StatusRunning, StatusSucceeded},
		},
	}
}

// TerminalFailureScript fails in a way the service will not retry.
func TerminalFailureScript() TaskScript {
	return TaskScript{
		Accepted:  StatusPending,
		Poll:      []string{StatusRunning, StatusFailed},
		Retriable: false,
		Failure: &TaskFailure{
			Stage: "package-deploy",
			Errors: []TaskError{
				{
					ID:             "com.broadcom.lcm.ops.component.deploy.insufficient_capacity",
					DefaultMessage: "The target vCenter cluster does not have capacity for the appliance.",
				},
				{
					ID:             "com.broadcom.lcm.ops.component.deploy.rollback_done",
					DefaultMessage: "The partially deployed appliance was rolled back.",
				},
			},
		},
	}
}
