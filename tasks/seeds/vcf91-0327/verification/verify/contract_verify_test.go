// Package verify holds the acceptance tests for the vcfauto package.
//
// These tests are protected: they define what "done" means for this task and
// must not be edited, renamed, moved, or deleted. They contact no host other
// than the loopback listener started by vcfauto.NewMock.
package verify

import (
	"context"
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"

	"example.com/vcfauto"
)

const (
	contractPath = "../docs/contract.json"
	sourcesPath  = "../docs/official_sources.json"
	docsPortal   = "developer.broadcom.com"
)

// requiredOps pins the operation set the contract must cover, with the wire
// method and path template each one uses.
var requiredOps = []struct {
	ID             string
	Method         string
	Path           string
	RequestSchema  string
	ResponseSchema string
	Codes          []int
	DocURL         string
	Fields         map[string]string
}{
	{"GetDeployment", "GET", "/deployment/api/deployments/{deploymentId}", "", "Deployment", []int{200, 401, 404}, "https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/deployments/deploymentId/get/", nil},
	{"PatchDeployment", "PATCH", "/deployment/api/deployments/{deploymentId}", "DeploymentUpdate", "Deployment", []int{200, 401, 403, 404}, "https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/deployments/deploymentId/patch/", map[string]string{"name": "string", "description": "string", "iconId": "string"}},
	{"GetDeploymentActions", "GET", "/deployment/api/deployments/{deploymentId}/actions", "", "ResourceAction[]", []int{200, 401, 404}, "https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/deployments/deploymentId/actions/get/", nil},
	{"SubmitDeploymentActionRequest", "POST", "/deployment/api/deployments/{deploymentId}/requests", "ResourceActionRequest", "Request", []int{200, 401, 403, 404, 409}, "https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/deployments/deploymentId/requests/post/", map[string]string{"actionId": "string", "inputs": "object", "reason": "string"}},
	{"GetDeploymentResources", "GET", "/deployment/api/deployments/{deploymentId}/resources", "", "PageDeploymentResource", []int{200, 401, 404}, "https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/deployments/deploymentId/resources/get/", nil},
	{"SubmitResourceActionRequest", "POST", "/deployment/api/deployments/{deploymentId}/resources/{resourceId}/requests", "ResourceActionRequest", "Request", []int{200, 401, 403, 404, 409}, "https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/deployments/deploymentId/resources/resourceId/requests/post/", map[string]string{"actionId": "string", "inputs": "object", "reason": "string"}},
	{"GetRequest", "GET", "/deployment/api/requests/{requestId}", "", "Request", []int{200, 401, 404}, "https://developer.broadcom.com/xapis/vm-apps-org-deployment/latest/deployment/api/requests/requestId/get/", nil},
}

// ---------------------------------------------------------------------------
// docs/contract.json
// ---------------------------------------------------------------------------

type rawContract struct {
	Product    string `json:"product"`
	Release    string `json:"release"`
	Provenance struct {
		SourceKind             string `json:"source_kind"`
		Portal                 string `json:"portal"`
		SpecificationAvailable *bool  `json:"specification_available"`
		Statement              string `json:"statement"`
	} `json:"provenance"`
	Operations []struct {
		ID                    string `json:"id"`
		Summary               string `json:"summary"`
		Method                string `json:"method"`
		PathTemplate          string `json:"path_template"`
		RequestSchema         string `json:"request_schema"`
		ResponseSchema        string `json:"response_schema"`
		DocumentedStatusCodes []int  `json:"documented_status_codes"`
		DocURL                string `json:"doc_url"`
		RequestFields         []struct {
			Name     string `json:"name"`
			Type     string `json:"type"`
			Required bool   `json:"required"`
			Notes    string `json:"notes"`
		} `json:"request_fields"`
	} `json:"operations"`
}

func readJSON(t *testing.T, path string, into any) []byte {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", filepath.Base(path), err)
	}
	if err := json.Unmarshal(b, into); err != nil {
		t.Fatalf("parse %s: %v", filepath.Base(path), err)
	}
	return b
}

func loadRawContract(t *testing.T) (*rawContract, []byte) {
	t.Helper()
	var rc rawContract
	b := readJSON(t, contractPath, &rc)
	return &rc, b
}

