package wordfreq

import (
	"reflect"
	"testing"
)

// Acceptance tests for the "top words" report: Counter.TopK with a
// deterministic tie-break, plus report-time stopword filtering via
// Counter.SetStopwords.

func TestTopKOrdersByCountDescending(t *testing.T) {
	c := New()
	c.Add("cache miss cache hit cache miss timeout")
	got := c.TopK(3)
	want := []Entry{
		{Word: "cache", Count: 3},
		{Word: "miss", Count: 2},
		{Word: "hit", Count: 1},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("TopK(3) = %v, want %v", got, want)
	}
}

func TestTopKBreaksTiesAlphabetically(t *testing.T) {
	c := New()
	c.Add("date banana apple date apple banana date cherry")
	got := c.TopK(4)
	want := []Entry{
		{Word: "date", Count: 3},
		{Word: "apple", Count: 2},
		{Word: "banana", Count: 2},
		{Word: "cherry", Count: 1},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("TopK(4) = %v, want %v", got, want)
	}
}

func TestTopKAllTiedIsFullyAlphabetical(t *testing.T) {
	c := New()
	c.Add("kiwi fig lime oat pea yam ash bay elm gum ivy oak")
	got := c.TopK(12)
	want := []Entry{
		{Word: "ash", Count: 1}, {Word: "bay", Count: 1}, {Word: "elm", Count: 1},
		{Word: "fig", Count: 1}, {Word: "gum", Count: 1}, {Word: "ivy", Count: 1},
		{Word: "kiwi", Count: 1}, {Word: "lime", Count: 1}, {Word: "oak", Count: 1},
		{Word: "oat", Count: 1}, {Word: "pea", Count: 1}, {Word: "yam", Count: 1},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("TopK(12) = %v, want %v", got, want)
	}
}

func TestTopKLargerThanVocabularyReturnsEverything(t *testing.T) {
	c := New()
	c.Add("alpha beta alpha")
	got := c.TopK(50)
	want := []Entry{
		{Word: "alpha", Count: 2},
		{Word: "beta", Count: 1},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("TopK(50) = %v, want %v", got, want)
	}
}

func TestTopKZeroOrNegativeKIsEmpty(t *testing.T) {
	c := New()
	c.Add("some words here")
	if got := c.TopK(0); len(got) != 0 {
		t.Fatalf("TopK(0) = %v, want empty", got)
	}
	if got := c.TopK(-4); len(got) != 0 {
		t.Fatalf("TopK(-4) = %v, want empty", got)
	}
}

func TestTopKOnEmptyCounterIsEmpty(t *testing.T) {
	if got := New().TopK(10); len(got) != 0 {
		t.Fatalf("TopK on empty counter = %v, want empty", got)
	}
}

func TestStopwordsExcludedAndNextWordsFillTheSlots(t *testing.T) {
	c := New()
	c.Add("the error in the log is the timeout error again")
	c.SetStopwords([]string{"the", "is", "in"})
	got := c.TopK(3)
	want := []Entry{
		{Word: "error", Count: 2},
		{Word: "again", Count: 1},
		{Word: "log", Count: 1},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("TopK(3) with stopwords = %v, want %v", got, want)
	}
}

func TestStopwordsMatchCaseInsensitively(t *testing.T) {
	c := New()
	c.Add("the server the client")
	c.SetStopwords([]string{"THE"})
	got := c.TopK(5)
	want := []Entry{
		{Word: "client", Count: 1},
		{Word: "server", Count: 1},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("TopK with uppercase stopword = %v, want %v", got, want)
	}
}

func TestSetStopwordsReplacesThePreviousSet(t *testing.T) {
	c := New()
	c.Add("red blue red green blue red")
	c.SetStopwords([]string{"red"})
	c.SetStopwords([]string{"blue"})
	got := c.TopK(2)
	want := []Entry{
		{Word: "red", Count: 3},
		{Word: "green", Count: 1},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("TopK after replacing stopwords = %v, want %v", got, want)
	}
	c.SetStopwords(nil)
	got = c.TopK(3)
	want = []Entry{
		{Word: "red", Count: 3},
		{Word: "blue", Count: 2},
		{Word: "green", Count: 1},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("TopK after clearing stopwords = %v, want %v", got, want)
	}
}

func TestStopwordsDoNotTouchTheRawTally(t *testing.T) {
	c := New()
	c.Add("the cat and the hat")
	c.SetStopwords([]string{"the", "and"})
	if got := c.Count("the"); got != 2 {
		t.Fatalf("Count(the) after SetStopwords = %d, want 2 (raw tally must stay intact)", got)
	}
	if got := c.Total(); got != 5 {
		t.Fatalf("Total after SetStopwords = %d, want 5", got)
	}
	if got := c.Distinct(); got != 4 {
		t.Fatalf("Distinct after SetStopwords = %d, want 4", got)
	}
}

func TestTopKIsRepeatableAndDoesNotMutateTheCounter(t *testing.T) {
	c := New()
	c.Add("x y z x y x w v u t")
	first := c.TopK(4)
	for i := 0; i < 5; i++ {
		if got := c.TopK(4); !reflect.DeepEqual(got, first) {
			t.Fatalf("TopK not deterministic: call %d = %v, first = %v", i+2, got, first)
		}
	}
	if got := c.Total(); got != 10 {
		t.Fatalf("Total changed after TopK: %d, want 10", got)
	}
	if got := c.Count("x"); got != 3 {
		t.Fatalf("Count(x) changed after TopK: %d, want 3", got)
	}
}
