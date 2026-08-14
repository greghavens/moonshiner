package networks

import (
	"context"
	"fmt"
	"net/http"
	"time"
)

// UpdateStatus is the state of a certificate update operation.
type UpdateStatus string

const (
	StatusSubmitted  UpdateStatus = "SUBMITTED"
	StatusInProgress UpdateStatus = "IN_PROGRESS"
	StatusSuccess    UpdateStatus = "SUCCESS"
	StatusFailed     UpdateStatus = "FAILED"
)

// CertificateUpdateRequest is the request accepted by the certificate update API.
type CertificateUpdateRequest struct {
	Certificate string `json:"certificate,omitempty"`
	PrivateKey  string `json:"private_key,omitempty"`
	Chain       string `json:"chain,omitempty"`
}

// CertificateUpdateStatus is returned by both the submit and status operations.
type CertificateUpdateStatus struct {
	ID             string       `json:"id,omitempty"`
	Name           string       `json:"name,omitempty"`
	Status         UpdateStatus `json:"status,omitempty"`
	ErrorMessage   string       `json:"error_message,omitempty"`
	FailedNodes    []Node       `json:"failed_nodes,omitempty"`
	UpdatedNodes   []Node       `json:"updated_nodes,omitempty"`
	LastModifiedBy string       `json:"last_modified_by_user,omitempty"`
	LastModifiedAt int64        `json:"last_modified_time,omitempty"`
}

// Node contains the node fields used by CertificateUpdateStatus.
type Node struct {
	ID   string `json:"id,omitempty"`
	Name string `json:"name,omitempty"`
}

// OperationError reports a terminal FAILED update.
type OperationError struct {
	UpdateID string
	Message  string
}

func (e *OperationError) Error() string {
	return fmt.Sprintf("certificate update %s failed: %s", e.UpdateID, e.Message)
}

// Client calls the VCF Operations for Networks certificate update operations.
type Client struct {
	baseURL      string
	token        string
	httpClient   *http.Client
	pollInterval time.Duration
}

// NewClient returns a client using a raw VCF Operations for Networks API token.
func NewClient(baseURL, token string, httpClient *http.Client, pollInterval time.Duration) *Client {
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{
		baseURL:      baseURL,
		token:        token,
		httpClient:   httpClient,
		pollInterval: pollInterval,
	}
}

// UpdateCertificateAndWait submits an update and waits for its terminal status.
func (c *Client) UpdateCertificateAndWait(ctx context.Context, certificateID string, request CertificateUpdateRequest) (CertificateUpdateStatus, error) {
	return CertificateUpdateStatus{}, fmt.Errorf("not implemented")
}
