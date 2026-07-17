package store

import (
	"fmt"
	"sort"

	"go-stockroom/domain"
)

// Rows returns a warehouse's stock rows, ordered by SKU.
func (s *Store) Rows(warehouse string) ([]domain.StockRow, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.has(warehouse) {
		return nil, fmt.Errorf("%w: %s", domain.ErrUnknownWarehouse, warehouse)
	}
	return s.rows[warehouse], nil
}

// Row returns one SKU's row.
func (s *Store) Row(warehouse, sku string) (domain.StockRow, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.has(warehouse) {
		return domain.StockRow{}, fmt.Errorf("%w: %s", domain.ErrUnknownWarehouse, warehouse)
	}
	if i, ok := s.rowIndex(warehouse, sku); ok {
		return s.rows[warehouse][i], nil
	}
	return domain.StockRow{}, fmt.Errorf("%w: %s", domain.ErrUnknownSKU, sku)
}

// ApplyMovement records a restock or pick and keeps the row set and the
// movement log consistent under one lock.
func (s *Store) ApplyMovement(warehouse string, kind domain.MovementKind, sku string, qty int) (domain.StockRow, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.has(warehouse) {
		return domain.StockRow{}, fmt.Errorf("%w: %s", domain.ErrUnknownWarehouse, warehouse)
	}
	if err := domain.ValidateQty(qty); err != nil {
		return domain.StockRow{}, err
	}

	i, ok := s.rowIndex(warehouse, sku)
	switch kind {
	case domain.MovementRestock:
		if ok {
			s.rows[warehouse][i].Qty += qty
		} else {
			s.insertRow(warehouse, i, domain.StockRow{SKU: sku, Qty: qty})
		}
	case domain.MovementPick:
		if !ok {
			return domain.StockRow{}, fmt.Errorf("%w: %s", domain.ErrUnknownSKU, sku)
		}
		if s.rows[warehouse][i].Qty < qty {
			return domain.StockRow{}, fmt.Errorf("%w: %s has %d, want %d",
				domain.ErrInsufficientStock, sku, s.rows[warehouse][i].Qty, qty)
		}
		s.rows[warehouse][i].Qty -= qty
	default:
		return domain.StockRow{}, fmt.Errorf("unknown movement kind %q", kind)
	}

	s.seq[warehouse]++
	s.movements[warehouse] = append(s.movements[warehouse], domain.Movement{
		Seq: s.seq[warehouse], SKU: sku, Kind: kind, Qty: qty,
	})

	i, _ = s.rowIndex(warehouse, sku)
	return s.rows[warehouse][i], nil
}

// rowIndex binary-searches the SKU-ordered row set. Must be called with
// s.mu held. Returns the row's index, or the insertion point when the
// SKU is absent.
func (s *Store) rowIndex(warehouse, sku string) (int, bool) {
	rows := s.rows[warehouse]
	i := sort.Search(len(rows), func(k int) bool { return rows[k].SKU >= sku })
	return i, i < len(rows) && rows[i].SKU == sku
}

// insertRow places a new row at index i, keeping SKU order. Must be
// called with s.mu held.
func (s *Store) insertRow(warehouse string, i int, row domain.StockRow) {
	rows := append(s.rows[warehouse], domain.StockRow{})
	copy(rows[i+1:], rows[i:])
	rows[i] = row
	s.rows[warehouse] = rows
}
