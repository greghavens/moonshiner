package domainsnapshot_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"

	ds "vcf91-0026"
	"vcf91-0026/internal/contractmock"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedSpec   = "specifications/sddc-manager/sddc-manager-openapi.json"
	contractSHA256 = "a8924d45cefd3254345707b346b795d1817012609c76eae47e0b8f1546b44812"
	sourcesSHA256  = "39a94a0f2493322133c0bcdcf3a58445df3431d5f8a50876332128f7c7551688"
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
			SpecPath string `json:"spec_path"`
			Version  string `json:"info_version"`
		} `json:"derived_from"`
		Operations []struct {
			operationSource
			QueryParameters []struct {
				Name string `json:"name"`
			} `json:"query_parameters"`
			Request struct {
				Schema map[string]any `json:"schema"`
			} `json:"request"`
			Responses map[string]struct {
				Schema map[string]any `json:"schema"`
			} `json:"responses"`
		} `json:"operations"`
	}
	readJSON(t, "docs/contract.json", &contract)

	var sources struct {
		Repository struct {
			Commit string `json:"commit_sha"`
		} `json:"repository"`
		Specification struct {
			Path string `json:"path"`
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
	if contract.DerivedFrom.Version != "9.1.0.0" {
		t.Fatalf("wrong SDDC Manager API version: %q", contract.DerivedFrom.Version)
	}

	wantOperations := []operationSource{
		{OperationID: "createToken", Method: "POST", Path: "/v1/tokens"},
		{OperationID: "refreshAccessToken", Method: "PATCH", Path: "/v1/tokens/access-token/refresh"},
		{OperationID: "getDomains", Method: "GET", Path: "/v1/domains"},
	}
	gotContractOperations := make([]operationSource, len(contract.Operations))
	for index, operation := range contract.Operations {
		gotContractOperations[index] = operation.operationSource
	}
	if !reflect.DeepEqual(gotContractOperations, wantOperations) {
		t.Fatalf("contract operations mismatch\n got: %#v\nwant: %#v",
			gotContractOperations, wantOperations)
	}
	if !reflect.DeepEqual(sources.Operations, wantOperations) {
		t.Fatalf("official source operations mismatch\n got: %#v\nwant: %#v",
			sources.Operations, wantOperations)
	}

	wantQueryParameters := []string{
		"type",
		"name",
		"vcFqdn",
		"vcInstanceId",
		"isManagementSsoDomain",
		"pageNumber",
		"pageSize",
		"useCache",
	}
	gotQueryParameters := make([]string, len(contract.Operations[2].QueryParameters))
	for index, parameter := range contract.Operations[2].QueryParameters {
		gotQueryParameters[index] = parameter.Name
	}
	if !reflect.DeepEqual(gotQueryParameters, wantQueryParameters) {
		t.Fatalf("getDomains query projection mismatch\n got: %v\nwant: %v",
			gotQueryParameters, wantQueryParameters)
	}
	refresh := contract.Operations[1]
	if !reflect.DeepEqual(refresh.Request.Schema, map[string]any{
		"type":        "string",
		"description": "ID of the refresh token",
	}) {
		t.Fatalf("refresh request is not the specified JSON string: %#v",
			refresh.Request.Schema)
	}
	if !reflect.DeepEqual(refresh.Responses["200"].Schema, map[string]any{
		"type": "string",
	}) {
		t.Fatalf("refresh response is not the specified JSON string: %#v",
			refresh.Responses["200"].Schema)
	}
}

func TestListDomainsRefreshesOnlyInterruptedPageAndMatchesWire(t *testing.T) {
	fixtureDomains := []map[string]any{
		{
			"id": "domain-0", "name": "Management", "type": "MANAGEMENT",
			"unknownMarker": map[string]any{"ordinal": float64(0)},
		},
		{
			"id": "domain-1", "name": "Compute A", "type": "VI",
			"unknownMarker": map[string]any{"ordinal": float64(1)},
		},
		{
			"id": "domain-2", "name": "Compute B", "type": "VI",
			"unknownMarker": map[string]any{"ordinal": float64(2)},
		},
		{
			"id": "domain-3", "name": "Compute C", "type": "VI",
			"unknownMarker": map[string]any{"ordinal": float64(3)},
		},
		{
			"id": "domain-4", "name": "Edge", "type": "VI",
			"unknownMarker": map[string]any{"ordinal": float64(4)},
		},
	}
	server := contractmock.New(contractmock.Plan{Domains: fixtureDomains})
	t.Cleanup(server.Close)
	secrets := server.Secrets()

	client, err := ds.NewClient(ds.Config{
		BaseURL:    server.URL(),
		Username:   secrets.Username,
		Password:   secrets.Password,
		HTTPClient: server.Client(),
		PageSize:   2,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	got, err := client.ListDomains(context.Background())
	if err != nil {
		t.Fatalf("ListDomains: %v", err)
	}
	want := make([]ds.Domain, len(fixtureDomains))
	for index, domain := range fixtureDomains {
		want[index] = ds.Domain(domain)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("completed work was lost, duplicated, reordered, or altered\n got: %#v\nwant: %#v",
			got, want)
	}

	createBody, _ := json.Marshal(ds.TokenCreationSpec{
		Username: secrets.Username,
		Password: secrets.Password,
	})
	refreshBody, _ := json.Marshal(secrets.RefreshTokenID)
	wantRequests := []wireExpectation{
		{
			operationID: "createToken",
			method:      http.MethodPost,
			path:        "/v1/tokens",
			contentType: "application/json",
			body:        createBody,
		},
		{
			operationID:   "getDomains",
			method:        http.MethodGet,
			path:          "/v1/domains",
			rawQuery:      "pageNumber=0&pageSize=2",
			authorization: "Bearer " + secrets.AccessToken,
		},
		{
			operationID:   "getDomains",
			method:        http.MethodGet,
			path:          "/v1/domains",
			rawQuery:      "pageNumber=1&pageSize=2",
			authorization: "Bearer " + secrets.AccessToken,
		},
		{
			operationID: "refreshAccessToken",
			method:      http.MethodPatch,
			path:        "/v1/tokens/access-token/refresh",
			contentType: "application/json",
			body:        refreshBody,
		},
		{
			operationID:   "getDomains",
			method:        http.MethodGet,
			path:          "/v1/domains",
			rawQuery:      "pageNumber=1&pageSize=2",
			authorization: "Bearer " + secrets.NewAccessToken,
		},
		{
			operationID:   "getDomains",
			method:        http.MethodGet,
			path:          "/v1/domains",
			rawQuery:      "pageNumber=2&pageSize=2",
			authorization: "Bearer " + secrets.NewAccessToken,
		},
	}
	gotRequests := server.Requests()
	if len(gotRequests) != len(wantRequests) {
		t.Fatalf("request count = %d, want %d\nrequests: %#v",
			len(gotRequests), len(wantRequests), gotRequests)
	}
	wantHost := strings.TrimPrefix(server.URL(), "http://")
	for index, wantRequest := range wantRequests {
		assertWireRequest(t, index, gotRequests[index], wantRequest, wantHost)
	}

	var createObject map[string]any
	if err := json.Unmarshal(gotRequests[0].Body, &createObject); err != nil {
		t.Fatalf("createToken body is not JSON: %v", err)
	}
	wantCreateObject := map[string]any{
		"username": secrets.Username,
		"password": secrets.Password,
	}
	if !reflect.DeepEqual(createObject, wantCreateObject) {
		t.Fatalf("createToken body emitted unset optionals or lost required fields\n got: %#v\nwant: %#v",
			createObject, wantCreateObject)
	}
	for _, forbidden := range []string{
		"type=",
		"name=",
		"vcFqdn=",
		"vcInstanceId=",
		"isManagementSsoDomain=",
		"useCache=",
	} {
		for index, request := range gotRequests {
			if strings.Contains(request.RawQuery, forbidden) {
				t.Fatalf("request %d sent unset optional query %q: %q",
					index, forbidden, request.RawQuery)
			}
		}
	}
}

type wireExpectation struct {
	operationID   string
	method        string
	path          string
	rawQuery      string
	authorization string
	contentType   string
	body          []byte
}

func assertWireRequest(
	t *testing.T,
	index int,
	got contractmock.Request,
	want wireExpectation,
	wantHost string,
) {
	t.Helper()
	if got.OperationID != want.operationID ||
		got.Method != want.method ||
		got.Path != want.path ||
		got.RawQuery != want.rawQuery {
		t.Fatalf("request %d target mismatch\n got: %+v\nwant operation=%s method=%s path=%s query=%s",
			index, got, want.operationID, want.method, want.path, want.rawQuery)
	}
	if got.Host != wantHost {
		t.Fatalf("request %d Host = %q, want %q", index, got.Host, wantHost)
	}
	wantContentLength := int64(0)
	if want.body != nil {
		wantContentLength = int64(len(want.body))
	}
	if got.ContentLength != wantContentLength {
		t.Fatalf("request %d Content-Length = %d, want %d",
			index, got.ContentLength, wantContentLength)
	}
	if len(got.TransferEncoding) != 0 {
		t.Fatalf("request %d unexpectedly used Transfer-Encoding: %#v",
			index, got.TransferEncoding)
	}
	if !reflect.DeepEqual(got.Header.Values("Accept"), []string{"application/json"}) {
		t.Fatalf("request %d Accept = %#v, want exactly application/json",
			index, got.Header.Values("Accept"))
	}
	if want.authorization == "" {
		if values := got.Header.Values("Authorization"); len(values) != 0 {
			t.Fatalf("request %d unexpectedly sent Authorization: %#v", index, values)
		}
	} else if !reflect.DeepEqual(got.Header.Values("Authorization"), []string{want.authorization}) {
		t.Fatalf("request %d Authorization = %#v, want exactly %q",
			index, got.Header.Values("Authorization"), want.authorization)
	}
	if want.contentType == "" {
		if values := got.Header.Values("Content-Type"); len(values) != 0 {
			t.Fatalf("request %d unexpectedly sent Content-Type: %#v", index, values)
		}
	} else if !reflect.DeepEqual(got.Header.Values("Content-Type"), []string{want.contentType}) {
		t.Fatalf("request %d Content-Type = %#v, want exactly %q",
			index, got.Header.Values("Content-Type"), want.contentType)
	}
	if !reflect.DeepEqual(got.Body, want.body) {
		t.Fatalf("request %d body = %q, want %q", index, got.Body, want.body)
	}
	allowedHeaders := map[string]bool{
		"Accept":          true,
		"Accept-Encoding": true,
		"Authorization":   true,
		"Content-Length":  true,
		"Content-Type":    true,
		"User-Agent":      true,
	}
	for name := range got.Header {
		if !allowedHeaders[name] {
			t.Fatalf("request %d sent unexpected header %q: %#v",
				index, name, got.Header.Values(name))
		}
	}
}

func TestTokenCreationSpecOptionalPresence(t *testing.T) {
	empty := ""
	tests := []struct {
		name string
		spec ds.TokenCreationSpec
		want string
	}{
		{
			name: "unset optionals omitted",
			spec: ds.TokenCreationSpec{Username: "u", Password: "p"},
			want: `{"username":"u","password":"p"}`,
		},
		{
			name: "present empty optionals retained",
			spec: ds.TokenCreationSpec{
				Username: "u", Password: "p", APIKey: &empty, IDToken: &empty,
			},
			want: `{"username":"u","password":"p","apiKey":"","idToken":""}`,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, err := json.Marshal(test.spec)
			if err != nil {
				t.Fatalf("Marshal: %v", err)
			}
			if string(got) != test.want {
				t.Fatalf("wire body = %s, want %s", got, test.want)
			}
		})
	}
}

func TestExactSuccessStatusesAndOneRefreshBound(t *testing.T) {
	fixtureDomains := []map[string]any{
		{"id": "domain-0"},
		{"id": "domain-1"},
		{"id": "domain-2"},
	}
	tests := []struct {
		name          string
		plan          contractmock.Plan
		wantOperation string
		wantStatus    int
		wantRequests  int
	}{
		{
			name: "createToken other 2xx",
			plan: contractmock.Plan{
				Domains: fixtureDomains, CreateStatus: http.StatusOK,
			},
			wantOperation: "createToken",
			wantStatus:    http.StatusOK,
			wantRequests:  1,
		},
		{
			name: "refreshAccessToken other 2xx",
			plan: contractmock.Plan{
				Domains: fixtureDomains, RefreshStatus: http.StatusCreated,
			},
			wantOperation: "refreshAccessToken",
			wantStatus:    http.StatusCreated,
			wantRequests:  4,
		},
		{
			name: "getDomains other 2xx",
			plan: contractmock.Plan{
				Domains: fixtureDomains, DomainStatus: http.StatusCreated,
			},
			wantOperation: "getDomains",
			wantStatus:    http.StatusCreated,
			wantRequests:  2,
		},
		{
			name: "second 401 is terminal",
			plan: contractmock.Plan{
				Domains: fixtureDomains, RejectRefreshedToken: true,
			},
			wantOperation: "getDomains",
			wantStatus:    http.StatusUnauthorized,
			wantRequests:  5,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.New(test.plan)
			t.Cleanup(server.Close)
			secrets := server.Secrets()
			client, err := ds.NewClient(ds.Config{
				BaseURL: server.URL(), Username: secrets.Username,
				Password: secrets.Password, HTTPClient: server.Client(),
				PageSize: 2,
			})
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			got, err := client.ListDomains(context.Background())
			if len(got) != 0 {
				t.Fatalf("failure returned partial domains: %#v", got)
			}
			var apiError *ds.APIError
			if !errors.As(err, &apiError) {
				t.Fatalf("error = %T %v, want *APIError", err, err)
			}
			if apiError.OperationID != test.wantOperation ||
				apiError.StatusCode != test.wantStatus {
				t.Fatalf("API error = %+v, want operation=%s status=%d",
					apiError, test.wantOperation, test.wantStatus)
			}
			if gotRequests := len(server.Requests()); gotRequests != test.wantRequests {
				t.Fatalf("request count = %d, want %d", gotRequests, test.wantRequests)
			}
			for _, secret := range []string{
				secrets.Username,
				secrets.Password,
				secrets.AccessToken,
				secrets.NewAccessToken,
				secrets.RefreshTokenID,
			} {
				if strings.Contains(err.Error(), secret) {
					t.Fatalf("error text leaked runtime secret: %q", err)
				}
			}
		})
	}
}

func TestMalformedPagesReturnProtocolErrorWithoutPartialData(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(pageNumber int, payload map[string]any)
	}{
		{
			name: "missing metadata",
			mutate: func(pageNumber int, payload map[string]any) {
				if pageNumber == 0 {
					delete(payload, "pageMetadata")
				}
			},
		},
		{
			name: "wrong page number",
			mutate: func(pageNumber int, payload map[string]any) {
				if pageNumber == 0 {
					payload["pageMetadata"].(map[string]any)["pageNumber"] = 7
				}
			},
		},
		{
			name: "short non-final page",
			mutate: func(pageNumber int, payload map[string]any) {
				if pageNumber == 0 {
					payload["elements"] = payload["elements"].([]map[string]any)[:1]
					payload["pageMetadata"].(map[string]any)["pageSize"] = 1
				}
			},
		},
		{
			name: "non-object element",
			mutate: func(pageNumber int, payload map[string]any) {
				if pageNumber == 0 {
					payload["elements"] = []any{
						map[string]any{"id": "valid"},
						"not-an-object",
					}
				}
			},
		},
		{
			name: "totals change after refresh",
			mutate: func(pageNumber int, payload map[string]any) {
				if pageNumber == 1 {
					metadata := payload["pageMetadata"].(map[string]any)
					metadata["totalElements"] = 6
					metadata["totalPages"] = 3
				}
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.New(contractmock.Plan{
				Domains: []map[string]any{
					{"id": "0"}, {"id": "1"}, {"id": "2"}, {"id": "3"}, {"id": "4"},
				},
				MutatePage: test.mutate,
			})
			t.Cleanup(server.Close)
			secrets := server.Secrets()
			client, err := ds.NewClient(ds.Config{
				BaseURL: server.URL(), Username: secrets.Username,
				Password: secrets.Password, HTTPClient: server.Client(),
				PageSize: 2,
			})
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			got, err := client.ListDomains(context.Background())
			if len(got) != 0 {
				t.Fatalf("protocol failure returned partial data: %#v", got)
			}
			var protocolError *ds.ProtocolError
			if !errors.As(err, &protocolError) {
				t.Fatalf("error = %T %v, want *ProtocolError", err, err)
			}
			if protocolError.OperationID != "getDomains" || protocolError.Reason == "" {
				t.Fatalf("ProtocolError lost operation or reason: %+v", protocolError)
			}
		})
	}
}

