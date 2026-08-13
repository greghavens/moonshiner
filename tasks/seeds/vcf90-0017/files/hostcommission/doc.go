// Package hostcommission commissions ESXi hosts into a VMware Cloud Foundation
// 9.0 fleet through the SDDC Manager API: acquire a token, validate the host
// commission specification, poll the validation to a terminal state, commission
// the hosts, then poll the resulting task to a terminal state.
//
// Both halves of the flow are asynchronous. validateHostCommissionSpec answers
// 202 with a Validation whose executionStatus is still running, and
// commissionHosts answers 202 with a Task that has only been accepted. Neither
// response is the outcome; each has to be polled until its status is terminal.
//
// The wire contract for this package is docs/contract.json, derived from the
// OpenAPI specification recorded in docs/official_sources.json. It is the
// authority on paths, required versus optional properties, headers and the two
// status vocabularies. The rule that catches most implementations is
// contract.omitEmptyRule: an optional property the caller did not set must be
// absent from the serialized request, not present and empty.
//
// This package is a stub. Implement Config, Client, New, HostSpec, Validation,
// ValidationCheck, Task, Result, CommissionHosts and the ErrInvalidRequest,
// ErrValidationFailed, ErrTaskFailed, ErrPollTimeout and ErrAPI sentinels, and
// add table-driven tests alongside them.
package hostcommission
