// Package money implements exact currency arithmetic for the billing
// service. Amounts are stored as int64 minor units (cents), never
// floats, so totals reconcile to the cent. Only two-decimal
// currencies are in scope for now.
package money

import (
	"errors"
	"fmt"
)

// ErrCurrencyMismatch is returned when arithmetic mixes currencies.
var ErrCurrencyMismatch = errors.New("money: currency mismatch")

// Money is an exact amount in a currency's minor units. Money values
// are comparable with ==.
type Money struct {
	amount   int64
	currency string
}

// New returns amount minor units (cents) of currency.
func New(amount int64, currency string) Money {
	return Money{amount: amount, currency: currency}
}

// Amount reports the amount in minor units.
func (m Money) Amount() int64 { return m.amount }

// Currency reports the currency code.
func (m Money) Currency() string { return m.currency }

// IsZero reports whether the amount is exactly zero.
func (m Money) IsZero() bool { return m.amount == 0 }

// Neg returns the amount negated.
func (m Money) Neg() Money { return Money{amount: -m.amount, currency: m.currency} }

// Add returns m + o. Both operands must share a currency.
func (m Money) Add(o Money) (Money, error) {
	if m.currency != o.currency {
		return Money{}, fmt.Errorf("%w: %s vs %s", ErrCurrencyMismatch, m.currency, o.currency)
	}
	return Money{amount: m.amount + o.amount, currency: m.currency}, nil
}

// Sub returns m - o. Both operands must share a currency.
func (m Money) Sub(o Money) (Money, error) { return m.Add(o.Neg()) }

// String renders the amount with two decimals: "12.34 USD", "-0.05 EUR".
func (m Money) String() string {
	a, sign := m.amount, ""
	if a < 0 {
		sign, a = "-", -a
	}
	return fmt.Sprintf("%s%d.%02d %s", sign, a/100, a%100, m.currency)
}
