// Package verification holds the protected acceptance tests for
// vcfautomation. Every request is served by the contract-pinned loopback mock
// in internal/contractmock. No live VMware endpoint is contacted.
package verification

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"

	"example.com/vcf-automation-deployment-collector/internal/contractmock"
	"example.com/vcf-automation-deployment-collector/vcfautomation"
)

const (
	contractPath        = "../../docs/contract.json"
	officialSourcesPath = "../../docs/official_sources.json"
	operationName       = "Get Deployments"
	operationPath       = "/deployment/api/deployments"
	accessToken         = "eyJhbGciOiJSUzI1NiJ9.vcf-automation-access-token"
	orgID               = "8a71c1e0-6d1c-4d0a-9a4e-6d0f1b2c3d4e"
	projectID           = "3f0b9c2a-51d4-4f79-9d8a-2c7b6a5e4f31"
)

// item is a fixture deployment. Fields left empty are omitted from the JSON
// the mock serves, which exercises the optional response members.
type item struct {
	id          string
	name        string
	status      string
	createdAt   string
	description string
	ownedBy     string
}

func (i item) json() map[string]any {
	body := map[string]any{
		"id":            i.id,
		"name":          i.name,
		"status":        i.status,
		"orgId":         orgID,
		"projectId":     projectID,
		"createdAt":     i.createdAt,
		"blueprintId":   "bp-" + i.id,
		"ownerType":     "USER",
		"deleted":       false,
		"lastUpdatedAt": i.createdAt,
	}
	if i.description != "" {
		body["description"] = i.description
	}
	if i.ownedBy != "" {
		body["ownedBy"] = i.ownedBy
	}
	return body
}

func (i item) deployment() vcfautomation.Deployment {
	lastUpdatedAt := i.createdAt
	deployment := vcfautomation.Deployment{
		ID:            i.id,
		Name:          i.name,
		Status:        i.status,
		OrgID:         orgID,
		ProjectID:     projectID,
		CreatedAt:     i.createdAt,
		LastUpdatedAt: &lastUpdatedAt,
	}
	if i.description != "" {
		description := i.description
		deployment.Description = &description
	}
	if i.ownedBy != "" {
		ownedBy := i.ownedBy
		deployment.OwnedBy = &ownedBy
	}
	return deployment
}

// pageEnvelope builds a PageDeployment body exactly as docs/contract.json
// records it.
func pageEnvelope(number, size, totalElements, totalPages int, content []item) map[string]any {
	elements := make([]any, 0, len(content))
	for _, entry := range content {
		elements = append(elements, entry.json())
	}
	sortObject := map[string]any{"empty": false, "sorted": true, "unsorted": false}
	return map[string]any{
		"content":          elements,
		"empty":            len(elements) == 0,
		"first":            number == 0,
		"last":             number+1 >= totalPages,
		"number":           number,
		"numberOfElements": len(elements),
		"size":             size,
		"sort":             sortObject,
		"totalElements":    totalElements,
		"totalPages":       totalPages,
		"pageable": map[string]any{
			"offset":     number * size,
			"pageNumber": number,
			"pageSize":   size,
			"paged":      true,
			"sort":       sortObject,
			"unpaged":    false,
		},
	}
}

// mutator lets a scenario corrupt one page envelope before it is served.
type mutator func(pageIndex int, envelope map[string]any) any

// newServer starts the contract-pinned mock serving pages in order.
func newServer(t testing.TB, pages [][]item, mutate mutator) *contractmock.Server {
	t.Helper()
	total := 0
	for _, page := range pages {
		total += len(page)
	}
	return contractmock.New(t, contractPath, func(matched string, request contractmock.Request) contractmock.Response {
		if matched != operationName {
			t.Errorf("mock matched unexpected operation %q", matched)
		}
		number := queryInt(t, request, "page", 0)
		size := queryInt(t, request, "size", 20)
		var content []item
		if number >= 0 && number < len(pages) {
			content = pages[number]
		}
		envelope := pageEnvelope(number, size, total, len(pages), content)
		var body any = envelope
		if mutate != nil {
			body = mutate(number, envelope)
		}
		if raw, ok := body.([]byte); ok {
			return contractmock.Response{Status: http.StatusOK, ContentType: "application/json", Body: raw}
		}
		return contractmock.JSONResponse(t, http.StatusOK, body)
	})
}

func queryInt(t testing.TB, request contractmock.Request, name string, fallback int) int {
	t.Helper()
	raw := request.Query.Get(name)
	if raw == "" {
		return fallback
	}
	value, err := strconv.Atoi(raw)
	if err != nil {
		t.Errorf("query parameter %s=%q is not an integer: %v", name, raw, err)
		return fallback
	}
	return value
}

