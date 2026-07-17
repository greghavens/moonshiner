// Package spanoverlap reports which sensor capture windows overlap.
//
// Vibration probes record in bursts; each capture window is a half-open
// interval [Start, End) in microseconds since boot. Post-processing needs
// every pair of windows that were live at the same instant (their samples
// have to be cross-correlated). A window with End <= Start is empty and
// overlaps nothing.
//
// Overlaps returns every index pair (A < B into the input slice) whose
// windows intersect, sorted ascending by A then B.
//
// Check accounting: Budget.Checks counts candidate-pair examinations —
// it must be incremented every time the implementation weighs a specific
// pair of windows, whether that is testing two windows for intersection
// or emitting a pair it already knows intersects. When MaxChecks > 0 and
// the count would pass it, Overlaps stops promptly and returns ErrBudget
// with a nil pair list. A nil *Budget means "don't count, no limit".
package spanoverlap

import "errors"

// Span is one capture window, half-open: [Start, End).
type Span struct {
	Start int64
	End   int64
}

// Pair identifies two overlapping input windows by index, A < B.
type Pair struct {
	A int
	B int
}

// Budget bounds and reports how many candidate pairs an Overlaps call
// examined. The perf suite passes one in and asserts the total.
type Budget struct {
	MaxChecks int64 // 0 = unlimited
	Checks    int64
}

// ErrBudget is returned when a Budget's MaxChecks is exhausted.
var ErrBudget = errors.New("spanoverlap: check budget exhausted")

// spend records one candidate-pair examination and reports whether the
// caller may continue.
func (b *Budget) spend() bool {
	if b == nil {
		return true
	}
	b.Checks++
	return b.MaxChecks <= 0 || b.Checks <= b.MaxChecks
}

func intersects(a, b Span) bool {
	return a.Start < a.End && b.Start < b.End && a.Start < b.End && b.Start < a.End
}

// Overlaps reports every overlapping pair of windows in spans.
func Overlaps(spans []Span, budget *Budget) ([]Pair, error) {
	var out []Pair
	for i := 0; i < len(spans); i++ {
		for j := i + 1; j < len(spans); j++ {
			if !budget.spend() {
				return nil, ErrBudget
			}
			if intersects(spans[i], spans[j]) {
				out = append(out, Pair{A: i, B: j})
			}
		}
	}
	return out, nil
}
