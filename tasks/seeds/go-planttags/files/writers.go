package planttags

import (
	"fmt"
	"strings"
)

// TextWriter renders the human-readable bench tag.
type TextWriter struct{}

func (w TextWriter) Format(t Tag) (string, error) {
	if t.Common == "" {
		return "", fmt.Errorf("tag has no common name")
	}
	return fmt.Sprintf("%s (%s) [%s] $%.2f", t.Common, t.Latin, t.SunCode, t.Price), nil
}

// CSVWriter renders one row per tag for the label vendor's importer.
type CSVWriter struct {
	Sep string
}

func (w CSVWriter) Format(t Tag) (string, error) {
	if strings.Contains(t.Common, w.Sep) || strings.Contains(t.Latin, w.Sep) {
		return "", fmt.Errorf("tag field contains the separator %q", w.Sep)
	}
	return strings.Join([]string{
		t.Common, t.Latin, t.SunCode, fmt.Sprintf("%.2f", t.Price),
	}, w.Sep), nil
}

// BadgeWriter drives the thermal badge printer; it counts what it has
// printed so the roll can be reconciled at close.
type BadgeWriter struct {
	Printed int
}

func (w *BadgeWriter) WriteTag(t Tag) (string, error) {
	if t.SunCode == "" {
		return "", fmt.Errorf("badge layout needs a sun code")
	}
	w.Printed++
	return fmt.Sprintf("%s|%s|%.2f", strings.ToUpper(t.Common), t.SunCode, t.Price), nil
}