func TestContractDeclaresReferenceDerivedProvenance(t *testing.T) {
	rc, raw := loadRawContract(t)

	if !strings.Contains(strings.ToLower(rc.Product), "automation") {
		t.Errorf("contract.product = %q, want it to identify VCF Automation", rc.Product)
	}
	if rc.Release != "9.1" {
		t.Errorf("contract.release = %q, want 9.1", rc.Release)
	}

	p := rc.Provenance
	if p.SpecificationAvailable == nil {
		t.Fatal("contract.provenance.specification_available is missing; it must be present and false")
	}
	if *p.SpecificationAvailable {
		t.Error("contract.provenance.specification_available must be false: VCF Automation has no published specification in vmware/vcf-api-specs")
	}
	if got := strings.ToLower(p.SourceKind); !strings.Contains(got, "reference") {
		t.Errorf("contract.provenance.source_kind = %q, want it to identify the source as reference documentation", p.SourceKind)
	}
	if !strings.Contains(p.Portal, docsPortal) {
		t.Errorf("contract.provenance.portal = %q, want it to name %s", p.Portal, docsPortal)
	}

	// The statement must say plainly, in prose, that this contract was derived
	// from reference documentation rather than from a published specification.
	stmt := strings.ToLower(p.Statement)
	if len(strings.Fields(stmt)) < 12 {
		t.Fatalf("contract.provenance.statement is too terse to state the caveat plainly: %q", p.Statement)
	}
	for _, want := range []string{"reference documentation", "specification", "lag", "appliance"} {
		if !strings.Contains(stmt, want) {
			t.Errorf("contract.provenance.statement does not mention %q: %q", want, p.Statement)
		}
	}

	if strings.Contains(string(raw), ".invalid") {
		t.Error("contract.json references a .invalid host; every URL must be a real page on the vendor portal")
	}
}

func TestContractCoversRequiredOperations(t *testing.T) {
	rc, _ := loadRawContract(t)
	if len(rc.Operations) != len(requiredOps) {
		t.Errorf("contract has %d operations, want exactly %d", len(rc.Operations), len(requiredOps))
	}

	byID := map[string]int{}
	for i, op := range rc.Operations {
		if _, dup := byID[op.ID]; dup {
			t.Errorf("duplicate operation id %q", op.ID)
		}
		byID[op.ID] = i
	}

	for _, want := range requiredOps {
		i, ok := byID[want.ID]
		if !ok {
			t.Errorf("contract is missing operation %q", want.ID)
			continue
		}
		got := rc.Operations[i]
		if strings.TrimSpace(got.Summary) == "" {
			t.Errorf("%s: summary is empty", want.ID)
		}
		if got.Method != want.Method {
			t.Errorf("%s: method = %q, want %q", want.ID, got.Method, want.Method)
		}
		if got.PathTemplate != want.Path {
			t.Errorf("%s: path_template = %q, want %q", want.ID, got.PathTemplate, want.Path)
		}
		if got.RequestSchema != want.RequestSchema {
			t.Errorf("%s: request_schema = %q, want %q", want.ID, got.RequestSchema, want.RequestSchema)
		}
		if got.ResponseSchema != want.ResponseSchema {
			t.Errorf("%s: response_schema = %q, want %q", want.ID, got.ResponseSchema, want.ResponseSchema)
		}

		gotCodes := append([]int(nil), got.DocumentedStatusCodes...)
		wantCodes := append([]int(nil), want.Codes...)
		sort.Ints(gotCodes)
		sort.Ints(wantCodes)
		if !reflect.DeepEqual(gotCodes, wantCodes) {
			t.Errorf("%s: documented_status_codes = %v, want every documented code %v", want.ID, got.DocumentedStatusCodes, want.Codes)
		}
		if got.DocURL != want.DocURL {
			t.Errorf("%s: doc_url = %q, want the operation reference page %q", want.ID, got.DocURL, want.DocURL)
		}

		gotFields := map[string]string{}
		for _, f := range got.RequestFields {
			if _, duplicate := gotFields[f.Name]; duplicate {
				t.Errorf("%s: duplicate request field %q", want.ID, f.Name)
			}
			gotFields[f.Name] = f.Type
			if f.Required {
				t.Errorf("%s: request field %q is required, want optional", want.ID, f.Name)
			}
			if strings.TrimSpace(f.Notes) == "" {
				t.Errorf("%s: request field %q has empty notes", want.ID, f.Name)
			}
		}
		if want.Fields == nil {
			want.Fields = map[string]string{}
		}
		if !reflect.DeepEqual(gotFields, want.Fields) {
			t.Errorf("%s: request fields = %v, want exactly %v", want.ID, gotFields, want.Fields)
		}
	}
}

