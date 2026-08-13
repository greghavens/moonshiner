// Package credrotate rotates resource credentials held by a VMware Cloud
// Foundation 9.0 SDDC Manager appliance.
//
// Rotation is asynchronous and destructive: once the appliance starts changing
// a password, the old secret stops working, and any request still in flight on
// that old secret is stranded. The Store here exists so that does not happen.
// Callers borrow a secret through Acquire and hand it back through the returned
// release function; a rotation seals the affected credentials, waits for the
// borrowed ones to come back, and only then asks SDDC Manager to change
// anything. New secrets are published from the settled task before the seal is
// lifted, so nobody ever borrows a secret the appliance has already retired.
//
// A rotation can also settle partially. SDDC Manager reports per-resource
// progress in the credentials task's subtasks, so the store is updated per
// credential from the subtask outcomes rather than from the overall status.
//
// See docs/contract.json for the wire contract and docs/official_sources.json
// for its provenance.
package credrotate

import (
	"context"
	"errors"
	"net/http"
	"strconv"
	"time"
)

// Key identifies one credential. SDDC Manager reports rotation progress per
// resource name and username, so that pair, not the resource id, is the
// correlation key between a request and the settled task's subtasks.
type Key struct {
	ResourceName string
	Username     string
}

// Secret is the credential a caller borrows to reach a VCF resource.
// Generation starts at 1 for every secret handed to NewStore and increases by
// one each time the appliance confirms a new password for that credential.
type Secret struct {
	Username   string
	Password   string
	Generation uint64
}

// Store holds the secrets in use and coordinates borrowing against rotation.
// Its zero value is not usable; build one with NewStore. All methods are safe
// for concurrent use.
type Store struct {
	// Replaced by the implementation.
	unimplemented struct{}
}

// NewStore builds a store over the supplied secrets. Every secret is stamped
// with generation 1. The supplied map is not retained.
func NewStore(secrets map[Key]Secret) (*Store, error) {
	return nil, errors.New("credrotate: NewStore is not implemented")
}

// Acquire borrows the current secret for key and returns a release function the
// caller must invoke once it is done with it. While a rotation of key is in
// flight, Acquire blocks until that rotation settles and then returns whichever
// secret is live afterwards. It returns ctx.Err() if ctx ends first, and an
// error if the store does not hold key.
func (s *Store) Acquire(ctx context.Context, key Key) (Secret, func(), error) {
	return Secret{}, nil, errors.New("credrotate: Acquire is not implemented")
}

// Snapshot returns a copy of every secret the store currently holds.
func (s *Store) Snapshot() map[Key]Secret {
	return nil
}

// CredentialSpec is one credential of a resource to change. A blank optional
// field is unset and must not reach the wire in any form.
type CredentialSpec struct {
	// Username is required.
	Username string

	CredentialType string
	AccountType    string

	// Password is the operator-supplied replacement. It is required for an
	// UPDATE and must be left blank for any other operation type, because the
	// appliance generates the replacement itself.
	Password string
}

// ResourceSpec is one resource whose credentials are being changed.
type ResourceSpec struct {
	// ResourceName is required: it is what the settled task's subtasks report
	// back, and it is half of the Key used to update the store.
	ResourceName string

	// ResourceID is optional.
	ResourceID string

	// ResourceType is required and must be one of the values the pinned
	// 9.0.0.0 specification enumerates.
	ResourceType string

	Credentials []CredentialSpec
}

// AutoRotatePolicy is the optional auto-rotation policy carried alongside a
// change. Enable is a required member of the specification's input spec and is
// therefore sent even when false; FrequencyInDays is optional and is sent only
// when it is nonzero.
type AutoRotatePolicy struct {
	Enable          bool
	FrequencyInDays int32
}

// RotateRequest is one credential change.
type RotateRequest struct {
	// OperationType must be one of the values the pinned specification
	// enumerates.
	OperationType string

	Resources []ResourceSpec

	// AutoRotate is optional. A nil pointer means the whole member is absent.
	AutoRotate *AutoRotatePolicy
}

// Outcome is what the settled credentials task reported for one credential.
type Outcome struct {
	Key    Key
	Status string

	// SecretChanged reports whether the appliance confirmed a new password for
	// this credential, which is the only reason the store is updated for it.
	SecretChanged bool

	ErrorCode string
	Message   string
}

// Result describes how far a rotation got. Rotate always returns a populated
// Result, including alongside a non-nil error.
type Result struct {
	TaskID string

	// TaskStatus is the normalized status last observed for the credentials
	// task, or the normalized status of the accepted Task when polling never
	// produced one.
	TaskStatus string

	// Succeeded is true only when the credentials task settled SUCCESSFUL.
	Succeeded bool

	// Cancelled reports whether cancelCredentialsTask was issued.
	Cancelled bool

	// Outcomes is one entry per correlated subtask, in subtask order.
	Outcomes []Outcome

	// Rotated and Retained partition the sealed credentials in request order:
	// Rotated moved to a new secret, Retained kept the one they had.
	Rotated  []Key
	Retained []Key
}

// APIError is a non-success HTTP status from SDDC Manager.
type APIError struct {
	OperationID string
	StatusCode  int
	ErrorCode   string
	Message     string
}

func (e *APIError) Error() string {
	return "credrotate: " + e.OperationID + ": http " + strconv.Itoa(e.StatusCode) + ": " + e.ErrorCode + ": " + e.Message
}

// RotationFailedError is a credentials task that settled in an unsuccessful
// terminal state. Every HTTP call succeeded; the change still did not.
type RotationFailedError struct {
	TaskID     string
	TaskStatus string
	ErrorCode  string
	Message    string
	Outcomes   []Outcome
}

func (e *RotationFailedError) Error() string {
	return "credrotate: credentials task " + e.TaskID + " ended " + e.TaskStatus + ": " + e.ErrorCode + ": " + e.Message
}

// Client talks to one SDDC Manager appliance.
type Client struct {
	baseURL     string
	accessToken string
	httpClient  *http.Client
}

// NewClient builds a client for the SDDC Manager service root. A nil
// httpClient means http.DefaultClient.
func NewClient(baseURL, accessToken string, httpClient *http.Client) (*Client, error) {
	return nil, errors.New("credrotate: NewClient is not implemented")
}

// Rotate applies req and waits for the resulting credentials task to settle,
// updating store from the settled subtasks before any sealed credential can be
// borrowed again.
func (c *Client) Rotate(ctx context.Context, store *Store, req RotateRequest, pollInterval time.Duration) (Result, error) {
	return Result{}, errors.New("credrotate: Rotate is not implemented")
}

var _ = time.Second
