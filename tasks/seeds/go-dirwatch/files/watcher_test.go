package dirwatch

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

// Acceptance contract for the new watcher features: TakeIgnoring (glob
// ignore patterns), DiffRenames (rename detection via content hash),
// and Debouncer (quiet-period coalescing on an injectable clock).

func TestTakeIgnoringMatchesBaseNames(t *testing.T) {
	root := t.TempDir()
	write(t, root, "main.go", "package main")
	write(t, root, "main.go.tmp", "scratch")
	write(t, root, "sub/deep/editor.tmp", "scratch")
	write(t, root, "sub/notes.txt", "hi")
	snap, err := TakeIgnoring(root, []string{"*.tmp"})
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for p := range snap {
		got = append(got, p)
	}
	if len(snap) != 2 {
		t.Fatalf("snapshot = %v, want exactly main.go and sub/notes.txt", got)
	}
	for _, p := range []string{"main.go", "sub/notes.txt"} {
		if _, ok := snap[p]; !ok {
			t.Fatalf("snapshot missing %q (got %v)", p, got)
		}
	}
}

func TestTakeIgnoringMatchesRelativePaths(t *testing.T) {
	root := t.TempDir()
	write(t, root, "build/out.js", "bundle")
	write(t, root, "build/sub/out.js", "nested bundle")
	write(t, root, "src/out.js", "source")
	snap, err := TakeIgnoring(root, []string{"build/*"})
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := snap["build/out.js"]; ok {
		t.Fatal("build/out.js should be ignored by pattern build/*")
	}
	// path.Match semantics: * does not cross a slash, and the pattern
	// is not a basename match here, so the deeper file stays.
	if _, ok := snap["build/sub/out.js"]; !ok {
		t.Fatal("build/sub/out.js should NOT be ignored by pattern build/*")
	}
	if _, ok := snap["src/out.js"]; !ok {
		t.Fatal("src/out.js should NOT be ignored by pattern build/*")
	}
}

