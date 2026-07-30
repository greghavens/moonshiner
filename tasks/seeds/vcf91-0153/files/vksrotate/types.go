// Package vksrotate reads Supervisor namespace and VKS Cluster state while
// allowing callers to rotate both API credentials as one atomic generation.
package vksrotate

import (
	"fmt"
	"net/http"
	"sync"
)

const (
	OperationGetSupervisorNamespace = "getSupervisorNamespace"
	OperationListVKSClusters        = "listVksClusters"
)

type Credentials struct {
	VCenterSessionID      string
	KubernetesBearerToken string
}

type Config struct {
	VCenterURL    string
	KubernetesURL string
	Credentials   Credentials
	HTTPClient    *http.Client
}

type Cluster struct {
	Name            string
	Namespace       string
	UID             string
	ResourceVersion string
}

type Snapshot struct {
	Namespace    string
	Supervisor   string
	ConfigStatus string
	Clusters     []Cluster
}

// APIError reports an HTTP failure without exposing a response body.
type APIError struct {
	Operation  string
	StatusCode int
}

func (e *APIError) Error() string {
	return fmt.Sprintf("vksrotate: %s returned HTTP %d", e.Operation, e.StatusCode)
}

// ProtocolError reports invalid success data without including response data.
type ProtocolError struct {
	Operation string
	Problem   string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("vksrotate: %s protocol error: %s", e.Operation, e.Problem)
}

type credentialGeneration struct {
	value      Credentials
	generation uint64
}

// Client is safe for concurrent use.
type Client struct {
	vcenterOrigin    string
	kubernetesOrigin string
	httpClient       *http.Client

	credentialMu sync.RWMutex
	credential   credentialGeneration
}
