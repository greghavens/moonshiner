// Package reportclient drives the VCF Operations report-generation flow:
// acquire a token, create a report, poll the report to a terminal state, then
// download it.
//
// The flow is asynchronous. createReport returns as soon as the request is
// queued, so its response status is not the outcome - the report has to be
// polled with getReport until the status is terminal.
//
// The wire contract this package must honour lives in docs/contract.json, which
// was derived from the OpenAPI specification recorded in
// docs/official_sources.json. The rule that catches most implementations is
// contract.omitEmptyRule: an optional field the caller did not set must be
// absent from the serialized request, not present and empty.
//
// This package is a stub. Implement Config, Client, New, ReportRequest,
// TraversalSpec, Report, Result, GenerateReport and the ErrInvalidRequest,
// ErrReportFailed and ErrPollTimeout sentinels, and add table-driven tests
// alongside them.
package reportclient
