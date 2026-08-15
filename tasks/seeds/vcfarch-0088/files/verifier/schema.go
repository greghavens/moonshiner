package verifier

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

func decodeJSON(data []byte) (any, error) {
	decoder := json.NewDecoder(strings.NewReader(string(data)))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		if err == nil {
			return nil, fmt.Errorf("trailing JSON value")
		}
		return nil, fmt.Errorf("trailing JSON: %w", err)
	}
	return value, nil
}

func validateSchema(root, schema any, value any, path string) error {
	s, ok := schema.(map[string]any)
	if !ok {
		return fmt.Errorf("%s: schema is not an object", path)
	}

	if ref, ok := s["$ref"].(string); ok {
		resolved, err := resolvePointer(root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		return validateSchema(root, resolved, value, path)
	}

	if nullable, _ := s["nullable"].(bool); nullable && value == nil {
		return nil
	}
	if constant, ok := s["const"]; ok && !jsonEqual(constant, value) {
		return fmt.Errorf("%s: value does not equal const", path)
	}
	if enum, ok := s["enum"].([]any); ok {
		matched := false
		for _, candidate := range enum {
			if jsonEqual(candidate, value) {
				matched = true
				break
			}
		}
		if !matched {
			return fmt.Errorf("%s: value is not in enum", path)
		}
	}
	if all, ok := s["allOf"].([]any); ok {
		for _, child := range all {
			if err := validateSchema(root, child, value, path); err != nil {
				return err
			}
		}
	}
	if anySchemas, ok := s["anyOf"].([]any); ok {
		matches := 0
		for _, child := range anySchemas {
			if validateSchema(root, child, value, path) == nil {
				matches++
			}
		}
		if matches == 0 {
			return fmt.Errorf("%s: no anyOf schema matched", path)
		}
	}
	if oneSchemas, ok := s["oneOf"].([]any); ok {
		matches := 0
		for _, child := range oneSchemas {
			if validateSchema(root, child, value, path) == nil {
				matches++
			}
		}
		if matches != 1 {
			return fmt.Errorf("%s: expected one oneOf match, got %d", path, matches)
		}
	}

	typeName, _ := s["type"].(string)
	switch typeName {
	case "object":
		object, ok := value.(map[string]any)
		if !ok {
			return fmt.Errorf("%s: expected object", path)
		}
		if required, ok := s["required"].([]any); ok {
			for _, item := range required {
				name, _ := item.(string)
				if _, exists := object[name]; !exists {
					return fmt.Errorf("%s: missing required property %q", path, name)
				}
			}
		}
		properties, _ := s["properties"].(map[string]any)
		for name, child := range properties {
			if childValue, exists := object[name]; exists {
				if err := validateSchema(root, child, childValue, path+"."+name); err != nil {
					return err
				}
			}
		}
		if additional, exists := s["additionalProperties"]; exists {
			if allowed, ok := additional.(bool); ok && !allowed {
				for name := range object {
					if _, known := properties[name]; !known {
						return fmt.Errorf("%s: additional property %q is not allowed", path, name)
					}
				}
			}
		}
	case "array":
		array, ok := value.([]any)
		if !ok {
			return fmt.Errorf("%s: expected array", path)
		}
		if minimum, ok := integerKeyword(s, "minItems"); ok && len(array) < minimum {
			return fmt.Errorf("%s: expected at least %d items", path, minimum)
		}
		if maximum, ok := integerKeyword(s, "maxItems"); ok && len(array) > maximum {
			return fmt.Errorf("%s: expected at most %d items", path, maximum)
		}
		if unique, _ := s["uniqueItems"].(bool); unique {
			for i := range array {
				for j := 0; j < i; j++ {
					if jsonEqual(array[i], array[j]) {
						return fmt.Errorf("%s: duplicate array item", path)
					}
				}
			}
		}
		if items, exists := s["items"]; exists {
			for i, item := range array {
				if err := validateSchema(root, items, item, fmt.Sprintf("%s[%d]", path, i)); err != nil {
					return err
				}
			}
		}
	case "string":
		str, ok := value.(string)
		if !ok {
			return fmt.Errorf("%s: expected string", path)
		}
		length := utf8.RuneCountInString(str)
		if minimum, ok := integerKeyword(s, "minLength"); ok && length < minimum {
			return fmt.Errorf("%s: string shorter than %d", path, minimum)
		}
		if maximum, ok := integerKeyword(s, "maxLength"); ok && length > maximum {
			return fmt.Errorf("%s: string longer than %d", path, maximum)
		}
		if pattern, ok := s["pattern"].(string); ok {
			re, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: invalid schema pattern: %w", path, err)
			}
			if !re.MatchString(str) {
				return fmt.Errorf("%s: string does not match %q", path, pattern)
			}
		}
	case "integer":
		number, ok := value.(json.Number)
		if !ok {
			return fmt.Errorf("%s: expected integer", path)
		}
		integer, err := strconv.ParseInt(string(number), 10, 64)
		if err != nil {
			return fmt.Errorf("%s: expected integer", path)
		}
		if err := validateNumberBounds(s, float64(integer), path); err != nil {
			return err
		}
	case "number":
		number, ok := value.(json.Number)
		if !ok {
			return fmt.Errorf("%s: expected number", path)
		}
		parsed, err := number.Float64()
		if err != nil || math.IsInf(parsed, 0) || math.IsNaN(parsed) {
			return fmt.Errorf("%s: invalid number", path)
		}
		if err := validateNumberBounds(s, parsed, path); err != nil {
			return err
		}
	case "boolean":
		if _, ok := value.(bool); !ok {
			return fmt.Errorf("%s: expected boolean", path)
		}
	}
	return nil
}

func validateNumberBounds(schema map[string]any, value float64, path string) error {
	if minimum, ok := numberKeyword(schema, "minimum"); ok && value < minimum {
		return fmt.Errorf("%s: number is below minimum %v", path, minimum)
	}
	if maximum, ok := numberKeyword(schema, "maximum"); ok && value > maximum {
		return fmt.Errorf("%s: number is above maximum %v", path, maximum)
	}
	return nil
}

func resolvePointer(root any, ref string) (any, error) {
	if ref == "#" {
		return root, nil
	}
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("external schema reference %q is not permitted", ref)
	}
	current := root
	for _, raw := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		part := strings.ReplaceAll(strings.ReplaceAll(raw, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("schema reference %q traverses a non-object", ref)
		}
		current, ok = object[part]
		if !ok {
			return nil, fmt.Errorf("schema reference %q does not exist", ref)
		}
	}
	return current, nil
}

func integerKeyword(schema map[string]any, key string) (int, bool) {
	value, ok := schema[key].(json.Number)
	if !ok {
		return 0, false
	}
	parsed, err := strconv.Atoi(string(value))
	return parsed, err == nil
}

func numberKeyword(schema map[string]any, key string) (float64, bool) {
	value, ok := schema[key].(json.Number)
	if !ok {
		return 0, false
	}
	parsed, err := value.Float64()
	return parsed, err == nil
}

func jsonEqual(left, right any) bool {
	return reflect.DeepEqual(normalizeNumber(left), normalizeNumber(right))
}

func normalizeNumber(value any) any {
	if number, ok := value.(json.Number); ok {
		if integer, err := number.Int64(); err == nil {
			return integer
		}
		if decimal, err := number.Float64(); err == nil {
			return decimal
		}
	}
	return value
}
