package logfmt

import (
	"errors"
	"reflect"
	"testing"
	"time"
)

func mustEncode(t *testing.T, fields []Field) string {
	t.Helper()
	line, err := Encode(fields)
	if err != nil {
		t.Fatalf("Encode(%v): unexpected error %v", fields, err)
	}
	return line
}

func mustDecode(t *testing.T, line string) []Field {
	t.Helper()
	fields, err := Decode(line)
	if err != nil {
		t.Fatalf("Decode(%q): unexpected error %v", line, err)
	}
	return fields
}

func TestEncodeBasics(t *testing.T) {
	line := mustEncode(t, []Field{
		{"level", "info"},
		{"msg", "user logged in"},
		{"attempts", 3},
		{"ratio", 0.25},
		{"ok", true},
		{"wait", 90 * time.Second},
	})
	want := `level=info msg="user logged in" attempts=3 ratio=0.25 ok=true wait=1m30s`
	if line != want {
		t.Errorf("Encode = %q\nwant       %q", line, want)
	}
}

func TestEncodePreservesKeyOrder(t *testing.T) {
	line := mustEncode(t, []Field{
		{"zebra", 1}, {"apple", 2}, {"mango", 3},
	})
	if line != "zebra=1 apple=2 mango=3" {
		t.Errorf("Encode = %q; fields must appear in the order given, not sorted", line)
	}
}

func TestEncodeQuotingAndEscaping(t *testing.T) {
	cases := []struct {
		key   string
		value string
		want  string
	}{
		{"env", "prod", "env=prod"},                       // simple values stay bare
		{"msg", "disk almost full", `msg="disk almost full"`},
		{"expr", "a=b", `expr="a=b"`},                      // '=' forces quotes
		{"note", "", `note=""`},                            // empty value must stay visible
		{"say", `he said "hi"`, `say="he said \"hi\""`},    // quotes escaped
		{"path", `C:\logs`, `path="C:\\logs"`},             // backslash escaped
		{"body", "line1\nline2", `body="line1\nline2"`},    // newline escaped
		{"cell", "a\tb", `cell="a\tb"`},                    // tab escaped
	}
	for _, tc := range cases {
		got := mustEncode(t, []Field{{tc.key, tc.value}})
		if got != tc.want {
			t.Errorf("Encode({%q: %q}) = %q, want %q", tc.key, tc.value, got, tc.want)
		}
	}
}

func TestEncodeIntWidths(t *testing.T) {
	line := mustEncode(t, []Field{
		{"small", int(7)},
		{"big", int64(9_223_372_036_854_775_807)},
		{"neg", int64(-42)},
	})
	if line != "small=7 big=9223372036854775807 neg=-42" {
		t.Errorf("Encode = %q", line)
	}
}

func TestEncodeRejectsBadKeys(t *testing.T) {
	bad := []string{"", "user name", "a=b", `he"llo`, "tab\tkey"}
	for _, key := range bad {
		if _, err := Encode([]Field{{key, "v"}}); err == nil {
			t.Errorf("Encode with key %q: error = nil, want non-nil", key)
		}
	}
}

func TestEncodeRejectsUnsupportedValueTypes(t *testing.T) {
	for _, v := range []any{[]string{"a"}, map[string]int{"a": 1}, struct{ X int }{1}, nil} {
		if _, err := Encode([]Field{{"k", v}}); err == nil {
			t.Errorf("Encode with value of type %T: error = nil, want non-nil", v)
		}
	}
}

func TestDecodeTypedValues(t *testing.T) {
	fields := mustDecode(t, `level=info msg="user logged in" attempts=3 ratio=0.25 ok=true wait=1m30s`)
	want := []Field{
		{"level", "info"},
		{"msg", "user logged in"},
		{"attempts", int64(3)},
		{"ratio", 0.25},
		{"ok", true},
		{"wait", 90 * time.Second},
	}
	if !reflect.DeepEqual(fields, want) {
		t.Errorf("Decode = %#v\nwant      %#v", fields, want)
	}
}

func TestDecodeScientificAndNegativeNumbers(t *testing.T) {
	fields := mustDecode(t, "delta=-7 load=1e3 drift=-0.5")
	want := []Field{
		{"delta", int64(-7)},
		{"load", float64(1000)},
		{"drift", -0.5},
	}
	if !reflect.DeepEqual(fields, want) {
		t.Errorf("Decode = %#v\nwant      %#v", fields, want)
	}
}

func TestDecodeQuotedValuesStayStrings(t *testing.T) {
	fields := mustDecode(t, `flag="true" count="42" pause="5s"`)
	want := []Field{
		{"flag", "true"},
		{"count", "42"},
		{"pause", "5s"},
	}
	if !reflect.DeepEqual(fields, want) {
		t.Errorf("quoted values must decode as strings, not re-typed: got %#v", fields)
	}
}

