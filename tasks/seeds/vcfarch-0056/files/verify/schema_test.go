package verify

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"unicode/utf8"
)

// Test00SddcSpecConformsToInstallerSchema is also run alone as phase one of
// verify/run.sh. Keep it independent: no other artifact or fixture is read.
func Test00SddcSpecConformsToInstallerSchema(t *testing.T) {
	openAPI := readJSONObject(t, "../specifications/vcf-installer/vcf-installer-openapi.json")
	artifact := readJSONValue(t, "../architecture/sddc-spec.json")

	components := objectAt(t, openAPI, "components")
	schemas := objectAt(t, components, "schemas")
	schema, ok := schemas["SddcSpec"].(map[string]any)
	if !ok {
		t.Fatal("installer OpenAPI has no object schema named SddcSpec")
	}

	if err := validateSchema(artifact, schema, openAPI, "$", map[string]bool{}); err != nil {
		t.Fatalf("architecture/sddc-spec.json does not validate against installer #/components/schemas/SddcSpec: %v", err)
	}
}

func readJSONValue(t *testing.T, path string) any {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatalf("open %s: %v", path, err)
	}
	defer f.Close()
	dec := json.NewDecoder(f)
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
	if dec.More() {
		t.Fatalf("decode %s: trailing JSON value", path)
	}
	return v
}

func readJSONObject(t *testing.T, path string) map[string]any {
	t.Helper()
	v := readJSONValue(t, path)
	m, ok := v.(map[string]any)
	if !ok {
		t.Fatalf("%s: top-level JSON value is not an object", path)
	}
	return m
}

func objectAt(t *testing.T, parent map[string]any, key string) map[string]any {
	t.Helper()
	v, ok := parent[key].(map[string]any)
	if !ok {
		t.Fatalf("%s is not an object", key)
	}
	return v
}

func validateSchema(value any, schema map[string]any, root map[string]any, path string, resolving map[string]bool) error {
	if ref, ok := schema["$ref"].(string); ok {
		if resolving[ref] {
			return fmt.Errorf("%s: cyclic schema reference %s", path, ref)
		}
		resolved, err := resolveLocalRef(root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		resolving[ref] = true
		err = validateSchema(value, resolved, root, path, resolving)
		delete(resolving, ref)
		return err
	}

	if branches, ok := schema["allOf"].([]any); ok {
		for i, branch := range branches {
			m, ok := branch.(map[string]any)
			if !ok {
				return fmt.Errorf("%s: allOf[%d] is not a schema", path, i)
			}
			if err := validateSchema(value, m, root, path, resolving); err != nil {
				return err
			}
		}
	}
	for _, keyword := range []string{"oneOf", "anyOf"} {
		if branches, ok := schema[keyword].([]any); ok {
			matches := 0
			var last error
			for _, branch := range branches {
				m, ok := branch.(map[string]any)
				if !ok {
					continue
				}
				if err := validateSchema(value, m, root, path, copySet(resolving)); err == nil {
					matches++
				} else {
					last = err
				}
			}
			if (keyword == "oneOf" && matches != 1) || (keyword == "anyOf" && matches == 0) {
				return fmt.Errorf("%s: %s matched %d branches (last error: %v)", path, keyword, matches, last)
			}
		}
	}

	if value == nil {
		if nullable, _ := schema["nullable"].(bool); nullable {
			return nil
		}
		return fmt.Errorf("%s: null is not allowed", path)
	}

	typeName, _ := schema["type"].(string)
	switch typeName {
	case "object":
		obj, ok := value.(map[string]any)
		if !ok {
			return typeError(path, typeName, value)
		}
		if required, ok := schema["required"].([]any); ok {
			for _, raw := range required {
				name, _ := raw.(string)
				if _, exists := obj[name]; !exists {
					return fmt.Errorf("%s: missing required property %q", path, name)
				}
			}
		}
		properties, _ := schema["properties"].(map[string]any)
		for name, child := range obj {
			childSchema, exists := properties[name]
			if !exists {
				switch additional := schema["additionalProperties"].(type) {
				case bool:
					if additional {
						continue
					}
					return fmt.Errorf("%s: additional property %q is forbidden", path, name)
				case map[string]any:
					if err := validateSchema(child, additional, root, path+"."+name, resolving); err != nil {
						return err
					}
					continue
				default:
					return fmt.Errorf("%s: property %q is not declared by the installer schema", path, name)
				}
			}
			m, ok := childSchema.(map[string]any)
			if !ok {
				return fmt.Errorf("%s.%s: property schema is not an object", path, name)
			}
			if err := validateSchema(child, m, root, path+"."+name, resolving); err != nil {
				return err
			}
		}
	case "array":
		items, ok := value.([]any)
		if !ok {
			return typeError(path, typeName, value)
		}
		if err := checkCount(path, len(items), schema, "minItems", "maxItems"); err != nil {
			return err
		}
		if itemSchema, ok := schema["items"].(map[string]any); ok {
			for i, item := range items {
				if err := validateSchema(item, itemSchema, root, fmt.Sprintf("%s[%d]", path, i), resolving); err != nil {
					return err
				}
			}
		}
	case "string":
		s, ok := value.(string)
		if !ok {
			return typeError(path, typeName, value)
		}
		if err := checkCount(path, utf8.RuneCountInString(s), schema, "minLength", "maxLength"); err != nil {
			return err
		}
		if pattern, ok := schema["pattern"].(string); ok {
			re, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: invalid schema pattern %q: %w", path, pattern, err)
			}
			if !re.MatchString(s) {
				return fmt.Errorf("%s: %q does not match %q", path, s, pattern)
			}
		}
	case "integer":
		if !isInteger(value) {
			return typeError(path, typeName, value)
		}
		if err := checkNumericBounds(path, value, schema); err != nil {
			return err
		}
	case "number":
		if _, ok := numberValue(value); !ok {
			return typeError(path, typeName, value)
		}
		if err := checkNumericBounds(path, value, schema); err != nil {
			return err
		}
	case "boolean":
		if _, ok := value.(bool); !ok {
			return typeError(path, typeName, value)
		}
	}

	if enum, ok := schema["enum"].([]any); ok {
		matched := false
		for _, candidate := range enum {
			if jsonScalarEqual(value, candidate) {
				matched = true
				break
			}
		}
		if !matched {
			return fmt.Errorf("%s: value %v is not in enum %v", path, value, enum)
		}
	}
	return nil
}

