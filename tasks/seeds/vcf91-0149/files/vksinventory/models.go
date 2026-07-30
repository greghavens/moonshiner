// Package vksinventory implements the focused VCF 9.1 Supervisor namespace
// and VKS Cluster collection contract pinned in docs/contract.json.
package vksinventory

import (
	"fmt"
	"net/http"
)

// Config describes the two authenticated HTTP boundaries used by Client.
type Config struct {
	VCenterURL       string
	Namespace        string
	SessionID        string
	KubernetesScheme string
	PageLimit        int64
	HTTPClient       *http.Client
}

// Cluster is the stable public projection of one VKS Cluster resource.
type Cluster struct {
	Name            string
	UID             string
	ResourceVersion string
}

// APIError represents a non-success status from a named contract operation.
// Response content is intentionally not retained.
type APIError struct {
	Operation  string
	StatusCode int
}

func (e *APIError) Error() string {
	return fmt.Sprintf("vksinventory: %s returned HTTP %d", e.Operation, e.StatusCode)
}

// ProtocolError represents a malformed success response.
type ProtocolError struct {
	Operation string
	Problem   string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("vksinventory: malformed %s response: %s", e.Operation, e.Problem)
}
