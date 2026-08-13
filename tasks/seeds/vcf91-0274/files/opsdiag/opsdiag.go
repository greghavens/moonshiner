// Package opsdiag diagnoses a stalled VCF Operations adapter instance by
// reading the alert, symptom, note and audit records the platform already
// holds, rather than by inferring a cause from the resource identifier.
package opsdiag

import (
	"context"
	"errors"
	"net/http"
)

// Root cause classifications returned in Diagnosis.RootCause.
const (
	RootCauseCredentialRejected = "ADAPTER_CREDENTIAL_REJECTED"
	RootCauseCollectorOffline   = "COLLECTOR_OFFLINE"
	RootCauseMonitoringStopped  = "ADAPTER_MONITORING_STOPPED"
	RootCauseUnclassified       = "UNCLASSIFIED"
	RootCauseNoActiveAlerts     = "NO_ACTIVE_ALERTS"
)

// ErrIncompleteContract is returned by NewClient when the supplied contract
// does not name every operation this package needs.
var ErrIncompleteContract = errors.New("opsdiag: contract does not name every required operation")

// Contract is the loaded projection of the VCF Operations OpenAPI
// specification that pins every request this package issues.
type Contract struct {
	// TODO: model the fields of docs/contract.json that the client needs.
}

// Diagnosis is the result of correlating the records retrieved for one resource.
type Diagnosis struct {
	// ResourceID is the resource that was diagnosed.
	ResourceID string
	// AlertID is the selected alert, or "" when no alert was returned.
	AlertID string
	// RootSymptomID is the id of the root contributing symptom, or "".
	RootSymptomID string
	// RootSymptomDefinitionID is that symptom's definition id, or "".
	RootSymptomDefinitionID string
	// RootCause is one of the RootCause* constants.
	RootCause string
	// ObjectsConfigured, ObjectsCollecting and ObjectsNotCollecting come from
	// the system audit report.
	ObjectsConfigured    int
	ObjectsCollecting    int
	ObjectsNotCollecting int
	// Notes holds the note text of the selected alert, in the order returned.
	Notes []string
}

// Client issues contract-pinned requests against a VCF Operations endpoint.
// A Client is safe for concurrent use by multiple goroutines.
type Client struct {
	// TODO
}

// LoadContract reads and validates the contract projection at path.
func LoadContract(path string) (*Contract, error) {
	return nil, errors.New("opsdiag: LoadContract not implemented")
}

// NewClient builds a client for the endpoint at baseURL, presenting token with
// the authorization scheme the contract names. A nil hc selects
// http.DefaultClient. NewClient returns ErrIncompleteContract if the contract
// omits any operation this package issues.
func NewClient(baseURL, token string, contract *Contract, hc *http.Client) (*Client, error) {
	return nil, errors.New("opsdiag: NewClient not implemented")
}

// Diagnose retrieves the records for resourceID and correlates them.
func (c *Client) Diagnose(ctx context.Context, resourceID string) (*Diagnosis, error) {
	return nil, errors.New("opsdiag: Diagnose not implemented")
}
