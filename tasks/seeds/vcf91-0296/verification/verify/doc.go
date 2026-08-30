// Package verify holds the protected acceptance suite for internal/opsnet.
//
// It drives the client against the loopback mock in internal/opsnetmock and
// asserts the exact request wire shape recorded in the mock's request log. No
// live VMware endpoint is contacted.
//
// This package is part of the protected harness. Do not edit it.
package verify
