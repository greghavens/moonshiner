// Acceptance suite for spanoverlap.
//
// Pinned behavior: half-open [Start, End) windows, empty (End <= Start)
// windows overlap nothing, result is every intersecting index pair with
// A < B sorted ascending by A then B, Budget accounting per the package
// docs (one Checks increment per candidate-pair examination, prompt
// ErrBudget + nil list on exhaustion, nil *Budget = uncounted).
//
// Run: go test -race -timeout 120s ./...
package spanoverlap

import (
	"encoding/binary"
	"errors"
	"hash/fnv"
	"slices"
	"testing"
)

// ---- deterministic input generator ----------------------------------------

type lcg struct{ s uint32 }

func (l *lcg) next() uint32 {
	l.s = l.s*1664525 + 1013904223
	return l.s
}

func (l *lcg) below(n uint32) uint32 { return l.next() % n }

// genSpans is byte-stable across runs: capture windows over [0, rangeMax)
// with mostly ~10k-long bursts, ~2% zero-length windows and ~1% inverted
// ones (both empty by definition).
func genSpans(n int, rangeMax uint32, seed uint32) []Span {
	rng := &lcg{s: seed}
	spans := make([]Span, n)
	for i := range spans {
		start := int64(rng.below(rangeMax))
		var length int64
		switch rng.below(100) {
		case 0, 1:
			length = 0
		case 2:
			length = -int64(1 + rng.below(50))
		default:
			length = int64(1 + rng.below(20_000))
		}
		spans[i] = Span{Start: start, End: start + length}
	}
	return spans
}

// oraclePairs is the test-local reference: the definition, applied
// directly (only ever used at sizes where that is cheap).
func oraclePairs(spans []Span) []Pair {
	var out []Pair
	for i := 0; i < len(spans); i++ {
		a := spans[i]
		if a.Start >= a.End {
			continue
		}
		for j := i + 1; j < len(spans); j++ {
			b := spans[j]
			if b.Start >= b.End {
				continue
			}
			if a.Start < b.End && b.Start < a.End {
				out = append(out, Pair{A: i, B: j})
			}
		}
	}
	return out
}

// ---- behavior --------------------------------------------------------------

func TestIntersectionRulesExact(t *testing.T) {
	spans := []Span{
		{0, 5},   // 0
		{5, 9},   // 1: touches 0 at 5 -> NOT an overlap (half-open)
		{3, 3},   // 2: empty
		{2, 7},   // 3
		{8, 2},   // 4: inverted -> empty
		{0, 100}, // 5
		{98, 99}, // 6
		{2, 7},   // 7: identical to 3
	}
	want := []Pair{
		{0, 3}, {0, 5}, {0, 7},
		{1, 3}, {1, 5}, {1, 7},
		{3, 5}, {3, 7},
		{5, 6}, {5, 7},
	}
	b := &Budget{}
	got, err := Overlaps(spans, b)
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(got, want) {
		t.Fatalf("pairs mismatch:\n got  %v\n want %v", got, want)
	}
	// accounting honesty: every reported pair was, at minimum, examined
	if b.Checks < int64(len(want)) {
		t.Fatalf("Budget.Checks = %d, below the %d pairs reported", b.Checks, len(want))
	}
}

func TestEmptyAndSingleInputs(t *testing.T) {
	if got, err := Overlaps(nil, nil); err != nil || len(got) != 0 {
		t.Fatalf("nil input: got %v, %v", got, err)
	}
	if got, err := Overlaps([]Span{{1, 10}}, nil); err != nil || len(got) != 0 {
		t.Fatalf("single window: got %v, %v", got, err)
	}
	if got, err := Overlaps([]Span{{3, 3}, {2, 9}, {5, 1}}, nil); err != nil || len(got) != 0 {
		t.Fatalf("empty windows must overlap nothing: got %v, %v", got, err)
	}
}