func resolveLocalRef(root map[string]any, ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("non-local schema reference %q", ref)
	}
	var current any = root
	for _, encoded := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		key := strings.ReplaceAll(strings.ReplaceAll(encoded, "~1", "/"), "~0", "~")
		obj, ok := current.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("schema reference %q traverses a non-object", ref)
		}
		current, ok = obj[key]
		if !ok {
			return nil, fmt.Errorf("schema reference %q does not exist", ref)
		}
	}
	m, ok := current.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("schema reference %q is not an object", ref)
	}
	return m, nil
}

func checkCount(path string, got int, schema map[string]any, minKey, maxKey string) error {
	if min, ok := intKeyword(schema[minKey]); ok && got < min {
		return fmt.Errorf("%s: length %d is less than %s %d", path, got, minKey, min)
	}
	if max, ok := intKeyword(schema[maxKey]); ok && got > max {
		return fmt.Errorf("%s: length %d exceeds %s %d", path, got, maxKey, max)
	}
	return nil
}

func intKeyword(v any) (int, bool) {
	n, ok := numberValue(v)
	return int(n), ok && math.Trunc(n) == n
}

func checkNumericBounds(path string, value any, schema map[string]any) error {
	n, _ := numberValue(value)
	if min, ok := numberValue(schema["minimum"]); ok && n < min {
		return fmt.Errorf("%s: %v is less than minimum %v", path, value, min)
	}
	if max, ok := numberValue(schema["maximum"]); ok && n > max {
		return fmt.Errorf("%s: %v exceeds maximum %v", path, value, max)
	}
	return nil
}

func numberValue(v any) (float64, bool) {
	switch n := v.(type) {
	case json.Number:
		f, err := strconv.ParseFloat(string(n), 64)
		return f, err == nil
	case float64:
		return n, true
	case float32:
		return float64(n), true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	default:
		return 0, false
	}
}

func isInteger(v any) bool {
	if n, ok := v.(json.Number); ok {
		_, err := n.Int64()
		return err == nil
	}
	n, ok := numberValue(v)
	return ok && math.Trunc(n) == n
}

func jsonScalarEqual(a, b any) bool {
	if af, ok := numberValue(a); ok {
		bf, bok := numberValue(b)
		return bok && af == bf
	}
	return reflect.DeepEqual(a, b)
}

func typeError(path, want string, got any) error {
	return fmt.Errorf("%s: expected %s, got %T", path, want, got)
}

func copySet(in map[string]bool) map[string]bool {
	out := make(map[string]bool, len(in))
	for key, value := range in {
		out[key] = value
	}
	return out
}
