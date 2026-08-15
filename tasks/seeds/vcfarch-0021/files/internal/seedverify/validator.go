package seedverify

import (
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
)

// Validate checks value against schema. root is the document used to resolve
// local JSON pointers. It implements the OpenAPI/JSON-Schema keywords exercised
// by the pinned installer SddcSpec and migration-plan schema.
func Validate(root, schema map[string]any, value any) error {
	return validateAt(root, schema, value, "$")
}

func validateAt(root, schema map[string]any, value any, path string) error {
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolve(root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		return validateAt(root, resolved, value, path)
	}

	if value == nil {
		if nullable, _ := schema["nullable"].(bool); nullable {
			return nil
		}
		if typ, _ := schema["type"].(string); typ != "" && typ != "null" {
			return fmt.Errorf("%s: null is not %s", path, typ)
		}
	}

	for _, keyword := range []string{"allOf"} {
		if list, ok := schema[keyword].([]any); ok {
			for i, candidate := range list {
				child, ok := candidate.(map[string]any)
				if !ok {
					return fmt.Errorf("%s: %s[%d] is not a schema", path, keyword, i)
				}
				if err := validateAt(root, child, value, path); err != nil {
					return err
				}
			}
		}
	}
	for _, keyword := range []string{"anyOf", "oneOf"} {
		if list, ok := schema[keyword].([]any); ok {
			matches := 0
			for _, candidate := range list {
				child, ok := candidate.(map[string]any)
				if ok && validateAt(root, child, value, path) == nil {
					matches++
				}
			}
			if matches == 0 || (keyword == "oneOf" && matches != 1) {
				return fmt.Errorf("%s: failed %s (%d matches)", path, keyword, matches)
			}
		}
	}

	if expected, ok := schema["const"]; ok && !jsonEqual(expected, value) {
		return fmt.Errorf("%s: value does not equal const", path)
	}
	if enum, ok := schema["enum"].([]any); ok {
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

	typ, _ := schema["type"].(string)
	switch typ {
	case "object":
		obj, ok := value.(map[string]any)
		if !ok {
			return fmt.Errorf("%s: expected object, got %T", path, value)
		}
		if required, ok := schema["required"].([]any); ok {
			for _, item := range required {
				name, _ := item.(string)
				if _, exists := obj[name]; !exists {
					return fmt.Errorf("%s: missing required property %q", path, name)
				}
			}
		}
		properties, _ := schema["properties"].(map[string]any)
		for name, childValue := range obj {
			if childAny, exists := properties[name]; exists {
				child, ok := childAny.(map[string]any)
				if !ok {
					return fmt.Errorf("%s.%s: property schema is invalid", path, name)
				}
				if err := validateAt(root, child, childValue, path+"."+name); err != nil {
					return err
				}
				continue
			}
			if additional, exists := schema["additionalProperties"]; exists {
				switch rule := additional.(type) {
				case bool:
					if !rule {
						return fmt.Errorf("%s: additional property %q", path, name)
					}
				case map[string]any:
					if err := validateAt(root, rule, childValue, path+"."+name); err != nil {
						return err
					}
				}
			}
		}
	case "array":
		items, ok := value.([]any)
		if !ok {
			return fmt.Errorf("%s: expected array, got %T", path, value)
		}
		if min, ok := number(schema["minItems"]); ok && float64(len(items)) < min {
			return fmt.Errorf("%s: fewer than %.0f items", path, min)
		}
		if max, ok := number(schema["maxItems"]); ok && float64(len(items)) > max {
			return fmt.Errorf("%s: more than %.0f items", path, max)
		}
		if unique, _ := schema["uniqueItems"].(bool); unique {
			seen := map[string]struct{}{}
			for _, item := range items {
				key := canonical(item)
				if _, exists := seen[key]; exists {
					return fmt.Errorf("%s: duplicate array item", path)
				}
				seen[key] = struct{}{}
			}
		}
		if child, ok := schema["items"].(map[string]any); ok {
			for i, item := range items {
				if err := validateAt(root, child, item, fmt.Sprintf("%s[%d]", path, i)); err != nil {
					return err
				}
			}
		}
	case "string":
		s, ok := value.(string)
		if !ok {
			return fmt.Errorf("%s: expected string, got %T", path, value)
		}
		length := float64(len([]rune(s)))
		if min, ok := number(schema["minLength"]); ok && length < min {
			return fmt.Errorf("%s: string is shorter than %.0f", path, min)
		}
		if max, ok := number(schema["maxLength"]); ok && length > max {
			return fmt.Errorf("%s: string is longer than %.0f", path, max)
		}
		if pattern, ok := schema["pattern"].(string); ok {
			matched, err := regexp.MatchString(pattern, s)
			if err != nil {
				return fmt.Errorf("%s: invalid schema pattern: %w", path, err)
			}
			if !matched {
				return fmt.Errorf("%s: string does not match %q", path, pattern)
			}
		}
	case "integer":
		n, ok := number(value)
		if !ok || math.Trunc(n) != n {
			return fmt.Errorf("%s: expected integer, got %T", path, value)
		}
		if err := validateNumber(schema, n, path); err != nil {
			return err
		}
	case "number":
		n, ok := number(value)
		if !ok {
			return fmt.Errorf("%s: expected number, got %T", path, value)
		}
		if err := validateNumber(schema, n, path); err != nil {
			return err
		}
	case "boolean":
		if _, ok := value.(bool); !ok {
			return fmt.Errorf("%s: expected boolean, got %T", path, value)
		}
	case "null":
		if value != nil {
			return fmt.Errorf("%s: expected null", path)
		}
	}
	return nil
}

func validateNumber(schema map[string]any, value float64, path string) error {
	if min, ok := number(schema["minimum"]); ok && value < min {
		return fmt.Errorf("%s: %.4g is below minimum %.4g", path, value, min)
	}
	if max, ok := number(schema["maximum"]); ok && value > max {
		return fmt.Errorf("%s: %.4g is above maximum %.4g", path, value, max)
	}
	return nil
}

func resolve(root map[string]any, ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("only local refs are supported: %q", ref)
	}
	var current any = root
	for _, raw := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		part := strings.ReplaceAll(strings.ReplaceAll(raw, "~1", "/"), "~0", "~")
		obj, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("ref %q crosses a non-object", ref)
		}
		current, ok = obj[part]
		if !ok {
			return nil, fmt.Errorf("ref %q is missing segment %q", ref, part)
		}
	}
	result, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("ref %q does not identify a schema", ref)
	}
	return result, nil
}

func number(value any) (float64, bool) {
	switch n := value.(type) {
	case json.Number:
		v, err := strconv.ParseFloat(string(n), 64)
		return v, err == nil
	case float64:
		return n, true
	case float32:
		return float64(n), true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case int32:
		return float64(n), true
	default:
		return 0, false
	}
}

func jsonEqual(a, b any) bool { return canonical(a) == canonical(b) }

func canonical(value any) string {
	b, _ := json.Marshal(value)
	return string(b)
}
