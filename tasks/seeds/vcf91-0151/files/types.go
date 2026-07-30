package vkschange

import "net/http"

const (
	OperationGetNamespace    = "Vcenter.Namespaces.Instances_get"
	OperationUpdateNamespace = "Vcenter.Namespaces.Instances_update"
	OperationPatchVKSCluster = "Vks.Cluster_patch"
)

// Client coordinates a vCenter namespace change with a VKS Cluster API change.
// VCenterURL includes vCenter's /api base path. KubernetesURL is the Kubernetes
// API server root URL.
type Client struct {
	VCenterURL    string
	KubernetesURL string
	SessionID     string
	BearerToken   string
	HTTPClient    *http.Client
}

// NamespacePatch contains the namespace fields this integration is allowed to
// change. A nil field is unset and must not appear on the wire. A non-nil
// pointer to an empty slice is an explicit request to clear the policies.
type NamespacePatch struct {
	Description            *string
	InfrastructurePolicies *[]string
}

// Change identifies one coordinated namespace and VKS cluster change.
type Change struct {
	Namespace         string
	Cluster           string
	NamespacePatch    NamespacePatch
	KubernetesVersion string
}

type StepState string

const (
	StepSkipped   StepState = "skipped"
	StepSucceeded StepState = "succeeded"
	StepFailed    StepState = "failed"
)

// StepResult reports one attempted or skipped operation. HTTPStatus is zero
// when no HTTP response was received.
type StepResult struct {
	Operation  string
	State      StepState
	HTTPStatus int
	Error      string
}

// Report always contains the three ordered steps, including skipped steps.
type Report struct {
	PreviousDescription string
	Steps               []StepResult
}

func newReport() Report {
	return Report{Steps: []StepResult{
		{Operation: OperationGetNamespace, State: StepSkipped},
		{Operation: OperationUpdateNamespace, State: StepSkipped},
		{Operation: OperationPatchVKSCluster, State: StepSkipped},
	}}
}
