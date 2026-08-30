package verify

import (
	"encoding/json"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"

	"vcfops.local/opssync/mock"
)

const (
	contractPath = "../docs/contract.json"
	sourcesPath  = "../docs/official_sources.json"

	specRepository = "https://github.com/vmware/vcf-api-specs"
	specPath       = "specifications/vcf-operations/vcf-operations-openapi.json"
	specCommit     = "c3f3b52c845dd967cabbc21680e893292077d5ba"
	specTitle      = "VMware Cloud Foundation Operations API"
	specVersion    = "9.1.0.0"
	specBasePath   = "/suite-api"
)

// contractedOperationIDs are the operationIds this integration is built on.
var contractedOperationIDs = []string{"acquireToken", "addResourcesProperties", "getResources"}

// specQueryParams is the complete set of query parameter names the
// specification declares for getResources. A contract may name a subset.
var specQueryParams = []string{
	"adapterInstanceId", "adapterKind", "collectorId", "collectorName", "credentialId",
	"includeRelated", "maintenanceScheduleId", "name", "page", "pageSize", "parentId",
	"propertyName", "propertyValue", "recentlyAdded", "regex", "resourceHealth", "resourceId",
	"resourceKind", "resourceState", "resourceStatus", "statKey", "statKeyInclusive",
	"statKeyLowerBound", "statKeyUpperBound",
}

// requiredQueryParams are the getResources parameters the client must be able
// to send, so the contract has to name at least these.
var requiredQueryParams = []string{"adapterKind", "name", "page", "pageSize", "resourceKind"}

func loadContract(t *testing.T) *mock.Contract {
	t.Helper()
	c, err := mock.LoadContract(contractPath)
	if err != nil {
		t.Fatalf("load %s: %v", contractPath, err)
	}
	return c
}

func TestContractMatchesSpecification(t *testing.T) {
	c := loadContract(t)

	t.Run("provenance", func(t *testing.T) {
		if c.Spec.Repository != specRepository {
			t.Errorf("spec.repository = %q, want %q", c.Spec.Repository, specRepository)
		}
		if c.Spec.Path != specPath {
			t.Errorf("spec.path = %q, want %q", c.Spec.Path, specPath)
		}
		if c.Spec.Commit != specCommit {
			t.Errorf("spec.commit = %q, want the revision that published the VCF 9.1 specifications (%q)", c.Spec.Commit, specCommit)
		}
		if c.Spec.Title != specTitle {
			t.Errorf("spec.title = %q, want %q", c.Spec.Title, specTitle)
		}
		if c.Spec.Version != specVersion {
			t.Errorf("spec.version = %q, want %q", c.Spec.Version, specVersion)
		}
		if c.BasePath != specBasePath {
			t.Errorf("basePath = %q, want %q", c.BasePath, specBasePath)
		}
	})

	t.Run("security_scheme", func(t *testing.T) {
		want := mock.SecurityScheme{Name: "Authorization", In: "header", Type: "apiKey"}
		if c.SecurityScheme != want {
			t.Errorf("securityScheme = %+v, want %+v", c.SecurityScheme, want)
		}
	})

	t.Run("operation_ids", func(t *testing.T) {
		got := append([]string(nil), c.OperationIDs()...)
		sort.Strings(got)
		if !reflect.DeepEqual(got, contractedOperationIDs) {
			t.Fatalf("operationIds = %v, want exactly %v", got, contractedOperationIDs)
		}
	})

	routes := []struct {
		operationID    string
		method         string
		path           string
		authenticated  bool
		successStatus  int
		requestSchema  string
		responseSchema string
	}{
		{"acquireToken", "POST", "/api/auth/token/acquire", false, 200, "username-password", "auth-token"},
		{"getResources", "GET", "/api/resources", true, 200, "", "resources"},
		{"addResourcesProperties", "POST", "/api/resources/properties", true, 200, "resources-property-contents", ""},
	}
	for _, want := range routes {
		t.Run("operation_"+want.operationID, func(t *testing.T) {
			op, ok := c.Operation(want.operationID)
			if !ok {
				t.Fatalf("contract declares no operation %q", want.operationID)
			}
			if op.Method != want.method || op.Path != want.path {
				t.Errorf("route = %s %s, want %s %s", op.Method, op.Path, want.method, want.path)
			}
			if op.Authenticated != want.authenticated {
				t.Errorf("authenticated = %v, want %v", op.Authenticated, want.authenticated)
			}
			if op.SuccessStatus != want.successStatus {
				t.Errorf("successStatus = %d, want %d", op.SuccessStatus, want.successStatus)
			}
			if op.RequestSchema != want.requestSchema {
				t.Errorf("requestSchema = %q, want %q", op.RequestSchema, want.requestSchema)
			}
			if op.ResponseSchema != want.responseSchema {
				t.Errorf("responseSchema = %q, want %q", op.ResponseSchema, want.responseSchema)
			}
		})
	}

	t.Run("query_parameters", func(t *testing.T) {
		for _, id := range []string{"acquireToken", "addResourcesProperties"} {
			op, _ := c.Operation(id)
			if len(op.QueryParams) != 0 {
				t.Errorf("operation %s declares queryParams %v, want none", id, op.QueryParams)
			}
		}

		op, _ := c.Operation("getResources")
		known := map[string]bool{}
		for _, name := range specQueryParams {
			known[name] = true
		}
		declared := map[string]bool{}
		for _, name := range op.QueryParams {
			if !known[name] {
				t.Errorf("getResources declares query parameter %q, which the specification does not define", name)
			}
			declared[name] = true
		}
		for _, name := range requiredQueryParams {
			if !declared[name] {
				t.Errorf("getResources does not declare query parameter %q", name)
			}
		}
	})

	t.Run("schemas", func(t *testing.T) {
		want := map[string]mock.Schema{
			"username-password":           {Required: []string{"password", "username"}, Optional: []string{"authSource"}},
			"auth-token":                  {Required: []string{"token", "validity"}, Optional: []string{"expiresAt", "roles"}},
			"resources":                   {Required: []string{}, Optional: []string{"links", "pageInfo", "resourceList"}},
			"resources-property-contents": {Required: []string{}, Optional: []string{"values"}},
			"resource-property-contents":  {Required: []string{"property-contents", "resourceId"}, Optional: []string{}},
			"property-contents":           {Required: []string{"property-content"}, Optional: []string{}},
			"property-content":            {Required: []string{"statKey", "timestamps"}, Optional: []string{"data", "values"}},
		}

		var gotNames, wantNames []string
		for name := range c.Schemas {
			gotNames = append(gotNames, name)
		}
		for name := range want {
			wantNames = append(wantNames, name)
		}
		sort.Strings(gotNames)
		sort.Strings(wantNames)
		if !reflect.DeepEqual(gotNames, wantNames) {
			t.Fatalf("schemas = %v, want exactly %v", gotNames, wantNames)
		}

		for name, wantSchema := range want {
			got := c.Schemas[name]
			gotRequired := sortedCopy(got.Required)
			gotOptional := sortedCopy(got.Optional)
			if !reflect.DeepEqual(gotRequired, sortedCopy(wantSchema.Required)) {
				t.Errorf("schema %s required = %v, want %v", name, gotRequired, wantSchema.Required)
			}
			if !reflect.DeepEqual(gotOptional, sortedCopy(wantSchema.Optional)) {
				t.Errorf("schema %s optional = %v, want %v", name, gotOptional, wantSchema.Optional)
			}
		}
	})
}

