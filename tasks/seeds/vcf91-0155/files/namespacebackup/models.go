// Package namespacebackup coordinates a vSphere Supervisor backup with
// inventory reads from the VKS Kubernetes API.
package namespacebackup

import (
	"fmt"
	"net/http"
	"time"
)

const (
	OperationGetNamespace = "Vcenter.Namespaces.Instances_getV2"
	OperationCreateBackup = "Vcenter.NamespaceManagement.Supervisors.Recovery.Backup.Jobs_create"
	OperationGetTask      = "Cis.Tasks_get"
	OperationListClusters = "cluster.x-k8s.io/v1beta2:namespaced-clusters:list"
)

// Config configures a Client. Construction must not perform network traffic.
type Config struct {
	VCenterURL      string
	KubernetesURL   string
	SessionID       string
	KubernetesToken string
	HTTPClient      *http.Client
	PollInterval    time.Duration
	MaxPolls        int
}

// BackupRequest selects a vSphere Namespace. Comment is omitted when nil.
type BackupRequest struct {
	Namespace string
	Comment   *string
}

// Cluster is the stable projection of a VKS Cluster resource.
type Cluster struct {
	Name            string `json:"name"`
	TopologyVersion string `json:"topologyVersion"`
}

// BackupResult describes the terminal Supervisor backup task.
type BackupResult struct {
	OperationID     string `json:"operationId"`
	TaskOperationID string `json:"taskOperationId"`
	TaskID          string `json:"taskId"`
	Status          string `json:"status"`
	PollCount       int    `json:"pollCount"`
	Result          any    `json:"result"`
}

// Result is returned only after the backup task succeeds and the inventory is
// confirmed stable.
type Result struct {
	Namespace  string       `json:"namespace"`
	Supervisor string       `json:"supervisor"`
	Clusters   []Cluster    `json:"clusters"`
	Backup     BackupResult `json:"backup"`
}

// Client is safe for concurrent use after construction.
type Client struct {
	vcenterURL      string
	kubernetesURL   string
	sessionID       string
	kubernetesToken string
	httpClient      *http.Client
	pollInterval    time.Duration
	maxPolls        int
}

// APIError represents an HTTP or transport failure. Its message intentionally
// excludes credentials, response data, and lower-level transport text.
type APIError struct {
	OperationID string
	StatusCode  int
}

func (e *APIError) Error() string {
	if e.StatusCode == 0 {
		return fmt.Sprintf("%s request failed", e.OperationID)
	}
	return fmt.Sprintf("%s returned HTTP %d", e.OperationID, e.StatusCode)
}

// ProtocolError represents a malformed successful response or inconsistent
// cross-system evidence.
type ProtocolError struct {
	OperationID string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("%s returned invalid protocol data", e.OperationID)
}

// NamespaceNotReadyError reports a recognized, non-running namespace state.
type NamespaceNotReadyError struct {
	Status string
}

func (e *NamespaceNotReadyError) Error() string {
	return "vSphere Namespace is not RUNNING"
}

// TaskFailedError reports a terminal failed backup without exposing task error
// or result content.
type TaskFailedError struct {
	TaskID string
	Status string
}

func (e *TaskFailedError) Error() string {
	return "Supervisor backup task failed"
}

// PollTimeoutError reports exhaustion of the configured task-read budget.
type PollTimeoutError struct {
	TaskID   string
	MaxPolls int
}

func (e *PollTimeoutError) Error() string {
	return "Supervisor backup task did not reach a terminal state"
}
