package namespacebackup

import (
	"context"
	"fmt"
)

// NewClient validates configuration without network traffic.
func NewClient(cfg Config) (*Client, error) {
	return nil, fmt.Errorf("TODO: implement NewClient")
}

// BackupNamespace performs the contract workflow.
func (c *Client) BackupNamespace(ctx context.Context, req BackupRequest) (Result, error) {
	return Result{}, fmt.Errorf("TODO: implement BackupNamespace")
}
