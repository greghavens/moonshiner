package migration

import (
	"encoding/json"
	"errors"
)

var ErrNotImplemented = errors.New("migration planner not implemented")

// BuildPlan constructs the ordered migration architecture from the estate and
// pinned compatibility documents.
func BuildPlan(estateJSON, compatibilityJSON []byte) ([]byte, error) {
	return nil, ErrNotImplemented
}

// IndentJSON is kept small so callers can write a stable artifact once the
// planner is implemented.
func IndentJSON(v any) ([]byte, error) {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(b, '\n'), nil
}
