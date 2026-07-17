package glob

import (
	"errors"
	"testing"
)

func TestMatchTable(t *testing.T) {
	cases := []struct {
		pattern, name string
		want          bool
	}{
		// literals
		{"main.go", "main.go", true},
		{"main.go", "main.rs", false},
		{"README", "readme", false}, // case-sensitive
		{"", "", true},
		{"", "x", false},
		{"abc", "ab", false},
		{"ab", "abc", false},

		// single star: any run of non-separator characters, possibly empty
		{"*", "", true},
		{"*", "readme", true},
		{"*", "docs/readme", false},
		{"*.go", "main.go", true},
		{"*.go", "cmd/main.go", false},
		{"a*b", "ab", true},
		{"a*b", "aXYZb", true},
		{"a*b", "aXbXb", true},      // must backtrack to the last b
		{"a*b*c", "aXbYbZc", true},  // multiple stars with backtracking
		{"*a*", "banana", true},
		{"a*a", "aa", true},
		{"a*a", "a", false},

		// question mark: exactly one character, never the separator,
		// counted in runes not bytes
		{"?at", "cat", true},
		{"?at", "at", false},
		{"?at", "flat", false},
		{"?at", "ñat", true},
		{"caf?", "café", true},
		{"a?c", "a/c", false},

		// double star: any run of characters INCLUDING separators
		{"**", "docs/readme.md", true},
		{"**", "", true},
		{"src/**.go", "src/main.go", true},
		{"src/**.go", "src/a/b/c.go", true},
		{"src/**.go", "lib/a.go", false},
		{"**/*.txt", "docs/notes.txt", true},
		{"**/*.txt", "a/b/notes.txt", true},
		{"**/*.txt", "notes.txt", false}, // the literal / still has to be there
		{"a***b", "a/xb", true},          // *** = ** then *

		// character classes
		{"[abc]at", "bat", true},
		{"[abc]at", "cat", true},
		{"[abc]at", "dat", false},
		{"[a-c]at", "bat", true},
		{"[a-c]at", "eat", false},
		{"[!0-9]x", "ax", true},
		{"[!0-9]x", "7x", false},
		{"report-[0-9][0-9]", "report-42", true},
		{"report-[0-9][0-9]", "report-4x", false},
		{"[abc]at", "at", false},

		// backslash escapes match the next character literally
		{`data\*`, "data*", true},
		{`data\*`, "dataX", false},
		{`data\*`, "data", false},
		{`\[ok\]`, "[ok]", true},
		{`a\?b`, "a?b", true},
		{`a\?b`, "aXb", false},
	}
	for _, tc := range cases {
		got, err := Match(tc.pattern, tc.name)
		if err != nil {
			t.Errorf("Match(%q, %q): unexpected error %v", tc.pattern, tc.name, err)
			continue
		}
		if got != tc.want {
			t.Errorf("Match(%q, %q) = %v, want %v", tc.pattern, tc.name, got, tc.want)
		}
	}
}

func TestMalformedPatterns(t *testing.T) {
	bad := []string{
		"[abc",   // unterminated class
		"a[",     // unterminated class at end
		`trail\`, // dangling escape
		"[z-a]x", // inverted range
		"[]x",    // empty class
		"[!]x",   // negated empty class
	}
	for _, p := range bad {
		matched, err := Match(p, "anything")
		if !errors.Is(err, ErrBadPattern) {
			t.Errorf("Match(%q, ...) error = %v, want ErrBadPattern", p, err)
		}
		if matched {
			t.Errorf("Match(%q, ...) reported a match alongside the error", p)
		}
	}
}

func TestBadPatternReportedEvenWhenNameCouldNeverMatch(t *testing.T) {
	// The mismatch is at the front of the name; the malformed class is at
	// the back of the pattern. The pattern is still malformed.
	if _, err := Match("zzz[abc", "different"); !errors.Is(err, ErrBadPattern) {
		t.Fatalf("error = %v, want ErrBadPattern regardless of the name", err)
	}
}

func TestGoodPatternsNeverError(t *testing.T) {
	for _, p := range []string{"*", "**", "a?b", "[a-z]*", `x\*y`, "[!x]"} {
		if _, err := Match(p, "some/input"); err != nil {
			t.Errorf("Match(%q, ...) unexpected error: %v", p, err)
		}
	}
}