// TestContractRecordsRequestSchemasAsDocumented checks relationships between
// the operations' request bodies that only hold if they were read off the
// reference pages: the two action-request operations share one body schema,
// the patch operation uses a different one, and the reads have no body.
func TestContractRecordsRequestSchemasAsDocumented(t *testing.T) {
	rc, raw := loadRawContract(t)
	var document struct {
		Operations []map[string]json.RawMessage `json:"operations"`
	}
	if err := json.Unmarshal(raw, &document); err != nil {
		t.Fatalf("parse raw contract fields: %v", err)
	}

	schema := map[string]string{}
	fields := map[string][]string{}
	for _, op := range rc.Operations {
		schema[op.ID] = op.RequestSchema
		for _, f := range op.RequestFields {
			fields[op.ID] = append(fields[op.ID], f.Name)
		}
	}

	depAction := schema["SubmitDeploymentActionRequest"]
	resAction := schema["SubmitResourceActionRequest"]
	patch := schema["PatchDeployment"]

	if depAction == "" {
		t.Error("SubmitDeploymentActionRequest: request_schema is empty")
	}
	if depAction != resAction {
		t.Errorf("SubmitDeploymentActionRequest and SubmitResourceActionRequest post the same documented body schema, but request_schema is %q vs %q", depAction, resAction)
	}
	if patch == "" {
		t.Error("PatchDeployment: request_schema is empty")
	}
	if patch != "" && patch == depAction {
		t.Errorf("PatchDeployment must not share a request body schema with the action-request operations (both are %q)", patch)
	}

	for _, id := range []string{"GetDeployment", "GetDeploymentActions", "GetDeploymentResources", "GetRequest"} {
		if schema[id] != "" {
			t.Errorf("%s: request_schema = %q, want empty; the operation takes no request body", id, schema[id])
		}
		if len(fields[id]) != 0 {
			t.Errorf("%s: request_fields = %v, want none; the operation takes no request body", id, fields[id])
		}
		for _, rawOp := range document.Operations {
			var rawID string
			if err := json.Unmarshal(rawOp["id"], &rawID); err != nil || rawID != id {
				continue
			}
			if _, present := rawOp["request_fields"]; present {
				t.Errorf("%s: request_fields key is present; omit it entirely for an operation with no body", id)
			}
		}
	}

	// Every documented body field must be optional on these three operations.
	for _, id := range []string{"PatchDeployment", "SubmitDeploymentActionRequest", "SubmitResourceActionRequest"} {
		var found bool
		for _, op := range rc.Operations {
			if op.ID != id {
				continue
			}
			found = true
			if len(op.RequestFields) == 0 {
				t.Errorf("%s: request_fields is empty; record the documented body fields", id)
			}
			for _, f := range op.RequestFields {
				if f.Required {
					t.Errorf("%s: request field %q is recorded as required, but the reference page documents every field of this body as optional", id, f.Name)
				}
			}
		}
		if !found {
			t.Errorf("%s: operation missing from contract", id)
		}
	}
}

// ---------------------------------------------------------------------------
// docs/official_sources.json
// ---------------------------------------------------------------------------

type rawSources struct {
	Sources []struct {
		URL       string `json:"url"`
		Operation string `json:"operation"`
		PageTitle string `json:"page_title"`
		Documents string `json:"documents"`
		FetchedAt string `json:"fetched_at"`
	} `json:"sources"`
}

var dateRE = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)

func TestOfficialSourcesRecordEveryOperation(t *testing.T) {
	rc, _ := loadRawContract(t)

	var rs rawSources
	raw := readJSON(t, sourcesPath, &rs)

	if strings.Contains(string(raw), ".invalid") {
		t.Error("official_sources.json references a .invalid host; every source must be a real page on the vendor portal")
	}
	if len(rs.Sources) == 0 {
		t.Fatal("official_sources.json lists no sources")
	}

	contractOps := map[string]bool{}
	contractURLs := map[string]string{}
	for _, op := range rc.Operations {
		contractOps[op.ID] = true
		contractURLs[op.ID] = op.DocURL
	}

	covered := map[string]bool{}
	seenURLs := map[string]bool{}
	for i, s := range rs.Sources {
		where := "sources[" + s.Operation + "]"
		if s.Operation == "" {
			t.Errorf("sources[%d]: operation is empty; record which operation the page documents", i)
			continue
		}
		if !contractOps[s.Operation] {
			t.Errorf("%s: names an operation that is not in the contract", where)
		}
		if strings.TrimSpace(s.PageTitle) == "" {
			t.Errorf("%s: page_title is empty", where)
		}
		if strings.TrimSpace(s.Documents) == "" {
			t.Errorf("%s: documents is empty", where)
		}
		if seenURLs[s.URL] {
			t.Errorf("%s: duplicate source URL %q", where, s.URL)
		}
		seenURLs[s.URL] = true
		if s.URL == contractURLs[s.Operation] {
			covered[s.Operation] = true
		}

		u, err := url.Parse(s.URL)
		if err != nil {
			t.Errorf("%s: url %q does not parse: %v", where, s.URL, err)
			continue
		}
		if u.Scheme != "https" {
			t.Errorf("%s: url scheme = %q, want https", where, u.Scheme)
		}
		if u.Host != docsPortal {
			t.Errorf("%s: url host = %q, want %s", where, u.Host, docsPortal)
		}
		if !strings.Contains(u.Path, "/xapis/") {
			t.Errorf("%s: url path %q is not an xAPIs reference page", where, u.Path)
		}

		if !dateRE.MatchString(s.FetchedAt) {
			t.Errorf("%s: fetched_at = %q, want a YYYY-MM-DD date", where, s.FetchedAt)
			continue
		}
		if _, err := time.Parse("2006-01-02", s.FetchedAt); err != nil {
			t.Errorf("%s: fetched_at = %q is not a real date", where, s.FetchedAt)
		}
	}

	for _, want := range requiredOps {
		if !covered[want.ID] {
			t.Errorf("no source entry for %q matches its contract doc_url", want.ID)
		}
	}
}

