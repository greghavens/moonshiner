package verify

import (
	"encoding/json"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const (
	contractPath = "../docs/contract.json"
	sourcesPath  = "../docs/official_sources.json"

	precheckOpID = "getDeploymentActions"
	mutatingOpID = "submitDeploymentActionRequest"

	precheckPath = "/deployment/api/deployments/{deploymentId}/actions"
	mutatingPath = "/deployment/api/deployments/{deploymentId}/requests"

	referenceHost = "developer.broadcom.com"
)

type param struct {
	Name     string `json:"name"`
	Type     string `json:"type"`
	Required bool   `json:"required"`
}

type bodyField struct {
	Name          string `json:"name"`
	Type          string `json:"type"`
	Required      bool   `json:"required"`
	OmitWhenUnset bool   `json:"omitWhenUnset"`
}

type requestBody struct {
	ContentType string      `json:"contentType"`
	Schema      string      `json:"schema"`
	Fields      []bodyField `json:"fields"`
}

type operation struct {
	ID          string       `json:"id"`
	Role        string       `json:"role"`
	Method      string       `json:"method"`
	Path        string       `json:"path"`
	Summary     string       `json:"summary"`
	Source      string       `json:"source"`
	PathParams  []param      `json:"pathParams"`
	QueryParams []param      `json:"queryParams"`
	RequestBody *requestBody `json:"requestBody"`
}

type contract struct {
	API struct {
		Name                   string `json:"name"`
		Product                string `json:"product"`
		Version                string `json:"version"`
		SourceType             string `json:"sourceType"`
		SpecificationAvailable *bool  `json:"specificationAvailable"`
		SourceNote             string `json:"sourceNote"`
	} `json:"api"`
	Auth struct {
		Scheme      string `json:"scheme"`
		Header      string `json:"header"`
		ValuePrefix string `json:"valuePrefix"`
	} `json:"auth"`
	Operations []operation `json:"operations"`
	Gate       struct {
		Precheck string `json:"precheck"`
		Mutating string `json:"mutating"`
		Rule     string `json:"rule"`
	} `json:"gate"`
}

type sourceEntry struct {
	URL       string `json:"url"`
	Operation string `json:"operation"`
	Title     string `json:"title"`
	FetchedOn string `json:"fetchedOn"`
}

type officialSources struct {
	Sources []sourceEntry `json:"sources"`
}

func loadContract(t *testing.T) *contract {
	t.Helper()
	b, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read %s: %v", contractPath, err)
	}
	var c contract
	if err := json.Unmarshal(b, &c); err != nil {
		t.Fatalf("parse %s: %v", contractPath, err)
	}
	return &c
}

func loadSources(t *testing.T) *officialSources {
	t.Helper()
	b, err := os.ReadFile(sourcesPath)
	if err != nil {
		t.Fatalf("read %s: %v", sourcesPath, err)
	}
	var s officialSources
	if err := json.Unmarshal(b, &s); err != nil {
		t.Fatalf("parse %s: %v", sourcesPath, err)
	}
	return &s
}

func opByID(t *testing.T, c *contract, id string) operation {
	t.Helper()
	for _, op := range c.Operations {
		if op.ID == id {
			return op
		}
	}
	t.Fatalf("contract has no operation with id %q", id)
	return operation{}
}

// TestContractDeclaresItsProvenance pins the statement that this contract came
// from reference documentation and not from a published specification.
func TestContractDeclaresItsProvenance(t *testing.T) {
	c := loadContract(t)
	if strings.TrimSpace(c.API.Name) == "" {
		t.Error("api.name is empty")
	}
	if strings.TrimSpace(c.API.Product) == "" {
		t.Error("api.product is empty")
	}

	if got := c.API.SourceType; got != "reference-documentation" {
		t.Errorf("api.sourceType = %q, want %q", got, "reference-documentation")
	}
	if c.API.SpecificationAvailable == nil {
		t.Fatal("api.specificationAvailable is missing")
	}
	if *c.API.SpecificationAvailable {
		t.Error("api.specificationAvailable = true, want false: VCF Automation has no published specification in vmware/vcf-api-specs")
	}

	note := c.API.SourceNote
	if len(note) < 80 {
		t.Errorf("api.sourceNote is %d chars, want at least 80 stating the contract's provenance", len(note))
	}
	lower := strings.ToLower(note)
	for _, want := range []string{"reference documentation", "specification"} {
		if !strings.Contains(lower, want) {
			t.Errorf("api.sourceNote does not mention %q: %q", want, note)
		}
	}

	if c.API.Version != "9.1" {
		t.Errorf("api.version = %q, want %q", c.API.Version, "9.1")
	}
}

