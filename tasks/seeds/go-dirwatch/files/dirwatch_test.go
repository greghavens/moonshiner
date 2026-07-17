package dirwatch

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

// Pins the existing snapshot/diff behavior. Must keep passing.

func write(t *testing.T, root, rel, content string) {
	t.Helper()
	p := filepath.Join(root, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestTakeUsesSlashRelativePaths(t *testing.T) {
	root := t.TempDir()
	write(t, root, "index.html", "<html>")
	write(t, root, "css/site.css", "body{}")
	snap, err := Take(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(snap) != 2 {
		t.Fatalf("snapshot has %d entries, want 2", len(snap))
	}
	for _, p := range []string{"index.html", "css/site.css"} {
		if _, ok := snap[p]; !ok {
			t.Fatalf("snapshot missing %q (keys: %v)", p, snap)
		}
	}
}

func TestDiffReportsAddRemoveModifySorted(t *testing.T) {
	root := t.TempDir()
	write(t, root, "a.js", "let a")
	write(t, root, "b.js", "let b")
	write(t, root, "c.js", "let c")
	before, err := Take(root)
	if err != nil {
		t.Fatal(err)
	}
	write(t, root, "b.js", "let b = 2") // modified
	if err := os.Remove(filepath.Join(root, "c.js")); err != nil {
		t.Fatal(err)
	}
	write(t, root, "z.js", "let z")
	write(t, root, "d.js", "let d")
	after, err := Take(root)
	if err != nil {
		t.Fatal(err)
	}
	got := Diff(before, after)
	want := Changes{
		Added:    []string{"d.js", "z.js"},
		Removed:  []string{"c.js"},
		Modified: []string{"b.js"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Diff = %+v, want %+v", got, want)
	}
}

func TestDiffOfIdenticalSnapshotsIsEmpty(t *testing.T) {
	root := t.TempDir()
	write(t, root, "app.js", "boot()")
	s1, err := Take(root)
	if err != nil {
		t.Fatal(err)
	}
	s2, err := Take(root)
	if err != nil {
		t.Fatal(err)
	}
	got := Diff(s1, s2)
	if len(got.Added)+len(got.Removed)+len(got.Modified) != 0 {
		t.Fatalf("Diff of identical snapshots = %+v, want empty", got)
	}
}

func TestSameContentDifferentPathIsAddPlusRemove(t *testing.T) {
	root := t.TempDir()
	write(t, root, "one.txt", "same bytes")
	before, err := Take(root)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(filepath.Join(root, "one.txt"), filepath.Join(root, "two.txt")); err != nil {
		t.Fatal(err)
	}
	after, err := Take(root)
	if err != nil {
		t.Fatal(err)
	}
	got := Diff(before, after)
	want := Changes{Added: []string{"two.txt"}, Removed: []string{"one.txt"}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Diff = %+v, want %+v", got, want)
	}
}
