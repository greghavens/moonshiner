package jsonschema

import "testing"

func TestValidateTable(t *testing.T) {
	schema := map[string]any{
		"type":                 "object",
		"required":             []any{"name", "ports"},
		"additionalProperties": false,
		"properties": map[string]any{
			"name": map[string]any{"type": "string", "pattern": "^[a-z]+$"},
			"ports": map[string]any{
				"type": "array", "minItems": float64(1), "uniqueItems": true,
				"items": map[string]any{"type": "integer", "minimum": float64(1)},
			},
		},
	}
	tests := []struct {
		name    string
		value   any
		wantErr bool
	}{
		{name: "valid", value: map[string]any{"name": "vcf", "ports": []any{float64(443)}}},
		{name: "required", value: map[string]any{"name": "vcf"}, wantErr: true},
		{name: "pattern", value: map[string]any{"name": "VCF", "ports": []any{float64(443)}}, wantErr: true},
		{name: "integer", value: map[string]any{"name": "vcf", "ports": []any{1.5}}, wantErr: true},
		{name: "unique", value: map[string]any{"name": "vcf", "ports": []any{float64(443), float64(443)}}, wantErr: true},
		{name: "additional", value: map[string]any{"name": "vcf", "ports": []any{float64(443)}, "extra": true}, wantErr: true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			err := Validate(schema, schema, tc.value)
			if (err != nil) != tc.wantErr {
				t.Fatalf("Validate() error = %v, wantErr %v", err, tc.wantErr)
			}
		})
	}
}
