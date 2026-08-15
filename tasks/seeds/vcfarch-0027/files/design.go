package architecture

import (
	"encoding/json"
	"errors"
)

// Artifacts is the machine-readable output of the architecture package.
type Artifacts struct {
	SddcSpec      json.RawMessage
	MigrationPlan json.RawMessage
}

// Build returns the greenfield installer specification and estate migration plan.
func Build() (Artifacts, error) {
	return Artifacts{}, errors.New("architecture not implemented")
}
