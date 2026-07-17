// Package auditsum aggregates newline-delimited audit records for the
// nightly compliance report.
package auditsum

import (
	"bufio"
	"fmt"
	"io"
	"strings"
)

// Summary aggregates one audit stream. It is all-or-nothing: when
// Summarize returns an error the summary is the zero value, never a
// partial aggregate.
type Summary struct {
	Records int
	Bytes   int
	ByLevel map[string]int
}

// RecordTooLongError reports a record above the configured ceiling.
type RecordTooLongError struct {
	Record int // 1-based record number in the stream
	Limit  int // configured ceiling in bytes
}

func (e *RecordTooLongError) Error() string {
	return fmt.Sprintf("record %d exceeds the %d-byte record limit", e.Record, e.Limit)
}

// Summarize aggregates the records in r. Records are newline
// delimited; the final record may lack a trailing newline. maxRecord
// is the configured per-record byte ceiling: any valid record up to
// exactly that length must be counted, and a longer one rejects the
// whole stream with a RecordTooLongError naming it.
func Summarize(r io.Reader, maxRecord int) (Summary, error) {
	sum := Summary{ByLevel: make(map[string]int)}
	sc := bufio.NewScanner(r)
	n := 0
	for sc.Scan() {
		line := sc.Text()
		n++
		if len(line) > maxRecord {
			return Summary{}, &RecordTooLongError{Record: n, Limit: maxRecord}
		}
		if line == "" {
			continue // blank separator lines are legal and not records
		}
		level, _, _ := strings.Cut(line, " ")
		sum.Records++
		sum.Bytes += len(line)
		sum.ByLevel[level]++
	}
	return sum, nil
}
