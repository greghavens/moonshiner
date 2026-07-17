package store

import (
	"fmt"

	"go-stockroom/domain"
)

// AppendLedger records one reconciliation result.
func (s *Store) AppendLedger(warehouse string, entry domain.LedgerEntry) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.has(warehouse) {
		return fmt.Errorf("%w: %s", domain.ErrUnknownWarehouse, warehouse)
	}
	s.ledger[warehouse] = append(s.ledger[warehouse], entry)
	return nil
}

// Ledger returns a copy of a warehouse's reconciliation history, oldest
// first.
func (s *Store) Ledger(warehouse string) ([]domain.LedgerEntry, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.has(warehouse) {
		return nil, fmt.Errorf("%w: %s", domain.ErrUnknownWarehouse, warehouse)
	}
	out := make([]domain.LedgerEntry, len(s.ledger[warehouse]))
	copy(out, s.ledger[warehouse])
	return out, nil
}

// Lock starts a counting session: reconciliation must not run while the
// floor team is mid-count. Movements stay allowed; the floor process
// owns that freeze.
func (s *Store) Lock(warehouse string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.has(warehouse) {
		return fmt.Errorf("%w: %s", domain.ErrUnknownWarehouse, warehouse)
	}
	if s.locks[warehouse] {
		return fmt.Errorf("%w: %s", domain.ErrLocked, warehouse)
	}
	s.locks[warehouse] = true
	return nil
}

// Unlock ends a counting session.
func (s *Store) Unlock(warehouse string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.has(warehouse) {
		return fmt.Errorf("%w: %s", domain.ErrUnknownWarehouse, warehouse)
	}
	if !s.locks[warehouse] {
		return fmt.Errorf("%w: %s", domain.ErrNotLocked, warehouse)
	}
	s.locks[warehouse] = false
	return nil
}

// IsLocked reports whether a counting session is in progress.
func (s *Store) IsLocked(warehouse string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.locks[warehouse]
}
