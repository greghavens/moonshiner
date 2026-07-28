package csvlimits

import (
	"context"
	"encoding/csv"
	"errors"
	"fmt"
	"io"
)

// Import reads CSV records and offers valid rows to accept.
//
// This implementation performs its resource checks after encoding/csv has
// materialized a complete record. It is retained from the original importer.
func Import(
	ctx context.Context,
	src io.Reader,
	limits Limits,
	accept func(context.Context, []string) error,
	progress func(accepted int),
) (Result, error) {
	var result Result
	if ctx == nil {
		return result, ErrNilContext
	}
	if src == nil {
		return result, ErrNilReader
	}
	if limits.MaxRowBytes <= 0 ||
		limits.MaxFields <= 0 ||
		limits.MaxFieldBytes <= 0 ||
		limits.MaxDiagnostics < 0 {
		return result, fmt.Errorf("%w: all byte and field limits must be positive", ErrInvalidLimits)
	}

	reader := csv.NewReader(src)
	reader.FieldsPerRecord = -1
	reader.ReuseRecord = true

	for record := 1; ; record++ {
		if err := ctx.Err(); err != nil {
			return result, err
		}

		row, err := reader.Read()
		if errors.Is(err, io.EOF) {
			return result, nil
		}
		if err != nil {
			var parseErr *csv.ParseError
			if errors.As(err, &parseErr) {
				return result, &SyntaxError{
					Position: Position{Record: record, Line: parseErr.Line, Column: parseErr.Column},
					Reason:   parseErr.Err.Error(),
				}
			}
			return result, fmt.Errorf("read record %d: %w", record, err)
		}

		var diagnostic *Diagnostic
		rowBytes := len(row) - 1
		for _, field := range row {
			rowBytes += len(field)
		}
		if rowBytes > limits.MaxRowBytes {
			line, column := reader.FieldPos(0)
			diagnostic = &Diagnostic{
				Violation: RowBytes,
				Position:  Position{Record: record, Line: line, Column: column},
			}
		} else if len(row) > limits.MaxFields {
			line, column := reader.FieldPos(limits.MaxFields)
			diagnostic = &Diagnostic{
				Violation: FieldCount,
				Position:  Position{Record: record, Line: line, Column: column},
			}
		} else {
			for i, field := range row {
				if len(field) > limits.MaxFieldBytes {
					line, column := reader.FieldPos(i)
					diagnostic = &Diagnostic{
						Violation: FieldBytes,
						Position:  Position{Record: record, Line: line, Column: column},
					}
					break
				}
			}
		}

		if diagnostic != nil {
			result.Rejected++
			result.Diagnostics = append(result.Diagnostics, *diagnostic)
			continue
		}

		result.Accepted++
		if progress != nil {
			progress(result.Accepted)
		}
		if accept != nil {
			if err := accept(ctx, row); err != nil {
				return result, fmt.Errorf("accept record %d: %w", record, err)
			}
		}
	}
}
