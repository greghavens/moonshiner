// Package flagbind populates configuration structs from command-line
// style arguments. Fields opt in with a `flag:"name"` tag; both
// "--name=value" and "--name value" forms are accepted. It is the
// argument layer of our internal service launchers.
package flagbind

import (
	"errors"
	"fmt"
	"reflect"
	"strconv"
	"strings"
)

// ErrUnknownFlag is wrapped into the error returned when args contain a
// flag that no struct field declares.
var ErrUnknownFlag = errors.New("unknown flag")

// Bind parses args (e.g. ["--host=db1", "--port", "5432"]) into dst,
// which must be a pointer to a struct. Supported field types: string
// and int. Fields without a flag tag (or tagged "-") are ignored, and
// fields whose flag never appears keep their current value.
func Bind(dst any, args []string) error {
	rv := reflect.ValueOf(dst)
	if rv.Kind() != reflect.Pointer || rv.IsNil() || rv.Elem().Kind() != reflect.Struct {
		return fmt.Errorf("flagbind: dst must be a non-nil pointer to struct, got %T", dst)
	}
	fields := fieldMap(rv.Elem())

	i := 0
	for i < len(args) {
		arg := args[i]
		if !strings.HasPrefix(arg, "--") {
			return fmt.Errorf("flagbind: expected a --flag, got %q", arg)
		}
		name, value := arg[2:], ""
		hasValue := false
		if eq := strings.IndexByte(name, '='); eq >= 0 {
			name, value, hasValue = name[:eq], name[eq+1:], true
		}
		fv, ok := fields[name]
		if !ok {
			return fmt.Errorf("flagbind: %w: --%s", ErrUnknownFlag, name)
		}
		if !hasValue {
			i++
			if i >= len(args) {
				return fmt.Errorf("flagbind: flag --%s is missing a value", name)
			}
			value = args[i]
		}
		if err := setField(fv, name, value); err != nil {
			return err
		}
		i++
	}
	return nil
}

// fieldMap collects the settable tagged fields of a struct value,
// keyed by flag name.
func fieldMap(sv reflect.Value) map[string]reflect.Value {
	m := make(map[string]reflect.Value)
	st := sv.Type()
	for i := 0; i < st.NumField(); i++ {
		tag := st.Field(i).Tag.Get("flag")
		if tag == "" || tag == "-" || !sv.Field(i).CanSet() {
			continue
		}
		m[tag] = sv.Field(i)
	}
	return m
}

func setField(fv reflect.Value, name, value string) error {
	switch fv.Kind() {
	case reflect.String:
		fv.SetString(value)
	case reflect.Int:
		n, err := strconv.Atoi(value)
		if err != nil {
			return fmt.Errorf("flagbind: flag --%s: %q is not an integer", name, value)
		}
		fv.SetInt(int64(n))
	default:
		return fmt.Errorf("flagbind: flag --%s: unsupported field type %s", name, fv.Type())
	}
	return nil
}
