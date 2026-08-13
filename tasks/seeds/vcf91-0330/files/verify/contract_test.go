package verify

import (
	"encoding/json"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------------------
// contract shape
// ---------------------------------------------------------------------------

type contractDoc struct {
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
	Operations []contractOp `json:"operations"`
}

type contractOp struct {
	ID         string          `json:"id"`
	Method     string          `json:"method"`
	Path       string          `json:"path"`
	Summary    string          `json:"summary"`
	Source     string          `json:"source"`
	PathParams []contractParam `json:"pathParams"`
	Query      []contractParam `json:"queryParams"`
	Response   struct {
		Name         string   `json:"name"`
		ContentField string   `json:"contentField"`
		ItemFields   []string `json:"itemFields"`
		Fields       []string `json:"fields"`
		PageFields   []string `json:"pageFields"`
		SliceFields  []string `json:"sliceFields"`
	} `json:"responseSchema"`
}

type contractParam struct {
	Name          string `json:"name"`
	Type          string `json:"type"`
	Required      *bool  `json:"required"`
	OmitWhenUnset *bool  `json:"omitWhenUnset"`
}

type paramWant struct {
	name, typ      string
	required, omit bool
}

type sourcesDoc struct {
	Sources []struct {
		URL       string `json:"url"`
		Title     string `json:"title"`
		Operation string `json:"operation"`
		FetchedOn string `json:"fetchedOn"`
	} `json:"sources"`
}

// The four operations of the diagnosis chain, in the order the client walks
// them. Paths are the reference documentation's, with its parameter names.
var wantOps = []struct {
	id     string
	method string
	path   string
}{
	{"getDeploymentRequests", "GET", "/deployment/api/deployments/{deploymentId}/requests"},
	{"getRequest", "GET", "/deployment/api/requests/{requestId}"},
	{"getRequestEvents", "GET", "/deployment/api/requests/{requestId}/events"},
	{"getEventLogs", "GET", "/deployment/api/requests/{requestId}/events/{eventId}/logs"},
}

func repoRoot(t *testing.T) string {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	return filepath.Dir(wd)
}

func loadContract(t *testing.T) contractDoc {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(repoRoot(t), "docs", "contract.json"))
	if err != nil {
		t.Fatalf("docs/contract.json: %v", err)
	}
	var c contractDoc
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	if err := dec.Decode(&c); err != nil {
		t.Fatalf("docs/contract.json is not valid JSON: %v", err)
	}
	return c
}

func TestContractDeclaresItsSourceIsReferenceDocumentation(t *testing.T) {
	c := loadContract(t)

	if got := c.API.Version; got != "9.1" {
		t.Errorf("api.version = %q, want %q", got, "9.1")
	}
	if got := c.API.SourceType; got != "reference-documentation" {
		t.Errorf("api.sourceType = %q, want %q", got, "reference-documentation")
	}
	if c.API.SpecificationAvailable == nil {
		t.Fatal("api.specificationAvailable is missing; it must be present and false")
	}
	if *c.API.SpecificationAvailable {
		t.Error("api.specificationAvailable = true, want false: VCF Automation has no published specification")
	}

	// The note has to say this plainly, not just carry the two words.
	note := c.API.SourceNote
	if len(note) < 80 {
		t.Errorf("api.sourceNote is %d characters, want at least 80: it must state plainly that the "+
			"contract is derived from reference documentation rather than a published specification", len(note))
	}
	low := strings.ToLower(note)
	for _, want := range []string{"reference documentation", "specification"} {
		if !strings.Contains(low, want) {
			t.Errorf("api.sourceNote does not mention %q: %q", want, note)
		}
	}

	if c.API.Product == "" {
		t.Error("api.product is empty")
	}
	if c.Auth.Scheme != "bearer" {
		t.Errorf("auth.scheme = %q, want %q", c.Auth.Scheme, "bearer")
	}
	if c.Auth.Header != "Authorization" {
		t.Errorf("auth.header = %q, want %q", c.Auth.Header, "Authorization")
	}
	if strings.TrimSpace(c.Auth.ValuePrefix) != "Bearer" {
		t.Errorf("auth.valuePrefix = %q, want %q", c.Auth.ValuePrefix, "Bearer ")
	}
}

