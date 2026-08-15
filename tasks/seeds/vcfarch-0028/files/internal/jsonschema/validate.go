package jsonschema

import (
	"encoding/json"
	"fmt"
	"math"
	"reflect"
	"regexp"
	"strconv"
	"strings"
)

// ValidateReference validates value against an internal JSON Schema reference
// in document. It supports the validation vocabulary used by the vendored
// OpenAPI schemas and the migration-plan schema.
func ValidateReference(document map[string]any, ref string, value any) error {
	schema, err := resolve(document, ref)
	if err != nil {
		return err
	}
	return validate(document, schema, value, "$")
}

// Validate validates value against schema, resolving internal references from
// document.
func Validate(document, schema map[string]any, value any) error {
	return validate(document, schema, value, "$")
}

func validate(root, schema map[string]any, value any, path string) error {
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolve(root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		return validate(root, resolved, value, path)
	}

	if value == nil {
		if nullable, _ := schema["nullable"].(bool); nullable {
			return nil
		}
	}

	for _, keyword := range []string{"allOf", "anyOf", "oneOf"} {
		if raw, ok := schema[keyword].([]any); ok {
			matches := 0
			var firstErr error
			for _, item := range raw {
				sub, ok := item.(map[string]any)
				if !ok {
					return fmt.Errorf("%s: %s contains a non-schema", path, keyword)
				}
				if err := validate(root, sub, value, path); err == nil {
					matches++
				} else if firstErr == nil {
					firstErr = err
				}
			}
			switch keyword {
			case "allOf":
				if matches != len(raw) {
					return firstErr
				}
			case "anyOf":
				if matches == 0 {
					return fmt.Errorf("%s: does not match anyOf: %w", path, firstErr)
				}
			case "oneOf":
				if matches != 1 {
					return fmt.Errorf("%s: matches %d oneOf alternatives", path, matches)
				}
			}
		}
	}

	if raw, ok := schema["not"].(map[string]any); ok {
		if err := validate(root, raw, value, path); err == nil {
			return fmt.Errorf("%s: matches forbidden schema", path)
		}
	}

	if c, ok := schema["const"]; ok && !reflect.DeepEqual(c, value) {
		return fmt.Errorf("%s: value does not equal const", path)
	}
	if values, ok := schema["enum"].([]any); ok {
		found := false
		for _, candidate := range values {
			if reflect.DeepEqual(candidate, value) {
				found = true
				break
			}
		}
		if !found {
			return fmt.Errorf("%s: value is not in enum", path)
		}
	}

	if rawType, ok := schema["type"]; ok {
		if !matchesType(rawType, value) {
			return fmt.Errorf("%s: expected type %v, got %T", path, rawType, value)
		}
	}

	switch v := value.(type) {
	case map[string]any:
		if err := validateObject(root, schema, v, path); err != nil {
			return err
		}
	case []any:
		if err := validateArray(root, schema, v, path); err != nil {
			return err
		}
	case string:
		if err := validateString(schema, v, path); err != nil {
			return err
		}
	case float64:
		if err := validateNumber(schema, v, path); err != nil {
			return err
		}
	}
	return nil
}

