package verifier

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"unicode/utf8"
)

func loadJSONValue(t *testing.T, path string) any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		t.Fatalf("decode %s: trailing JSON content", path)
	}
	return value
}

// validateJSONSchema implements the JSON Schema/OpenAPI keywords exercised by
// the pinned installer SddcSpec and the fixed migration-plan schema. It resolves
// only in-document references and never performs network access.
func validateJSONSchema(root, schema, value any, path string) error {
	schemaObject, ok := schema.(map[string]any)
	if !ok {
		if booleanSchema, ok := schema.(bool); ok && booleanSchema {
			return nil
		}
		return fmt.Errorf("%s: invalid schema node", path)
	}

	if reference, ok := schemaObject["$ref"].(string); ok {
		resolved, err := resolveJSONPointer(root, reference)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		if err := validateJSONSchema(root, resolved, value, path); err != nil {
			return err
		}
	}

	if schemas, ok := schemaObject["allOf"].([]any); ok {
		for _, candidate := range schemas {
			if err := validateJSONSchema(root, candidate, value, path); err != nil {
				return err
			}
		}
	}
	if schemas, ok := schemaObject["anyOf"].([]any); ok {
		matched := false
		for _, candidate := range schemas {
			if validateJSONSchema(root, candidate, value, path) == nil {
				matched = true
				break
			}
		}
		if !matched {
			return fmt.Errorf("%s: does not match any allowed schema", path)
		}
	}
	if schemas, ok := schemaObject["oneOf"].([]any); ok {
		matches := 0
		for _, candidate := range schemas {
			if validateJSONSchema(root, candidate, value, path) == nil {
				matches++
			}
		}
		if matches != 1 {
			return fmt.Errorf("%s: matches %d oneOf schemas, want 1", path, matches)
		}
	}

	if constant, exists := schemaObject["const"]; exists && !reflect.DeepEqual(constant, value) {
		return fmt.Errorf("%s: got %v, want constant %v", path, value, constant)
	}
	if options, ok := schemaObject["enum"].([]any); ok {
		matched := false
		for _, option := range options {
			if reflect.DeepEqual(option, value) {
				matched = true
				break
			}
		}
		if !matched {
			return fmt.Errorf("%s: %v is not in enum", path, value)
		}
	}

	if expectedType, ok := schemaObject["type"].(string); ok {
		if err := requireJSONType(expectedType, value, path); err != nil {
			return err
		}
	}

	switch typed := value.(type) {
	case map[string]any:
		if required, ok := schemaObject["required"].([]any); ok {
			for _, item := range required {
				name, _ := item.(string)
				if _, exists := typed[name]; !exists {
					return fmt.Errorf("%s: missing required property %q", path, name)
				}
			}
		}
		properties, _ := schemaObject["properties"].(map[string]any)
		for name, child := range typed {
			if childSchema, exists := properties[name]; exists {
				if err := validateJSONSchema(root, childSchema, child, path+"."+name); err != nil {
					return err
				}
				continue
			}
			if additional, exists := schemaObject["additionalProperties"]; exists {
				switch rule := additional.(type) {
				case bool:
					if !rule {
						return fmt.Errorf("%s: additional property %q is not allowed", path, name)
					}
				case map[string]any:
					if err := validateJSONSchema(root, rule, child, path+"."+name); err != nil {
						return err
					}
				}
			}
		}
		if minimum, ok := schemaInteger(schemaObject["minProperties"]); ok && len(typed) < minimum {
			return fmt.Errorf("%s: has %d properties, minimum is %d", path, len(typed), minimum)
		}
	case []any:
		if minimum, ok := schemaInteger(schemaObject["minItems"]); ok && len(typed) < minimum {
			return fmt.Errorf("%s: has %d items, minimum is %d", path, len(typed), minimum)
		}
		if maximum, ok := schemaInteger(schemaObject["maxItems"]); ok && len(typed) > maximum {
			return fmt.Errorf("%s: has %d items, maximum is %d", path, len(typed), maximum)
		}
		if itemSchema, exists := schemaObject["items"]; exists {
			for index, child := range typed {
				if err := validateJSONSchema(root, itemSchema, child, fmt.Sprintf("%s[%d]", path, index)); err != nil {
					return err
				}
			}
		}
		if unique, _ := schemaObject["uniqueItems"].(bool); unique {
			for left := range typed {
				for right := left + 1; right < len(typed); right++ {
					if reflect.DeepEqual(typed[left], typed[right]) {
						return fmt.Errorf("%s: items %d and %d are duplicates", path, left, right)
					}
				}
			}
		}
	case string:
		length := utf8.RuneCountInString(typed)
		if minimum, ok := schemaInteger(schemaObject["minLength"]); ok && length < minimum {
			return fmt.Errorf("%s: string length %d is below %d", path, length, minimum)
		}
		if maximum, ok := schemaInteger(schemaObject["maxLength"]); ok && length > maximum {
			return fmt.Errorf("%s: string length %d exceeds %d", path, length, maximum)
		}
		if expression, ok := schemaObject["pattern"].(string); ok {
			compiled, err := regexp.Compile(expression)
			if err != nil {
				return fmt.Errorf("%s: bad schema pattern: %w", path, err)
			}
			if !compiled.MatchString(typed) {
				return fmt.Errorf("%s: %q does not match %q", path, typed, expression)
			}
		}
	case json.Number:
		number, err := typed.Float64()
		if err != nil {
			return fmt.Errorf("%s: invalid number %q", path, typed)
		}
		if minimum, ok := schemaFloat(schemaObject["minimum"]); ok && number < minimum {
			return fmt.Errorf("%s: %v is below minimum %v", path, number, minimum)
		}
		if maximum, ok := schemaFloat(schemaObject["maximum"]); ok && number > maximum {
			return fmt.Errorf("%s: %v exceeds maximum %v", path, number, maximum)
		}
	}
	return nil
}

func requireJSONType(expected string, value any, path string) error {
	valid := false
	switch expected {
	case "object":
		_, valid = value.(map[string]any)
	case "array":
		_, valid = value.([]any)
	case "string":
		_, valid = value.(string)
	case "boolean":
		_, valid = value.(bool)
	case "number":
		_, valid = value.(json.Number)
	case "integer":
		if number, ok := value.(json.Number); ok {
			parsed, err := strconv.ParseFloat(number.String(), 64)
			valid = err == nil && !math.IsNaN(parsed) && math.Trunc(parsed) == parsed
		}
	case "null":
		valid = value == nil
	default:
		return fmt.Errorf("%s: unsupported schema type %q", path, expected)
	}
	if !valid {
		return fmt.Errorf("%s: expected %s, got %T", path, expected, value)
	}
	return nil
}

func resolveJSONPointer(root any, reference string) (any, error) {
	if !strings.HasPrefix(reference, "#/") {
		return nil, fmt.Errorf("only local JSON pointers are supported, got %q", reference)
	}
	current := root
	for _, token := range strings.Split(strings.TrimPrefix(reference, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("reference %q traverses a non-object", reference)
		}
		var exists bool
		current, exists = object[token]
		if !exists {
			return nil, fmt.Errorf("reference %q does not exist", reference)
		}
	}
	return current, nil
}

func schemaInteger(value any) (int, bool) {
	number, ok := value.(json.Number)
	if !ok {
		return 0, false
	}
	parsed, err := strconv.Atoi(number.String())
	return parsed, err == nil
}

func schemaFloat(value any) (float64, bool) {
	number, ok := value.(json.Number)
	if !ok {
		return 0, false
	}
	parsed, err := number.Float64()
	return parsed, err == nil
}
