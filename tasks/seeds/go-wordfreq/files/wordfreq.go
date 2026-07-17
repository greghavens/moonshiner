// Package wordfreq tallies word frequencies for the content-analytics
// service. Editors paste an article draft and get back vocabulary
// stats; the ingest job feeds whole documents through a Counter.
package wordfreq

import (
	"strings"
	"unicode"
)

// Counter accumulates word counts across one or more documents.
type Counter struct {
	counts map[string]int
	total  int
}

// New returns an empty Counter.
func New() *Counter {
	return &Counter{counts: make(map[string]int)}
}

// Tokenize lowercases text and splits it into words. A word is a
// maximal run of letters and digits; apostrophes are kept when they
// sit inside a word ("don't" stays one word) but quoting apostrophes
// at the edges are stripped ("'fine'" tokenizes as "fine").
func Tokenize(text string) []string {
	isWordRune := func(r rune) bool {
		return unicode.IsLetter(r) || unicode.IsDigit(r) || r == '\''
	}
	var words []string
	for _, run := range strings.FieldsFunc(strings.ToLower(text), func(r rune) bool {
		return !isWordRune(r)
	}) {
		run = strings.Trim(run, "'")
		if run != "" {
			words = append(words, run)
		}
	}
	return words
}

// Add tokenizes text and adds every word to the tally.
func (c *Counter) Add(text string) {
	for _, w := range Tokenize(text) {
		c.counts[w]++
		c.total++
	}
}

// Count reports how many times word has been seen. Lookup is
// case-insensitive: Count("Go") and Count("go") agree.
func (c *Counter) Count(word string) int {
	return c.counts[strings.ToLower(word)]
}

// Distinct reports how many unique words have been seen.
func (c *Counter) Distinct() int { return len(c.counts) }

// Total reports how many words have been seen, repeats included.
func (c *Counter) Total() int { return c.total }
