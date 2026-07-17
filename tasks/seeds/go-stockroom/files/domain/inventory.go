package domain

import "sort"

// AvailabilityReport ranks rows for the ops dashboard: highest quantity
// first, SKU as the tiebreaker. top limits the result (top <= 0 = all).
func AvailabilityReport(rows []StockRow, top int) []StockRow {
	sort.Slice(rows, func(i, j int) bool {
		if rows[i].Qty != rows[j].Qty {
			return rows[i].Qty > rows[j].Qty
		}
		return rows[i].SKU < rows[j].SKU
	})
	if top > 0 && top < len(rows) {
		return rows[:top]
	}
	return rows
}

// OnHand folds a movement history into per-SKU quantities.
func OnHand(movements []Movement) map[string]int {
	totals := make(map[string]int, len(movements))
	for _, m := range movements {
		switch m.Kind {
		case MovementRestock:
			totals[m.SKU] += m.Qty
		case MovementPick:
			totals[m.SKU] -= m.Qty
		}
	}
	return totals
}

// ValidateQty rejects zero and negative movement quantities.
func ValidateQty(qty int) error {
	if qty <= 0 {
		return ErrInvalidQty
	}
	return nil
}
