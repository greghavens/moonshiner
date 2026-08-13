package vcfinstaller

import (
	"context"
	"errors"
	"net/http"
	"time"
)

var ErrNotImplemented = errors.New("vcf installer client is not implemented")

// BundleDownloadSpec is the request documented by the VCF Installer 9.0
// BundleDownloadSpec schema. Pointers distinguish omitted values from explicit
// false or empty values on the wire.
type BundleDownloadSpec struct {
	ScheduledTimestamp *string `json:"scheduledTimestamp,omitempty"`
	DownloadNow        *bool   `json:"downloadNow,omitempty"`
	CancelNow          *bool   `json:"cancelNow,omitempty"`
}

// Task is the portion of the VCF Installer Task schema needed to follow an
// asynchronous operation.
type Task struct {
	ID                  string `json:"id"`
	Name                string `json:"name"`
	Status              string `json:"status"`
	CreationTimestamp   string `json:"creationTimestamp"`
	CompletionTimestamp string `json:"completionTimestamp,omitempty"`
}

// Client calls a VCF Installer endpoint using the supplied transport and poll
// interval.
type Client struct {
	baseURL      string
	httpClient   *http.Client
	pollInterval time.Duration
}

// NewClient constructs a VCF Installer client.
func NewClient(baseURL string, httpClient *http.Client, pollInterval time.Duration) (*Client, error) {
	return nil, ErrNotImplemented
}

// StartBundleDownload invokes the startBundleDownloadByID operation.
func (c *Client) StartBundleDownload(ctx context.Context, bundleID string, spec BundleDownloadSpec) (Task, error) {
	return Task{}, ErrNotImplemented
}

// GetTask invokes the getTask operation.
func (c *Client) GetTask(ctx context.Context, taskID string) (Task, error) {
	return Task{}, ErrNotImplemented
}

// DownloadBundle starts the asynchronous operation and polls it to a terminal
// status.
func (c *Client) DownloadBundle(ctx context.Context, bundleID string, spec BundleDownloadSpec) (Task, error) {
	return Task{}, ErrNotImplemented
}
