package automation

import "encoding/json"

// Opt is an optional request value.
//
// The zero Opt is *unset*, which is not the same as set-to-the-zero-value. An
// unset Opt must never reach the wire: it is omitted from the query string
// entirely, and omitted from a JSON body entirely, rather than being sent as
// an empty string, a zero, a false or a null. A set Opt is sent even when its
// value is the zero value, because "size=0" and "reason=" are meaningful
// requests that differ from saying nothing at all.
//
// Opt implements IsZero so that an unset Opt is dropped by encoding/json's
// "omitzero" struct tag option.
type Opt[T any] struct {
	value T
	set   bool
}

// Set returns an Opt holding v.
func Set[T any](v T) Opt[T] { return Opt[T]{value: v, set: true} }

// Get returns the value and whether it was set.
func (o Opt[T]) Get() (T, bool) { return o.value, o.set }

// IsSet reports whether a value was set.
func (o Opt[T]) IsSet() bool { return o.set }

// Value returns the value, or the zero value of T if unset.
func (o Opt[T]) Value() T { return o.value }

// IsZero reports whether the Opt is unset. encoding/json consults this for
// fields tagged "omitzero".
func (o Opt[T]) IsZero() bool { return !o.set }

// MarshalJSON encodes the held value. A field holding an unset Opt must be
// tagged "omitzero" so that this is never reached for an unset value; if it
// is, it encodes as null, which is exactly the wire shape to avoid.
func (o Opt[T]) MarshalJSON() ([]byte, error) {
	if !o.set {
		return []byte("null"), nil
	}
	return json.Marshal(o.value)
}

// UnmarshalJSON decodes into the held value and marks the Opt set.
func (o *Opt[T]) UnmarshalJSON(b []byte) error {
	if string(b) == "null" {
		var zero T
		o.value, o.set = zero, false
		return nil
	}
	if err := json.Unmarshal(b, &o.value); err != nil {
		return err
	}
	o.set = true
	return nil
}
