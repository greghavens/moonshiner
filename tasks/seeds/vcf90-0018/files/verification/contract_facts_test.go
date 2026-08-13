package verification

import (
	"encoding/json"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// Digests of the normalised facts that docs/contract.json must carry. They are
// pinned rather than spelled out so that the contract has to be derived from
// the specification document itself.
const (
	wantSpecRefDigest            = "b4ca6f45d9f073bd5a35fb88bda59d3d5c85fdf1b848277e6d358a2971e86457"
	wantSpecInfoDigest           = "c3836b758e0851be6942444cb44f392e3439e7cf0055e859ba2c490ae859a77b"
	wantOperationsDigest         = "7e5dce5cdcc58232b8e03b30bacc2c236e69f060707c3c9546b3394c8ae23a9c"
	wantCredentialsQueryDigest   = "d3f439f130064bc2f08191ef736fb52f616063880251041f0cc30dad55556a6a"
	wantResourceTypeValuesDigest = "5ff062227647d024d189d99a3bf6b032ffc638c4a56d63def614e61e89fef749"
	wantRequestBodiesDigest      = "dc3fdb0b5ee44790c2158757d2e4f1e659507430bbf3c08e68669f4c457029b2"
)

const (
	contractPath = "../docs/contract.json"
	sourcesPath  = "../docs/official_sources.json"
)

func loadContractT(t *testing.T) *Contract {
	t.Helper()
	c, err := LoadContract(contractPath)
	if err != nil {
		t.Fatalf("load %s: %v", contractPath, err)
	}
	return c
}

func TestContractFactsMatchSpecification(t *testing.T) {
	c := loadContractT(t)

	cases := []struct {
		name    string
		label   string
		payload string
		want    string
	}{
		{"spec reference", "spec-ref", c.SpecRefPayload(), wantSpecRefDigest},
		{"spec identity", "spec-info", c.SpecInfoPayload(), wantSpecInfoDigest},
		{"operations", "operations", c.OperationsPayload(), wantOperationsDigest},
		{"getCredentials query parameters", "get-credentials-query", c.QueryPayload("getCredentials"), wantCredentialsQueryDigest},
		{"resourceType values", "resource-type-values", c.AllowedValuesPayload("getCredentials", "resourceType"), wantResourceTypeValuesDigest},
		{"request bodies", "request-bodies", c.RequestBodiesPayload(), wantRequestBodiesDigest},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := Digest(tc.label, tc.payload)
			if got != tc.want {
				t.Errorf("%s does not match the pinned specification facts\ndigest of contract.json: %s\nnormalised value derived from contract.json:\n%s",
					tc.name, got, tc.payload)
			}
		})
	}
}

func TestContractNamesExactlyTheThreeOperations(t *testing.T) {
	c := loadContractT(t)

	if len(c.Operations) != 3 {
		t.Fatalf("contract must name exactly 3 operations, got %d", len(c.Operations))
	}
	got := make([]string, 0, len(c.Operations))
	for _, op := range c.Operations {
		got = append(got, op.OperationID)
	}
	sort.Strings(got)
	want := []string{"createToken", "getCredentials", "refreshAccessToken"}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("contract operations = %v, want %v", got, want)
		}
	}

	// The paging operation is the only one carrying query parameters, and the
	// token operations are the only ones carrying request bodies.
	for _, op := range c.Operations {
		switch op.OperationID {
		case "getCredentials":
			if len(op.QueryParameters) == 0 {
				t.Errorf("getCredentials must list its query parameters")
			}
			if op.RequestBody != nil {
				t.Errorf("getCredentials has no request body in the specification")
			}
		default:
			if op.RequestBody == nil {
				t.Errorf("%s must describe its request body", op.OperationID)
			}
			if len(op.QueryParameters) != 0 {
				t.Errorf("%s has no query parameters in the specification", op.OperationID)
			}
		}
	}
}