func newClient(t testing.TB, baseURL string) *vcfautomation.Client {
	t.Helper()
	client, err := vcfautomation.NewClient(baseURL, accessToken, nil)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if client == nil {
		t.Fatal("NewClient returned a nil client and a nil error")
	}
	return client
}

func stringPointer(value string) *string { return &value }
func boolPointer(value bool) *bool       { return &value }

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

// expectedOrder is the required deterministic order: createdAt descending,
// ties broken by id ascending, one entry per id.
func expectedOrder(items []item) []vcfautomation.Deployment {
	seen := make(map[string]bool, len(items))
	unique := make([]item, 0, len(items))
	for _, entry := range items {
		if seen[entry.id] {
			continue
		}
		seen[entry.id] = true
		unique = append(unique, entry)
	}
	sort.SliceStable(unique, func(i, j int) bool {
		if unique[i].createdAt != unique[j].createdAt {
			return unique[i].createdAt > unique[j].createdAt
		}
		return unique[i].id < unique[j].id
	})
	deployments := make([]vcfautomation.Deployment, 0, len(unique))
	for _, entry := range unique {
		deployments = append(deployments, entry.deployment())
	}
	return deployments
}

func flatten(pages [][]item) []item {
	var all []item
	for _, page := range pages {
		all = append(all, page...)
	}
	return all
}

func assertDeployments(t *testing.T, got, want []vcfautomation.Deployment) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("got %d deployments, want %d\ngot:  %s\nwant: %s", len(got), len(want), describe(got), describe(want))
	}
	for i := range want {
		if !reflect.DeepEqual(got[i], want[i]) {
			t.Fatalf("deployment %d mismatch\ngot:  %s\nwant: %s", i, describeOne(got[i]), describeOne(want[i]))
		}
	}
}

func describe(deployments []vcfautomation.Deployment) string {
	parts := make([]string, 0, len(deployments))
	for _, deployment := range deployments {
		parts = append(parts, describeOne(deployment))
	}
	return "[" + strings.Join(parts, " ") + "]"
}

func describeOne(deployment vcfautomation.Deployment) string {
	optional := func(value *string) string {
		if value == nil {
			return "<nil>"
		}
		return strconv.Quote(*value)
	}
	return fmt.Sprintf("{id:%s createdAt:%s status:%s description:%s ownedBy:%s lastUpdatedAt:%s}",
		deployment.ID, deployment.CreatedAt, deployment.Status,
		optional(deployment.Description), optional(deployment.OwnedBy), optional(deployment.LastUpdatedAt))
}

// --- provenance -------------------------------------------------------------

func TestContractDeclaresReferenceDerivedSource(t *testing.T) {
	var contract struct {
		SourceBasis struct {
			Kind                     string `json:"kind"`
			IsPublishedSpecification bool   `json:"isPublishedSpecification"`
			Statement                string `json:"statement"`
		} `json:"sourceBasis"`
		Operations []struct {
			OperationName string `json:"operationName"`
			Method        string `json:"method"`
			Path          string `json:"path"`
		} `json:"operations"`
	}
	readJSON(t, contractPath, &contract)

	if contract.SourceBasis.Kind != "reference-documentation" {
		t.Errorf("sourceBasis.kind = %q, want %q", contract.SourceBasis.Kind, "reference-documentation")
	}
	if contract.SourceBasis.IsPublishedSpecification {
		t.Error("sourceBasis.isPublishedSpecification must be false: VCF Automation has no published specification")
	}
	statement := strings.ToLower(contract.SourceBasis.Statement)
	for _, phrase := range []string{"reference documentation", "not a published specification"} {
		if !strings.Contains(statement, phrase) {
			t.Errorf("sourceBasis.statement must say %q; got %q", phrase, contract.SourceBasis.Statement)
		}
	}
	if len(contract.Operations) != 1 || contract.Operations[0].OperationName != operationName {
		t.Fatalf("contract must name exactly the %q operation, got %+v", operationName, contract.Operations)
	}
	if contract.Operations[0].Method != http.MethodGet || contract.Operations[0].Path != operationPath {
		t.Errorf("contract operation = %s %s, want GET %s",
			contract.Operations[0].Method, contract.Operations[0].Path, operationPath)
	}
}