// ---------------------------------------------------------------------------
// The mock is pinned to the contract
// ---------------------------------------------------------------------------

func loadContract(t *testing.T) *vcfauto.Contract {
	t.Helper()
	c, err := vcfauto.LoadContract(contractPath)
	if err != nil {
		t.Fatalf("LoadContract: %v", err)
	}
	return c
}

func TestOperationExpandsDocumentedPathParameters(t *testing.T) {
	c := loadContract(t)
	tests := []struct {
		name    string
		opID    string
		params  map[string]string
		want    string
		wantErr bool
	}{
		{"deployment", "GetDeployment", map[string]string{"deploymentId": "dep-a"}, "/deployment/api/deployments/dep-a", false},
		{"resource action", "SubmitResourceActionRequest", map[string]string{"deploymentId": "dep-a", "resourceId": "res-b"}, "/deployment/api/deployments/dep-a/resources/res-b/requests", false},
		{"request", "GetRequest", map[string]string{"requestId": "req-c"}, "/deployment/api/requests/req-c", false},
		{"missing", "SubmitResourceActionRequest", map[string]string{"deploymentId": "dep-a"}, "", true},
		{"empty", "GetDeployment", map[string]string{"deploymentId": ""}, "", true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			op, ok := c.Operation(tc.opID)
			if !ok {
				t.Fatalf("Operation(%q) is missing", tc.opID)
			}
			got, err := op.ExpandPath(tc.params)
			if tc.wantErr {
				if err == nil {
					t.Fatalf("ExpandPath returned %q without an error", got)
				}
				return
			}
			if err != nil {
				t.Fatalf("ExpandPath: %v", err)
			}
			if got != tc.want {
				t.Errorf("ExpandPath = %q, want %q", got, tc.want)
			}
		})
	}

}

func TestPackageIncludesTableDrivenTests(t *testing.T) {
	paths, err := filepath.Glob("../*_test.go")
	if err != nil {
		t.Fatalf("find package tests: %v", err)
	}
	if len(paths) == 0 {
		t.Fatal("no package test file at the module root; add the requested table-driven package tests")
	}

	var testFunctions, ranges, subtests int
	fs := token.NewFileSet()
	for _, path := range paths {
		file, err := parser.ParseFile(fs, path, nil, 0)
		if err != nil {
			t.Errorf("parse %s: %v", filepath.Base(path), err)
			continue
		}
		ast.Inspect(file, func(n ast.Node) bool {
			switch n := n.(type) {
			case *ast.FuncDecl:
				if strings.HasPrefix(n.Name.Name, "Test") {
					testFunctions++
				}
			case *ast.RangeStmt:
				ranges++
			case *ast.CallExpr:
				if sel, ok := n.Fun.(*ast.SelectorExpr); ok && sel.Sel.Name == "Run" {
					subtests++
				}
			}
			return true
		})
	}
	if testFunctions == 0 || ranges == 0 || subtests == 0 {
		t.Errorf("package tests are not table-driven (Test functions=%d, range loops=%d, Run subtests=%d)", testFunctions, ranges, subtests)
	}
}

