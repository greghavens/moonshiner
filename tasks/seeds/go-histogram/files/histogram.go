// Package histogram renders fixed-width ASCII histograms of numeric
// samples. It backs the --stats output of several internal CLIs, where
// a quick latency or payload-size distribution beats a spreadsheet.
package histogram

import (
	"fmt"
	"strings"
)

const defaultBarWidth = 40

// Histogram buckets float64 samples and renders them as ASCII bars.
// Bucket i covers the half-open range [bounds[i], bounds[i+1]).
type Histogram struct {
	bounds  []float64 // len(counts)+1, strictly increasing
	counts  []int
	samples int
}

// New returns a histogram with n equal-width buckets covering [lo, hi).
// n must be at least 1 and hi must be greater than lo.
func New(lo, hi float64, n int) (*Histogram, error) {
	if n < 1 {
		return nil, fmt.Errorf("histogram: bucket count %d < 1", n)
	}
	if hi <= lo {
		return nil, fmt.Errorf("histogram: invalid range [%g, %g)", lo, hi)
	}
	bounds := make([]float64, n+1)
	step := (hi - lo) / float64(n)
	for i := range bounds {
		bounds[i] = lo + float64(i)*step
	}
	bounds[n] = hi // avoid float drift on the top edge
	return &Histogram{bounds: bounds, counts: make([]int, n)}, nil
}

// Add records one sample. Samples outside the histogram's range are
// clamped into the first or last bucket so nothing is silently lost.
func (h *Histogram) Add(v float64) {
	h.counts[h.bucketOf(v)]++
	h.samples++
}

// Samples reports how many values have been recorded.
func (h *Histogram) Samples() int { return h.samples }

func (h *Histogram) bucketOf(v float64) int {
	last := len(h.counts) - 1
	if v < h.bounds[0] {
		return 0
	}
	if v >= h.bounds[len(h.bounds)-1] {
		return last
	}
	for i := 0; i < len(h.counts); i++ {
		if v < h.bounds[i+1] {
			return i
		}
	}
	return last
}

// Render returns one line per bucket, e.g.
//
//	[0, 10)  |########################                | 12
//
// Bars are scaled so the fullest bucket spans the full bar width.
func (h *Histogram) Render() string {
	labels := make([]string, len(h.counts))
	labelW, maxCount := 0, 0
	for i, c := range h.counts {
		labels[i] = fmt.Sprintf("[%g, %g)", h.bounds[i], h.bounds[i+1])
		if len(labels[i]) > labelW {
			labelW = len(labels[i])
		}
		if c > maxCount {
			maxCount = c
		}
	}
	if maxCount == 0 {
		maxCount = 1 // empty histogram: all bars zero, no divide by zero
	}
	lines := make([]string, len(h.counts))
	for i, c := range h.counts {
		barLen := c * defaultBarWidth / maxCount
		bar := strings.Repeat("#", barLen)
		lines[i] = fmt.Sprintf("%-*s |%-*s| %d", labelW, labels[i], defaultBarWidth, bar, c)
	}
	return strings.Join(lines, "\n")
}
