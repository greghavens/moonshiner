package dirwalk

import (
	"path/filepath"
	"reflect"
	"testing"
)

// Acceptance tests for WalkFiltered: gitignore-style excludes,
// MaxDepth limiting, and the dirs-first deterministic ordering the
// manifest differ needs.

func mustWalkFiltered(t *testing.T, root string, opts Options) []Entry {
	t.Helper()
	got, err := WalkFiltered(root, opts)
	if err != nil {
		t.Fatalf("WalkFiltered(%+v): %v", opts, err)
	}
	return got
}

func TestFilteredOrderIsDirsFirstThenFilesAlphabetical(t *testing.T) {
	root := t.TempDir()
	writeTree(t, root, []string{"zeta", "alpha/sub"}, map[string]string{
		"beta.txt":           "b",
		"apple.txt":          "a",
		"alpha/z.txt":        "z",
		"alpha/a.txt":        "a",
		"alpha/sub/deep.txt": "d",
		"zeta/1.txt":         "1",
	})
	got := mustWalkFiltered(t, root, Options{})
	want := []string{
		"alpha",
		"alpha/sub",
		"alpha/sub/deep.txt",
		"alpha/a.txt",
		"alpha/z.txt",
		"zeta",
		"zeta/1.txt",
		"apple.txt",
		"beta.txt",
	}
	if !reflect.DeepEqual(paths(got), want) {
		t.Fatalf("order = %v, want %v", paths(got), want)
	}
}

func TestSlashlessPatternsMatchBasenamesAtAnyDepth(t *testing.T) {
	root := t.TempDir()
	writeTree(t, root, nil, map[string]string{
		"x.log":                    "x",
		"keep.txt":                 "k",
		"sub/y.log":                "y",
		"sub/keep.md":              "k",
		"node_modules/pkg/main.js": "m",
		"sub/node_modules/lib.js":  "l",
	})
	got := mustWalkFiltered(t, root, Options{Excludes: []string{"*.log", "node_modules"}})
	want := []string{"sub", "sub/keep.md", "keep.txt"}
	if !reflect.DeepEqual(paths(got), want) {
		t.Fatalf("filtered = %v, want %v", paths(got), want)
	}
}

func TestPatternsWithSlashesAnchorAtTheRoot(t *testing.T) {
	root := t.TempDir()
	writeTree(t, root, nil, map[string]string{
		"docs/tmp/draft.md":  "d",
		"docs/readme.md":     "r",
		"other/docs/tmp/x.md": "x",
	})
	got := mustWalkFiltered(t, root, Options{Excludes: []string{"docs/tmp"}})
	want := []string{
		"docs",
		"docs/readme.md",
		"other",
		"other/docs",
		"other/docs/tmp",
		"other/docs/tmp/x.md",
	}
	if !reflect.DeepEqual(paths(got), want) {
		t.Fatalf("filtered = %v, want %v (only the root-anchored docs/tmp goes)", paths(got), want)
	}
}

func TestStarDoesNotCrossSlashesAndMatchedDirsArePruned(t *testing.T) {
	root := t.TempDir()
	writeTree(t, root, nil, map[string]string{
		"build/a.o":      "a",
		"build/deep/b.o": "b",
		"src/main.go":    "m",
	})
	got := mustWalkFiltered(t, root, Options{Excludes: []string{"build/*"}})
	want := []string{"build", "src", "src/main.go"}
	if !reflect.DeepEqual(paths(got), want) {
		t.Fatalf("filtered = %v, want %v (build's children pruned, build itself kept)", paths(got), want)
	}
}