func TestOfficialSourcesRecordEveryReferencePage(t *testing.T) {
	var sources struct {
		SourceKind string `json:"sourceKind"`
		Pages      []struct {
			URL         string `json:"url"`
			Operation   string `json:"operation"`
			DateFetched string `json:"dateFetched"`
			Documents   string `json:"documents"`
		} `json:"pages"`
	}
	readJSON(t, officialSourcesPath, &sources)

	if sources.SourceKind != "reference-documentation" {
		t.Errorf("sourceKind = %q, want %q", sources.SourceKind, "reference-documentation")
	}
	if len(sources.Pages) == 0 {
		t.Fatal("official_sources.json records no pages")
	}
	date := regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)
	operationCovered := false
	for _, page := range sources.Pages {
		if !strings.HasPrefix(page.URL, "https://developer.broadcom.com/") {
			t.Errorf("page URL %q is not on the authoritative Broadcom developer portal", page.URL)
		}
		if page.Operation == "" {
			t.Errorf("page %q does not record the operation it documents", page.URL)
		}
		if page.Documents == "" {
			t.Errorf("page %q does not record what it contributed to the contract", page.URL)
		}
		if !date.MatchString(page.DateFetched) {
			t.Errorf("page %q has dateFetched %q, want YYYY-MM-DD", page.URL, page.DateFetched)
		}
		if page.Operation == operationName {
			operationCovered = true
		}
	}
	if !operationCovered {
		t.Errorf("no recorded page documents the %q operation", operationName)
	}
}

func readJSON(t *testing.T, path string, target any) {
	t.Helper()
	data, err := os.ReadFile(filepath.Clean(path))
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

// --- mock pinning -----------------------------------------------------------

func TestMockServesOnlyTheContractOperation(t *testing.T) {
	server := newServer(t, [][]item{{{id: "a", name: "a", status: "CREATE_SUCCESSFUL", createdAt: "2026-05-01T00:00:00.000Z"}}}, nil)

	if operations := server.Operations(); len(operations) != 1 || operations[0] != operationName {
		t.Fatalf("mock loaded operations %v, want [%q]", operations, operationName)
	}
	method, path, ok := server.Route(operationName)
	if !ok || method != http.MethodGet || path != operationPath {
		t.Fatalf("mock route = %s %s (found=%v), want GET %s", method, path, ok, operationPath)
	}

	cases := []struct {
		name       string
		method     string
		target     string
		omitAuth   bool
		omitAccept bool
		wantStatus int
	}{
		{"contract operation", http.MethodGet, operationPath + "?page=0&size=5", false, false, http.StatusOK},
		{"operation not in the contract", http.MethodGet, "/catalog/api/items?page=0", false, false, http.StatusNotFound},
		{"path not in the contract", http.MethodGet, operationPath + "/filters", false, false, http.StatusNotFound},
		{"method not in the contract", http.MethodPost, operationPath, false, false, http.StatusMethodNotAllowed},
		{"undocumented query parameter", http.MethodGet, operationPath + "?pageNumber=0", false, false, http.StatusBadRequest},
		{"optional parameter sent empty", http.MethodGet, operationPath + "?page=0&size=5&search=", false, false, http.StatusBadRequest},
		{"parameter sent twice", http.MethodGet, operationPath + "?page=0&page=1", false, false, http.StatusBadRequest},
		{"missing bearer credentials", http.MethodGet, operationPath + "?page=0", true, false, http.StatusUnauthorized},
		{"missing json accept", http.MethodGet, operationPath + "?page=0", false, true, http.StatusNotAcceptable},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			request, err := http.NewRequest(testCase.method, server.URL()+testCase.target, nil)
			if err != nil {
				t.Fatalf("build probe: %v", err)
			}
			if !testCase.omitAuth {
				request.Header.Set("Authorization", "Bearer "+accessToken)
			}
			if !testCase.omitAccept {
				request.Header.Set("Accept", "application/json")
			}
			response, err := http.DefaultClient.Do(request)
			if err != nil {
				t.Fatalf("probe: %v", err)
			}
			defer response.Body.Close()
			_, _ = io.Copy(io.Discard, response.Body)
			if response.StatusCode != testCase.wantStatus {
				t.Errorf("status = %d, want %d", response.StatusCode, testCase.wantStatus)
			}
		})
	}
}

// --- collection completeness ------------------------------------------------

