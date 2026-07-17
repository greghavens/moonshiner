// Package manifest loads the deploy manifests produced by the release
// tooling. A manifest is a JSON document describing every service in a
// release: image, scaling, ports, environment and health checking.
package manifest

import (
	"encoding/json"
	"fmt"
	"io"
)

// Service is one deployable unit from the manifest.
type Service struct {
	Name      string            `json:"name"`
	Image     string            `json:"image"`
	Replicas  int               `json:"replicas"`
	Ports     []int             `json:"ports"`
	Env       map[string]string `json:"environment"`
	CheckPath string
}

// Manifest is the full release document.
type Manifest struct {
	Release  string    `json:"release"`
	Services []Service `json:"services"`
}

// Load parses a manifest, applies defaults and validates it.
func Load(r io.Reader) (*Manifest, error) {
	var m Manifest
	dec := json.NewDecoder(r)
	if err := dec.Decode(&m); err != nil {
		return nil, fmt.Errorf("manifest: %w", err)
	}
	if m.Release == "" {
		return nil, fmt.Errorf("manifest: missing release name")
	}
	if len(m.Services) == 0 {
		return nil, fmt.Errorf("manifest: release %s declares no services", m.Release)
	}
	seen := make(map[string]bool)
	for i := range m.Services {
		s := &m.Services[i]
		if s.Name == "" || s.Image == "" {
			return nil, fmt.Errorf("manifest: service #%d needs both name and image", i)
		}
		if seen[s.Name] {
			return nil, fmt.Errorf("manifest: duplicate service %s", s.Name)
		}
		seen[s.Name] = true
		if s.Replicas <= 0 {
			s.Replicas = 1
		}
		if s.CheckPath == "" {
			s.CheckPath = "/healthz"
		}
	}
	return &m, nil
}
