// Package inventory mirrors the deploy service's component inventory
// and caches the latest decoded record for each component between
// syncs.
package inventory

import (
	"encoding/json"
	"fmt"
)

// Component is one deployable unit. The wire contract is strict:
// "tags" is always a JSON array — [] when empty, never null — and
// "notes" is omitted entirely when empty.
type Component struct {
	ID    string   `json:"id"`
	Tags  []string `json:"tags"`
	Notes string   `json:"notes,omitempty"`
}

// Encode renders c as a wire record for the deploy API.
func Encode(c Component) ([]byte, error) {
	return json.Marshal(c)
}

// Decode parses a wire record.
func Decode(data []byte) (Component, error) {
	var c Component
	if err := json.Unmarshal(data, &c); err != nil {
		return Component{}, fmt.Errorf("decode component: %w", err)
	}
	return c, nil
}

// Cache holds the latest known record per component id. Records
// handed out of the cache are snapshots: nothing a caller does to a
// returned Component may change what the cache holds.
type Cache struct {
	byID map[string]Component
}

// NewCache returns an empty cache.
func NewCache() *Cache {
	return &Cache{byID: make(map[string]Component)}
}

// Sync decodes data, stores the record as the latest for its id, and
// returns the decoded record.
func (c *Cache) Sync(data []byte) (Component, error) {
	comp, err := Decode(data)
	if err != nil {
		return Component{}, err
	}
	c.byID[comp.ID] = comp
	return comp, nil
}

// Get returns the cached record for id.
func (c *Cache) Get(id string) (Component, bool) {
	comp, ok := c.byID[id]
	return comp, ok
}