func TestListAllDeploymentsRetrievesEveryPage(t *testing.T) {
	cases := []struct {
		name         string
		pageSize     int
		pages        [][]item
		wantRequests int
	}{
		{
			name:     "single partial page with no invented maximum size",
			pageSize: 1_000_000,
			pages: [][]item{{
				{id: "d-02", name: "beta", status: "CREATE_SUCCESSFUL", createdAt: "2026-04-02T10:00:00.000Z"},
				{id: "d-01", name: "alpha", status: "UPDATE_FAILED", createdAt: "2026-04-01T10:00:00.000Z", description: "first"},
			}},
			wantRequests: 1,
		},
		{
			name:     "three pages, final page partial",
			pageSize: 2,
			pages: [][]item{
				{
					{id: "d-06", name: "zeta", status: "CREATE_SUCCESSFUL", createdAt: "2026-04-06T10:00:00.000Z"},
					{id: "d-05", name: "epsilon", status: "CREATE_INPROGRESS", createdAt: "2026-04-05T10:00:00.000Z", ownedBy: "svc-automation"},
				},
				{
					{id: "d-04", name: "delta", status: "CREATE_SUCCESSFUL", createdAt: "2026-04-04T10:00:00.000Z", description: "fourth"},
					{id: "d-03", name: "gamma", status: "UPDATE_SUCCESSFUL", createdAt: "2026-04-03T10:00:00.000Z"},
				},
				{
					{id: "d-02", name: "beta", status: "CREATE_FAILED", createdAt: "2026-04-02T10:00:00.000Z"},
				},
			},
			wantRequests: 3,
		},
		{
			name:     "final page exactly fills the page size",
			pageSize: 2,
			pages: [][]item{
				{
					{id: "d-04", name: "delta", status: "CREATE_SUCCESSFUL", createdAt: "2026-04-04T10:00:00.000Z"},
					{id: "d-03", name: "gamma", status: "CREATE_SUCCESSFUL", createdAt: "2026-04-03T10:00:00.000Z"},
				},
				{
					{id: "d-02", name: "beta", status: "CREATE_SUCCESSFUL", createdAt: "2026-04-02T10:00:00.000Z"},
					{id: "d-01", name: "alpha", status: "CREATE_SUCCESSFUL", createdAt: "2026-04-01T10:00:00.000Z"},
				},
			},
			wantRequests: 2,
		},
		{
			name:         "empty collection",
			pageSize:     20,
			pages:        [][]item{{}},
			wantRequests: 1,
		},
	}

	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			server := newServer(t, testCase.pages, nil)
			client := newClient(t, server.URL())

			got, err := client.ListAllDeployments(context.Background(), vcfautomation.ListDeploymentsOptions{PageSize: testCase.pageSize})
			if err != nil {
				t.Fatalf("ListAllDeployments: %v", err)
			}
			assertDeployments(t, got, expectedOrder(flatten(testCase.pages)))

			requests := server.Requests()
			if len(requests) != testCase.wantRequests {
				t.Fatalf("made %d requests, want %d", len(requests), testCase.wantRequests)
			}
			for i, request := range requests {
				if got := request.Query.Get("page"); got != strconv.Itoa(i) {
					t.Errorf("request %d asked for page %q, want %q", i, got, strconv.Itoa(i))
				}
				if got := request.Query.Get("size"); got != strconv.Itoa(testCase.pageSize) {
					t.Errorf("request %d asked for size %q, want %q", i, got, strconv.Itoa(testCase.pageSize))
				}
			}
		})
	}
}

func TestListAllDeploymentsReturnsDeterministicOrder(t *testing.T) {
	pages := [][]item{
		{
			{id: "d-c", name: "gamma", status: "CREATE_SUCCESSFUL", createdAt: "2026-03-02T08:00:00.000Z"},
			{id: "d-a", name: "alpha", status: "CREATE_SUCCESSFUL", createdAt: "2026-03-04T08:00:00.000Z", description: "newest"},
		},
		{
			{id: "d-e", name: "epsilon", status: "UPDATE_FAILED", createdAt: "2026-03-02T08:00:00.000Z"},
			{id: "d-b", name: "beta", status: "CREATE_SUCCESSFUL", createdAt: "2026-03-03T08:00:00.000Z", ownedBy: "provider@vcf.local"},
		},
		{
			{id: "d-d", name: "delta", status: "CREATE_INPROGRESS", createdAt: "2026-03-01T08:00:00.000Z"},
		},
	}
	want := []string{"d-a", "d-b", "d-c", "d-e", "d-d"}

	server := newServer(t, pages, nil)
	client := newClient(t, server.URL())

	for attempt := 0; attempt < 3; attempt++ {
		got, err := client.ListAllDeployments(context.Background(), vcfautomation.ListDeploymentsOptions{PageSize: 2})
		if err != nil {
			t.Fatalf("attempt %d: ListAllDeployments: %v", attempt, err)
		}
		assertDeployments(t, got, expectedOrder(flatten(pages)))
		ids := make([]string, 0, len(got))
		for _, deployment := range got {
			ids = append(ids, deployment.ID)
		}
		if !reflect.DeepEqual(ids, want) {
			t.Fatalf("attempt %d: order = %v, want %v (createdAt descending, ties by id ascending)", attempt, ids, want)
		}
	}
}

