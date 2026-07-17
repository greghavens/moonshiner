// Package store is the in-memory persistence layer. One Store per
// service instance; every method is safe for concurrent use.
package store

import (
	"fmt"
	"sort"
	"sync"

	"go-stockroom/domain"
)

type Store struct {
	mu         sync.Mutex
	warehouses map[string]bool
	rows       map[string][]domain.StockRow // per warehouse, ordered by SKU
	movements  map[string][]domain.Movement // per warehouse, append-only
	ledger     map[string][]domain.LedgerEntry
	locks      map[string]bool
	seq        map[string]int
}

func New() *Store {
	return &Store{
		warehouses: make(map[string]bool),
		rows:       make(map[string][]domain.StockRow),
		movements:  make(map[string][]domain.Movement),
		ledger:     make(map[string][]domain.LedgerEntry),
		locks:      make(map[string]bool),
		seq:        make(map[string]int),
	}
}

func (s *Store) AddWarehouse(id string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.warehouses[id] {
		return fmt.Errorf("%w: %s", domain.ErrWarehouseExists, id)
	}
	s.warehouses[id] = true
	return nil
}

// Warehouses lists known warehouse ids in stable (sorted) order.
func (s *Store) Warehouses() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	ids := make([]string, 0, len(s.warehouses))
	for id := range s.warehouses {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}

// has must be called with s.mu held.
func (s *Store) has(warehouse string) bool {
	return s.warehouses[warehouse]
}
