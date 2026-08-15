// Package migration contains Northstar's machine-readable VCF migration design.
package migration

import _ "embed"

//go:embed plan.json
var plan []byte

// Document returns an independent copy of the embedded migration plan.
func Document() []byte {
	return append([]byte(nil), plan...)
}
