package verifier

import (
	"encoding/json"
	"fmt"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// ValidateJSONSchema validates the subset of JSON Schema 2020-12 used by the
// installer specification. Keeping it here avoids network-fetched Go modules.
func ValidateJSONSchema(document any, schema, root map[string]any, path string) []string {
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolveLocalRef(root, ref)
		if err != nil {
			return []string{fmt.Sprintf("%s: %v", path, err)}
		}
		return ValidateJSONSchema(document, resolved, root, path)
	}

	var errs []string
	if rawType, ok := schema["type"]; ok && !matchesType(document, rawType) {
		return []string{fmt.Sprintf("%s: expected type %v, got %T", path, rawType, document)}
	}
	if want, ok := schema["const"]; ok && !jsonEqual(document, want) {
		errs = append(errs, fmt.Sprintf("%s: value does not equal const %v", path, want))
	}
	if values, ok := schema["enum"].([]any); ok {
		matched := false
		for _, candidate := range values {
			if jsonEqual(document, candidate) {
				matched = true
				break
			}
		}
		if !matched {
			errs = append(errs, fmt.Sprintf("%s: value %v is not in enum", path, document))
		}
	}

	switch value := document.(type) {
	case map[string]any:
		errs = append(errs, validateObject(value, schema, root, path)...)
	case []any:
		errs = append(errs, validateArray(value, schema, root, path)...)
	case string:
		errs = append(errs, validateString(value, schema, path)...)
	case json.Number:
		if minimum, ok := numberAsFloat(schema["minimum"]); ok {
			actual, _ := value.Float64()
			if actual < minimum {
				errs = append(errs, fmt.Sprintf("%s: %v is below minimum %v", path, actual, minimum))
			}
		}
	}
	return errs
}

func validateObject(value map[string]any, schema, root map[string]any, path string) []string {
	var errs []string
	properties, _ := schema["properties"].(map[string]any)
	if required, ok := schema["required"].([]any); ok {
		for _, item := range required {
			name, _ := item.(string)
			if _, exists := value[name]; !exists {
				errs = append(errs, fmt.Sprintf("%s: missing required property %q", path, name))
			}
		}
	}
	for name, child := range value {
		childSchema, exists := properties[name]
		if !exists {
			if additional, ok := schema["additionalProperties"].(bool); ok && !additional {
				errs = append(errs, fmt.Sprintf("%s: additional property %q is not allowed", path, name))
			}
			continue
		}
		childMap, ok := childSchema.(map[string]any)
		if !ok {
			errs = append(errs, fmt.Sprintf("%s.%s: invalid schema node", path, name))
			continue
		}
		errs = append(errs, ValidateJSONSchema(child, childMap, root, path+"."+name)...)
	}
	return errs
}

func validateArray(value []any, schema, root map[string]any, path string) []string {
	var errs []string
	if minimum, ok := integerKeyword(schema["minItems"]); ok && len(value) < minimum {
		errs = append(errs, fmt.Sprintf("%s: has %d items, minimum is %d", path, len(value), minimum))
	}
	if maximum, ok := integerKeyword(schema["maxItems"]); ok && len(value) > maximum {
		errs = append(errs, fmt.Sprintf("%s: has %d items, maximum is %d", path, len(value), maximum))
	}
	if unique, _ := schema["uniqueItems"].(bool); unique {
		seen := map[string]bool{}
		for index, item := range value {
			encoded, _ := json.Marshal(item)
			key := string(encoded)
			if seen[key] {
				errs = append(errs, fmt.Sprintf("%s[%d]: duplicate array item", path, index))
			}
			seen[key] = true
		}
	}
	if rawItems, ok := schema["items"].(map[string]any); ok {
		for index, item := range value {
			errs = append(errs, ValidateJSONSchema(item, rawItems, root, fmt.Sprintf("%s[%d]", path, index))...)
		}
	}
	return errs
}

func validateString(value string, schema map[string]any, path string) []string {
	var errs []string
	if minimum, ok := integerKeyword(schema["minLength"]); ok && len([]rune(value)) < minimum {
		errs = append(errs, fmt.Sprintf("%s: string is shorter than %d", path, minimum))
	}
	if pattern, ok := schema["pattern"].(string); ok {
		re, err := regexp.Compile(pattern)
		if err != nil || !re.MatchString(value) {
			errs = append(errs, fmt.Sprintf("%s: string does not match %q", path, pattern))
		}
	}
	if format, _ := schema["format"].(string); format != "" {
		switch format {
		case "date":
			if _, err := time.Parse("2006-01-02", value); err != nil {
				errs = append(errs, fmt.Sprintf("%s: invalid date", path))
			}
		case "uri":
			parsed, err := url.ParseRequestURI(value)
			if err != nil || parsed.Host == "" || (parsed.Scheme != "https" && parsed.Scheme != "http") {
				errs = append(errs, fmt.Sprintf("%s: invalid HTTP(S) URI", path))
			}
		}
	}
	return errs
}

func resolveLocalRef(root map[string]any, ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("only local schema references are supported: %q", ref)
	}
	var current any = root
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("reference %q traverses a non-object", ref)
		}
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		current, ok = object[token]
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

func matchesType(value any, raw any) bool {
	types := []string{}
	switch candidate := raw.(type) {
	case string:
		types = append(types, candidate)
	case []any:
		for _, item := range candidate {
			if name, ok := item.(string); ok {
				types = append(types, name)
			}
		}
	}
	for _, candidate := range types {
		switch candidate {
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
		case "number":
			_, ok := value.(json.Number)
			if ok {
				return true
			}
		case "integer":
			if number, ok := value.(json.Number); ok {
				if _, err := strconv.ParseInt(number.String(), 10, 64); err == nil {
					return true
				}
			}
		case "boolean":
			_, ok := value.(bool)
			if ok {
				return true
			}
		}
	}
	return false
}

func jsonEqual(left, right any) bool {
	leftJSON, _ := json.Marshal(left)
	rightJSON, _ := json.Marshal(right)
	return string(leftJSON) == string(rightJSON)
}

func integerKeyword(raw any) (int, bool) {
	if raw == nil {
		return 0, false
	}
	switch value := raw.(type) {
	case json.Number:
		parsed, err := strconv.Atoi(value.String())
		return parsed, err == nil
	case float64:
		return int(value), value == float64(int(value))
	}
	return 0, false
}

func numberAsFloat(raw any) (float64, bool) {
	switch value := raw.(type) {
	case json.Number:
		parsed, err := value.Float64()
		return parsed, err == nil
	case float64:
		return value, true
	}
	return 0, false
}