func TestContractNamesExactlyTheDiagnosisChain(t *testing.T) {
	c := loadContract(t)

	if len(c.Operations) != len(wantOps) {
		var got []string
		for _, op := range c.Operations {
			got = append(got, op.ID)
		}
		t.Fatalf("contract names %d operations %v, want exactly the %d of the diagnosis chain",
			len(c.Operations), got, len(wantOps))
	}

	byID := map[string]contractOp{}
	for _, op := range c.Operations {
		if _, dup := byID[op.ID]; dup {
			t.Fatalf("operation id %q appears twice", op.ID)
		}
		byID[op.ID] = op
	}

	for _, want := range wantOps {
		t.Run(want.id, func(t *testing.T) {
			op, ok := byID[want.id]
			if !ok {
				t.Fatalf("contract does not name operation %q", want.id)
			}
			if op.Method != want.method {
				t.Errorf("method = %q, want %q", op.Method, want.method)
			}
			if op.Path != want.path {
				t.Errorf("path = %q, want %q", op.Path, want.path)
			}
			if strings.TrimSpace(op.Summary) == "" {
				t.Error("summary is empty")
			}

			// Every path parameter in the template is declared, and required.
			for _, name := range templateParams(op.Path) {
				var found bool
				for _, p := range op.PathParams {
					if p.Name != name {
						continue
					}
					found = true
					if p.Required == nil || !*p.Required {
						t.Errorf("path parameter %q must be declared required", name)
					}
				}
				if !found {
					t.Errorf("path parameter %q appears in the path but is not declared in pathParams", name)
				}
			}

			assertSourceURL(t, op.Source, "source")
		})
	}
}

// The query parameters the client depends on, and whether the contract has to
// record them as omitted when the caller leaves them unset.
func TestContractRecordsQueryParameterOmission(t *testing.T) {
	c := loadContract(t)
	byID := map[string]contractOp{}
	for _, op := range c.Operations {
		byID[op.ID] = op
	}

	cases := []struct {
		op            string
		param         string
		omitWhenUnset bool
	}{
		{"getDeploymentRequests", "size", true},
		{"getDeploymentRequests", "search", true},
		{"getDeploymentRequests", "sort", false},
		{"getRequestEvents", "size", true},
		{"getRequestEvents", "sort", false},
		{"getEventLogs", "sinceRow", true},
	}

	for _, tc := range cases {
		t.Run(tc.op+"/"+tc.param, func(t *testing.T) {
			op, ok := byID[tc.op]
			if !ok {
				t.Fatalf("contract does not name operation %q", tc.op)
			}
			for _, p := range op.Query {
				if p.Name != tc.param {
					continue
				}
				if p.OmitWhenUnset == nil {
					t.Fatalf("queryParams[%q].omitWhenUnset is missing; it records whether the client "+
						"leaves the parameter out of the query string entirely when it is unset", tc.param)
				}
				if *p.OmitWhenUnset != tc.omitWhenUnset {
					t.Errorf("queryParams[%q].omitWhenUnset = %v, want %v", tc.param, *p.OmitWhenUnset, tc.omitWhenUnset)
				}
				return
			}
			t.Fatalf("operation %q does not declare query parameter %q", tc.op, tc.param)
		})
	}
}

// getRequest takes no query parameters at all, per the reference.
func TestContractGivesGetRequestNoQueryParameters(t *testing.T) {
	c := loadContract(t)
	for _, op := range c.Operations {
		if op.ID != "getRequest" {
			continue
		}
		if len(op.Query) != 0 {
			var names []string
			for _, p := range op.Query {
				names = append(names, p.Name)
			}
			t.Errorf("getRequest declares query parameters %v, want none", names)
		}
		return
	}
	t.Fatal("contract does not name operation getRequest")
}

