// Package verify holds the protected acceptance harness for the vCenter
// tagging inventory integration. It drives the vctag package against the
// contract pinned loopback double in internal/mockvc and asserts the exact
// wire shape of every request the client makes.
//
// This file is part of the protected harness. Do not modify it.
package verify
