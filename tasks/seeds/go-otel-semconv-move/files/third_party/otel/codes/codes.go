// Package codes defines OpenTelemetry span status codes.
package codes

// Code is the status of a span.
type Code uint32

const (
	// Unset leaves the span without an explicit error status.
	Unset Code = iota
	// Error indicates that the operation represented by the span failed.
	Error
)
