package csvlimits

import (
	"errors"
	"fmt"
)

// Limits are hard per-record resource limits. MaxDiagnostics bounds retained
// diagnostics; the other limits must be positive.
type Limits struct {
	MaxRowBytes    int
	MaxFields      int
	MaxFieldBytes  int
	MaxDiagnostics int
}

// Violation identifies the resource budget that first rejected a record.
type Violation string

const (
	RowBytes   Violation = "row_bytes"
	FieldCount Violation = "field_count"
	FieldBytes Violation = "field_bytes"
)

// Position is a one-based logical record and physical source location.
// Column counts bytes, not runes.
type Position struct {
	Record int
	Line   int
	Column int
}

// Diagnostic describes the first resource violation in a rejected record.
type Diagnostic struct {
	Violation Violation
	Position  Position
}

// Result summarizes records fully processed before Import returned.
type Result struct {
	Accepted    int
	Rejected    int
	Diagnostics []Diagnostic
	Suppressed  int
}

var (
	ErrInvalidLimits = errors.New("invalid CSV import limits")
	ErrNilContext    = errors.New("nil context")
	ErrNilReader     = errors.New("nil reader")
)

// SyntaxError reports malformed CSV at a stable source position.
type SyntaxError struct {
	Position Position
	Reason   string
}

func (e *SyntaxError) Error() string {
	return fmt.Sprintf(
		"csv syntax error at record %d, line %d, column %d: %s",
		e.Position.Record,
		e.Position.Line,
		e.Position.Column,
		e.Reason,
	)
}
