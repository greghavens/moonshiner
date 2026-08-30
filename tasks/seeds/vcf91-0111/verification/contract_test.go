package vcentercategories_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync/atomic"
	"testing"

	vc "vcf91-0111"
	"vcf91-0111/internal/contractmock"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedSpec   = "specifications/vsphere/openapi/automation/vcenter.yaml"
	expectedOpID   = "Vcenter.Tagging.Categories_list"
	contractSHA256 = "683d7eaf2f3776428d78895f65296ae2312771d52489e5af610a0e28d425a1f3"
	sourcesSHA256  = "f1321decae65239fe87d506c1222942181b8e54d89f24be855a3199f797950ac"
)

type operationSource struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

func TestProtectedContractProvenance(t *testing.T) {
	assertFileHash(t, "docs/contract.json", contractSHA256)
	assertFileHash(t, "docs/official_sources.json", sourcesSHA256)

	var contract struct {
		DerivedFrom struct {
			Commit   string `json:"repository_commit_sha"`
			BlobSHA  string `json:"source_blob_sha"`
			SpecPath string `json:"spec_path"`
			OpenAPI  string `json:"openapi"`
			Version  string `json:"info_version"`
			License  string `json:"repository_license"`
		} `json:"derived_from"`
		Servers []struct {
			URL      string `json:"url"`
			BasePath string `json:"base_path"`
		} `json:"servers"`
		SecuritySchemes map[string]struct {
			Type string `json:"type"`
			Name string `json:"name"`
			In   string `json:"in"`
		} `json:"security_schemes"`
		Operations []struct {
			operationSource
			Security   []string `json:"security"`
			Parameters []struct {
				Name      string `json:"name"`
				Style     string `json:"style"`
				Explode   bool   `json:"explode"`
				SchemaRef string `json:"schema_ref"`
				Schema    struct {
					Type        string `json:"type"`
					UniqueItems bool   `json:"uniqueItems"`
				} `json:"schema"`
			} `json:"query_parameters"`
		} `json:"operations"`
		Schemas map[string]struct {
			Required   []string                   `json:"required"`
			Properties map[string]json.RawMessage `json:"properties"`
		} `json:"schemas"`
	}
	readJSON(t, "docs/contract.json", &contract)

	var sources struct {
		Repository struct {
			Commit  string `json:"commit_sha"`
			License string `json:"license"`
		} `json:"repository"`
		Specification struct {
			Path    string `json:"path"`
			BlobSHA string `json:"blob_sha"`
			OpenAPI string `json:"openapi_version"`
			Version string `json:"info_version"`
		} `json:"specification"`
		Operations []operationSource `json:"operations"`
	}
	readJSON(t, "docs/official_sources.json", &sources)

	if contract.DerivedFrom.Commit != expectedCommit ||
		sources.Repository.Commit != expectedCommit {
		t.Fatalf("wrong repository commit: contract=%q sources=%q",
			contract.DerivedFrom.Commit, sources.Repository.Commit)
	}
	if contract.DerivedFrom.SpecPath != expectedSpec ||
		sources.Specification.Path != expectedSpec {
		t.Fatalf("wrong specification path: contract=%q sources=%q",
			contract.DerivedFrom.SpecPath, sources.Specification.Path)
	}
	if contract.DerivedFrom.BlobSHA != "8028b0824c4ff3503d05f44814f967938a795c40" ||
		sources.Specification.BlobSHA != contract.DerivedFrom.BlobSHA {
		t.Fatalf("source blob SHA mismatch: contract=%q sources=%q",
			contract.DerivedFrom.BlobSHA, sources.Specification.BlobSHA)
	}
	if contract.DerivedFrom.OpenAPI != "3.0.3" ||
		sources.Specification.OpenAPI != "3.0.3" ||
		contract.DerivedFrom.Version != "9.1.0.0" ||
		sources.Specification.Version != "9.1.0.0" {
		t.Fatalf("wrong OpenAPI/API version in protected provenance")
	}
	if contract.DerivedFrom.License != "Apache-2.0" ||
		sources.Repository.License != "Apache-2.0" {
		t.Fatalf("wrong repository license in protected provenance")
	}
	if !reflect.DeepEqual(contract.Servers, []struct {
		URL      string `json:"url"`
		BasePath string `json:"base_path"`
	}{{URL: "https://{host}/api", BasePath: "/api"}}) {
		t.Fatalf("server projection mismatch: %#v", contract.Servers)
	}
	if !reflect.DeepEqual(contract.SecuritySchemes["api_key_auth"], struct {
		Type string `json:"type"`
		Name string `json:"name"`
		In   string `json:"in"`
	}{Type: "apiKey", Name: "vmware-api-session-id", In: "header"}) {
		t.Fatalf("api_key_auth projection mismatch: %#v",
			contract.SecuritySchemes["api_key_auth"])
	}

	wantOperations := []operationSource{{
		OperationID: expectedOpID,
		Method:      http.MethodGet,
		Path:        "/vcenter/tagging/categories",
	}}
	gotOperations := make([]operationSource, len(contract.Operations))
	for index, operation := range contract.Operations {
		gotOperations[index] = operation.operationSource
	}
	if !reflect.DeepEqual(gotOperations, wantOperations) ||
		!reflect.DeepEqual(sources.Operations, wantOperations) {
		t.Fatalf("operation projection mismatch\ncontract: %#v\nsources: %#v",
			gotOperations, sources.Operations)
	}
	if !reflect.DeepEqual(contract.Operations[0].Security, []string{"api_key_auth"}) {
		t.Fatalf("operation security mismatch: %#v", contract.Operations[0].Security)
	}

	parameters := contract.Operations[0].Parameters
	if len(parameters) != 2 ||
		parameters[0].Name != "names" ||
		parameters[0].Style != "form" ||
		!parameters[0].Explode ||
		parameters[0].Schema.Type != "array" ||
		!parameters[0].Schema.UniqueItems ||
		parameters[1].Name != "iterate" ||
		parameters[1].Style != "form" ||
		!parameters[1].Explode ||
		parameters[1].SchemaRef != "#/components/schemas/Vcenter.Tagging.Categories.IterationSpec" {
		t.Fatalf("query parameter projection mismatch: %#v", parameters)
	}
	iteration := contract.Schemas["Vcenter.Tagging.Categories.IterationSpec"]
	properties := make([]string, 0, len(iteration.Properties))
	for name := range iteration.Properties {
		properties = append(properties, name)
	}
	sort.Strings(properties)
	if !reflect.DeepEqual(properties, []string{"marker", "page_size"}) {
		t.Fatalf("iteration properties mismatch: %v", properties)
	}
	if !reflect.DeepEqual(
		contract.Schemas["Vcenter.Tagging.Categories.ListResult"].Required,
		[]string{"items"},
	) {
		t.Fatalf("ListResult required fields were not projected")
	}
	if !reflect.DeepEqual(
		contract.Schemas["Vcenter.Tagging.Categories.ListItem"].Required,
		[]string{"category_id", "info"},
	) {
		t.Fatalf("ListItem required fields were not projected")
	}
}

