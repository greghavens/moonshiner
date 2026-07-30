package vksprovision

import (
	"context"
	"errors"
)

// NewClient constructs the focused VCF 9.1 client.
func NewClient(Config) (*Client, error) {
	return nil, errors.New("TODO: implement NewClient")
}

// Provision creates the Supervisor Namespace and VKS Cluster, then waits for
// the Cluster's Available condition to reach a terminal state.
func (c *Client) Provision(context.Context, ProvisionRequest) (ProvisionResult, error) {
	return ProvisionResult{}, errors.New("TODO: implement Provision")
}
