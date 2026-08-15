package migrationplan

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"os"
	"reflect"
	"regexp"
	"strings"
	"testing"
	"time"
)

const installerSpecSHA256 = "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"

// TestArchitectureContract is deliberately one test: validation of the
// submitted artifact as the installer's own SddcSpec is completed before the
// verifier reads or checks the migration schema, estate, or compatibility
// snapshot. The semantic cases remain table-driven and run under -race.
func TestArchitectureContract(t *testing.T) {
	planRaw := mustRead(t, "migration-plan.json")
	installerRaw := mustRead(t, "specifications/vcf-installer/vcf-installer-openapi.json")

	var artifact any
	decodeJSON(t, planRaw, &artifact)
	var installer map[string]any
	decodeJSON(t, installerRaw, &installer)
	sddcSchema, err := objectAt(installer, "components", "schemas", "SddcSpec")
	if err != nil {
		t.Fatalf("installer specification does not expose components.schemas.SddcSpec: %v", err)
	}
	if err := validateJSONSchema(artifact, sddcSchema, installer, "$", false); err != nil {
		t.Fatalf("migration-plan.json is not a valid installer SddcSpec: %v", err)
	}

	// Everything below intentionally happens only after SddcSpec validation.
	digest := sha256.Sum256(installerRaw)
	if got := hex.EncodeToString(digest[:]); got != installerSpecSHA256 {
		t.Fatalf("installer specification digest = %s, want pinned %s", got, installerSpecSHA256)
	}
	info, err := objectAt(installer, "info")
	if err != nil || info["version"] != "9.1.0.0" {
		t.Fatalf("installer specification must be version 9.1.0.0")
	}

	planSchemaRaw := mustRead(t, "schemas/migration-plan.schema.json")
	var planSchema map[string]any
	decodeJSON(t, planSchemaRaw, &planSchema)
	if err := validateJSONSchema(artifact, planSchema, planSchema, "$", true); err != nil {
		t.Fatalf("migration-plan.json violates the migration plan schema: %v", err)
	}
	validateResearchArtifact(t, mustRead(t, "RESEARCH.md"))

	plan, err := DecodePlan(bytes.NewReader(planRaw))
	if err != nil {
		t.Fatalf("DecodePlan: %v", err)
	}
	estate, err := DecodeEstate(bytes.NewReader(mustRead(t, "fixtures/estate.json")))
	if err != nil {
		t.Fatalf("DecodeEstate: %v", err)
	}
	snapshot, err := DecodeSnapshot(bytes.NewReader(mustRead(t, "compatibility/compatibility-snapshot.json")))
	if err != nil {
		t.Fatalf("DecodeSnapshot: %v", err)
	}

	tests := []struct {
		name    string
		mutate  func(*Plan)
		wantErr string
	}{
		{name: "reference architecture"},
		{
			name: "incomplete inventory coverage",
			mutate: func(p *Plan) {
				p.Components = p.Components[:len(p.Components)-1]
			},
			wantErr: "component coverage",
		},
		{
			name: "management domain drift",
			mutate: func(p *Plan) {
				p.Components[0].TargetVersion = "9.1.0.0"
			},
			wantErr: "management component",
		},
		{
			name: "unpinned target",
			mutate: func(p *Plan) {
				for i := range p.Components {
					if p.Components[i].ID == "workload-vcenter" {
						p.Components[i].TargetVersion = "8.0.3.99999"
					}
				}
			},
			wantErr: "pinned target",
		},
		{
			name: "component omits technical gate",
			mutate: func(p *Plan) {
				for i := range p.Components {
					if p.Components[i].ID == "workload-esxi-cluster" {
						p.Components[i].GatedBy = []string{"vcenter-at-target"}
					}
				}
			},
			wantErr: "missing required gate",
		},
		{
			name: "component adds unrelated technical gate",
			mutate: func(p *Plan) {
				for i := range p.Components {
					if p.Components[i].ID == "workload-vcenter" {
						p.Components[i].GatedBy = append(p.Components[i].GatedBy, "hardware-supports-esxi-target")
					}
				}
			},
			wantErr: "gates do not match",
		},
		{
			name: "duplicate gate declaration",
			mutate: func(p *Plan) {
				p.Gates = append(p.Gates, p.Gates[0])
			},
			wantErr: "duplicate gate",
		},
		{
			name: "step order is not strict",
			mutate: func(p *Plan) {
				p.Steps[1].Order = p.Steps[0].Order
			},
			wantErr: "strictly increasing",
		},
		{
			name: "step order must be positive",
			mutate: func(p *Plan) {
				p.Steps[0].Order = 0
			},
			wantErr: "positive",
		},
		{
			name: "gate consumed before produced",
			mutate: func(p *Plan) {
				p.Steps[0].Requires = append(p.Steps[0].Requires, "esxi-at-target")
			},
			wantErr: "not available",
		},
		{
			name: "step adds unrelated available prerequisite",
			mutate: func(p *Plan) {
				p.Steps[0].Requires = append(p.Steps[0].Requires, "network-inputs-valid")
			},
			wantErr: "required gates do not match",
		},
		{
			name: "step omits produced gate",
			mutate: func(p *Plan) {
				p.Steps[0].Produces = nil
			},
			wantErr: "produced gates do not match",
		},
		{
			name: "transition omitted",
			mutate: func(p *Plan) {
				p.Steps[2].Transitions = p.Steps[2].Transitions[:1]
			},
			wantErr: "not planned",
		},
		{
			name: "transition duplicated",
			mutate: func(p *Plan) {
				p.Steps[2].Transitions = append(p.Steps[2].Transitions, p.Steps[2].Transitions[0])
			},
			wantErr: "more than once",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			candidate := clonePlan(t, plan)
			if tt.mutate != nil {
				tt.mutate(&candidate)
			}
			err := Validate(candidate, estate, snapshot)
			if tt.wantErr == "" {
				if err != nil {
					t.Fatalf("Validate returned error: %v", err)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
				t.Fatalf("Validate error = %v, want substring %q", err, tt.wantErr)
			}
		})
	}

	planDecoderTests := []struct {
		name    string
		input   string
		wantErr string
	}{
		{
			name:    "malformed JSON",
			input:   `{"sddcId":`,
			wantErr: "decode plan",
		},
		{
			name:    "unknown field",
			input:   `{"sddcId":"dfw-w01","unexpected":true}`,
			wantErr: "unknown field",
		},
		{
			name:    "trailing value",
			input:   string(planRaw) + ` {}`,
			wantErr: "trailing JSON value",
		},
	}
	for _, tt := range planDecoderTests {
		t.Run("decoder "+tt.name, func(t *testing.T) {
			_, err := DecodePlan(strings.NewReader(tt.input))
			if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
				t.Fatalf("DecodePlan error = %v, want substring %q", err, tt.wantErr)
			}
		})
	}

	strictDecoderTests := []struct {
		name    string
		decode  func(string) error
		valid   string
		unknown string
	}{
		{
			name: "estate",
			decode: func(input string) error {
				_, err := DecodeEstate(strings.NewReader(input))
				return err
			},
			valid:   string(mustRead(t, "fixtures/estate.json")),
			unknown: `{"unexpected":true}`,
		},
		{
			name: "snapshot",
			decode: func(input string) error {
				_, err := DecodeSnapshot(strings.NewReader(input))
				return err
			},
			valid:   string(mustRead(t, "compatibility/compatibility-snapshot.json")),
			unknown: `{"unexpected":true}`,
		},
	}
	for _, tt := range strictDecoderTests {
		t.Run(tt.name+" decoder rejects unknown field", func(t *testing.T) {
			if err := tt.decode(tt.unknown); err == nil || !strings.Contains(err.Error(), "unknown field") {
				t.Fatalf("decoder error = %v, want unknown-field error", err)
			}
		})
		t.Run(tt.name+" decoder rejects trailing value", func(t *testing.T) {
			if err := tt.decode(tt.valid + ` {}`); err == nil || !strings.Contains(err.Error(), "trailing JSON value") {
				t.Fatalf("decoder error = %v, want trailing-value error", err)
			}
		})
		t.Run(tt.name+" decoder rejects malformed JSON", func(t *testing.T) {
			if err := tt.decode(`{"broken":`); err == nil {
				t.Fatal("decoder accepted malformed JSON")
			}
		})
	}
}

