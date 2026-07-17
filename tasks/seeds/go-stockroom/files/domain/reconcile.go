package domain

import "time"

// BuildLedgerEntry reconciles one warehouse: stored quantities are
// compared against what the movement history says should be on hand.
// A mismatch means shrinkage, a miscount, or a write that skipped the
// movement log — the ledger only counts them, ops investigates.
func BuildLedgerEntry(warehouse string, runAt time.Time, rows []StockRow, movements []Movement) LedgerEntry {
	expected := OnHand(movements)
	mismatched := 0
	for _, row := range rows {
		if expected[row.SKU] != row.Qty {
			mismatched++
		}
	}
	return LedgerEntry{
		Warehouse:  warehouse,
		RunAt:      runAt,
		Checked:    len(rows),
		Mismatched: mismatched,
	}
}
