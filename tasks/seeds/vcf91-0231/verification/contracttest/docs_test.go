package contracttest

import (
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
)

const (
	specRepo   = "vmware/vcf-api-specs"
	specPath   = "specifications/sddc-lcm/sddc-lcm-openapi.yaml"
	specLicen  = "Apache-2.0"
	specCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	specHash   = "158cab89bc56e1bb80b662a859499efc6ee57c1d35503d4d1f855809c213436c"
)

// operationIDs are the operations this tool calls, as the specification names
// them.
var operationIDs = []string{
	"backupRestoreComponentsAction",
	"fetchComponentStatuses",
	"getComponents",
	"getComponentsBackups",
	"getTask",
}

var (
	shaRe  = regexp.MustCompile(`^[0-9a-f]{40}$`)
	hashRe = regexp.MustCompile(`^[0-9a-f]{64}$`)
	dateRe = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)
)

func readJSON(t *testing.T, path string) map[string]any {
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

func str(t *testing.T, where string, obj map[string]any, key string) string {
	t.Helper()
	v, ok := obj[key]
	if !ok {
		t.Fatalf("%s has no %q", where, key)
	}
	s, ok := v.(string)
	if !ok {
		t.Fatalf("%s.%s is not a string", where, key)
	}
	return s
}

func TestContractDocument(t *testing.T) {
	t.Parallel()
	doc := readJSON(t, contractPath)

	if got, want := keysOf(t, contractPath, doc),
		[]string{"operations", "schemas", "security", "server", "source"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("%s top level keys are %v, want %v", contractPath, got, want)
	}

	source, _ := doc["source"].(map[string]any)
	if got, want := keysOf(t, "source", doc["source"]),
		[]string{"apiTitle", "apiVersion", "commit", "license", "openapi", "repository", "specPath"}; !reflect.DeepEqual(got, want) {
		t.Errorf("source keys are %v, want %v", got, want)
	}
	if got := str(t, "source", source, "repository"); got != specRepo {
		t.Errorf("source.repository is %q, want %q", got, specRepo)
	}
	if got := str(t, "source", source, "specPath"); got != specPath {
		t.Errorf("source.specPath is %q, want %q", got, specPath)
	}
	if got := str(t, "source", source, "license"); got != specLicen {
		t.Errorf("source.license is %q, want %q", got, specLicen)
	}
	if got := str(t, "source", source, "commit"); !shaRe.MatchString(got) {
		t.Errorf("source.commit is %q, want the full 40 character commit sha the spec was read at", got)
	}
	if got := str(t, "source", source, "openapi"); !strings.HasPrefix(got, "3.") {
		t.Errorf("source.openapi is %q, want the spec's openapi version", got)
	}
	if got := str(t, "source", source, "apiVersion"); !strings.HasPrefix(got, "9.1") {
		t.Errorf("source.apiVersion is %q, want the spec's info.version", got)
	}
	if got := str(t, "source", source, "apiTitle"); got == "" {
		t.Errorf("source.apiTitle is empty, want the spec's info.title")
	}
	requireEqualJSON(t, "source", source, map[string]any{
		"repository": specRepo,
		"specPath":   specPath,
		"commit":     specCommit,
		"license":    specLicen,
		"openapi":    "3.0.4",
		"apiTitle":   "VCF SDDC LCM Service APIs",
		"apiVersion": "9.1.0.0",
	})

	if got := doc["server"]; !strings.HasPrefix(fmt.Sprint(got), "https://") {
		t.Errorf("server is %v, want the server URL the spec publishes", got)
	}
	if got, want := doc["server"], "https://vcf.broadcom.com/sddc-lcm"; got != want {
		t.Errorf("server is %v, want %q", got, want)
	}

	security, _ := doc["security"].(map[string]any)
	if got, want := keysOf(t, "security", doc["security"]),
		[]string{"bearerFormat", "httpScheme", "scheme", "type"}; !reflect.DeepEqual(got, want) {
		t.Errorf("security keys are %v, want %v", got, want)
	}
	for _, key := range []string{"bearerFormat", "httpScheme", "scheme", "type"} {
		if str(t, "security", security, key) == "" {
			t.Errorf("security.%s is empty", key)
		}
	}
	requireEqualJSON(t, "security", security, map[string]any{
		"scheme":       "bearerToken",
		"type":         "http",
		"httpScheme":   "Bearer",
		"bearerFormat": "JWT",
	})

	if got := keysOf(t, "operations", doc["operations"]); !reflect.DeepEqual(got, operationIDs) {
		t.Errorf("operations names %v, want exactly %v", got, operationIDs)
	}

	operations, _ := doc["operations"].(map[string]any)
	baseKeys := []string{
		"method", "optionalHeaders", "path", "pathParams",
		"queryParams", "requestSchema", "responseSchema", "successStatus",
	}
	withVariants := append(append([]string{}, baseKeys...), "requestVariants")
	sort.Strings(withVariants)
	variantCount := 0
	for _, name := range operationIDs {
		entry, ok := operations[name].(map[string]any)
		if !ok {
			continue
		}
		got := keysOf(t, "operations."+name, entry)
		if _, declared := entry["requestVariants"]; declared {
			variantCount++
			if !reflect.DeepEqual(got, withVariants) {
				t.Errorf("operations.%s keys are %v, want %v", name, got, withVariants)
			}
		} else if !reflect.DeepEqual(got, baseKeys) {
			t.Errorf("operations.%s keys are %v, want %v", name, got, baseKeys)
		}
		if str(t, "operations."+name, entry, "responseSchema") == "" {
			t.Errorf("operations.%s.responseSchema is empty", name)
		}
		if got, want := keysOf(t, "operations."+name+".queryParams", entry["queryParams"]),
			[]string{"optional", "required"}; !reflect.DeepEqual(got, want) {
			t.Errorf("operations.%s.queryParams keys are %v, want %v", name, got, want)
		}
	}
	if variantCount != 1 {
		t.Errorf("%d operations declare requestVariants, want exactly the one whose request body the spec defines as a discriminated oneOf",
			variantCount)
	}
	requireEqualJSON(t, "operations", operations, parseJSON(t, `{
		"backupRestoreComponentsAction": {
			"method": "POST", "path": "/v1/components/backups", "successStatus": 202,
			"requestSchema": "BackupRestoreSpec", "responseSchema": "Task",
			"pathParams": [], "queryParams": {"required": [], "optional": []},
			"optionalHeaders": ["X-Correlation-Id"],
			"requestVariants": {"discriminator": "actionType", "variants": ["ComponentsBackupSpec", "ComponentsRestoreSpec"]}
		},
		"fetchComponentStatuses": {
			"method": "POST", "path": "/v1/components/status", "successStatus": 200,
			"requestSchema": "ComponentStatusesSpec", "responseSchema": "ComponentStatuses",
			"pathParams": [], "queryParams": {"required": [], "optional": []}, "optionalHeaders": []
		},
		"getComponents": {
			"method": "GET", "path": "/v1/components", "successStatus": 200,
			"requestSchema": null, "responseSchema": "Components", "pathParams": [],
			"queryParams": {"required": [], "optional": ["scope"]}, "optionalHeaders": []
		},
		"getComponentsBackups": {
			"method": "GET", "path": "/v1/components/backups", "successStatus": 200,
			"requestSchema": null, "responseSchema": "ComponentBackups", "pathParams": [],
			"queryParams": {"required": [], "optional": ["componentId", "periodEnd", "periodStart"]},
			"optionalHeaders": []
		},
		"getTask": {
			"method": "GET", "path": "/v1/tasks/{taskId}", "successStatus": 200,
			"requestSchema": null, "responseSchema": "Task", "pathParams": ["taskId"],
			"queryParams": {"required": [], "optional": []}, "optionalHeaders": []
		}
	}`))

	wantSchemas := []string{
		"ComponentStatusesSpec",
		"ComponentsBackupSpec",
		"ComponentsRestoreSpec",
		"RestoreBackupSpec",
	}
	if got := keysOf(t, "schemas", doc["schemas"]); !reflect.DeepEqual(got, wantSchemas) {
		t.Errorf("schemas names %v, want exactly %v", got, wantSchemas)
	}
	schemas, _ := doc["schemas"].(map[string]any)
	for _, name := range wantSchemas {
		entry, ok := schemas[name]
		if !ok {
			continue
		}
		if got, want := keysOf(t, "schemas."+name, entry),
			[]string{"optional", "required"}; !reflect.DeepEqual(got, want) {
			t.Errorf("schemas.%s keys are %v, want %v", name, got, want)
		}
	}
	requireEqualJSON(t, "schemas", schemas, parseJSON(t, `{
		"ComponentStatusesSpec": {"required": [], "optional": ["componentIds"]},
		"ComponentsBackupSpec": {"required": ["actionType", "componentIds"], "optional": []},
		"ComponentsRestoreSpec": {"required": ["actionType", "components"], "optional": ["encryptionPassphrase"]},
		"RestoreBackupSpec": {"required": [], "optional": ["componentId", "componentType", "path", "point"]}
	}`))

	requireSortedStringLists(t, contractPath, doc)
}

func TestOfficialSources(t *testing.T) {
	t.Parallel()
	doc := readJSON(t, sourcesPath)
	if got, want := keysOf(t, sourcesPath, doc), []string{"sources"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("%s top level keys are %v, want %v", sourcesPath, got, want)
	}

	list, ok := doc["sources"].([]any)
	if !ok || len(list) != 1 {
		t.Fatalf("%s sources has length %d, want exactly one pinned source", sourcesPath, len(list))
	}
	entry, ok := list[0].(map[string]any)
	if !ok {
		t.Fatalf("%s: sources[0] is not an object", sourcesPath)
	}

	wantKeys := []string{
		"commit", "license", "operationIds", "path", "rawUrl",
		"repository", "retrieved", "sha256", "title", "url",
	}
	if got := keysOf(t, "sources[0]", list[0]); !reflect.DeepEqual(got, wantKeys) {
		t.Fatalf("sources[0] keys are %v, want %v", got, wantKeys)
	}

	commit := str(t, "sources[0]", entry, "commit")
	if !shaRe.MatchString(commit) {
		t.Errorf("sources[0].commit is %q, want a full 40 character commit sha", commit)
	}
	if commit != specCommit {
		t.Errorf("sources[0].commit is %q, want pinned commit %q", commit, specCommit)
	}
	if got, want := str(t, "sources[0]", entry, "repository"), "https://github.com/"+specRepo; got != want {
		t.Errorf("sources[0].repository is %q, want %q", got, want)
	}
	if got := str(t, "sources[0]", entry, "path"); got != specPath {
		t.Errorf("sources[0].path is %q, want %q", got, specPath)
	}
	if got := str(t, "sources[0]", entry, "license"); got != specLicen {
		t.Errorf("sources[0].license is %q, want %q", got, specLicen)
	}
	if got := str(t, "sources[0]", entry, "title"); got == "" {
		t.Errorf("sources[0].title is empty")
	}
	if got := str(t, "sources[0]", entry, "retrieved"); !dateRe.MatchString(got) {
		t.Errorf("sources[0].retrieved is %q, want an ISO YYYY-MM-DD date", got)
	}
	if got := str(t, "sources[0]", entry, "sha256"); !hashRe.MatchString(got) {
		t.Errorf("sources[0].sha256 is %q, want the digest of the specification file", got)
	}
	if got := str(t, "sources[0]", entry, "sha256"); got != specHash {
		t.Errorf("sources[0].sha256 is %q, want digest %q of the pinned specification", got, specHash)
	}

	url := str(t, "sources[0]", entry, "url")
	if !strings.HasPrefix(url, "https://github.com/"+specRepo+"/") ||
		!strings.Contains(url, commit) || !strings.HasSuffix(url, specPath) {
		t.Errorf("sources[0].url is %q, want a github.com permalink pinned to %s", url, commit)
	}
	wantRaw := "https://raw.githubusercontent.com/" + specRepo + "/" + commit + "/" + specPath
	if got := str(t, "sources[0]", entry, "rawUrl"); got != wantRaw {
		t.Errorf("sources[0].rawUrl is %q, want %q", got, wantRaw)
	}

	var ids []string
	raw, _ := entry["operationIds"].([]any)
	for _, v := range raw {
		ids = append(ids, fmt.Sprint(v))
	}
	if !reflect.DeepEqual(ids, operationIDs) {
		t.Errorf("sources[0].operationIds is %v, want %v", ids, operationIDs)
	}

	contract := readJSON(t, contractPath)
	source, _ := contract["source"].(map[string]any)
	if got := str(t, "source", source, "commit"); got != commit {
		t.Errorf("the contract was derived at commit %q but the sources record %q", got, commit)
	}
}

// requireSortedStringLists walks the document and insists every array of
// strings is in lexicographic order.
func requireSortedStringLists(t *testing.T, where string, node any) {
	t.Helper()
	switch v := node.(type) {
	case map[string]any:
		keys := make([]string, 0, len(v))
		for k := range v {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			requireSortedStringLists(t, where+"."+k, v[k])
		}
	case []any:
		var strs []string
		for i, item := range v {
			if s, ok := item.(string); ok {
				strs = append(strs, s)
				continue
			}
			requireSortedStringLists(t, fmt.Sprintf("%s[%d]", where, i), item)
		}
		if len(strs) == len(v) && !sort.StringsAreSorted(strs) {
			t.Errorf("%s is %v, want it sorted lexicographically", where, strs)
		}
	}
}
