package report

import (
	"bytes"
	"errors"
	"strings"
	"testing"
)

// Acceptance tests for streaming reports (NewStream: rows flow to an
// io.Writer in batches of flushEvery) and column subset selection
// (Report.Subset). Unknown-column failures from the new APIs must
// satisfy errors.Is(err, ErrUnknownColumn).

// recorder captures everything written to it.
type recorder struct {
	buf    bytes.Buffer
	writes int
}

func (r *recorder) Write(p []byte) (int, error) {
	r.writes++
	return r.buf.Write(p)
}

func (r *recorder) String() string { return r.buf.String() }

func TestStreamWritesHeaderImmediately(t *testing.T) {
	rec := &recorder{}
	if _, err := NewStream(rec, []string{"id", "name"}, 3); err != nil {
		t.Fatalf("NewStream: %v", err)
	}
	if got := rec.String(); got != "id,name\n" {
		t.Fatalf("after NewStream, writer holds %q, want just the header", got)
	}
}

func TestStreamFlushesAtThreshold(t *testing.T) {
	rec := &recorder{}
	s, err := NewStream(rec, []string{"id", "name"}, 2)
	if err != nil {
		t.Fatalf("NewStream: %v", err)
	}
	if err := s.Add(map[string]string{"id": "1", "name": "ana"}); err != nil {
		t.Fatalf("Add: %v", err)
	}
	if got := rec.String(); got != "id,name\n" {
		t.Fatalf("one row buffered, but writer holds %q — flushed too early", got)
	}
	if err := s.Add(map[string]string{"id": "2"}); err != nil {
		t.Fatalf("Add: %v", err)
	}
	if got := rec.String(); got != "id,name\n1,ana\n2,\n" {
		t.Fatalf("after second row, writer holds %q, want both rows flushed", got)
	}
	if err := s.Add(map[string]string{"id": "3", "name": "bo"}); err != nil {
		t.Fatalf("Add: %v", err)
	}
	if got := rec.String(); got != "id,name\n1,ana\n2,\n" {
		t.Fatalf("third row buffered, but writer holds %q", got)
	}
	if err := s.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if got := rec.String(); got != "id,name\n1,ana\n2,\n3,bo\n" {
		t.Fatalf("after Close, writer holds %q, want the remainder flushed", got)
	}
}

func TestStreamFlushEveryOne(t *testing.T) {
	rec := &recorder{}
	s, err := NewStream(rec, []string{"n"}, 1)
	if err != nil {
		t.Fatalf("NewStream: %v", err)
	}
	for _, v := range []string{"a", "b"} {
		if err := s.Add(map[string]string{"n": v}); err != nil {
			t.Fatalf("Add(%s): %v", v, err)
		}
	}
	if got := rec.String(); got != "n\na\nb\n" {
		t.Fatalf("flushEvery=1 should write each row as it arrives, got %q", got)
	}
	if err := s.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if got := rec.String(); got != "n\na\nb\n" {
		t.Fatalf("Close added bytes it should not have: %q", got)
	}
}

func TestStreamQuotesLikeTheBatchWriter(t *testing.T) {
	rec := &recorder{}
	s, err := NewStream(rec, []string{"id", "note"}, 1)
	if err != nil {
		t.Fatalf("NewStream: %v", err)
	}
	if err := s.Add(map[string]string{"id": "1", "note": "a,b"}); err != nil {
		t.Fatalf("Add: %v", err)
	}
	if err := s.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if got := rec.String(); got != "id,note\n1,\"a,b\"\n" {
		t.Fatalf("streamed CSV = %q, want standard quoting", got)
	}
}

func TestStreamRejectsUnknownColumn(t *testing.T) {
	rec := &recorder{}
	s, err := NewStream(rec, []string{"id"}, 5)
	if err != nil {
		t.Fatalf("NewStream: %v", err)
	}
	err = s.Add(map[string]string{"id": "1", "phone": "555"})
	if !errors.Is(err, ErrUnknownColumn) {
		t.Fatalf("err = %v, want errors.Is(err, ErrUnknownColumn)", err)
	}
	if err := s.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if got := rec.String(); got != "id\n" {
		t.Fatalf("rejected row leaked into output: %q", got)
	}
}

