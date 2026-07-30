// Package vcenter implements the reduced VCF 9.1 vCenter inventory contract.
package vcenter

import (
	"fmt"
	"net/http"
	"sync"
)

// PowerState is a Vcenter.Vm.Power.State value used by VM summaries and filters.
type PowerState string

const (
	PowerStatePoweredOff PowerState = "POWERED_OFF"
	PowerStatePoweredOn  PowerState = "POWERED_ON"
	PowerStateSuspended  PowerState = "SUSPENDED"
)

// DatacenterFilter contains optional Vcenter.Datacenter_list query fields.
type DatacenterFilter struct {
	Datacenters []string
	Names       []string
	Folders     []string
}

// VMFilter contains optional Vcenter.VM_list query fields.
type VMFilter struct {
	VMs           []string
	Names         []string
	Folders       []string
	Datacenters   []string
	Hosts         []string
	Clusters      []string
	ResourcePools []string
	PowerStates   []PowerState
}

// DatacenterSummary is a Vcenter.Datacenter.Summary.
type DatacenterSummary struct {
	Datacenter string `json:"datacenter"`
	Name       string `json:"name"`
}

// VMSummary is a Vcenter.VM.Summary.
type VMSummary struct {
	VM            string     `json:"vm"`
	Name          string     `json:"name"`
	PowerState    PowerState `json:"power_state"`
	CPUCount      *int64     `json:"cpu_count,omitempty"`
	MemorySizeMiB *int64     `json:"memory_size_mib,omitempty"`
}

// InventorySnapshot is the result (or partial result) of CollectInventory.
type InventorySnapshot struct {
	Datacenters []DatacenterSummary
	VMs         []VMSummary
}

// APIError represents an HTTP response or transport failure.
type APIError struct {
	OperationID string
	StatusCode  int
	Payload     any
}

func (e *APIError) Error() string {
	if e.StatusCode == 0 {
		return fmt.Sprintf("vcenter: %s transport failure", e.OperationID)
	}
	return fmt.Sprintf("vcenter: %s returned HTTP %d", e.OperationID, e.StatusCode)
}

// ProtocolError represents a successful response outside the focused contract.
type ProtocolError struct {
	OperationID string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("vcenter: %s returned an invalid response", e.OperationID)
}

// Client calls the three operations in docs/contract.json.
type Client struct {
	baseURL    string
	username   string
	password   string
	httpClient *http.Client

	sessionMu sync.Mutex
	sessionID string
}
