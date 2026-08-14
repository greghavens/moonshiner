package vcfautomation_test

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strings"
	"testing"

	vcf "vcfautomation"
	"vcfautomation/internal/vcfmock"
)

func TestProvisionWireContractAndRefreshResume(t *testing.T) {
	bulk := 2
	tests := []struct {
		name       string
		fixture    vcfmock.Fixture
		request    vcf.CatalogItemRequest
		wantCreate string
	}{
		{
			name: "unset optional fields are omitted",
			fixture: vcfmock.Fixture{
				ItemID: "item-42", DeploymentID: "dep-99", DeploymentName: "payments",
				ClientID: "client-A", ClientSecret: "secret-A", AccessToken: "access-old",
				RefreshToken: "refresh-A", RefreshedAccessToken: "access-new",
			},
			request: vcf.CatalogItemRequest{
				DeploymentName: "payments",
				Inputs:         map[string]any{"cpu": 2},
				ProjectID:      "proj-7",
			},
			wantCreate: `{"deploymentName":"payments","inputs":{"cpu":2},"projectId":"proj-7"}`,
		},
		{
			name: "set optional fields retain contract names",
			fixture: vcfmock.Fixture{
				ItemID: "item 77", DeploymentID: "dep 31", DeploymentName: "analytics",
				ClientID: "client-B", ClientSecret: "secret B", AccessToken: "stale.two",
				RefreshToken: "refresh Z/2", RefreshedAccessToken: "fresh.two",
			},
			request: vcf.CatalogItemRequest{
				BulkRequestCount: &bulk,
				DeploymentName:   "analytics",
				Inputs:           map[string]any{"memoryGiB": 8, "region": "west"},
				ProjectID:        "proj-9",
				Reason:           "nightly validation",
				Version:          "v2.0",
			},
			wantCreate: `{"bulkRequestCount":2,"deploymentName":"analytics","inputs":{"memoryGiB":8,"region":"west"},"projectId":"proj-9","reason":"nightly validation","version":"v2.0"}`,
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server := vcfmock.New(t, test.fixture)
			client, err := vcf.NewClient(vcf.Config{
				BaseURL:      server.URL(),
				ClientID:     test.fixture.ClientID,
				ClientSecret: test.fixture.ClientSecret,
				AccessToken:  test.fixture.AccessToken,
				RefreshToken: test.fixture.RefreshToken,
				HTTPClient:   server.Client(),
			})
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}

			deployment, err := client.Provision(context.Background(), test.fixture.ItemID, test.request)
			if err != nil {
				t.Fatalf("Provision: %v", err)
			}
			if deployment != (vcf.Deployment{ID: test.fixture.DeploymentID, Name: test.fixture.DeploymentName, Status: "CREATE_SUCCESSFUL"}) {
				t.Fatalf("deployment = %#v", deployment)
			}

			log := server.Log()
			if len(log) != 4 {
				t.Fatalf("request count = %d, want 4; log = %#v", len(log), log)
			}
			basic := "Basic " + base64.StdEncoding.EncodeToString([]byte(test.fixture.ClientID+":"+test.fixture.ClientSecret))
			createPath := "/catalog/api/items/" + test.fixture.ItemID + "/request"
			createURI := "/catalog/api/items/" + url.PathEscape(test.fixture.ItemID) + "/request"
			deploymentPath := "/deployment/api/deployments/" + test.fixture.DeploymentID
			deploymentURI := "/deployment/api/deployments/" + url.PathEscape(test.fixture.DeploymentID)
			refreshBody := url.Values{"grant_type": {"refresh_token"}, "refresh_token": {test.fixture.RefreshToken}}.Encode()
			want := []vcfmock.Request{
				{Method: "POST", Path: createPath, RequestURI: createURI, Header: http.Header{"Authorization": {"Bearer " + test.fixture.AccessToken}, "Content-Type": {"application/json"}}, Body: test.wantCreate},
				{Method: "GET", Path: deploymentPath, RequestURI: deploymentURI, Header: http.Header{"Authorization": {"Bearer " + test.fixture.AccessToken}}},
				{Method: "POST", Path: "/oidc/oauth2/token", RequestURI: "/oidc/oauth2/token", Header: http.Header{"Authorization": {basic}, "Content-Type": {"application/x-www-form-urlencoded"}}, Body: refreshBody},
				{Method: "GET", Path: deploymentPath, RequestURI: deploymentURI, Header: http.Header{"Authorization": {"Bearer " + test.fixture.RefreshedAccessToken}}},
			}
			for i := range want {
				assertWireRequest(t, i, log[i], want[i])
			}

			var sent map[string]any
			if err := json.Unmarshal([]byte(log[0].Body), &sent); err != nil {
				t.Fatalf("create body is not JSON: %v", err)
			}
			if test.request.BulkRequestCount == nil {
				for _, omitted := range []string{"bulkRequestCount", "reason", "version"} {
					if _, exists := sent[omitted]; exists {
						t.Errorf("unset optional field %q was sent in %s", omitted, log[0].Body)
					}
				}
			}
		})
	}
}

