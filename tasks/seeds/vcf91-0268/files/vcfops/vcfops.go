// Package vcfops is a client for the VMware Cloud Foundation Operations API.
package vcfops

import (
	"context"
	"errors"

	"vcfops.local/opssync/opsapi"
)

var errNotImplemented = errors.New("vcfops: not implemented")

// Client talks to one VCF Operations appliance. It is safe for concurrent use
// by multiple goroutines.
type Client struct {
	cfg opsapi.Config
}

// New builds a client from cfg. It performs no network I/O.
func New(cfg opsapi.Config) (*Client, error) {
	return nil, errNotImplemented
}

// ListAllResources walks every page of the resource listing operation and
// returns the resources it collected, in server order.
func (c *Client) ListAllResources(ctx context.Context, filter opsapi.ResourceFilter) ([]opsapi.Resource, error) {
	return nil, errNotImplemented
}

// PushProperties groups samples by resource, splits them into batches of at
// most batchSize resources, and posts each batch to the property operation.
func (c *Client) PushProperties(ctx context.Context, samples []opsapi.PropertySample, batchSize int) error {
	return errNotImplemented
}

// Stats reports what this client has done so far.
func (c *Client) Stats() opsapi.Stats {
	return opsapi.Stats{}
}
