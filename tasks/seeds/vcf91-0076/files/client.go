package nsxpolicy

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
)

// ErrNoDiagnosticEvidence means that the controller log and NSX data did not
// contain enough correlated evidence to state a failure cause.
var ErrNoDiagnosticEvidence = errors.New("no diagnostic evidence")

// Config configures an NSX Policy client.
type Config struct {
	BaseURL    string
	Username   string
	Password   string
	HTTPClient *http.Client
}

// Client calls the VCF 9.1 NSX Policy API.
type Client struct{}

// SegmentPatch is the supported subset of the NSX Segment request.
type SegmentPatch struct {
	DisplayName       string          `json:"display_name,omitempty"`
	Description       string          `json:"description,omitempty"`
	ConnectivityPath  string          `json:"connectivity_path,omitempty"`
	TransportZonePath string          `json:"transport_zone_path,omitempty"`
	Subnets           []SegmentSubnet `json:"subnets,omitempty"`
	VLANIDs           []string        `json:"vlan_ids,omitempty"`
	AdminState        *string         `json:"admin_state,omitempty"`
}

// SegmentSubnet is the supported subnet request shape.
type SegmentSubnet struct {
	GatewayAddress string   `json:"gateway_address,omitempty"`
	DHCPRanges     []string `json:"dhcp_ranges,omitempty"`
}

// DiagnosticsOptions maps to the optional query parameters in the pinned
// ListRealizedEntities and ListAlarms operations.
type DiagnosticsOptions struct {
	SitePath       string
	IncludedFields string
	PageSize       *int64
	SortAscending  *bool
	SortBy         string
}

// DiagnosticReport contains only evidence correlated across the controller log
// and NSX realized-state/alarm responses.
type DiagnosticReport struct {
	CorrelationID     string
	IntentPath        string
	ControllerMessage string
	RealizedEntityID  string
	RealizationState  string
	AlarmID           string
	Severity          string
	Cause             string
}

// APIError is the relevant NSX error response shape plus its HTTP status.
type APIError struct {
	StatusCode   int    `json:"-"`
	ErrorCode    int64  `json:"error_code"`
	ErrorMessage string `json:"error_message"`
	ModuleName   string `json:"module_name"`
	Details      string `json:"details"`
}

func (e *APIError) Error() string {
	return fmt.Sprintf("NSX Policy API returned HTTP %d (code %d): %s", e.StatusCode, e.ErrorCode, e.ErrorMessage)
}

// New constructs a client.
func New(cfg Config) (*Client, error) {
	return nil, errors.New("not implemented")
}

// PatchSegment calls PatchInfraSegment.
func (c *Client) PatchSegment(ctx context.Context, segmentID string, patch SegmentPatch) error {
	return errors.New("not implemented")
}

// DiagnoseSegment correlates a failed controller log record with realized
// entities and alarms rather than inferring a cause.
func (c *Client) DiagnoseSegment(ctx context.Context, segmentID string, controllerLog io.Reader, options DiagnosticsOptions) (DiagnosticReport, error) {
	return DiagnosticReport{}, errors.New("not implemented")
}