func TestClientAndMockUseContractPathTemplates(t *testing.T) {
	raw, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var document map[string]any
	if err := json.Unmarshal(raw, &document); err != nil {
		t.Fatalf("parse contract: %v", err)
	}
	operations, ok := document["operations"].([]any)
	if !ok {
		t.Fatal("contract.operations is not an array")
	}
	for i, value := range operations {
		op, ok := value.(map[string]any)
		if !ok {
			t.Fatalf("contract.operations[%d] is not an object", i)
		}
		pathTemplate, ok := op["path_template"].(string)
		if !ok {
			t.Fatalf("contract.operations[%d].path_template is not a string", i)
		}
		op["path_template"] = "/contract-pinned" + pathTemplate
	}
	mutated, err := json.Marshal(document)
	if err != nil {
		t.Fatalf("marshal changed contract: %v", err)
	}
	path := filepath.Join(t.TempDir(), "contract.json")
	if err := os.WriteFile(path, mutated, 0o600); err != nil {
		t.Fatalf("write changed contract: %v", err)
	}
	c, err := vcfauto.LoadContract(path)
	if err != nil {
		t.Fatalf("LoadContract: %v", err)
	}
	m, err := vcfauto.NewMock(c, vcfauto.MockConfig{DeploymentID: "dep-contract"})
	if err != nil {
		t.Fatalf("NewMock: %v", err)
	}
	t.Cleanup(m.Close)
	cl, err := vcfauto.NewClient(vcfauto.ClientConfig{BaseURL: m.URL(), Token: "token", Contract: c})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	res, err := cl.ApplyPlan(context.Background(), []vcfauto.Step{{Name: "read", OperationID: "GetDeployment", DeploymentID: "dep-contract"}})
	if err != nil {
		t.Fatalf("ApplyPlan: %v", err)
	}
	if len(res.Results) != 1 || res.Results[0].Outcome != vcfauto.StepSucceeded {
		t.Fatalf("changed contract path was not served successfully: %+v", res.Results)
	}
	log := m.Requests()
	if len(log) != 1 || log[0].Path != "/contract-pinned/deployment/api/deployments/dep-contract" {
		t.Fatalf("request path = %+v, want the changed contract path", log)
	}
}

