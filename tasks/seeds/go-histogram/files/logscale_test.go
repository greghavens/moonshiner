package histogram

import (
	"strings"
	"testing"
)

// Acceptance tests for logarithmic buckets (NewLog) and configurable
// render width with proper rounding (RenderWidth).

func mustNewLog(t *testing.T, lo float64, n int, base float64) *Histogram {
	t.Helper()
	h, err := NewLog(lo, n, base)
	if err != nil {
		t.Fatalf("NewLog(%g, %d, %g): %v", lo, n, base, err)
	}
	return h
}

// barLens extracts the number of '#' characters in each rendered bar.
func barLens(t *testing.T, rendered string) []int {
	t.Helper()
	var lens []int
	for _, line := range strings.Split(rendered, "\n") {
		first := strings.Index(line, "|")
		last := strings.LastIndex(line, "|")
		if first < 0 || last <= first {
			t.Fatalf("line %q has no |bar| section", line)
		}
		lens = append(lens, strings.Count(line[first:last], "#"))
	}
	return lens
}

func TestNewLogBucketBoundaries(t *testing.T) {
	h := mustNewLog(t, 1, 3, 10) // [1, 10), [10, 100), [100, 1000)
	for _, v := range []float64{1, 9.99} {
		h.Add(v)
	}
	h.Add(10) // boundary goes up
	h.Add(99)
	h.Add(100)
	h.Add(999.5)
	h.Add(0.001) // clamped low
	h.Add(1e9)   // clamped high
	if h.counts[0] != 3 || h.counts[1] != 2 || h.counts[2] != 3 {
		t.Fatalf("counts = %v, want [3 2 3]", h.counts)
	}
}

func TestNewLogValidatesArguments(t *testing.T) {
	if _, err := NewLog(0, 3, 10); err == nil {
		t.Fatal("NewLog accepted lo = 0 (log scale needs lo > 0)")
	}
	if _, err := NewLog(-2, 3, 10); err == nil {
		t.Fatal("NewLog accepted negative lo")
	}
	if _, err := NewLog(1, 3, 1); err == nil {
		t.Fatal("NewLog accepted base = 1")
	}
	if _, err := NewLog(1, 0, 10); err == nil {
		t.Fatal("NewLog accepted zero buckets")
	}
}

func TestRenderWidthFullOutputLogScale(t *testing.T) {
	h := mustNewLog(t, 1, 3, 10)
	for _, v := range []float64{2, 3, 4, 5} {
		h.Add(v)
	}
	h.Add(50)

	// max count 4 at width 10: 4 -> 10 chars, 1 -> 2.5 rounds to 3, 0 -> 0.
	want := strings.Join([]string{
		"[1, 10)     |##########| 4",
		"[10, 100)   |###       | 1",
		"[100, 1000) |          | 0",
	}, "\n")
	got, err := h.RenderWidth(10)
	if err != nil {
		t.Fatalf("RenderWidth(10): %v", err)
	}
	if got != want {
		t.Fatalf("RenderWidth(10) =\n%s\nwant\n%s", got, want)
	}
}

func TestRenderWidthRoundsHalfUp(t *testing.T) {
	h := mustNew(t, 0, 20, 2)
	for i := 0; i < 3; i++ {
		h.Add(1) // bucket 0: 3
	}
	for i := 0; i < 8; i++ {
		h.Add(11) // bucket 1: 8
	}
	got, err := h.RenderWidth(4)
	if err != nil {
		t.Fatalf("RenderWidth(4): %v", err)
	}
	// 3/8 of 4 = 1.5 -> 2, not the truncated 1.
	if lens := barLens(t, got); lens[0] != 2 || lens[1] != 4 {
		t.Fatalf("bar lengths = %v, want [2 4]", lens)
	}

	h2 := mustNew(t, 0, 20, 2)
	for i := 0; i < 5; i++ {
		h2.Add(1)
	}
	for i := 0; i < 8; i++ {
		h2.Add(11)
	}
	got2, err := h2.RenderWidth(4)
	if err != nil {
		t.Fatalf("RenderWidth(4): %v", err)
	}
	// 5/8 of 4 = 2.5 -> 3.
	if lens := barLens(t, got2); lens[0] != 3 || lens[1] != 4 {
		t.Fatalf("bar lengths = %v, want [3 4]", lens)
	}
}

func TestRenderWidthRoundsInsteadOfTruncating(t *testing.T) {
	h := mustNew(t, 0, 20, 2)
	h.Add(1)
	h.Add(1)
	for i := 0; i < 3; i++ {
		h.Add(11)
	}
	got, err := h.RenderWidth(10)
	if err != nil {
		t.Fatalf("RenderWidth(10): %v", err)
	}
	// 2/3 of 10 = 6.67 -> 7 (truncation would give 6).
	if lens := barLens(t, got); lens[0] != 7 || lens[1] != 10 {
		t.Fatalf("bar lengths = %v, want [7 10]", lens)
	}
}

func TestRenderWidthNonZeroCountsGetAtLeastOneChar(t *testing.T) {
	h := mustNew(t, 0, 30, 3)
	h.Add(1) // bucket 0: count 1
	for i := 0; i < 400; i++ {
		h.Add(25) // bucket 2: count 400
	}
	got, err := h.RenderWidth(10)
	if err != nil {
		t.Fatalf("RenderWidth(10): %v", err)
	}
	// 1/400 of 10 rounds to 0, but a non-empty bucket must stay visible;
	// a genuinely empty bucket must stay blank.
	if lens := barLens(t, got); lens[0] != 1 || lens[1] != 0 || lens[2] != 10 {
		t.Fatalf("bar lengths = %v, want [1 0 10]", lens)
	}
}

func TestRenderWidthRejectsBadWidths(t *testing.T) {
	h := mustNew(t, 0, 10, 1)
	if _, err := h.RenderWidth(0); err == nil {
		t.Fatal("RenderWidth accepted width 0")
	}
	if _, err := h.RenderWidth(-3); err == nil {
		t.Fatal("RenderWidth accepted a negative width")
	}
}
