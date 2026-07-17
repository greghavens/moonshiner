// Package snapprune decides which backup snapshots to delete. The
// nightly job lists snapshot names from the bucket, asks this package
// what to keep, and removes the rest.
package snapprune

import (
	"fmt"
	"sort"
	"time"
)

const stampLayout = "20060102-150405"

// Timestamp extracts the UTC timestamp from a snapshot name of the
// form "<prefix>-YYYYMMDD-HHMMSS", e.g. "db-20260711-031500".
func Timestamp(name string) (time.Time, error) {
	if len(name) < len(stampLayout)+2 || name[len(name)-len(stampLayout)-1] != '-' {
		return time.Time{}, fmt.Errorf("snapprune: %q does not end in -YYYYMMDD-HHMMSS", name)
	}
	ts, err := time.ParseInLocation(stampLayout, name[len(name)-len(stampLayout):], time.UTC)
	if err != nil {
		return time.Time{}, fmt.Errorf("snapprune: bad timestamp in %q: %v", name, err)
	}
	return ts, nil
}

// sortNewestFirst returns names ordered newest-first; equal timestamps
// tie-break by name ascending so runs are deterministic.
func sortNewestFirst(names []string) ([]string, error) {
	type snap struct {
		name string
		ts   time.Time
	}
	snaps := make([]snap, 0, len(names))
	for _, n := range names {
		ts, err := Timestamp(n)
		if err != nil {
			return nil, err
		}
		snaps = append(snaps, snap{n, ts})
	}
	sort.Slice(snaps, func(i, j int) bool {
		if !snaps[i].ts.Equal(snaps[j].ts) {
			return snaps[i].ts.After(snaps[j].ts)
		}
		return snaps[i].name < snaps[j].name
	})
	out := make([]string, len(snaps))
	for i, s := range snaps {
		out[i] = s.name
	}
	return out, nil
}

// KeepLastN partitions names into the n newest (keep) and the rest
// (drop), both ordered newest-first. n must be at least 1: a pruning
// policy that keeps nothing is always a configuration mistake.
func KeepLastN(names []string, n int) (keep, drop []string, err error) {
	if n < 1 {
		return nil, nil, fmt.Errorf("snapprune: keep-last-N needs n >= 1, got %d", n)
	}
	sorted, err := sortNewestFirst(names)
	if err != nil {
		return nil, nil, err
	}
	if len(sorted) <= n {
		return sorted, nil, nil
	}
	return sorted[:n], sorted[n:], nil
}