func TestNewClientValidationIsLocal(t *testing.T) {
	tests := []struct {
		name   string
		config ds.Config
	}{
		{name: "empty origin", config: ds.Config{Username: "u", Password: "p", PageSize: 1}},
		{name: "non HTTP scheme", config: ds.Config{BaseURL: "ftp://127.0.0.1", Username: "u", Password: "p", PageSize: 1}},
		{name: "embedded credentials", config: ds.Config{BaseURL: "http://u:p@127.0.0.1", Username: "u", Password: "p", PageSize: 1}},
		{name: "non-root path", config: ds.Config{BaseURL: "http://127.0.0.1/v1", Username: "u", Password: "p", PageSize: 1}},
		{name: "query", config: ds.Config{BaseURL: "http://127.0.0.1?q=1", Username: "u", Password: "p", PageSize: 1}},
		{name: "fragment", config: ds.Config{BaseURL: "http://127.0.0.1/#f", Username: "u", Password: "p", PageSize: 1}},
		{name: "blank username", config: ds.Config{BaseURL: "http://127.0.0.1", Username: " ", Password: "p", PageSize: 1}},
		{name: "blank password", config: ds.Config{BaseURL: "http://127.0.0.1", Username: "u", Password: "\t", PageSize: 1}},
		{name: "zero page size", config: ds.Config{BaseURL: "http://127.0.0.1", Username: "u", Password: "p"}},
		{name: "negative page size", config: ds.Config{BaseURL: "http://127.0.0.1", Username: "u", Password: "p", PageSize: -1}},
		{name: "page size over int32", config: ds.Config{BaseURL: "http://127.0.0.1", Username: "u", Password: "p", PageSize: int(^uint32(0)>>1) + 1}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := ds.NewClient(test.config); err == nil {
				t.Fatal("NewClient accepted invalid configuration")
			}
		})
	}

	validOrigins := []string{"http://127.0.0.1:1234", "https://example.com/"}
	for _, origin := range validOrigins {
		if _, err := ds.NewClient(ds.Config{
			BaseURL: origin, Username: "u", Password: "p", PageSize: 1,
		}); err != nil {
			t.Fatalf("NewClient rejected valid origin %q: %v", origin, err)
		}
	}
}

