package nsxpolicy_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"reflect"
	"testing"

	nsxpolicy "vcf91-0071"
	"vcf91-0071/internal/contractmock"
)

const (
	contractPath = "docs/contract.json"
	sourcePath   = "docs/official_sources.json"
	specCommit   = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	specPath     = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
)

func TestPinnedOfficialContract(t *testing.T) {
	t.Parallel()

	contract, err := contractmock.LoadContract(contractPath)
	if err != nil {
		t.Fatalf("LoadContract: %v", err)
	}
	if contract.Source.Repository != "https://github.com/vmware/vcf-api-specs" ||
		contract.Source.Commit != specCommit ||
		contract.Source.Path != specPath ||
		contract.Source.License != "Apache-2.0" {
		t.Fatalf("unexpected source provenance: %+v", contract.Source)
	}
	if contract.Info.Title != "NSX Policy API" || contract.Info.Version != "9.1.0.0" {
		t.Fatalf("unexpected specification info: %+v", contract.Info)
	}
	if contract.Security.Name != "BasicAuth" || contract.Security.Type != "basic" {
		t.Fatalf("unexpected security contract: %+v", contract.Security)
	}
	if len(contract.Operations) != 1 {
		t.Fatalf("got %d operations, want exactly 1", len(contract.Operations))
	}
	operation := contract.Operations[0]
	if operation.OperationID != contractmock.ListAllInfraSegments ||
		operation.Method != http.MethodGet ||
		operation.Path != "/infra/segments" {
		t.Fatalf("unexpected operation: %+v", operation)
	}

	data, err := os.ReadFile(sourcePath)
	if err != nil {
		t.Fatal(err)
	}
	var sources struct {
		CommitSHA    string   `json:"commit_sha"`
		SpecPath     string   `json:"spec_path"`
		OperationIDs []string `json:"operationIds"`
	}
	if err := json.Unmarshal(data, &sources); err != nil {
		t.Fatal(err)
	}
	if sources.CommitSHA != specCommit || sources.SpecPath != specPath ||
		!reflect.DeepEqual(sources.OperationIDs, []string{contractmock.ListAllInfraSegments}) {
		t.Fatalf("official_sources.json does not pin the contract: %+v", sources)
	}
}

func TestListAllSegmentsWirePaginationAndStableOrder(t *testing.T) {
	falseValue := false
	total := int64(4)

	tests := []struct {
		name        string
		options     nsxpolicy.ListOptions
		pages       map[string]contractmock.Page
		wantQueries []string
	}{
		{
			name:    "unset optionals are omitted and empty page continues",
			options: nsxpolicy.ListOptions{},
			pages: map[string]contractmock.Page{
				"": {
					Results: []contractmock.Segment{
						{ID: "zeta", DisplayName: "web", Path: "/infra/segments/zeta"},
						{ID: "b", DisplayName: "app", Path: "/infra/segments/b"},
					},
					Cursor:      "next +/=",
					ResultCount: &total,
				},
				"next +/=": {
					Results: []contractmock.Segment{},
					Cursor:  "empty-page",
				},
				"empty-page": {
					Results: []contractmock.Segment{
						{ID: "db", DisplayName: "database", Path: "/infra/segments/db"},
						{ID: "a", DisplayName: "app", Path: "/infra/segments/a"},
					},
				},
			},
			wantQueries: []string{
				"sort_ascending=true&sort_by=display_name",
				"cursor=next+%2B%2F%3D&sort_ascending=true&sort_by=display_name",
				"cursor=empty-page&sort_ascending=true&sort_by=display_name",
			},
		},
		{
			name: "set optionals repeat exactly on every page",
			options: nsxpolicy.ListOptions{
				PageSize:                      2,
				SegmentType:                   "DVPortgroup",
				IncludeMarkedForDeleteObjects: &falseValue,
				IncludedFields:                "id,display_name,path",
			},
			pages: map[string]contractmock.Page{
				"": {
					Results: []contractmock.Segment{
						{ID: "zeta", DisplayName: "web", Path: "/infra/segments/zeta"},
						{ID: "b", DisplayName: "app", Path: "/infra/segments/b"},
					},
					Cursor:      "page-2",
					ResultCount: &total,
				},
				"page-2": {
					Results: []contractmock.Segment{
						{ID: "db", DisplayName: "database", Path: "/infra/segments/db"},
						{ID: "a", DisplayName: "app", Path: "/infra/segments/a"},
					},
				},
			},
			wantQueries: []string{
				"include_mark_for_delete_objects=false&included_fields=id%2Cdisplay_name%2Cpath&page_size=2&segment_type=DVPortgroup&sort_ascending=true&sort_by=display_name",
				"cursor=page-2&include_mark_for_delete_objects=false&included_fields=id%2Cdisplay_name%2Cpath&page_size=2&segment_type=DVPortgroup&sort_ascending=true&sort_by=display_name",
			},
		},
	}

	wantSegments := []nsxpolicy.Segment{
		{ID: "a", DisplayName: "app", Path: "/infra/segments/a"},
		{ID: "b", DisplayName: "app", Path: "/infra/segments/b"},
		{ID: "db", DisplayName: "database", Path: "/infra/segments/db"},
		{ID: "zeta", DisplayName: "web", Path: "/infra/segments/zeta"},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server := contractmock.New(t, contractPath, test.pages)
			client, err := nsxpolicy.NewClient(nsxpolicy.Config{
				BaseURL:    server.URL,
				Username:   "admin",
				Password:   "secret",
				HTTPClient: server.Client(),
			})
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}

			got, err := client.ListAllSegments(context.Background(), test.options)
			if err != nil {
				t.Fatalf("ListAllSegments: %v", err)
			}
			if !reflect.DeepEqual(got, wantSegments) {
				t.Fatalf("segments:\n got: %#v\nwant: %#v", got, wantSegments)
			}

			requests := server.Requests()
			if len(requests) != len(test.wantQueries) {
				t.Fatalf("got %d requests, want %d: %+v", len(requests), len(test.wantQueries), requests)
			}
			for i, request := range requests {
				if request.OperationID != contractmock.ListAllInfraSegments {
					t.Errorf("request %d operation = %q", i, request.OperationID)
				}
				if request.Method != http.MethodGet {
					t.Errorf("request %d method = %q, want GET", i, request.Method)
				}
				if request.Path != "/policy/api/v1/infra/segments" {
					t.Errorf("request %d path = %q", i, request.Path)
				}
				if request.RawQuery != test.wantQueries[i] {
					t.Errorf("request %d query:\n got: %q\nwant: %q", i, request.RawQuery, test.wantQueries[i])
				}
				if got := request.Header.Values("Accept"); !reflect.DeepEqual(got, []string{"application/json"}) {
					t.Errorf("request %d Accept = %#v", i, got)
				}
				if got := request.Header.Values("Authorization"); !reflect.DeepEqual(got, []string{"Basic YWRtaW46c2VjcmV0"}) {
					t.Errorf("request %d Authorization = %#v", i, got)
				}
				if got := request.Header.Values("Content-Type"); len(got) != 0 {
					t.Errorf("request %d unexpectedly set Content-Type: %#v", i, got)
				}
				if request.Body != "" {
					t.Errorf("request %d body = %q, want empty", i, request.Body)
				}
			}
		})
	}
}

