package attestdiag

import "context"

// NewClient validates cfg and returns an independent client.
func NewClient(cfg Config) (*Client, error) {
	return nil, &ValidationError{Field: "configuration: TODO"}
}

// ListTPMs retrieves TPM summaries using only the focused list operation.
func (c *Client) ListTPMs(
	ctx context.Context,
	host string,
	options TPMListOptions,
) ([]TPMSummary, error) {
	return nil, &ValidationError{Field: "ListTPMs: TODO"}
}

// GetTPMEventLog retrieves the measured-boot event log for one TPM.
func (c *Client) GetTPMEventLog(
	ctx context.Context,
	host string,
	tpm string,
) (TPMEventLog, error) {
	return TPMEventLog{}, &ValidationError{Field: "GetTPMEventLog: TODO"}
}

// CreateLogBundle requests a vCenter support bundle containing logs.
func (c *Client) CreateLogBundle(
	ctx context.Context,
	description string,
	options BundleOptions,
) (string, error) {
	return "", &ValidationError{Field: "CreateLogBundle: TODO"}
}

// CollectDiagnosis gathers evidence before classifying the reported failure.
func CollectDiagnosis(
	ctx context.Context,
	client *Client,
	host string,
	description string,
) (DiagnosisReport, error) {
	return DiagnosisReport{}, &ValidationError{Field: "CollectDiagnosis: TODO"}
}