func TestListAllDeploymentsCollapsesIDsRepeatedAcrossPages(t *testing.T) {
	first := item{
		id:          "d-shift",
		name:        "first occurrence",
		status:      "CREATE_SUCCESSFUL",
		createdAt:   "2026-02-02T09:00:00.000Z",
		description: "keep this representation",
	}
	later := item{
		id:          "d-shift",
		name:        "later occurrence",
		status:      "UPDATE_FAILED",
		createdAt:   "2026-12-31T09:00:00.000Z",
		description: "discard this representation",
	}
	pages := [][]item{
		{
			{id: "d-top", name: "top", status: "CREATE_SUCCESSFUL", createdAt: "2026-02-03T09:00:00.000Z"},
			first,
		},
		{
			later,
			{id: "d-tail", name: "tail", status: "CREATE_SUCCESSFUL", createdAt: "2026-02-01T09:00:00.000Z"},
		},
	}

	server := newServer(t, pages, nil)
	client := newClient(t, server.URL())

	got, err := client.ListAllDeployments(context.Background(), vcfautomation.ListDeploymentsOptions{PageSize: 2})
	if err != nil {
		t.Fatalf("ListAllDeployments: %v", err)
	}
	assertDeployments(t, got, expectedOrder(flatten(pages)))
	if len(got) != 3 {
		t.Fatalf("got %d deployments, want 3 distinct ids", len(got))
	}
	for _, deployment := range got {
		if deployment.ID == first.id && deployment.Name != first.name {
			t.Fatalf("duplicate id retained %q, want first occurrence %q", deployment.Name, first.name)
		}
	}
}

func TestListAllDeploymentsDoesNotInventAPageBodyLimit(t *testing.T) {
	// The protected contract documents no maximum PageDeployment body size.
	largeDescription := strings.Repeat("x", (1<<24)+1024)
	entry := item{
		id:          "d-large",
		name:        "large documented deployment",
		status:      "CREATE_SUCCESSFUL",
		createdAt:   "2026-02-01T09:00:00.000Z",
		description: largeDescription,
	}
	server := newServer(t, [][]item{{entry}}, nil)
	client := newClient(t, server.URL())

	got, err := client.ListAllDeployments(context.Background(), vcfautomation.ListDeploymentsOptions{PageSize: 1})
	if err != nil {
		t.Fatalf("ListAllDeployments rejected a contract-valid page: %v", err)
	}
	if len(got) != 1 || got[0].ID != entry.id {
		t.Fatalf("got %d deployments with ids %v, want [%s]", len(got), deploymentIDs(got), entry.id)
	}
	if got[0].Description == nil || *got[0].Description != largeDescription {
		gotLength := -1
		if got[0].Description != nil {
			gotLength = len(*got[0].Description)
		}
		t.Fatalf("large description length = %d, want %d", gotLength, len(largeDescription))
	}
}

func deploymentIDs(deployments []vcfautomation.Deployment) []string {
	ids := make([]string, len(deployments))
	for i := range deployments {
		ids[i] = deployments[i].ID
	}
	return ids
}

// --- request wire shape -----------------------------------------------------