func TestContractUsesTheRequiredShapeAndOmitsAbsentOptionalFields(t *testing.T) {
	raw, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read %s: %v", contractPath, err)
	}
	var doc struct {
		Operations []json.RawMessage `json:"operations"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("decode %s: %v", contractPath, err)
	}

	for _, rawOperation := range doc.Operations {
		var operation Operation
		if err := json.Unmarshal(rawOperation, &operation); err != nil {
			t.Fatalf("decode operation: %v", err)
		}
		var fields map[string]json.RawMessage
		if err := json.Unmarshal(rawOperation, &fields); err != nil {
			t.Fatalf("decode operation fields: %v", err)
		}
		_, hasRequestBody := fields["requestBody"]
		if hasRequestBody != (operation.RequestBody != nil) {
			t.Errorf("%s: requestBody must be omitted exactly when the operation takes no body", operation.OperationID)
		}
		_, hasQueryParameters := fields["queryParameters"]
		if hasQueryParameters != (len(operation.QueryParameters) > 0) {
			t.Errorf("%s: queryParameters must be omitted exactly when the operation takes none", operation.OperationID)
		}

		if operation.RequestBody != nil {
			var bodyFields map[string]json.RawMessage
			if err := json.Unmarshal(fields["requestBody"], &bodyFields); err != nil {
				t.Fatalf("%s requestBody: %v", operation.OperationID, err)
			}
			for _, required := range []string{"required", "contentType", "schema", "properties"} {
				if _, ok := bodyFields[required]; !ok {
					t.Errorf("%s requestBody is missing required field %q", operation.OperationID, required)
				}
			}
		}

		if len(operation.QueryParameters) == 0 {
			continue
		}
		var rawParameters []json.RawMessage
		if err := json.Unmarshal(fields["queryParameters"], &rawParameters); err != nil {
			t.Fatalf("%s queryParameters: %v", operation.OperationID, err)
		}
		if len(rawParameters) != len(operation.QueryParameters) {
			t.Fatalf("%s: decoded %d query parameters from %d raw entries", operation.OperationID, len(operation.QueryParameters), len(rawParameters))
		}
		for i, parameter := range operation.QueryParameters {
			var parameterFields map[string]json.RawMessage
			if err := json.Unmarshal(rawParameters[i], &parameterFields); err != nil {
				t.Fatalf("%s query parameter %d: %v", operation.OperationID, i, err)
			}
			for _, required := range []string{"name", "required"} {
				if _, ok := parameterFields[required]; !ok {
					t.Errorf("%s parameter %q is missing required field %q", operation.OperationID, parameter.Name, required)
				}
			}
			optional := []struct {
				name    string
				present bool
			}{
				{"deprecated", parameter.Deprecated},
				{"default", parameter.Default != ""},
				{"allowedValues", len(parameter.AllowedValues) > 0},
			}
			for _, field := range optional {
				_, present := parameterFields[field.name]
				if present != field.present {
					t.Errorf("%s parameter %q: field %q presence = %t, want %t", operation.OperationID, parameter.Name, field.name, present, field.present)
				}
			}
		}
	}
}

func TestOfficialSourcesRecordTheSpecification(t *testing.T) {
	c := loadContractT(t)

	s, err := LoadSources(sourcesPath)
	if err != nil {
		t.Fatalf("load %s: %v", sourcesPath, err)
	}
	if len(s.Sources) != 1 {
		t.Fatalf("expected exactly one recorded source, got %d", len(s.Sources))
	}
	src := s.Sources[0]

	if src.Repository != c.Spec.Repository {
		t.Errorf("source repository = %q, contract repository = %q", src.Repository, c.Spec.Repository)
	}
	if src.Path != c.Spec.Path {
		t.Errorf("source path = %q, contract path = %q", src.Path, c.Spec.Path)
	}
	if src.Tag != c.Spec.Tag {
		t.Errorf("source tag = %q, contract tag = %q", src.Tag, c.Spec.Tag)
	}
	if !strings.EqualFold(src.Commit, c.Spec.Commit) {
		t.Errorf("source commit = %q, contract commit = %q", src.Commit, c.Spec.Commit)
	}
	commitPattern := regexp.MustCompile(`^[0-9a-f]{40}$`)
	if !commitPattern.MatchString(c.Spec.Commit) {
		t.Errorf("contract commit = %q, want a 40-character lower-case commit sha", c.Spec.Commit)
	}
	if !commitPattern.MatchString(src.Commit) {
		t.Errorf("source commit = %q, want a 40-character lower-case commit sha", src.Commit)
	}
	if src.License != "Apache-2.0" {
		t.Errorf("source license = %q, want %q", src.License, "Apache-2.0")
	}
	if strings.TrimSpace(src.Title) == "" {
		t.Errorf("source title must not be empty")
	}

	u, err := url.Parse(src.URL)
	if err != nil {
		t.Fatalf("source url %q: %v", src.URL, err)
	}
	if u.Scheme != "https" {
		t.Errorf("source url scheme = %q, want https", u.Scheme)
	}
	switch u.Host {
	case "github.com":
		wantPath := "/" + c.Spec.Repository + "/blob/" + c.Spec.Commit + "/" + c.Spec.Path
		if u.Path != wantPath {
			t.Errorf("source url path = %q, want %q", u.Path, wantPath)
		}
	case "raw.githubusercontent.com":
		wantPath := "/" + c.Spec.Repository + "/" + c.Spec.Commit + "/" + c.Spec.Path
		if u.Path != wantPath {
			t.Errorf("source url path = %q, want %q", u.Path, wantPath)
		}
	default:
		t.Errorf("source url host = %q, want the repository host", u.Host)
	}
	if u.RawQuery != "" || u.Fragment != "" {
		t.Errorf("source url %q must end with the specification path and carry no query or fragment", src.URL)
	}

	gotOps := append([]string(nil), src.OperationIDs...)
	sort.Strings(gotOps)
	wantOps := make([]string, 0, len(c.Operations))
	for _, op := range c.Operations {
		wantOps = append(wantOps, op.OperationID)
	}
	sort.Strings(wantOps)
	if len(gotOps) != len(wantOps) {
		t.Fatalf("source operationIds = %v, want %v", gotOps, wantOps)
	}
	for i := range wantOps {
		if gotOps[i] != wantOps[i] {
			t.Fatalf("source operationIds = %v, want %v", gotOps, wantOps)
		}
	}
}
