package grader

import (
	"encoding/json"
	"fmt"
	"math"
	"reflect"
	"regexp"
	"strings"
)

// schemaValidator implements the JSON Schema vocabulary used by the pinned
// OpenAPI 3.0 document and the migration-plan schema. It deliberately resolves
// schemas from those documents at runtime instead of restating SddcSpec.
type schemaValidator struct {
	document map[string]any
}

func (v schemaValidator) validate(schema, value any, path string) error {
	s, ok := schema.(map[string]any)
	if !ok {
		return fmt.Errorf("%s: schema is not an object", path)
	}
	if ref, ok := s["$ref"].(string); ok {
		resolved, err := v.resolve(ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		return v.validate(resolved, value, path)
	}
	if nullable, _ := s["nullable"].(bool); nullable && value == nil {
		return nil
	}
	if expected, exists := s["const"]; exists && !reflect.DeepEqual(expected, value) {
		return fmt.Errorf("%s: got %v, want constant %v", path, value, expected)
	}
	if choices, ok := s["enum"].([]any); ok {
		matched := false
		for _, choice := range choices {
			matched = matched || reflect.DeepEqual(choice, value)
		}
		if !matched {
			return fmt.Errorf("%s: value is not in enum", path)
		}
	}
	for _, raw := range arrayKeyword(s, "allOf") {
		if err := v.validate(raw, value, path); err != nil {
			return err
		}
	}
	if options := arrayKeyword(s, "anyOf"); len(options) > 0 {
		matches := 0
		for _, option := range options {
			if v.validate(option, value, path) == nil {
				matches++
			}
		}
		if matches == 0 {
			return fmt.Errorf("%s: no anyOf schema matched", path)
		}
	}
	if options := arrayKeyword(s, "oneOf"); len(options) > 0 {
		matches := 0
		for _, option := range options {
			if v.validate(option, value, path) == nil {
				matches++
			}
		}
		if matches != 1 {
			return fmt.Errorf("%s: %d oneOf schemas matched", path, matches)
		}
	}
	if raw, ok := s["not"]; ok && v.validate(raw, value, path) == nil {
		return fmt.Errorf("%s: prohibited schema matched", path)
	}

	switch s["type"] {
	case "object":
		object, ok := value.(map[string]any)
		if !ok {
			return fmt.Errorf("%s: expected object, got %T", path, value)
		}
		for _, required := range stringArray(s["required"]) {
			if _, present := object[required]; !present {
				return fmt.Errorf("%s: missing required property %q", path, required)
			}
		}
		properties, _ := s["properties"].(map[string]any)
		for name, child := range object {
			propertySchema, declared := properties[name]
			if declared {
				if err := v.validate(propertySchema, child, path+"."+name); err != nil {
					return err
				}
				continue
			}
			switch additional := s["additionalProperties"].(type) {
			case bool:
				if !additional {
					return fmt.Errorf("%s: additional property %q", path, name)
				}
			case map[string]any:
				if err := v.validate(additional, child, path+"."+name); err != nil {
					return err
				}
			}
		}
	case "array":
		array, ok := value.([]any)
		if !ok {
			return fmt.Errorf("%s: expected array, got %T", path, value)
		}
		if minimum, ok := number(s["minItems"]); ok && float64(len(array)) < minimum {
			return fmt.Errorf("%s: fewer than %v items", path, minimum)
		}
		if maximum, ok := number(s["maxItems"]); ok && float64(len(array)) > maximum {
			return fmt.Errorf("%s: more than %v items", path, maximum)
		}
		if unique, _ := s["uniqueItems"].(bool); unique {
			for i := range array {
				for j := 0; j < i; j++ {
					if reflect.DeepEqual(array[i], array[j]) {
						return fmt.Errorf("%s: duplicate items", path)
					}
				}
			}
		}
		if itemSchema, present := s["items"]; present {
			for i, item := range array {
				if err := v.validate(itemSchema, item, fmt.Sprintf("%s[%d]", path, i)); err != nil {
					return err
				}
			}
		}
	case "string":
		text, ok := value.(string)
		if !ok {
			return fmt.Errorf("%s: expected string, got %T", path, value)
		}
		length := float64(len([]rune(text)))
		if minimum, ok := number(s["minLength"]); ok && length < minimum {
			return fmt.Errorf("%s: string shorter than %v", path, minimum)
		}
		if maximum, ok := number(s["maxLength"]); ok && length > maximum {
			return fmt.Errorf("%s: string longer than %v", path, maximum)
		}
		if pattern, ok := s["pattern"].(string); ok {
			compiled, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: invalid schema pattern: %w", path, err)
			}
			if !compiled.MatchString(text) {
				return fmt.Errorf("%s: string does not match %q", path, pattern)
			}
		}
	case "integer":
		valueNumber, ok := number(value)
		if !ok || math.Trunc(valueNumber) != valueNumber {
			return fmt.Errorf("%s: expected integer, got %T", path, value)
		}
		if err := numericBounds(s, valueNumber, path); err != nil {
			return err
		}
	case "number":
		valueNumber, ok := number(value)
		if !ok {
			return fmt.Errorf("%s: expected number, got %T", path, value)
		}
		if err := numericBounds(s, valueNumber, path); err != nil {
			return err
		}
	case "boolean":
		if _, ok := value.(bool); !ok {
			return fmt.Errorf("%s: expected boolean, got %T", path, value)
		}
	case nil:
		// Schemas composed only from refs/combinators/const need no type check.
	default:
		return fmt.Errorf("%s: unsupported schema type %v", path, s["type"])
	}
	return nil
}

func (v schemaValidator) resolve(ref string) (any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("only local refs are supported: %q", ref)
	}
	var current any = v.document
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("ref %q traverses a non-object", ref)
		}
		current, ok = object[token]
		if !ok {
			return nil, fmt.Errorf("ref %q does not exist", ref)
		}
	}
	return current, nil
}

func arrayKeyword(schema map[string]any, name string) []any {
	values, _ := schema[name].([]any)
	return values
}

func stringArray(value any) []string {
	values, _ := value.([]any)
	result := make([]string, 0, len(values))
	for _, item := range values {
		if text, ok := item.(string); ok {
			result = append(result, text)
		}
	}
	return result
}

func number(value any) (float64, bool) {
	switch n := value.(type) {
	case float64:
		return n, true
	case json.Number:
		converted, err := n.Float64()
		return converted, err == nil
	case int:
		return float64(n), true
	default:
		return 0, false
	}
}

func numericBounds(schema map[string]any, value float64, path string) error {
	if minimum, ok := number(schema["minimum"]); ok && value < minimum {
		return fmt.Errorf("%s: %v is below minimum %v", path, value, minimum)
	}
	if maximum, ok := number(schema["maximum"]); ok && value > maximum {
		return fmt.Errorf("%s: %v exceeds maximum %v", path, value, maximum)
	}
	if exclusive, _ := schema["exclusiveMinimum"].(bool); exclusive {
		if minimum, ok := number(schema["minimum"]); ok && value <= minimum {
			return fmt.Errorf("%s: %v does not exceed exclusive minimum %v", path, value, minimum)
		}
	}
	if exclusive, _ := schema["exclusiveMaximum"].(bool); exclusive {
		if maximum, ok := number(schema["maximum"]); ok && value >= maximum {
			return fmt.Errorf("%s: %v is not below exclusive maximum %v", path, value, maximum)
		}
	}
	return nil
}
