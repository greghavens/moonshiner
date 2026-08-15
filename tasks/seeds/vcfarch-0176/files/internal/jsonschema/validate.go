// Package jsonschema implements the deliberately small JSON Schema vocabulary
// used by the installer specification. It has no network behavior and resolves
// only local JSON Pointers in the supplied schema document.
package jsonschema

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// Decode parses JSON without converting all numbers to float64.
func Decode(data []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var value any
	if err := dec.Decode(&value); err != nil {
		return nil, err
	}
	var trailing any
	if err := dec.Decode(&trailing); err != io.EOF {
		if err == nil {
			return nil, fmt.Errorf("multiple JSON values")
		}
		return nil, err
	}
	return value, nil
}

// Validate returns every deterministic validation error. An empty result means
// the instance conforms to the supplied installer schema.
func Validate(schema, instance any) []error {
	v := validator{root: schema}
	v.check(schema, instance, "$")
	return v.errs
}

type validator struct {
	root any
	errs []error
}

func (v *validator) add(path, format string, args ...any) {
	v.errs = append(v.errs, fmt.Errorf("%s: %s", path, fmt.Sprintf(format, args...)))
}

func (v *validator) check(rawSchema, instance any, path string) {
	schema, ok := rawSchema.(map[string]any)
	if !ok {
		v.add(path, "schema node is not an object")
		return
	}
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolve(v.root, ref)
		if err != nil {
			v.add(path, "invalid schema reference %q: %v", ref, err)
			return
		}
		v.check(resolved, instance, path)
		return
	}
	if want, ok := schema["const"]; ok && !reflect.DeepEqual(want, instance) {
		v.add(path, "must equal %s", compact(want))
		return
	}
	if options, ok := schema["enum"].([]any); ok {
		matched := false
		for _, option := range options {
			if reflect.DeepEqual(option, instance) {
				matched = true
				break
			}
		}
		if !matched {
			v.add(path, "must be one of %s", compact(options))
			return
		}
	}
	if kind, ok := schema["type"].(string); ok && !isType(kind, instance) {
		v.add(path, "must have type %s", kind)
		return
	}

	switch value := instance.(type) {
	case map[string]any:
		v.checkObject(schema, value, path)
	case []any:
		v.checkArray(schema, value, path)
	case string:
		v.checkString(schema, value, path)
	case json.Number:
		v.checkNumber(schema, value, path)
	}
}

func (v *validator) checkObject(schema, object map[string]any, path string) {
	if required, ok := schema["required"].([]any); ok {
		for _, raw := range required {
			name, _ := raw.(string)
			if _, found := object[name]; !found {
				v.add(path, "missing required property %q", name)
			}
		}
	}
	properties, _ := schema["properties"].(map[string]any)
	keys := make([]string, 0, len(object))
	for key := range object {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		propertySchema, known := properties[key]
		if !known {
			if allowed, ok := schema["additionalProperties"].(bool); ok && !allowed {
				v.add(path, "unexpected property %q", key)
			}
			continue
		}
		v.check(propertySchema, object[key], path+"."+key)
	}
}

func (v *validator) checkArray(schema map[string]any, array []any, path string) {
	if minimum, ok := integerKeyword(schema, "minItems"); ok && len(array) < minimum {
		v.add(path, "must contain at least %d items", minimum)
	}
	if maximum, ok := integerKeyword(schema, "maxItems"); ok && len(array) > maximum {
		v.add(path, "must contain at most %d items", maximum)
	}
	if unique, _ := schema["uniqueItems"].(bool); unique {
		seen := map[string]struct{}{}
		for i, item := range array {
			key := compact(item)
			if _, exists := seen[key]; exists {
				v.add(path, "item %d is duplicated", i)
			}
			seen[key] = struct{}{}
		}
	}
	if itemSchema, ok := schema["items"]; ok {
		for i, item := range array {
			v.check(itemSchema, item, fmt.Sprintf("%s[%d]", path, i))
		}
	}
}

func (v *validator) checkString(schema map[string]any, value, path string) {
	if minimum, ok := integerKeyword(schema, "minLength"); ok && len([]rune(value)) < minimum {
		v.add(path, "must contain at least %d characters", minimum)
	}
	if pattern, ok := schema["pattern"].(string); ok {
		re, err := regexp.Compile(pattern)
		if err != nil {
			v.add(path, "schema has invalid pattern %q", pattern)
		} else if !re.MatchString(value) {
			v.add(path, "must match %q", pattern)
		}
	}
}

func (v *validator) checkNumber(schema map[string]any, value json.Number, path string) {
	minimum, ok := schema["minimum"].(json.Number)
	if !ok {
		return
	}
	got, gotErr := value.Float64()
	want, wantErr := minimum.Float64()
	if gotErr != nil || wantErr != nil || got < want {
		v.add(path, "must be at least %s", minimum)
	}
}

func isType(kind string, value any) bool {
	switch kind {
	case "object":
		_, ok := value.(map[string]any)
		return ok
	case "array":
		_, ok := value.([]any)
		return ok
	case "string":
		_, ok := value.(string)
		return ok
	case "boolean":
		_, ok := value.(bool)
		return ok
	case "integer":
		n, ok := value.(json.Number)
		if !ok {
			return false
		}
		_, err := strconv.ParseInt(n.String(), 10, 64)
		return err == nil
	case "number":
		_, ok := value.(json.Number)
		return ok
	default:
		return false
	}
}

func integerKeyword(schema map[string]any, key string) (int, bool) {
	number, ok := schema[key].(json.Number)
	if !ok {
		return 0, false
	}
	parsed, err := strconv.Atoi(number.String())
	return parsed, err == nil
}

func resolve(root any, ref string) (any, error) {
	if ref == "#" {
		return root, nil
	}
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("only local references are supported")
	}
	current := root
	for _, encoded := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		part := strings.ReplaceAll(strings.ReplaceAll(encoded, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%q is not an object", part)
		}
		current, ok = object[part]
		if !ok {
			return nil, fmt.Errorf("pointer segment %q does not exist", part)
		}
	}
	return current, nil
}

func compact(value any) string {
	encoded, err := json.Marshal(value)
	if err != nil {
		return fmt.Sprint(value)
	}
	return string(encoded)
}
