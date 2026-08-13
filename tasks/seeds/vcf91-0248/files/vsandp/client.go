// Package vsandp is a dependency-free client for the subset of the VMware
// Cloud Foundation 9.1 vSAN Data Protection ("Snapshot Appliance") API that is
// pinned in docs/contract.json.
//
// The client takes a one-time snapshot of every protection group in a cluster
// and waits for each snapshot task to reach a terminal state. The appliance
// hands out short-lived session tokens, so a long batch will normally outlive
// the token it started with; recovering from that without redoing completed
// work is the point of this package.
package vsandp

import (
	"context"
	"errors"
	"net/http"
	"time"
)

// ErrNotImplemented is returned by the unfinished parts of this package.
var ErrNotImplemented = errors.New("vsandp: not implemented")

// TimeUnit values accepted by RetentionPeriod.Unit, per the Snapservice.TimeUnit
// enumeration in the pinned contract.
const (
	UnitMinute = "MINUTE"
	UnitHour   = "HOUR"
	UnitDay    = "DAY"
	UnitWeek   = "WEEK"
	UnitMonth  = "MONTH"
	UnitYear   = "YEAR"
)

// Terminal and non-terminal task states, per Snapservice.Tasks.Status.
const (
	StatusPending   = "PENDING"
	StatusRunning   = "RUNNING"
	StatusBlocked   = "BLOCKED"
	StatusSucceeded = "SUCCEEDED"
	StatusFailed    = "FAILED"
)

// RetentionPeriod mirrors the Snapservice.RetentionPeriod schema. Both
// properties are required by the schema, so a RetentionPeriod is either sent in
// full or not sent at all.
type RetentionPeriod struct {
	Unit     string
	Duration int64
}

// Config describes how to reach the snapshot appliance.
type Config struct {
	// BaseURL is the scheme://host[:port] prefix that the contract's operation
	// paths are appended to. It has no trailing slash.
	BaseURL string

	// BootstrapToken is the long-lived operator token. It is presented to
	// Snapservice.Sessions_create to mint a short-lived working session token
	// and is never presented to any other operation.
	BootstrapToken string

	// HTTPClient is used for every request. If nil, http.DefaultClient is used.
	HTTPClient *http.Client

	// PollInterval is the delay between successive polls of the same task. A
	// zero value means poll without delay.
	PollInterval time.Duration
}

// BatchRequest describes one snapshot batch.
type BatchRequest struct {
	// Cluster is the ClusterComputeResource identifier, required.
	Cluster string

	// Names optionally restricts the batch to protection groups with these
	// names. An empty slice means "every protection group in the cluster" and
	// the corresponding query parameter is not sent at all.
	Names []string

	// SnapshotName is the name given to every protection group snapshot created
	// by this batch, required.
	SnapshotName string

	// Retention is optional. A nil value means the snapshot is retained for the
	// life of the protection group, and the corresponding property is omitted
	// from the request body entirely.
	Retention *RetentionPeriod
}

// Result records the outcome of a single protection group snapshot.
type Result struct {
	// PG is the protection group identifier.
	PG string
	// Name is the protection group name.
	Name string
	// Task is the identifier returned by the snapshot create operation.
	Task string
	// Status is the terminal task status: StatusSucceeded or StatusFailed.
	Status string
}

// BatchReport is the outcome of a batch.
type BatchReport struct {
	// Results holds one entry per protection group, in the order the list
	// operation returned them.
	Results []Result
}

// Client talks to one snapshot appliance. A Client is safe for concurrent use
// by multiple goroutines and shares one working session token across them.
type Client struct {
	cfg Config
}

// New validates cfg and returns a ready Client. It performs no I/O.
func New(cfg Config) (*Client, error) {
	return nil, ErrNotImplemented
}

// SessionCreates reports how many working session tokens this Client has minted
// over its lifetime, including the first one. It is safe to call concurrently
// with SnapshotProtectionGroups.
func (c *Client) SessionCreates() int {
	return 0
}

// SnapshotProtectionGroups lists the protection groups selected by req, starts a
// one-time snapshot on each of them, and waits for every snapshot task to reach
// a terminal state.
//
// A task that ends in StatusFailed is reported in the returned BatchReport; it
// is not an error. Any transport failure, malformed response, or non-2xx status
// other than 401 aborts the batch and is returned as an error.
func (c *Client) SnapshotProtectionGroups(ctx context.Context, req BatchRequest) (*BatchReport, error) {
	return nil, ErrNotImplemented
}
