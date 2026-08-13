package contracttest

import (
	"context"
	"encoding/json"
	"errors"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"

	vcfops "example.com/vcfops"
)

const (
	wantRepository = "https://github.com/vmware/vcf-api-specs"
	wantTag        = "9.0.0.0"
	wantCommit     = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
	wantSpecPath   = "specifications/vcf-operations/vcf-operations-openapi.json"
)

func repositoryRoot(t testing.TB) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("locate acceptance test")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(file), "..", ".."))
}

func contractPath(t testing.TB) string {
	return filepath.Join(repositoryRoot(t), "docs", "contract.json")
}

func TestOfficialArtifactsArePinnedToVCF90Specification(t *testing.T) {
	t.Parallel()
	type operation struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	}
	var sources struct {
		Repository string      `json:"repository"`
		License    string      `json:"license"`
		Tag        string      `json:"tag"`
		Commit     string      `json:"commit"`
		SpecPath   string      `json:"spec_path"`
		Operations []operation `json:"operations"`
	}
	b, err := os.ReadFile(filepath.Join(repositoryRoot(t), "docs", "official_sources.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, &sources); err != nil {
		t.Fatal(err)
	}
	if sources.Repository != wantRepository || sources.License != "Apache-2.0" ||
		sources.Tag != wantTag || sources.Commit != wantCommit || sources.SpecPath != wantSpecPath {
		t.Fatalf("unexpected official source: %+v", sources)
	}
	wantOperations := []operation{
		{OperationID: "createCollectorGroup", Method: "POST", Path: "/api/collectorgroups"},
		{OperationID: "getCollectorGroups", Method: "GET", Path: "/api/collectorgroups"},
	}
	sort.Slice(sources.Operations, func(i, j int) bool { return sources.Operations[i].OperationID < sources.Operations[j].OperationID })
	if !reflect.DeepEqual(sources.Operations, wantOperations) {
		t.Fatalf("unexpected source operations: %#v", sources.Operations)
	}

	contractBytes, err := os.ReadFile(contractPath(t))
	if err != nil {
		t.Fatal(err)
	}
	var contract map[string]any
	if err := json.Unmarshal(contractBytes, &contract); err != nil {
		t.Fatal(err)
	}
	if contract["openapi"] != "3.0.1" {
		t.Fatalf("openapi = %v", contract["openapi"])
	}
	wantSource := map[string]any{
		"repository": wantRepository,
		"tag":        wantTag,
		"commit":     wantCommit,
		"path":       wantSpecPath,
	}
	if !reflect.DeepEqual(contract["x-official-source"], wantSource) {
		t.Fatalf("contract source = %#v", contract["x-official-source"])
	}
	servers := contract["servers"].([]any)
	if len(servers) != 1 || servers[0].(map[string]any)["url"] != "/suite-api" {
		t.Fatalf("unexpected servers: %#v", servers)
	}
	paths := contract["paths"].(map[string]any)
	if len(paths) != 1 {
		t.Fatalf("contract must contain one path, got %d", len(paths))
	}
	item := paths["/api/collectorgroups"].(map[string]any)
	if len(item) != 2 || item["get"].(map[string]any)["operationId"] != "getCollectorGroups" ||
		item["post"].(map[string]any)["operationId"] != "createCollectorGroup" {
		t.Fatalf("contract does not contain exactly the named operations: %#v", item)
	}
	post := item["post"].(map[string]any)
	getResponses := item["get"].(map[string]any)["responses"].(map[string]any)
	postResponses := post["responses"].(map[string]any)
	if !reflect.DeepEqual(sortedKeys(getResponses), []string{"200", "500"}) ||
		!reflect.DeepEqual(sortedKeys(postResponses), []string{"201", "500"}) {
		t.Fatalf("unexpected response statuses: GET %v POST %v", sortedKeys(getResponses), sortedKeys(postResponses))
	}
	parameters := post["parameters"].([]any)
	if len(parameters) != 1 {
		t.Fatalf("create parameters = %#v", parameters)
	}
	parameter := parameters[0].(map[string]any)
	if parameter["name"] != "checkCollectorMembers" || parameter["in"] != "query" || parameter["required"] != false {
		t.Fatalf("unexpected create query contract: %#v", parameter)
	}
	parameterSchema := parameter["schema"].(map[string]any)
	if parameterSchema["type"] != "boolean" || parameterSchema["default"] != false {
		t.Fatalf("unexpected create query schema: %#v", parameterSchema)
	}
	requestBody := post["requestBody"].(map[string]any)
	jsonBody := requestBody["content"].(map[string]any)["application/json"].(map[string]any)
	if requestBody["required"] != true || jsonBody["schema"].(map[string]any)["$ref"] != "#/components/schemas/collector-group-create" {
		t.Fatalf("unexpected JSON request body contract: %#v", requestBody)
	}
	if responseSchemaRef(t, getResponses["200"]) != "#/components/schemas/collector-groups" ||
		responseSchemaRef(t, postResponses["201"]) != "#/components/schemas/collector-group" {
		t.Fatalf("unexpected success response schemas: GET %#v POST %#v", getResponses["200"], postResponses["201"])
	}
	components := contract["components"].(map[string]any)
	security := components["securitySchemes"].(map[string]any)["Token-based-authorization"].(map[string]any)
	if security["type"] != "apiKey" || security["name"] != "Authorization" || security["in"] != "header" {
		t.Fatalf("unexpected authorization scheme: %#v", security)
	}
	globalSecurity := contract["security"].([]any)
	if len(globalSecurity) != 1 || !reflect.DeepEqual(
		globalSecurity[0], map[string]any{"Token-based-authorization": []any{}},
	) {
		t.Fatalf("unexpected global security: %#v", globalSecurity)
	}
	schemas := components["schemas"].(map[string]any)
	if !reflect.DeepEqual(sortedKeys(schemas), []string{"collector-group", "collector-group-create", "collector-groups"}) {
		t.Fatalf("unexpected reduced schemas: %v", sortedKeys(schemas))
	}
	createSchema := schemas["collector-group-create"].(map[string]any)
	properties := createSchema["properties"].(map[string]any)
	wantProperties := []string{"collectorId", "description", "haEnabled", "lbEnabled", "name", "virtualIP"}
	gotProperties := make([]string, 0, len(properties))
	for name := range properties {
		gotProperties = append(gotProperties, name)
	}
	sort.Strings(gotProperties)
	if !reflect.DeepEqual(gotProperties, wantProperties) {
		t.Fatalf("create properties = %v", gotProperties)
	}
	if !reflect.DeepEqual(createSchema["required"], []any{"name"}) {
		t.Fatalf("create required = %#v", createSchema["required"])
	}
	if properties["name"].(map[string]any)["type"] != "string" ||
		properties["description"].(map[string]any)["type"] != "string" ||
		properties["haEnabled"].(map[string]any)["type"] != "boolean" ||
		properties["lbEnabled"].(map[string]any)["type"] != "boolean" ||
		properties["virtualIP"].(map[string]any)["type"] != "string" {
		t.Fatalf("unexpected create field types: %#v", properties)
	}
	collectorIDs := properties["collectorId"].(map[string]any)
	items := collectorIDs["items"].(map[string]any)
	if collectorIDs["type"] != "array" || items["type"] != "integer" || items["format"] != "int32" {
		t.Fatalf("unexpected collectorId contract: %#v", collectorIDs)
	}

	groupSchema := schemas["collector-group"].(map[string]any)
	groupProperties := groupSchema["properties"].(map[string]any)
	wantGroupProperties := []string{"collectorId", "description", "haEnabled", "id", "lbEnabled", "name", "systemDefined", "virtualIP"}
	if !reflect.DeepEqual(sortedKeys(groupProperties), wantGroupProperties) ||
		!reflect.DeepEqual(groupSchema["required"], []any{"id", "name"}) {
		t.Fatalf("unexpected collector-group response schema: %#v", groupSchema)
	}
	if groupProperties["id"].(map[string]any)["type"] != "string" ||
		groupProperties["id"].(map[string]any)["format"] != "uuid" ||
		groupProperties["systemDefined"].(map[string]any)["type"] != "boolean" {
		t.Fatalf("unexpected returned system fields: %#v", groupProperties)
	}
	for _, name := range []string{"name", "description", "haEnabled", "lbEnabled", "virtualIP"} {
		if !reflect.DeepEqual(groupProperties[name], properties[name]) {
			t.Fatalf("response field %q differs from create field: %#v / %#v", name, groupProperties[name], properties[name])
		}
	}
	if !reflect.DeepEqual(groupProperties["collectorId"], properties["collectorId"]) {
		t.Fatalf("response collectorId differs from create collectorId: %#v / %#v", groupProperties["collectorId"], properties["collectorId"])
	}

	groupsSchema := schemas["collector-groups"].(map[string]any)
	groupsProperties := groupsSchema["properties"].(map[string]any)
	if !reflect.DeepEqual(sortedKeys(groupsProperties), []string{"collectorGroups"}) {
		t.Fatalf("unexpected collector-groups properties: %#v", groupsProperties)
	}
	collection := groupsProperties["collectorGroups"].(map[string]any)
	if collection["type"] != "array" || collection["items"].(map[string]any)["$ref"] != "#/components/schemas/collector-group" {
		t.Fatalf("unexpected collector-groups collection: %#v", collection)
	}
}