func validateObject(root, schema map[string]any, value map[string]any, path string) error {
	if min, ok := number(schema["minProperties"]); ok && float64(len(value)) < min {
		return fmt.Errorf("%s: has fewer than %v properties", path, min)
	}
	if max, ok := number(schema["maxProperties"]); ok && float64(len(value)) > max {
		return fmt.Errorf("%s: has more than %v properties", path, max)
	}
	if required, ok := schema["required"].([]any); ok {
		for _, raw := range required {
			name, _ := raw.(string)
			if _, exists := value[name]; !exists {
				return fmt.Errorf("%s: missing required property %q", path, name)
			}
		}
	}

	properties, _ := schema["properties"].(map[string]any)
	for name, item := range value {
		if raw, exists := properties[name]; exists {
			sub, ok := raw.(map[string]any)
			if !ok {
				return fmt.Errorf("%s.%s: property schema is invalid", path, name)
			}
			if err := validate(root, sub, item, path+"."+name); err != nil {
				return err
			}
			continue
		}
		if additional, exists := schema["additionalProperties"]; exists {
			switch a := additional.(type) {
			case bool:
				if !a {
					return fmt.Errorf("%s: additional property %q is not allowed", path, name)
				}
			case map[string]any:
				if err := validate(root, a, item, path+"."+name); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

func validateArray(root, schema map[string]any, value []any, path string) error {
	if min, ok := number(schema["minItems"]); ok && float64(len(value)) < min {
		return fmt.Errorf("%s: has fewer than %v items", path, min)
	}
	if max, ok := number(schema["maxItems"]); ok && float64(len(value)) > max {
		return fmt.Errorf("%s: has more than %v items", path, max)
	}
	if unique, _ := schema["uniqueItems"].(bool); unique {
		for i := range value {
			for j := 0; j < i; j++ {
				if reflect.DeepEqual(value[i], value[j]) {
					return fmt.Errorf("%s: items %d and %d are duplicates", path, j, i)
				}
			}
		}
	}
	if items, ok := schema["items"].(map[string]any); ok {
		for i, item := range value {
			if err := validate(root, items, item, path+"["+strconv.Itoa(i)+"]"); err != nil {
				return err
			}
		}
	}
	return nil
}

func validateString(schema map[string]any, value, path string) error {
	runes := len([]rune(value))
	if min, ok := number(schema["minLength"]); ok && float64(runes) < min {
		return fmt.Errorf("%s: string is shorter than %v", path, min)
	}
	if max, ok := number(schema["maxLength"]); ok && float64(runes) > max {
		return fmt.Errorf("%s: string is longer than %v", path, max)
	}
	if pattern, ok := schema["pattern"].(string); ok {
		re, err := regexp.Compile(pattern)
		if err != nil {
			return fmt.Errorf("%s: invalid schema pattern: %w", path, err)
		}
		if !re.MatchString(value) {
			return fmt.Errorf("%s: %q does not match %q", path, value, pattern)
		}
	}
	return nil
}

func validateNumber(schema map[string]any, value float64, path string) error {
	if min, ok := number(schema["minimum"]); ok && value < min {
		return fmt.Errorf("%s: %v is below minimum %v", path, value, min)
	}
	if max, ok := number(schema["maximum"]); ok && value > max {
		return fmt.Errorf("%s: %v is above maximum %v", path, value, max)
	}
	if multiple, ok := number(schema["multipleOf"]); ok && multiple != 0 {
		quotient := value / multiple
		if math.Abs(quotient-math.Round(quotient)) > 1e-9 {
			return fmt.Errorf("%s: %v is not a multiple of %v", path, value, multiple)
		}
	}
	return nil
}

func matchesType(raw any, value any) bool {
	if types, ok := raw.([]any); ok {
		for _, item := range types {
			if matchesType(item, value) {
				return true
			}
		}
		return false
	}
	typeName, ok := raw.(string)
	if !ok {
		return false
	}
	switch typeName {
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
	case "number":
		_, ok := value.(float64)
		return ok
	case "integer":
		n, ok := value.(float64)
		return ok && math.Trunc(n) == n
	case "boolean":
		_, ok := value.(bool)
		return ok
	default:
		return false
	}
}

func resolve(root map[string]any, ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("only internal references are supported: %q", ref)
	}
	var current any = root
	for _, encoded := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		part := strings.ReplaceAll(strings.ReplaceAll(encoded, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("reference %q traverses a non-object", ref)
		}
		current, ok = object[part]
		if !ok {
			return nil, fmt.Errorf("reference %q does not exist", ref)
		}
	}
	resolved, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("reference %q is not a schema object", ref)
	}
	return resolved, nil
}

func number(value any) (float64, bool) {
	switch n := value.(type) {
	case float64:
		return n, true
	case json.Number:
		f, err := n.Float64()
		return f, err == nil
	default:
		return 0, false
	}
}
