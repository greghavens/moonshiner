package verifier

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// ValidateAgainstInstallerSchema validates an artifact with the small,
// deterministic JSON Schema implementation used by the installer verifier.
// It supports every keyword used by installer_spec.schema.json.
func ValidateAgainstInstallerSchema(schemaJSON, artifactJSON []byte) error {
	var schema any
	if err := decodeJSON(schemaJSON, &schema); err != nil {
		return fmt.Errorf("invalid installer schema: %w", err)
	}
	root, ok := schema.(map[string]any)
	if !ok {
		return fmt.Errorf("invalid installer schema: root is not an object")
	}

	var artifact any
	if err := decodeJSON(artifactJSON, &artifact); err != nil {
		return fmt.Errorf("artifact is not valid JSON: %w", err)
	}
	if err := validateSchemaNode(root, root, artifact, "$"); err != nil {
		return err
	}
	return nil
}

func decodeJSON(data []byte, dst any) error {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	if err := dec.Decode(dst); err != nil {
		return err
	}
	var extra any
	if err := dec.Decode(&extra); err != io.EOF {
		if err == nil {
			return fmt.Errorf("multiple JSON values")
		}
		return err
	}
	return nil
}

func validateSchemaNode(root, schema map[string]any, value any, path string) error {
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolveLocalRef(root, ref)
		if err != nil {
			return err
		}
		return validateSchemaNode(root, resolved, value, path)
	}

	if expected, ok := schema["const"]; ok && !jsonEqual(expected, value) {
		return fmt.Errorf("%s: must equal %v", path, expected)
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
			return fmt.Errorf("%s: value is not in enum", path)
		}
	}

	if kind, ok := schema["type"].(string); ok {
		if !matchesJSONType(kind, value) {
			return fmt.Errorf("%s: expected %s", path, kind)
		}
	}

	switch typed := value.(type) {
	case map[string]any:
		if err := validateObject(root, schema, typed, path); err != nil {
			return err
		}
	case []any:
		if err := validateArray(root, schema, typed, path); err != nil {
			return err
		}
	case string:
		if err := validateString(schema, typed, path); err != nil {
			return err
		}
	case json.Number:
		if err := validateNumber(schema, typed, path); err != nil {
			return err
		}
	}
	return nil
}

func resolveLocalRef(root map[string]any, ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("installer schema uses unsupported reference %q", ref)
	}
	var current any = root
	for _, token := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		token = strings.ReplaceAll(strings.ReplaceAll(token, "~1", "/"), "~0", "~")
		object, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("installer schema reference %q does not resolve", ref)
		}
		current, ok = object[token]
		if !ok {
			return nil, fmt.Errorf("installer schema reference %q does not resolve", ref)
		}
	}
	resolved, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("installer schema reference %q is not an object", ref)
	}
	return resolved, nil
}

func matchesJSONType(kind string, value any) bool {
	switch kind {
	case "object":
		_, ok := value.(map[string]any)
		return ok
	case "array":
		_, ok := value.([]any)
		return ok
	case "string":
		_, ok := value.(string)
		return ok
	case "integer":
		n, ok := value.(json.Number)
		if !ok {
			return false
		}
		_, err := strconv.ParseInt(n.String(), 10, 64)
		return err == nil
	case "number":
		_, ok := value.(json.Number)
		return ok
	case "boolean":
		_, ok := value.(bool)
		return ok
	case "null":
		return value == nil
	default:
		return false
	}
}

func validateObject(root, schema map[string]any, value map[string]any, path string) error {
	if required, ok := schema["required"].([]any); ok {
		for _, raw := range required {
			name := raw.(string)
			if _, exists := value[name]; !exists {
				return fmt.Errorf("%s: missing required property %q", path, name)
			}
		}
	}
	properties, _ := schema["properties"].(map[string]any)
	for name, child := range value {
		childSchema, known := properties[name]
		if !known {
			if additional, exists := schema["additionalProperties"].(bool); exists && !additional {
				return fmt.Errorf("%s: additional property %q is not allowed", path, name)
			}
			continue
		}
		schemaObject, ok := childSchema.(map[string]any)
		if !ok {
			return fmt.Errorf("installer schema property %q is invalid", name)
		}
		if err := validateSchemaNode(root, schemaObject, child, path+"."+name); err != nil {
			return err
		}
	}
	return nil
}