func TestListOptionsValidationMakesNoRequest(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name    string
		options nsxpolicy.ListOptions
	}{
		{name: "negative page size", options: nsxpolicy.ListOptions{PageSize: -1}},
		{name: "page size above spec maximum", options: nsxpolicy.ListOptions{PageSize: 1001}},
		{name: "unknown segment type", options: nsxpolicy.ListOptions{SegmentType: "OVERLAY"}},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server := contractmock.New(t, contractPath, map[string]contractmock.Page{
				"": {Results: []contractmock.Segment{}},
			})
			client, err := nsxpolicy.NewClient(nsxpolicy.Config{
				BaseURL: server.URL, Username: "admin", Password: "secret", HTTPClient: server.Client(),
			})
			if err != nil {
				t.Fatal(err)
			}
			if _, err := client.ListAllSegments(context.Background(), test.options); err == nil {
				t.Fatal("ListAllSegments returned nil error")
			}
			if got := len(server.Requests()); got != 0 {
				t.Fatalf("validation made %d requests, want 0", got)
			}
		})
	}
}

func TestRepeatedCursorStops(t *testing.T) {
	t.Parallel()
	server := contractmock.New(t, contractPath, map[string]contractmock.Page{
		"": {
			Results: []contractmock.Segment{{ID: "a", DisplayName: "a"}},
			Cursor:  "repeat-me",
		},
		"repeat-me": {
			Results: []contractmock.Segment{{ID: "b", DisplayName: "b"}},
			Cursor:  "repeat-me",
		},
	})
	client, err := nsxpolicy.NewClient(nsxpolicy.Config{
		BaseURL: server.URL, Username: "admin", Password: "secret", HTTPClient: server.Client(),
	})
	if err != nil {
		t.Fatal(err)
	}
	_, err = client.ListAllSegments(context.Background(), nsxpolicy.ListOptions{})
	if !errors.Is(err, nsxpolicy.ErrRepeatedCursor) {
		t.Fatalf("error = %v, want ErrRepeatedCursor", err)
	}
	if got := len(server.Requests()); got != 2 {
		t.Fatalf("got %d requests, want 2", got)
	}
}

func TestContractMockRejectsUnlistedOperations(t *testing.T) {
	t.Parallel()
	server := contractmock.New(t, contractPath, map[string]contractmock.Page{
		"": {Results: []contractmock.Segment{}},
	})
	response, err := server.Client().Get(server.URL + "/policy/api/v1/infra/tier-1s")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, response.Body)
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", response.StatusCode)
	}
	requests := server.Requests()
	if len(requests) != 1 || requests[0].OperationID != "" {
		t.Fatalf("unexpected request log: %+v", requests)
	}
}
