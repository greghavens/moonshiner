package keycatalog

import (
	"errors"
	"fmt"
	"strings"
)

// ErrCollision identifies a catalog whose persisted records contain two or
// more spellings of the same protocol identity.
var ErrCollision = errors.New("identifier collision")

// Record is one persisted catalog record. ID is also its display spelling.
type Record struct {
	ID      string `json:"id"`
	Owner   string `json:"owner"`
	Payload string `json:"payload"`
}

// Collision describes one lookup identity occupied by multiple persisted IDs.
// LookupKey is the normalized internal comparison key, not a display ID.
type Collision struct {
	LookupKey string
	IDs       []string
}

// CollisionError reports every colliding lookup identity in a persisted
// catalog. Load returns no partial Catalog with this error.
type CollisionError struct {
	Collisions []Collision
}

func (e *CollisionError) Error() string {
	if e == nil {
		return ErrCollision.Error()
	}
	var message strings.Builder
	message.WriteString(ErrCollision.Error())
	for _, collision := range e.Collisions {
		fmt.Fprintf(
			&message,
			"; %q <- %q",
			collision.LookupKey,
			collision.IDs,
		)
	}
	return message.String()
}

func (e *CollisionError) Unwrap() error {
	return ErrCollision
}
