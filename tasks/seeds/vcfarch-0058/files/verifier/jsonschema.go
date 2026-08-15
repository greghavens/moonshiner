// Package verifier provides the small, deterministic JSON Schema evaluator
// used by the protected acceptance test. It resolves schemas from the bundled
// OpenAPI document and never performs network access.
package verifier

import (
	"encoding/json"
	"fmt"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

// Validate validates instance against schema, resolving local references from
// root. Returned messages are deterministic and empty on success.
func Validate(root, schema, instance any) []string {
	v := validator{root: root}
	v.walk(schema, instance, "$")
	return v.errors
}

type validator struct {
	root   any
	errors []string
}

func (v *validator) add(path, format string, args ...any) {
	v.errors = append(v.errors, path+": "+fmt.Sprintf(format, args...))
}

func (v *validator) walk(rawSchema, instance any, path string) {
	schema, ok := rawSchema.(map[string]any)
	if !ok {
		v.add(path, "schema is not an object")
		return
	}

	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolvePointer(v.root, ref)
		if err != nil {
			v.add(path, "cannot resolve %s: %v", ref, err)
			return
		}
		v.walk(resolved, instance, path)
	}
	if instance == nil {
		if nullable, _ := schema["nullable"].(bool); nullable {
			return
		}
	}

	v.compositions(schema, instance, path)
	if values, ok := schema["enum"].([]any); ok && !containsJSON(values, instance) {
		v.add(path, "value is not in enum")
	}

	typeName, _ := schema["type"].(string)
	if typeName != "" && !matchesType(typeName, instance) {
		v.add(path, "expected %s, got %T", typeName, instance)
		return
	}

	switch typeName {
	case "object":
		v.object(schema, instance.(map[string]any), path)
	case "array":
		v.array(schema, instance.([]any), path)
	case "string":
		v.string(schema, instance.(string), path)
	case "integer", "number":
		v.number(schema, instance, path)
	}
}

func (v *validator) compositions(schema map[string]any, instance any, path string) {
	if all, ok := schema["allOf"].([]any); ok {
		for _, child := range all {
			v.walk(child, instance, path)
		}
	}
	if anySchemas, ok := schema["anyOf"].([]any); ok {
		matched := false
		for _, child := range anySchemas {
			probe := validator{root: v.root}
			probe.walk(child, instance, path)
			if len(probe.errors) == 0 {
				matched = true
				break
			}
		}
		if !matched {
			v.add(path, "does not match anyOf")
		}
	}
	if one, ok := schema["oneOf"].([]any); ok {
		matches := 0
		for _, child := range one {
			probe := validator{root: v.root}
			probe.walk(child, instance, path)
			if len(probe.errors) == 0 {
				matches++
			}
		}
		if matches != 1 {
			v.add(path, "matches %d oneOf alternatives", matches)
		}
	}
}

func (v *validator) object(schema, object map[string]any, path string) {
	if minimum, ok := integerKeyword(schema, "minProperties"); ok && len(object) < minimum {
		v.add(path, "has %d properties, minimum is %d", len(object), minimum)
	}
	if maximum, ok := integerKeyword(schema, "maxProperties"); ok && len(object) > maximum {
		v.add(path, "has %d properties, maximum is %d", len(object), maximum)
	}
	if required, ok := schema["required"].([]any); ok {
		for _, item := range required {
			name, _ := item.(string)
			if _, exists := object[name]; !exists {
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
		if child, exists := properties[key]; exists {
			v.walk(child, object[key], childPath(path, key))
			continue
		}
		switch additional := schema["additionalProperties"].(type) {
		case bool:
			if !additional {
				v.add(path, "additional property %q is not allowed", key)
			}
		case map[string]any:
			v.walk(additional, object[key], childPath(path, key))
		}
	}
}

func (v *validator) array(schema map[string]any, values []any, path string) {
	if minimum, ok := integerKeyword(schema, "minItems"); ok && len(values) < minimum {
		v.add(path, "has %d items, minimum is %d", len(values), minimum)
	}
	if maximum, ok := integerKeyword(schema, "maxItems"); ok && len(values) > maximum {
		v.add(path, "has %d items, maximum is %d", len(values), maximum)
	}
	if unique, _ := schema["uniqueItems"].(bool); unique {
		for i := range values {
			for j := 0; j < i; j++ {
				if reflect.DeepEqual(values[i], values[j]) {
					v.add(path, "items %d and %d are duplicates", j, i)
				}
			}
		}
	}
	if itemSchema, exists := schema["items"]; exists {
		for i, value := range values {
			v.walk(itemSchema, value, fmt.Sprintf("%s[%d]", path, i))
		}
	}
}

func (v *validator) string(schema map[string]any, value, path string) {
	length := utf8.RuneCountInString(value)
	if minimum, ok := integerKeyword(schema, "minLength"); ok && length < minimum {
		v.add(path, "length %d is below minimum %d", length, minimum)
	}
	if maximum, ok := integerKeyword(schema, "maxLength"); ok && length > maximum {
		v.add(path, "length %d exceeds maximum %d", length, maximum)
	}
	if pattern, ok := schema["pattern"].(string); ok {
		re, err := regexp.Compile(pattern)
		if err != nil {
			v.add(path, "invalid schema pattern %q", pattern)
		} else if !re.MatchString(value) {
			v.add(path, "does not match pattern %q", pattern)
		}
	}
}

func (v *validator) number(schema map[string]any, value any, path string) {
	number, ok := numeric(value)
	if !ok {
		return
	}
	if minimum, ok := numeric(schema["minimum"]); ok && number < minimum {
		v.add(path, "%v is below minimum %v", number, minimum)
	}
	if maximum, ok := numeric(schema["maximum"]); ok && number > maximum {
		v.add(path, "%v exceeds maximum %v", number, maximum)
	}
}

func matchesType(want string, value any) bool {
	switch want {
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
		_, ok := numeric(value)
		return ok
	case "integer":
		switch number := value.(type) {
		case json.Number:
			_, err := strconv.ParseInt(string(number), 10, 64)
			return err == nil
		case float64:
			return number == float64(int64(number))
		case int, int32, int64:
			return true
		}
	}
	return false
}

func numeric(value any) (float64, bool) {
	switch number := value.(type) {
	case json.Number:
		parsed, err := strconv.ParseFloat(string(number), 64)
		return parsed, err == nil
	case float64:
		return number, true
	case int:
		return float64(number), true
	case int32:
		return float64(number), true
	case int64:
		return float64(number), true
	default:
		return 0, false
	}
}

func integerKeyword(schema map[string]any, key string) (int, bool) {
	number, ok := numeric(schema[key])
	return int(number), ok
}

func containsJSON(values []any, value any) bool {
	for _, candidate := range values {
		if reflect.DeepEqual(candidate, value) {
			return true
		}
	}
	return false
}

func resolvePointer(root any, pointer string) (any, error) {
	if pointer == "#" {
		return root, nil
	}
	if !strings.HasPrefix(pointer, "#/") {
		return nil, fmt.Errorf("only local JSON pointers are supported")
	}
	current := root
	for _, encoded := range strings.Split(strings.TrimPrefix(pointer, "#/"), "/") {
		key := strings.ReplaceAll(strings.ReplaceAll(encoded, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%q traverses a non-object", key)
		}
		current, ok = object[key]
		if !ok {
			return nil, fmt.Errorf("key %q does not exist", key)
		}
	}
	return current, nil
}

func childPath(parent, key string) string {
	if regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_-]*$`).MatchString(key) {
		return parent + "." + key
	}
	return parent + "[" + strconv.Quote(key) + "]"
}