func TestContractProvenanceAndMockOperationSet(t *testing.T) {
	type operation struct {
		Operation string `json:"operation"`
		Method    string `json:"method"`
		Path      string `json:"path"`
		SourceURL string `json:"source_url"`
	}
	var contract struct {
		ProductVersion string `json:"product_version"`
		Provenance     struct {
			SourceKind                        string `json:"source_kind"`
			PublishedSpecification            *bool  `json:"published_specification"`
			Statement                         string `json:"statement"`
			ComparisonRepository              string `json:"comparison_repository"`
			ComparisonLicense                 string `json:"comparison_repository_license"`
			VCFAutomationSpecificationPresent *bool  `json:"vcf_automation_specification_present"`
		} `json:"provenance"`
		Operations []operation `json:"operations"`
	}
	readJSON(t, "docs/contract.json", &contract)
	if contract.ProductVersion != "9.0" {
		t.Fatalf("contract product_version = %q", contract.ProductVersion)
	}
	if contract.Provenance.SourceKind != "official_reference_documentation" ||
		contract.Provenance.PublishedSpecification == nil || *contract.Provenance.PublishedSpecification ||
		!strings.Contains(contract.Provenance.Statement, "not a published specification") ||
		contract.Provenance.ComparisonRepository != "vmware/vcf-api-specs" || contract.Provenance.ComparisonLicense != "Apache-2.0" ||
		contract.Provenance.VCFAutomationSpecificationPresent == nil || *contract.Provenance.VCFAutomationSpecificationPresent {
		t.Fatalf("contract provenance does not state its reference-derived status: %#v", contract.Provenance)
	}

	var sources struct {
		FetchedOn string `json:"fetched_on"`
		Sources   []struct {
			PageURL   string `json:"page_url"`
			Operation string `json:"operation"`
			FetchedOn string `json:"fetched_on"`
		} `json:"sources"`
	}
	readJSON(t, "docs/official_sources.json", &sources)
	if sources.FetchedOn != "2026-08-13" || len(sources.Sources) != len(contract.Operations) {
		t.Fatalf("source manifest does not cover every operation: %#v", sources)
	}
	sourceRecords := make(map[string]bool)
	for _, source := range sources.Sources {
		parsed, err := url.Parse(source.PageURL)
		if err != nil || parsed.Scheme != "https" || parsed.Host != "developer.broadcom.com" || source.FetchedOn != "2026-08-13" {
			t.Fatalf("invalid official source record: %#v (parse error %v)", source, err)
		}
		sourceRecords[source.PageURL+"\n"+source.Operation] = true
	}

	var fromContract []string
	for _, operation := range contract.Operations {
		if !sourceRecords[operation.SourceURL+"\n"+operation.Operation] {
			t.Errorf("operation %q has no exact official source record", operation.Operation)
		}
		fromContract = append(fromContract, operation.Method+" "+operation.Path)
	}
	var fromMock []string
	for _, operation := range vcfmock.Operations() {
		fromMock = append(fromMock, operation.Method+" "+operation.Path)
	}
	sort.Strings(fromContract)
	sort.Strings(fromMock)
	if strings.Join(fromContract, "\n") != strings.Join(fromMock, "\n") {
		t.Fatalf("mock operations = %v, contract operations = %v", fromMock, fromContract)
	}
}

func TestMockRejectsOperationOutsideContract(t *testing.T) {
	server := vcfmock.New(t, vcfmock.Fixture{
		ItemID: "item", DeploymentID: "deployment", DeploymentName: "name",
		ClientID: "client", ClientSecret: "secret", AccessToken: "old",
		RefreshToken: "refresh", RefreshedAccessToken: "new",
	})
	response, err := server.Client().Get(server.URL() + "/deployment/api/requests/not-in-contract")
	if err != nil {
		t.Fatalf("loopback request: %v", err)
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, response.Body)
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("outside-contract status = %d, want 404", response.StatusCode)
	}
}

func TestNewClientValidation(t *testing.T) {
	tests := []struct {
		name   string
		change func(*vcf.Config)
		want   string
	}{
		{name: "base URL", change: func(c *vcf.Config) { c.BaseURL = "" }, want: "base URL"},
		{name: "client credentials", change: func(c *vcf.Config) { c.ClientSecret = "" }, want: "client credentials"},
		{name: "tokens", change: func(c *vcf.Config) { c.RefreshToken = "" }, want: "tokens"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			config := vcf.Config{BaseURL: "http://127.0.0.1", ClientID: "id", ClientSecret: "secret", AccessToken: "access", RefreshToken: "refresh"}
			test.change(&config)
			if _, err := vcf.NewClient(config); err == nil || !strings.Contains(err.Error(), test.want) {
				t.Fatalf("NewClient error = %v, want text %q", err, test.want)
			}
		})
	}
}

func assertWireRequest(t *testing.T, index int, got, want vcfmock.Request) {
	t.Helper()
	if got.Method != want.Method || got.Path != want.Path || got.RequestURI != want.RequestURI || got.Query != "" || got.Body != want.Body {
		t.Errorf("request[%d] wire = method %q path %q URI %q query %q body %q; want method %q path %q URI %q empty query body %q", index, got.Method, got.Path, got.RequestURI, got.Query, got.Body, want.Method, want.Path, want.RequestURI, want.Body)
	}
	for _, name := range []string{"Authorization", "Content-Type"} {
		if got.Header.Get(name) != want.Header.Get(name) {
			t.Errorf("request[%d] %s = %q, want %q", index, name, got.Header.Get(name), want.Header.Get(name))
		}
	}
}

func readJSON(t *testing.T, path string, target any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
}