func responseSchemaRef(t testing.TB, value any) string {
	t.Helper()
	response := value.(map[string]any)
	content := response["content"].(map[string]any)
	jsonContent := content["application/json"].(map[string]any)
	return jsonContent["schema"].(map[string]any)["$ref"].(string)
}

func sortedKeys(values map[string]any) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func TestPackageIncludesTableDrivenTests(t *testing.T) {
	root := repositoryRoot(t)
	paths, err := filepath.Glob(filepath.Join(root, "*_test.go"))
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range paths {
		file, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", path, err)
		}
		hasTable := false
		hasSubtest := false
		importsContracttest := false
		hasContractMock := false
		hasRequestLog := false
		hasResponseLoss := false
		for _, spec := range file.Imports {
			importPath, err := strconv.Unquote(spec.Path.Value)
			if err == nil && strings.HasSuffix(importPath, "/internal/contracttest") {
				importsContracttest = true
			}
		}
		ast.Inspect(file, func(node ast.Node) bool {
			switch node := node.(type) {
			case *ast.CompositeLit:
				array, ok := node.Type.(*ast.ArrayType)
				if ok && array.Len == nil {
					if _, ok := array.Elt.(*ast.StructType); ok {
						hasTable = true
					}
				}
			case *ast.CallExpr:
				selector, ok := node.Fun.(*ast.SelectorExpr)
				if ok {
					switch selector.Sel.Name {
					case "Run":
						hasSubtest = true
					case "NewMock":
						hasContractMock = true
					case "Logs":
						hasRequestLog = true
					case "DropNextCreateResponse":
						hasResponseLoss = true
					}
				}
			}
			return true
		})
		if hasTable && hasSubtest && importsContracttest && hasContractMock && hasRequestLog && hasResponseLoss {
			return
		}
	}
	t.Fatal("add a table-driven root package test using contracttest.NewMock, its Logs, DropNextCreateResponse, a []struct table, and t.Run subtests")
}

