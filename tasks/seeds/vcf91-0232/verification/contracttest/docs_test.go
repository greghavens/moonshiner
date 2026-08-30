package contracttest

import (
	"encoding/json"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"
)

// The specification the contract must be derived from.
const (
	specRepository = "vmware/vcf-api-specs"
	specFilePath   = "specifications/sddc-lcm/sddc-lcm-openapi.yaml"
	specLicense    = "Apache-2.0"
	specCommit     = "c3f3b52c845dd967cabbc21680e893292077d5ba"
	specSHA256     = "158cab89bc56e1bb80b662a859499efc6ee57c1d35503d4d1f855809c213436c"
)

// contractOperations are the operations this tool calls, as the specification
// names them.
var contractOperations = []string{
	"createComponents",
	"getComponents",
	"getTask",
	"resolveDepotComponents",
	"retryTask",
}

// contractSchemas are the request schemas a body may be built against.
var contractSchemas = []string{
	"ComponentImportSpec",
	"ComponentRepository",
	"ComponentSpecs",
	"ComponentVersionSpec",
	"DepotComponentsSpec",
	"FleetDepotSpec",
}

var (
	shaRe  = regexp.MustCompile(`^[0-9a-f]{40}$`)
	dateRe = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)
)

type operationWant struct {
	method          string
	path            string
	successStatus   float64
	requestSchema   string // empty means the JSON value must be null
	responseSchema  string
	pathParams      []string
	fixedQuery      map[string]string
	queryRequired   []string
	queryOptional   []string
	optionalHeaders []string
	discriminator   string
	variants        []string
}

type fieldsWant struct {
	required []string
	optional []string
}

func readJSONFile(t *testing.T, path string) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
	return out
}