func TestDecodeBareKeyAndEmptyValue(t *testing.T) {
	fields := mustDecode(t, "dry-run verbose=true note=")
	want := []Field{
		{"dry-run", true},   // bare key: boolean flag
		{"verbose", true},
		{"note", ""},        // key= : empty string
	}
	if !reflect.DeepEqual(fields, want) {
		t.Errorf("Decode = %#v\nwant      %#v", fields, want)
	}
}

func TestDecodeEscapes(t *testing.T) {
	fields := mustDecode(t, `path="C:\\logs" say="he said \"hi\"" body="line1\nline2" cell="a\tb"`)
	want := []Field{
		{"path", `C:\logs`},
		{"say", `he said "hi"`},
		{"body", "line1\nline2"},
		{"cell", "a\tb"},
	}
	if !reflect.DeepEqual(fields, want) {
		t.Errorf("Decode = %#v\nwant      %#v", fields, want)
	}
}

func TestDecodeWhitespaceHandling(t *testing.T) {
	fields := mustDecode(t, "  a=1    b=2  ")
	want := []Field{{"a", int64(1)}, {"b", int64(2)}}
	if !reflect.DeepEqual(fields, want) {
		t.Errorf("Decode = %#v, want %#v (runs of spaces separate fields)", fields, want)
	}

	if got := mustDecode(t, ""); len(got) != 0 {
		t.Errorf("Decode(\"\") = %#v, want no fields", got)
	}
	if got := mustDecode(t, "   "); len(got) != 0 {
		t.Errorf("Decode of blank line = %#v, want no fields", got)
	}
}

func TestDecodeKeepsDuplicateKeysInOrder(t *testing.T) {
	fields := mustDecode(t, "retry=1 retry=2 retry=3")
	want := []Field{{"retry", int64(1)}, {"retry", int64(2)}, {"retry", int64(3)}}
	if !reflect.DeepEqual(fields, want) {
		t.Errorf("Decode = %#v, want duplicates preserved in order %#v", fields, want)
	}
}

func TestEncodeDecodeUsesDocumentedWireNormalization(t *testing.T) {
	original := []Field{
		{"native_int", int(7)},
		{"integral_float", float64(1000)},
		{"typed_string", "true"},
	}
	line := mustEncode(t, original)
	if line != "native_int=7 integral_float=1000 typed_string=true" {
		t.Fatalf("Encode normalization fixture = %q", line)
	}

	decoded := mustDecode(t, line)
	want := []Field{
		{"native_int", int64(7)},
		{"integral_float", int64(1000)},
		{"typed_string", true},
	}
	if !reflect.DeepEqual(decoded, want) {
		t.Errorf("Decode normalization = %#v\nwant                 %#v", decoded, want)
	}
}

func TestDecodeErrorPositions(t *testing.T) {
	cases := []struct {
		line    string
		wantPos int
	}{
		{`msg="unterminated`, 4},        // opening quote never closed
		{`err="bad \q escape"`, 9},      // unknown escape, at the backslash
		{`k="ab"junk`, 6},               // text glued to a closing quote
		{`=5`, 0},                       // empty key
		{`a=1 =2`, 4},                   // empty key mid-line
		{`path=a"b`, 6},                 // stray quote inside a bare value
	}
	for _, tc := range cases {
		_, err := Decode(tc.line)
		if err == nil {
			t.Errorf("Decode(%q): error = nil, want *SyntaxError", tc.line)
			continue
		}
		var serr *SyntaxError
		if !errors.As(err, &serr) {
			t.Errorf("Decode(%q): error %T (%v) is not a *SyntaxError", tc.line, err, err)
			continue
		}
		if serr.Pos != tc.wantPos {
			t.Errorf("Decode(%q): SyntaxError.Pos = %d, want %d", tc.line, serr.Pos, tc.wantPos)
		}
		if serr.Msg == "" {
			t.Errorf("Decode(%q): SyntaxError.Msg is empty", tc.line)
		}
	}
}

func TestRoundTrip(t *testing.T) {
	original := []Field{
		{"service", "billing-api"},
		{"msg", `charge failed: card "4242" declined`},
		{"amount_cents", int64(12999)},
		{"fee_rate", 0.029},
		{"retriable", false},
		{"backoff", 2500 * time.Millisecond},
		{"trace", ""},
	}
	line := mustEncode(t, original)
	decoded := mustDecode(t, line)
	if !reflect.DeepEqual(decoded, original) {
		t.Errorf("round trip lost fidelity:\nencoded %q\ngot     %#v\nwant    %#v", line, decoded, original)
	}
}
