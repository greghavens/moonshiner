// Package verify provides the small, dependency-free JSON Schema evaluator used
// by the protected verifier. It evaluates the schema keywords used by the
// pinned OpenAPI document and the migration-plan schema.
package verify

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

// Violation describes one failed schema assertion.
type Violation struct {
	Path    string
	Message string
}

func (v Violation) Error() string { return v.Path + ": " + v.Message }

// Decode parses JSON without losing integer precision.
func Decode(data []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return nil, fmt.Errorf("multiple JSON values")
		}
		return nil, err
	}
	return value, nil
}

// Validate validates instance against schema, resolving local references from
// documentRoot.
func Validate(documentRoot, schema, instance any) []Violation {
	return validate(documentRoot, schema, instance, "$", 0)
}

// ValidateRef validates instance against a local schema reference.
func ValidateRef(documentRoot any, ref string, instance any) []Violation {
	target, err := resolveRef(documentRoot, ref)
	if err != nil {
		return []Violation{{Path: "$", Message: err.Error()}}
	}
	return validate(documentRoot, target, instance, "$", 0)
}

func validate(root, rawSchema, instance any, path string, depth int) []Violation {
	if depth > 256 {
		return []Violation{{Path: path, Message: "schema nesting exceeds 256 levels"}}
	}
	schema, ok := rawSchema.(map[string]any)
	if !ok {
		if allowed, boolean := rawSchema.(bool); boolean && allowed {
			return nil
		}
		return []Violation{{Path: path, Message: "schema is not an object"}}
	}
	if nullable, _ := schema["nullable"].(bool); nullable && instance == nil {
		return nil
	}
	if ref, ok := schema["$ref"].(string); ok {
		target, err := resolveRef(root, ref)
		if err != nil {
			return []Violation{{Path: path, Message: err.Error()}}
		}
		return validate(root, target, instance, path, depth+1)
	}

	var out []Violation
	if expected, exists := schema["const"]; exists && !jsonEqual(expected, instance) {
		out = append(out, Violation{path, fmt.Sprintf("must equal %v", expected)})
	}
	if values, ok := schema["enum"].([]any); ok {
		matched := false
		for _, value := range values {
			if jsonEqual(value, instance) {
				matched = true
				break
			}
		}
		if !matched {
			out = append(out, Violation{path, "value is not in enum"})
		}
	}
	for _, keyword := range []string{"allOf", "anyOf", "oneOf"} {
		branches, ok := schema[keyword].([]any)
		if !ok {
			continue
		}
		successes := 0
		var allErrors []Violation
		for _, branch := range branches {
			errs := validate(root, branch, instance, path, depth+1)
			if len(errs) == 0 {
				successes++
			} else {
				allErrors = append(allErrors, errs...)
			}
		}
		switch keyword {
		case "allOf":
			out = append(out, allErrors...)
		case "anyOf":
			if successes == 0 {
				out = append(out, Violation{path, "does not satisfy anyOf"})
			}
		case "oneOf":
			if successes != 1 {
				out = append(out, Violation{path, fmt.Sprintf("satisfies %d oneOf branches", successes)})
			}
		}
	}
	if negated, ok := schema["not"]; ok && len(validate(root, negated, instance, path, depth+1)) == 0 {
		out = append(out, Violation{path, "satisfies forbidden not schema"})
	}

	typeName, _ := schema["type"].(string)
	if typeName != "" && !hasType(instance, typeName) {
		return append(out, Violation{path, fmt.Sprintf("must be %s", typeName)})
	}

	switch value := instance.(type) {
	case map[string]any:
		properties, _ := schema["properties"].(map[string]any)
		if required, ok := schema["required"].([]any); ok {
			for _, item := range required {
				name, _ := item.(string)
				if _, exists := value[name]; !exists {
					out = append(out, Violation{join(path, name), "required property is missing"})
				}
			}
		}
		for name, child := range value {
			if childSchema, exists := properties[name]; exists {
				out = append(out, validate(root, childSchema, child, join(path, name), depth+1)...)
				continue
			}
			if additional, exists := schema["additionalProperties"]; exists {
				switch rule := additional.(type) {
				case bool:
					if !rule {
						out = append(out, Violation{join(path, name), "additional property is forbidden"})
					}
				case map[string]any:
					out = append(out, validate(root, rule, child, join(path, name), depth+1)...)
				}
			}
		}
		out = append(out, propertyCount(schema, len(value), path)...)
	case []any:
		out = append(out, arrayBounds(schema, len(value), path)...)
		if unique, _ := schema["uniqueItems"].(bool); unique {
			for i := range value {
				for j := 0; j < i; j++ {
					if jsonEqual(value[i], value[j]) {
						out = append(out, Violation{index(path, i), fmt.Sprintf("duplicates item %d", j)})
					}
				}
			}
		}
		if itemSchema, exists := schema["items"]; exists {
			for i, item := range value {
				out = append(out, validate(root, itemSchema, item, index(path, i), depth+1)...)
			}
		}
	case string:
		length := utf8.RuneCountInString(value)
		if min, ok := integerKeyword(schema, "minLength"); ok && int64(length) < min {
			out = append(out, Violation{path, fmt.Sprintf("length must be at least %d", min)})
		}
		if max, ok := integerKeyword(schema, "maxLength"); ok && int64(length) > max {
			out = append(out, Violation{path, fmt.Sprintf("length must be at most %d", max)})
		}
		if pattern, ok := schema["pattern"].(string); ok {
			re, err := regexp.Compile(pattern)
			if err != nil {
				out = append(out, Violation{path, "invalid schema pattern: " + err.Error()})
			} else if !re.MatchString(value) {
				out = append(out, Violation{path, "does not match pattern"})
			}
		}
	case json.Number:
		out = append(out, numericBounds(schema, value, path)...)
	}
	return out
}

