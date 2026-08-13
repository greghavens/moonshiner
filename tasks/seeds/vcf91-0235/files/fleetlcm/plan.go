// Package fleetlcm applies an ordered SDDC/Fleet lifecycle change plan against a
// VMware Cloud Foundation 9.1 SDDC LCM service.
//
// Every route, request shape and response status used here is pinned by
// docs/contract.json, a projection of specifications/sddc-lcm/sddc-lcm-openapi.yaml
// from the Apache-2.0 vmware/vcf-api-specs repository. docs/official_sources.json
// records the specification path, the repository commit SHA and each operationId.
package fleetlcm

import (
	"context"
	"errors"
	"net/http"
	"strconv"
	"time"
)

// Operation identifiers, copied verbatim from the pinned specification.
const (
	OpSetDepot               = "setDepot"
	OpResolveDepotComponents = "resolveDepotComponents"
	OpCreateComponents       = "createComponents"
	OpGetTask                = "getTask"
	OpUpdateComponentConfig  = "updateComponentConfig"
)

// ServiceBasePath is the path component of the single server URL declared by the
// pinned specification ("https://vcf.broadcom.com/sddc-lcm"). Every request target
// is ServiceBasePath joined with the operation's path template.
const ServiceBasePath = "/sddc-lcm"

// Step outcome values reported in PlanReport.
const (
	StatusSucceeded = "SUCCEEDED"
	StatusFailed    = "FAILED"
	StatusSkipped   = "SKIPPED"
)

// PlanOperations lists, in execution order, the operationIds that always appear as
// steps in a PlanReport. getTask is not a plan step: it is the polling operation
// used to drive an accepted task to a terminal state.
var PlanOperations = []string{
	OpSetDepot,
	OpResolveDepotComponents,
	OpCreateComponents,
	OpUpdateComponentConfig,
}

// ErrNotImplemented is returned by the unfinished scaffold.
var ErrNotImplemented = errors.New("fleetlcm: not implemented")

// DepotSpec mirrors the specification's FleetDepotSpec. Both members are required.
type DepotSpec struct {
	FQDN        string
	Certificate string
}

// ComponentPin mirrors the specification's ComponentVersionSpec. Version is
// optional: an empty Version means "let the depot choose" and must not reach the
// wire at all.
type ComponentPin struct {
	Component string
	Version   string
}

// IPv4Settings mirrors the specification's IPv4Settings. Every member is optional.
type IPv4Settings struct {
	AddressType string
	Address     string
	Gateway     string
	Netmask     string
}

// IPv6Settings mirrors the specification's IPv6Settings. Every member is optional;
// Force is a pointer so that an explicit false is distinguishable from unset.
type IPv6Settings struct {
	AddressType string
	Address     string
	Gateway     string
	Netmask     string
	Force       *bool
}

// NodePlan describes one OvaNodeSpec together with its nested OvaDeploymentSpec.
// NodeType, Version, DownloadURL, FQDN and Password are required; the remaining
// members are optional and must be omitted from the request when unset.
type NodePlan struct {
	NodeType       string
	Version        string
	DownloadURL    string
	RepositoryCert string

	FQDN             string
	Password         string
	DeploymentOption string
	DNSServers       string
	NTPServers       string
	DNSSuffix        string
	NetworkName      string
	IPv4             *IPv4Settings
	IPv6             *IPv6Settings
	ExtraConfig      map[string]string

	DeploymentMode string
}

// ComponentPlan describes one OvaComponentSpec entry of a ComponentSpecs body.
type ComponentPlan struct {
	ComponentType  string
	DeploymentType string
	Nodes          []NodePlan
}

// NodeSizePlan mirrors the specification's OvaNodeSizeConfigSpec. NodeID, Size and
// AdditionalDiskSize are required; the remaining members are optional. NumCores and
// MemoryGB are pointers so that an explicit zero is distinguishable from unset.
type NodeSizePlan struct {
	NodeID             string
	Size               string
	AdditionalDiskSize int64
	NodeName           string
	NodeType           string
	NumCores           *int
	MemoryGB           *int
	ConfigMode         string
}

// ConfigPlan describes the post-deployment OvaComponentConfigSpec applied through
// updateComponentConfig.
type ConfigPlan struct {
	ComponentID string
	Type        string
	NodeSizes   []NodeSizePlan
}

