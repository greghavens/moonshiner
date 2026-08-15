package verifier

import "testing"

func TestValidateJSONSchemaTable(t *testing.T) {
	schema := []byte(`{
      "type":"object",
      "additionalProperties":false,
      "required":["name","values"],
      "properties":{
        "name":{"type":"string","pattern":"^[a-z]+$","minLength":2},
        "values":{"type":"array","minItems":2,"uniqueItems":true,"items":{"type":"integer","minimum":1}}
      }
    }`)
	tests := []struct {
		name     string
		document string
		wantErr  bool
	}{
		{name: "valid", document: `{"name":"ok","values":[1,2]}`},
		{name: "missing required", document: `{"name":"ok"}`, wantErr: true},
		{name: "pattern", document: `{"name":"NO","values":[1,2]}`, wantErr: true},
		{name: "duplicate", document: `{"name":"ok","values":[1,1]}`, wantErr: true},
		{name: "additional", document: `{"name":"ok","values":[1,2],"extra":true}`, wantErr: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			err := validateJSONSchema([]byte(test.document), schema)
			if (err != nil) != test.wantErr {
				t.Fatalf("validateJSONSchema() error = %v, wantErr %v", err, test.wantErr)
			}
		})
	}
}