func TestBudgetExhaustionStopsPromptly(t *testing.T) {
	spans := make([]Span, 100)
	for i := range spans {
		spans[i] = Span{0, 1000} // all 4950 pairs overlap
	}
	b := &Budget{MaxChecks: 100}
	got, err := Overlaps(spans, b)
	if !errors.Is(err, ErrBudget) {
		t.Fatalf("want ErrBudget, got %v (pairs=%d)", err, len(got))
	}
	if got != nil {
		t.Fatalf("exhausted call must return a nil list, got %d pairs", len(got))
	}
	if b.Checks == 0 || b.Checks > 200 {
		t.Fatalf("expected a prompt stop just past MaxChecks, Checks=%d", b.Checks)
	}
}

func TestMediumMatchesOracle(t *testing.T) {
	spans := genSpans(2500, 25_000_000, 20260713)
	want := oraclePairs(spans)
	if len(want) != 2396 { // sanity that the generator didn't drift
		t.Fatalf("oracle expected 2396 pairs, got %d", len(want))
	}
	got, err := Overlaps(spans, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(got, want) {
		t.Fatalf("medium input diverged from the definition: got %d pairs, want %d", len(got), len(want))
	}
	again, err := Overlaps(spans, &Budget{})
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(again, got) {
		t.Fatal("repeated call returned a different result")
	}
}

// ---- the perf gate ----------------------------------------------------------

func TestShiftScaleWithinCheckBudget(t *testing.T) {
	// Scale gate arithmetic (perf-seed policy: document the margin):
	//   n = 60_000 windows, K = 56_794 real overlaps (pinned below).
	//   MaxChecks = 4_300_000 ≈ 64 checks per window plus 8 per real
	//   overlap: loose enough for any strategy that only weighs windows
	//   that can still intersect (an ordered sweep needs exactly K
	//   checks, ~75x under; log-factor slop still fits easily), while
	//   the weigh-every-pair shape needs C(60000,2) = 1_799_970_000
	//   checks — ~419x OVER — and gets stopped by the budget within a
	//   fraction of a second instead of grinding out the full pass.
	//   A within-budget implementation finishes this test in about a
	//   second under -race.
	const maxChecks = 4_300_000
	const wantPairs = 56_794
	const wantSum = uint64(0xe5e7c495262d43f3)

	spans := genSpans(60_000, 600_000_000, 424242)
	budget := &Budget{MaxChecks: maxChecks}
	pairs, err := Overlaps(spans, budget)
	if err != nil {
		t.Fatalf("shift-scale overlap pass: %v after %d checks — pair examinations blew the budget", err, budget.Checks)
	}
	if budget.Checks > maxChecks {
		t.Fatalf("budget accounting broken: Checks=%d > MaxChecks with no error", budget.Checks)
	}
	if len(pairs) != wantPairs {
		t.Fatalf("want %d overlapping pairs, got %d", wantPairs, len(pairs))
	}
	for i := range pairs {
		if pairs[i].A >= pairs[i].B {
			t.Fatalf("pair %d not ordered A<B: %+v", i, pairs[i])
		}
		if i > 0 {
			p, q := pairs[i-1], pairs[i]
			if !(p.A < q.A || (p.A == q.A && p.B < q.B)) {
				t.Fatalf("pair list not strictly sorted at %d: %+v then %+v", i, p, q)
			}
		}
	}
	for i := 0; i < len(pairs); i += 997 {
		p := pairs[i]
		a, b := spans[p.A], spans[p.B]
		if !(a.Start < a.End && b.Start < b.End && a.Start < b.End && b.Start < a.End) {
			t.Fatalf("reported pair %+v does not actually overlap: %+v vs %+v", p, a, b)
		}
	}
	h := fnv.New64a()
	var buf [16]byte
	for _, p := range pairs {
		binary.LittleEndian.PutUint64(buf[0:8], uint64(p.A))
		binary.LittleEndian.PutUint64(buf[8:16], uint64(p.B))
		h.Write(buf[:])
	}
	if got := h.Sum64(); got != wantSum {
		t.Fatalf("overlap set diverged at scale: checksum %#x, want %#x", got, wantSum)
	}
}
