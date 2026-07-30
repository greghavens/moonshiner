// Package vksprovision implements one focused VCF 9.1 Supervisor and VKS
// provisioning workflow.
package vksprovision

import (
	"fmt"
	"net/http"
	"time"
)

const (
	// NamespaceCreateOperation is the exact vCenter OpenAPI operationId.
	NamespaceCreateOperation = "Vcenter.Namespaces.Instances_createV2"
	// ClusterCreateOperation identifies the Supervisor Kubernetes create.
	ClusterCreateOperation = "cluster.x-k8s.io/v1beta2:namespaced-clusters:create"
	// ClusterGetOperation identifies the Supervisor Kubernetes get.
	ClusterGetOperation = "cluster.x-k8s.io/v1beta2:namespaced-clusters:get"
)

// Config configures the focused client.
type Config struct {
	VCenterURL      string
	KubernetesURL   string
	VCenterSession  string
	KubernetesToken string
	HTTPClient      *http.Client
	Timeout         time.Duration
	PollInterval    time.Duration
	MaxPolls        int
}

// ProvisionRequest is the supported projection of the vCenter namespace
// CreateSpecV2 and a topology-managed VKS Cluster.
type ProvisionRequest struct {
	Supervisor           string
	Namespace            string
	NamespaceDescription string
	ClusterName          string
	ClusterClass         string
	KubernetesVersion    string
	VMClass              string
	StorageClass         string
	ControlPlaneReplicas int32
	WorkerReplicas       *int32
	PodCIDRs             []string
	ServiceCIDRs         []string
}

// ProvisionResult contains the successful terminal observation.
type ProvisionResult struct {
	Namespace        string
	ClusterName      string
	ResourceVersion  string
	AvailableReason  string
	AvailableMessage string
	PollCount        int
}

// ValidationError reports a local input error.
type ValidationError struct {
	Field  string
	Reason string
}

func (e *ValidationError) Error() string {
	return fmt.Sprintf("invalid %s: %s", e.Field, e.Reason)
}

func (e *ValidationError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

// APIError is a non-success response. Body is intentionally retained outside
// the formatted error string for programmatic inspection.
type APIError struct {
	Operation  string
	StatusCode int
	Body       []byte
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s failed with HTTP %d", e.Operation, e.StatusCode)
}

func (e *APIError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

// ProtocolError reports a success response that violates the focused contract.
type ProtocolError struct {
	Operation string
	Reason    string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("%s returned an invalid response: %s", e.Operation, e.Reason)
}

func (e *ProtocolError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

// TransportError reports a sanitized request transport failure.
type TransportError struct {
	Operation string
	cause     error
}

func (e *TransportError) Error() string {
	return fmt.Sprintf("%s transport failed", e.Operation)
}

func (e *TransportError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

func (e *TransportError) Unwrap() error {
	return e.cause
}

// ClusterFailedError reports the terminal ProvisioningFailed condition.
type ClusterFailedError struct {
	Namespace string
	Name      string
	Reason    string
	Message   string
}

func (e *ClusterFailedError) Error() string {
	return "VKS Cluster reached terminal ProvisioningFailed"
}

func (e *ClusterFailedError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

// PollLimitError reports bounded exhaustion while the Cluster remains
// nonterminal.
type PollLimitError struct {
	Namespace string
	Name      string
	MaxPolls  int
}

func (e *PollLimitError) Error() string {
	return fmt.Sprintf("VKS Cluster remained nonterminal after %d polls", e.MaxPolls)
}

func (e *PollLimitError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

func formatError(state fmt.State, verb rune, text string) {
	if verb == 'q' {
		_, _ = fmt.Fprintf(state, "%q", text)
		return
	}
	_, _ = state.Write([]byte(text))
}

// Client performs the three contract-named operations.
type Client struct {
	vcenterURL    string
	kubernetesURL string
	session       string
	token         string
	httpClient    *http.Client
	pollInterval  time.Duration
	maxPolls      int
}
