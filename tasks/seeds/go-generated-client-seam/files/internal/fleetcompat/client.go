// Package fleetcompat is the handwritten compatibility seam around generated
// fleet API code. Consumers depend on this package, never on retry details.
package fleetcompat

import (
	"context"
	"errors"
	"fmt"
	"time"

	"go-generated-client-seam/internal/generated/fleetapi"
)

var (
	ErrUnavailable = errors.New("fleet temporarily unavailable")
	ErrNotFound     = errors.New("fleet node not found")
)

type NodeLookup interface {
	Lookup(context.Context, string) (fleetapi.Node, error)
}

type Client struct {
	raw         fleetapi.Client
	maxAttempts int
	sleep       func(time.Duration)
}

func New(raw fleetapi.Client, maxAttempts int, sleep func(time.Duration)) *Client {
	if maxAttempts < 1 {
		maxAttempts = 3
	}
	if sleep == nil {
		sleep = time.Sleep
	}
	return &Client{raw: raw, maxAttempts: maxAttempts, sleep: sleep}
}

func (c *Client) Lookup(ctx context.Context, nodeID string) (fleetapi.Node, error) {
	for attempt := 0; attempt < c.maxAttempts; attempt++ {
		node, err := c.raw.GetNode(ctx, nodeID)
		if err == nil {
			return node, nil
		}
		if errors.Is(err, fleetapi.ErrServiceBusy) {
			if attempt+1 < c.maxAttempts {
				c.sleep(10 * time.Millisecond)
				continue
			}
			return fleetapi.Node{}, fmt.Errorf("%w: %w", ErrUnavailable, err)
		}
		var problem *fleetapi.Problem
		if errors.As(err, &problem) && problem.Status == 404 {
			return fleetapi.Node{}, fmt.Errorf("%w: %w", ErrNotFound, err)
		}
		return fleetapi.Node{}, err
	}
	panic("unreachable retry loop")
}