// These are all the parameters and successful-response fields documented on
// the four cited 9.1 operation pages. Checking the full shape keeps a minimal
// hand-written manifest from passing without doing the requested research.
func TestContractRecordsTheDocumentedParametersAndResponses(t *testing.T) {
	type responseWant struct {
		name, content           string
		items, fields, envelope []string
	}
	type operationWant struct {
		pathParams, query []paramWant
		response          responseWant
	}

	pageFields := []string{"content", "empty", "first", "last", "number", "numberOfElements", "pageable", "size", "sort", "totalElements", "totalPages"}
	requestFields := []string{"actionId", "approvedAt", "blueprintId", "cancelable", "catalogItemId", "completedAt", "completedTasks", "createdAt", "deploymentId", "details", "dismissed", "estimatedCompletionTime", "id", "initializedAt", "inputs", "name", "outputs", "requestedBy", "resourceIds", "resources", "status", "totalTasks", "updatedAt"}

	wants := map[string]operationWant{
		"getDeploymentRequests": {
			pathParams: []paramWant{{"deploymentId", "string", true, false}},
			query: []paramWant{
				{"deleted", "boolean", false, true},
				{"inprogressRequests", "boolean", false, true},
				{"search", "string", false, true},
				{"page", "integer", false, true},
				{"size", "integer", false, true},
				{"sort", "string", false, false},
				{"$top", "integer", false, true},
				{"$skip", "integer", false, true},
				{"$orderby", "string", false, true},
			},
			response: responseWant{"PageRequest", "content", requestFields, nil, pageFields},
		},
		"getRequest": {
			pathParams: []paramWant{{"requestId", "string", true, false}},
			response:   responseWant{"Request", "", nil, requestFields, nil},
		},
		"getRequestEvents": {
			pathParams: []paramWant{{"requestId", "string", true, false}},
			query: []paramWant{
				{"page", "integer", false, true},
				{"size", "integer", false, true},
				{"sort", "string", false, false},
			},
			response: responseWant{
				"PageEvent", "content",
				[]string{"details", "hasLogs", "id", "name", "resourceName", "resourceType", "timestamp", "userEvent"},
				nil, pageFields,
			},
		},
		"getEventLogs": {
			pathParams: []paramWant{{"requestId", "string", true, false}, {"eventId", "string", true, false}},
			query:      []paramWant{{"sinceRow", "integer", false, true}},
			response: responseWant{
				"SliceEventLog", "content",
				[]string{"eof", "id", "message", "rownum", "timestamp"}, nil,
				[]string{"content", "empty", "first", "last", "number", "numberOfElements", "pageable", "size", "sort"},
			},
		},
	}

	for _, op := range loadContract(t).Operations {
		want, ok := wants[op.ID]
		if !ok {
			continue
		}
		t.Run(op.ID, func(t *testing.T) {
			assertParams(t, "pathParams", op.PathParams, want.pathParams)
			assertParams(t, "queryParams", op.Query, want.query)
			if op.Response.Name != want.response.name {
				t.Errorf("responseSchema.name = %q, want %q", op.Response.Name, want.response.name)
			}
			if op.Response.ContentField != want.response.content {
				t.Errorf("responseSchema.contentField = %q, want %q", op.Response.ContentField, want.response.content)
			}
			assertStringSet(t, "responseSchema.itemFields", op.Response.ItemFields, want.response.items)
			assertStringSet(t, "responseSchema.fields", op.Response.Fields, want.response.fields)
			envelope := op.Response.PageFields
			if op.ID == "getEventLogs" {
				envelope = op.Response.SliceFields
			}
			assertStringSet(t, "response envelope fields", envelope, want.response.envelope)
		})
	}
}

// ---------------------------------------------------------------------------
// sources
// ---------------------------------------------------------------------------