type officialSources struct {
	Sources []struct {
		Repository   string   `json:"repository"`
		License      string   `json:"license"`
		Path         string   `json:"path"`
		Commit       string   `json:"commit"`
		URL          string   `json:"url"`
		SpecVersion  string   `json:"specVersion"`
		OperationIDs []string `json:"operationIds"`
	} `json:"sources"`
}

func TestOfficialSourcesRecordTheSpecification(t *testing.T) {
	raw, err := os.ReadFile(sourcesPath)
	if err != nil {
		t.Fatalf("read %s: %v", filepath.Clean(sourcesPath), err)
	}
	var doc officialSources
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parse %s: %v", sourcesPath, err)
	}
	if len(doc.Sources) != 1 {
		t.Fatalf("sources has %d entries, want exactly 1 (the OpenAPI document)", len(doc.Sources))
	}
	got := doc.Sources[0]

	if got.Repository != specRepository {
		t.Errorf("repository = %q, want %q", got.Repository, specRepository)
	}
	if got.License != "Apache-2.0" {
		t.Errorf("license = %q, want %q", got.License, "Apache-2.0")
	}
	if got.Path != specPath {
		t.Errorf("path = %q, want %q", got.Path, specPath)
	}
	if got.Commit != specCommit {
		t.Errorf("commit = %q, want %q", got.Commit, specCommit)
	}
	if got.SpecVersion != specVersion {
		t.Errorf("specVersion = %q, want %q", got.SpecVersion, specVersion)
	}
	if !isPinnedSpecURL(got.URL) {
		t.Errorf("url = %q, want a canonical GitHub page or raw-file URL pinned to commit %s and path %s", got.URL, specCommit, specPath)
	}
	ids := sortedCopy(got.OperationIDs)
	if !reflect.DeepEqual(ids, contractedOperationIDs) {
		t.Errorf("operationIds = %v, want exactly %v", ids, contractedOperationIDs)
	}

	c := loadContract(t)
	if c.Spec.Commit != got.Commit {
		t.Errorf("contract.json commit %q and official_sources.json commit %q disagree", c.Spec.Commit, got.Commit)
	}
	if c.Spec.Path != got.Path {
		t.Errorf("contract.json path %q and official_sources.json path %q disagree", c.Spec.Path, got.Path)
	}
}

func isPinnedSpecURL(raw string) bool {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme != "https" || parsed.RawQuery != "" || parsed.Fragment != "" {
		return false
	}
	if parsed.Host != "github.com" && parsed.Host != "raw.githubusercontent.com" {
		return false
	}
	return strings.HasPrefix(parsed.Path, "/vmware/vcf-api-specs/") &&
		strings.Contains(parsed.Path, "/"+specCommit+"/") &&
		strings.HasSuffix(parsed.Path, "/"+specPath)
}

func sortedCopy(in []string) []string {
	out := append([]string{}, in...)
	sort.Strings(out)
	return out
}
