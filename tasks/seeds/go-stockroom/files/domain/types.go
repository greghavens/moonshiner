// Package domain holds the pure business types and rules of the
// stockroom service. It depends on nothing but the standard library.
package domain

import "time"

// StockRow is one SKU's on-hand quantity in one warehouse.
type StockRow struct {
	SKU string `json:"sku"`
	Qty int    `json:"qty"`
}

// MovementKind labels an audit-trail entry.
type MovementKind string

const (
	MovementRestock MovementKind = "restock"
	MovementPick    MovementKind = "pick"
)

// Movement is one stock change. Seq is assigned by the store and is
// strictly increasing per warehouse.
type Movement struct {
	Seq  int          `json:"seq"`
	SKU  string       `json:"sku"`
	Kind MovementKind `json:"kind"`
	Qty  int          `json:"qty"`
}

// LedgerEntry is the result of one reconciliation run for a warehouse.
type LedgerEntry struct {
	Warehouse  string    `json:"warehouse"`
	RunAt      time.Time `json:"run_at"`
	Checked    int       `json:"checked"`
	Mismatched int       `json:"mismatched"`
}
