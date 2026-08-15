package migrationplan

import (
	_ "embed"
	"encoding/json"
	"fmt"
)

//go:embed architecture.json
var architectureJSON []byte

// Plan deliberately keeps the installer document open to schema evolution. The
// installer-owned JSON Schema is the normative interface and is applied by the
// verifier before this package decodes the document.
type Plan map[string]any

// JSON returns a copy of the machine-readable architecture artifact.
func JSON() []byte {
	return append([]byte(nil), architectureJSON...)
}

// Architecture decodes the embedded artifact after schema validation has taken
// place at the installer boundary.
func Architecture() (Plan, error) {
	var plan Plan
	if err := json.Unmarshal(architectureJSON, &plan); err != nil {
		return nil, fmt.Errorf("decode migration architecture: %w", err)
	}
	return plan, nil
}
