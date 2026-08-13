package vcfops

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"
)

// OperationState is a content operation state from VCF Operations 9.0.
type OperationState string

const (
	StateNotInitialized OperationState = "NOT_INITIALIZED"
	StateInitialized    OperationState = "INITIALIZED"
	StateRunning        OperationState = "RUNNING"
	StateFailed         OperationState = "FAILED"
	StateFinished       OperationState = "FINISHED"
	StateUnknown        OperationState = "UNKNOWN"
)

// Link is a link returned by the VCF Operations API.
type Link struct {
	Href        string `json:"href"`
	Rel         string `json:"rel"`
	Name        string `json:"name"`
	Description string `json:"description"`
}

// ContentImport is the response from importContent.
type ContentImport struct {
	FileName string `json:"fileName"`
	Force    bool   `json:"force"`
	ID       string `json:"id"`
	Links    []Link `json:"links"`
}

// OperationSummary is one content-type summary from an import operation.
type OperationSummary struct {
	Type          string         `json:"type"`
	ContentType   string         `json:"contentType"`
	State         OperationState `json:"state"`
	Imported      int            `json:"imported"`
	Skipped       int            `json:"skipped"`
	Failed        int            `json:"failed"`
	Total         int            `json:"total"`
	InfoMessages  []string       `json:"infoMessages"`
	ErrorMessages []string       `json:"errorMessages"`
}

// OperationDetails is the response from getLastImportOperation.
type OperationDetails struct {
	OperationSummaries []OperationSummary `json:"operationSummaries"`
	ErrorMessages      []string           `json:"errorMessages"`
	Links              []Link             `json:"links"`
	ID                 string             `json:"id"`
	Type               string             `json:"type"`
	State              OperationState     `json:"state"`
	StartTime          int64              `json:"startTime"`
	EndTime            int64              `json:"endTime"`
	LastUpdatedTime    int64              `json:"lastUpdatedTime"`
	ErrorCode          string             `json:"errorCode"`
}

// ImportOptions contains the optional importContent parameters.
type ImportOptions struct {
	Force              *bool
	EncryptionPassword string
}

// ImportResult contains both the acceptance response and the terminal operation.
type ImportResult struct {
	Accepted  ContentImport
	Operation OperationDetails
}

// OperationError reports a terminal operation that did not finish successfully.
type OperationError struct {
	Operation OperationDetails
}

func (e *OperationError) Error() string {
	return fmt.Sprintf("content import reached terminal state %s (error code %s)", e.Operation.State, e.Operation.ErrorCode)
}

// Client calls the two VCF Operations endpoints in docs/contract.json.
type Client struct {
	baseURL       *url.URL
	authorization string
	httpClient    *http.Client
	pollInterval  time.Duration
}

// NewClient constructs a VCF Operations content-import client.
func NewClient(baseURL, authorization string, httpClient *http.Client, pollInterval time.Duration) (*Client, error) {
	u, err := url.Parse(baseURL)
	if err != nil {
		return nil, fmt.Errorf("parse base URL: %w", err)
	}
	if u.Scheme == "" || u.Host == "" {
		return nil, errors.New("base URL must be absolute")
	}
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{
		baseURL:       u,
		authorization: authorization,
		httpClient:    httpClient,
		pollInterval:  pollInterval,
	}, nil
}

// ImportAndWait submits a content import and waits for its terminal state.
func (c *Client) ImportAndWait(ctx context.Context, filename string, content io.Reader, opts ImportOptions) (ImportResult, error) {
	return ImportResult{}, errors.New("ImportAndWait is not implemented")
}