func TestRequestWireShape(t *testing.T) {
	pages := [][]item{
		{{id: "d-2", name: "two", status: "CREATE_SUCCESSFUL", createdAt: "2026-01-02T00:00:00.000Z"}},
		{{id: "d-1", name: "one", status: "CREATE_SUCCESSFUL", createdAt: "2026-01-01T00:00:00.000Z"}},
	}

	cases := []struct {
		name           string
		baseURLSuffix  string
		options        vcfautomation.ListDeploymentsOptions
		wantQueryPages []map[string]string
	}{
		{
			name:    "no optional filter is set",
			options: vcfautomation.ListDeploymentsOptions{PageSize: 1},
			wantQueryPages: []map[string]string{
				{"page": "0", "size": "1"},
				{"page": "1", "size": "1"},
			},
		},
		{
			name:          "base URL carries a trailing slash",
			baseURLSuffix: "/",
			options:       vcfautomation.ListDeploymentsOptions{PageSize: 1},
			wantQueryPages: []map[string]string{
				{"page": "0", "size": "1"},
				{"page": "1", "size": "1"},
			},
		},
		{
			name: "every supported filter is set",
			options: vcfautomation.ListDeploymentsOptions{
				PageSize:      1,
				Sort:          stringPointer("createdAt,DESC"),
				Search:        stringPointer("payments tier"),
				Name:          stringPointer("payments-prod"),
				Status:        []string{"CREATE_SUCCESSFUL", "UPDATE_FAILED"},
				Projects:      []string{projectID},
				ResourceTypes: []string{"Cloud.vSphere.Machine", "Cloud.NSX.Network"},
				OwnedBy:       []string{"provider@vcf.local"},
				Deleted:       boolPointer(true),
			},
			wantQueryPages: []map[string]string{
				{
					"page": "0", "size": "1",
					"sort": "createdAt,DESC", "search": "payments tier", "name": "payments-prod",
					"status": "CREATE_SUCCESSFUL,UPDATE_FAILED", "projects": projectID,
					"resourceTypes": "Cloud.vSphere.Machine,Cloud.NSX.Network",
					"ownedBy":       "provider@vcf.local", "deleted": "true",
				},
				{
					"page": "1", "size": "1",
					"sort": "createdAt,DESC", "search": "payments tier", "name": "payments-prod",
					"status": "CREATE_SUCCESSFUL,UPDATE_FAILED", "projects": projectID,
					"resourceTypes": "Cloud.vSphere.Machine,Cloud.NSX.Network",
					"ownedBy":       "provider@vcf.local", "deleted": "true",
				},
			},
		},
		{
			name: "an explicit false boolean is sent, unset filters are not",
			options: vcfautomation.ListDeploymentsOptions{
				PageSize: 1,
				Deleted:  boolPointer(false),
			},
			wantQueryPages: []map[string]string{
				{"page": "0", "size": "1", "deleted": "false"},
				{"page": "1", "size": "1", "deleted": "false"},
			},
		},
		{
			name: "empty slices and empty strings are omitted, not sent empty",
			options: vcfautomation.ListDeploymentsOptions{
				PageSize:      1,
				Search:        stringPointer(""),
				Name:          stringPointer(""),
				Sort:          stringPointer(""),
				Status:        []string{},
				Projects:      nil,
				ResourceTypes: []string{},
				OwnedBy:       []string{},
			},
			wantQueryPages: []map[string]string{
				{"page": "0", "size": "1"},
				{"page": "1", "size": "1"},
			},
		},
	}

	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			server := newServer(t, pages, nil)
			client := newClient(t, server.URL()+testCase.baseURLSuffix)

			if _, err := client.ListAllDeployments(context.Background(), testCase.options); err != nil {
				t.Fatalf("ListAllDeployments: %v", err)
			}

			requests := server.Requests()
			if len(requests) != len(testCase.wantQueryPages) {
				t.Fatalf("made %d requests, want %d", len(requests), len(testCase.wantQueryPages))
			}
			for i, request := range requests {
				want := testCase.wantQueryPages[i]

				if request.Method != http.MethodGet {
					t.Errorf("request %d method = %s, want GET", i, request.Method)
				}
				if request.Path != operationPath {
					t.Errorf("request %d path = %q, want %q", i, request.Path, operationPath)
				}
				if len(request.Body) != 0 {
					t.Errorf("request %d carried a %d byte body; Get Deployments takes no request body", i, len(request.Body))
				}
				if request.ContentLength > 0 {
					t.Errorf("request %d declared Content-Length %d, want none", i, request.ContentLength)
				}
				if len(request.TransferEncoding) != 0 {
					t.Errorf("request %d used Transfer-Encoding %v, want none", i, request.TransferEncoding)
				}
				if request.Header.Get("Content-Type") != "" {
					t.Errorf("request %d set Content-Type %q on a bodyless GET", i, request.Header.Get("Content-Type"))
				}
				if got := request.Header.Get("Authorization"); got != "Bearer "+accessToken {
					t.Errorf("request %d Authorization = %q, want %q", i, got, "Bearer "+accessToken)
				}
				if got := request.Header.Get("Accept"); !strings.Contains(got, "application/json") {
					t.Errorf("request %d Accept = %q, want it to accept application/json", i, got)
				}

				wantKeys := make([]string, 0, len(want))
				for key := range want {
					wantKeys = append(wantKeys, key)
				}
				sort.Strings(wantKeys)
				if gotKeys := request.QueryKeys(); !reflect.DeepEqual(gotKeys, wantKeys) {
					t.Errorf("request %d query keys = %v, want exactly %v (raw query %q)", i, gotKeys, wantKeys, request.RawQuery)
				}
				for key, wantValue := range want {
					values := request.Query[key]
					if len(values) != 1 {
						t.Errorf("request %d sent %s %d times, want once", i, key, len(values))
						continue
					}
					if values[0] != wantValue {
						t.Errorf("request %d %s = %q, want %q", i, key, values[0], wantValue)
					}
				}
				for key, values := range request.Query {
					for _, value := range values {
						if value == "" {
							t.Errorf("request %d sent %s with an empty value; an unset optional parameter must be omitted (raw query %q)", i, key, request.RawQuery)
						}
					}
				}
				for _, fragment := range strings.Split(request.RawQuery, "&") {
					if fragment == "" {
						continue
					}
					if !strings.Contains(fragment, "=") || strings.HasSuffix(fragment, "=") {
						t.Errorf("request %d raw query fragment %q is a bare or empty parameter", i, fragment)
					}
				}
			}
		})
	}
}

// --- failure handling -------------------------------------------------------

