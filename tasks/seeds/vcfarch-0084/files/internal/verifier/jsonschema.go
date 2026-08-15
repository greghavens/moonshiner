package verifier

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"reflect"
	"regexp"
	"strconv"
	"strings"
)

func validateSddcSpec(document, openAPI []byte) error {
	value, err := decodeJSON(document)
	if err != nil {
		return fmt.Errorf("decode architecture: %w", err)
	}
	rootValue, err := decodeJSON(openAPI)
	if err != nil {
		return fmt.Errorf("decode installer OpenAPI: %w", err)
	}
	root, ok := rootValue.(map[string]any)
	if !ok {
		return fmt.Errorf("installer OpenAPI root is not an object")
	}
	schema, err := objectPath(root, "components", "schemas", "SddcSpec")
	if err != nil {
		return err
	}
	return validateValue(value, schema, root, "$")
}

func validateJSONSchema(document, schemaDocument []byte) error {
	value, err := decodeJSON(document)
	if err != nil {
		return fmt.Errorf("decode document: %w", err)
	}
	rootValue, err := decodeJSON(schemaDocument)
	if err != nil {
		return fmt.Errorf("decode schema: %w", err)
	}
	root, ok := rootValue.(map[string]any)
	if !ok {
		return fmt.Errorf("schema root is not an object")
	}
	return validateValue(value, root, root, "$")
}

func decodeJSON(raw []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		if err == nil {
			return nil, fmt.Errorf("multiple JSON values")
		}
		return nil, err
	}
	return value, nil
}

func objectPath(root map[string]any, parts ...string) (map[string]any, error) {
	var current any = root
	for _, part := range parts {
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("schema path %q is not an object", strings.Join(parts, "/"))
		}
		current, ok = object[part]
		if !ok {
			return nil, fmt.Errorf("schema path %q is missing", strings.Join(parts, "/"))
		}
	}
	object, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("schema path %q is not an object", strings.Join(parts, "/"))
	}
	return object, nil
}

func validateValue(value any, schema map[string]any, root map[string]any, path string) error {
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolveRef(root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		return validateValue(value, resolved, root, path)
	}
	if nullable, _ := schema["nullable"].(bool); nullable && value == nil {
		return nil
	}
	if constant, ok := schema["const"]; ok && !reflect.DeepEqual(value, constant) {
		return fmt.Errorf("%s: value does not equal const", path)
	}
	if enumValues, ok := schema["enum"].([]any); ok {
		matched := false
		for _, candidate := range enumValues {
			if reflect.DeepEqual(value, candidate) {
				matched = true
				break
			}
		}
		if !matched {
			return fmt.Errorf("%s: value is not in enum", path)
		}
	}
	for _, keyword := range []string{"allOf", "anyOf", "oneOf"} {
		if alternatives, ok := schema[keyword].([]any); ok {
			matches := 0
			var lastErr error
			for _, alternative := range alternatives {
				candidate, ok := alternative.(map[string]any)
				if !ok {
					return fmt.Errorf("%s: %s entry is not an object", path, keyword)
				}
				if err := validateValue(value, candidate, root, path); err == nil {
					matches++
				} else {
					lastErr = err
				}
			}
			switch keyword {
			case "allOf":
				if matches != len(alternatives) {
					return fmt.Errorf("%s: allOf failed: %v", path, lastErr)
				}
			case "anyOf":
				if matches == 0 {
					return fmt.Errorf("%s: anyOf failed: %v", path, lastErr)
				}
			case "oneOf":
				if matches != 1 {
					return fmt.Errorf("%s: oneOf matched %d alternatives", path, matches)
				}
			}
		}
	}
	if negative, ok := schema["not"].(map[string]any); ok {
		if validateValue(value, negative, root, path) == nil {
			return fmt.Errorf("%s: value matches forbidden schema", path)
		}
	}

	if typeName, ok := schema["type"].(string); ok {
		if err := validateType(value, typeName, path); err != nil {
			return err
		}
	}

	switch typed := value.(type) {
	case map[string]any:
		if err := validateObject(typed, schema, root, path); err != nil {
			return err
		}
	case []any:
		if err := validateArray(typed, schema, root, path); err != nil {
			return err
		}
	case string:
		if err := validateString(typed, schema, path); err != nil {
			return err
		}
	case json.Number:
		if err := validateNumber(typed, schema, path); err != nil {
			return err
		}
	}
	return nil
}