// TestContractNamesExactlyTheTwoOperations keeps the contract, and therefore the
// mock, scoped to the precheck and the mutating call.
func TestContractNamesExactlyTheTwoOperations(t *testing.T) {
	c := loadContract(t)

	if len(c.Operations) != 2 {
		var ids []string
		for _, op := range c.Operations {
			ids = append(ids, op.ID)
		}
		t.Fatalf("contract names %d operations (%v), want exactly 2", len(c.Operations), ids)
	}

	for _, tc := range []struct {
		id     string
		role   string
		method string
		path   string
	}{
		{precheckOpID, "precheck", "GET", precheckPath},
		{mutatingOpID, "mutating", "POST", mutatingPath},
	} {
		t.Run(tc.id, func(t *testing.T) {
			op := opByID(t, c, tc.id)
			if op.Role != tc.role {
				t.Errorf("role = %q, want %q", op.Role, tc.role)
			}
			if op.Method != tc.method {
				t.Errorf("method = %q, want %q", op.Method, tc.method)
			}
			if op.Path != tc.path {
				t.Errorf("path = %q, want %q", op.Path, tc.path)
			}
			if strings.TrimSpace(op.Summary) == "" {
				t.Error("summary is empty")
			}
			if len(op.QueryParams) != 0 {
				t.Errorf("queryParams = %v, want none: neither operation documents a query parameter", op.QueryParams)
			}
			if len(op.PathParams) != 1 || op.PathParams[0].Name != "deploymentId" {
				t.Errorf("pathParams = %v, want exactly one named deploymentId", op.PathParams)
			} else {
				if op.PathParams[0].Type != "string" {
					t.Errorf("pathParams[0].type = %q, want string", op.PathParams[0].Type)
				}
				if !op.PathParams[0].Required {
					t.Error("pathParams[0].required = false, want true")
				}
			}
		})
	}
}

// TestContractPinsRequestBodyOmission is what the wire-shape assertions rest on:
// the two optional body fields must be recorded as omitted when unset.
func TestContractPinsRequestBodyOmission(t *testing.T) {
	c := loadContract(t)

	if op := opByID(t, c, precheckOpID); op.RequestBody != nil {
		t.Errorf("%s has a request body, want null: it is a GET", precheckOpID)
	}

	op := opByID(t, c, mutatingOpID)
	if op.RequestBody == nil {
		t.Fatalf("%s has no requestBody", mutatingOpID)
	}
	if got := op.RequestBody.ContentType; got != "application/json" {
		t.Errorf("requestBody.contentType = %q, want %q", got, "application/json")
	}
	if strings.TrimSpace(op.RequestBody.Schema) == "" {
		t.Error("requestBody.schema is empty")
	}

	want := map[string]bodyField{
		"actionId": {Name: "actionId", Type: "string", Required: false, OmitWhenUnset: false},
		"inputs":   {Name: "inputs", Type: "object", Required: false, OmitWhenUnset: true},
		"reason":   {Name: "reason", Type: "string", Required: false, OmitWhenUnset: true},
	}
	got := map[string]bodyField{}
	for _, f := range op.RequestBody.Fields {
		if _, dup := got[f.Name]; dup {
			t.Errorf("requestBody field %q listed twice", f.Name)
		}
		got[f.Name] = f
	}
	if len(got) != len(want) {
		t.Fatalf("requestBody names %d fields %v, want exactly %v", len(got), got, want)
	}
	for name, wantField := range want {
		gotField, ok := got[name]
		if !ok {
			t.Errorf("requestBody is missing field %q", name)
			continue
		}
		if gotField != wantField {
			t.Errorf("field %q = %+v, want %+v", name, gotField, wantField)
		}
	}
}

