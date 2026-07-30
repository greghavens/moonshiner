// Package vksguard implements the focused VCF 9.1 Supervisor namespace
// precheck and VKS Cluster mutation contract pinned in docs/contract.json.
package vksguard

import (
	"fmt"
	"net/http"
)

// Config describes the two separately authenticated HTTP boundaries.
type Config struct {
	VCenterURL       string
	KubernetesURL    string
	VCenterSessionID string
	KubernetesToken  string
	HTTPClient       *http.Client
}

// Result records the gate and mutation outcome.
type Result struct {
	Status   string
	Changed  bool
	Precheck PrecheckResult
	Mutation MutationResult
}

// PrecheckResult identifies the vCenter operation and its decision.
type PrecheckResult struct {
	OperationID  string
	Passed       bool
	ConfigStatus string
}

// MutationResult identifies the Kubernetes operation and whether it ran.
type MutationResult struct {
	OperationKey string
	Attempted    bool
}

// APIError represents a non-success status from a named contract operation.
// Response content is intentionally not retained.
type APIError struct {
	Operation  string
	StatusCode int
}

func (e *APIError) Error() string {
	return fmt.Sprintf("vksguard: %s returned HTTP %d", e.Operation, e.StatusCode)
}

// ProtocolError represents malformed success data.
type ProtocolError struct {
	Operation string
	Problem   string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("vksguard: malformed %s response: %s", e.Operation, e.Problem)
}

// PrecheckError represents a namespace/Supervisor ownership mismatch.
type PrecheckError struct {
	Problem string
}

func (e *PrecheckError) Error() string {
	return "vksguard: precheck failed: " + e.Problem
}
