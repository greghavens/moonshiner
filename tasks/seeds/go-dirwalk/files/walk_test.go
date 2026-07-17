package dirwalk

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

// writeTree materializes a fixture tree under root. Keys of files are
// slash-separated relative paths; dirs lists directories that must
// exist even if empty.
func writeTree(t *testing.T, root string, dirs []string, files map[string]string) {
	t.Helper()
	for _, d := range dirs {
		if err := os.MkdirAll(filepath.Join(root, filepath.FromSlash(d)), 0o755); err != nil {
			t.Fatalf("mkdir %s: %v", d, err)
		}
	}
	for p, content := range files {
		full := filepath.Join(root, filepath.FromSlash(p))
		if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
			t.Fatalf("mkdir for %s: %v", p, err)
		}
		if err := os.WriteFile(full, []byte(content), 0o644); err != nil {
			t.Fatalf("write %s: %v", p, err)
		}
	}
}

// paths projects entries onto their Path field.
func paths(entries []Entry) []string {
	out := make([]string, len(entries))
	for i, e := range entries {
		out[i] = e.Path
	}
	return out
}

func TestWalkListsEverythingInLexicalOrder(t *testing.T) {
	root := t.TempDir()
	writeTree(t, root, []string{"cache"}, map[string]string{
		"z.txt":          "zz",
		"a.txt":          "a",
		"cache/blob.bin": "0123456789",
	})
	got, err := Walk(root)
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	want := []string{"a.txt", "cache", "cache/blob.bin", "z.txt"}
	if !reflect.DeepEqual(paths(got), want) {
		t.Fatalf("Walk order = %v, want %v", paths(got), want)
	}
}

func TestWalkReportsSizesAndDirFlags(t *testing.T) {
	root := t.TempDir()
	writeTree(t, root, []string{"logs"}, map[string]string{
		"logs/app.log": "hello world",
	})
	got, err := Walk(root)
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	want := []Entry{
		{Path: "logs", IsDir: true, Size: 0},
		{Path: "logs/app.log", IsDir: false, Size: 11},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Walk = %+v, want %+v", got, want)
	}
}

func TestWalkEmptyRoot(t *testing.T) {
	got, err := Walk(t.TempDir())
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("Walk of empty dir = %v, want no entries", got)
	}
}

func TestWalkMissingRootErrors(t *testing.T) {
	if _, err := Walk(filepath.Join(t.TempDir(), "no-such-dir")); err == nil {
		t.Fatal("Walk on a missing root must return an error")
	}
}