func newFixture(t *testing.T) (*vcfauto.Mock, *vcfauto.Client) {
	t.Helper()
	c := loadContract(t)
	m, err := vcfauto.NewMock(c, vcfauto.MockConfig{
		DeploymentID: "dep-1",
		ResourceID:   "res-1",
		ActionResult: map[string]string{
			"Deployment.ChangeLease":            "SUCCESSFUL",
			"Cloud.vSphere.Machine.Reconfigure": "FAILED",
		},
		ActionDetails: map[string]string{
			"Cloud.vSphere.Machine.Reconfigure": "insufficient capacity in cluster wld-01-cl01",
		},
		PollsBeforeTerminal: 2,
	})
	if err != nil {
		t.Fatalf("NewMock: %v", err)
	}
	t.Cleanup(m.Close)

	cl, err := vcfauto.NewClient(vcfauto.ClientConfig{
		BaseURL:      m.URL(),
		Token:        "test-token",
		Contract:     c,
		PollInterval: time.Millisecond,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return m, cl
}

func TestMockServesOnlyContractOperations(t *testing.T) {
	m, _ := newFixture(t)

	tests := []struct {
		name   string
		method string
		path   string
		want   int
	}{
		{"unknown collection", "GET", "/deployment/api/blueprints", http.StatusNotFound},
		{"operation from another service", "GET", "/catalog/api/items", http.StatusNotFound},
		{"unknown nested path", "GET", "/deployment/api/deployments/dep-1/events", http.StatusNotFound},
		{"root", "GET", "/", http.StatusNotFound},
		{"method not on contract path", "POST", "/deployment/api/deployments/dep-1", http.StatusMethodNotAllowed},
		{"method not on contract path", "DELETE", "/deployment/api/deployments/dep-1", http.StatusMethodNotAllowed},
		{"contract path and method", "GET", "/deployment/api/deployments/dep-1", http.StatusOK},
	}

	for _, tc := range tests {
		t.Run(tc.method+" "+tc.path, func(t *testing.T) {
			req, err := http.NewRequest(tc.method, m.URL()+tc.path, nil)
			if err != nil {
				t.Fatalf("build request: %v", err)
			}
			req.Header.Set("Authorization", "Bearer test-token")
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatalf("do: %v", err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != tc.want {
				t.Errorf("status = %d, want %d", resp.StatusCode, tc.want)
			}
		})
	}

	log := m.Requests()
	if len(log) != len(tests) {
		t.Fatalf("mock recorded %d requests, want %d including every rejected request", len(log), len(tests))
	}
	for i, tc := range tests {
		if log[i].Method != tc.method || log[i].Path != tc.path {
			t.Errorf("log[%d] = %s %s, want %s %s in arrival order", i, log[i].Method, log[i].Path, tc.method, tc.path)
		}
	}
	if got := log[len(log)-1].OperationID; got != "GetDeployment" {
		t.Errorf("served request OperationID = %q, want GetDeployment", got)
	}
	for i := 0; i < len(log)-1; i++ {
		if log[i].OperationID != "" {
			t.Errorf("rejected log[%d] unexpectedly matched operation %q", i, log[i].OperationID)
		}
	}
}

// ---------------------------------------------------------------------------
// Request wire shape
// ---------------------------------------------------------------------------

func bodyKeys(t *testing.T, r vcfauto.RecordedRequest) []string {
	t.Helper()
	var m map[string]json.RawMessage
	if err := json.Unmarshal(r.Body, &m); err != nil {
		t.Fatalf("%s %s: body is not a JSON object (%v): %s", r.Method, r.Path, err, r.Body)
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func findRequest(t *testing.T, log []vcfauto.RecordedRequest, opID string) vcfauto.RecordedRequest {
	t.Helper()
	for _, r := range log {
		if r.OperationID == opID {
			return r
		}
	}
	t.Fatalf("no %s request in the mock request log (%d entries)", opID, len(log))
	return vcfauto.RecordedRequest{}
}

// TestUnsetOptionalFieldsAreOmitted is the core wire-shape assertion: a field
// the caller did not set must be absent from the JSON body, not present with a
// zero value. Sending "name": "" to PatchDeployment renames the deployment to
// the empty string; omitting the key leaves it alone.
func TestUnsetOptionalFieldsAreOmitted(t *testing.T) {
	tests := []struct {
		name     string
		step     vcfauto.Step
		opID     string
		wantKeys []string
		wantBody map[string]any
	}{
		{
			name:     "patch description only",
			step:     vcfauto.Step{Name: "s", OperationID: "PatchDeployment", DeploymentID: "dep-1", Description: "owned by platform-eng"},
			opID:     "PatchDeployment",
			wantKeys: []string{"description"},
			wantBody: map[string]any{"description": "owned by platform-eng"},
		},
		{
			name:     "patch name only",
			step:     vcfauto.Step{Name: "s", OperationID: "PatchDeployment", DeploymentID: "dep-1", NewName: "payments-uat"},
			opID:     "PatchDeployment",
			wantKeys: []string{"name"},
			wantBody: map[string]any{"name": "payments-uat"},
		},
		{
			name:     "patch name and icon",
			step:     vcfauto.Step{Name: "s", OperationID: "PatchDeployment", DeploymentID: "dep-1", NewName: "payments-uat", IconID: "8a1f0c62-3b4d-4e5f-9a70-1c2d3e4f5a6b"},
			opID:     "PatchDeployment",
			wantKeys: []string{"iconId", "name"},
			wantBody: map[string]any{"iconId": "8a1f0c62-3b4d-4e5f-9a70-1c2d3e4f5a6b", "name": "payments-uat"},
		},
		{
			name:     "action with inputs only",
			step:     vcfauto.Step{Name: "s", OperationID: "SubmitDeploymentActionRequest", DeploymentID: "dep-1", ActionID: "Deployment.ChangeLease", Inputs: map[string]any{"leaseDays": 30}},
			opID:     "SubmitDeploymentActionRequest",
			wantKeys: []string{"actionId", "inputs"},
			wantBody: map[string]any{"actionId": "Deployment.ChangeLease", "inputs": map[string]any{"leaseDays": float64(30)}},
		},
		{
			name:     "action with reason only",
			step:     vcfauto.Step{Name: "s", OperationID: "SubmitDeploymentActionRequest", DeploymentID: "dep-1", ActionID: "Deployment.ChangeLease", Reason: "extend for Q3 soak"},
			opID:     "SubmitDeploymentActionRequest",
			wantKeys: []string{"actionId", "reason"},
			wantBody: map[string]any{"actionId": "Deployment.ChangeLease", "reason": "extend for Q3 soak"},
		},
		{
			name:     "action with neither",
			step:     vcfauto.Step{Name: "s", OperationID: "SubmitDeploymentActionRequest", DeploymentID: "dep-1", ActionID: "Deployment.ChangeLease"},
			opID:     "SubmitDeploymentActionRequest",
			wantKeys: []string{"actionId"},
			wantBody: map[string]any{"actionId": "Deployment.ChangeLease"},
		},
		{
			name:     "resource action with reason only",
			step:     vcfauto.Step{Name: "s", OperationID: "SubmitResourceActionRequest", DeploymentID: "dep-1", ResourceID: "res-1", ActionID: "Deployment.ChangeLease", Reason: "scale up"},
			opID:     "SubmitResourceActionRequest",
			wantKeys: []string{"actionId", "reason"},
			wantBody: map[string]any{"actionId": "Deployment.ChangeLease", "reason": "scale up"},
		},
		{
			name:     "empty inputs map is still omitted",
			step:     vcfauto.Step{Name: "s", OperationID: "SubmitDeploymentActionRequest", DeploymentID: "dep-1", ActionID: "Deployment.ChangeLease", Inputs: map[string]any{}},
			opID:     "SubmitDeploymentActionRequest",
			wantKeys: []string{"actionId"},
			wantBody: map[string]any{"actionId": "Deployment.ChangeLease"},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			m, cl := newFixture(t)
			if _, err := cl.ApplyPlan(context.Background(), []vcfauto.Step{tc.step}); err != nil {
				t.Fatalf("ApplyPlan: %v", err)
			}
			recorded := findRequest(t, m.Requests(), tc.opID)
			got := bodyKeys(t, recorded)
			if !reflect.DeepEqual(got, tc.wantKeys) {
				t.Errorf("request body keys = %v, want exactly %v", got, tc.wantKeys)
			}
			var body map[string]any
			if err := json.Unmarshal(recorded.Body, &body); err != nil {
				t.Fatalf("decode body: %v", err)
			}
			if !reflect.DeepEqual(body, tc.wantBody) {
				t.Errorf("request body = %s, want %v", recorded.Body, tc.wantBody)
			}
		})
	}
}

func TestRequestHeadersAndPaths(t *testing.T) {
	m, cl := newFixture(t)

	steps := []vcfauto.Step{
		{Name: "read", OperationID: "GetDeployment", DeploymentID: "dep-1"},
		{Name: "patch", OperationID: "PatchDeployment", DeploymentID: "dep-1", Description: "d"},
		{Name: "act", OperationID: "SubmitResourceActionRequest", DeploymentID: "dep-1", ResourceID: "res-1", ActionID: "Deployment.ChangeLease"},
	}
	if _, err := cl.ApplyPlan(context.Background(), steps); err != nil {
		t.Fatalf("ApplyPlan: %v", err)
	}

	log := m.Requests()
	if len(log) == 0 {
		t.Fatal("mock recorded no requests")
	}
	for _, r := range log {
		if got := r.Header.Get("Authorization"); got != "Bearer test-token" {
			t.Errorf("%s %s: Authorization = %q, want %q", r.Method, r.Path, got, "Bearer test-token")
		}
		ct := r.Header.Get("Content-Type")
		if len(r.Body) > 0 {
			if ct != "application/json" {
				t.Errorf("%s %s: Content-Type = %q, want application/json", r.Method, r.Path, ct)
			}
		} else if ct != "" {
			t.Errorf("%s %s: bodiless request sent Content-Type %q", r.Method, r.Path, ct)
		}
	}

	if got := findRequest(t, log, "PatchDeployment"); got.Path != "/deployment/api/deployments/dep-1" {
		t.Errorf("PatchDeployment path = %q", got.Path)
	}
	if got := findRequest(t, log, "SubmitResourceActionRequest"); got.Path != "/deployment/api/deployments/dep-1/resources/res-1/requests" {
		t.Errorf("SubmitResourceActionRequest path = %q", got.Path)
	}
	if got := findRequest(t, log, "GetDeployment"); len(got.Body) != 0 {
		t.Errorf("GetDeployment sent a body: %s", got.Body)
	}
}

// ---------------------------------------------------------------------------
// Partial failure reporting
// ---------------------------------------------------------------------------

// TestPlanReportsEarlierStepsAccurately runs a four-step change in which the
// third step's request is accepted with 200 and then settles as FAILED. The
// two changes that already took effect must be reported as applied, the third
// as failed, the fourth as skipped, and nothing may be claimed as rolled back.
func TestPlanReportsEarlierStepsAccurately(t *testing.T) {
	m, cl := newFixture(t)

	steps := []vcfauto.Step{
		{Name: "retag", OperationID: "PatchDeployment", DeploymentID: "dep-1", Description: "owned by platform-eng"},
		{Name: "extend-lease", OperationID: "SubmitDeploymentActionRequest", DeploymentID: "dep-1", ActionID: "Deployment.ChangeLease", Inputs: map[string]any{"leaseDays": 30}},
		{Name: "resize-vm", OperationID: "SubmitResourceActionRequest", DeploymentID: "dep-1", ResourceID: "res-1", ActionID: "Cloud.vSphere.Machine.Reconfigure", Reason: "scale up for Q3"},
		{Name: "confirm", OperationID: "GetDeployment", DeploymentID: "dep-1"},
	}

	res, err := cl.ApplyPlan(context.Background(), steps)
	if err != nil {
		t.Fatalf("ApplyPlan returned an error for a step-level failure; the failure belongs in the result: %v", err)
	}
	if res == nil {
		t.Fatal("ApplyPlan returned a nil result")
	}
	if len(res.Results) != len(steps) {
		t.Fatalf("len(Results) = %d, want %d: every step must be accounted for", len(res.Results), len(steps))
	}

	wantOutcomes := []vcfauto.StepOutcome{
		vcfauto.StepSucceeded,
		vcfauto.StepSucceeded,
		vcfauto.StepFailed,
		vcfauto.StepSkipped,
	}
	for i, want := range wantOutcomes {
		if got := res.Results[i].Outcome; got != want {
			t.Errorf("Results[%d] (%s): outcome = %q, want %q", i, steps[i].Name, got, want)
		}
	}

	failed := res.Results[2]
	if failed.Status != "FAILED" {
		t.Errorf("failed step: Status = %q, want the terminal request status FAILED", failed.Status)
	}
	if !strings.Contains(failed.Detail, "insufficient capacity") {
		t.Errorf("failed step: Detail = %q, want it to carry the reason the request reported", failed.Detail)
	}
	if failed.RequestID == "" {
		t.Error("failed step: RequestID is empty; the request was accepted and has an id")
	}

	if got, want := res.Applied(), []string{"retag", "extend-lease"}; !reflect.DeepEqual(got, want) {
		t.Errorf("Applied() = %v, want %v", got, want)
	}
	if res.RolledBack {
		t.Error("RolledBack = true, but no compensating request was sent; the two applied changes are still in effect")
	}

	summary := res.Summary()
	for _, want := range []string{
		"1. retag [PatchDeployment] SUCCEEDED",
		"2. extend-lease [SubmitDeploymentActionRequest] SUCCEEDED",
		"3. resize-vm [SubmitResourceActionRequest] FAILED",
		"4. confirm [GetDeployment] SKIPPED",
		"applied: retag, extend-lease",
		"rolled back: false",
	} {
		if !strings.Contains(summary, want) {
			t.Errorf("Summary() is missing %q:\n%s", want, summary)
		}
	}

	// The plan must stop at the failure: no request for the skipped step.
	log := m.Requests()
	for _, r := range log {
		if r.OperationID == "GetDeployment" {
			t.Errorf("a GetDeployment request was sent for the skipped step %q", steps[3].Name)
		}
	}

	// The failure must be observed by polling the request to a terminal state,
	// not inferred from the POST status alone.
	var polls int
	for _, r := range log {
		if r.OperationID == "GetRequest" {
			polls++
		}
	}
	if polls == 0 {
		t.Error("no GetRequest polls were recorded; an accepted action request settles asynchronously and must be followed to a terminal status")
	}
}

func TestPlanSucceedsWhenNoStepFails(t *testing.T) {
	_, cl := newFixture(t)

	steps := []vcfauto.Step{
		{Name: "retag", OperationID: "PatchDeployment", DeploymentID: "dep-1", Description: "d"},
		{Name: "extend-lease", OperationID: "SubmitDeploymentActionRequest", DeploymentID: "dep-1", ActionID: "Deployment.ChangeLease"},
		{Name: "confirm", OperationID: "GetDeployment", DeploymentID: "dep-1"},
	}
	res, err := cl.ApplyPlan(context.Background(), steps)
	if err != nil {
		t.Fatalf("ApplyPlan: %v", err)
	}
	for i, r := range res.Results {
		if r.Outcome != vcfauto.StepSucceeded {
			t.Errorf("Results[%d] (%s): outcome = %q, want SUCCEEDED", i, steps[i].Name, r.Outcome)
		}
	}
	if got, want := res.Applied(), []string{"retag", "extend-lease", "confirm"}; !reflect.DeepEqual(got, want) {
		t.Errorf("Applied() = %v, want %v", got, want)
	}
	if res.RolledBack {
		t.Error("RolledBack = true on a clean run")
	}
}

func TestApplyPlanRejectsOperationsOutsideTheContract(t *testing.T) {
	m, cl := newFixture(t)

	_, err := cl.ApplyPlan(context.Background(), []vcfauto.Step{
		{Name: "would-be-valid", OperationID: "GetDeployment", DeploymentID: "dep-1"},
		{Name: "nope", OperationID: "DeleteDeployment", DeploymentID: "dep-1"},
	})
	if err == nil {
		t.Fatal("ApplyPlan accepted an operation the contract does not name; it must refuse before sending anything")
	}
	if !strings.Contains(err.Error(), "DeleteDeployment") {
		t.Errorf("error should name the unknown operation: %v", err)
	}
	if got := len(m.Requests()); got != 0 {
		t.Errorf("invalid plan sent %d request(s); all steps must be validated before any I/O", got)
	}
}
