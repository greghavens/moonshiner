package planttags

// A Tag is one nursery pot label: what the plant is, the sun code the
// benches are sorted by (FS, PS, SH), and the sticker price.
type Tag struct {
	Common  string
	Latin   string
	SunCode string
	Price   float64
}

// TagWriter renders a single tag for one kind of output device.
type TagWriter interface {
	WriteTag(t Tag) (string, error)
}

// Writers is the device registry the tag CLI picks from by name.
func Writers() map[string]TagWriter {
	return map[string]TagWriter{
		"text":  TextWriter{},
		"csv":   CSVWriter{Sep: ","},
		"badge": BadgeWriter{},
	}
}