func TestTakeIgnoringNoPatternsEqualsTake(t *testing.T) {
	root := t.TempDir()
	write(t, root, "a.txt", "a")
	write(t, root, "b/c.txt", "c")
	plain, err := Take(root)
	if err != nil {
		t.Fatal(err)
	}
	filtered, err := TakeIgnoring(root, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(plain, filtered) {
		t.Fatalf("TakeIgnoring(nil) = %v, want %v", filtered, plain)
	}
}

func TestTakeIgnoringRejectsBadPattern(t *testing.T) {
	root := t.TempDir()
	write(t, root, "a.txt", "a")
	if _, err := TakeIgnoring(root, []string{"["}); err == nil {
		t.Fatal(`TakeIgnoring with pattern "[" succeeded, want error`)
	}
}

func TestDiffRenamesPairsMovedFile(t *testing.T) {
	root := t.TempDir()
	write(t, root, "assets/logo-old.svg", "<svg>logo</svg>")
	write(t, root, "assets/app.css", "body{}")
	before, err := Take(root)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(
		filepath.Join(root, "assets", "logo-old.svg"),
		filepath.Join(root, "assets", "logo.svg")); err != nil {
		t.Fatal(err)
	}
	after, err := Take(root)
	if err != nil {
		t.Fatal(err)
	}
	got := DiffRenames(before, after)
	want := Changes{Renamed: []Rename{{From: "assets/logo-old.svg", To: "assets/logo.svg"}}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("DiffRenames = %+v, want %+v", got, want)
	}
}

func TestDiffRenamesLeavesRealChangesAlone(t *testing.T) {
	old := Snapshot{"keep.txt": "h1", "gone.txt": "h2", "moved.txt": "h3", "edited.txt": "h4"}
	cur := Snapshot{"keep.txt": "h1", "arrived.txt": "h3", "edited.txt": "h9", "brand-new.txt": "h5"}
	got := DiffRenames(old, cur)
	want := Changes{
		Added:    []string{"brand-new.txt"},
		Removed:  []string{"gone.txt"},
		Modified: []string{"edited.txt"},
		Renamed:  []Rename{{From: "moved.txt", To: "arrived.txt"}},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("DiffRenames = %+v, want %+v", got, want)
	}
}

func TestDiffRenamesAmbiguousHashesAreNotPaired(t *testing.T) {
	// Two identical files removed, two identical files added: no way to
	// know which went where, so report plain adds and removes.
	old := Snapshot{"a.txt": "same", "b.txt": "same"}
	cur := Snapshot{"x.txt": "same", "y.txt": "same"}
	got := DiffRenames(old, cur)
	want := Changes{
		Added:   []string{"x.txt", "y.txt"},
		Removed: []string{"a.txt", "b.txt"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("DiffRenames = %+v, want %+v", got, want)
	}
}

func TestDiffRenamesSortsMultipleRenamesByFrom(t *testing.T) {
	old := Snapshot{"z.txt": "hz", "m.txt": "hm", "a.txt": "ha"}
	cur := Snapshot{"z2.txt": "hz", "m2.txt": "hm", "a2.txt": "ha"}
	got := DiffRenames(old, cur)
	want := Changes{Renamed: []Rename{
		{From: "a.txt", To: "a2.txt"},
		{From: "m.txt", To: "m2.txt"},
		{From: "z.txt", To: "z2.txt"},
	}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("DiffRenames = %+v, want %+v", got, want)
	}
}

func TestPlainDiffStillReportsAddPlusRemove(t *testing.T) {
	old := Snapshot{"moved.txt": "h3"}
	cur := Snapshot{"arrived.txt": "h3"}
	got := Diff(old, cur)
	want := Changes{Added: []string{"arrived.txt"}, Removed: []string{"moved.txt"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Diff = %+v, want %+v (plain Diff must not detect renames)", got, want)
	}
}

type tick struct{ t time.Time }

func (c *tick) now() time.Time          { return c.t }
func (c *tick) advance(d time.Duration) { c.t = c.t.Add(d) }

func TestDebouncerHoldsUntilQuietPeriodPasses(t *testing.T) {
	clk := &tick{t: time.Date(2026, 7, 11, 10, 0, 0, 0, time.UTC)}
	d := NewDebouncer(500*time.Millisecond, clk.now)
	d.Mark("app.js")
	if got := d.Ready(); len(got) != 0 {
		t.Fatalf("Ready() immediately after Mark = %v, want none", got)
	}
	clk.advance(499 * time.Millisecond)
	if got := d.Ready(); len(got) != 0 {
		t.Fatalf("Ready() 1ms before the quiet period ends = %v, want none", got)
	}
	clk.advance(1 * time.Millisecond)
	if got := d.Ready(); !reflect.DeepEqual(got, []string{"app.js"}) {
		t.Fatalf("Ready() at exactly the quiet period = %v, want [app.js]", got)
	}
	if got := d.Ready(); len(got) != 0 {
		t.Fatalf("second Ready() = %v, want none (delivery clears the entry)", got)
	}
}

func TestDebouncerMarkResetsTheTimer(t *testing.T) {
	clk := &tick{t: time.Date(2026, 7, 11, 10, 0, 0, 0, time.UTC)}
	d := NewDebouncer(time.Second, clk.now)
	d.Mark("style.css")
	clk.advance(900 * time.Millisecond)
	d.Mark("style.css") // still being written: timer restarts
	clk.advance(900 * time.Millisecond)
	if got := d.Ready(); len(got) != 0 {
		t.Fatalf("Ready() = %v, want none (re-Mark must reset the quiet timer)", got)
	}
	clk.advance(100 * time.Millisecond)
	if got := d.Ready(); !reflect.DeepEqual(got, []string{"style.css"}) {
		t.Fatalf("Ready() = %v, want [style.css]", got)
	}
}

func TestDebouncerReturnsQuietPathsSortedAndKeepsBusyOnes(t *testing.T) {
	clk := &tick{t: time.Date(2026, 7, 11, 10, 0, 0, 0, time.UTC)}
	d := NewDebouncer(200*time.Millisecond, clk.now)
	d.Mark("b.txt")
	d.Mark("a.txt")
	clk.advance(150 * time.Millisecond)
	d.Mark("c.txt")
	clk.advance(50 * time.Millisecond)
	if got := d.Ready(); !reflect.DeepEqual(got, []string{"a.txt", "b.txt"}) {
		t.Fatalf("Ready() = %v, want [a.txt b.txt]", got)
	}
	clk.advance(150 * time.Millisecond)
	if got := d.Ready(); !reflect.DeepEqual(got, []string{"c.txt"}) {
		t.Fatalf("Ready() = %v, want [c.txt]", got)
	}
}

func TestDebouncerPathCanGoAroundAgain(t *testing.T) {
	clk := &tick{t: time.Date(2026, 7, 11, 10, 0, 0, 0, time.UTC)}
	d := NewDebouncer(100*time.Millisecond, clk.now)
	d.Mark("a.txt")
	clk.advance(100 * time.Millisecond)
	if got := d.Ready(); !reflect.DeepEqual(got, []string{"a.txt"}) {
		t.Fatalf("first cycle Ready() = %v", got)
	}
	d.Mark("a.txt")
	clk.advance(100 * time.Millisecond)
	if got := d.Ready(); !reflect.DeepEqual(got, []string{"a.txt"}) {
		t.Fatalf("second cycle Ready() = %v", got)
	}
}