// keysOf returns an object's keys, sorted.
func keysOf(t *testing.T, where string, v any) []string {
	t.Helper()
	obj, ok := v.(map[string]any)
	if !ok {
		t.Fatalf("%s is not a JSON object", where)
	}
	out := make([]string, 0, len(obj))
	for k := range obj {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func checkKeys(t *testing.T, where string, v any, want ...string) map[string]any {
	t.Helper()
	got := keysOf(t, where, v)
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Errorf("%s has keys %v, want exactly %v", where, got, want)
	}
	obj, _ := v.(map[string]any)
	return obj
}

func str(t *testing.T, where string, v any) string {
	t.Helper()
	s, ok := v.(string)
	if !ok {
		t.Fatalf("%s is %v, want a string", where, v)
	}
	return s
}

// strList reads a JSON array of strings and checks it is sorted.
func strList(t *testing.T, where string, v any) []string {
	t.Helper()
	items, ok := v.([]any)
	if !ok {
		t.Fatalf("%s is %v, want an array", where, v)
	}
	out := make([]string, 0, len(items))
	for i, item := range items {
		s, ok := item.(string)
		if !ok {
			t.Fatalf("%s[%d] is %v, want a string", where, i, item)
		}
		out = append(out, s)
	}
	if !sort.StringsAreSorted(out) {
		t.Errorf("%s is %v, want it sorted", where, out)
	}
	return out
}

func equalSet(a, b []string) bool {
	x := append([]string(nil), a...)
	y := append([]string(nil), b...)
	sort.Strings(x)
	sort.Strings(y)
	return reflect.DeepEqual(x, y)
}

// TestContractDocument checks docs/contract.json is a well formed contract for
// the operations this tool calls.
//
// What each operation and schema actually says is checked where it matters: the
// mock builds its routes from this file, and the wire tests pin the target,
// method, status and body of every request the run makes. A contract that
// disagrees with the specification therefore fails there.
func TestContractDocument(t *testing.T) {
	t.Parallel()
	doc := readJSONFile(t, contractPath)
	checkKeys(t, "contract.json", doc, "source", "server", "security", "operations", "schemas")

	source := checkKeys(t, "source", doc["source"],
		"repository", "specPath", "commit", "license", "openapi", "apiTitle", "apiVersion")
	if got := str(t, "source.repository", source["repository"]); got != specRepository {
		t.Errorf("source.repository is %q, want %q", got, specRepository)
	}
	if got := str(t, "source.specPath", source["specPath"]); got != specFilePath {
		t.Errorf("source.specPath is %q, want %q", got, specFilePath)
	}
	if got := str(t, "source.license", source["license"]); got != specLicense {
		t.Errorf("source.license is %q, want %q", got, specLicense)
	}
	if got := str(t, "source.commit", source["commit"]); got != specCommit {
		t.Errorf("source.commit is %q, want the commit the contract was derived at, %q", got, specCommit)
	}
	// These come from the specification document itself.
	if got := str(t, "source.openapi", source["openapi"]); got != "3.0.4" {
		t.Errorf("source.openapi is %q, want the spec's openapi version", got)
	}
	if got := str(t, "source.apiTitle", source["apiTitle"]); got != "VCF SDDC LCM Service APIs" {
		t.Errorf("source.apiTitle is %q, want the spec's info.title", got)
	}
	if got := str(t, "source.apiVersion", source["apiVersion"]); got != "9.1.0.0" {
		t.Errorf("source.apiVersion is %q, want the spec's info.version", got)
	}
	if got := str(t, "server", doc["server"]); got != "https://vcf.broadcom.com/sddc-lcm" {
		t.Errorf("server is %q, want the URL the spec publishes", got)
	}

	security := checkKeys(t, "security", doc["security"], "scheme", "type", "httpScheme", "bearerFormat")
	for field, want := range map[string]string{
		"scheme": "bearerToken", "type": "http", "httpScheme": "Bearer", "bearerFormat": "JWT",
	} {
		if got := str(t, "security."+field, security[field]); got != want {
			t.Errorf("security.%s is %q, want %q spelled as the spec spells it", field, got, want)
		}
	}

	operations, ok := doc["operations"].(map[string]any)
	if !ok {
		t.Fatalf("operations is not a JSON object")
	}
	if got := keysOf(t, "operations", doc["operations"]); !equalSet(got, contractOperations) {
		t.Fatalf("operations names %v, want exactly %v", got, contractOperations)
	}
	schemaNames := keysOf(t, "schemas", doc["schemas"])
	if !equalSet(schemaNames, contractSchemas) {
		t.Errorf("schemas names %v, want exactly %v", schemaNames, contractSchemas)
	}

	// Exactly one operation may carry a discriminated request body, and only
	// that one may declare requestVariants.
	discriminated := 0
	for _, id := range contractOperations {
		where := "operations." + id
		keys := keysOf(t, where, operations[id])
		base := []string{"method", "path", "successStatus", "requestSchema", "responseSchema",
			"pathParams", "fixedQuery", "queryParams", "optionalHeaders"}
		want := append([]string(nil), base...)
		if containsStr(keys, "requestVariants") {
			discriminated++
			want = append(want, "requestVariants")
		}
		op := checkKeys(t, where, operations[id], want...)

		method := str(t, where+".method", op["method"])
		if method != strings.ToUpper(method) {
			t.Errorf("%s.method is %q, want it upper case", where, method)
		}
		path := str(t, where+".path", op["path"])
		if !strings.HasPrefix(path, "/") {
			t.Errorf("%s.path is %q, want a path template", where, path)
		}
		if strings.Contains(path, "?") {
			t.Errorf("%s.path is %q; the query the spec pins belongs in fixedQuery, not in the path", where, path)
		}
		status, ok := op["successStatus"].(float64)
		if !ok || status < 200 || status > 299 {
			t.Errorf("%s.successStatus is %v, want the 2xx status the operation documents", where, op["successStatus"])
		}

		// pathParams must be exactly the parameters the path template carries.
		declared := strList(t, where+".pathParams", op["pathParams"])
		if !equalSet(declared, templateParams(path)) {
			t.Errorf("%s.pathParams is %v but path %q carries %v", where, declared, path, templateParams(path))
		}

		if op["requestSchema"] != nil {
			name := str(t, where+".requestSchema", op["requestSchema"])
			if !containsStr(schemaNames, name) {
				t.Errorf("%s.requestSchema is %q, which schemas does not define", where, name)
			}
		}
		if str(t, where+".responseSchema", op["responseSchema"]) == "" {
			t.Errorf("%s.responseSchema is empty", where)
		}

		query := checkKeys(t, where+".queryParams", op["queryParams"], "required", "optional")
		required := strList(t, where+".queryParams.required", query["required"])
		optional := strList(t, where+".queryParams.optional", query["optional"])
		for _, name := range required {
			if containsStr(optional, name) {
				t.Errorf("%s.queryParams lists %q as both required and optional", where, name)
			}
		}
		strList(t, where+".optionalHeaders", op["optionalHeaders"])

		fixed, ok := op["fixedQuery"].(map[string]any)
		if !ok {
			t.Fatalf("%s.fixedQuery is not a JSON object", where)
		}
		for k, v := range fixed {
			if str(t, where+".fixedQuery."+k, v) == "" {
				t.Errorf("%s.fixedQuery[%q] is empty", where, k)
			}
			if containsStr(required, k) || containsStr(optional, k) {
				t.Errorf("%s pins %q in fixedQuery and also lists it as a parameter", where, k)
			}
		}

		if variants, ok := op["requestVariants"]; ok {
			v := checkKeys(t, where+".requestVariants", variants, "discriminator", "variants")
			if str(t, where+".requestVariants.discriminator", v["discriminator"]) == "" {
				t.Errorf("%s.requestVariants.discriminator is empty", where)
			}
			names := strList(t, where+".requestVariants.variants", v["variants"])
			if len(names) < 2 {
				t.Errorf("%s.requestVariants.variants is %v, want the schemas the discriminator selects between", where, names)
			}
			// The variant this tool builds must be one the contract describes.
			if !containsStr(names, "ComponentImportSpec") {
				t.Errorf("%s.requestVariants.variants %v does not include ComponentImportSpec", where, names)
			}
		}
	}
	if discriminated != 1 {
		t.Errorf("%d operations declare requestVariants, want exactly the one whose body the spec defines as a discriminated oneOf", discriminated)
	}

	schemas, _ := doc["schemas"].(map[string]any)
	for _, name := range schemaNames {
		where := "schemas." + name
		s := checkKeys(t, where, schemas[name], "required", "optional")
		required := strList(t, where+".required", s["required"])
		optional := strList(t, where+".optional", s["optional"])
		for _, f := range required {
			if containsStr(optional, f) {
				t.Errorf("%s lists %q as both required and optional", where, f)
			}
		}
		if len(required)+len(optional) == 0 {
			t.Errorf("%s declares no fields at all", where)
		}
	}

	// Pin the complete derived subset. Structural checks alone can be satisfied
	// by a plausible-looking contract that does not say what the pinned
	// specification says, especially for fields the example plan leaves unset.
	wantOperations := map[string]operationWant{
		"createComponents": {
			method: "POST", path: "/v1/components", successStatus: 202,
			requestSchema: "ComponentSpecs", responseSchema: "Task",
			pathParams: []string{}, fixedQuery: map[string]string{},
			queryRequired: []string{}, queryOptional: []string{},
			optionalHeaders: []string{"X-Correlation-Id"},
			discriminator:   "deploymentType",
			variants:        []string{"ComponentImportSpec", "OvaComponentSpec", "VspClusterSpec", "VspComponentSpec"},
		},
		"getComponents": {
			method: "GET", path: "/v1/components", successStatus: 200,
			responseSchema: "Components", pathParams: []string{}, fixedQuery: map[string]string{},
			queryRequired: []string{}, queryOptional: []string{"scope"}, optionalHeaders: []string{},
		},
		"getTask": {
			method: "GET", path: "/v1/tasks/{taskId}", successStatus: 200,
			responseSchema: "Task", pathParams: []string{"taskId"}, fixedQuery: map[string]string{},
			queryRequired: []string{}, queryOptional: []string{}, optionalHeaders: []string{},
		},
		"resolveDepotComponents": {
			method: "POST", path: "/v1/depot/components", successStatus: 200,
			requestSchema: "DepotComponentsSpec", responseSchema: "ResolvedComponentVersions",
			pathParams: []string{}, fixedQuery: map[string]string{},
			queryRequired: []string{}, queryOptional: []string{}, optionalHeaders: []string{},
		},
		"retryTask": {
			method: "POST", path: "/v1/tasks/{taskId}", successStatus: 200,
			responseSchema: "Task", pathParams: []string{"taskId"},
			fixedQuery:    map[string]string{"action": "retry"},
			queryRequired: []string{}, queryOptional: []string{}, optionalHeaders: []string{},
		},
	}
	for id, want := range wantOperations {
		where := "operations." + id
		op := operations[id].(map[string]any)
		if got := str(t, where+".method", op["method"]); got != want.method {
			t.Errorf("%s.method is %q, want %q from the specification", where, got, want.method)
		}
		if got := str(t, where+".path", op["path"]); got != want.path {
			t.Errorf("%s.path is %q, want %q from the specification", where, got, want.path)
		}
		if got := op["successStatus"]; got != want.successStatus {
			t.Errorf("%s.successStatus is %v, want %.0f from the specification", where, got, want.successStatus)
		}
		if want.requestSchema == "" {
			if op["requestSchema"] != nil {
				t.Errorf("%s.requestSchema is %v, want null", where, op["requestSchema"])
			}
		} else if got := str(t, where+".requestSchema", op["requestSchema"]); got != want.requestSchema {
			t.Errorf("%s.requestSchema is %q, want %q", where, got, want.requestSchema)
		}
		if got := str(t, where+".responseSchema", op["responseSchema"]); got != want.responseSchema {
			t.Errorf("%s.responseSchema is %q, want %q", where, got, want.responseSchema)
		}
		if got := strList(t, where+".pathParams", op["pathParams"]); !reflect.DeepEqual(got, want.pathParams) {
			t.Errorf("%s.pathParams is %v, want %v", where, got, want.pathParams)
		}
		gotFixed := map[string]string{}
		for name, value := range op["fixedQuery"].(map[string]any) {
			gotFixed[name] = str(t, where+".fixedQuery."+name, value)
		}
		if !reflect.DeepEqual(gotFixed, want.fixedQuery) {
			t.Errorf("%s.fixedQuery is %v, want %v", where, gotFixed, want.fixedQuery)
		}
		query := op["queryParams"].(map[string]any)
		if got := strList(t, where+".queryParams.required", query["required"]); !reflect.DeepEqual(got, want.queryRequired) {
			t.Errorf("%s.queryParams.required is %v, want %v", where, got, want.queryRequired)
		}
		if got := strList(t, where+".queryParams.optional", query["optional"]); !reflect.DeepEqual(got, want.queryOptional) {
			t.Errorf("%s.queryParams.optional is %v, want %v", where, got, want.queryOptional)
		}
		if got := strList(t, where+".optionalHeaders", op["optionalHeaders"]); !reflect.DeepEqual(got, want.optionalHeaders) {
			t.Errorf("%s.optionalHeaders is %v, want %v", where, got, want.optionalHeaders)
		}
		if want.discriminator != "" {
			variants := op["requestVariants"].(map[string]any)
			if got := str(t, where+".requestVariants.discriminator", variants["discriminator"]); got != want.discriminator {
				t.Errorf("%s.requestVariants.discriminator is %q, want %q", where, got, want.discriminator)
			}
			if got := strList(t, where+".requestVariants.variants", variants["variants"]); !reflect.DeepEqual(got, want.variants) {
				t.Errorf("%s.requestVariants.variants is %v, want %v", where, got, want.variants)
			}
		}
	}

	wantSchemas := map[string]fieldsWant{
		"ComponentImportSpec": {
			required: []string{"componentType", "deploymentType", "fqdn", "password"},
			optional: []string{"certificate", "repository", "size", "sslThumbprint", "username", "version", "vmId"},
		},
		"ComponentRepository": {required: []string{}, optional: []string{"certificate", "downloadUrl"}},
		"ComponentSpecs":      {required: []string{}, optional: []string{"componentSpecs"}},
		"ComponentVersionSpec": {
			required: []string{"component"}, optional: []string{"version"},
		},
		"DepotComponentsSpec": {
			required: []string{"componentVersions", "fleetDepotSpec"}, optional: []string{"version"},
		},
		"FleetDepotSpec": {required: []string{"certificate", "fqdn"}, optional: []string{}},
	}
	for name, want := range wantSchemas {
		where := "schemas." + name
		schema := schemas[name].(map[string]any)
		if got := strList(t, where+".required", schema["required"]); !reflect.DeepEqual(got, want.required) {
			t.Errorf("%s.required is %v, want %v from the specification", where, got, want.required)
		}
		if got := strList(t, where+".optional", schema["optional"]); !reflect.DeepEqual(got, want.optional) {
			t.Errorf("%s.optional is %v, want %v from the specification", where, got, want.optional)
		}
	}
}

// TestOfficialSources checks the provenance record points at the specification
// the contract was derived from, pinned to an immutable commit.
func TestOfficialSources(t *testing.T) {
	t.Parallel()
	doc := readJSONFile(t, sourcesPath)
	checkKeys(t, "official_sources.json", doc, "sources")
	list, ok := doc["sources"].([]any)
	if !ok || len(list) != 1 {
		t.Fatalf("sources has %v entries, want exactly one", doc["sources"])
	}
	src := checkKeys(t, "sources[0]", list[0],
		"title", "repository", "path", "commit", "url", "rawUrl", "license",
		"retrieved", "sha256", "operationIds")

	if got := str(t, "sources[0].title", src["title"]); strings.TrimSpace(got) == "" {
		t.Errorf("sources[0].title is empty")
	}
	if got := str(t, "sources[0].repository", src["repository"]); got != "https://github.com/"+specRepository {
		t.Errorf("sources[0].repository is %q, want the repository URL", got)
	}
	if got := str(t, "sources[0].path", src["path"]); got != specFilePath {
		t.Errorf("sources[0].path is %q, want %q", got, specFilePath)
	}
	if got := str(t, "sources[0].license", src["license"]); got != specLicense {
		t.Errorf("sources[0].license is %q, want %q", got, specLicense)
	}

	commit := str(t, "sources[0].commit", src["commit"])
	if !shaRe.MatchString(commit) {
		t.Errorf("sources[0].commit is %q, want a full 40 character commit sha", commit)
	}
	if commit != specCommit {
		t.Errorf("sources[0].commit is %q, want %q", commit, specCommit)
	}
	// The digest can only be known by fetching the file itself. Pin the value,
	// not merely its shape, so a made-up digest cannot pass as provenance.
	if got := str(t, "sources[0].sha256", src["sha256"]); got != specSHA256 {
		t.Errorf("sources[0].sha256 is %q, want the specification digest %q", got, specSHA256)
	}
	if got := str(t, "sources[0].retrieved", src["retrieved"]); !dateRe.MatchString(got) {
		t.Errorf("sources[0].retrieved is %q, want an ISO YYYY-MM-DD date", got)
	} else if _, err := time.Parse("2006-01-02", got); err != nil {
		t.Errorf("sources[0].retrieved is not a real calendar date: %v", err)
	}

	// Both links must be permalinks: pinned to the commit and naming the file.
	for _, field := range []string{"url", "rawUrl"} {
		link := str(t, "sources[0]."+field, src[field])
		if !strings.HasPrefix(link, "https://") {
			t.Errorf("sources[0].%s is %q, want an https URL", field, link)
		}
		if !strings.Contains(link, commit) {
			t.Errorf("sources[0].%s is %q, want it pinned to commit %s", field, link, commit)
		}
		if !strings.HasSuffix(link, specFilePath) {
			t.Errorf("sources[0].%s is %q, want it to name %s", field, link, specFilePath)
		}
	}
	if got := str(t, "sources[0].rawUrl", src["rawUrl"]); !strings.Contains(got, "raw.githubusercontent.com") {
		t.Errorf("sources[0].rawUrl is %q, want the raw permalink", got)
	}
	wantURL := "https://github.com/" + specRepository + "/blob/" + specCommit + "/" + specFilePath
	if got := str(t, "sources[0].url", src["url"]); got != wantURL {
		t.Errorf("sources[0].url is %q, want %q", got, wantURL)
	}
	wantRawURL := "https://raw.githubusercontent.com/" + specRepository + "/" + specCommit + "/" + specFilePath
	if got := str(t, "sources[0].rawUrl", src["rawUrl"]); got != wantRawURL {
		t.Errorf("sources[0].rawUrl is %q, want %q", got, wantRawURL)
	}

	ids := strList(t, "sources[0].operationIds", src["operationIds"])
	if !equalSet(ids, contractOperations) {
		t.Errorf("sources[0].operationIds is %v, want exactly %v", ids, contractOperations)
	}
}

// templateParams returns the parameters a path template carries.
func templateParams(path string) []string {
	var out []string
	for _, seg := range strings.Split(strings.Trim(path, "/"), "/") {
		if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
			out = append(out, seg[1:len(seg)-1])
		}
	}
	return out
}

func containsStr(hay []string, needle string) bool {
	for _, h := range hay {
		if h == needle {
			return true
		}
	}
	return false
}
