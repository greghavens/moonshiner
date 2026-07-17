package auditsum

import (
	"errors"
	"strings"
	"testing"
)

const ceiling = 100000

func TestAggregatesMixedLevels(t *testing.T) {
	log := "INFO service started\nWARN cache cold\n\nINFO ready\nERROR upstream 502\n"
	sum, err := Summarize(strings.NewReader(log), ceiling)
	if err != nil {
		t.Fatalf("Summarize: %v", err)
	}
	if sum.Records != 4 {
		t.Fatalf("Records = %d, want 4", sum.Records)
	}
	if sum.ByLevel["INFO"] != 2 || sum.ByLevel["WARN"] != 1 || sum.ByLevel["ERROR"] != 1 {
		t.Fatalf("ByLevel = %v, want INFO:2 WARN:1 ERROR:1", sum.ByLevel)
	}
}

func TestLargeValidRecordIsCounted(t *testing.T) {
	blob := "INFO payload " + strings.Repeat("x", 80000)
	log := "INFO before\n" + blob + "\nINFO after\n"
	sum, err := Summarize(strings.NewReader(log), ceiling)
	if err != nil {
		t.Fatalf("a %d-byte record is under the %d-byte ceiling and must be accepted: %v", len(blob), ceiling, err)
	}
	if sum.Records != 3 {
		t.Fatalf("Records = %d, want 3 — part of the stream silently vanished from the aggregate", sum.Records)
	}
	if want := len("INFO before") + len(blob) + len("INFO after"); sum.Bytes != want {
		t.Fatalf("Bytes = %d, want %d", sum.Bytes, want)
	}
}

func TestExactCeilingRecordIsAccepted(t *testing.T) {
	rec := "INFO " + strings.Repeat("y", ceiling-5)
	if len(rec) != ceiling {
		t.Fatalf("fixture bug: record is %d bytes, want exactly %d", len(rec), ceiling)
	}
	log := rec + "\nINFO tail\n"
	sum, err := Summarize(strings.NewReader(log), ceiling)
	if err != nil {
		t.Fatalf("a record of exactly the configured maximum must be accepted: %v", err)
	}
	if sum.Records != 2 {
		t.Fatalf("Records = %d, want 2", sum.Records)
	}
	if want := ceiling + len("INFO tail"); sum.Bytes != want {
		t.Fatalf("Bytes = %d, want %d", sum.Bytes, want)
	}
}

func TestOversizeRecordRejectsTheWholeSummary(t *testing.T) {
	over := "INFO " + strings.Repeat("z", ceiling-4)
	if len(over) != ceiling+1 {
		t.Fatalf("fixture bug: record is %d bytes, want %d", len(over), ceiling+1)
	}
	log := "INFO a\nWARN b\nINFO c\n" + over + "\nINFO d\n"
	sum, err := Summarize(strings.NewReader(log), ceiling)
	if err == nil {
		t.Fatalf("oversize record accepted; summary = %+v", sum)
	}
	var tooLong *RecordTooLongError
	if !errors.As(err, &tooLong) {
		t.Fatalf("error = %v (%T), want a RecordTooLongError diagnosing the record", err, err)
	}
	if tooLong.Record != 4 {
		t.Fatalf("diagnostic names record %d, want 4", tooLong.Record)
	}
	if tooLong.Limit != ceiling {
		t.Fatalf("diagnostic names limit %d, want %d", tooLong.Limit, ceiling)
	}
	if sum.Records != 0 || sum.Bytes != 0 || len(sum.ByLevel) != 0 {
		t.Fatalf("rejected stream still produced a partial aggregate: %+v", sum)
	}
}

func TestFinalRecordWithoutNewlineIsCounted(t *testing.T) {
	log := "INFO a\nERROR final line has no newline"
	sum, err := Summarize(strings.NewReader(log), ceiling)
	if err != nil {
		t.Fatalf("Summarize: %v", err)
	}
	if sum.Records != 2 {
		t.Fatalf("Records = %d, want 2 — the unterminated final record was dropped", sum.Records)
	}
	if sum.ByLevel["ERROR"] != 1 {
		t.Fatalf("ByLevel = %v, want the final ERROR record counted", sum.ByLevel)
	}
}

func TestSmallCeilingDiagnosesRecordNumber(t *testing.T) {
	log := "INFO ok\nINFO " + strings.Repeat("q", 60) + "\n"
	sum, err := Summarize(strings.NewReader(log), 50)
	var tooLong *RecordTooLongError
	if !errors.As(err, &tooLong) {
		t.Fatalf("error = %v (%T), want a RecordTooLongError for a 65-byte record over a 50-byte ceiling", err, err)
	}
	if tooLong.Record != 2 || tooLong.Limit != 50 {
		t.Fatalf("diagnostic = %+v, want record 2 limit 50", tooLong)
	}
	if sum.Records != 0 {
		t.Fatalf("rejected stream still produced a partial aggregate: %+v", sum)
	}
}
