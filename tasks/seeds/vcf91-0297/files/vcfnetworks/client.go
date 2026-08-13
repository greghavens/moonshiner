package vcfnetworks

import (
	"context"
	"errors"
	"net/http"
)

// ProblemEvent is the focused getProblemEvent representation used by this
// package. It projects the specification's ProblemEvent schema.
type ProblemEvent struct {
	EntityID         string   `json:"entity_id"`
	Name             string   `json:"name"`
	EntityType       string   `json:"entity_type"`
	Message          string   `json:"message"`
	EventType        string   `json:"event_type"`
	EventTags        []string `json:"event_tags"`
	AdminState       *string  `json:"admin_state"`
	Archived         bool     `json:"archived"`
	EventTimeEpochMs int64    `json:"event_time_epoch_ms"`
	Severity         *string  `json:"severity"`
}

// CollectOptions carries the listProblemEvents filters plus the optional
// getProblemEvent time parameter. Every specification parameter other than the
// page size is optional: a nil pointer or an empty slice means the field is not
// sent at all.
type CollectOptions struct {
	// Size is the listProblemEvents page size and is always sent.
	Size int

	StartTime      *int64
	EndTime        *int64
	EventType      *string
	EventTags      []string
	EventStatus    *string
	UpdateTimeFrom *int64
	UpdateTimeTo   *int64
	EventSeverity  []string
	Managers       []string

	// DetailTime is the optional getProblemEvent "time" query parameter.
	DetailTime *int64
}

// APIError is a decoded non-success response carrying the ApiError body.
type APIError struct {
	StatusCode int
	Code       int
	Message    string
}

func (e *APIError) Error() string {
	return "VCF Operations for Networks API request failed"
}

// ProtocolError reports a malformed or incoherent success response.
type ProtocolError struct{ Reason string }

func (e *ProtocolError) Error() string {
	return "VCF Operations for Networks protocol error: " + e.Reason
}

// Client calls the VCF Operations for Networks API.
type Client struct{}

// NewClient constructs a client. It is intentionally incomplete.
func NewClient(baseURL, apiToken string, httpClient *http.Client) (*Client, error) {
	return nil, errors.New("TODO: implement NewClient")
}

// CollectProblemEvents retrieves the complete problem event collection and
// returns it in a deterministic order.
func (c *Client) CollectProblemEvents(ctx context.Context, options CollectOptions) ([]ProblemEvent, error) {
	return nil, errors.New("TODO: implement CollectProblemEvents")
}
