// Package jsonschema validates the small, standards-based JSON Schema subset used
// by the installer specification. It deliberately has no network dependencies.
package jsonschema

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// Validator is a compiled local JSON Schema document.
type Validator struct {
	root map[string]any
}

// Compile parses a JSON Schema. Remote references are intentionally unsupported;
// the installer specification uses only local references.
func Compile(schema []byte) (*Validator, error) {
	value, err := decodeOne(schema)
	if err != nil {
		return nil, fmt.Errorf("parse schema: %w", err)
	}
	root, ok := value.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("schema root must be an object")
	}
	return &Validator{root: root}, nil
}

// Validate parses and validates exactly one JSON document.
func (v *Validator) Validate(document []byte) error {
	value, err := decodeOne(document)
	if err != nil {
		return fmt.Errorf("$: invalid JSON: %w", err)
	}
	return v.validate(v.root, value, "$")
}

func decodeOne(data []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var value any
	if err := dec.Decode(&value); err != nil {
		return nil, err
	}
	var extra any
	if err := dec.Decode(&extra); err == nil {
		return nil, fmt.Errorf("multiple JSON values")
	} else if err != io.EOF {
		return nil, err
	}
	return value, nil
}

func (v *Validator) validate(schema map[string]any, value any, path string) error {
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := v.resolve(ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		return v.validate(resolved, value, path)
	}

	if expected, ok := schema["const"]; ok && !equalJSON(expected, value) {
		return fmt.Errorf("%s: must equal %v", path, expected)
	}
	if values, ok := schema["enum"].([]any); ok {
		matched := false
		for _, candidate := range values {
			matched = matched || equalJSON(candidate, value)
		}
		if !matched {
			return fmt.Errorf("%s: value is not in enum", path)
		}
	}

	if kind, ok := schema["type"].(string); ok {
		if err := checkType(kind, value); err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
	}

	switch typed := value.(type) {
	case map[string]any:
		return v.validateObject(schema, typed, path)
	case []any:
		return v.validateArray(schema, typed, path)
	case string:
		return validateString(schema, typed, path)
	case json.Number:
		return validateNumber(schema, typed, path)
	default:
		return nil
	}
}

func (v *Validator) validateObject(schema map[string]any, value map[string]any, path string) error {
	if raw, ok := schema["required"].([]any); ok {
		for _, item := range raw {
			name, ok := item.(string)
			if !ok {
				return fmt.Errorf("%s: schema has non-string required property", path)
			}
			if _, exists := value[name]; !exists {
				return fmt.Errorf("%s: missing required property %q", path, name)
			}
		}
	}

	properties, _ := schema["properties"].(map[string]any)
	for name, childValue := range value {
		rawChild, known := properties[name]
		if !known {
			if additional, present := schema["additionalProperties"].(bool); present && !additional {
				return fmt.Errorf("%s: additional property %q is not allowed", path, name)
			}
			continue
		}
		childSchema, ok := rawChild.(map[string]any)
		if !ok {
			return fmt.Errorf("%s: schema property %q is not an object", path, name)
		}
		if err := v.validate(childSchema, childValue, childPath(path, name)); err != nil {
			return err
		}
	}
	return nil
}

func (v *Validator) validateArray(schema map[string]any, value []any, path string) error {
	if minimum, ok := integerKeyword(schema, "minItems"); ok && int64(len(value)) < minimum {
		return fmt.Errorf("%s: must contain at least %d items", path, minimum)
	}
	if unique, _ := schema["uniqueItems"].(bool); unique {
		for i := range value {
			for j := 0; j < i; j++ {
				if equalJSON(value[i], value[j]) {
					return fmt.Errorf("%s: items %d and %d are duplicates", path, j, i)
				}
			}
		}
	}
	itemSchema, _ := schema["items"].(map[string]any)
	for i, item := range value {
		if itemSchema != nil {
			if err := v.validate(itemSchema, item, fmt.Sprintf("%s[%d]", path, i)); err != nil {
				return err
			}
		}
	}
	return nil
}

func validateString(schema map[string]any, value, path string) error {
	if minimum, ok := integerKeyword(schema, "minLength"); ok && int64(len([]rune(value))) < minimum {
		return fmt.Errorf("%s: string must contain at least %d characters", path, minimum)
	}
	switch format, _ := schema["format"].(string); format {
	case "date":
		parsed, err := time.Parse("2006-01-02", value)
		if err != nil || parsed.Format("2006-01-02") != value {
			return fmt.Errorf("%s: %q is not an RFC 3339 full-date", path, value)
		}
	case "uri":
		parsed, err := url.ParseRequestURI(value)
		if err != nil || parsed.Scheme == "" || parsed.Host == "" {
			return fmt.Errorf("%s: %q is not an absolute URI", path, value)
		}
	}
	return nil
}

func validateNumber(schema map[string]any, value json.Number, path string) error {
	if minimum, ok := numberKeyword(schema, "minimum"); ok {
		actual, err := strconv.ParseFloat(string(value), 64)
		if err != nil || actual < minimum {
			return fmt.Errorf("%s: number must be at least %v", path, minimum)
		}
	}
	return nil
}

func checkType(expected string, value any) error {
	valid := false
	switch expected {
	case "object":
		_, valid = value.(map[string]any)
	case "array":
		_, valid = value.([]any)
	case "string":
		_, valid = value.(string)
	case "integer":
		number, ok := value.(json.Number)
		if ok {
			_, err := strconv.ParseInt(string(number), 10, 64)
			valid = err == nil
		}
	case "number":
		_, valid = value.(json.Number)
	case "boolean":
		_, valid = value.(bool)
	case "null":
		valid = value == nil
	default:
		return fmt.Errorf("unsupported schema type %q", expected)
	}
	if !valid {
		return fmt.Errorf("expected %s", expected)
	}
	return nil
}

func (v *Validator) resolve(ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("only local JSON Pointer references are supported: %q", ref)
	}
	var current any = v.root
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("reference %q traverses a non-object", ref)
		}
		current, ok = object[token]
		if !ok {
			return nil, fmt.Errorf("reference %q does not exist", ref)
		}
	}
	resolved, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("reference %q does not identify a schema object", ref)
	}
	return resolved, nil
}

func childPath(parent, name string) string {
	if strings.ContainsAny(name, ".[]") {
		return fmt.Sprintf("%s[%q]", parent, name)
	}
	return parent + "." + name
}

func integerKeyword(schema map[string]any, name string) (int64, bool) {
	number, ok := schema[name].(json.Number)
	if !ok {
		return 0, false
	}
	value, err := strconv.ParseInt(string(number), 10, 64)
	return value, err == nil
}

func numberKeyword(schema map[string]any, name string) (float64, bool) {
	number, ok := schema[name].(json.Number)
	if !ok {
		return 0, false
	}
	value, err := strconv.ParseFloat(string(number), 64)
	return value, err == nil
}

func equalJSON(left, right any) bool {
	leftBytes, leftErr := json.Marshal(left)
	rightBytes, rightErr := json.Marshal(right)
	return leftErr == nil && rightErr == nil && bytes.Equal(leftBytes, rightBytes)
}