// Plan is one ordered SDDC/Fleet lifecycle change.
type Plan struct {
	// Depot is registered through setDepot and echoed inside the
	// resolveDepotComponents body.
	Depot DepotSpec
	// DepotVersion is the optional DepotComponentsSpec.version.
	DepotVersion string
	// Pins are the components whose binary URLs are resolved.
	Pins []ComponentPin
	// Components are installed through createComponents.
	Components []ComponentPlan
	// Config is optional. A nil Config means the plan requests no configuration
	// change and updateComponentConfig is reported as skipped.
	Config *ConfigPlan
}

// ResolvedVersion mirrors the specification's ResolvedComponentVersion.
type ResolvedVersion struct {
	Component string
	Version   string
	BinaryURL string
}

// StepReport is the outcome of a single plan step.
type StepReport struct {
	// OperationID is the specification operationId of the step.
	OperationID string
	// Status is StatusSucceeded, StatusFailed or StatusSkipped.
	Status string
	// TaskID is the id of the accepted task, or "" when the step returned no task.
	TaskID string
	// TaskStatus is the terminal task status observed while polling, or "".
	TaskStatus string
	// FailedStage is the name of the failing TaskStage, or "".
	FailedStage string
	// Message explains a failed or skipped step, and is "" for a succeeded step.
	Message string
	// HTTPStatus is the response status when the step failed on a status code,
	// and 0 otherwise.
	HTTPStatus int
}

// PlanReport is the complete outcome of Apply. It always carries one StepReport per
// entry of PlanOperations, in that order, whether or not the plan ran to completion.
type PlanReport struct {
	Steps            []StepReport
	ResolvedVersions []ResolvedVersion
}

// Step returns the report for operationID.
func (r *PlanReport) Step(operationID string) (StepReport, bool) {
	if r == nil {
		return StepReport{}, false
	}
	for _, s := range r.Steps {
		if s.OperationID == operationID {
			return s, true
		}
	}
	return StepReport{}, false
}

// StepError is returned by Apply when a plan step fails. The accompanying
// *PlanReport is never nil and always describes every step.
type StepError struct {
	OperationID string
	HTTPStatus  int
	TaskID      string
	TaskStatus  string
	FailedStage string
	Message     string
	Err         error
}

func (e *StepError) Error() string {
	if e == nil {
		return "<nil>"
	}
	switch {
	case e.HTTPStatus != 0:
		return e.OperationID + ": rejected with HTTP status " + strconv.Itoa(e.HTTPStatus)
	case e.TaskStatus != "":
		return e.OperationID + ": task ended " + e.TaskStatus
	default:
		return e.OperationID + ": failed"
	}
}

func (e *StepError) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.Err
}

// Config configures a Client.
type Config struct {
	// BaseURL is the appliance root, for example "https://sddc.vcf.example.com".
	// It must be an absolute http or https URL with a host and without userinfo,
	// a non-root path, a query or a fragment. ServiceBasePath is appended by the
	// client.
	BaseURL string
	// Token is the bearer token presented as "Authorization: Bearer <token>".
	Token string
	// CorrelationID is optional. When empty, no X-Correlation-Id header is sent.
	CorrelationID string
	// PollInterval is the delay between getTask polls. Defaults to 2s.
	PollInterval time.Duration
	// PollTimeout bounds the polling of a single task. Defaults to 30m.
	PollTimeout time.Duration
	// HTTPClient defaults to a client with a 30s timeout.
	HTTPClient *http.Client
}

// Client applies change plans against one SDDC LCM service.
type Client struct {
	root          string
	token         string
	correlationID string
	pollInterval  time.Duration
	pollTimeout   time.Duration
	httpClient    *http.Client
}

// NewClient validates cfg and returns a ready Client.
func NewClient(cfg Config) (*Client, error) {
	return nil, ErrNotImplemented
}

// Apply runs the plan steps in PlanOperations order and returns a complete report.
//
// The returned *PlanReport is non-nil even when the error is non-nil: steps that ran
// before the failure keep their real outcome, the failing step is reported with its
// diagnosis, and every step after it is reported as skipped.
func (c *Client) Apply(ctx context.Context, plan Plan) (*PlanReport, error) {
	return nil, ErrNotImplemented
}
