package jsonschema

import "testing"

func TestValidate(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name       string
		schemaJSON string
		valueJSON  string
		wantErrors int
	}{
		{
			name:       "local ref and required object pass",
			schemaJSON: `{"type":"object","required":["x"],"additionalProperties":false,"properties":{"x":{"$ref":"#/$defs/positive"}},"$defs":{"positive":{"type":"integer","minimum":1}}}`,
			valueJSON:  `{"x":2}`,
		},
		{
			name:       "missing required and unexpected property",
			schemaJSON: `{"type":"object","required":["x"],"additionalProperties":false,"properties":{"x":{"type":"string"}}}`,
			valueJSON:  `{"y":true}`,
			wantErrors: 2,
		},
		{
			name:       "array constraints",
			schemaJSON: `{"type":"array","minItems":2,"uniqueItems":true,"items":{"type":"string","minLength":1}}`,
			valueJSON:  `[""]`,
			wantErrors: 2,
		},
		{
			name:       "enum rejection",
			schemaJSON: `{"enum":["a","b"]}`,
			valueJSON:  `"c"`,
			wantErrors: 1,
		},
	}
	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			schema, err := Decode([]byte(tt.schemaJSON))
			if err != nil {
				t.Fatal(err)
			}
			value, err := Decode([]byte(tt.valueJSON))
			if err != nil {
				t.Fatal(err)
			}
			if got := len(Validate(schema, value)); got != tt.wantErrors {
				t.Fatalf("got %d validation errors, want %d", got, tt.wantErrors)
			}
		})
	}
}