func validateResearchArtifact(t *testing.T, raw []byte) {
	t.Helper()
	research := string(raw)
	if len(strings.Fields(research)) < 60 {
		t.Fatal("RESEARCH.md must contain substantive source titles and supported decisions")
	}
	datePattern := regexp.MustCompile(`\b20\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b`)
	validDate := false
	for _, date := range datePattern.FindAllString(research, -1) {
		if _, err := time.Parse("2006-01-02", date); err == nil {
			validDate = true
			break
		}
	}
	if !validDate {
		t.Fatal("RESEARCH.md must record an ISO 8601 access date")
	}
	urlPattern := regexp.MustCompile(`https://[^\s)>\]]+`)
	seenURLs := make(map[string]struct{})
	for _, rawURL := range urlPattern.FindAllString(research, -1) {
		rawURL = strings.TrimRight(rawURL, ".,;:")
		parsed, err := url.Parse(rawURL)
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" {
			t.Fatalf("RESEARCH.md contains invalid source URL %q", rawURL)
		}
		host := strings.ToLower(parsed.Hostname())
		if host == "localhost" || host == "example.com" || strings.HasSuffix(host, ".example") || strings.HasSuffix(host, ".invalid") || strings.HasSuffix(host, ".localhost") {
			t.Fatalf("RESEARCH.md source URL %q is not a real public source", rawURL)
		}
		seenURLs[rawURL] = struct{}{}
	}
	if len(seenURLs) < 3 {
		t.Fatalf("RESEARCH.md records %d distinct public sources, want at least 3", len(seenURLs))
	}
	lower := strings.ToLower(research)
	requiredTopics := []string{"interoperability matrix", "upgrade", "import"}
	for _, topic := range requiredTopics {
		if !strings.Contains(lower, topic) {
			t.Fatalf("RESEARCH.md does not document %s research", topic)
		}
	}
	if !strings.Contains(lower, "bill of materials") && !regexp.MustCompile(`\bbom\b`).MatchString(lower) {
		t.Fatal("RESEARCH.md does not document compatibility/BOM research")
	}
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return b
}