func TestListAllCategoriesCompletesPaginationAndStabilizesOrder(t *testing.T) {
	server := startServer(t, contractmock.Plan{Categories: fixtureCategories(), PageWidth: 2})
	client := newClient(t, server)

	want := []vc.Category{
		category("cat-a1", "Alpha/β"),
		category("cat-a2", "Alpha/β"),
		category("cat-db", "DB + Tier"),
		category("cat-z", "Zeta"),
		category("cat-e", "éclair"),
	}
	for run := 0; run < 2; run++ {
		got, err := client.ListAllCategories(context.Background(), vc.ListOptions{})
		if err != nil {
			t.Fatalf("run %d ListAllCategories: %v", run, err)
		}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("run %d collection is incomplete or unstable\n got: %#v\nwant: %#v",
				run, got, want)
		}
	}

	requests := readRequests(t, server)
	if len(requests) != 6 {
		t.Fatalf("request count = %d, want 6: %#v", len(requests), requests)
	}
	secrets := server.Secrets()
	wantQueries := []string{
		"",
		url.Values{"marker": {secrets.Markers[0]}}.Encode(),
		url.Values{"marker": {secrets.Markers[1]}}.Encode(),
	}
	wantHost := strings.TrimPrefix(server.URL(), "http://")
	for index, request := range requests {
		query := wantQueries[index%len(wantQueries)]
		assertWireRequest(t, index, request, query, secrets.SessionID, wantHost)
		for _, forbidden := range []string{
			"names=",
			"page_size=",
			"marker=",
			"iterate=",
			"iterate.marker=",
			"iterate.page_size=",
		} {
			if query == "" && strings.Contains(request.RawQuery, forbidden) {
				t.Fatalf("request %d emitted unset optional query %q: %q",
					index, forbidden, request.RawQuery)
			}
		}
	}
}

