package vcfarch

import "errors"

// Build returns the VCF architecture for inventory under the pinned snapshot.
func Build(inventory Inventory, snapshot CompatibilitySnapshot) (Architecture, error) {
	return Architecture{}, errors.New("vcf architecture is not implemented")
}
