// Package tally aggregates token frequencies across pre-split log shards.
// The ingest service hands us one shard per source file; shards are large,
// so each one is tallied on its own goroutine.
package tally

import (
	"strings"
	"sync"
)

// Tokenize normalizes one raw log line into countable tokens: lowercased,
// punctuation trimmed, empty fields dropped.
func Tokenize(line string) []string {
	fields := strings.Fields(line)
	toks := make([]string, 0, len(fields))
	for _, f := range fields {
		f = strings.Trim(strings.ToLower(f), ".,:;!?\"'()[]")
		if f != "" {
			toks = append(toks, f)
		}
	}
	return toks
}

// Count tallies token frequencies across all shards in parallel and returns
// the combined counts.
func Count(shards [][]string) map[string]int {
	counts := make(map[string]int)
	var wg sync.WaitGroup
	for _, shard := range shards {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for _, line := range shard {
				for _, tok := range Tokenize(line) {
					counts[tok]++
				}
			}
		}()
	}
	wg.Wait()
	return counts
}

// Top returns the count for a single token (0 if unseen).
func Top(counts map[string]int, token string) int {
	return counts[strings.ToLower(token)]
}