func TestExplodedFilterAndIterationWireShape(t *testing.T) {
	server := startServer(t, contractmock.Plan{Categories: fixtureCategories(), PageWidth: 2})
	client := newClient(t, server)
	pageSize := int64(2)
	names := []string{"DB + Tier", "Alpha/β", "Zeta", "éclair"}

	got, err := client.ListAllCategories(context.Background(), vc.ListOptions{
		Names:    names,
		PageSize: &pageSize,
	})
	if err != nil {
		t.Fatalf("ListAllCategories: %v", err)
	}
	if len(got) != len(fixtureCategories()) {
		t.Fatalf("returned %d categories, want %d", len(got), len(fixtureCategories()))
	}

	requests := readRequests(t, server)
	if len(requests) != 3 {
		t.Fatalf("request count = %d, want 3: %#v", len(requests), requests)
	}
	secrets := server.Secrets()
	wantQueries := []string{
		url.Values{"names": names, "page_size": {"2"}}.Encode(),
		url.Values{"marker": {secrets.Markers[0]}, "page_size": {"2"}}.Encode(),
		url.Values{"marker": {secrets.Markers[1]}, "page_size": {"2"}}.Encode(),
	}
	wantHost := strings.TrimPrefix(server.URL(), "http://")
	for index, request := range requests {
		assertWireRequest(
			t,
			index,
			request,
			wantQueries[index],
			secrets.SessionID,
			wantHost,
		)
		if index > 0 && strings.Contains(request.RawQuery, "names=") {
			t.Fatalf("continuation request %d resent filter with marker: %q",
				index, request.RawQuery)
		}
	}
	if !strings.Contains(requests[0].RawQuery, "names=DB+%2B+Tier") ||
		!strings.Contains(requests[0].RawQuery, "names=Alpha%2F%CE%B2") ||
		!strings.Contains(requests[0].RawQuery, "names=%C3%A9clair") {
		t.Fatalf("first request did not use standard form encoding: %q",
			requests[0].RawQuery)
	}
}

