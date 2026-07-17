package textindex

import (
	"fmt"
	"sync"
	"testing"
)

func mustAdd(t *testing.T, idx *Index, id, text string) {
	t.Helper()
	if err := idx.Add(id, text); err != nil {
		t.Fatalf("Add(%q): %v", id, err)
	}
}

func search(t *testing.T, idx *Index, q string) []Result {
	t.Helper()
	got, err := idx.Search(q)
	if err != nil {
		t.Fatalf("Search(%q): %v", q, err)
	}
	return got
}

func wantResults(t *testing.T, q string, got, want []Result) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("Search(%q) = %+v, want %+v", q, got, want)
	}
	for i := range want {
		if got[i].ID != want[i].ID || got[i].Score != want[i].Score {
			t.Fatalf("Search(%q)[%d] = %+v, want %+v (full: %+v)", q, i, got[i], want[i], got)
		}
	}
}

func TestTFRankingDescending(t *testing.T) {
	idx := NewIndex()
	mustAdd(t, idx, "r1", "go go go gopher")
	mustAdd(t, idx, "r2", "go routines go")
	mustAdd(t, idx, "r3", "go once")

	got := search(t, idx, "go")
	wantResults(t, "go", got, []Result{
		{ID: "r1", Score: 3}, // "gopher" is its own token, not an occurrence of "go"
		{ID: "r2", Score: 2},
		{ID: "r3", Score: 1},
	})
}

func TestTieBreakAscendingDocID(t *testing.T) {
	idx := NewIndex()
	mustAdd(t, idx, "t-b", "cache cache")
	mustAdd(t, idx, "t-a", "cache cache")
	mustAdd(t, idx, "t-c", "cache")

	got := search(t, idx, "cache")
	wantResults(t, "cache", got, []Result{
		{ID: "t-a", Score: 2},
		{ID: "t-b", Score: 2},
		{ID: "t-c", Score: 1},
	})
}

func TestTokenizerFoldsCaseAndSplitsOnPunctuation(t *testing.T) {
	idx := NewIndex()
	mustAdd(t, idx, "p1", "Hello, HELLO; hello!")
	mustAdd(t, idx, "p2", "shout HELLOHELLO")

	got := search(t, idx, "hello")
	wantResults(t, "hello", got, []Result{{ID: "p1", Score: 3}})
}

func TestDigitsAreTokenCharacters(t *testing.T) {
	idx := NewIndex()
	mustAdd(t, idx, "q1", "error 404 page")
	mustAdd(t, idx, "q2", "error404 page")

	got := search(t, idx, "404")
	wantResults(t, "404", got, []Result{{ID: "q1", Score: 1}})
}

func TestAddEmptyIDRejected(t *testing.T) {
	idx := NewIndex()
	if err := idx.Add("", "some text"); err == nil {
		t.Fatal("Add with empty doc id must return an error")
	}
	if n := idx.Len(); n != 0 {
		t.Fatalf("rejected Add must not index anything, Len = %d", n)
	}
}

func TestAddReplacesExistingDoc(t *testing.T) {
	idx := NewIndex()
	mustAdd(t, idx, "readme", "go tooling notes")
	if n := idx.Len(); n != 1 {
		t.Fatalf("Len = %d, want 1", n)
	}

	mustAdd(t, idx, "readme", "rust ferris notes")
	if n := idx.Len(); n != 1 {
		t.Fatalf("Len after re-Add = %d, want 1 (replace, not duplicate)", n)
	}
	if got := search(t, idx, "go"); len(got) != 0 {
		t.Fatalf("stale tokens still searchable after re-Add: %+v", got)
	}
	got := search(t, idx, "rust")
	wantResults(t, "rust", got, []Result{{ID: "readme", Score: 1}})
}

func TestRemoveDropsDocFromResults(t *testing.T) {
	idx := NewIndex()
	mustAdd(t, idx, "keep", "shared token here")
	mustAdd(t, idx, "drop", "shared token there")

	if !idx.Remove("drop") {
		t.Fatal("Remove of an existing doc must return true")
	}
	if idx.Remove("drop") {
		t.Fatal("Remove of an already-removed doc must return false")
	}
	if n := idx.Len(); n != 1 {
		t.Fatalf("Len after Remove = %d, want 1", n)
	}
	got := search(t, idx, "shared")
	wantResults(t, "shared", got, []Result{{ID: "keep", Score: 1}})
}

func TestConcurrentAddAndSearch(t *testing.T) {
	idx := NewIndex()
	const writers, perWriter = 4, 25

	var wg sync.WaitGroup
	for w := 0; w < writers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			for i := 0; i < perWriter; i++ {
				id := fmt.Sprintf("w%d-doc%02d", w, i)
				if err := idx.Add(id, fmt.Sprintf("common payload number %d", i)); err != nil {
					t.Errorf("concurrent Add(%q): %v", id, err)
					return
				}
			}
		}(w)
	}
	for r := 0; r < 4; r++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < 50; i++ {
				if _, err := idx.Search("common AND payload"); err != nil {
					t.Errorf("concurrent Search: %v", err)
					return
				}
			}
		}()
	}
	wg.Wait()

	got := search(t, idx, "common")
	if len(got) != writers*perWriter {
		t.Fatalf("after concurrent adds Search(common) returned %d docs, want %d", len(got), writers*perWriter)
	}
	if n := idx.Len(); n != writers*perWriter {
		t.Fatalf("Len = %d, want %d", n, writers*perWriter)
	}
}
