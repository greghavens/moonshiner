package fieldquote

import (
	"errors"
	"testing"
)

func TestQuoteTable(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{"plain", "badge 7", `"badge 7"`},
		{"empty", "", `""`},
		{"quote and backslash", `door "A" \ east`, `"door \"A\" \\ east"`},
		{"named escapes", "a\nb\tc\rd", `"a\nb\tc\rd"`},
		{"bell and vt are hex not letters", "\x07\x0b", `"\x07\x0B"`},
		{"escape byte uppercase hex", "\x1b[2J", `"\x1B[2J"`},
		{"del is not printable", "\x7f", `"\x7F"`},
		{"nul", "\x00", `"\x00"`},
		{"accented letters stay verbatim", "café", "\"café\""},
		{"no-break space escapes as rune", "a\u00a0b", `"a\u00A0b"`},
		{"line separator escapes as rune", "x\u2028y", `"x\u2028y"`},
		{"emoji stays verbatim", "ok \U0001f44d", "\"ok \U0001f44d\""},
		{"non-printable astral rune", "\U000e0001", `"\U000E0001"`},
		{"invalid utf-8 byte as byte escape", "caf\xe9", `"caf\xE9"`},
	}
	for _, c := range cases {
		if got := Quote(c.in); got != c.want {
			t.Errorf("%s: Quote(%q) = %s, want %s", c.name, c.in, got, c.want)
		}
	}
}

func TestUnquoteTable(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want string
	}{
		{"plain", `"badge 7"`, "badge 7"},
		{"empty", `""`, ""},
		{"quote and backslash", `"door \"A\" \\ east"`, `door "A" \ east`},
		{"named escapes", `"a\nb\tc\rd"`, "a\nb\tc\rd"},
		{"hex accepts lowercase", `"\x41\x6c"`, "Al"},
		{"two byte escapes forming utf-8", `"\xC3\xA9"`, "é"},
		{"u escape", `"\u00E9"`, "é"},
		{"u escape lowercase digits", `"\u00e9"`, "é"},
		{"big U escape", `"\U0001F600"`, "\U0001f600"},
	}
	for _, c := range cases {
		got, err := Unquote(c.in)
		if err != nil {
			t.Errorf("%s: Unquote(%s) error: %v", c.name, c.in, err)
			continue
		}
		if got != c.want {
			t.Errorf("%s: Unquote(%s) = %q, want %q", c.name, c.in, got, c.want)
		}
	}
}

func TestByteEscapeIsARawByteNotARune(t *testing.T) {
	got, err := Unquote(`"\xE9"`)
	if err != nil {
		t.Fatalf("Unquote: %v", err)
	}
	if len(got) != 1 || got[0] != 0xE9 {
		t.Fatalf("Unquote(\"\\xE9\") = %q (len %d), want the single byte 0xE9", got, len(got))
	}
}

func TestUnquoteErrors(t *testing.T) {
	cases := []struct {
		name string
		in   string
		want error
	}{
		{"no quotes at all", `badge`, ErrNotQuoted},
		{"missing closing quote", `"badge`, ErrNotQuoted},
		{"interior quote ends value early", `"a"b"`, ErrNotQuoted},
		{"content after closing quote", `"" `, ErrNotQuoted},
		{"escape swallows the closer", `"a\"`, ErrNotQuoted},
		{"backslash at end of input", `"a\`, ErrTrailingBackslash},
		{"unknown escape letter", `"\q"`, ErrUnknownEscape},
		{"hex escape cut short", `"\x4"`, ErrBadHex},
		{"hex escape bad digit", `"\xg1"`, ErrBadHex},
		{"u escape needs four digits", `"\u12"`, ErrBadHex},
		{"surrogate is not a code point", `"\uD800"`, ErrBadRune},
		{"surrogate pair is not accepted", `"\uD83D\uDE00"`, ErrBadRune},
		{"beyond max code point", `"\U00110000"`, ErrBadRune},
		{"raw newline inside value", "\"a\nb\"", ErrBareControl},
		{"raw tab inside value", "\"a\tb\"", ErrBareControl},
	}
	for _, c := range cases {
		_, err := Unquote(c.in)
		if !errors.Is(err, c.want) {
			t.Errorf("%s: Unquote(%q) error = %v, want %v", c.name, c.in, err, c.want)
		}
	}
}

func TestRoundTrip(t *testing.T) {
	values := []string{
		"",
		"badge 7",
		`door "A" \ east`,
		"a\nb\tc\rd",
		"\x00\x07\x1b\x7f",
		"café au lait",
		"a\u00a0b\u2028c",
		"ok \U0001f44d \U000e0001",
		"caf\xe9 raw byte",
	}
	for _, v := range values {
		q := Quote(v)
		back, err := Unquote(q)
		if err != nil {
			t.Errorf("Unquote(Quote(%q)) error: %v", v, err)
			continue
		}
		if back != v {
			t.Errorf("round trip %q -> %s -> %q", v, q, back)
		}
	}
}

func TestPrintable(t *testing.T) {
	cases := []struct {
		r    rune
		want bool
	}{
		{'A', true},
		{' ', true},
		{'~', true},
		{'\t', false},
		{'\n', false},
		{0x00, false},
		{0x7f, false},
		{0x00e9, true},
		{0x00a0, false},
		{0x2028, false},
		{0x1f44d, true},
		{0xe0001, false},
	}
	for _, c := range cases {
		if got := Printable(c.r); got != c.want {
			t.Errorf("Printable(%U) = %v, want %v", c.r, got, c.want)
		}
	}
}
