package vcfplan

import (
	"encoding/json"
	"fmt"
	"math"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

// ValidateJSONSchema validates value against the JSON Schema/OpenAPI Schema
// subset used by the pinned contracts. References are always resolved from
// root, so the authoritative schema document itself drives validation.
func ValidateJSONSchema(root, schema map[string]any, value any) error {
	return validateJSONSchema(root, schema, value, "$", 0)
}

func validateJSONSchema(root, schema map[string]any, value any, path string, depth int) error {
	if depth > 128 {
		return fmt.Errorf("%s: schema reference depth exceeded", path)
	}
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolveLocalRef(root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		return validateJSONSchema(root, resolved, value, path, depth+1)
	}

	if constant, ok := schema["const"]; ok && !jsonEqual(constant, value) {
		return fmt.Errorf("%s: value does not equal const %v", path, constant)
	}
	if choices, ok := schema["enum"].([]any); ok {
		matched := false
		for _, choice := range choices {
			if jsonEqual(choice, value) {
				matched = true
				break
			}
		}
		if !matched {
			return fmt.Errorf("%s: value %v is not in enum", path, value)
		}
	}

	if typeName, ok := schema["type"].(string); ok && !matchesType(typeName, value) {
		if value == nil && schema["nullable"] == true {
			return nil
		}
		return fmt.Errorf("%s: expected %s, got %T", path, typeName, value)
	}

	if object, ok := value.(map[string]any); ok {
		if required, ok := schema["required"].([]any); ok {
			for _, raw := range required {
				name, _ := raw.(string)
				if _, exists := object[name]; !exists {
					return fmt.Errorf("%s: missing required property %q", path, name)
				}
			}
		}
		properties, _ := schema["properties"].(map[string]any)
		for name, child := range object {
			rawChildSchema, known := properties[name]
			if !known {
				if additional, exists := schema["additionalProperties"]; exists && additional == false {
					return fmt.Errorf("%s: additional property %q is not allowed", path, name)
				}
				continue
			}
			childSchema, ok := rawChildSchema.(map[string]any)
			if !ok {
				return fmt.Errorf("%s.%s: invalid property schema", path, name)
			}
			if err := validateJSONSchema(root, childSchema, child, path+"."+name, depth+1); err != nil {
				return err
			}
		}
	}

	if array, ok := value.([]any); ok {
		if minimum, ok := schemaNumber(schema["minItems"]); ok && float64(len(array)) < minimum {
			return fmt.Errorf("%s: has %d items, minimum is %v", path, len(array), minimum)
		}
		if maximum, ok := schemaNumber(schema["maxItems"]); ok && float64(len(array)) > maximum {
			return fmt.Errorf("%s: has %d items, maximum is %v", path, len(array), maximum)
		}
		if schema["uniqueItems"] == true {
			seen := map[string]struct{}{}
			for _, item := range array {
				encoded, _ := json.Marshal(item)
				key := string(encoded)
				if _, duplicate := seen[key]; duplicate {
					return fmt.Errorf("%s: items are not unique", path)
				}
				seen[key] = struct{}{}
			}
		}
		if itemSchema, ok := schema["items"].(map[string]any); ok {
			for index, item := range array {
				if err := validateJSONSchema(root, itemSchema, item, fmt.Sprintf("%s[%d]", path, index), depth+1); err != nil {
					return err
				}
			}
		}
	}

	if text, ok := value.(string); ok {
		length := float64(utf8.RuneCountInString(text))
		if minimum, ok := schemaNumber(schema["minLength"]); ok && length < minimum {
			return fmt.Errorf("%s: string is shorter than %v", path, minimum)
		}
		if maximum, ok := schemaNumber(schema["maxLength"]); ok && length > maximum {
			return fmt.Errorf("%s: string is longer than %v", path, maximum)
		}
		if pattern, ok := schema["pattern"].(string); ok {
			expression, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: schema pattern %q is unsupported: %w", path, pattern, err)
			}
			if !expression.MatchString(text) {
				return fmt.Errorf("%s: %q does not match %q", path, text, pattern)
			}
		}
	}

	if number, ok := schemaNumber(value); ok {
		if minimum, ok := schemaNumber(schema["minimum"]); ok && number < minimum {
			return fmt.Errorf("%s: %v is less than minimum %v", path, number, minimum)
		}
		if maximum, ok := schemaNumber(schema["maximum"]); ok && number > maximum {
			return fmt.Errorf("%s: %v is greater than maximum %v", path, number, maximum)
		}
	}
	return nil
}

func resolveLocalRef(root map[string]any, ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("only local schema references are supported: %q", ref)
	}
	var current any = root
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("schema reference %q traverses a non-object", ref)
		}
		current, ok = object[token]
		if !ok {
			return nil, fmt.Errorf("schema reference %q does not exist", ref)
		}
	}
	resolved, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("schema reference %q is not an object", ref)
	}
	return resolved, nil
}

func matchesType(typeName string, value any) bool {
	switch typeName {
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
		_, ok := schemaNumber(value)
		return ok
	case "integer":
		number, ok := schemaNumber(value)
		return ok && math.Trunc(number) == number
	case "null":
		return value == nil
	default:
		return false
	}
}

func schemaNumber(value any) (float64, bool) {
	switch number := value.(type) {
	case json.Number:
		parsed, err := strconv.ParseFloat(number.String(), 64)
		return parsed, err == nil
	case float64:
		return number, true
	case float32:
		return float64(number), true
	case int:
		return float64(number), true
	case int64:
		return float64(number), true
	default:
		return 0, false
	}
}

func jsonEqual(left, right any) bool {
	if leftNumber, ok := schemaNumber(left); ok {
		if rightNumber, ok := schemaNumber(right); ok {
			return leftNumber == rightNumber
		}
	}
	return reflect.DeepEqual(left, right)
}