func TestOfficialSourcesRecordEveryPageRead(t *testing.T) {
	root := repoRoot(t)

	raw, err := os.ReadFile(filepath.Join(root, "docs", "official_sources.json"))
	if err != nil {
		t.Fatalf("docs/official_sources.json: %v", err)
	}
	var s sourcesDoc
	if err := json.Unmarshal(raw, &s); err != nil {
		t.Fatalf("docs/official_sources.json is not valid JSON: %v", err)
	}
	if len(s.Sources) == 0 {
		t.Fatal("docs/official_sources.json records no sources")
	}

	recorded := map[string]bool{} // url -> seen
	operations := map[string]bool{}

	for i, src := range s.Sources {
		assertSourceURL(t, src.URL, "sources["+itoa(i)+"].url")
		if strings.TrimSpace(src.Title) == "" {
			t.Errorf("sources[%d].title is empty", i)
		}
		if strings.TrimSpace(src.Operation) == "" {
			t.Errorf("sources[%d].operation is empty: name the contract operation the page documents, "+
				"or a label such as api-overview for a page that covers no single operation", i)
		}
		if _, err := time.Parse("2006-01-02", src.FetchedOn); err != nil {
			t.Errorf("sources[%d].fetchedOn = %q, want a YYYY-MM-DD date", i, src.FetchedOn)
		}
		recorded[src.URL] = true
		operations[src.Operation] = true
	}

	// Every operation of the chain was read from somewhere.
	for _, want := range wantOps {
		if !operations[want.id] {
			t.Errorf("no source records operation %q", want.id)
		}
	}

	// And every URL the contract cites was recorded.
	for _, op := range loadContract(t).Operations {
		if op.Source != "" && !recorded[op.Source] {
			t.Errorf("operation %q cites source %q, which docs/official_sources.json does not record",
				op.ID, op.Source)
		}
	}
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

var templateParamRe = regexp.MustCompile(`\{([^{}]+)\}`)

func templateParams(path string) []string {
	var out []string
	for _, m := range templateParamRe.FindAllStringSubmatch(path, -1) {
		out = append(out, m[1])
	}
	return out
}

func assertSourceURL(t *testing.T, raw, field string) {
	t.Helper()
	if strings.TrimSpace(raw) == "" {
		t.Errorf("%s is empty: cite the developer.broadcom.com page it was read from", field)
		return
	}
	u, err := url.Parse(raw)
	if err != nil {
		t.Errorf("%s = %q is not a URL: %v", field, raw, err)
		return
	}
	if u.Scheme != "https" {
		t.Errorf("%s = %q, want an https URL", field, raw)
	}
	if u.Host != "developer.broadcom.com" {
		t.Errorf("%s = %q, want a page on developer.broadcom.com (the authoritative xAPIs reference)", field, raw)
	}
}

func assertParams(t *testing.T, field string, got []contractParam, want []paramWant) {
	t.Helper()
	if len(got) != len(want) {
		t.Errorf("%s has %d parameters, want %d", field, len(got), len(want))
	}
	byName := make(map[string]contractParam, len(got))
	for _, p := range got {
		if _, duplicate := byName[p.Name]; duplicate {
			t.Errorf("%s contains duplicate parameter %q", field, p.Name)
		}
		byName[p.Name] = p
	}
	for _, expected := range want {
		p, ok := byName[expected.name]
		if !ok {
			t.Errorf("%s does not document %q", field, expected.name)
			continue
		}
		if p.Type != expected.typ {
			t.Errorf("%s[%q].type = %q, want %q", field, expected.name, p.Type, expected.typ)
		}
		if p.Required == nil || *p.Required != expected.required {
			t.Errorf("%s[%q].required = %v, want %v", field, expected.name, p.Required, expected.required)
		}
		if field == "queryParams" && (p.OmitWhenUnset == nil || *p.OmitWhenUnset != expected.omit) {
			t.Errorf("%s[%q].omitWhenUnset = %v, want %v", field, expected.name, p.OmitWhenUnset, expected.omit)
		}
	}
}

func assertStringSet(t *testing.T, field string, got, want []string) {
	t.Helper()
	if len(got) != len(want) {
		t.Errorf("%s = %v (%d fields), want exactly %v (%d fields)", field, got, len(got), want, len(want))
	}
	seen := make(map[string]bool, len(got))
	for _, value := range got {
		if seen[value] {
			t.Errorf("%s contains duplicate field %q", field, value)
		}
		seen[value] = true
	}
	for _, expected := range want {
		if !seen[expected] {
			t.Errorf("%s does not document %q", field, expected)
		}
	}
}

func itoa(i int) string {
	if i == 0 {
		return "0"
	}
	var b []byte
	for i > 0 {
		b = append([]byte{byte('0' + i%10)}, b...)
		i /= 10
	}
	return string(b)
}