func TestNonSuccessResponseStopsCollection(t *testing.T) {
	cases := []struct {
		name   string
		status int
		body   string
	}{
		{"documented 401", http.StatusUnauthorized, `{"message":"token expired"}`},
		{"other 4xx or 5xx", http.StatusServiceUnavailable, "temporarily unavailable\n"},
		{"non-200 success status", http.StatusCreated, `{"message":"unexpected status"}`},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			server := contractmock.New(t, contractPath, func(_ string, request contractmock.Request) contractmock.Response {
				if request.Query.Get("page") == "0" {
					return contractmock.JSONResponse(t, http.StatusOK, pageEnvelope(0, 1, 2, 2, []item{
						{id: "d-1", name: "one", status: "CREATE_SUCCESSFUL", createdAt: "2026-01-01T00:00:00.000Z"},
					}))
				}
				return contractmock.Response{
					Status:      testCase.status,
					ContentType: "application/json",
					Body:        []byte(testCase.body),
				}
			})
			client := newClient(t, server.URL())

			got, err := client.ListAllDeployments(context.Background(), vcfautomation.ListDeploymentsOptions{PageSize: 1})
			if err == nil {
				t.Fatalf("ListAllDeployments returned %d deployments and no error, want an *APIError", len(got))
			}
			var apiErr *vcfautomation.APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("error is %T (%v), want *vcfautomation.APIError", err, err)
			}
			if apiErr.StatusCode != testCase.status {
				t.Errorf("APIError.StatusCode = %d, want %d", apiErr.StatusCode, testCase.status)
			}
			if apiErr.Body != testCase.body {
				t.Errorf("APIError.Body = %q, want exact raw body %q", apiErr.Body, testCase.body)
			}
			if requests := server.Requests(); len(requests) != 2 {
				t.Errorf("made %d requests, want 2: the client must stop at the failing page", len(requests))
			}
		})
	}
}

func TestIncoherentSuccessResponseIsAProtocolError(t *testing.T) {
	pages := [][]item{
		{{id: "d-2", name: "two", status: "CREATE_SUCCESSFUL", createdAt: "2026-01-02T00:00:00.000Z"}},
		{{id: "d-1", name: "one", status: "CREATE_SUCCESSFUL", createdAt: "2026-01-01T00:00:00.000Z"}},
	}

	cases := []struct {
		name         string
		mutate       mutator
		wantRequests int
	}{
		{
			name:         "page number does not echo the requested page",
			wantRequests: 1,
			mutate: func(pageIndex int, envelope map[string]any) any {
				envelope["number"] = pageIndex + 7
				return envelope
			},
		},
		{
			name:         "numberOfElements disagrees with content",
			wantRequests: 1,
			mutate: func(_ int, envelope map[string]any) any {
				envelope["numberOfElements"] = 99
				return envelope
			},
		},
		{
			name:         "last is false on the final page",
			wantRequests: 2,
			mutate: func(pageIndex int, envelope map[string]any) any {
				if pageIndex == len(pages)-1 {
					envelope["last"] = false
				}
				return envelope
			},
		},
		{
			name:         "body is not decodable",
			wantRequests: 2,
			mutate: func(pageIndex int, envelope map[string]any) any {
				if pageIndex == 1 {
					return []byte(`{"content":[`)
				}
				return envelope
			},
		},
	}

	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			server := newServer(t, pages, testCase.mutate)
			client := newClient(t, server.URL())

			got, err := client.ListAllDeployments(context.Background(), vcfautomation.ListDeploymentsOptions{PageSize: 1})
			if err == nil {
				t.Fatalf("ListAllDeployments returned %d deployments and no error, want a *ProtocolError", len(got))
			}
			var protocolErr *vcfautomation.ProtocolError
			if !errors.As(err, &protocolErr) {
				t.Fatalf("error is %T (%v), want *vcfautomation.ProtocolError", err, err)
			}
			if protocolErr.Reason == "" {
				t.Error("ProtocolError.Reason is empty")
			}
			if requests := server.Requests(); len(requests) != testCase.wantRequests {
				t.Errorf("made %d requests, want %d: collection must stop at the incoherent page", len(requests), testCase.wantRequests)
			}
		})
	}
}

func TestNewClientRejectsUnusableConfiguration(t *testing.T) {
	cases := []struct {
		name        string
		baseURL     string
		accessToken string
	}{
		{"empty base URL", "", accessToken},
		{"empty access token", "http://127.0.0.1:8080", ""},
		{"base URL without a scheme", "127.0.0.1:8080", accessToken},
		{"relative base URL", "/vcf", accessToken},
		{"unsupported base URL scheme", "ftp://127.0.0.1/vcf", accessToken},
		{"http base URL without a host", "http:///vcf", accessToken},
		{"http base URL with only a port", "http://:8080", accessToken},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			client, err := vcfautomation.NewClient(testCase.baseURL, testCase.accessToken, nil)
			if err == nil {
				t.Fatalf("NewClient(%q, %q) = %v, nil; want an error", testCase.baseURL, testCase.accessToken, client)
			}
		})
	}
}