func TestEnsureCollectorGroupWireShapeAndRetry(t *testing.T) {
	text := func(v string) *string { return &v }
	boolean := func(v bool) *bool { return &v }
	tests := []struct {
		name      string
		desired   vcfops.CollectorGroupInput
		wantQuery string
		wantBody  map[string]any
	}{
		{
			name:     "unset optional fields are omitted",
			desired:  vcfops.CollectorGroupInput{Name: "edge-proxies"},
			wantBody: map[string]any{"name": "edge-proxies"},
		},
		{
			name: "populated fields preserve explicit false",
			desired: vcfops.CollectorGroupInput{
				Name: "ha-proxies", Description: text("zone-a"), CollectorIDs: []int32{7, 9},
				HAEnabled: boolean(false), LBEnabled: boolean(true), VirtualIP: text("192.0.2.10"),
				CheckCollectorMembers: boolean(false),
			},
			wantQuery: "checkCollectorMembers=false",
			wantBody: map[string]any{
				"name": "ha-proxies", "description": "zone-a", "collectorId": []any{float64(7), float64(9)},
				"haEnabled": false, "lbEnabled": true, "virtualIP": "192.0.2.10",
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			mock := NewMock(t, contractPath(t))
			if got := mock.OperationIDs(); !reflect.DeepEqual(got, []string{"createCollectorGroup", "getCollectorGroups"}) {
				t.Fatalf("mock operations = %v", got)
			}
			client, err := vcfops.NewClient(mock.URL(), "test-token", mock.HTTPClient())
			if err != nil {
				t.Fatal(err)
			}
			group, created, err := client.EnsureCollectorGroup(context.Background(), tt.desired)
			if err != nil {
				t.Fatal(err)
			}
			if !created || group.Name != tt.desired.Name || group.ID == "" {
				t.Fatalf("first ensure = (%+v, %v)", group, created)
			}
			again, created, err := client.EnsureCollectorGroup(context.Background(), tt.desired)
			if err != nil {
				t.Fatal(err)
			}
			if created || again.ID != group.ID || mock.GroupCount() != 1 {
				t.Fatalf("retry = (%+v, %v), effects=%d", again, created, mock.GroupCount())
			}
			logs := mock.Logs()
			if len(logs) != 3 {
				t.Fatalf("request count = %d", len(logs))
			}
			wantMethods := []string{"GET", "POST", "GET"}
			for i, log := range logs {
				if log.Method != wantMethods[i] || log.Path != "/suite-api/api/collectorgroups" {
					t.Errorf("request %d = %s %s", i, log.Method, log.Path)
				}
				if log.Header.Get("Authorization") != "test-token" || log.Header.Get("Accept") != "application/json" {
					t.Errorf("request %d headers = %#v", i, log.Header)
				}
				if log.Method == "GET" && (log.RawQuery != "" || len(log.Body) != 0 || log.Header.Get("Content-Type") != "") {
					t.Errorf("GET wire shape = query %q body %q content-type %q", log.RawQuery, log.Body, log.Header.Get("Content-Type"))
				}
			}
			post := logs[1]
			if post.RawQuery != tt.wantQuery || post.Header.Get("Content-Type") != "application/json" {
				t.Errorf("POST query/content-type = %q/%q", post.RawQuery, post.Header.Get("Content-Type"))
			}
			var gotBody map[string]any
			if err := json.Unmarshal(post.Body, &gotBody); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(gotBody, tt.wantBody) {
				t.Errorf("POST body = %#v, want %#v", gotBody, tt.wantBody)
			}
		})
	}
}

