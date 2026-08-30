package nsxpolicy_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"example.com/vcf91/nsxtokenresume/internal/contractmock"
	"example.com/vcf91/nsxtokenresume/nsxpolicy"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedSpec   = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
)

type operationSource struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

func TestProtectedContractProvenance(t *testing.T) {
	t.Parallel()

	var contract struct {
		Source struct {
			Commit   string `json:"repository_commit_sha"`
			SpecPath string `json:"spec_path"`
		} `json:"source"`
		Info struct {
			Version string `json:"version"`
		} `json:"info"`
		BasePath   string `json:"basePath"`
		Operations []struct {
			operationSource
			Parameters []struct {
				Name     string `json:"name"`
				In       string `json:"in"`
				Required bool   `json:"required"`
			} `json:"parameters"`
		} `json:"operations"`
	}
	readJSON(t, "../docs/contract.json", &contract)

	var sources struct {
		Commit     string            `json:"repository_commit_sha"`
		SpecPath   string            `json:"spec_path"`
		Operations []operationSource `json:"operations"`
	}
	readJSON(t, "../docs/official_sources.json", &sources)

	if contract.Source.Commit != expectedCommit || sources.Commit != expectedCommit {
		t.Fatalf(
			"repository commit mismatch: contract=%q sources=%q",
			contract.Source.Commit,
			sources.Commit,
		)
	}
	if contract.Source.SpecPath != expectedSpec || sources.SpecPath != expectedSpec {
		t.Fatalf(
			"specification path mismatch: contract=%q sources=%q",
			contract.Source.SpecPath,
			sources.SpecPath,
		)
	}
	if contract.Info.Version != "9.1.0.0" ||
		contract.BasePath != "/policy/api/v1" {
		t.Fatalf(
			"unexpected contract version/base path: version=%q base=%q",
			contract.Info.Version,
			contract.BasePath,
		)
	}
	wantOperation := operationSource{
		OperationID: "ListGroupForDomain",
		Method:      http.MethodGet,
		Path:        "/infra/domains/{domain-id}/groups",
	}
	if len(contract.Operations) != 1 ||
		contract.Operations[0].operationSource != wantOperation {
		t.Fatalf("contract operations mismatch: %#v", contract.Operations)
	}
	if !reflect.DeepEqual(sources.Operations, []operationSource{wantOperation}) {
		t.Fatalf("official source operations mismatch: %#v", sources.Operations)
	}

	gotParameters := make([]string, 0, len(contract.Operations[0].Parameters))
	for _, parameter := range contract.Operations[0].Parameters {
		gotParameters = append(gotParameters, parameter.Name)
		if parameter.Name == "domain-id" {
			if parameter.In != "path" || !parameter.Required {
				t.Fatalf("domain-id projection is not a required path parameter")
			}
		} else if parameter.In != "query" || parameter.Required {
			t.Fatalf("optional query projection is wrong for %q", parameter.Name)
		}
	}
	wantParameters := []string{
		"domain-id",
		"cursor",
		"include_mark_for_delete_objects",
		"included_fields",
		"member_types",
		"page_size",
		"sort_ascending",
		"sort_by",
	}
	if !reflect.DeepEqual(gotParameters, wantParameters) {
		t.Fatalf(
			"ListGroupForDomain parameters mismatch:\n got: %v\nwant: %v",
			gotParameters,
			wantParameters,
		)
	}
}

