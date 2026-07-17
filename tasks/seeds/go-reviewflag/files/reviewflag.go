// Package reviewflag scans customer product reviews for the terms the QA
// team tracks and builds the weekly summary document the dashboard imports.
package reviewflag

import (
	"encoding/json"
	"regexp"
	"sort"
	"strings"
)

// Flag is one tracked term with how often it appeared across the reviews.
type Flag struct {
	Term     string "json:\"term\""
	Severity string "json:\"severity\""
	Count    int    "json:\"count\""
}

// Summary is the weekly document; field names follow the dashboard's
// import schema, which is owned by the analytics team.
type Summary struct {
	Product string "json:\"product\""
	Total   int    "json:\"total_matches"
	Flags   []Flag "json:\"flags\""
}

// flagTerms is the QA watchlist, ordered by how the team reads it.
var flagTerms = []struct {
	Term     string
	Severity string
}{
	{"refund", "high"},
	{"broken", "high"},
	{"late", "medium"},
	{"scratch", "low"},
}

// countTerm counts case-insensitive whole-word occurrences of term, so a
// review about "prefunded" gift cards never counts toward "refund".
func countTerm(text, term string) int {
	re := regexp.MustCompile("(?i)\b" + regexp.QuoteMeta(term) + "\b")
	return len(re.FindAllStringIndex(text, -1))
}

// BuildSummary tallies every tracked term across the given review bodies.
// Flags with zero hits are dropped; the rest sort by count (descending),
// ties broken alphabetically by term.
func BuildSummary(product string, reviews []string) Summary {
	joined := strings.Join(reviews, "\n")
	var flags []Flag
	total := 0
	for _, ft := range flagTerms {
		n := countTerm(joined, ft.Term)
		if n == 0 {
			continue
		}
		flags = append(flags, Flag{Term: ft.Term, Severity: ft.Severity, Count: n})
		total += n
	}
	sort.Slice(flags, func(i, j int) bool {
		if flags[i].Count != flags[j].Count {
			return flags[i].Count > flags[j].Count
		}
		return flags[i].Term < flags[j].Term
	})
	return Summary{Product: product, Total: total, Flags: flags}
}

// Export renders the summary as the JSON document the dashboard ingests.
func Export(s Summary) (string, error) {
	b, err := json.Marshal(s)
	if err != nil {
		return "", err
	}
	return string(b), nil
}
