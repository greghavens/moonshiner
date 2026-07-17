// Package ledger holds the in-memory account balances behind the payments
// API. Balances are written by transfer batches coming off the queue and
// read concurrently by the balance endpoint, so all access goes through
// the ledger's lock.
package ledger

import (
	"fmt"
	"sync"
)

// Txn moves Amount cents from one account to another.
type Txn struct {
	From   string
	To     string
	Amount int64
}

// Ledger is a set of account balances safe for concurrent use.
type Ledger struct {
	mu       sync.Mutex
	balances map[string]int64
}

// New starts a ledger with the given opening balances (cents).
func New(opening map[string]int64) *Ledger {
	b := make(map[string]int64, len(opening))
	for acct, cents := range opening {
		b[acct] = cents
	}
	return &Ledger{balances: b}
}

// Balance returns the current balance for an account (0 if unknown).
func (l *Ledger) Balance(acct string) int64 {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.balances[acct]
}

// ApplyBatch applies the transactions in order, stopping at the first one
// that is invalid or would overdraw its source account. It returns how
// many transactions were applied.
func (l *Ledger) ApplyBatch(txns []Txn) (int, error) {
	applied := 0
	for i, t := range txns {
		l.mu.Lock()
		defer l.mu.Unlock()
		if t.Amount <= 0 {
			return applied, fmt.Errorf("txn %d: non-positive amount %d", i, t.Amount)
		}
		if t.From == t.To {
			return applied, fmt.Errorf("txn %d: %s cannot transfer to itself", i, t.From)
		}
		if l.balances[t.From] < t.Amount {
			return applied, fmt.Errorf("txn %d: %s has insufficient funds", i, t.From)
		}
		l.balances[t.From] -= t.Amount
		l.balances[t.To] += t.Amount
		applied++
	}
	return applied, nil
}
