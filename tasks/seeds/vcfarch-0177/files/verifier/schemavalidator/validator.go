package schemavalidator

import (
	"encoding/json"
	"fmt"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// Validate applies the subset of JSON Schema 2020-12 used by the checked-in
// installer specification. The schema, rather than a second Go struct, defines
// the accepted artifact shape.
func Validate(schema, value any) error {
	return validate(schema, value, schema, "$")
}

func validate(schema, value, root any, path string) error {
	s, ok := schema.(map[string]any)
	if !ok {
		return fmt.Errorf("%s: schema node is not an object", path)
	}
	if ref, ok := s["$ref"].(string); ok {
		resolved, err := resolveRef(root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		return validate(resolved, value, root, path)
	}
	if constant, exists := s["const"]; exists && !reflect.DeepEqual(constant, value) {
		return fmt.Errorf("%s: value does not equal const %v", path, constant)
	}
	if rawEnum, exists := s["enum"].([]any); exists {
		matched := false
		for _, candidate := range rawEnum {
			matched = matched || reflect.DeepEqual(candidate, value)
		}
		if !matched {
			return fmt.Errorf("%s: %v is not in enum", path, value)
		}
	}
	if typ, ok := s["type"].(string); ok {
		if err := validateType(typ, value, path); err != nil {
			return err
		}
	}

	switch v := value.(type) {
	case map[string]any:
		required, _ := s["required"].([]any)
		for _, raw := range required {
			key := raw.(string)
			if _, exists := v[key]; !exists {
				return fmt.Errorf("%s: missing required property %q", path, key)
			}
		}
		properties, _ := s["properties"].(map[string]any)
		if additional, exists := s["additionalProperties"].(bool); exists && !additional {
			for key := range v {
				if _, allowed := properties[key]; !allowed {
					return fmt.Errorf("%s: additional property %q", path, key)
				}
			}
		}
		keys := make([]string, 0, len(v))
		for key := range v {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			if childSchema, exists := properties[key]; exists {
				if err := validate(childSchema, v[key], root, path+"."+key); err != nil {
					return err
				}
			}
		}
	case []any:
		if min, exists := jsonInt(s["minItems"]); exists && len(v) < min {
			return fmt.Errorf("%s: has %d items, minimum is %d", path, len(v), min)
		}
		if unique, _ := s["uniqueItems"].(bool); unique {
			seen := map[string]bool{}
			for _, item := range v {
				encoded, _ := json.Marshal(item)
				key := string(encoded)
				if seen[key] {
					return fmt.Errorf("%s: duplicate array item", path)
				}
				seen[key] = true
			}
		}
		if itemSchema, exists := s["items"]; exists {
			for i, item := range v {
				if err := validate(itemSchema, item, root, fmt.Sprintf("%s[%d]", path, i)); err != nil {
					return err
				}
			}
		}
	case string:
		if min, exists := jsonInt(s["minLength"]); exists && len([]rune(v)) < min {
			return fmt.Errorf("%s: string is shorter than %d", path, min)
		}
		if pattern, exists := s["pattern"].(string); exists {
			re, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: invalid schema pattern: %v", path, err)
			}
			if !re.MatchString(v) {
				return fmt.Errorf("%s: string does not match %q", path, pattern)
			}
		}
	case json.Number:
		if min, exists := jsonInt(s["minimum"]); exists {
			n, err := strconv.Atoi(v.String())
			if err != nil || n < min {
				return fmt.Errorf("%s: number %q is below minimum %d", path, v, min)
			}
		}
	}
	return nil
}

func validateType(typ string, value any, path string) error {
	valid := false
	switch typ {
	case "object":
		_, valid = value.(map[string]any)
	case "array":
		_, valid = value.([]any)
	case "string":
		_, valid = value.(string)
	case "integer":
		if n, ok := value.(json.Number); ok {
			_, err := strconv.ParseInt(n.String(), 10, 64)
			valid = err == nil
		}
	default:
		return fmt.Errorf("%s: unsupported schema type %q", path, typ)
	}
	if !valid {
		return fmt.Errorf("%s: expected %s, got %T", path, typ, value)
	}
	return nil
}

func resolveRef(root any, ref string) (any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("unsupported ref %q", ref)
	}
	cur := root
	for _, part := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		obj, ok := cur.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("ref %q traverses a non-object", ref)
		}
		part = strings.ReplaceAll(strings.ReplaceAll(part, "~1", "/"), "~0", "~")
		cur, ok = obj[part]
		if !ok {
			return nil, fmt.Errorf("ref %q is unresolved", ref)
		}
	}
	return cur, nil
}

func jsonInt(v any) (int, bool) {
	switch n := v.(type) {
	case json.Number:
		i, err := strconv.Atoi(n.String())
		return i, err == nil
	case float64:
		return int(n), n == float64(int(n))
	default:
		return 0, false
	}
}
