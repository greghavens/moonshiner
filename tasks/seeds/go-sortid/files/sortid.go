// Package sortid issues identifiers for objects written by the ingest
// pipeline. Every blob, job, and manifest gets one at creation time.
// Today the IDs are simple per-process counter IDs, unique within a
// single Generator.
package sortid

import (
	"fmt"
	"strings"
	"sync"
)

// Generator hands out sequential zero-padded IDs with a fixed prefix,
// e.g. "job-000001", "job-000002". Safe for concurrent use.
type Generator struct {
	mu     sync.Mutex
	prefix string
	last   uint64
}

// New returns a Generator whose IDs carry the given prefix.
func New(prefix string) *Generator {
	return &Generator{prefix: prefix}
}

// Next returns the next ID in the sequence. The first ID a Generator
// hands out ends in 000001.
func (g *Generator) Next() string {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.last++
	return fmt.Sprintf("%s-%06d", g.prefix, g.last)
}

// Issued reports how many IDs this Generator has handed out so far.
func (g *Generator) Issued() uint64 {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.last
}

// HasPrefix reports whether id was plausibly issued by a Generator
// created with the given prefix.
func HasPrefix(id, prefix string) bool {
	return strings.HasPrefix(id, prefix+"-")
}
