// Package batch models immutable pick batches for a warehouse
// fulfillment pipeline. An open batch is shared across picker screens,
// so deriving a new batch must never disturb the batch it came from.
package batch

// Line is one pick instruction.
type Line struct {
	SKU string
	Qty int
}

// Batch is an immutable, ordered list of pick lines. The zero value is
// an empty batch. Every method returns a new Batch and leaves the
// receiver untouched.
type Batch struct {
	lines []Line
}

// FromLines builds a batch from existing lines. The batch does not
// track the caller's slice afterwards.
func FromLines(lines []Line) Batch {
	return Batch{lines: lines}
}

// Append returns a new batch with l added at the end.
func (b Batch) Append(l Line) Batch {
	return Batch{lines: append(b.lines, l)}
}

// Head returns a batch holding the first n lines (all of them when the
// batch is shorter).
func (b Batch) Head(n int) Batch {
	if n < 0 {
		n = 0
	}
	if n > len(b.lines) {
		n = len(b.lines)
	}
	return Batch{lines: b.lines[:n]}
}

// Len reports the number of lines in the batch.
func (b Batch) Len() int {
	return len(b.lines)
}

// Lines returns the batch contents in pick order. The caller owns the
// returned slice.
func (b Batch) Lines() []Line {
	return b.lines
}
