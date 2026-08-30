package protected_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	nsxpolicy "vcf91-0078"
	"vcf91-0078/internal/contractmock"
)

const (
	pinnedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	pinnedBlob   = "102d15fd342f6a45bb6d84a5b39a916c65929f4c"
	pinnedSpec   = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
)

func TestOfficialSpecificationProvenance(t *testing.T) {
	t.Parallel()

	var contract struct {
		SchemaVersion int    `json:"schema_version"`
		Swagger       string `json:"swagger"`
		Info          struct {
			Title   string `json:"title"`
			Version string `json:"version"`
		} `json:"info"`
		BasePath string `json:"basePath"`
		Source   struct {
			Repository string `json:"repository"`
			Commit     string `json:"repository_commit_sha"`
			Blob       string `json:"spec_blob_sha"`
			Path       string `json:"spec_path"`
			License    string `json:"license"`
			Derivation string `json:"derivation"`
		} `json:"source"`
		Operations map[string]struct {
			OperationID     string `json:"operationId"`
			Method          string `json:"method"`
			Path            string `json:"path"`
			PathParameters  []any  `json:"path_parameters"`
			QueryParameters []struct {
				Name     string   `json:"name"`
				Required bool     `json:"required"`
				Type     string   `json:"type"`
				Format   string   `json:"format"`
				Minimum  *int64   `json:"minimum"`
				Maximum  *int64   `json:"maximum"`
				Default  any      `json:"default"`
				Enum     []string `json:"enum"`
			} `json:"query_parameters"`
			RequestBody any `json:"request_body"`
			Success     struct {
				Status    int    `json:"status"`
				SchemaRef string `json:"schema_ref"`
			} `json:"success"`
			Errors []int `json:"declared_error_statuses"`
		} `json:"operations"`
	}
	readJSONFile(t, "../../docs/contract.json", &contract)
	if contract.SchemaVersion != 1 || contract.Swagger != "2.0" ||
		contract.Info.Title != "NSX Policy API" ||
		contract.Info.Version != "9.1.0.0" ||
		contract.BasePath != "/policy/api/v1" {
		t.Fatalf("unexpected OpenAPI projection metadata: %#v", contract)
	}
	if contract.Source.Repository != "https://github.com/vmware/vcf-api-specs" ||
		contract.Source.Commit != pinnedCommit || contract.Source.Blob != pinnedBlob ||
		contract.Source.Path != pinnedSpec || contract.Source.License != "Apache-2.0" ||
		!strings.Contains(contract.Source.Derivation, "no rendered documentation page") {
		t.Fatalf("contract source is not the pinned specification: %#v", contract.Source)
	}
	if len(contract.Operations) != 1 {
		t.Fatalf("contract has %d operations, want 1", len(contract.Operations))
	}
	op, ok := contract.Operations[nsxpolicy.ListAllInfraSegmentsOperation]
	if !ok || op.OperationID != nsxpolicy.ListAllInfraSegmentsOperation ||
		op.Method != http.MethodGet || op.Path != "/infra/segments" ||
		len(op.PathParameters) != 0 || op.RequestBody != nil ||
		op.Success.Status != http.StatusOK ||
		op.Success.SchemaRef != "#/definitions/SegmentListResult" ||
		!reflect.DeepEqual(op.Errors, []int{400, 403, 404, 412, 500, 503, 504}) {
		t.Fatalf("focused operation mismatch: %#v", op)
	}
	gotParameters := make([]string, 0, len(op.QueryParameters))
	for _, parameter := range op.QueryParameters {
		if parameter.Required {
			t.Fatalf("query parameter unexpectedly required: %#v", parameter)
		}
		gotParameters = append(gotParameters, parameter.Name+":"+parameter.Type)
		switch parameter.Name {
		case "page_size":
			if parameter.Format != "int64" || parameter.Minimum == nil ||
				*parameter.Minimum != 0 || parameter.Maximum == nil ||
				*parameter.Maximum != 1000 || parameter.Default != float64(1000) {
				t.Fatalf("page_size projection mismatch: %#v", parameter)
			}
		case "segment_type":
			if !reflect.DeepEqual(parameter.Enum, []string{"DVPortgroup", "ALL"}) {
				t.Fatalf("segment_type enum mismatch: %#v", parameter.Enum)
			}
		case "include_mark_for_delete_objects":
			if parameter.Default != false {
				t.Fatalf("include_mark_for_delete_objects default mismatch: %#v", parameter)
			}
		}
	}
	sort.Strings(gotParameters)
	wantParameters := []string{
		"cursor:string",
		"include_mark_for_delete_objects:boolean",
		"included_fields:string",
		"page_size:integer",
		"segment_type:string",
		"sort_ascending:boolean",
		"sort_by:string",
	}
	if !reflect.DeepEqual(gotParameters, wantParameters) {
		t.Fatalf("query parameters = %v, want %v", gotParameters, wantParameters)
	}

	var sources struct {
		Repository string `json:"repository"`
		Commit     string `json:"repository_commit_sha"`
		License    string `json:"license"`
		SpecPath   string `json:"spec_path"`
		Blob       string `json:"spec_blob_sha"`
		SpecURL    string `json:"spec_url"`
		Derivation string `json:"derivation"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			BasePath    string `json:"base_path"`
			Path        string `json:"path"`
			Commit      string `json:"repository_commit_sha"`
			SpecPath    string `json:"spec_path"`
		} `json:"operations"`
	}
	readJSONFile(t, "../../docs/official_sources.json", &sources)
	if sources.Repository != "https://github.com/vmware/vcf-api-specs" ||
		sources.Commit != pinnedCommit || sources.License != "Apache-2.0" ||
		sources.SpecPath != pinnedSpec || sources.Blob != pinnedBlob ||
		!strings.Contains(sources.SpecURL, pinnedCommit+"/"+pinnedSpec) ||
		!strings.Contains(sources.Derivation, "no rendered documentation page") {
		t.Fatalf("official source metadata mismatch: %#v", sources)
	}
	if len(sources.Operations) != 1 {
		t.Fatalf("official sources have %d operations, want 1", len(sources.Operations))
	}
	sourceOp := sources.Operations[0]
	if sourceOp.OperationID != nsxpolicy.ListAllInfraSegmentsOperation ||
		sourceOp.Method != http.MethodGet || sourceOp.BasePath != "/policy/api/v1" ||
		sourceOp.Path != "/infra/segments" || sourceOp.Commit != pinnedCommit ||
		sourceOp.SpecPath != pinnedSpec {
		t.Fatalf("official operation provenance mismatch: %#v", sourceOp)
	}
}

func TestRefreshResumesInterruptedPageAndSortsEveryTraversal(t *testing.T) {
	const (
		expiredToken = "expired-access-token-078"
		freshToken   = "fresh-access-token-078"
	)
	srv := newMock(t, contractmock.Scenario{
		ExpiredToken: expiredToken,
		FreshToken:   freshToken,
		ExpireOnce:   true,
	})
	source := &recordingTokenSource{
		current: expiredToken,
		fresh:   freshToken,
	}
	callerClient := &http.Client{Timeout: 3 * time.Second}
	client, err := nsxpolicy.NewClient(nsxpolicy.Config{
		BaseURL:     srv.URL + "/",
		TokenSource: source,
		HTTPClient:  callerClient,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if callerClient.CheckRedirect != nil {
		t.Fatal("NewClient mutated the caller-owned HTTP client")
	}

	falseValue := false
	zero := int64(0)
	explicitOptions := nsxpolicy.ListOptions{
		IncludeMarkedForDelete: &falseValue,
		IncludedFields:         stringPointer("id,display_name,path"),
		PageSize:               &zero,
		SegmentType:            stringPointer("ALL"),
		SortAscending:          &falseValue,
		SortBy:                 stringPointer("display_name"),
	}
	first, err := client.ListAllSegments(context.Background(), explicitOptions)
	if err != nil {
		t.Fatalf("first ListAllSegments: %v", err)
	}
	second, err := client.ListAllSegments(context.Background(), nsxpolicy.ListOptions{})
	if err != nil {
		t.Fatalf("second ListAllSegments: %v", err)
	}

	want := []nsxpolicy.Segment{
		{ID: "segment-a", DisplayName: "Alpha", Path: "/infra/segments/alpha"},
		{ID: "segment-b", DisplayName: "Bravo", Path: "/infra/segments/bravo"},
		{ID: "segment-c", DisplayName: "Charlie", Path: "/infra/segments/charlie"},
		{ID: "segment-x", DisplayName: "Xray", Path: "/infra/segments/xray"},
		{ID: "segment-y", DisplayName: "Yankee", Path: "/infra/segments/yankee"},
		{ID: "segment-z", DisplayName: "Zulu", Path: "/infra/segments/zulu"},
	}
	if !reflect.DeepEqual(first, want) {
		t.Fatalf("first collection is not complete and sorted:\n got: %#v\nwant: %#v", first, want)
	}
	if !reflect.DeepEqual(second, want) {
		t.Fatalf("second collection changed with flipped response order:\n got: %#v\nwant: %#v", second, want)
	}

	tokenCalls, refreshes := source.snapshot()
	if tokenCalls != 2 {
		t.Fatalf("Token calls = %d, want one per traversal", tokenCalls)
	}
	if !reflect.DeepEqual(refreshes, []string{expiredToken}) {
		t.Fatalf("Refresh rejected-token arguments = %q, want exactly the expired token", refreshes)
	}

	explicitQuery := "include_mark_for_delete_objects=false&included_fields=id%2Cdisplay_name%2Cpath&page_size=0&segment_type=ALL&sort_ascending=false&sort_by=display_name"
	explicitTwo := "cursor=cursor-two&" + explicitQuery
	explicitThree := "cursor=cursor-three&" + explicitQuery
	wantQueries := []string{
		explicitQuery,
		explicitTwo,
		explicitTwo,
		explicitThree,
		"",
		"cursor=cursor-two",
		"cursor=cursor-three",
	}
	wantTokens := []string{
		expiredToken,
		expiredToken,
		freshToken,
		freshToken,
		freshToken,
		freshToken,
		freshToken,
	}
	wantStatuses := []int{200, 401, 200, 200, 200, 200, 200}
	log := srv.Snapshot()
	if len(log) != len(wantQueries) {
		t.Fatalf("request log has %d entries, want %d: %#v", len(log), len(wantQueries), log)
	}
	for i := range log {
		target := "/policy/api/v1/infra/segments"
		if wantQueries[i] != "" {
			target += "?" + wantQueries[i]
		}
		assertLoggedRequest(t, i, log[i], target, wantQueries[i],
			"Bearer "+wantTokens[i], wantStatuses[i])
	}
	firstPageRequests := 0
	for _, entry := range log[:4] {
		if entry.RawQuery == explicitQuery {
			firstPageRequests++
		}
	}
	if firstPageRequests != 1 {
		t.Fatalf("first page requested %d times during refresh traversal, want 1", firstPageRequests)
	}
}

func TestInputValidationPrecedesTokenAndNetwork(t *testing.T) {
	const (
		expiredToken = "validation-expired-token"
		freshToken   = "validation-fresh-token"
	)
	srv := newMock(t, contractmock.Scenario{
		ExpiredToken: expiredToken,
		FreshToken:   freshToken,
	})
	source := &recordingTokenSource{current: expiredToken, fresh: freshToken}
	client, err := nsxpolicy.NewClient(nsxpolicy.Config{
		BaseURL:     srv.URL,
		TokenSource: source,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	negative := int64(-1)
	tooLarge := int64(1001)
	cancelledContext, cancel := context.WithCancel(context.Background())
	cancel()
	tests := []struct {
		name    string
		ctx     context.Context
		options nsxpolicy.ListOptions
		is      error
	}{
		{name: "nil context", ctx: nil},
		{name: "cancelled context", ctx: cancelledContext, is: context.Canceled},
		{name: "blank included fields", ctx: context.Background(), options: nsxpolicy.ListOptions{IncludedFields: stringPointer("  ")}},
		{name: "padded included fields", ctx: context.Background(), options: nsxpolicy.ListOptions{IncludedFields: stringPointer(" id ")}},
		{name: "negative page size", ctx: context.Background(), options: nsxpolicy.ListOptions{PageSize: &negative}},
		{name: "page size too large", ctx: context.Background(), options: nsxpolicy.ListOptions{PageSize: &tooLarge}},
		{name: "invalid segment type", ctx: context.Background(), options: nsxpolicy.ListOptions{SegmentType: stringPointer("OVERLAY")}},
		{name: "blank sort by", ctx: context.Background(), options: nsxpolicy.ListOptions{SortBy: stringPointer("")}},
	}
	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			_, gotErr := client.ListAllSegments(tt.ctx, tt.options)
			if gotErr == nil {
				t.Fatal("ListAllSegments unexpectedly succeeded")
			}
			if tt.is != nil && !errors.Is(gotErr, tt.is) {
				t.Fatalf("error = %v, want errors.Is(_, %v)", gotErr, tt.is)
			}
		})
	}
	tokenCalls, refreshes := source.snapshot()
	if tokenCalls != 0 || len(refreshes) != 0 {
		t.Fatalf("invalid inputs reached token source: Token=%d Refresh=%q", tokenCalls, refreshes)
	}
	if log := srv.Snapshot(); len(log) != 0 {
		t.Fatalf("invalid inputs reached network: %#v", log)
	}
}

func TestNewClientValidationIsLocalAndCallerClientIsImmutable(t *testing.T) {
	validSource := &recordingTokenSource{current: "current", fresh: "fresh"}
	tests := []struct {
		name   string
		config nsxpolicy.Config
	}{
		{name: "missing origin", config: nsxpolicy.Config{TokenSource: validSource}},
		{name: "relative origin", config: nsxpolicy.Config{BaseURL: "manager.example", TokenSource: validSource}},
		{name: "unsupported scheme", config: nsxpolicy.Config{BaseURL: "ftp://manager.example", TokenSource: validSource}},
		{name: "userinfo", config: nsxpolicy.Config{BaseURL: "https://user@manager.example", TokenSource: validSource}},
		{name: "non-root path", config: nsxpolicy.Config{BaseURL: "https://manager.example/policy/api/v1", TokenSource: validSource}},
		{name: "query", config: nsxpolicy.Config{BaseURL: "https://manager.example?x=1", TokenSource: validSource}},
		{name: "dangling query", config: nsxpolicy.Config{BaseURL: "https://manager.example?", TokenSource: validSource}},
		{name: "fragment", config: nsxpolicy.Config{BaseURL: "https://manager.example#fragment", TokenSource: validSource}},
		{name: "missing token source", config: nsxpolicy.Config{BaseURL: "https://manager.example"}},
	}
	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			if _, err := nsxpolicy.NewClient(tt.config); err == nil {
				t.Fatal("NewClient unexpectedly succeeded")
			}
		})
	}
	tokenCalls, refreshes := validSource.snapshot()
	if tokenCalls != 0 || len(refreshes) != 0 {
		t.Fatalf("configuration validation called token source: Token=%d Refresh=%q", tokenCalls, refreshes)
	}
}

func TestSecondUnauthorizedResponseIsProjectedAPIError(t *testing.T) {
	const expiredToken = "still-expired-token"
	srv := newMock(t, contractmock.Scenario{
		ExpiredToken: expiredToken,
		FreshToken:   "unused-fresh-token",
		ExpireOnce:   true,
	})
	source := &recordingTokenSource{
		current: expiredToken,
		fresh:   expiredToken,
	}
	client, err := nsxpolicy.NewClient(nsxpolicy.Config{
		BaseURL:     srv.URL,
		TokenSource: source,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	_, gotErr := client.ListAllSegments(context.Background(), nsxpolicy.ListOptions{})
	var apiError *nsxpolicy.APIError
	if !errors.As(gotErr, &apiError) {
		t.Fatalf("error = %T %v, want *APIError", gotErr, gotErr)
	}
	if apiError.OperationID != nsxpolicy.ListAllInfraSegmentsOperation ||
		apiError.StatusCode != http.StatusUnauthorized ||
		apiError.ErrorCode == nil || *apiError.ErrorCode != 40102 ||
		apiError.ErrorMessage != "access token expired" ||
		apiError.ModuleName != "common-services" || apiError.Envelope == nil {
		t.Fatalf("API error projection mismatch: %#v", apiError)
	}
	if strings.Contains(apiError.Error(), "expired") ||
		strings.Contains(apiError.Error(), expiredToken) {
		t.Fatalf("APIError.Error disclosed protected text: %q", apiError.Error())
	}
	log := srv.Snapshot()
	if len(log) != 3 || log[0].RawQuery != "" ||
		log[1].RawQuery != "cursor=cursor-two" ||
		log[2].RawQuery != "cursor=cursor-two" {
		t.Fatalf("401 retry did not stay on the interrupted page: %#v", log)
	}
}

func TestTokenErrorsPreserveIdentityWithoutDisclosingText(t *testing.T) {
	sourceFailure := errors.New("issuer failed while handling sensitive-token-text")
	source := &recordingTokenSource{
		current:  "unused-token",
		fresh:    "unused-fresh",
		tokenErr: sourceFailure,
	}
	client, err := nsxpolicy.NewClient(nsxpolicy.Config{
		BaseURL:     "https://manager.example",
		TokenSource: source,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	_, gotErr := client.ListAllSegments(context.Background(), nsxpolicy.ListOptions{})
	var tokenError *nsxpolicy.TokenError
	if !errors.As(gotErr, &tokenError) || !errors.Is(gotErr, sourceFailure) {
		t.Fatalf("error = %T %v, want wrapping *TokenError", gotErr, gotErr)
	}
	if strings.Contains(gotErr.Error(), "sensitive-token-text") {
		t.Fatalf("TokenError disclosed source text: %q", gotErr.Error())
	}
}

type recordingTokenSource struct {
	mu         sync.Mutex
	current    string
	fresh      string
	tokenErr   error
	refreshErr error
	tokenCalls int
	refreshes  []string
}

func (s *recordingTokenSource) Token(context.Context) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.tokenCalls++
	if s.tokenErr != nil {
		return "", s.tokenErr
	}
	return s.current, nil
}

func (s *recordingTokenSource) Refresh(_ context.Context, rejected string) (string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.refreshes = append(s.refreshes, rejected)
	if s.refreshErr != nil {
		return "", s.refreshErr
	}
	s.current = s.fresh
	return s.fresh, nil
}

func (s *recordingTokenSource) snapshot() (int, []string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.tokenCalls, append([]string(nil), s.refreshes...)
}

func newMock(t *testing.T, scenario contractmock.Scenario) *contractmock.Server {
	t.Helper()
	srv, err := contractmock.New("../../docs/contract.json", scenario)
	if err != nil {
		t.Fatalf("start contract mock: %v", err)
	}
	t.Cleanup(func() {
		if err := srv.Close(); err != nil {
			t.Errorf("close contract mock: %v", err)
		}
	})
	return srv
}

func assertLoggedRequest(
	t *testing.T,
	index int,
	got contractmock.LoggedRequest,
	requestURI string,
	rawQuery string,
	authorization string,
	status int,
) {
	t.Helper()
	if got.OperationID != nsxpolicy.ListAllInfraSegmentsOperation ||
		got.Method != http.MethodGet ||
		got.RequestURI != requestURI ||
		got.RawQuery != rawQuery ||
		got.Authorization != authorization ||
		got.Accept != "application/json" ||
		got.ContentType != "" ||
		got.ContentLength != 0 ||
		len(got.TransferEncoding) != 0 ||
		got.Body != "" ||
		got.StatusCode != status {
		t.Fatalf("request %d wire mismatch:\n got: %#v", index, got)
	}
	if rawQuery == "" && strings.Contains(got.RequestURI, "?") {
		t.Fatalf("request %d has a trailing query delimiter: %q", index, got.RequestURI)
	}
}

func readJSONFile(t *testing.T, path string, target any) {
	t.Helper()
	raw, err := osReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(raw, target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func stringPointer(value string) *string {
	return &value
}

// Kept as a variable so the verifier's file reads remain easy to audit.
var osReadFile = func(path string) ([]byte, error) {
	return os.ReadFile(path)
}
