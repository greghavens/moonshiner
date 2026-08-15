package jsonschema

import "testing"

func TestValidator(t *testing.T) {
	schema := []byte(`{
		"type":"object",
		"additionalProperties":false,
		"required":["name","count","tags"],
		"properties":{
			"name":{"type":"string","minLength":3},
			"count":{"type":"integer","minimum":1},
			"tags":{"type":"array","minItems":1,"uniqueItems":true,"items":{"$ref":"#/$defs/tag"}}
		},
		"$defs":{"tag":{"type":"string","minLength":2}}
	}`)
	validator, err := Compile(schema)
	if err != nil {
		t.Fatalf("Compile() error = %v", err)
	}

	tests := []struct {
		name    string
		doc     string
		wantErr bool
	}{
		{name: "valid", doc: `{"name":"northstar","count":2,"tags":["ha","dr"]}`},
		{name: "missing required", doc: `{"name":"northstar","count":2}`, wantErr: true},
		{name: "additional property", doc: `{"name":"northstar","count":2,"tags":["ha"],"extra":true}`, wantErr: true},
		{name: "non integer", doc: `{"name":"northstar","count":2.5,"tags":["ha"]}`, wantErr: true},
		{name: "minimum", doc: `{"name":"northstar","count":0,"tags":["ha"]}`, wantErr: true},
		{name: "duplicate", doc: `{"name":"northstar","count":2,"tags":["ha","ha"]}`, wantErr: true},
		{name: "trailing document", doc: `{"name":"northstar","count":2,"tags":["ha"]} {}`, wantErr: true},
		{name: "malformed trailing document", doc: `{"name":"northstar","count":2,"tags":["ha"]} {`, wantErr: true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := validator.Validate([]byte(tt.doc))
			if (err != nil) != tt.wantErr {
				t.Fatalf("Validate() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}
