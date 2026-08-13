// Package wire verifies that a recorded request had the exact shape it was
// supposed to have.
//
// "Exact" is meant strictly, and in both directions. A query parameter the
// expectation does not list must not have been sent — not sent empty, not
// sent as the zero value, not sent at all. A JSON body key the expectation
// does not list must be absent from the object. This is the whole point of the
// package: the interesting bugs in a client of an API whose fields are almost
// all optional are the fields it sends when it should have stayed quiet.
//
// Every exported symbol here has a fixed signature. The bodies are yours.
package wire

import (
	"errors"
	"net/url"

	"vcfauto/mock"
)

var errNotImplemented = errors.New("wire: not implemented")

// Expectation is the exact shape a request must have had.
type Expectation struct {
	// Operation is the contract operation ID the request must have matched.
	Operation string

	Method string

	// Path is the concrete request path, with path parameters substituted.
	Path string

	// Query is the complete set of query parameters the request must have
	// carried. A parameter absent from Query must be absent from the
	// request. A nil Query means the request must have carried no query
	// string at all.
	Query url.Values

	// Header lists headers that must be present with exactly these values.
	// Headers not listed are ignored, since the transport adds its own.
	Header map[string]string

	// AbsentHeaders lists headers that must not be present.
	AbsentHeaders []string

	// JSONBody, when non-empty, is the JSON object the body must have been,
	// compared by decoded value rather than by byte equality. A key absent
	// from JSONBody must be absent from the body.
	JSONBody string

	// FormBody, when non-nil, is the complete set of form-encoded values
	// the body must have carried.
	FormBody url.Values

	// NoBody asserts the request carried an empty body.
	NoBody bool

	// Status is the status the server replied with. Zero means "don't
	// check".
	Status int
}

// Check reports how got departs from want, or nil if it matches exactly.
//
// The error must name every discrepancy it found, not just the first, and must
// distinguish "parameter absent" from "parameter present but empty" in its
// wording — those are the two cases this package exists to tell apart.
func Check(got mock.RecordedRequest, want Expectation) error {
	return errNotImplemented
}

// CheckAll matches a recorded sequence against an expected sequence, in order,
// reporting a length mismatch or the first index that differs.
func CheckAll(got []mock.RecordedRequest, want []Expectation) error {
	return errNotImplemented
}
