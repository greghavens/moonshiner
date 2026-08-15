// Package architecture builds a VMware Cloud Foundation installer artifact.
package architecture

import "errors"

// Build returns a VCF Installer SddcSpec derived from the supplied estate
// requirements and pinned compatibility snapshot.
func Build(estateJSON, compatibilityJSON []byte) ([]byte, error) {
	return nil, errors.New("architecture design is not implemented")
}