func decodeJSON(t *testing.T, b []byte, out any) {
	t.Helper()
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.UseNumber()
	if err := dec.Decode(out); err != nil {
		t.Fatalf("decode JSON: %v", err)
	}
	if dec.More() {
		t.Fatal("unexpected trailing JSON")
	}
}

func clonePlan(t *testing.T, p Plan) Plan {
	t.Helper()
	b, err := json.Marshal(p)
	if err != nil {
		t.Fatalf("clone marshal: %v", err)
	}
	copy, err := DecodePlan(bytes.NewReader(b))
	if err != nil {
		t.Fatalf("clone decode: %v", err)
	}
	return copy
}

func objectAt(root map[string]any, path ...string) (map[string]any, error) {
	var cur any = root
	for _, part := range path {
		obj, ok := cur.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%s is not an object", strings.Join(path, "."))
		}
		var found bool
		cur, found = obj[part]
		if !found {
			return nil, fmt.Errorf("missing %s", strings.Join(path, "."))
		}
	}
	obj, ok := cur.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("%s is not an object", strings.Join(path, "."))
	}
	return obj, nil
}

func resolveRef(root map[string]any, ref string) (map[string]any, error) {
	if !strings.HasPrefix(ref, "#/") {
		return nil, fmt.Errorf("external schema reference %q is not allowed", ref)
	}
	var cur any = root
	for _, raw := range strings.Split(strings.TrimPrefix(ref, "#/"), "/") {
		part := strings.ReplaceAll(strings.ReplaceAll(raw, "~1", "/"), "~0", "~")
		obj, ok := cur.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("reference %q traverses a non-object", ref)
		}
		var found bool
		cur, found = obj[part]
		if !found {
			return nil, fmt.Errorf("reference %q does not resolve", ref)
		}
	}
	obj, ok := cur.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("reference %q does not resolve to a schema", ref)
	}
	return obj, nil
}

