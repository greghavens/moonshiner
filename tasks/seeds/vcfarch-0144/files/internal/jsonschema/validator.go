// Package jsonschema implements the small, deterministic JSON Schema/OpenAPI
// validation surface needed by the protected architecture verifier. It resolves
// constraints from the vendored documents at runtime; it does not contain a
// second, hand-written SddcSpec.
package jsonschema

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"
)

type Validator struct {
	root any
}

func ReadFile(path string) (any, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return Decode(b)
}

func Decode(b []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(b))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	if decoder.More() {
		return nil, errors.New("more than one JSON value")
	}
	return value, nil
}

func New(root any) *Validator {
	return &Validator{root: root}
}

func (v *Validator) ValidateAt(pointer string, instance any) error {
	schema, err := resolvePointer(v.root, pointer)
	if err != nil {
		return err
	}
	return v.validate(schema, instance, "$", map[string]bool{})
}

func (v *Validator) Validate(instance any) error {
	return v.validate(v.root, instance, "$", map[string]bool{})
}

func (v *Validator) validate(rawSchema, instance any, path string, refs map[string]bool) error {
	if booleanSchema, ok := rawSchema.(bool); ok {
		if booleanSchema {
			return nil
		}
		return fmt.Errorf("%s: rejected by false schema", path)
	}
	schema, ok := rawSchema.(map[string]any)
	if !ok {
		return fmt.Errorf("%s: schema is not an object", path)
	}

	if ref, ok := schema["$ref"].(string); ok {
		if !strings.HasPrefix(ref, "#") {
			return fmt.Errorf("%s: external schema reference %q is not supported", path, ref)
		}
		key := ref + "@" + path
		if refs[key] {
			return fmt.Errorf("%s: cyclic schema reference %q", path, ref)
		}
		refs[key] = true
		defer delete(refs, key)
		resolved, err := resolvePointer(v.root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		if err := v.validate(resolved, instance, path, refs); err != nil {
			return err
		}
	}

	if nullable, _ := schema["nullable"].(bool); nullable && instance == nil {
		return nil
	}
	if raw, ok := schema["const"]; ok && !jsonEqual(raw, instance) {
		return fmt.Errorf("%s: value does not equal const", path)
	}
	if values, ok := schema["enum"].([]any); ok {
		matched := false
		for _, candidate := range values {
			if jsonEqual(candidate, instance) {
				matched = true
				break
			}
		}
		if !matched {
			return fmt.Errorf("%s: value is not in enum", path)
		}
	}

	if alternatives, ok := schema["allOf"].([]any); ok {
		for _, alternative := range alternatives {
			if err := v.validate(alternative, instance, path, refs); err != nil {
				return err
			}
		}
	}
	if alternatives, ok := schema["anyOf"].([]any); ok {
		matched := 0
		for _, alternative := range alternatives {
			if v.validate(alternative, instance, path, cloneRefs(refs)) == nil {
				matched++
			}
		}
		if matched == 0 {
			return fmt.Errorf("%s: no anyOf schema matched", path)
		}
	}
	if alternatives, ok := schema["oneOf"].([]any); ok {
		matched := 0
		for _, alternative := range alternatives {
			if v.validate(alternative, instance, path, cloneRefs(refs)) == nil {
				matched++
			}
		}
		if matched != 1 {
			return fmt.Errorf("%s: expected one oneOf match, got %d", path, matched)
		}
	}
	if excluded, ok := schema["not"]; ok && v.validate(excluded, instance, path, cloneRefs(refs)) == nil {
		return fmt.Errorf("%s: matched excluded schema", path)
	}

	if rawType, ok := schema["type"]; ok && !matchesType(rawType, instance) {
		return fmt.Errorf("%s: expected type %v, got %T", path, rawType, instance)
	}

	switch value := instance.(type) {
	case map[string]any:
		if err := validateObjectShape(schema, value, path); err != nil {
			return err
		}
		properties, _ := schema["properties"].(map[string]any)
		for key, propertySchema := range properties {
			propertyValue, exists := value[key]
			if !exists {
				continue
			}
			if err := v.validate(propertySchema, propertyValue, childPath(path, key), refs); err != nil {
				return err
			}
		}
		if additional, exists := schema["additionalProperties"]; exists {
			for key, propertyValue := range value {
				if _, known := properties[key]; known {
					continue
				}
				switch additionalSchema := additional.(type) {
				case bool:
					if !additionalSchema {
						return fmt.Errorf("%s: additional property %q is forbidden", path, key)
					}
				case map[string]any:
					if err := v.validate(additionalSchema, propertyValue, childPath(path, key), refs); err != nil {
						return err
					}
				}
			}
		}
	case []any:
		if min, ok := integerKeyword(schema, "minItems"); ok && len(value) < min {
			return fmt.Errorf("%s: has %d items, minimum is %d", path, len(value), min)
		}
		if max, ok := integerKeyword(schema, "maxItems"); ok && len(value) > max {
			return fmt.Errorf("%s: has %d items, maximum is %d", path, len(value), max)
		}
		if unique, _ := schema["uniqueItems"].(bool); unique {
			seen := map[string]bool{}
			for _, item := range value {
				encoded, _ := json.Marshal(item)
				key := string(encoded)
				if seen[key] {
					return fmt.Errorf("%s: array items are not unique", path)
				}
				seen[key] = true
			}
		}
		if itemSchema, ok := schema["items"]; ok {
			for i, item := range value {
				if err := v.validate(itemSchema, item, fmt.Sprintf("%s[%d]", path, i), refs); err != nil {
					return err
				}
			}
		}
	case string:
		length := utf8.RuneCountInString(value)
		if min, ok := integerKeyword(schema, "minLength"); ok && length < min {
			return fmt.Errorf("%s: string length %d is below %d", path, length, min)
		}
		if max, ok := integerKeyword(schema, "maxLength"); ok && length > max {
			return fmt.Errorf("%s: string length %d exceeds %d", path, length, max)
		}
		if pattern, ok := schema["pattern"].(string); ok {
			re, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: invalid schema pattern: %w", path, err)
			}
			if !re.MatchString(value) {
				return fmt.Errorf("%s: string does not match %q", path, pattern)
			}
		}
	default:
		if number, ok := asFloat(instance); ok {
			if minimum, ok := numberKeyword(schema, "minimum"); ok {
				exclusive, _ := schema["exclusiveMinimum"].(bool)
				if number < minimum || (exclusive && number == minimum) {
					return fmt.Errorf("%s: number %v is below minimum %v", path, number, minimum)
				}
			}
			if maximum, ok := numberKeyword(schema, "maximum"); ok {
				exclusive, _ := schema["exclusiveMaximum"].(bool)
				if number > maximum || (exclusive && number == maximum) {
					return fmt.Errorf("%s: number %v exceeds maximum %v", path, number, maximum)
				}
			}
			if multiple, ok := numberKeyword(schema, "multipleOf"); ok && multiple != 0 {
				quotient := number / multiple
				if math.Abs(quotient-math.Round(quotient)) > 1e-9 {
					return fmt.Errorf("%s: number %v is not a multiple of %v", path, number, multiple)
				}
			}
		}
	}
	return nil
}