func readJSON(t testing.TB, path string, target any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func TestListAllGroupsContract(t *testing.T) {
	t.Parallel()

	first := []contractmock.WireGroup{
		{
			ID:           "web",
			DisplayName:  "Web",
			Path:         "/infra/domains/prod/groups/web",
			ResourceType: "Group",
		},
		{
			ID:           "app",
			DisplayName:  "App",
			Path:         "/infra/domains/prod/groups/app",
			ResourceType: "Group",
		},
	}
	second := []contractmock.WireGroup{
		{
			ID:           "db",
			DisplayName:  "Database",
			Path:         "/infra/domains/prod/groups/db",
			ResourceType: "Group",
		},
	}

	tests := []struct {
		name             string
		expire           bool
		domainID         string
		cursor           string
		wantTargets      []string
		wantAuth         []string
		wantRefreshCalls int32
	}{
		{
			name:     "expired token retries the current opaque cursor",
			expire:   true,
			domainID: "prod/east +%",
			cursor:   "after/group +%",
			wantTargets: []string{
				"/policy/api/v1/infra/domains/prod%2Feast%20+%25/groups",
				"/policy/api/v1/infra/domains/prod%2Feast%20+%25/groups?cursor=after%2Fgroup+%2B%25",
				"/policy/api/v1/infra/domains/prod%2Feast%20+%25/groups?cursor=after%2Fgroup+%2B%25",
			},
			wantAuth: []string{
				"Bearer old access/token +%",
				"Bearer old access/token +%",
				"Bearer new access/token +%",
			},
			wantRefreshCalls: 1,
		},
		{
			name:     "valid token traverses pages without refresh",
			expire:   false,
			domainID: "production",
			cursor:   "next",
			wantTargets: []string{
				"/policy/api/v1/infra/domains/production/groups",
				"/policy/api/v1/infra/domains/production/groups?cursor=next",
			},
			wantAuth: []string{
				"Bearer old access/token +%",
				"Bearer old access/token +%",
			},
			wantRefreshCalls: 0,
		},
	}

	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()

			logPath := t.TempDir() + "/requests.jsonl"
			mock := contractmock.Start(
				t,
				"../docs/contract.json",
				logPath,
				contractmock.Scenario{
					DomainID:        tt.domainID,
					OldToken:        "old access/token +%",
					NewToken:        "new access/token +%",
					Cursor:          tt.cursor,
					FirstPage:       first,
					SecondPage:      second,
					ExpireOldCursor: tt.expire,
				},
			)
			defer mock.Close()

			var refreshCalls atomic.Int32
			client, err := nsxpolicy.NewClient(nsxpolicy.Config{
				BaseURL:     mock.URL,
				AccessToken: "old access/token +%",
				HTTPClient:  mock.Client,
				Refresh: func(_ context.Context, expired string) (string, error) {
					refreshCalls.Add(1)
					if expired != "old access/token +%" {
						return "", fmt.Errorf("unexpected expired token")
					}
					return "new access/token +%", nil
				},
			})
			if err != nil {
				t.Fatalf("NewClient() error = %v", err)
			}

			got, err := client.ListAllGroups(context.Background(), tt.domainID)
			if err != nil {
				t.Fatalf("ListAllGroups() error = %v", err)
			}
			want := []nsxpolicy.Group{
				{
					ID: "web", DisplayName: "Web",
					Path: "/infra/domains/prod/groups/web", ResourceType: "Group",
				},
				{
					ID: "app", DisplayName: "App",
					Path: "/infra/domains/prod/groups/app", ResourceType: "Group",
				},
				{
					ID: "db", DisplayName: "Database",
					Path: "/infra/domains/prod/groups/db", ResourceType: "Group",
				},
			}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("ListAllGroups() = %#v, want %#v", got, want)
			}
			if got := refreshCalls.Load(); got != tt.wantRefreshCalls {
				t.Fatalf("refresh calls = %d, want %d", got, tt.wantRefreshCalls)
			}

			records, err := contractmock.ReadLog(logPath)
			if err != nil {
				t.Fatalf("ReadLog() error = %v", err)
			}
			assertExactWire(t, records, tt.wantTargets, tt.wantAuth)
		})
	}
}

func TestConcurrentCallsCoalesceStaleTokenRefresh(t *testing.T) {
	t.Parallel()

	logPath := t.TempDir() + "/requests.jsonl"
	mock := contractmock.Start(
		t,
		"../docs/contract.json",
		logPath,
		contractmock.Scenario{
			DomainID: "concurrent",
			OldToken: "stale",
			NewToken: "fresh",
			Cursor:   "next",
			FirstPage: []contractmock.WireGroup{
				{ID: "one", DisplayName: "One", ResourceType: "Group"},
			},
			SecondPage: []contractmock.WireGroup{
				{ID: "two", DisplayName: "Two", ResourceType: "Group"},
			},
			ExpireOldCursor: true,
		},
	)
	defer mock.Close()

	var refreshCalls atomic.Int32
	client, err := nsxpolicy.NewClient(nsxpolicy.Config{
		BaseURL:     mock.URL,
		AccessToken: "stale",
		HTTPClient:  mock.Client,
		Refresh: func(context.Context, string) (string, error) {
			refreshCalls.Add(1)
			return "fresh", nil
		},
	})
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}

	start := make(chan struct{})
	errs := make(chan error, 2)
	var wg sync.WaitGroup
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			groups, err := client.ListAllGroups(context.Background(), "concurrent")
			if err == nil && len(groups) != 2 {
				err = fmt.Errorf("group count = %d, want 2", len(groups))
			}
			errs <- err
		}()
	}
	close(start)
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Errorf("concurrent ListAllGroups() error = %v", err)
		}
	}
	if got := refreshCalls.Load(); got != 1 {
		t.Fatalf("refresh calls = %d, want exactly 1 for one stale token", got)
	}
}