func TestContractDescribesBearerAuthAndTheGate(t *testing.T) {
	c := loadContract(t)

	if c.Auth.Scheme != "bearer" {
		t.Errorf("auth.scheme = %q, want %q", c.Auth.Scheme, "bearer")
	}
	if c.Auth.Header != "Authorization" {
		t.Errorf("auth.header = %q, want %q", c.Auth.Header, "Authorization")
	}
	if c.Auth.ValuePrefix != "Bearer " {
		t.Errorf("auth.valuePrefix = %q, want %q", c.Auth.ValuePrefix, "Bearer ")
	}

	if c.Gate.Precheck != precheckOpID {
		t.Errorf("gate.precheck = %q, want %q", c.Gate.Precheck, precheckOpID)
	}
	if c.Gate.Mutating != mutatingOpID {
		t.Errorf("gate.mutating = %q, want %q", c.Gate.Mutating, mutatingOpID)
	}
	if len(strings.TrimSpace(c.Gate.Rule)) < 40 {
		t.Errorf("gate.rule = %q, want a sentence describing when the mutating call is allowed", c.Gate.Rule)
	}
}

// TestOfficialSourcesRecordEveryPage checks the research trail: every page that
// was read, the operation it documents and the date it was fetched.
func TestOfficialSourcesRecordEveryPage(t *testing.T) {
	c := loadContract(t)
	s := loadSources(t)

	seen := map[string]bool{}
	byOperation := map[string]int{}
	urls := map[string]bool{}

	for i, e := range s.Sources {
		key := e.URL + "\x00" + e.Operation
		if seen[key] {
			t.Errorf("sources[%d]: duplicate url/operation pair %q %q", i, e.URL, e.Operation)
		}
		seen[key] = true
		urls[e.URL] = true

		u, err := url.Parse(e.URL)
		if err != nil {
			t.Errorf("sources[%d]: url %q does not parse: %v", i, e.URL, err)
			continue
		}
		if u.Scheme != "https" {
			t.Errorf("sources[%d]: url scheme = %q, want https", i, u.Scheme)
		}
		if u.Host != referenceHost {
			t.Errorf("sources[%d]: url host = %q, want %q", i, u.Host, referenceHost)
		}
		if !strings.Contains(u.Path, "/xapis/") {
			t.Errorf("sources[%d]: url path %q is not under /xapis/", i, u.Path)
		}
		if strings.TrimSpace(e.Operation) == "" {
			t.Errorf("sources[%d]: operation is empty; record what the page documents", i)
		}
		if strings.TrimSpace(e.Title) == "" {
			t.Errorf("sources[%d]: title is empty", i)
		}
		byOperation[e.Operation]++

		_, err = time.Parse("2006-01-02", e.FetchedOn)
		if err != nil {
			t.Errorf("sources[%d]: fetchedOn %q is not YYYY-MM-DD: %v", i, e.FetchedOn, err)
		}
	}

	for _, op := range c.Operations {
		if byOperation[op.ID] == 0 {
			t.Errorf("no source entry records operation %q", op.ID)
		}
		if op.Source == "" {
			t.Errorf("operation %q has no source url", op.ID)
			continue
		}
		u, err := url.Parse(op.Source)
		if err != nil || u.Scheme != "https" || u.Host != referenceHost {
			t.Errorf("operation %q source = %q, want an https %s url", op.ID, op.Source, referenceHost)
		}
		if !urls[op.Source] {
			t.Errorf("operation %q cites %q, which is not recorded in official_sources.json", op.ID, op.Source)
		}
	}
}

// TestDocsAreTheOnlyGeneratedArtifacts guards against the contract being written
// somewhere the mock will not find it.
func TestDocsLiveWhereTheMockExpectsThem(t *testing.T) {
	for _, p := range []string{contractPath, sourcesPath} {
		if _, err := os.Stat(filepath.Clean(p)); err != nil {
			t.Errorf("stat %s: %v", p, err)
		}
	}
}
