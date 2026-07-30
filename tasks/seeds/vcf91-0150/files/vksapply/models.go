// Package vksapply implements the focused VCF 9.1 Supervisor namespace and
// VKS server-side-apply contract pinned in docs/contract.json.
package vksapply

import (
	"fmt"
	"net/http"
	"time"
)

const (
	// NamespaceGetOperation is the exact vCenter OpenAPI operationId.
	NamespaceGetOperation = "Vcenter.Namespaces.Instances_getV2"
	// ClusterApplyOperation identifies the native Supervisor Kubernetes apply.
	ClusterApplyOperation = "cluster.x-k8s.io/v1beta2:namespaced-clusters:server-side-apply"
)

// Config describes the two authenticated HTTP boundaries used by Client.
type Config struct {
	VCenterURL      string
	KubernetesURL   string
	VCenterSession  string
	KubernetesToken string
	HTTPClient      *http.Client
	Timeout         time.Duration
}

// ApplyRequest is the supported VKS Cluster desired-state projection.
type ApplyRequest struct {
	Supervisor           string
	Namespace            string
	ClusterName          string
	FieldManager         string
	ClusterClass         string
	KubernetesVersion    string
	VMClass              string
	StorageClass         string
	ControlPlaneReplicas int32
	WorkerReplicas       *int32
	PodCIDRs             []string
	ServiceCIDRs         []string
	Force                *bool
}

// ApplyResult is the validated identity returned by Kubernetes.
type ApplyResult struct {
	UID             string
	ResourceVersion string
	Generation      int64
	Attempts        int
}

// ValidationError reports invalid local configuration or call input.
type ValidationError struct {
	Field  string
	Reason string
}

func (e *ValidationError) Error() string {
	return fmt.Sprintf("vksapply: invalid %s: %s", e.Field, e.Reason)
}

func (e ValidationError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

// APIError reports a non-success response from a named contract operation.
// Body is retained only for programmatic inspection.
type APIError struct {
	Operation  string
	StatusCode int
	Body       []byte
}

func (e *APIError) Error() string {
	return fmt.Sprintf("vksapply: %s returned HTTP %d", e.Operation, e.StatusCode)
}

func (e APIError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

// ProtocolError reports a success response that violates the focused contract.
type ProtocolError struct {
	Operation string
	Problem   string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("vksapply: malformed %s response: %s", e.Operation, e.Problem)
}

func (e ProtocolError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

// NamespaceNotReadyError reports a recognized non-running namespace state.
type NamespaceNotReadyError struct {
	ConfigStatus string
}

func (e *NamespaceNotReadyError) Error() string {
	return "vksapply: Supervisor namespace is not RUNNING"
}

func (e NamespaceNotReadyError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

// TransportError is a sanitized terminal request transport failure.
type TransportError struct {
	Operation string
	cause     error
}

func (e *TransportError) Error() string {
	return fmt.Sprintf("vksapply: %s transport failed", e.Operation)
}

func (e TransportError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

func (e *TransportError) Unwrap() error {
	return e.cause
}

func formatError(state fmt.State, verb rune, text string) {
	if verb == 'q' {
		_, _ = fmt.Fprintf(state, "%q", text)
		return
	}
	_, _ = state.Write([]byte(text))
}
