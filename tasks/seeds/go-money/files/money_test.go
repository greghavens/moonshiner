package money

import (
	"errors"
	"testing"
)

func TestNewAndAccessors(t *testing.T) {
	m := New(1234, "USD")
	if m.Amount() != 1234 || m.Currency() != "USD" {
		t.Fatalf("got %d %s, want 1234 USD", m.Amount(), m.Currency())
	}
	if m.IsZero() {
		t.Fatal("1234 cents must not be zero")
	}
	if !New(0, "EUR").IsZero() {
		t.Fatal("0 cents must be zero")
	}
}

func TestAddAndSubSameCurrency(t *testing.T) {
	a, b := New(250, "USD"), New(120, "USD")
	sum, err := a.Add(b)
	if err != nil {
		t.Fatalf("Add: %v", err)
	}
	if sum != New(370, "USD") {
		t.Fatalf("Add = %v, want 3.70 USD", sum)
	}
	diff, err := a.Sub(b)
	if err != nil {
		t.Fatalf("Sub: %v", err)
	}
	if diff != New(130, "USD") {
		t.Fatalf("Sub = %v, want 1.30 USD", diff)
	}
}

func TestMixingCurrenciesFails(t *testing.T) {
	_, err := New(100, "USD").Add(New(100, "EUR"))
	if !errors.Is(err, ErrCurrencyMismatch) {
		t.Fatalf("Add across currencies: err = %v, want ErrCurrencyMismatch", err)
	}
	_, err = New(100, "USD").Sub(New(100, "GBP"))
	if !errors.Is(err, ErrCurrencyMismatch) {
		t.Fatalf("Sub across currencies: err = %v, want ErrCurrencyMismatch", err)
	}
}

func TestNegFlipsSign(t *testing.T) {
	if got := New(75, "USD").Neg(); got != New(-75, "USD") {
		t.Fatalf("Neg = %v", got)
	}
	if got := New(-75, "USD").Neg(); got != New(75, "USD") {
		t.Fatalf("Neg of negative = %v", got)
	}
}

func TestStringFormatsMinorUnits(t *testing.T) {
	cases := []struct {
		m    Money
		want string
	}{
		{New(1234, "USD"), "12.34 USD"},
		{New(5, "USD"), "0.05 USD"},
		{New(-5, "EUR"), "-0.05 EUR"},
		{New(0, "GBP"), "0.00 GBP"},
		{New(-120000, "USD"), "-1200.00 USD"},
	}
	for _, c := range cases {
		if got := c.m.String(); got != c.want {
			t.Fatalf("String() = %q, want %q", got, c.want)
		}
	}
}
