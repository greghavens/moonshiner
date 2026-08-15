package vcfarch

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

// validateJSONSchema validates the JSON value against the supplied schema. It
// implements the validation keywords used by the vendored OpenAPI schema and
// the migration-plan schema without relying on the network or a module cache.
func validateJSONSchema(root, schema, value any, path string) error {
	if booleanSchema, ok := schema.(bool); ok {
		if !booleanSchema {
			return fmt.Errorf("%s: rejected by false schema", path)
		}
		return nil
	}
	s, ok := schema.(map[string]any)
	if !ok {
		return fmt.Errorf("%s: schema is not an object", path)
	}

	if ref, ok := s["$ref"].(string); ok {
		resolved, err := resolveLocalRef(root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		if err := validateJSONSchema(root, resolved, value, path); err != nil {
			return err
		}
	}

	for _, keyword := range []string{"allOf"} {
		if subschemas, ok := s[keyword].([]any); ok {
			for _, sub := range subschemas {
				if err := validateJSONSchema(root, sub, value, path); err != nil {
					return err
				}
			}
		}
	}
	for _, keyword := range []string{"anyOf", "oneOf"} {
		if subschemas, ok := s[keyword].([]any); ok {
			matches := 0
			for _, sub := range subschemas {
				if validateJSONSchema(root, sub, value, path) == nil {
					matches++
				}
			}
			if matches == 0 || (keyword == "oneOf" && matches != 1) {
				return fmt.Errorf("%s: matched %d alternatives in %s", path, matches, keyword)
			}
		}
	}
	if notSchema, ok := s["not"]; ok && validateJSONSchema(root, notSchema, value, path) == nil {
		return fmt.Errorf("%s: matched forbidden schema", path)
	}

	if nullable, _ := s["nullable"].(bool); nullable && value == nil {
		return nil
	}
	if expected, ok := s["type"]; ok && !matchesSchemaType(expected, value) {
		return fmt.Errorf("%s: expected type %v, got %T", path, expected, value)
	}
	if enumValues, ok := s["enum"].([]any); ok {
		matched := false
		for _, candidate := range enumValues {
			if jsonValuesEqual(candidate, value) {
				matched = true
				break
			}
		}
		if !matched {
			return fmt.Errorf("%s: value is not in enum", path)
		}
	}
	if constant, ok := s["const"]; ok && !jsonValuesEqual(constant, value) {
		return fmt.Errorf("%s: value differs from const", path)
	}

	switch typed := value.(type) {
	case map[string]any:
		if err := validateObject(root, s, typed, path); err != nil {
			return err
		}
	case []any:
		if err := validateArray(root, s, typed, path); err != nil {
			return err
		}
	case string:
		if err := validateString(s, typed, path); err != nil {
			return err
		}
	case json.Number:
		if err := validateNumber(s, typed, path); err != nil {
			return err
		}
	}
	return nil
}

func validateObject(root any, schema, value map[string]any, path string) error {
	if minimum, ok := schemaInt(schema["minProperties"]); ok && len(value) < minimum {
		return fmt.Errorf("%s: has fewer than %d properties", path, minimum)
	}
	if maximum, ok := schemaInt(schema["maxProperties"]); ok && len(value) > maximum {
		return fmt.Errorf("%s: has more than %d properties", path, maximum)
	}
	if required, ok := schema["required"].([]any); ok {
		for _, item := range required {
			name, _ := item.(string)
			if _, exists := value[name]; !exists {
				return fmt.Errorf("%s: missing required property %q", path, name)
			}
		}
	}
	properties, _ := schema["properties"].(map[string]any)
	for name, child := range value {
		childSchema, declared := properties[name]
		if declared {
			if err := validateJSONSchema(root, childSchema, child, path+"."+name); err != nil {
				return err
			}
			continue
		}
		if additional, exists := schema["additionalProperties"]; exists {
			switch extra := additional.(type) {
			case bool:
				if !extra {
					return fmt.Errorf("%s: additional property %q is not allowed", path, name)
				}
			case map[string]any:
				if err := validateJSONSchema(root, extra, child, path+"."+name); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

func validateArray(root any, schema map[string]any, value []any, path string) error {
	if minimum, ok := schemaInt(schema["minItems"]); ok && len(value) < minimum {
		return fmt.Errorf("%s: has fewer than %d items", path, minimum)
	}
	if maximum, ok := schemaInt(schema["maxItems"]); ok && len(value) > maximum {
		return fmt.Errorf("%s: has more than %d items", path, maximum)
	}
	if unique, _ := schema["uniqueItems"].(bool); unique {
		for i := range value {
			for j := 0; j < i; j++ {
				if jsonValuesEqual(value[i], value[j]) {
					return fmt.Errorf("%s: items %d and %d are equal", path, j, i)
				}
			}
		}
	}
	if itemSchema, ok := schema["items"]; ok {
		for i, item := range value {
			if err := validateJSONSchema(root, itemSchema, item, fmt.Sprintf("%s[%d]", path, i)); err != nil {
				return err
			}
		}
	}
	return nil
}

func validateString(schema map[string]any, value, path string) error {
	length := utf8.RuneCountInString(value)
	if minimum, ok := schemaInt(schema["minLength"]); ok && length < minimum {
		return fmt.Errorf("%s: string is shorter than %d", path, minimum)
	}
	if maximum, ok := schemaInt(schema["maxLength"]); ok && length > maximum {
		return fmt.Errorf("%s: string is longer than %d", path, maximum)
	}
	if pattern, ok := schema["pattern"].(string); ok {
		re, err := regexp.Compile(pattern)
		if err != nil {
			return fmt.Errorf("%s: invalid schema pattern %q: %w", path, pattern, err)
		}
		if !re.MatchString(value) {
			return fmt.Errorf("%s: %q does not match %q", path, value, pattern)
		}
	}
	return nil
}

func validateNumber(schema map[string]any, value json.Number, path string) error {
	number, err := value.Float64()
	if err != nil {
		return fmt.Errorf("%s: invalid number: %w", path, err)
	}
	if minimum, ok := schemaFloat(schema["minimum"]); ok && number < minimum {
		return fmt.Errorf("%s: %v is below minimum %v", path, number, minimum)
	}
	if maximum, ok := schemaFloat(schema["maximum"]); ok && number > maximum {
		return fmt.Errorf("%s: %v is above maximum %v", path, number, maximum)
	}
	if multiple, ok := schemaFloat(schema["multipleOf"]); ok && math.Mod(number, multiple) != 0 {
		return fmt.Errorf("%s: %v is not a multiple of %v", path, number, multiple)
	}
	return nil
}

func resolveLocalRef(root any, ref string) (any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("unsupported non-local ref %q", ref)
	}
	current := root
	for _, encoded := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		part := strings.ReplaceAll(strings.ReplaceAll(encoded, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("ref %q crosses a non-object", ref)
		}
		current, ok = object[part]
		if !ok {
			return nil, fmt.Errorf("ref %q does not exist", ref)
		}
	}
	return current, nil
}

func matchesSchemaType(expected, value any) bool {
	if alternatives, ok := expected.([]any); ok {
		for _, alternative := range alternatives {
			if matchesSchemaType(alternative, value) {
				return true
			}
		}
		return false
	}
	name, _ := expected.(string)
	switch name {
	case "null":
		return value == nil
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
	case "number":
		_, ok := value.(json.Number)
		return ok
	case "integer":
		number, ok := value.(json.Number)
		if !ok {
			return false
		}
		_, err := strconv.ParseInt(number.String(), 10, 64)
		return err == nil
	default:
		return true
	}
}

func schemaInt(value any) (int, bool) {
	float, ok := schemaFloat(value)
	return int(float), ok
}

func schemaFloat(value any) (float64, bool) {
	switch number := value.(type) {
	case json.Number:
		parsed, err := number.Float64()
		return parsed, err == nil
	case float64:
		return number, true
	default:
		return 0, false
	}
}

func jsonValuesEqual(left, right any) bool {
	leftNumber, leftIsNumber := left.(json.Number)
	rightNumber, rightIsNumber := right.(json.Number)
	if leftIsNumber && rightIsNumber {
		leftFloat, leftErr := leftNumber.Float64()
		rightFloat, rightErr := rightNumber.Float64()
		return leftErr == nil && rightErr == nil && leftFloat == rightFloat
	}
	return reflect.DeepEqual(left, right)
}

func joinedErrors(errs []error) error {
	return errors.Join(errs...)
}
