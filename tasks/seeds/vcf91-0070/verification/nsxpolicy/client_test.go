package nsxpolicy_test

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sync"
	"testing"

	"example.com/vcf91/nsxtokenresume/internal/contractmock"
	"example.com/vcf91/nsxtokenresume/nsxpolicy"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedSpec   = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
)

func TestProtectedContractUsesBasicAuth(t *testing.T) {
	var document struct {
		Source struct {
			Commit string `json:"repository_commit_sha"`
			Path   string `json:"spec_path"`
		} `json:"source"`
		Security []map[string][]string `json:"security"`
		SecurityDefinitions map[string]struct{ Type string `json:"type"` } `json:"securityDefinitions"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method string `json:"method"`
			Path string `json:"path"`
		} `json:"operations"`
	}
	readJSON(t, "../docs/contract.json", &document)
	if document.Source.Commit != expectedCommit || document.Source.Path != expectedSpec {
		t.Fatalf("wrong source: %#v", document.Source)
	}
	if len(document.Security) != 1 || document.SecurityDefinitions["BasicAuth"].Type != "basic" {
		t.Fatalf("contract does not project BasicAuth: %#v", document.Security)
	}
	if len(document.Operations) != 1 || document.Operations[0].OperationID != "ListGroupForDomain" || document.Operations[0].Method != http.MethodGet {
		t.Fatalf("wrong operation: %#v", document.Operations)
	}
}

func TestListAllGroupsUsesBasicAndFollowsCursor(t *testing.T) {
	first := []contractmock.WireGroup{{ID: "web", DisplayName: "Web", Path: "/infra/domains/prod/groups/web", ResourceType: "Group"}}
	second := []contractmock.WireGroup{{ID: "db", DisplayName: "Database", Path: "/infra/domains/prod/groups/db", ResourceType: "Group"}}
	logPath := filepath.Join(t.TempDir(), "requests.jsonl")
	server := contractmock.Start(t, "../docs/contract.json", logPath, contractmock.Scenario{
		DomainID: "prod", Username: "automation", Password: "secret",
		Cursor: "after/group +%", FirstPage: first, SecondPage: second,
	})
	defer server.Close()
	client, err := nsxpolicy.NewClient(nsxpolicy.Config{BaseURL: server.URL, Username: "automation", Password: "secret", HTTPClient: server.Client})
	if err != nil { t.Fatal(err) }
	got, err := client.ListAllGroups(context.Background(), "prod")
	if err != nil { t.Fatal(err) }
	want := []nsxpolicy.Group{
		{ID: "web", DisplayName: "Web", Path: "/infra/domains/prod/groups/web", ResourceType: "Group"},
		{ID: "db", DisplayName: "Database", Path: "/infra/domains/prod/groups/db", ResourceType: "Group"},
	}
	if !reflect.DeepEqual(got, want) { t.Fatalf("groups = %#v, want %#v", got, want) }
	records, err := contractmock.ReadLog(logPath)
	if err != nil { t.Fatal(err) }
	if len(records) != 2 { t.Fatalf("requests = %d, want 2", len(records)) }
	wantTargets := []string{
		"/policy/api/v1/infra/domains/prod/groups",
		"/policy/api/v1/infra/domains/prod/groups?cursor=after%2Fgroup+%2B%25",
	}
	wantAuth := "Basic " + base64.StdEncoding.EncodeToString([]byte("automation:secret"))
	for index, record := range records {
		if record.Method != http.MethodGet || record.Target != wantTargets[index] || record.Authorization != wantAuth || record.Accept != "application/json" {
			t.Fatalf("request %d = %#v", index, record)
		}
		if record.ContentType != "" || record.ContentLength != 0 || record.BodyBase64 != "" {
			t.Fatalf("GET request carried body metadata: %#v", record)
		}
	}
}

func TestRejectedBasicCredentialsAreFinal(t *testing.T) {
	logPath := filepath.Join(t.TempDir(), "requests.jsonl")
	server := contractmock.Start(t, "../docs/contract.json", logPath, contractmock.Scenario{DomainID: "default", Username: "right", Password: "right", FirstPage: []contractmock.WireGroup{}})
	defer server.Close()
	client, err := nsxpolicy.NewClient(nsxpolicy.Config{BaseURL: server.URL, Username: "wrong", Password: "wrong", HTTPClient: server.Client})
	if err != nil { t.Fatal(err) }
	_, err = client.ListAllGroups(context.Background(), "default")
	var apiError *nsxpolicy.APIError
	if !errors.As(err, &apiError) || apiError.StatusCode != http.StatusForbidden || apiError.ErrorCode != 403 || apiError.ModuleName != "common-services" {
		t.Fatalf("error = %#v", err)
	}
	records, readErr := contractmock.ReadLog(logPath)
	if readErr != nil { t.Fatal(readErr) }
	if len(records) != 1 { t.Fatalf("authentication failure was retried: %d", len(records)) }
}

func TestConfigurationValidation(t *testing.T) {
	tests := []nsxpolicy.Config{
		{},
		{BaseURL: "ftp://example.test", Username: "u", Password: "p"},
		{BaseURL: "https://example.test/path", Username: "u", Password: "p"},
		{BaseURL: "https://example.test", Username: "", Password: "p"},
		{BaseURL: "https://example.test", Username: "u", Password: "\n"},
	}
	for _, config := range tests {
		if _, err := nsxpolicy.NewClient(config); err == nil { t.Fatalf("accepted invalid config: %#v", config) }
	}
}

func TestClientIsSafeForConcurrentReads(t *testing.T) {
	logPath := filepath.Join(t.TempDir(), "requests.jsonl")
	server := contractmock.Start(t, "../docs/contract.json", logPath, contractmock.Scenario{DomainID: "default", Username: "u", Password: "p", FirstPage: []contractmock.WireGroup{}})
	defer server.Close()
	client, err := nsxpolicy.NewClient(nsxpolicy.Config{BaseURL: server.URL, Username: "u", Password: "p", HTTPClient: server.Client})
	if err != nil { t.Fatal(err) }
	var wg sync.WaitGroup
	errorsSeen := make(chan error, 8)
	for index := 0; index < 8; index++ {
		wg.Add(1)
		go func() { defer wg.Done(); _, callErr := client.ListAllGroups(context.Background(), "default"); errorsSeen <- callErr }()
	}
	wg.Wait()
	close(errorsSeen)
	for callErr := range errorsSeen { if callErr != nil { t.Fatal(callErr) } }
}

func readJSON(t testing.TB, path string, target any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil { t.Fatal(err) }
	if err := json.Unmarshal(data, target); err != nil { t.Fatal(err) }
}