// validateJSONSchema implements the JSON Schema/OpenAPI subset exercised by
// the pinned SddcSpec and the local migration schema. It resolves the schemas
// from the files themselves; field requirements are not duplicated in tests.
func validateJSONSchema(value any, schema, root map[string]any, path string, enforceAdditional bool) error {
	if ref, ok := schema["$ref"].(string); ok {
		resolved, err := resolveRef(root, ref)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		return validateJSONSchema(value, resolved, root, path, enforceAdditional)
	}
	if all, ok := schema["allOf"].([]any); ok {
		for _, item := range all {
			child, ok := item.(map[string]any)
			if !ok {
				return fmt.Errorf("%s: invalid allOf schema", path)
			}
			if err := validateJSONSchema(value, child, root, path, enforceAdditional); err != nil {
				return err
			}
		}
	}
	if enum, ok := schema["enum"].([]any); ok {
		matched := false
		for _, allowed := range enum {
			if reflect.DeepEqual(value, allowed) {
				matched = true
				break
			}
		}
		if !matched {
			return fmt.Errorf("%s: value %v is not in enum", path, value)
		}
	}
	if want, ok := schema["const"]; ok && !reflect.DeepEqual(value, want) {
		return fmt.Errorf("%s: value %v does not equal const %v", path, value, want)
	}

	typeName, _ := schema["type"].(string)
	switch typeName {
	case "object":
		obj, ok := value.(map[string]any)
		if !ok {
			return fmt.Errorf("%s: expected object", path)
		}
		if required, ok := schema["required"].([]any); ok {
			for _, raw := range required {
				name, ok := raw.(string)
				if !ok {
					return fmt.Errorf("%s: invalid required entry", path)
				}
				if _, found := obj[name]; !found {
					return fmt.Errorf("%s: required property %q is missing", path, name)
				}
			}
		}
		properties, _ := schema["properties"].(map[string]any)
		for name, childValue := range obj {
			rawChild, known := properties[name]
			if !known {
				if enforceAdditional && schema["additionalProperties"] == false {
					return fmt.Errorf("%s: additional property %q is not allowed", path, name)
				}
				continue
			}
			childSchema, ok := rawChild.(map[string]any)
			if !ok {
				return fmt.Errorf("%s.%s: invalid property schema", path, name)
			}
			if err := validateJSONSchema(childValue, childSchema, root, path+"."+name, enforceAdditional); err != nil {
				return err
			}
		}
	case "array":
		items, ok := value.([]any)
		if !ok {
			return fmt.Errorf("%s: expected array", path)
		}
		if min, ok := schemaNumber(schema["minItems"]); ok && float64(len(items)) < min {
			return fmt.Errorf("%s: array has %d items, minimum is %.0f", path, len(items), min)
		}
		if max, ok := schemaNumber(schema["maxItems"]); ok && float64(len(items)) > max {
			return fmt.Errorf("%s: array has %d items, maximum is %.0f", path, len(items), max)
		}
		if itemSchema, ok := schema["items"].(map[string]any); ok {
			for i, item := range items {
				if err := validateJSONSchema(item, itemSchema, root, fmt.Sprintf("%s[%d]", path, i), enforceAdditional); err != nil {
					return err
				}
			}
		}
	case "string":
		s, ok := value.(string)
		if !ok {
			return fmt.Errorf("%s: expected string", path)
		}
		if min, ok := schemaNumber(schema["minLength"]); ok && float64(len([]rune(s))) < min {
			return fmt.Errorf("%s: string is shorter than %.0f", path, min)
		}
		if max, ok := schemaNumber(schema["maxLength"]); ok && float64(len([]rune(s))) > max {
			return fmt.Errorf("%s: string is longer than %.0f", path, max)
		}
		if pattern, ok := schema["pattern"].(string); ok {
			re, err := regexp.Compile(pattern)
			if err != nil {
				return fmt.Errorf("%s: invalid schema pattern: %v", path, err)
			}
			if !re.MatchString(s) {
				return fmt.Errorf("%s: %q does not match %q", path, s, pattern)
			}
		}
	case "integer":
		n, ok := schemaNumber(value)
		if !ok || math.Trunc(n) != n {
			return fmt.Errorf("%s: expected integer", path)
		}
		if err := validateNumberBounds(path, n, schema); err != nil {
			return err
		}
	case "number":
		n, ok := schemaNumber(value)
		if !ok {
			return fmt.Errorf("%s: expected number", path)
		}
		if err := validateNumberBounds(path, n, schema); err != nil {
			return err
		}
	case "boolean":
		if _, ok := value.(bool); !ok {
			return fmt.Errorf("%s: expected boolean", path)
		}
	}
	return nil
}

func schemaNumber(value any) (float64, bool) {
	switch n := value.(type) {
	case json.Number:
		v, err := n.Float64()
		return v, err == nil
	case float64:
		return n, true
	case int:
		return float64(n), true
	default:
		return 0, false
	}
}

func validateNumberBounds(path string, n float64, schema map[string]any) error {
	if min, ok := schemaNumber(schema["minimum"]); ok && n < min {
		return fmt.Errorf("%s: number %v is below minimum %v", path, n, min)
	}
	if max, ok := schemaNumber(schema["maximum"]); ok && n > max {
		return fmt.Errorf("%s: number %v is above maximum %v", path, n, max)
	}
	return nil
}
