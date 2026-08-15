package verifier

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
)

func ValidateOpenAPI(document, instance []byte, component string) error {
	root, err := decode(document)
	if err != nil {
		return fmt.Errorf("decode OpenAPI document: %w", err)
	}
	value, err := decode(instance)
	if err != nil {
		return fmt.Errorf("decode instance: %w", err)
	}
	rootObject, ok := root.(map[string]any)
	if !ok {
		return fmt.Errorf("OpenAPI document is not an object")
	}
	components, ok := objectAt(rootObject, "components", "schemas")
	if !ok {
		return fmt.Errorf("OpenAPI document has no components.schemas")
	}
	schema, ok := components[component].(map[string]any)
	if !ok {
		return fmt.Errorf("OpenAPI component %q is missing", component)
	}
	return validate(rootObject, schema, value, "$", true)
}

func ValidateJSONSchema(schemaDocument, instance []byte) error {
	root, err := decode(schemaDocument)
	if err != nil {
		return fmt.Errorf("decode JSON Schema: %w", err)
	}
	value, err := decode(instance)
	if err != nil {
		return fmt.Errorf("decode instance: %w", err)
	}
	rootObject, ok := root.(map[string]any)
	if !ok {
		return fmt.Errorf("JSON Schema is not an object")
	}
	return validate(rootObject, rootObject, value, "$", false)
}

func decode(data []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	if decoder.More() {
		return nil, fmt.Errorf("multiple JSON values")
	}
	return value, nil
}