func TestContextAndTransportErrorsAreSanitized(t *testing.T) {
	server := contractmock.New(contractmock.Plan{})
	t.Cleanup(server.Close)
	secrets := server.Secrets()

	client, err := ds.NewClient(ds.Config{
		BaseURL: server.URL(), Username: secrets.Username,
		Password: secrets.Password, HTTPClient: server.Client(), PageSize: 2,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err = client.ListDomains(ctx)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled request error = %T %v, want context.Canceled", err, err)
	}

	leak := strings.Join([]string{
		secrets.Username,
		secrets.Password,
		secrets.AccessToken,
		secrets.RefreshTokenID,
	}, " ")
	client, err = ds.NewClient(ds.Config{
		BaseURL: "http://127.0.0.1:1", Username: secrets.Username,
		Password: secrets.Password,
		HTTPClient: &http.Client{Transport: roundTripperFunc(func(*http.Request) (*http.Response, error) {
			return nil, errors.New(leak)
		})},
		PageSize: 2,
	})
	if err != nil {
		t.Fatalf("NewClient with failing transport: %v", err)
	}
	_, err = client.ListDomains(context.Background())
	if err == nil {
		t.Fatal("transport failure returned nil error")
	}
	if strings.Contains(err.Error(), leak) ||
		strings.Contains(err.Error(), secrets.Username) ||
		strings.Contains(err.Error(), secrets.Password) {
		t.Fatalf("transport failure leaked underlying error or credentials: %q", err)
	}
}

type roundTripperFunc func(*http.Request) (*http.Response, error)

func (function roundTripperFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func assertFileHash(t *testing.T, path, want string) {
	t.Helper()
	data := readFile(t, path)
	sum := sha256.Sum256(data)
	if got := hex.EncodeToString(sum[:]); got != want {
		t.Fatalf("%s hash = %s, want protected %s", path, got, want)
	}
}

func readJSON(t *testing.T, path string, destination any) {
	t.Helper()
	if err := json.Unmarshal(readFile(t, path), destination); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func readFile(t *testing.T, path string) []byte {
	t.Helper()
	data, err := osReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return data
}

var osReadFile = os.ReadFile
