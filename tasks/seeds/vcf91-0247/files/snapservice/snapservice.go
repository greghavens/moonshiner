// Package snapservice is a standard-library client for the VMware Cloud
// Foundation 9.1 vSAN Data Protection Snapshot Appliance.
//
// The two operations in scope are pinned in docs/contract.json, a projection of
// specifications/vsan-data-protection/vsan-data-protection-openapi.yaml in the
// Apache-2.0 vmware/vcf-api-specs repository at the commit recorded in
// docs/official_sources.json:
//
//   - Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task starts a
//     protection group snapshot and answers 202 with a task identifier.
//   - Snapservice.Tasks_get reports the status of that task.
//
// Creating a protection group snapshot is therefore asynchronous: the 202 does
// not mean the snapshot exists, only that the appliance accepted the work.
//
// This file is protected. Implement client.go against the declarations here.
package snapservice

import (
	"errors"
	"fmt"
	"net/http"
	"time"
)

// Defaults applied by NewClient when the caller does not override them.
const (
	// DefaultPollInterval is the delay between two Snapservice.Tasks_get requests.
	DefaultPollInterval = 2 * time.Second
	// DefaultMaxPolls caps the number of Snapservice.Tasks_get requests issued
	// for a single snapshot operation.
	DefaultMaxPolls = 450
	// DefaultTimeout is the per-request timeout of the default HTTP client.
	DefaultTimeout = 30 * time.Second
)

// TimeUnit mirrors the Snapservice.TimeUnit enumeration.
type TimeUnit string

// The values of the Snapservice.TimeUnit enumeration.
const (
	Minute TimeUnit = "MINUTE"
	Hour   TimeUnit = "HOUR"
	Day    TimeUnit = "DAY"
	Week   TimeUnit = "WEEK"
	Month  TimeUnit = "MONTH"
	Year   TimeUnit = "YEAR"
)

// RetentionPeriod mirrors the Snapservice.RetentionPeriod schema. Both
// properties are required by the specification once a retention period is
// supplied at all.
type RetentionPeriod struct {
	Unit     TimeUnit
	Duration int64
}

// SnapshotRequest describes one protection group snapshot.
//
// Cluster and ProtectionGroup address the operation. Name is the required
// property of Snapservice.Clusters.ProtectionGroups.Snapshots.CreateSpec.
// Retention is the schema's only optional property: a nil Retention means the
// caller did not set it, and an unset optional property is omitted from the
// request body rather than sent as an empty or null value.
type SnapshotRequest struct {
	Cluster         string
	ProtectionGroup string
	Name            string
	Retention       *RetentionPeriod
}

// TaskResult reports the terminal state of a snapshot operation.
type TaskResult struct {
	// TaskID is the identifier carried in the 202 body of the create operation.
	TaskID string
	// Status is the terminal Snapservice.Tasks.Status value that was observed.
	Status string
	// Polls counts the Snapservice.Tasks_get requests that were issued,
	// including the one that observed the terminal status.
	Polls int
	// Result is the Snapservice.Tasks.Info result property, or nil when the
	// appliance omitted it.
	Result any
	// StartTime and EndTime are the Snapservice.Tasks.Info start_time and
	// end_time properties, or "" when the appliance omitted them.
	StartTime string
	EndTime   string
}

// Client talks to one Snapshot Appliance service root.
//
// A Client carries no per-call mutable state and is safe for concurrent use.
type Client struct {
	// ServiceRoot is the HTTP(S) origin of the appliance, without the
	// contract's base path.
	ServiceRoot string
	// SessionID is sent in the header named by the contract's api_key_auth
	// security scheme on every request.
	SessionID string
	// HTTPClient performs the requests. NewClient always sets one.
	HTTPClient *http.Client
	// PollInterval is the delay between two Snapservice.Tasks_get requests. No
	// delay is taken before the first one.
	PollInterval time.Duration
	// MaxPolls caps the number of Snapservice.Tasks_get requests issued for a
	// single snapshot operation.
	MaxPolls int
}

// ErrInvalidRequest marks a problem the client detects locally, before any
// request reaches the appliance.
var ErrInvalidRequest = errors.New("snapservice: invalid request")

// Error reports a failure that the appliance is responsible for: a non-2xx
// response, a response body the client cannot use, or a task that reached a
// failed terminal state.
//
// Msg never carries a response body, an appliance-supplied localizable message
// or the session token.
type Error struct {
	// Op is the operationId named in the contract, or "" when the failure is
	// not attributable to a single operation.
	Op string
	// Status is the HTTP status code, or 0 when the failure is not an HTTP
	// status.
	Status int
	// Msg describes the failure.
	Msg string
}

func (e *Error) Error() string {
	switch {
	case e.Op != "" && e.Status != 0:
		return fmt.Sprintf("snapservice: %s: http %d: %s", e.Op, e.Status, e.Msg)
	case e.Op != "":
		return fmt.Sprintf("snapservice: %s: %s", e.Op, e.Msg)
	case e.Status != 0:
		return fmt.Sprintf("snapservice: http %d: %s", e.Status, e.Msg)
	default:
		return "snapservice: " + e.Msg
	}
}
