// Package attribute contains the small value types used by this exercise's
// OpenTelemetry API fixture.
package attribute

// Key identifies a span attribute.
type Key string

// Value is an attribute value. HTTP semantic conventions in this fixture only
// need strings and integers.
type Value struct {
	kind valueKind
	text string
	number int64
}

type valueKind uint8

const (
	invalidValue valueKind = iota
	stringValue
	intValue
)

// KeyValue associates a value with its semantic-convention key.
type KeyValue struct {
	Key   Key
	Value Value
}

// String constructs a string-valued attribute.
func (k Key) String(value string) KeyValue {
	return KeyValue{Key: k, Value: Value{kind: stringValue, text: value}}
}

// Int constructs an integer-valued attribute.
func (k Key) Int(value int) KeyValue {
	return KeyValue{Key: k, Value: Value{kind: intValue, number: int64(value)}}
}

// AsString returns a string value. It returns an empty string for other kinds.
func (v Value) AsString() string {
	if v.kind != stringValue {
		return ""
	}
	return v.text
}

// AsInt64 returns an integer value. It returns zero for other kinds.
func (v Value) AsInt64() int64 {
	if v.kind != intValue {
		return 0
	}
	return v.number
}

// IsString reports whether the value contains a string.
func (v Value) IsString() bool { return v.kind == stringValue }

// IsInt reports whether the value contains an integer.
func (v Value) IsInt() bool { return v.kind == intValue }