func validateArray(root, schema map[string]any, value []any, path string) error {
	if min, ok := schemaInteger(schema["minItems"]); ok && len(value) < min {
		return fmt.Errorf("%s: requires at least %d items", path, min)
	}
	if max, ok := schemaInteger(schema["maxItems"]); ok && len(value) > max {
		return fmt.Errorf("%s: allows at most %d items", path, max)
	}
	if unique, _ := schema["uniqueItems"].(bool); unique {
		seen := map[string]bool{}
		for _, item := range value {
			encoded, _ := json.Marshal(item)
			key := string(encoded)
			if seen[key] {
				return fmt.Errorf("%s: array items must be unique", path)
			}
			seen[key] = true
		}
	}
	if rawItems, exists := schema["items"]; exists {
		itemSchema, ok := rawItems.(map[string]any)
		if !ok {
			return fmt.Errorf("installer schema items declaration is invalid")
		}
		for i, item := range value {
			if err := validateSchemaNode(root, itemSchema, item, fmt.Sprintf("%s[%d]", path, i)); err != nil {
				return err
			}
		}
	}
	return nil
}

func validateString(schema map[string]any, value, path string) error {
	if min, ok := schemaInteger(schema["minLength"]); ok && len([]rune(value)) < min {
		return fmt.Errorf("%s: string is shorter than %d characters", path, min)
	}
	if pattern, ok := schema["pattern"].(string); ok {
		re, err := regexp.Compile(pattern)
		if err != nil {
			return fmt.Errorf("installer schema pattern is invalid: %w", err)
		}
		if !re.MatchString(value) {
			return fmt.Errorf("%s: string does not match %q", path, pattern)
		}
	}
	if format, ok := schema["format"].(string); ok {
		switch format {
		case "date":
			if parsed, err := time.Parse("2006-01-02", value); err != nil || parsed.Format("2006-01-02") != value {
				return fmt.Errorf("%s: expected RFC 3339 full-date", path)
			}
		case "uri":
			parsed, err := url.ParseRequestURI(value)
			if err != nil || parsed.Scheme == "" {
				return fmt.Errorf("%s: expected absolute URI", path)
			}
		}
	}
	return nil
}

func validateNumber(schema map[string]any, value json.Number, path string) error {
	number, err := strconv.ParseFloat(value.String(), 64)
	if err != nil {
		return fmt.Errorf("%s: invalid number", path)
	}
	if raw, exists := schema["minimum"]; exists {
		minimum, ok := schemaNumber(raw)
		if !ok {
			return fmt.Errorf("installer schema minimum is invalid")
		}
		if number < minimum {
			return fmt.Errorf("%s: number is below minimum %v", path, minimum)
		}
	}
	return nil
}

func schemaInteger(raw any) (int, bool) {
	switch n := raw.(type) {
	case json.Number:
		v, err := strconv.Atoi(n.String())
		return v, err == nil
	case float64:
		return int(n), n == float64(int(n))
	case int:
		return n, true
	default:
		return 0, false
	}
}

func schemaNumber(raw any) (float64, bool) {
	switch n := raw.(type) {
	case json.Number:
		v, err := strconv.ParseFloat(n.String(), 64)
		return v, err == nil
	case float64:
		return n, true
	case int:
		return float64(n), true
	default:
		return 0, false
	}
}

func jsonEqual(left, right any) bool {
	if ln, ok := left.(json.Number); ok {
		if rn, ok := right.(json.Number); ok {
			return ln.String() == rn.String()
		}
	}
	return reflect.DeepEqual(left, right)
}