func resolveRef(root any, ref string) (any, error) {
	if ref == "#" {
		return root, nil
	}
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("unsupported non-local reference %q", ref)
	}
	current := root
	for _, encoded := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		part := strings.ReplaceAll(strings.ReplaceAll(encoded, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("reference %q crosses a non-object", ref)
		}
		current, ok = object[part]
		if !ok {
			return nil, fmt.Errorf("reference %q does not exist", ref)
		}
	}
	return current, nil
}

func hasType(value any, expected string) bool {
	switch expected {
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
		value, success := new(big.Rat).SetString(number.String())
		return success && value.IsInt()
	case "null":
		return value == nil
	default:
		return false
	}
}

func numericBounds(schema map[string]any, number json.Number, path string) []Violation {
	value, ok := new(big.Rat).SetString(number.String())
	if !ok {
		return []Violation{{path, "invalid JSON number"}}
	}
	var out []Violation
	for _, bound := range []struct {
		name string
		cmp  func(int) bool
		text string
	}{
		{"minimum", func(cmp int) bool { return cmp < 0 }, "below minimum"},
		{"maximum", func(cmp int) bool { return cmp > 0 }, "above maximum"},
	} {
		raw, exists := schema[bound.name]
		if !exists {
			continue
		}
		limit, valid := rat(raw)
		if valid && bound.cmp(value.Cmp(limit)) {
			out = append(out, Violation{path, bound.text})
		}
	}
	return out
}

func rat(raw any) (*big.Rat, bool) {
	switch value := raw.(type) {
	case json.Number:
		parsed, ok := new(big.Rat).SetString(value.String())
		return parsed, ok
	case float64:
		parsed, ok := new(big.Rat).SetString(strconv.FormatFloat(value, 'g', -1, 64))
		return parsed, ok
	default:
		return nil, false
	}
}

func integerKeyword(schema map[string]any, name string) (int64, bool) {
	raw, exists := schema[name]
	if !exists {
		return 0, false
	}
	switch value := raw.(type) {
	case json.Number:
		parsed, err := value.Int64()
		return parsed, err == nil
	case float64:
		return int64(value), value == float64(int64(value))
	default:
		return 0, false
	}
}

func arrayBounds(schema map[string]any, length int, path string) []Violation {
	return countBounds(schema, length, path, "minItems", "maxItems", "items")
}

func propertyCount(schema map[string]any, length int, path string) []Violation {
	return countBounds(schema, length, path, "minProperties", "maxProperties", "properties")
}

func countBounds(schema map[string]any, length int, path, minName, maxName, noun string) []Violation {
	var out []Violation
	if min, ok := integerKeyword(schema, minName); ok && int64(length) < min {
		out = append(out, Violation{path, fmt.Sprintf("must contain at least %d %s", min, noun)})
	}
	if max, ok := integerKeyword(schema, maxName); ok && int64(length) > max {
		out = append(out, Violation{path, fmt.Sprintf("must contain at most %d %s", max, noun)})
	}
	return out
}

func jsonEqual(left, right any) bool {
	leftJSON, leftErr := json.Marshal(left)
	rightJSON, rightErr := json.Marshal(right)
	return leftErr == nil && rightErr == nil && bytes.Equal(leftJSON, rightJSON)
}

func join(path, name string) string {
	return path + "." + name
}

func index(path string, i int) string {
	return fmt.Sprintf("%s[%d]", path, i)
}
