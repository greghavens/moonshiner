package store

import (
	"fmt"

	"go-stockroom/domain"
)

// Movements returns a copy of a warehouse's audit trail in seq order.
func (s *Store) Movements(warehouse string) ([]domain.Movement, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.has(warehouse) {
		return nil, fmt.Errorf("%w: %s", domain.ErrUnknownWarehouse, warehouse)
	}
	out := make([]domain.Movement, len(s.movements[warehouse]))
	copy(out, s.movements[warehouse])
	return out, nil
}

// MovementCount reports the audit-trail length without copying it; the
// dashboard polls this.
func (s *Store) MovementCount(warehouse string) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.has(warehouse) {
		return 0, fmt.Errorf("%w: %s", domain.ErrUnknownWarehouse, warehouse)
	}
	return len(s.movements[warehouse]), nil
}