func assertExactWire(
	t *testing.T,
	records []contractmock.RequestRecord,
	wantTargets, wantAuth []string,
) {
	t.Helper()
	if len(records) != len(wantTargets) {
		t.Fatalf("request count = %d, want %d; records = %#v", len(records), len(wantTargets), records)
	}
	if len(wantAuth) != len(wantTargets) {
		t.Fatal("protected verifier has inconsistent expectations")
	}
	unset := []string{
		"include_mark_for_delete_objects",
		"included_fields",
		"member_types",
		"page_size",
		"sort_ascending",
		"sort_by",
	}
	for i, record := range records {
		if record.Sequence != i+1 {
			t.Errorf("request[%d] sequence = %d, want %d", i, record.Sequence, i+1)
		}
		if record.Method != http.MethodGet {
			t.Errorf("request[%d] method = %q, want GET", i, record.Method)
		}
		if record.Target != wantTargets[i] {
			t.Errorf("request[%d] target = %q, want %q", i, record.Target, wantTargets[i])
		}
		if record.Authorization != wantAuth[i] {
			t.Errorf("request[%d] authorization = %q, want %q", i, record.Authorization, wantAuth[i])
		}
		if record.Accept != "application/json" {
			t.Errorf("request[%d] Accept = %q, want application/json", i, record.Accept)
		}
		if record.ContentType != "" {
			t.Errorf("request[%d] Content-Type = %q, want omitted", i, record.ContentType)
		}
		if record.ContentLength != 0 || record.BodyBase64 != "" {
			t.Errorf(
				"request[%d] unexpectedly has a body: length=%d base64=%q",
				i,
				record.ContentLength,
				record.BodyBase64,
			)
		}
		for _, name := range unset {
			if strings.Contains(record.Target, name) {
				t.Errorf("request[%d] sends unset optional field %q in %q", i, name, record.Target)
			}
		}
	}
}

func TestNewClientValidation(t *testing.T) {
	t.Parallel()
	refresh := func(context.Context, string) (string, error) { return "new", nil }
	tests := []struct {
		name string
		cfg  nsxpolicy.Config
	}{
		{name: "empty URL", cfg: nsxpolicy.Config{AccessToken: "old", Refresh: refresh}},
		{name: "URL credentials", cfg: nsxpolicy.Config{BaseURL: "https://u:p@example.test", AccessToken: "old", Refresh: refresh}},
		{name: "URL path", cfg: nsxpolicy.Config{BaseURL: "https://example.test/policy", AccessToken: "old", Refresh: refresh}},
		{name: "URL query", cfg: nsxpolicy.Config{BaseURL: "https://example.test/?x=1", AccessToken: "old", Refresh: refresh}},
		{name: "blank token", cfg: nsxpolicy.Config{BaseURL: "https://example.test", AccessToken: " \t", Refresh: refresh}},
		{name: "nil refresh", cfg: nsxpolicy.Config{BaseURL: "https://example.test", AccessToken: "old"}},
	}
	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			if client, err := nsxpolicy.NewClient(tt.cfg); err == nil || client != nil {
				t.Fatalf("NewClient(%s) = (%#v, %v), want (nil, error)", tt.name, client, err)
			}
		})
	}
}

func TestListAllGroupsRejectsBlankDomainBeforeRequest(t *testing.T) {
	t.Parallel()
	client, err := nsxpolicy.NewClient(nsxpolicy.Config{
		BaseURL:     "http://127.0.0.1:1",
		AccessToken: "old",
		Refresh: func(context.Context, string) (string, error) {
			return "", errors.New("must not be called")
		},
	})
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}
	for _, domainID := range []string{"", " ", "\t\n"} {
		if _, err := client.ListAllGroups(context.Background(), domainID); err == nil {
			t.Errorf("ListAllGroups(%q) error = nil, want validation error", domainID)
		}
	}
}