func TestNewClientUsesProvidedHTTPClient(t *testing.T) {
	body, err := json.Marshal(pageEnvelope(0, 1, 1, 1, []item{{
		id: "d-custom-client", name: "custom", status: "CREATE_SUCCESSFUL", createdAt: "2026-01-01T00:00:00.000Z",
	}}))
	if err != nil {
		t.Fatalf("marshal response: %v", err)
	}
	for _, baseURL := range []string{"http://127.0.0.1:1", "https://127.0.0.1:1"} {
		t.Run(baseURL, func(t *testing.T) {
			called := false
			transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
				called = true
				return &http.Response{
					StatusCode: http.StatusOK,
					Status:     "200 OK",
					Header:     http.Header{"Content-Type": []string{"application/json"}},
					Body:       io.NopCloser(strings.NewReader(string(body))),
					Request:    request,
				}, nil
			})
			client, err := vcfautomation.NewClient(baseURL, accessToken, &http.Client{Transport: transport})
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			if _, err := client.ListAllDeployments(context.Background(), vcfautomation.ListDeploymentsOptions{PageSize: 1}); err != nil {
				t.Fatalf("ListAllDeployments: %v", err)
			}
			if !called {
				t.Fatal("the provided http.Client was not used")
			}
		})
	}
}

func TestTransportFailureIsWrapped(t *testing.T) {
	want := errors.New("transport failed")
	transport := roundTripFunc(func(*http.Request) (*http.Response, error) {
		return nil, want
	})
	client, err := vcfautomation.NewClient("https://127.0.0.1:1", accessToken, &http.Client{Transport: transport})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if _, err := client.ListAllDeployments(context.Background(), vcfautomation.ListDeploymentsOptions{PageSize: 1}); !errors.Is(err, want) {
		t.Fatalf("error = %v, want it to wrap the transport failure", err)
	}
}

func TestListAllDeploymentsRejectsInvalidPageSize(t *testing.T) {
	server := newServer(t, [][]item{{}}, nil)
	client := newClient(t, server.URL())

	for _, pageSize := range []int{0, -1, -42} {
		if _, err := client.ListAllDeployments(context.Background(), vcfautomation.ListDeploymentsOptions{PageSize: pageSize}); err == nil {
			t.Errorf("PageSize %d was accepted; the contract records a minimum of 1 for size", pageSize)
		}
	}
	if requests := server.Requests(); len(requests) != 0 {
		t.Errorf("made %d requests for an invalid page size, want 0", len(requests))
	}
}

func TestCancelledContextStopsCollection(t *testing.T) {
	pages := [][]item{
		{{id: "d-2", name: "two", status: "CREATE_SUCCESSFUL", createdAt: "2026-01-02T00:00:00.000Z"}},
		{{id: "d-1", name: "one", status: "CREATE_SUCCESSFUL", createdAt: "2026-01-01T00:00:00.000Z"}},
	}
	server := newServer(t, pages, nil)
	client := newClient(t, server.URL())

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	if _, err := client.ListAllDeployments(ctx, vcfautomation.ListDeploymentsOptions{PageSize: 1}); !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want it to wrap context.Canceled", err)
	}
}

// --- concurrency ------------------------------------------------------------

func TestConcurrentCollectionIsRaceFree(t *testing.T) {
	pages := [][]item{
		{
			{id: "d-04", name: "delta", status: "CREATE_SUCCESSFUL", createdAt: "2026-06-04T00:00:00.000Z"},
			{id: "d-01", name: "alpha", status: "CREATE_SUCCESSFUL", createdAt: "2026-06-01T00:00:00.000Z", description: "oldest"},
		},
		{
			{id: "d-03", name: "gamma", status: "UPDATE_FAILED", createdAt: "2026-06-03T00:00:00.000Z"},
			{id: "d-02", name: "beta", status: "CREATE_SUCCESSFUL", createdAt: "2026-06-02T00:00:00.000Z", ownedBy: "svc-automation"},
		},
	}
	server := newServer(t, pages, nil)
	client := newClient(t, server.URL())
	want := expectedOrder(flatten(pages))

	const workers = 8
	results := make([][]vcfautomation.Deployment, workers)
	errs := make([]error, workers)
	var group sync.WaitGroup
	group.Add(workers)
	for worker := 0; worker < workers; worker++ {
		go func(worker int) {
			defer group.Done()
			results[worker], errs[worker] = client.ListAllDeployments(context.Background(), vcfautomation.ListDeploymentsOptions{PageSize: 2})
		}(worker)
	}
	group.Wait()

	for worker := 0; worker < workers; worker++ {
		if errs[worker] != nil {
			t.Fatalf("worker %d: ListAllDeployments: %v", worker, errs[worker])
		}
		assertDeployments(t, results[worker], want)
	}
}
