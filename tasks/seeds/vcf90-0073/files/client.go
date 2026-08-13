package vcfops

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
)

// SymptomDefinition is the response shape used by getSymptomDefinitions.
// State remains raw because the VCF Operations contract permits several
// condition variants beneath it.
type SymptomDefinition struct {
	ID                        string          `json:"id,omitempty"`
	Name                      string          `json:"name"`
	AdapterKindKey            string          `json:"adapterKindKey"`
	ResourceKindKey           string          `json:"resourceKindKey"`
	WaitCycles                *int            `json:"waitCycles,omitempty"`
	CancelCycles              *int            `json:"cancelCycles,omitempty"`
	RealtimeMonitoringEnabled *bool           `json:"realtimeMonitoringEnabled,omitempty"`
	State                     json.RawMessage `json:"state"`
}

// ListSymptomDefinitionsOptions contains the filters accepted by
// getSymptomDefinitions. PageSize zero selects the contract default of 1000.
type ListSymptomDefinitionsOptions struct {
	AdapterKind  string
	ResourceKind string
	IDs          []string
	Name         string
	PageSize     int
}

type Client struct {
	baseURL    *url.URL
	token      string
	httpClient *http.Client
}

// NewClient constructs a VCF Operations client. baseURL is the appliance
// origin; operation paths are resolved below the specification's /suite-api
// server base path.
func NewClient(baseURL, token string, httpClient *http.Client) (*Client, error) {
	u, err := url.Parse(baseURL)
	if err != nil {
		return nil, err
	}
	if u.Scheme == "" || u.Host == "" {
		return nil, errors.New("base URL must be absolute")
	}
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{baseURL: u, token: token, httpClient: httpClient}, nil
}

// ListAllSymptomDefinitions retrieves every page and returns definitions in a
// deterministic order.
func (c *Client) ListAllSymptomDefinitions(ctx context.Context, opts ListSymptomDefinitionsOptions) ([]SymptomDefinition, error) {
	return nil, errors.New("ListAllSymptomDefinitions not implemented")
}
