// Package router maps request paths to handlers. It is deliberately
// transport-agnostic — the handler is a type parameter, so the gateway
// registers http.Handlers while the CLI registers command funcs.
package router

import (
	"errors"
	"fmt"
	"strings"
)

// ErrDuplicate is wrapped into the error returned when a pattern is
// registered twice.
var ErrDuplicate = errors.New("pattern already registered")

// Router matches paths against registered patterns.
type Router[H any] struct {
	byPattern map[string]H
	order     []string // registration order, for List
}

// New returns an empty router.
func New[H any]() *Router[H] {
	return &Router[H]{byPattern: make(map[string]H)}
}

// Handle registers h under pattern. Patterns must start with "/";
// trailing slashes are normalized away ("/users/" and "/users" are the
// same route). Registering the same pattern twice is an error.
func (r *Router[H]) Handle(pattern string, h H) error {
	p, err := normalize(pattern)
	if err != nil {
		return err
	}
	if _, dup := r.byPattern[p]; dup {
		return fmt.Errorf("router: %w: %s", ErrDuplicate, p)
	}
	r.byPattern[p] = h
	r.order = append(r.order, p)
	return nil
}

// Lookup returns the handler registered for exactly this path.
func (r *Router[H]) Lookup(path string) (H, bool) {
	var zero H
	p, err := normalize(path)
	if err != nil {
		return zero, false
	}
	h, ok := r.byPattern[p]
	if !ok {
		return zero, false
	}
	return h, true
}

// List returns the registered patterns in registration order.
func (r *Router[H]) List() []string {
	out := make([]string, len(r.order))
	copy(out, r.order)
	return out
}

// normalize validates a path or pattern and strips trailing slashes.
func normalize(p string) (string, error) {
	if p == "" || p[0] != '/' {
		return "", fmt.Errorf("router: path %q must start with '/'", p)
	}
	if len(p) > 1 {
		p = strings.TrimRight(p, "/")
		if p == "" {
			p = "/"
		}
	}
	return p, nil
}