func validate(root, schema map[string]any, value any, path string, openAPI bool) error {
	if nullable, _ := schema["nullable"].(bool); nullable && value == nil {
		return nil
	}
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolve(root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		if err := validate(root, resolved, value, path, openAPI); err != nil {
			return err
		}
	}
	for _, keyword := range []string{"allOf", "anyOf", "oneOf"} {
		branches, ok := schema[keyword].([]any)
		if !ok {
			continue
		}
		matches := 0
		var last error
		for _, raw := range branches {
			branch, ok := raw.(map[string]any)
			if !ok {
				return fmt.Errorf("%s: %s entry is not a schema", path, keyword)
			}
			if err := validate(root, branch, value, path, openAPI); err == nil {
				matches++
			} else {
				last = err
			}
		}
		switch keyword {
		case "allOf":
			if matches != len(branches) {
				return fmt.Errorf("%s: allOf failed: %v", path, last)
			}
		case "anyOf":
			if matches == 0 {
				return fmt.Errorf("%s: anyOf failed: %v", path, last)
			}
		case "oneOf":
			if matches != 1 {
				return fmt.Errorf("%s: oneOf matched %d branches", path, matches)
			}
		}
	}
	if enum, ok := schema["enum"].([]any); ok {
		matched := false
		for _, allowed := range enum {
			if jsonEqual(allowed, value) {
				matched = true
				break
			}
		}
		if !matched {
			return fmt.Errorf("%s: value is not in enum", path)
		}
	}
	if constant, ok := schema["const"]; ok && !jsonEqual(constant, value) {
		return fmt.Errorf("%s: value does not equal const", path)
	}
	typeName, _ := schema["type"].(string)
	switch typeName {
	case "object":
		object, ok := value.(map[string]any)
		if !ok {
			return fmt.Errorf("%s: expected object, got %T", path, value)
		}
		if err := validateObject(root, schema, object, path, openAPI); err != nil {
			return err
		}
	case "array":
		array, ok := value.([]any)
		if !ok {
			return fmt.Errorf("%s: expected array, got %T", path, value)
		}
		if err := validateArray(root, schema, array, path, openAPI); err != nil {
			return err
		}
	case "string":
		text, ok := value.(string)
		if !ok {
			return fmt.Errorf("%s: expected string, got %T", path, value)
		}
		if err := validateString(schema, text, path); err != nil {
			return err
		}
	case "integer":
		number, ok := value.(json.Number)
		if !ok {
			return fmt.Errorf("%s: expected integer, got %T", path, value)
		}
		parsed, err := strconv.ParseFloat(number.String(), 64)
		if err != nil || math.Trunc(parsed) != parsed {
			return fmt.Errorf("%s: expected integer, got %s", path, number)
		}
		if err := validateNumber(schema, parsed, path); err != nil {
			return err
		}
	case "number":
		number, ok := value.(json.Number)
		if !ok {
			return fmt.Errorf("%s: expected number, got %T", path, value)
		}
		parsed, err := strconv.ParseFloat(number.String(), 64)
		if err != nil {
			return fmt.Errorf("%s: invalid number %s", path, number)
		}
		if err := validateNumber(schema, parsed, path); err != nil {
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

func validateObject(root, schema, object map[string]any, path string, openAPI bool) error {
	if required, ok := schema["required"].([]any); ok {
		for _, raw := range required {
			name, _ := raw.(string)
			if _, exists := object[name]; !exists {
				return fmt.Errorf("%s: required property %q is missing", path, name)
			}
		}
	}
	properties, _ := schema["properties"].(map[string]any)
	for name, value := range object {
		property, known := properties[name].(map[string]any)
		if known {
			if err := validate(root, property, value, path+"."+name, openAPI); err != nil {
				return err
			}
			continue
		}
		if additional, exists := schema["additionalProperties"]; exists {
			switch typed := additional.(type) {
			case bool:
				if !typed {
					return fmt.Errorf("%s: additional property %q is not allowed", path, name)
				}
			case map[string]any:
				if err := validate(root, typed, value, path+"."+name, openAPI); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

func validateArray(root, schema map[string]any, array []any, path string, openAPI bool) error {
	if min, ok := numberKeyword(schema, "minItems"); ok && float64(len(array)) < min {
		return fmt.Errorf("%s: has %d items, minimum is %.0f", path, len(array), min)
	}
	if max, ok := numberKeyword(schema, "maxItems"); ok && float64(len(array)) > max {
		return fmt.Errorf("%s: has %d items, maximum is %.0f", path, len(array), max)
	}
	if unique, _ := schema["uniqueItems"].(bool); unique {
		seen := map[string]bool{}
		for _, item := range array {
			encoded, _ := json.Marshal(item)
			key := string(encoded)
			if seen[key] {
				return fmt.Errorf("%s: duplicate array item", path)
			}
			seen[key] = true
		}
	}
	if items, ok := schema["items"].(map[string]any); ok {
		for index, item := range array {
			if err := validate(root, items, item, fmt.Sprintf("%s[%d]", path, index), openAPI); err != nil {
				return err
			}
		}
	}
	return nil
}

func validateString(schema map[string]any, text, path string) error {
	length := float64(len([]rune(text)))
	if min, ok := numberKeyword(schema, "minLength"); ok && length < min {
		return fmt.Errorf("%s: string is shorter than %.0f", path, min)
	}
	if max, ok := numberKeyword(schema, "maxLength"); ok && length > max {
		return fmt.Errorf("%s: string is longer than %.0f", path, max)
	}
	if pattern, ok := schema["pattern"].(string); ok {
		expression, err := regexp.Compile(pattern)
		if err != nil {
			return fmt.Errorf("%s: schema pattern %q is invalid: %w", path, pattern, err)
		}
		if !expression.MatchString(text) {
			return fmt.Errorf("%s: %q does not match %q", path, text, pattern)
		}
	}
	return nil
}

func validateNumber(schema map[string]any, number float64, path string) error {
	if min, ok := numberKeyword(schema, "minimum"); ok && number < min {
		return fmt.Errorf("%s: %v is below minimum %v", path, number, min)
	}
	if max, ok := numberKeyword(schema, "maximum"); ok && number > max {
		return fmt.Errorf("%s: %v is above maximum %v", path, number, max)
	}
	if min, ok := numberKeyword(schema, "exclusiveMinimum"); ok && number <= min {
		return fmt.Errorf("%s: %v is not above %v", path, number, min)
	}
	if max, ok := numberKeyword(schema, "exclusiveMaximum"); ok && number >= max {
		return fmt.Errorf("%s: %v is not below %v", path, number, max)
	}
	return nil
}

func resolve(root map[string]any, ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("external reference %q is not supported", ref)
	}
	var current any = root
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("reference %q crosses a non-object", ref)
		}
		current, ok = object[token]
		if !ok {
			return nil, fmt.Errorf("reference %q is missing token %q", ref, token)
		}
	}
	resolved, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("reference %q is not a schema object", ref)
	}
	return resolved, nil
}

func objectAt(root map[string]any, names ...string) (map[string]any, bool) {
	current := root
	for _, name := range names {
		next, ok := current[name].(map[string]any)
		if !ok {
			return nil, false
		}
		current = next
	}
	return current, true
}

func numberKeyword(schema map[string]any, name string) (float64, bool) {
	raw, ok := schema[name]
	if !ok {
		return 0, false
	}
	switch value := raw.(type) {
	case json.Number:
		parsed, err := strconv.ParseFloat(value.String(), 64)
		return parsed, err == nil
	case float64:
		return value, true
	default:
		return 0, false
	}
}

func jsonEqual(left, right any) bool {
	l, _ := json.Marshal(left)
	r, _ := json.Marshal(right)
	return bytes.Equal(l, r)
}
