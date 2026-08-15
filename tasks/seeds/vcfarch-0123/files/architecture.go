package vcfarch

import "errors"

// Build returns the deterministic architecture artifact derived from the
// supplied estate inventory and pinned compatibility snapshot.
func Build(estateJSON, snapshotJSON []byte) ([]byte, error) {
	return nil, errors.New("vcf architecture builder is not implemented")
}