func TestRetryAfterCreateResponseIsLostDoesNotDuplicate(t *testing.T) {
	mock := NewMock(t, contractPath(t))
	client, err := vcfops.NewClient(mock.URL(), "test-token", mock.HTTPClient())
	if err != nil {
		t.Fatal(err)
	}
	mock.DropNextCreateResponse()
	if _, _, err := client.EnsureCollectorGroup(context.Background(), vcfops.CollectorGroupInput{Name: "lossy-create"}); err == nil {
		t.Fatal("first ensure unexpectedly succeeded after response loss")
	}
	group, created, err := client.EnsureCollectorGroup(context.Background(), vcfops.CollectorGroupInput{Name: "lossy-create"})
	if err != nil {
		t.Fatal(err)
	}
	if created || group.Name != "lossy-create" || mock.GroupCount() != 1 {
		t.Fatalf("retry = (%+v, %v), effects=%d", group, created, mock.GroupCount())
	}
	methods := make([]string, 0, 3)
	for _, log := range mock.Logs() {
		methods = append(methods, log.Method)
	}
	if strings.Join(methods, ",") != "GET,POST,GET" {
		t.Fatalf("request methods = %v", methods)
	}
}

func TestEnsureCollectorGroupReportsStatusAndDecodeErrors(t *testing.T) {
	tests := []struct {
		name       string
		operation  string
		status     int
		body       string
		wantParts  []string
		wantEffect int
	}{
		{
			name: "list status", operation: "getCollectorGroups",
			status: http.StatusServiceUnavailable, body: "maintenance window",
			wantParts: []string{"list collector groups", "503 Service Unavailable", "maintenance window"},
		},
		{
			name: "list decode", operation: "getCollectorGroups",
			status: http.StatusOK, body: "{",
			wantParts: []string{"decode collector groups response"},
		},
		{
			name: "create status", operation: "createCollectorGroup",
			status: http.StatusInternalServerError, body: "membership rejected",
			wantParts: []string{"create collector group", "500 Internal Server Error", "membership rejected"},
		},
		{
			name: "create decode", operation: "createCollectorGroup",
			status: http.StatusCreated, body: "{",
			wantParts: []string{"decode create collector group response"},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			mock := NewMock(t, contractPath(t))
			mock.QueueResponse(tt.operation, tt.status, tt.body)
			client, err := vcfops.NewClient(mock.URL(), "test-token", mock.HTTPClient())
			if err != nil {
				t.Fatal(err)
			}
			_, _, err = client.EnsureCollectorGroup(context.Background(), vcfops.CollectorGroupInput{Name: "error-case"})
			if err == nil {
				t.Fatal("EnsureCollectorGroup unexpectedly succeeded")
			}
			for _, part := range tt.wantParts {
				if !strings.Contains(err.Error(), part) {
					t.Errorf("error %q does not contain %q", err, part)
				}
			}
			if mock.GroupCount() != tt.wantEffect {
				t.Errorf("mutation effects = %d, want %d", mock.GroupCount(), tt.wantEffect)
			}
		})
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

type countingTransport struct {
	base  http.RoundTripper
	calls atomic.Int32
}

func (t *countingTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	t.calls.Add(1)
	return t.base.RoundTrip(request)
}

func TestEnsureCollectorGroupUsesSuppliedClientAndPreservesTransportErrors(t *testing.T) {
	t.Run("supplied client", func(t *testing.T) {
		mock := NewMock(t, contractPath(t))
		transport := &countingTransport{base: mock.HTTPClient().Transport}
		httpClient := &http.Client{Transport: transport}
		client, err := vcfops.NewClient(mock.URL(), "test-token", httpClient)
		if err != nil {
			t.Fatal(err)
		}
		if _, _, err := client.EnsureCollectorGroup(context.Background(), vcfops.CollectorGroupInput{Name: "supplied-client"}); err != nil {
			t.Fatal(err)
		}
		if got := transport.calls.Load(); got != 2 {
			t.Fatalf("supplied transport calls = %d, want 2", got)
		}
	})

	t.Run("transport error", func(t *testing.T) {
		transportErr := errors.New("fixture transport unavailable")
		httpClient := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
			return nil, transportErr
		})}
		client, err := vcfops.NewClient("http://127.0.0.1", "test-token", httpClient)
		if err != nil {
			t.Fatal(err)
		}
		_, _, err = client.EnsureCollectorGroup(context.Background(), vcfops.CollectorGroupInput{Name: "transport-error"})
		if !errors.Is(err, transportErr) || !strings.Contains(err.Error(), "list collector groups") {
			t.Fatalf("transport error = %v", err)
		}
	})

	t.Run("create transport error", func(t *testing.T) {
		transportErr := errors.New("fixture create transport unavailable")
		httpClient := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
			if request.Method == http.MethodGet {
				return &http.Response{
					StatusCode: http.StatusOK,
					Status:     "200 OK",
					Header:     make(http.Header),
					Body:       io.NopCloser(strings.NewReader(`{"collectorGroups":[]}`)),
					Request:    request,
				}, nil
			}
			return nil, transportErr
		})}
		client, err := vcfops.NewClient("http://127.0.0.1", "test-token", httpClient)
		if err != nil {
			t.Fatal(err)
		}
		_, _, err = client.EnsureCollectorGroup(context.Background(), vcfops.CollectorGroupInput{Name: "create-transport-error"})
		if !errors.Is(err, transportErr) || !strings.Contains(err.Error(), "create collector group") {
			t.Fatalf("create transport error = %v", err)
		}
	})
}

func TestEnsureCollectorGroupPreservesContextCancellation(t *testing.T) {
	httpClient := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		return nil, request.Context().Err()
	})}
	client, err := vcfops.NewClient("http://127.0.0.1", "test-token", httpClient)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, _, err = client.EnsureCollectorGroup(ctx, vcfops.CollectorGroupInput{Name: "cancelled"})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("cancellation error = %v", err)
	}
}
