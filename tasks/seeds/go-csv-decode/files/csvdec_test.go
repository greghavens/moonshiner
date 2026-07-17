package csvdec

import (
	"strings"
	"testing"
)

func assertRecords(t *testing.T, got [][]string, want [][]string) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("got %d records %v, want %d %v", len(got), got, len(want), want)
	}
	for i := range want {
		if len(got[i]) != len(want[i]) {
			t.Fatalf("record %d = %q, want %q", i, got[i], want[i])
		}
		for j := range want[i] {
			if got[i][j] != want[i][j] {
				t.Fatalf("record %d field %d = %q, want %q", i, j, got[i][j], want[i][j])
			}
		}
	}
}

func TestParseRecordsSimple(t *testing.T) {
	got, err := ParseRecords([]byte("a,b,c\n1,2,3\n"))
	if err != nil {
		t.Fatalf("ParseRecords: %v", err)
	}
	assertRecords(t, got, [][]string{{"a", "b", "c"}, {"1", "2", "3"}})
}

func TestParseRecordsCRLF(t *testing.T) {
	got, err := ParseRecords([]byte("a,b\r\n1,2\r\n"))
	if err != nil {
		t.Fatalf("ParseRecords: %v", err)
	}
	assertRecords(t, got, [][]string{{"a", "b"}, {"1", "2"}})
	if strings.Contains(got[1][1], "\r") {
		t.Fatal("carriage return leaked into a field value")
	}
}

func TestParseRecordsNoTrailingNewline(t *testing.T) {
	got, err := ParseRecords([]byte("a,b\n1,2"))
	if err != nil {
		t.Fatalf("ParseRecords: %v", err)
	}
	assertRecords(t, got, [][]string{{"a", "b"}, {"1", "2"}})
}

func TestParseRecordsQuoting(t *testing.T) {
	data := "name,note\n\"Smith, John\",\"said \"\"hi\"\" twice\"\n"
	got, err := ParseRecords([]byte(data))
	if err != nil {
		t.Fatalf("ParseRecords: %v", err)
	}
	assertRecords(t, got, [][]string{
		{"name", "note"},
		{"Smith, John", `said "hi" twice`},
	})
}

func TestParseRecordsQuotedNewlineAndEmpty(t *testing.T) {
	data := "id,body,tag\n7,\"line1\nline2\",\"\"\n"
	got, err := ParseRecords([]byte(data))
	if err != nil {
		t.Fatalf("ParseRecords: %v", err)
	}
	assertRecords(t, got, [][]string{
		{"id", "body", "tag"},
		{"7", "line1\nline2", ""},
	})
}

func TestParseRecordsSkipsBlankLines(t *testing.T) {
	got, err := ParseRecords([]byte("a,b\n\n1,2\n\n\n3,4\n"))
	if err != nil {
		t.Fatalf("ParseRecords: %v", err)
	}
	assertRecords(t, got, [][]string{{"a", "b"}, {"1", "2"}, {"3", "4"}})
}

func TestParseRecordsUnterminatedQuote(t *testing.T) {
	_, err := ParseRecords([]byte("a,b\n\"oops,2\n"))
	if err == nil {
		t.Fatal("unterminated quote accepted")
	}
	if !strings.Contains(err.Error(), "line 2") {
		t.Fatalf("error should locate the problem on line 2, got: %v", err)
	}
}

func TestParseRecordsStrayQuoteInBareField(t *testing.T) {
	_, err := ParseRecords([]byte("a,b\nfo\"o,2\n"))
	if err == nil {
		t.Fatal("stray quote inside an unquoted field accepted")
	}
	if !strings.Contains(err.Error(), "line 2") {
		t.Fatalf("error should locate the problem on line 2, got: %v", err)
	}
}