func TestEmptyPageWithMarkerDoesNotTerminateTraversal(t *testing.T) {
	server := startServer(t, contractmock.Plan{
		Categories: fixtureCategories(),
		PageWidth:  2,
		MutatePage: func(pageIndex int, payload map[string]any) {
			if pageIndex == 0 {
				payload["items"] = []map[string]any{}
			}
		},
	})
	client := newClient(t, server)

	got, err := client.ListAllCategories(context.Background(), vc.ListOptions{})
	if err != nil {
		t.Fatalf("ListAllCategories: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("client stopped at empty nonterminal page or fabricated items: %#v", got)
	}
	if requests := readRequests(t, server); len(requests) != 3 {
		t.Fatalf("empty page with marker produced %d requests, want 3", len(requests))
	}
}

func TestLocalValidationOccursBeforeTraffic(t *testing.T) {
	configCases := []struct {
		name   string
		config vc.Config
	}{
		{name: "empty URL", config: vc.Config{SessionID: "session"}},
		{name: "non HTTP scheme", config: vc.Config{BaseURL: "ftp://example.test", SessionID: "session"}},
		{name: "credentials in URL", config: vc.Config{BaseURL: "http://u:p@example.test", SessionID: "session"}},
		{name: "non root path", config: vc.Config{BaseURL: "http://example.test/api", SessionID: "session"}},
		{name: "query", config: vc.Config{BaseURL: "http://example.test/?x=1", SessionID: "session"}},
		{name: "empty query delimiter", config: vc.Config{BaseURL: "http://example.test?", SessionID: "session"}},
		{name: "fragment", config: vc.Config{BaseURL: "http://example.test/#x", SessionID: "session"}},
		{name: "blank session", config: vc.Config{BaseURL: "http://example.test", SessionID: "  "}},
		{name: "header newline", config: vc.Config{BaseURL: "http://example.test", SessionID: "session\ninjected"}},
		{name: "header control", config: vc.Config{BaseURL: "http://example.test", SessionID: "session\x7f"}},
	}
	for _, test := range configCases {
		t.Run("config/"+test.name, func(t *testing.T) {
			transport := &countingTransport{}
			test.config.HTTPClient = &http.Client{Transport: transport}
			if _, err := vc.NewClient(test.config); err == nil {
				t.Fatal("NewClient unexpectedly succeeded")
			}
			if got := transport.Count(); got != 0 {
				t.Fatalf("validation made %d requests", got)
			}
		})
	}

	transport := &countingTransport{}
	client, err := vc.NewClient(vc.Config{
		BaseURL:    "http://example.test",
		SessionID:  "session",
		HTTPClient: &http.Client{Transport: transport},
	})
	if err != nil {
		t.Fatalf("valid NewClient: %v", err)
	}
	zero := int64(0)
	negative := int64(-1)
	optionCases := []struct {
		name    string
		ctx     context.Context
		options vc.ListOptions
	}{
		{name: "nil context", ctx: nil},
		{name: "empty names", ctx: context.Background(), options: vc.ListOptions{Names: []string{}}},
		{name: "blank name", ctx: context.Background(), options: vc.ListOptions{Names: []string{"ok", " "}}},
		{name: "duplicate name", ctx: context.Background(), options: vc.ListOptions{Names: []string{"same", "same"}}},
		{name: "zero page size", ctx: context.Background(), options: vc.ListOptions{PageSize: &zero}},
		{name: "negative page size", ctx: context.Background(), options: vc.ListOptions{PageSize: &negative}},
	}
	for _, test := range optionCases {
		t.Run("options/"+test.name, func(t *testing.T) {
			before := transport.Count()
			if _, err := client.ListAllCategories(test.ctx, test.options); err == nil {
				t.Fatal("ListAllCategories unexpectedly succeeded")
			}
			if got := transport.Count(); got != before {
				t.Fatalf("validation made %d requests, count was %d", got-before, before)
			}
		})
	}
}

func TestProtocolFailuresNeverReturnPartialCollections(t *testing.T) {
	tests := []struct {
		name string
		plan contractmock.Plan
	}{
		{
			name: "missing items",
			plan: contractmock.Plan{
				Categories: fixtureCategories(),
				MutatePage: func(pageIndex int, payload map[string]any) {
					if pageIndex == 0 {
						delete(payload, "items")
					}
				},
			},
		},
		{
			name: "null info",
			plan: contractmock.Plan{
				Categories: fixtureCategories(),
				MutatePage: func(pageIndex int, payload map[string]any) {
					if pageIndex == 1 {
						items := payload["items"].([]map[string]any)
						items[0]["info"] = nil
					}
				},
			},
		},
		{
			name: "missing required info array",
			plan: contractmock.Plan{
				Categories: fixtureCategories(),
				MutatePage: func(pageIndex int, payload map[string]any) {
					if pageIndex == 1 {
						items := payload["items"].([]map[string]any)
						info := items[0]["info"].(map[string]any)
						delete(info, "used_by")
					}
				},
			},
		},
		{
			name: "empty marker",
			plan: contractmock.Plan{
				Categories: fixtureCategories(),
				MutatePage: func(pageIndex int, payload map[string]any) {
					if pageIndex == 0 {
						payload["marker"] = ""
					}
				},
			},
		},
		{
			name: "repeated marker",
			plan: contractmock.Plan{
				Categories:   fixtureCategories(),
				RepeatMarker: true,
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := startServer(t, test.plan)
			client := newClient(t, server)
			got, err := client.ListAllCategories(context.Background(), vc.ListOptions{})
			if err == nil {
				t.Fatalf("unexpected success with collection %#v", got)
			}
			var protocolError *vc.ProtocolError
			if !errors.As(err, &protocolError) {
				t.Fatalf("error type = %T, want *ProtocolError", err)
			}
			if protocolError.OperationID != expectedOpID || got != nil {
				t.Fatalf("protocol failure returned partial data or wrong operation: got=%#v error=%#v",
					got, protocolError)
			}
			if strings.Contains(err.Error(), server.Secrets().SessionID) {
				t.Fatal("protocol error text exposed the session ID")
			}
		})
	}
}

func TestAPIErrorPreservesFieldsButRedactsErrorText(t *testing.T) {
	for _, status := range []int{http.StatusUnauthorized, http.StatusInternalServerError} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			server := startServer(t, contractmock.Plan{
				Categories: fixtureCategories(),
				StatusCode: status,
			})
			client := newClient(t, server)

			got, err := client.ListAllCategories(context.Background(), vc.ListOptions{})
			if err == nil {
				t.Fatalf("unexpected success with collection %#v", got)
			}
			var apiError *vc.APIError
			if !errors.As(err, &apiError) {
				t.Fatalf("error type = %T, want *APIError", err)
			}
			if apiError.OperationID != expectedOpID ||
				apiError.StatusCode != status ||
				apiError.ErrorType != "MOCK_FAILURE" ||
				len(apiError.Messages) != 1 ||
				apiError.Messages[0].ID != "mock.failure" {
				t.Fatalf("API error fields were not preserved: %#v", apiError)
			}
			for _, secret := range []string{
				server.Secrets().SessionID,
				apiError.Messages[0].DefaultMessage,
			} {
				if strings.Contains(err.Error(), secret) {
					t.Fatalf("error text exposed sensitive/server-controlled text %q", secret)
				}
			}
			if got != nil {
				t.Fatalf("API failure returned a partial collection: %#v", got)
			}
		})
	}
}