func TestStreamAddAfterCloseFails(t *testing.T) {
	rec := &recorder{}
	s, err := NewStream(rec, []string{"id"}, 2)
	if err != nil {
		t.Fatalf("NewStream: %v", err)
	}
	if err := s.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if err := s.Add(map[string]string{"id": "1"}); err == nil {
		t.Fatal("Add after Close should fail")
	}
	if err := s.Close(); err != nil {
		t.Fatalf("second Close should be a no-op, got %v", err)
	}
}

func TestNewStreamValidatesArguments(t *testing.T) {
	rec := &recorder{}
	if _, err := NewStream(rec, []string{"id"}, 0); err == nil {
		t.Fatal("NewStream accepted flushEvery = 0")
	}
	if _, err := NewStream(rec, nil, 2); err == nil {
		t.Fatal("NewStream accepted an empty column list")
	}
	if _, err := NewStream(rec, []string{"id", "id"}, 2); err == nil {
		t.Fatal("NewStream accepted duplicate columns")
	}
}

func TestSubsetSelectsAndReordersColumns(t *testing.T) {
	r := mustReport(t, "id", "name", "email")
	rows := []map[string]string{
		{"id": "1", "name": "ana", "email": "ana@x"},
		{"id": "2", "name": "bo", "email": "bo@x"},
	}
	for _, row := range rows {
		if err := r.Add(row); err != nil {
			t.Fatalf("Add: %v", err)
		}
	}
	sub, err := r.Subset("email", "id")
	if err != nil {
		t.Fatalf("Subset: %v", err)
	}
	got, err := sub.CSV()
	if err != nil {
		t.Fatalf("CSV: %v", err)
	}
	want := "email,id\nana@x,1\nbo@x,2\n"
	if got != want {
		t.Fatalf("subset CSV = %q, want %q", got, want)
	}
}

func TestSubsetUnknownColumn(t *testing.T) {
	r := mustReport(t, "id", "name")
	_, err := r.Subset("id", "phone")
	if !errors.Is(err, ErrUnknownColumn) {
		t.Fatalf("err = %v, want errors.Is(err, ErrUnknownColumn)", err)
	}
	if !strings.Contains(err.Error(), "phone") {
		t.Fatalf("error %q should name the missing column", err)
	}
}

func TestSubsetIsIndependentOfTheOriginal(t *testing.T) {
	r := mustReport(t, "id", "name")
	if err := r.Add(map[string]string{"id": "1", "name": "ana"}); err != nil {
		t.Fatalf("Add: %v", err)
	}
	sub, err := r.Subset("name")
	if err != nil {
		t.Fatalf("Subset: %v", err)
	}
	if err := sub.Add(map[string]string{"name": "extra"}); err != nil {
		t.Fatalf("Add to subset: %v", err)
	}
	if r.RowCount() != 1 {
		t.Fatalf("original RowCount() = %d after subset Add, want 1", r.RowCount())
	}
	origCSV, err := r.CSV()
	if err != nil {
		t.Fatalf("CSV: %v", err)
	}
	if origCSV != "id,name\n1,ana\n" {
		t.Fatalf("original report changed: %q", origCSV)
	}
	if sub.RowCount() != 2 {
		t.Fatalf("subset RowCount() = %d, want 2", sub.RowCount())
	}
}

func TestSubsetValidatesColumns(t *testing.T) {
	r := mustReport(t, "id", "name")
	if _, err := r.Subset(); err == nil {
		t.Fatal("Subset() with no columns should fail")
	}
	if _, err := r.Subset("id", "id"); err == nil {
		t.Fatal("Subset accepted duplicate columns")
	}
}
