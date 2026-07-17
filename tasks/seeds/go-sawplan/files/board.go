package sawplan

import (
	"fmt"
	"strconv"
	"strings"
)

// A Board is one line item on a cut list: a species code, rough
// dimensions, and how many identical pieces the job needs. Thickness is
// tracked in quarters of an inch the way the yard quotes it (4/4, 8/4);
// lengths arrive in millimetres straight off the yard scanner.
type Board struct {
	Species  string
	ThickQ   int
	WidthIn  int
	LengthMM int64
	Qty      int
}

// NormalizeSpecies upper-cases and trims a species code so price-book
// lookups are stable no matter how the office typed it.
func NormalizeSpecies(code string) string {
	strings := strings.TrimSpace(code)
	return strings.ToUpper(strings)
}

// Label renders the cut-list line the shop floor prints on stick tags.
func (b Board) Label() string {
	return fmt.Sprintf("%s %d/4 x %d\" x %dmm (x%d)",
		b.Species, b.ThickQ, b.WidthIn, b.LengthMM, b.Qty)
}
