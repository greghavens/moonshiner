// Package diagnostics correlates VCF Operations logs and events for failed
// deployments.
package diagnostics

import (
	"context"
	"errors"
	"net/http"
	"time"
)

// ErrInsufficientEvidence means the available logs and events do not support a
// root-cause diagnosis.
var ErrInsufficientEvidence = errors.New("insufficient correlated evidence")

// CertificateRotationRootCause is the normalized cause reported when a TLS
// failure is confirmed by a correlated certificate-replacement event.
const CertificateRotationRootCause = "vCenter certificate rotation left the deployment trust cache stale"

// Config configures a VCF Operations Log Management client.
type Config struct {
	BaseURL    string
	Token      string
	HTTPClient *http.Client
}

// Incident identifies the failed deployment and its inclusive search window.
type Incident struct {
	DeploymentID string
	StartedAt    time.Time
	EndedAt      time.Time
}

// Evidence records one item used to support a diagnosis.
type Evidence struct {
	Kind      string
	Timestamp int64
	Text      string
}

// Diagnosis is returned only when correlated log and event evidence agrees.
type Diagnosis struct {
	RootCause     string
	CorrelationID string
	Endpoint      string
	Evidence      []Evidence
}

// Client searches VCF Operations Log Management.
type Client struct {
	baseURL    string
	token      string
	httpClient *http.Client
}

// NewClient constructs a Log Management client.
func NewClient(cfg Config) (*Client, error) {
	return nil, errors.New("TODO: implement VCF Operations Log Management client")
}

// DiagnoseFailure pulls and correlates the relevant logs and events.
func (c *Client) DiagnoseFailure(ctx context.Context, incident Incident) (Diagnosis, error) {
	return Diagnosis{}, errors.New("TODO: diagnose from correlated logs and events")
}
