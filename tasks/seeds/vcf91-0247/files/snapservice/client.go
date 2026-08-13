package snapservice

import (
	"context"
	"errors"
)

// NewClient builds a Client for one Snapshot Appliance service root and session
// token, applying DefaultPollInterval, DefaultMaxPolls and an HTTP client whose
// timeout is DefaultTimeout.
//
// A service root that is not a usable HTTP(S) origin, or an empty session
// token, is reported as an error wrapping ErrInvalidRequest.
func NewClient(serviceRoot, sessionID string) (*Client, error) {
	return nil, errors.New("snapservice: NewClient is not implemented")
}

// CreateProtectionGroupSnapshot starts a protection group snapshot with
// Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task and then polls
// Snapservice.Tasks_get until the task reaches a terminal status.
func (c *Client) CreateProtectionGroupSnapshot(ctx context.Context, req SnapshotRequest) (*TaskResult, error) {
	return nil, errors.New("snapservice: CreateProtectionGroupSnapshot is not implemented")
}
