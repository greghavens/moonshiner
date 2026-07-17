// Package report builds the CSV exports served by the admin dashboard
// (billing summaries, audit extracts, usage rollups). Rows arrive as
// maps from arbitrary query code; the report pins column order and
// keeps the output stable.
package report

import (
	"bytes"
	"encoding/csv"
	"fmt"
)

// Report accumulates rows and renders them as CSV with a header row.
type Report struct {
	columns []string
	index   map[string]int
	rows    [][]string
}

// New returns a report with the given column order. Column names must
// be non-empty and unique.
func New(columns ...string) (*Report, error) {
	if len(columns) == 0 {
		return nil, fmt.Errorf("report: at least one column is required")
	}
	index := make(map[string]int, len(columns))
	for i, c := range columns {
		if c == "" {
			return nil, fmt.Errorf("report: column %d has an empty name", i)
		}
		if _, dup := index[c]; dup {
			return nil, fmt.Errorf("report: duplicate column %q", c)
		}
		index[c] = i
	}
	return &Report{
		columns: append([]string(nil), columns...),
		index:   index,
	}, nil
}

// Columns returns the column order.
func (r *Report) Columns() []string {
	return append([]string(nil), r.columns...)
}

// Add appends one row. Every key must be a declared column; columns
// missing from the map become empty cells.
func (r *Report) Add(row map[string]string) error {
	cells := make([]string, len(r.columns))
	for k, v := range row {
		i, ok := r.index[k]
		if !ok {
			return fmt.Errorf("report: unknown column %q", k)
		}
		cells[i] = v
	}
	r.rows = append(r.rows, cells)
	return nil
}

// RowCount reports how many rows have been added.
func (r *Report) RowCount() int { return len(r.rows) }

// CSV renders the header and all rows using standard CSV quoting.
func (r *Report) CSV() (string, error) {
	var buf bytes.Buffer
	w := csv.NewWriter(&buf)
	if err := w.Write(r.columns); err != nil {
		return "", err
	}
	for _, row := range r.rows {
		if err := w.Write(row); err != nil {
			return "", err
		}
	}
	w.Flush()
	if err := w.Error(); err != nil {
		return "", err
	}
	return buf.String(), nil
}