func TestParseRecordsEmptyInput(t *testing.T) {
	got, err := ParseRecords(nil)
	if err != nil {
		t.Fatalf("ParseRecords(empty): %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("ParseRecords(empty) = %v, want no records", got)
	}
}

type pageStat struct {
	Site    string  `csv:"site"`
	Hits    int     `csv:"hits"`
	Ratio   float64 `csv:"bounce_ratio"`
	Mobile  bool    `csv:"mobile"`
	Ignored string  `csv:"-"`
	Comment string  // no tag: matches header "comment" case-insensitively
}

func TestUnmarshalHeaderToStruct(t *testing.T) {
	data := []byte("site,hits,bounce_ratio,mobile,Comment\n" +
		"\"example.com, inc\",1200,0.35,true,fine\n" +
		"docs.example.com,88,0.5,false,\"has, comma\"\n")
	var rows []pageStat
	if err := Unmarshal(data, &rows); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	if len(rows) != 2 {
		t.Fatalf("decoded %d rows, want 2", len(rows))
	}
	want0 := pageStat{Site: "example.com, inc", Hits: 1200, Ratio: 0.35, Mobile: true, Comment: "fine"}
	if rows[0] != want0 {
		t.Fatalf("row 0 = %+v, want %+v", rows[0], want0)
	}
	want1 := pageStat{Site: "docs.example.com", Hits: 88, Ratio: 0.5, Mobile: false, Comment: "has, comma"}
	if rows[1] != want1 {
		t.Fatalf("row 1 = %+v, want %+v", rows[1], want1)
	}
}

func TestUnmarshalUntaggedFieldMatchesCaseInsensitively(t *testing.T) {
	var rows []pageStat
	data := []byte("site,hits,bounce_ratio,mobile,COMMENT\nx.io,1,0,false,shouty header\n")
	if err := Unmarshal(data, &rows); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	if rows[0].Comment != "shouty header" {
		t.Fatalf("Comment = %q, want match via case-insensitive header", rows[0].Comment)
	}
}

func TestUnmarshalDashTagNeverMaps(t *testing.T) {
	// A column literally named "-" exists; the Ignored field still must
	// not be populated.
	data := []byte("site,hits,bounce_ratio,mobile,-\nx.io,1,0,false,secret\n")
	var rows []pageStat
	if err := Unmarshal(data, &rows); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	if rows[0].Ignored != "" {
		t.Fatalf("Ignored = %q, want empty — csv:\"-\" opts the field out", rows[0].Ignored)
	}
}

func TestUnmarshalExtraColumnsIgnoredMissingLeftZero(t *testing.T) {
	data := []byte("site,unknown_col,hits\nx.io,whatever,42\n")
	var rows []pageStat
	if err := Unmarshal(data, &rows); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	r := rows[0]
	if r.Site != "x.io" || r.Hits != 42 {
		t.Fatalf("mapped fields wrong: %+v", r)
	}
	if r.Ratio != 0 || r.Mobile || r.Comment != "" {
		t.Fatalf("fields without columns must stay zero, got %+v", r)
	}
}

func TestUnmarshalFieldCountMismatch(t *testing.T) {
	data := []byte("site,hits\nx.io\n")
	var rows []pageStat
	err := Unmarshal(data, &rows)
	if err == nil {
		t.Fatal("row with wrong field count accepted")
	}
	if !strings.Contains(err.Error(), "line 2") {
		t.Fatalf("error should name line 2, got: %v", err)
	}
}

func TestUnmarshalConversionErrorNamesColumnAndLine(t *testing.T) {
	data := []byte("site,hits\nx.io,12\ny.io,many\n")
	var rows []pageStat
	err := Unmarshal(data, &rows)
	if err == nil {
		t.Fatal("bad int accepted")
	}
	msg := err.Error()
	if !strings.Contains(msg, "hits") || !strings.Contains(msg, "line 3") {
		t.Fatalf("error should name column \"hits\" and \"line 3\", got: %v", err)
	}
}

func TestUnmarshalHeaderOnlyGivesEmptySlice(t *testing.T) {
	var rows []pageStat
	if err := Unmarshal([]byte("site,hits\n"), &rows); err != nil {
		t.Fatalf("Unmarshal(header only): %v", err)
	}
	if len(rows) != 0 {
		t.Fatalf("rows = %v, want empty", rows)
	}
}

func TestUnmarshalEmptyInputIsError(t *testing.T) {
	var rows []pageStat
	if err := Unmarshal(nil, &rows); err == nil {
		t.Fatal("input without a header row accepted")
	}
}

func TestUnmarshalRejectsWrongDestination(t *testing.T) {
	if err := Unmarshal([]byte("a\n1\n"), nil); err == nil {
		t.Fatal("nil destination accepted")
	}
	var notPointer []pageStat
	if err := Unmarshal([]byte("a\n1\n"), notPointer); err == nil {
		t.Fatal("non-pointer destination accepted")
	}
	x := 7
	if err := Unmarshal([]byte("a\n1\n"), &x); err == nil {
		t.Fatal("pointer to non-slice accepted")
	}
	var wrongElem []int
	if err := Unmarshal([]byte("a\n1\n"), &wrongElem); err == nil {
		t.Fatal("slice of non-struct accepted")
	}
}

func TestUnmarshalUnsupportedFieldTypeIsError(t *testing.T) {
	type bad struct {
		Tags []string `csv:"tags"`
	}
	var rows []bad
	err := Unmarshal([]byte("tags\na;b\n"), &rows)
	if err == nil {
		t.Fatal("unsupported field type accepted")
	}
	if !strings.Contains(err.Error(), "Tags") {
		t.Fatalf("error should name the offending struct field, got: %v", err)
	}
}

func TestUnmarshalUnicodePassthrough(t *testing.T) {
	type row struct {
		City string `csv:"city"`
	}
	var rows []row
	if err := Unmarshal([]byte("city\n\"Zürich, CH\"\n"), &rows); err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	if rows[0].City != "Zürich, CH" {
		t.Fatalf("City = %q", rows[0].City)
	}
}
