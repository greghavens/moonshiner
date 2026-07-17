package textindex

import "testing"

// newCorpus indexes a small fixed document set used by the query tests.
//
// Term frequencies for reference:
//
//	doc-a: log:3 append:1 only:1
//	doc-b: storage:2 cloud:1 disk:1 bills:2
//	doc-c: append:1 only:1 storage:2 log:1 wins:2
//	doc-d: disk:1 log:1
//	doc-e: append:2 only:2 appends:1
func newCorpus(t *testing.T) *Index {
	t.Helper()
	idx := NewIndex()
	mustAdd(t, idx, "doc-a", "Grep the log. The log never lies; the log is append only.")
	mustAdd(t, idx, "doc-b", "Cloud storage bills by the byte; local storage bills by the disk.")
	mustAdd(t, idx, "doc-c", "Append-only storage: the log wins, storage wins.")
	mustAdd(t, idx, "doc-d", "Disk failures corrupt the tail of the log.")
	mustAdd(t, idx, "doc-e", "Only the append queue appends; append to only one file.")
	return idx
}

func TestAndRequiresEveryTerm(t *testing.T) {
	idx := newCorpus(t)
	got := search(t, idx, "log AND storage")
	wantResults(t, "log AND storage", got, []Result{
		{ID: "doc-c", Score: 3}, // log:1 + storage:2
	})
}

func TestOrScoresSumAcrossTerms(t *testing.T) {
	idx := newCorpus(t)
	got := search(t, idx, "storage OR append")
	wantResults(t, "storage OR append", got, []Result{
		{ID: "doc-c", Score: 3}, // storage:2 + append:1
		{ID: "doc-b", Score: 2},
		{ID: "doc-e", Score: 2}, // tie with doc-b broken by id
		{ID: "doc-a", Score: 1},
	})
}

func TestNotExcludesMatches(t *testing.T) {
	idx := newCorpus(t)
	got := search(t, idx, "log AND NOT cloud")
	wantResults(t, "log AND NOT cloud", got, []Result{
		{ID: "doc-a", Score: 3},
		{ID: "doc-c", Score: 1},
		{ID: "doc-d", Score: 1},
	})
}

func TestBareNotIsComplement(t *testing.T) {
	idx := newCorpus(t)
	got := search(t, idx, "NOT log")
	wantResults(t, "NOT log", got, []Result{
		{ID: "doc-b", Score: 0},
		{ID: "doc-e", Score: 0},
	})
}

func TestAndBindsTighterThanOr(t *testing.T) {
	idx := newCorpus(t)
	// append OR (cloud AND wins) — no doc has both cloud and wins, so this
	// is exactly the append docs. A left-to-right mis-parse collapses it to
	// (append OR cloud) AND wins = only doc-c.
	got := search(t, idx, "append OR cloud AND wins")
	wantResults(t, "append OR cloud AND wins", got, []Result{
		{ID: "doc-c", Score: 3}, // append:1 + wins:2
		{ID: "doc-e", Score: 2},
		{ID: "doc-a", Score: 1},
	})
}

func TestParensOverridePrecedence(t *testing.T) {
	idx := newCorpus(t)
	got := search(t, idx, "(append OR cloud) AND wins")
	wantResults(t, "(append OR cloud) AND wins", got, []Result{
		{ID: "doc-c", Score: 3}, // append:1 + wins:2
	})
}

func TestPhraseRequiresAdjacency(t *testing.T) {
	idx := newCorpus(t)
	// doc-e contains both words but never the consecutive pair.
	got := search(t, idx, `"append only"`)
	wantResults(t, `"append only"`, got, []Result{
		{ID: "doc-a", Score: 1},
		{ID: "doc-c", Score: 1}, // "Append-only" tokenizes to the adjacent pair
	})
}

func TestPhraseIsCaseInsensitive(t *testing.T) {
	idx := newCorpus(t)
	got := search(t, idx, `"APPEND Only"`)
	wantResults(t, `"APPEND Only"`, got, []Result{
		{ID: "doc-a", Score: 1},
		{ID: "doc-c", Score: 1},
	})
}

func TestPhraseOccurrencesCounted(t *testing.T) {
	idx := NewIndex()
	mustAdd(t, idx, "x", "hot swap hot swap hot")
	mustAdd(t, idx, "y", "one hot swap here")

	got := search(t, idx, `"hot swap"`)
	wantResults(t, `"hot swap"`, got, []Result{
		{ID: "x", Score: 2},
		{ID: "y", Score: 1},
	})
}

func TestMultiTokenBareTermActsAsPhrase(t *testing.T) {
	idx := NewIndex()
	mustAdd(t, idx, "n1", "the wi fi network")
	mustAdd(t, idx, "n2", "fi wi backwards")
	mustAdd(t, idx, "n3", "wifi combined")

	got := search(t, idx, "wi-fi")
	wantResults(t, "wi-fi", got, []Result{{ID: "n1", Score: 1}})
}

func TestLowercaseKeywordsAreOrdinaryTerms(t *testing.T) {
	idx := NewIndex()
	mustAdd(t, idx, "j1", "not everything is lost")
	mustAdd(t, idx, "j2", "all is lost")

	got := search(t, idx, "not")
	wantResults(t, "not", got, []Result{{ID: "j1", Score: 1}})
}

func TestUnknownTermIsEmptyNotError(t *testing.T) {
	idx := newCorpus(t)
	if got := search(t, idx, "zeppelin"); len(got) != 0 {
		t.Fatalf("Search(zeppelin) = %+v, want empty", got)
	}
	if got := search(t, idx, "log AND zeppelin"); len(got) != 0 {
		t.Fatalf("Search(log AND zeppelin) = %+v, want empty", got)
	}
}

func TestMalformedQueriesError(t *testing.T) {
	idx := newCorpus(t)
	bad := []string{
		"",            // nothing to search
		"   ",         // still nothing
		"log AND",     // dangling operator
		"AND log",     // leading operator
		"log OR",      // dangling operator
		"NOT",         // operator with no operand
		"log storage", // adjacent operands without an operator
		"(log",        // unclosed paren
		"log)",        // stray close paren
		`"unclosed`,   // unterminated phrase
		`""`,          // phrase with no tokens
	}
	for _, q := range bad {
		if _, err := idx.Search(q); err == nil {
			t.Fatalf("Search(%q) must return an error", q)
		}
	}
}
