package wordfreq

import (
	"reflect"
	"testing"
)

func TestTokenizeLowercasesAndSplitsOnPunctuation(t *testing.T) {
	got := Tokenize("The QUICK, brown fox -- jumps! Over 2 dogs.")
	want := []string{"the", "quick", "brown", "fox", "jumps", "over", "2", "dogs"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Tokenize = %q, want %q", got, want)
	}
}

func TestTokenizeKeepsInteriorApostrophes(t *testing.T) {
	got := Tokenize("Don't quote 'this' — it's Alice's, isn't it?")
	want := []string{"don't", "quote", "this", "it's", "alice's", "isn't", "it"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Tokenize = %q, want %q", got, want)
	}
}

func TestTokenizeEmptyAndPunctuationOnly(t *testing.T) {
	if got := Tokenize(""); len(got) != 0 {
		t.Fatalf("Tokenize(\"\") = %q, want empty", got)
	}
	if got := Tokenize("... !!! ''' --- ,,,"); len(got) != 0 {
		t.Fatalf("Tokenize(punctuation) = %q, want empty", got)
	}
}

func TestAddAccumulatesAcrossDocuments(t *testing.T) {
	c := New()
	c.Add("deploy failed: retry queued")
	c.Add("Retry succeeded, deploy green")
	if got := c.Count("retry"); got != 2 {
		t.Fatalf("Count(retry) = %d, want 2", got)
	}
	if got := c.Count("deploy"); got != 2 {
		t.Fatalf("Count(deploy) = %d, want 2", got)
	}
	if got := c.Count("green"); got != 1 {
		t.Fatalf("Count(green) = %d, want 1", got)
	}
}

func TestCountIsCaseInsensitive(t *testing.T) {
	c := New()
	c.Add("Redis REDIS redis")
	if got := c.Count("Redis"); got != 3 {
		t.Fatalf("Count(Redis) = %d, want 3", got)
	}
	if got := c.Count("missing"); got != 0 {
		t.Fatalf("Count(missing) = %d, want 0", got)
	}
}

func TestDistinctAndTotal(t *testing.T) {
	c := New()
	if c.Distinct() != 0 || c.Total() != 0 {
		t.Fatalf("empty counter: Distinct=%d Total=%d, want 0/0", c.Distinct(), c.Total())
	}
	c.Add("a b a c b a")
	if got := c.Distinct(); got != 3 {
		t.Fatalf("Distinct = %d, want 3", got)
	}
	if got := c.Total(); got != 6 {
		t.Fatalf("Total = %d, want 6", got)
	}
}
