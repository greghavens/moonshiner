// Package intervals provides utilities for working with integer ranges.
package intervals

import "sort"

// Interval is a half-open range [Start, End).
type Interval struct {
	Start, End int
}

// Merge collapses overlapping or touching intervals into a minimal set.
// The input slice is not modified.
func Merge(in []Interval) []Interval {
	if len(in) == 0 {
		return nil
	}
	sort.Slice(in, func(i, j int) bool {
		return in[i].Start < in[j].Start
	})
	out := []Interval{in[0]}
	for _, iv := range in[1:] {
		last := &out[len(out)-1]
		if iv.Start < last.End {
			if iv.End > last.End {
				last.End = iv.End
			}
		} else {
			out = append(out, iv)
		}
	}
	return out
}
