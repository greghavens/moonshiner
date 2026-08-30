package domaininventory_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"

	di "vcf91-0035"
	"vcf91-0035/internal/contractmock"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedSpec   = "specifications/sddc-manager/sddc-manager-openapi.json"
	contractSHA256 = "1be31f0a86ef843b18eb9cb722081f53de0c1a77d988259da559f71be4ec5779"
	sourcesSHA256  = "e1cc0aeb15cde4499c94f82d7cbdbda1baccdda4b30355d25552155a426b8446"
)

type operationSource struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
	JSONPointer string `json:"json_pointer"`
}

func TestProtectedContractProvenance(t *testing.T) {
	assertFileHash(t, "docs/contract.json", contractSHA256)
	assertFileHash(t, "docs/official_sources.json", sourcesSHA256)

	var contract struct {
		DerivedFrom struct {
			Commit   string `json:"repository_commit_sha"`
			SpecPath string `json:"spec_path"`
			OpenAPI  string `json:"openapi"`
			Version  string `json:"info_version"`
			License  string `json:"repository_license"`
		} `json:"derived_from"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
			Parameters  []struct {
				Name   string `json:"name"`
				In     string `json:"in"`
				Schema struct {
					Type   string `json:"type"`
					Format string `json:"format"`
				} `json:"schema"`
			} `json:"query_parameters"`
			Responses map[string]struct {
				MediaType string `json:"media_type"`
				SchemaRef string `json:"schema_ref"`
			} `json:"responses"`
		} `json:"operations"`
		Schemas map[string]json.RawMessage `json:"schemas"`
	}
	readJSON(t, "docs/contract.json", &contract)

	var sources struct {
		Repository struct {
			Commit  string `json:"commit_sha"`
			License string `json:"license"`
		} `json:"repository"`
		Specification struct {
			Path    string `json:"path"`
			OpenAPI string `json:"openapi_version"`
			Version string `json:"info_version"`
		} `json:"specification"`
		Operations []operationSource `json:"operations"`
		Derivation string            `json:"derivation"`
	}
	readJSON(t, "docs/official_sources.json", &sources)

	if contract.DerivedFrom.Commit != expectedCommit ||
		sources.Repository.Commit != expectedCommit ||
		contract.DerivedFrom.SpecPath != expectedSpec ||
		sources.Specification.Path != expectedSpec {
		t.Fatalf(
			"wrong pinned specification: contract=%+v sources=%+v",
			contract.DerivedFrom,
			sources,
		)
	}
	if contract.DerivedFrom.OpenAPI != "3.0.1" ||
		sources.Specification.OpenAPI != "3.0.1" ||
		contract.DerivedFrom.Version != "9.1.0.0" ||
		sources.Specification.Version != "9.1.0.0" ||
		contract.DerivedFrom.License != "Apache-2.0" ||
		sources.Repository.License != "Apache-2.0" {
		t.Fatalf(
			"wrong OpenAPI version/product version/license: contract=%+v sources=%+v",
			contract.DerivedFrom,
			sources,
		)
	}
	if !strings.Contains(sources.Derivation, "OpenAPI specification") ||
		!strings.Contains(sources.Derivation, "No rendered documentation page") {
		t.Fatalf("derivation is not explicit: %q", sources.Derivation)
	}

	if len(contract.Operations) != 1 {
		t.Fatalf("contract operations = %d, want 1", len(contract.Operations))
	}
	operation := contract.Operations[0]
	if operation.OperationID != "getDomains" ||
		operation.Method != http.MethodGet ||
		operation.Path != "/v1/domains" {
		t.Fatalf("wrong contract operation: %+v", operation)
	}
	wantSource := []operationSource{{
		OperationID: "getDomains",
		Method:      http.MethodGet,
		Path:        "/v1/domains",
		JSONPointer: "/paths/~1v1~1domains/get",
	}}
	if !reflect.DeepEqual(sources.Operations, wantSource) {
		t.Fatalf(
			"official source operation = %#v, want %#v",
			sources.Operations,
			wantSource,
		)
	}
	parameters := make(map[string][2]string)
	for _, parameter := range operation.Parameters {
		if parameter.In != "query" {
			t.Fatalf("non-query parameter in getDomains: %+v", parameter)
		}
		parameters[parameter.Name] = [2]string{
			parameter.Schema.Type,
			parameter.Schema.Format,
		}
	}
	if parameters["pageNumber"] != [2]string{"integer", "int32"} ||
		parameters["pageSize"] != [2]string{"integer", "int32"} {
		t.Fatalf("pagination query projection mismatch: %#v", parameters)
	}
	if operation.Responses["200"].MediaType != "application/json" ||
		operation.Responses["200"].SchemaRef !=
			"#/components/schemas/PageOfDomain" ||
		operation.Responses["400"].SchemaRef !=
			"#/components/schemas/Error" ||
		operation.Responses["500"].SchemaRef !=
			"#/components/schemas/Error" {
		t.Fatalf("response projection mismatch: %#v", operation.Responses)
	}

	assertSchemaProjection(t, contract.Schemas)
}

func TestListDomainsRetrievesEveryPageAndSortsStable(t *testing.T) {
	server := newServer(t, contractmock.ModeOK)
	runtime := server.Runtime()
	client := newClient(t, server, runtime.AccessToken)

	first, err := client.ListDomains(context.Background())
	if err != nil {
		t.Fatalf("first ListDomains returned %T: %v", err, err)
	}
	second, err := client.ListDomains(context.Background())
	if err != nil {
		t.Fatalf("second ListDomains returned %T: %v", err, err)
	}

	want := make([]di.Domain, len(runtime.Domains))
	for index, domain := range runtime.Domains {
		want[index] = di.Domain{
			ID:     domain.ID,
			Name:   domain.Name,
			Status: domain.Status,
			Type:   domain.Type,
		}
	}
	sort.Slice(want, func(left, right int) bool {
		if want[left].Name != want[right].Name {
			return want[left].Name < want[right].Name
		}
		return want[left].ID < want[right].ID
	})

	if !reflect.DeepEqual(first, want) || !reflect.DeepEqual(second, want) {
		t.Fatalf(
			"complete alternating pages were not normalized\nfirst: %#v\nsecond: %#v\nwant: %#v",
			first,
			second,
			want,
		)
	}
	firstJSON, _ := json.Marshal(first)
	secondJSON, _ := json.Marshal(second)
	if string(firstJSON) != string(secondJSON) {
		t.Fatalf(
			"stable calls differ:\nfirst: %s\nsecond: %s",
			firstJSON,
			secondJSON,
		)
	}

	requests := server.Requests()
	if len(requests) != 6 {
		t.Fatalf("request count = %d, want 6: %#v", len(requests), requests)
	}
	for index, request := range requests {
		wantPage := index % 3
		wantQuery := "pageNumber=" + strconv.Itoa(wantPage) + "&pageSize=2"
		if request.Method != http.MethodGet ||
			request.Path != "/v1/domains" ||
			request.RawQuery != wantQuery {
			t.Errorf(
				"request %d target = %s %s?%s, want GET /v1/domains?%s",
				index,
				request.Method,
				request.Path,
				request.RawQuery,
				wantQuery,
			)
		}
		if request.Header.Get("Accept") != "application/json" ||
			request.Header.Get("Authorization") !=
				"Bearer "+runtime.AccessToken ||
			request.Header.Get("Content-Type") != "" ||
			len(request.Body) != 0 ||
			len(request.TransferEncoding) != 0 {
			t.Errorf("request %d wire shape mismatch: %+v", index, request)
		}
		wantReversed := index%2 == 0
		if request.Reversed != wantReversed {
			t.Errorf(
				"fixture reversal %d = %v, want %v",
				index,
				request.Reversed,
				wantReversed,
			)
		}
	}
}

func TestListDomainsEmptyCollection(t *testing.T) {
	server := newServer(t, contractmock.ModeEmpty)
	client := newClient(t, server, server.Runtime().AccessToken)

	domains, err := client.ListDomains(context.Background())
	if err != nil {
		t.Fatalf("ListDomains: %v", err)
	}
	if domains == nil || len(domains) != 0 {
		t.Fatalf("empty collection = %#v, want non-nil empty slice", domains)
	}
	if got := len(server.Requests()); got != 1 {
		t.Fatalf("empty collection request count = %d, want 1", got)
	}
}

func TestNewClientValidationIsLocalAndTableDriven(t *testing.T) {
	server := newServer(t, contractmock.ModeOK)
	validURL := server.URL()
	cases := []struct {
		name   string
		config di.Config
		wantOK bool
	}{
		{
			name: "valid",
			config: di.Config{
				BaseURL:     validURL,
				AccessToken: "token",
				HTTPClient:  server.Client(),
				PageSize:    2,
			},
			wantOK: true,
		},
		{
			name: "valid trailing slash",
			config: di.Config{
				BaseURL:     validURL + "/",
				AccessToken: "token",
				PageSize:    1,
			},
			wantOK: true,
		},
		{name: "blank URL", config: di.Config{
			AccessToken: "token",
			PageSize:    2,
		}},
		{name: "non HTTP scheme", config: di.Config{
			BaseURL:     "ftp://127.0.0.1",
			AccessToken: "token",
			PageSize:    2,
		}},
		{name: "embedded credentials", config: di.Config{
			BaseURL:     "http://user@127.0.0.1",
			AccessToken: "token",
			PageSize:    2,
		}},
		{name: "non-root path", config: di.Config{
			BaseURL:     validURL + "/sddc",
			AccessToken: "token",
			PageSize:    2,
		}},
		{name: "query", config: di.Config{
			BaseURL:     validURL + "?x=1",
			AccessToken: "token",
			PageSize:    2,
		}},
		{name: "dangling query", config: di.Config{
			BaseURL:     validURL + "?",
			AccessToken: "token",
			PageSize:    2,
		}},
		{name: "fragment", config: di.Config{
			BaseURL:     validURL + "#fragment",
			AccessToken: "token",
			PageSize:    2,
		}},
		{name: "blank token", config: di.Config{
			BaseURL:  validURL,
			PageSize: 2,
		}},
		{name: "whitespace token", config: di.Config{
			BaseURL:     validURL,
			AccessToken: "tok en",
			PageSize:    2,
		}},
		{name: "zero page size", config: di.Config{
			BaseURL:     validURL,
			AccessToken: "token",
		}},
		{name: "negative page size", config: di.Config{
			BaseURL:     validURL,
			AccessToken: "token",
			PageSize:    -1,
		}},
		{name: "page size outside int32", config: di.Config{
			BaseURL:     validURL,
			AccessToken: "token",
			PageSize:    int(math.MaxInt32) + 1,
		}},
	}

	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			client, err := di.NewClient(testCase.config)
			if testCase.wantOK {
				if err != nil || client == nil {
					t.Fatalf("NewClient = (%v, %v), want client", client, err)
				}
				return
			}
			if err == nil || client != nil {
				t.Fatalf("NewClient = (%v, %v), want local error", client, err)
			}
		})
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("NewClient sent %d requests", got)
	}
}

func TestListDomainsRejectsInvalidContextLocally(t *testing.T) {
	server := newServer(t, contractmock.ModeOK)
	client := newClient(t, server, server.Runtime().AccessToken)
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()

	cases := []struct {
		name          string
		client        *di.Client
		ctx           context.Context
		wantCancelled bool
	}{
		{name: "nil context", client: client},
		{
			name:          "cancelled context",
			client:        client,
			ctx:           cancelled,
			wantCancelled: true,
		},
		{name: "nil client", ctx: context.Background()},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := testCase.client.ListDomains(testCase.ctx)
			if err == nil {
				t.Fatal("ListDomains returned nil error")
			}
			if testCase.wantCancelled &&
				!errors.Is(err, context.Canceled) {
				t.Fatalf("error = %v, want context.Canceled", err)
			}
		})
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("invalid contexts sent %d requests", got)
	}
}

func TestProtocolFailuresAreTableDrivenAndStopPagination(t *testing.T) {
	cases := []struct {
		name         string
		mode         contractmock.Mode
		wantRequests int
	}{
		{name: "malformed JSON", mode: contractmock.ModeMalformed, wantRequests: 1},
		{name: "wrong media type", mode: contractmock.ModeWrongMediaType, wantRequests: 1},
		{name: "trailing JSON", mode: contractmock.ModeTrailingJSON, wantRequests: 1},
		{name: "oversized JSON", mode: contractmock.ModeOversized, wantRequests: 1},
		{name: "wrong page number", mode: contractmock.ModeBadPageNumber, wantRequests: 1},
		{name: "wrong page size", mode: contractmock.ModeBadPageSize, wantRequests: 1},
		{name: "negative metadata", mode: contractmock.ModeNegativeMetadata, wantRequests: 1},
		{
			name:         "totals change",
			mode:         contractmock.ModeInconsistentTotals,
			wantRequests: 2,
		},
		{
			name:         "final count mismatch",
			mode:         contractmock.ModeCountMismatch,
			wantRequests: 3,
		},
	}

	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			server := newServer(t, testCase.mode)
			client := newClient(t, server, server.Runtime().AccessToken)
			_, err := client.ListDomains(context.Background())
			var protocolError *di.ProtocolError
			if !errors.As(err, &protocolError) {
				t.Fatalf("error = %T %v, want *ProtocolError", err, err)
			}
			if protocolError.OperationID != "getDomains" {
				t.Fatalf(
					"ProtocolError.OperationID = %q",
					protocolError.OperationID,
				)
			}
			if got := len(server.Requests()); got != testCase.wantRequests {
				t.Fatalf(
					"request count = %d, want %d",
					got,
					testCase.wantRequests,
				)
			}
		})
	}
}

func TestStructuredAPIErrorIsPreservedAndRedacted(t *testing.T) {
	server := newServer(t, contractmock.ModeAPIError)
	runtime := server.Runtime()
	client := newClient(t, server, runtime.AccessToken)

	_, err := client.ListDomains(context.Background())
	var apiError *di.APIError
	if !errors.As(err, &apiError) {
		t.Fatalf("error = %T %v, want *APIError", err, err)
	}
	if apiError.OperationID != "getDomains" ||
		apiError.Status != http.StatusInternalServerError ||
		apiError.ErrorCode != runtime.ErrorCode ||
		apiError.Message != runtime.ErrorMessage ||
		apiError.RemediationMessage != runtime.Remediation ||
		apiError.ReferenceToken != runtime.ReferenceToken {
		t.Fatalf("APIError did not preserve fields: %+v", apiError)
	}
	assertRedacted(
		t,
		err.Error(),
		runtime.AccessToken,
		runtime.ErrorCode,
		runtime.ErrorMessage,
		runtime.Remediation,
		runtime.ReferenceToken,
	)
}

func TestTransportErrorPreservesCauseAndRedactsText(t *testing.T) {
	cause := errors.New("transport-detail-that-must-not-leak")
	token := "transport-token-that-must-not-leak"
	client, err := di.NewClient(di.Config{
		BaseURL:     "http://127.0.0.1:1",
		AccessToken: token,
		PageSize:    2,
		HTTPClient: &http.Client{
			Transport: roundTripperFunc(func(*http.Request) (*http.Response, error) {
				return nil, cause
			}),
		},
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	_, err = client.ListDomains(context.Background())
	var transportError *di.TransportError
	if !errors.As(err, &transportError) {
		t.Fatalf("error = %T %v, want *TransportError", err, err)
	}
	if transportError.OperationID != "getDomains" ||
		!errors.Is(err, cause) {
		t.Fatalf("transport cause was not preserved: %#v", transportError)
	}
	assertRedacted(t, err.Error(), token, cause.Error())
}

func TestRedirectsAreNotFollowed(t *testing.T) {
	server := newServer(t, contractmock.ModeRedirect)
	var redirectCalls atomic.Int32
	baseClient := server.Client()
	baseClient.CheckRedirect = func(
		_ *http.Request,
		_ []*http.Request,
	) error {
		redirectCalls.Add(1)
		return nil
	}
	client, err := di.NewClient(di.Config{
		BaseURL:     server.URL(),
		AccessToken: server.Runtime().AccessToken,
		HTTPClient:  baseClient,
		PageSize:    2,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	_, err = client.ListDomains(context.Background())
	var apiError *di.APIError
	if !errors.As(err, &apiError) ||
		apiError.Status != http.StatusFound {
		t.Fatalf("redirect error = %T %v, want HTTP 302 APIError", err, err)
	}
	if got := redirectCalls.Load(); got != 0 {
		t.Fatalf("caller's redirect hook invoked %d times", got)
	}
	if got := len(server.Requests()); got != 1 {
		t.Fatalf("redirect request count = %d, want 1", got)
	}
}

func assertSchemaProjection(
	t *testing.T,
	schemas map[string]json.RawMessage,
) {
	t.Helper()
	var page struct {
		Properties map[string]struct {
			Type  string `json:"type"`
			Ref   string `json:"$ref"`
			Items struct {
				Ref string `json:"$ref"`
			} `json:"items"`
		} `json:"properties"`
	}
	if err := json.Unmarshal(schemas["PageOfDomain"], &page); err != nil {
		t.Fatalf("decode PageOfDomain: %v", err)
	}
	if page.Properties["elements"].Type != "array" ||
		page.Properties["elements"].Items.Ref !=
			"#/components/schemas/Domain" ||
		page.Properties["pageMetadata"].Ref !=
			"#/components/schemas/PageMetadata" {
		t.Fatalf("PageOfDomain projection mismatch: %+v", page)
	}

	var metadata struct {
		ReadOnly   bool `json:"readOnly"`
		Properties map[string]struct {
			Type        string `json:"type"`
			Format      string `json:"format"`
			ReadOnly    bool   `json:"readOnly"`
			Description string `json:"description"`
		} `json:"properties"`
	}
	if err := json.Unmarshal(schemas["PageMetadata"], &metadata); err != nil {
		t.Fatalf("decode PageMetadata: %v", err)
	}
	for _, name := range []string{
		"pageNumber",
		"pageSize",
		"totalElements",
		"totalPages",
	} {
		property := metadata.Properties[name]
		if property.Type != "integer" ||
			property.Format != "int32" ||
			!property.ReadOnly ||
			property.Description == "" {
			t.Fatalf(
				"PageMetadata.%s projection mismatch: %+v",
				name,
				property,
			)
		}
	}
	if !metadata.ReadOnly {
		t.Fatal("PageMetadata must be readOnly")
	}

	var domain struct {
		Properties map[string]struct {
			Type string `json:"type"`
		} `json:"properties"`
	}
	if err := json.Unmarshal(schemas["Domain"], &domain); err != nil {
		t.Fatalf("decode Domain: %v", err)
	}
	for _, name := range []string{"id", "name", "status", "type"} {
		if domain.Properties[name].Type != "string" {
			t.Fatalf("Domain.%s is not projected as string", name)
		}
	}
}

func newServer(t *testing.T, mode contractmock.Mode) *contractmock.Server {
	t.Helper()
	server, err := contractmock.New(mode)
	if err != nil {
		t.Fatalf("contractmock.New: %v", err)
	}
	t.Cleanup(server.Close)
	if !strings.HasPrefix(server.URL(), "http://127.0.0.1:") {
		t.Fatalf("fixture did not bind IPv4 loopback: %q", server.URL())
	}
	return server
}

func newClient(
	t *testing.T,
	server *contractmock.Server,
	token string,
) *di.Client {
	t.Helper()
	client, err := di.NewClient(di.Config{
		BaseURL:     server.URL(),
		AccessToken: token,
		HTTPClient:  server.Client(),
		PageSize:    server.Runtime().TransportPageSize,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func readJSON(t *testing.T, path string, out any) {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(content, out); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func assertFileHash(t *testing.T, path, want string) {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	sum := sha256.Sum256(content)
	got := hex.EncodeToString(sum[:])
	if got != want {
		t.Fatalf("%s SHA-256 = %s, want %s", path, got, want)
	}
}

func assertRedacted(t *testing.T, text string, secrets ...string) {
	t.Helper()
	for _, secret := range secrets {
		if secret != "" && strings.Contains(text, secret) {
			t.Fatalf("error text leaked %q: %q", secret, text)
		}
	}
}

type roundTripperFunc func(*http.Request) (*http.Response, error)

func (function roundTripperFunc) RoundTrip(
	request *http.Request,
) (*http.Response, error) {
	return function(request)
}

func ExampleClient_ListDomains() {
	fmt.Println("ListDomains retrieves all pages and sorts by name, then id")
	// Output: ListDomains retrieves all pages and sorts by name, then id
}