func resolveRef(root map[string]any, ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("unsupported non-local $ref %q", ref)
	}
	var current any = root
	for _, rawPart := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		part := strings.ReplaceAll(strings.ReplaceAll(rawPart, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("$ref %q traverses a non-object", ref)
		}
		current, ok = object[part]
		if !ok {
			return nil, fmt.Errorf("unresolved $ref %q", ref)
		}
	}
	resolved, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("$ref %q does not resolve to an object", ref)
	}
	return resolved, nil
}

func validateType(value any, typeName, path string) error {
	valid := false
	switch typeName {
	case "object":
		_, valid = value.(map[string]any)
	case "array":
		_, valid = value.([]any)
	case "string":
		_, valid = value.(string)
	case "integer":
		if number, ok := value.(json.Number); ok {
			_, err := strconv.ParseInt(number.String(), 10, 64)
			valid = err == nil
		}
	case "number":
		_, valid = value.(json.Number)
	case "boolean":
		_, valid = value.(bool)
	case "null":
		valid = value == nil
	default:
		return fmt.Errorf("%s: unsupported schema type %q", path, typeName)
	}
	if !valid {
		return fmt.Errorf("%s: expected %s", path, typeName)
	}
	return nil
}

func validateObject(value map[string]any, schema, root map[string]any, path string) error {
	if required, ok := schema["required"].([]any); ok {
		for _, entry := range required {
			name, ok := entry.(string)
			if !ok {
				return fmt.Errorf("%s: required entry is not a string", path)
			}
			if _, exists := value[name]; !exists {
				return fmt.Errorf("%s: required property %q is missing", path, name)
			}
		}
	}
	properties, _ := schema["properties"].(map[string]any)
	for name, childValue := range value {
		childSchemaValue, known := properties[name]
		if !known {
			if allow, ok := schema["additionalProperties"].(bool); ok && !allow {
				return fmt.Errorf("%s: additional property %q is not allowed", path, name)
			}
			continue
		}
		childSchema, ok := childSchemaValue.(map[string]any)
		if !ok {
			return fmt.Errorf("%s.%s: property schema is not an object", path, name)
		}
		if err := validateValue(childValue, childSchema, root, path+"."+name); err != nil {
			return err
		}
	}
	return nil
}

func validateArray(value []any, schema, root map[string]any, path string) error {
	if min, ok := schemaInt(schema["minItems"]); ok && len(value) < min {
		return fmt.Errorf("%s: has %d items, minimum is %d", path, len(value), min)
	}
	if max, ok := schemaInt(schema["maxItems"]); ok && len(value) > max {
		return fmt.Errorf("%s: has %d items, maximum is %d", path, len(value), max)
	}
	if unique, _ := schema["uniqueItems"].(bool); unique {
		seen := map[string]struct{}{}
		for _, entry := range value {
			encoded, _ := json.Marshal(entry)
			key := string(encoded)
			if _, exists := seen[key]; exists {
				return fmt.Errorf("%s: contains duplicate items", path)
			}
			seen[key] = struct{}{}
		}
	}
	if itemValue, ok := schema["items"]; ok {
		itemSchema, ok := itemValue.(map[string]any)
		if !ok {
			return fmt.Errorf("%s: items schema is not an object", path)
		}
		for index, entry := range value {
			if err := validateValue(entry, itemSchema, root, fmt.Sprintf("%s[%d]", path, index)); err != nil {
				return err
			}
		}
	}
	return nil
}

func validateString(value string, schema map[string]any, path string) error {
	length := len([]rune(value))
	if min, ok := schemaInt(schema["minLength"]); ok && length < min {
		return fmt.Errorf("%s: length %d is less than %d", path, length, min)
	}
	if max, ok := schemaInt(schema["maxLength"]); ok && length > max {
		return fmt.Errorf("%s: length %d exceeds %d", path, length, max)
	}
	if pattern, ok := schema["pattern"].(string); ok {
		expression, err := regexp.Compile(pattern)
		if err != nil {
			return fmt.Errorf("%s: invalid schema pattern: %w", path, err)
		}
		if !expression.MatchString(value) {
			return fmt.Errorf("%s: does not match pattern %q", path, pattern)
		}
	}
	return nil
}

func validateNumber(value json.Number, schema map[string]any, path string) error {
	number, err := value.Float64()
	if err != nil {
		return fmt.Errorf("%s: invalid number: %w", path, err)
	}
	if minimum, ok := schemaFloat(schema["minimum"]); ok && number < minimum {
		return fmt.Errorf("%s: %v is below minimum %v", path, number, minimum)
	}
	if maximum, ok := schemaFloat(schema["maximum"]); ok && number > maximum {
		return fmt.Errorf("%s: %v exceeds maximum %v", path, number, maximum)
	}
	return nil
}

func schemaInt(value any) (int, bool) {
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
