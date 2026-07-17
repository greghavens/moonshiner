package planttags

import "fmt"

// PrintRun renders every tag with the writer registered under kind,
// stopping at the first tag the device rejects.
func PrintRun(kind string, tags []Tag) ([]string, error) {
	w, ok := Writers()[kind]
	if !ok {
		return nil, fmt.Errorf("no writer registered for %q", kind)
	}
	out := make([]string, 0, len(tags))
	for _, t := range tags {
		s, err := w.WriteTag(t)
		if err != nil {
			return nil, fmt.Errorf("tag %q: %w", t.Common, err)
		}
		out = append(out, s)
	}
	return out, nil
}

// SampleSheet renders the calibration tag on the devices the morning
// checklist exercises, in checklist order.
func SampleSheet() ([]string, error) {
	calibration := Tag{Common: "Rosemary", Latin: "Salvia rosmarinus", SunCode: "FS", Price: 6.5}
	devices := []TagWriter{TextWriter{}, &BadgeWriter{}}
	out := make([]string, 0, len(devices))
	for _, d := range devices {
		s, err := d.WriteTag(calibration)
		if err != nil {
			return nil, err
		}
		out = append(out, s)
	}
	return out, nil
}

// QuickTag is the one-liner helper the potting-shed scripts use for a
// single bench tag; bad tags come back as an empty string.
func QuickTag(t Tag) string {
	s, err := TextWriter{}.Format(t)
	if err != nil {
		return ""
	}
	return s
}