func TestContinuationHTTPFailureReturnsNoPartialCollection(t *testing.T) {
	server := startServer(t, contractmock.Plan{
		Categories:       fixtureCategories(),
		StatusCode:       http.StatusInternalServerError,
		FailContinuation: true,
	})
	client := newClient(t, server)

	got, err := client.ListAllCategories(context.Background(), vc.ListOptions{})
	if err == nil {
		t.Fatalf("unexpected success with collection %#v", got)
	}
	var apiError *vc.APIError
	if !errors.As(err, &apiError) || apiError.StatusCode != http.StatusInternalServerError {
		t.Fatalf("error = %#v, want continuation *APIError", err)
	}
	if got != nil {
		t.Fatalf("continuation failure returned partial collection: %#v", got)
	}
	if requests := readRequests(t, server); len(requests) != 2 {
		t.Fatalf("continuation failure made %d requests, want 2", len(requests))
	}
}

func TestCanceledContextIsPreservedWithoutTraffic(t *testing.T) {
	transport := &countingTransport{}
	client, err := vc.NewClient(vc.Config{
		BaseURL:    "http://example.test",
		SessionID:  "session",
		HTTPClient: &http.Client{Transport: transport},
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := client.ListAllCategories(ctx, vc.ListOptions{}); !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context.Canceled", err)
	}
	if got := transport.Count(); got != 0 {
		t.Fatalf("canceled context made %d transport requests", got)
	}
}

func assertWireRequest(
	t *testing.T,
	index int,
	got contractmock.Request,
	wantQuery string,
	sessionID string,
	wantHost string,
) {
	t.Helper()
	const wantPath = "/api/vcenter/tagging/categories"
	wantURI := wantPath
	if wantQuery != "" {
		wantURI += "?" + wantQuery
	}
	if got.OperationID != expectedOpID ||
		got.Method != http.MethodGet ||
		got.Path != wantPath ||
		got.RawQuery != wantQuery ||
		got.RequestURI != wantURI {
		t.Fatalf("request %d target mismatch\n got: %+v\nwant operation=%s method=GET uri=%s",
			index, got, expectedOpID, wantURI)
	}
	if got.Host != wantHost {
		t.Fatalf("request %d Host = %q, want %q", index, got.Host, wantHost)
	}
	if got.ContentLength != 0 || len(got.TransferEncoding) != 0 || got.Body != "" {
		t.Fatalf("request %d GET body framing mismatch: length=%d transfer=%#v body=%q",
			index, got.ContentLength, got.TransferEncoding, got.Body)
	}
	if !reflect.DeepEqual(got.Header.Values("Accept"), []string{"application/json"}) {
		t.Fatalf("request %d Accept = %#v, want exactly application/json",
			index, got.Header.Values("Accept"))
	}
	if !reflect.DeepEqual(
		got.Header.Values("vmware-api-session-id"),
		[]string{sessionID},
	) {
		t.Fatalf("request %d session header mismatch: %#v",
			index, got.Header.Values("vmware-api-session-id"))
	}
	for _, forbidden := range []string{"Authorization", "Content-Type"} {
		if values := got.Header.Values(forbidden); len(values) != 0 {
			t.Fatalf("request %d unexpectedly sent %s: %#v", index, forbidden, values)
		}
	}
	allowedHeaders := map[string]bool{
		"Accept":                true,
		"Accept-Encoding":       true,
		"User-Agent":            true,
		"Vmware-Api-Session-Id": true,
	}
	for name := range got.Header {
		if !allowedHeaders[name] {
			t.Fatalf("request %d sent unexpected header %q: %#v",
				index, name, got.Header.Values(name))
		}
	}
}

func fixtureCategories() []map[string]any {
	return []map[string]any{
		categoryObject("cat-z", "Zeta"),
		categoryObject("cat-a2", "Alpha/β"),
		categoryObject("cat-db", "DB + Tier"),
		categoryObject("cat-a1", "Alpha/β"),
		categoryObject("cat-e", "éclair"),
	}
}

func categoryObject(id, name string) map[string]any {
	return map[string]any{
		"category_id": id,
		"info": map[string]any{
			"name":             name,
			"description":      "description for " + id,
			"cardinality":      "MULTIPLE",
			"associable_types": []string{"VirtualMachine", "Datastore"},
			"used_by":          []string{},
		},
	}
}

func category(id, name string) vc.Category {
	return vc.Category{
		CategoryID: id,
		Info: vc.CategoryInfo{
			Name:            name,
			Description:     "description for " + id,
			Cardinality:     "MULTIPLE",
			AssociableTypes: []string{"VirtualMachine", "Datastore"},
			UsedBy:          []string{},
		},
	}
}

func startServer(t *testing.T, plan contractmock.Plan) *contractmock.Server {
	t.Helper()
	server, err := contractmock.New(
		"docs/contract.json",
		filepath.Join(t.TempDir(), "requests.jsonl"),
		plan,
	)
	if err != nil {
		t.Fatalf("start contract mock: %v", err)
	}
	t.Cleanup(server.Close)
	return server
}

func newClient(t *testing.T, server *contractmock.Server) *vc.Client {
	t.Helper()
	client, err := vc.NewClient(vc.Config{
		BaseURL:    server.URL(),
		SessionID:  server.Secrets().SessionID,
		HTTPClient: server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func readRequests(t *testing.T, server *contractmock.Server) []contractmock.Request {
	t.Helper()
	requests, err := server.ReadLog()
	if err != nil {
		t.Fatalf("read request log: %v", err)
	}
	return requests
}

type countingTransport struct{ count atomic.Int64 }

func (t *countingTransport) RoundTrip(*http.Request) (*http.Response, error) {
	t.count.Add(1)
	return nil, errors.New("transport detail must not escape")
}

func (t *countingTransport) Count() int64 { return t.count.Load() }

func assertFileHash(t *testing.T, path, want string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	sum := sha256.Sum256(data)
	got := hex.EncodeToString(sum[:])
	if got != want {
		t.Fatalf("%s SHA-256 = %s, want %s", path, got, want)
	}
}

func readJSON(t *testing.T, path string, destination any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, destination); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
}
