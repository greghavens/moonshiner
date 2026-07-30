package vksguard

import (
	"context"
	"errors"
)

// Client is the Supervisor-gated VKS Cluster client.
type Client struct{}

// NewClient validates config without performing network traffic.
func NewClient(config Config) (*Client, error) {
	return nil, errors.New("vksguard: implementation incomplete")
}

// ReconcileVersion applies targetVersion only when the namespace precheck passes.
func (c *Client) ReconcileVersion(
	ctx context.Context,
	supervisor string,
	namespace string,
	clusterName string,
	targetVersion string,
) (Result, error) {
	return Result{}, errors.New("vksguard: implementation incomplete")
}