func TestTrailingSlashPatternOnlyMatchesDirectories(t *testing.T) {
	root := t.TempDir()
	writeTree(t, root, []string{"tmp"}, map[string]string{
		"tmp/junk":  "j",
		"notes/tmp": "a file that happens to be named tmp",
	})
	got := mustWalkFiltered(t, root, Options{Excludes: []string{"tmp/"}})
	want := []Entry{
		{Path: "notes", IsDir: true, Size: 0},
		{Path: "notes/tmp", IsDir: false, Size: 35},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("filtered = %+v, want %+v (dir tmp pruned, file tmp kept)", got, want)
	}
}

func TestMaxDepthLimitsHowDeepEntriesGo(t *testing.T) {
	root := t.TempDir()
	writeTree(t, root, nil, map[string]string{
		"a/b/c/d.txt": "deep",
		"top.txt":     "t",
	})

	got := mustWalkFiltered(t, root, Options{MaxDepth: 1})
	if want := []string{"a", "top.txt"}; !reflect.DeepEqual(paths(got), want) {
		t.Fatalf("MaxDepth 1 = %v, want %v", paths(got), want)
	}

	got = mustWalkFiltered(t, root, Options{MaxDepth: 2})
	if want := []string{"a", "a/b", "top.txt"}; !reflect.DeepEqual(paths(got), want) {
		t.Fatalf("MaxDepth 2 = %v, want %v", paths(got), want)
	}

	all := []string{"a", "a/b", "a/b/c", "a/b/c/d.txt", "top.txt"}
	got = mustWalkFiltered(t, root, Options{MaxDepth: 0})
	if !reflect.DeepEqual(paths(got), all) {
		t.Fatalf("MaxDepth 0 (unlimited) = %v, want %v", paths(got), all)
	}
	got = mustWalkFiltered(t, root, Options{MaxDepth: -3})
	if !reflect.DeepEqual(paths(got), all) {
		t.Fatalf("negative MaxDepth (unlimited) = %v, want %v", paths(got), all)
	}
}

func TestExcludesAndMaxDepthCompose(t *testing.T) {
	root := t.TempDir()
	writeTree(t, root, nil, map[string]string{
		"a/skip.log":   "s",
		"a/keep.txt":   "k",
		"a/deep/x.txt": "x",
		"b.log":        "b",
	})
	got := mustWalkFiltered(t, root, Options{Excludes: []string{"*.log"}, MaxDepth: 2})
	want := []string{"a", "a/deep", "a/keep.txt"}
	if !reflect.DeepEqual(paths(got), want) {
		t.Fatalf("filtered = %v, want %v", paths(got), want)
	}
}

func TestFilteredWalkIsRepeatable(t *testing.T) {
	root := t.TempDir()
	writeTree(t, root, []string{"q", "b", "j"}, map[string]string{
		"j/9.txt": "9", "j/1.txt": "1", "m.txt": "m", "c.txt": "c", "b/f.txt": "f",
	})
	opts := Options{Excludes: []string{"*.tmp"}}
	first := mustWalkFiltered(t, root, opts)
	for i := 0; i < 5; i++ {
		if got := mustWalkFiltered(t, root, opts); !reflect.DeepEqual(got, first) {
			t.Fatalf("run %d differs: %v vs %v", i+2, paths(got), paths(first))
		}
	}
	want := []string{"b", "b/f.txt", "j", "j/1.txt", "j/9.txt", "q", "c.txt", "m.txt"}
	if !reflect.DeepEqual(paths(first), want) {
		t.Fatalf("order = %v, want %v", paths(first), want)
	}
}

func TestBadPatternIsAnError(t *testing.T) {
	root := t.TempDir()
	writeTree(t, root, nil, map[string]string{"f.txt": "f"})
	if _, err := WalkFiltered(root, Options{Excludes: []string{"["}}); err == nil {
		t.Fatal("malformed pattern must make WalkFiltered error")
	}
}

func TestFilteredMissingRootErrors(t *testing.T) {
	if _, err := WalkFiltered(filepath.Join(t.TempDir(), "gone"), Options{}); err == nil {
		t.Fatal("WalkFiltered on a missing root must return an error")
	}
}
