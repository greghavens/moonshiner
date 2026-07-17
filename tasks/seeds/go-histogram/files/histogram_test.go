package histogram

import (
	"strings"
	"testing"
)

func mustNew(t *testing.T, lo, hi float64, n int) *Histogram {
	t.Helper()
	h, err := New(lo, hi, n)
	if err != nil {
		t.Fatalf("New(%g, %g, %d): %v", lo, hi, n, err)
	}
	return h
}

func TestRenderScalesBarsToFullWidth(t *testing.T) {
	h := mustNew(t, 0, 30, 3)
	for _, v := range []float64{1, 2, 3, 4} {
		h.Add(v)
	}
	h.Add(12)
	h.Add(15)

	want := strings.Join([]string{
		"[0, 10)  |" + strings.Repeat("#", 40) + "| 4",
		"[10, 20) |" + strings.Repeat("#", 20) + strings.Repeat(" ", 20) + "| 2",
		"[20, 30) |" + strings.Repeat(" ", 40) + "| 0",
	}, "\n")
	if got := h.Render(); got != want {
		t.Fatalf("Render() =\n%s\nwant\n%s", got, want)
	}
}

func TestRenderEmptyHistogram(t *testing.T) {
	h := mustNew(t, 0, 10, 2)
	want := strings.Join([]string{
		"[0, 5)  |" + strings.Repeat(" ", 40) + "| 0",
		"[5, 10) |" + strings.Repeat(" ", 40) + "| 0",
	}, "\n")
	if got := h.Render(); got != want {
		t.Fatalf("Render() =\n%s\nwant\n%s", got, want)
	}
}

func TestAddBucketBoundaries(t *testing.T) {
	h := mustNew(t, 0, 30, 3)
	h.Add(10) // boundary value belongs to the upper bucket
	h.Add(9.999)
	h.Add(20)
	if h.counts[0] != 1 || h.counts[1] != 1 || h.counts[2] != 1 {
		t.Fatalf("counts = %v, want [1 1 1]", h.counts)
	}
}

func TestAddClampsOutOfRange(t *testing.T) {
	h := mustNew(t, 0, 30, 3)
	h.Add(-5)   // below range: first bucket
	h.Add(30)   // hi itself is out of the half-open range: last bucket
	h.Add(1e12) // way above: last bucket
	if h.counts[0] != 1 || h.counts[2] != 2 {
		t.Fatalf("counts = %v, want [1 0 2]", h.counts)
	}
	if h.Samples() != 3 {
		t.Fatalf("Samples() = %d, want 3", h.Samples())
	}
}

func TestNewValidatesArguments(t *testing.T) {
	if _, err := New(0, 30, 0); err == nil {
		t.Fatal("New accepted zero buckets")
	}
	if _, err := New(5, 5, 3); err == nil {
		t.Fatal("New accepted an empty range")
	}
	if _, err := New(10, 0, 3); err == nil {
		t.Fatal("New accepted an inverted range")
	}
}