func validateObjectShape(schema, value map[string]any, path string) error {
	if required, ok := schema["required"].([]any); ok {
		for _, rawName := range required {
			name, ok := rawName.(string)
			if ok {
				if _, exists := value[name]; !exists {
					return fmt.Errorf("%s: required property %q is missing", path, name)
				}
			}
		}
	}
	if min, ok := integerKeyword(schema, "minProperties"); ok && len(value) < min {
		return fmt.Errorf("%s: has %d properties, minimum is %d", path, len(value), min)
	}
	if max, ok := integerKeyword(schema, "maxProperties"); ok && len(value) > max {
		return fmt.Errorf("%s: has %d properties, maximum is %d", path, len(value), max)
	}
	return nil
}

func matchesType(rawType, value any) bool {
	types := []string{}
	switch typed := rawType.(type) {
	case string:
		types = append(types, typed)
	case []any:
		for _, item := range typed {
			if name, ok := item.(string); ok {
				types = append(types, name)
			}
		}
	}
	for _, name := range types {
		switch name {
		case "null":
			if value == nil {
				return true
			}
		case "object":
			_, ok := value.(map[string]any)
			if ok {
				return true
			}
		case "array":
			_, ok := value.([]any)
			if ok {
				return true
			}
		case "string":
			_, ok := value.(string)
			if ok {
				return true
			}
		case "boolean":
			_, ok := value.(bool)
			if ok {
				return true
			}
		case "number":
			_, ok := asFloat(value)
			if ok {
				return true
			}
		case "integer":
			if number, ok := asFloat(value); ok && math.Trunc(number) == number {
				return true
			}
		}
	}
	return false
}

func resolvePointer(root any, pointer string) (any, error) {
	if pointer == "#" || pointer == "" {
		return root, nil
	}
	if !strings.HasPrefix(pointer, "#/") {
		return nil, fmt.Errorf("invalid local JSON pointer %q", pointer)
	}
	current := root
	for _, token := range strings.Split(strings.TrimPrefix(pointer, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		switch value := current.(type) {
		case map[string]any:
			next, ok := value[token]
			if !ok {
				return nil, fmt.Errorf("JSON pointer %q does not exist", pointer)
			}
			current = next
		case []any:
			index, err := strconv.Atoi(token)
			if err != nil || index < 0 || index >= len(value) {
				return nil, fmt.Errorf("JSON pointer %q has invalid array index", pointer)
			}
			current = value[index]
		default:
			return nil, fmt.Errorf("JSON pointer %q traverses a scalar", pointer)
		}
	}
	return current, nil
}

func integerKeyword(schema map[string]any, name string) (int, bool) {
	number, ok := asFloat(schema[name])
	return int(number), ok
}

func numberKeyword(schema map[string]any, name string) (float64, bool) {
	return asFloat(schema[name])
}

func asFloat(value any) (float64, bool) {
	switch number := value.(type) {
	case json.Number:
		parsed, err := number.Float64()
		return parsed, err == nil
	case float64:
		return number, true
	case float32:
		return float64(number), true
	case int:
		return float64(number), true
	case int64:
		return float64(number), true
	case int32:
		return float64(number), true
	default:
		return 0, false
	}
}

func jsonEqual(left, right any) bool {
	leftNumber, leftIsNumber := asFloat(left)
	rightNumber, rightIsNumber := asFloat(right)
	if leftIsNumber && rightIsNumber {
		return leftNumber == rightNumber
	}
	return reflect.DeepEqual(left, right)
}

func childPath(parent, child string) string {
	if regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`).MatchString(child) {
		return parent + "." + child
	}
	return fmt.Sprintf("%s[%q]", parent, child)
}

func cloneRefs(refs map[string]bool) map[string]bool {
	cloned := make(map[string]bool, len(refs))
	for key, value := range refs {
		cloned[key] = value
	}
	return cloned
}
