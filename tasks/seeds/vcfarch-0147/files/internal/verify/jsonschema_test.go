package verify

import (
	"testing"
)

func TestValidateTable(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name     string
		document string
		ref      string
		instance string
		valid    bool
	}{
		{"valid ref", `{"$defs":{"x":{"type":"string","pattern":"^ok$"}}}`, "#/$defs/x", `"ok"`, true},
		{"bad ref value", `{"$defs":{"x":{"type":"string","pattern":"^ok$"}}}`, "#/$defs/x", `"no"`, false},
		{"required", `{"type":"object","required":["x"],"properties":{"x":{"type":"integer"}}}`, "#", `{}`, false},
		{"integer", `{"type":"integer","minimum":2}`, "#", `2`, true},
		{"not integer", `{"type":"integer"}`, "#", `2.5`, false},
		{"unique", `{"type":"array","uniqueItems":true}`, "#", `["x","x"]`, false},
		{"additional", `{"type":"object","additionalProperties":false}`, "#", `{"x":1}`, false},
		{"one of", `{"oneOf":[{"const":"a"},{"const":"b"}]}`, "#", `"b"`, true},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			document, err := Decode([]byte(test.document))
			if err != nil {
				t.Fatal(err)
			}
			instance, err := Decode([]byte(test.instance))
			if err != nil {
				t.Fatal(err)
			}
			violations := ValidateRef(document, test.ref, instance)
			if got := len(violations) == 0; got != test.valid {
				t.Fatalf("valid=%v, want %v; violations=%v", got, test.valid, violations)
			}
		})
	}
}
