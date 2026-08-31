package nsxpolicy

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"sort"
	"strings"
	"testing"

	"vcf91-0074/internal/contractmock"
)

const (
	pinnedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	pinnedSpec   = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
)

func TestOfficialSpecificationProvenance(t *testing.T) {
	t.Parallel()

	var contract struct {
		Swagger  string `json:"swagger"`
		BasePath string `json:"basePath"`
		Source   struct {
			Commit     string `json:"repository_commit_sha"`
			Blob       string `json:"spec_blob_sha"`
			Path       string `json:"spec_path"`
			License    string `json:"license"`
			Derivation string `json:"derivation"`
		} `json:"source"`
		Operations map[string]struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operations"`
	}
	readJSONFile(t, "docs/contract.json", &contract)
	if contract.Swagger != "2.0" || contract.BasePath != "/policy/api/v1" {
		t.Fatalf("unexpected OpenAPI projection metadata: %#v", contract)
	}
	if contract.Source.Commit != pinnedCommit || contract.Source.Path != pinnedSpec ||
		contract.Source.Blob != "102d15fd342f6a45bb6d84a5b39a916c65929f4c" ||
		contract.Source.License != "Apache-2.0" ||
		!strings.Contains(contract.Source.Derivation, "no rendered documentation page") {
		t.Fatalf("contract source is not the pinned specification: %#v", contract.Source)
	}
	wantOps := map[string]struct {
		method string
		path   string
	}{
		ReadInfraSegmentOperation:            {http.MethodGet, "/infra/segments/{segment-id}"},
		CreateOrReplaceInfraSegmentOperation: {http.MethodPut, "/infra/segments/{segment-id}"},
	}
	if len(contract.Operations) != len(wantOps) {
		t.Fatalf("contract has %d operations, want %d", len(contract.Operations), len(wantOps))
	}
	for id, want := range wantOps {
		got, ok := contract.Operations[id]
		if !ok || got.OperationID != id || got.Method != want.method || got.Path != want.path {
			t.Fatalf("operation %s mismatch: %#v", id, got)
		}
	}

	var sources struct {
		Commit     string `json:"repository_commit_sha"`
		SpecPath   string `json:"spec_path"`
		SpecURL    string `json:"spec_url"`
		Derivation string `json:"derivation"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
			Commit      string `json:"repository_commit_sha"`
			SpecPath    string `json:"spec_path"`
		} `json:"operations"`
	}
	readJSONFile(t, "docs/official_sources.json", &sources)
	if sources.Commit != pinnedCommit || sources.SpecPath != pinnedSpec ||
		!strings.Contains(sources.SpecURL, pinnedCommit+"/"+pinnedSpec) ||
		!strings.Contains(sources.Derivation, "no rendered documentation page") {
		t.Fatalf("official source metadata mismatch: %#v", sources)
	}
	if len(sources.Operations) != 2 {
		t.Fatalf("official sources have %d operations, want 2", len(sources.Operations))
	}
	for _, op := range sources.Operations {
		want, ok := wantOps[op.OperationID]
		if !ok || op.Method != want.method || op.Path != want.path ||
			op.Commit != pinnedCommit || op.SpecPath != pinnedSpec {
			t.Fatalf("official operation provenance mismatch: %#v", op)
		}
	}
}

func TestEnableSegmentExactWireShape(t *testing.T) {
	tests := []struct {
		name            string
		description     *string
		wantDescription string
	}{
		{
			name:            "unset optional description preserves the current value",
			description:     nil,
			wantDescription: "existing description",
		},
		{
			name:            "explicit description is present",
			description:     stringPointer("enable after maintenance"),
			wantDescription: "enable after maintenance",
		},
		{
			name:            "multi-byte description at the unicode limit is accepted",
			description:     stringPointer(strings.Repeat("é", 1024)),
			wantDescription: strings.Repeat("é", 1024),
		},
	}

	for i, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			segmentID := fmt.Sprintf("seg blue/canary?%d", i+1)
			revision := int32(41 + i)
			connectivity := fmt.Sprintf("/infra/tier-1s/gateway-%d", i+1)
			username := fmt.Sprintf("operator-%d", i+1)
			password := fmt.Sprintf("secret-%d", i+1)
			readBody := segmentDocument(segmentID, revision, connectivity, "DOWN", "NOT_PROTECTED")
			srv := newMock(t, contractmock.Scenario{
				SegmentID: segmentID,
				ReadBody:  readBody,
				PutStatus: http.StatusOK,
			})

			httpClient := &http.Client{}
			client, err := NewClient(Config{
				BaseURL:    srv.URL,
				Username:   username,
				Password:   password,
				HTTPClient: httpClient,
			})
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			if httpClient.CheckRedirect != nil {
				t.Fatal("NewClient mutated caller-owned HTTP client")
			}

			result, err := client.EnableSegment(context.Background(), segmentID, EnableRequest{
				ExpectedRevision:         revision,
				ExpectedConnectivityPath: connectivity,
				Description:              tt.description,
			})
			if err != nil {
				t.Fatalf("EnableSegment: %v", err)
			}
			wantResult := Result{
				SegmentID:           segmentID,
				Revision:            revision,
				PreviousAdminState:  "DOWN",
				AdminState:          "UP",
				Changed:             true,
				ReadOperationID:     ReadInfraSegmentOperation,
				MutationOperationID: CreateOrReplaceInfraSegmentOperation,
			}
			if !reflect.DeepEqual(result, wantResult) {
				t.Fatalf("result mismatch:\n got: %#v\nwant: %#v", result, wantResult)
			}

			log := readLog(t, srv.LogPath)
			if len(log) != 2 {
				t.Fatalf("got %d requests, want 2: %#v", len(log), log)
			}
			target := "/policy/api/v1/infra/segments/" + url.PathEscape(segmentID)
			wantAuth := "Basic " + base64.StdEncoding.EncodeToString([]byte(username+":"+password))
			assertLoggedRequest(t, log[0], contractmock.ReadOperation, http.MethodGet, target, wantAuth, "", "")
			assertLoggedRequest(t, log[1], contractmock.PutOperation, http.MethodPut, target, wantAuth, "application/json", log[1].Body)
			if log[0].ContentLength != 0 || len(log[0].TransferEncoding) != 0 {
				t.Fatalf("GET carried framing for a body: %#v", log[0])
			}
			if log[1].ContentLength != int64(len(log[1].Body)) || len(log[1].TransferEncoding) != 0 {
				t.Fatalf("PUT body framing mismatch: %#v", log[1])
			}
			var gotBody map[string]any
			if err := json.Unmarshal([]byte(log[1].Body), &gotBody); err != nil {
				t.Fatalf("decode PUT body: %v", err)
			}
			wantBody := map[string]any{
				"_revision":           revision,
				"admin_state":         "UP",
				"advanced_config":     map[string]any{"address_pool_paths": []any{"/infra/ip-pools/pool-1"}},
				"connectivity_path":   connectivity,
				"description":         tt.wantDescription,
				"display_name":        "runtime fixture segment",
				"id":                  segmentID,
				"replication_mode":    "MTEP",
				"resource_type":       "Segment",
				"subnets":             []any{map[string]any{"gateway_address": "192.0.2.1/24"}},
				"tags":                []any{map[string]any{"scope": "environment", "tag": "validation"}},
				"transport_zone_path": "/infra/sites/default/enforcement-points/default/transport-zones/tz-overlay",
			}
			wantJSON, err := json.Marshal(wantBody)
			if err != nil {
				t.Fatal(err)
			}
			var normalizedWant map[string]any
			if err := json.Unmarshal(wantJSON, &normalizedWant); err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(gotBody, normalizedWant) {
				t.Fatalf("PUT did not preserve the complete writable segment:\n got: %#v\nwant: %#v", gotBody, normalizedWant)
			}
			for _, forbidden := range []string{
				"_create_time", "_create_user", "_last_modified_time", "_last_modified_user",
				"_links", "_protection", "_system_owned", "marked_for_delete", "overridden",
				"owner_id", "origin_site_id", "parent_path", "path", "realization_id",
				"relative_path", "remote_path", "unique_id",
			} {
				if _, found := gotBody[forbidden]; found {
					t.Fatalf("server-owned field %q was sent in %s", forbidden, log[1].Body)
				}
			}
			if srv.Effects() != 1 {
				t.Fatalf("mutation effects = %d, want 1", srv.Effects())
			}
		})
	}
}

func TestPostReadRaceReturnsVersionConflict(t *testing.T) {
	const (
		segmentID    = "race-segment"
		revision     = int32(7)
		connectivity = "/infra/tier-1s/race-gateway"
	)
	srv := newMock(t, contractmock.Scenario{
		SegmentID:     segmentID,
		ReadBody:      segmentDocument(segmentID, revision, connectivity, "DOWN", "NOT_PROTECTED"),
		RaceAfterRead: true,
	})
	client, err := NewClient(Config{BaseURL: srv.URL, Username: "race-user", Password: "race-password"})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	_, err = client.EnableSegment(context.Background(), segmentID, EnableRequest{
		ExpectedRevision:         revision,
		ExpectedConnectivityPath: connectivity,
	})
	var apiErr *APIError
	if !errors.As(err, &apiErr) || apiErr.OperationID != CreateOrReplaceInfraSegmentOperation ||
		apiErr.StatusCode != http.StatusPreconditionFailed || apiErr.ErrorCode == nil || *apiErr.ErrorCode != 500071 {
		t.Fatalf("race error = %#v, want native 412 version conflict", err)
	}
	log := readLog(t, srv.LogPath)
	if len(log) != 2 || log[0].OperationID != contractmock.ReadOperation ||
		log[1].OperationID != contractmock.PutOperation || log[1].Method != http.MethodPut {
		t.Fatalf("race request log = %#v, want one GET followed by one PUT", log)
	}
	if srv.Effects() != 0 {
		t.Fatalf("stale replacement produced %d mutation effects", srv.Effects())
	}
}

func TestFailedPrecheckNeverMutates(t *testing.T) {
	const (
		segmentID    = "guarded segment/one"
		revision     = int32(17)
		connectivity = "/infra/tier-1s/expected"
	)
	tests := []struct {
		name       string
		status     int
		body       []byte
		wantKind   string
		checkError func(*testing.T, error)
	}{
		{
			name:     "identity mismatch",
			body:     segmentDocument("other", revision, connectivity, "DOWN", "NOT_PROTECTED"),
			wantKind: "precheck",
		},
		{
			name:     "revision mismatch",
			body:     segmentDocument(segmentID, revision+1, connectivity, "DOWN", "NOT_PROTECTED"),
			wantKind: "precheck",
		},
		{
			name:     "connectivity mismatch",
			body:     segmentDocument(segmentID, revision, "/infra/tier-1s/other", "DOWN", "NOT_PROTECTED"),
			wantKind: "precheck",
		},
		{
			name:     "already up is not accepted",
			body:     segmentDocument(segmentID, revision, connectivity, "UP", "NOT_PROTECTED"),
			wantKind: "precheck",
		},
		{
			name:     "protected segment",
			body:     segmentDocument(segmentID, revision, connectivity, "DOWN", "PROTECTED"),
			wantKind: "precheck",
		},
		{
			name:     "missing admin state",
			body:     []byte(`{"id":"guarded segment/one","_revision":17,"connectivity_path":"/infra/tier-1s/expected","_protection":"NOT_PROTECTED"}`),
			wantKind: "protocol",
		},
		{
			name:     "wrong revision type",
			body:     []byte(`{"id":"guarded segment/one","_revision":"17","connectivity_path":"/infra/tier-1s/expected","admin_state":"DOWN","_protection":"NOT_PROTECTED"}`),
			wantKind: "protocol",
		},
		{
			name:     "trailing success data",
			body:     append(segmentDocument(segmentID, revision, connectivity, "DOWN", "NOT_PROTECTED"), []byte(` {}`)...),
			wantKind: "protocol",
		},
		{
			name:     "http precondition failure",
			status:   http.StatusPreconditionFailed,
			body:     []byte(`{"error_code":93017,"error_message":"server-marker-should-stay-out-of-error-text","module_name":"Policy","details":"revision changed"}`),
			wantKind: "api",
			checkError: func(t *testing.T, err error) {
				t.Helper()
				var apiErr *APIError
				if !errors.As(err, &apiErr) {
					t.Fatalf("error type = %T, want *APIError", err)
				}
				if apiErr.OperationID != ReadInfraSegmentOperation ||
					apiErr.StatusCode != http.StatusPreconditionFailed ||
					apiErr.ErrorCode == nil || *apiErr.ErrorCode != 93017 ||
					apiErr.ErrorMessage != "server-marker-should-stay-out-of-error-text" ||
					apiErr.ModuleName != "Policy" || apiErr.Details != "revision changed" {
					t.Fatalf("APIError did not preserve contract fields: %#v", apiErr)
				}
				envelope, ok := apiErr.Envelope.(map[string]any)
				if !ok || envelope["details"] != "revision changed" {
					t.Fatalf("APIError did not preserve complete envelope: %#v", apiErr.Envelope)
				}
				if strings.Contains(err.Error(), "server-marker") ||
					strings.Contains(err.Error(), "revision changed") {
					t.Fatalf("error text leaked response data: %q", err)
				}
			},
		},
	}

	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			srv := newMock(t, contractmock.Scenario{
				SegmentID:  segmentID,
				ReadStatus: tt.status,
				ReadBody:   tt.body,
			})
			client, err := NewClient(Config{
				BaseURL:  srv.URL,
				Username: "failure-user",
				Password: "failure-password",
			})
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			_, err = client.EnableSegment(context.Background(), segmentID, EnableRequest{
				ExpectedRevision:         revision,
				ExpectedConnectivityPath: connectivity,
			})
			if err == nil {
				t.Fatal("EnableSegment unexpectedly succeeded")
			}
			switch tt.wantKind {
			case "precheck":
				var target *PrecheckError
				if !errors.As(err, &target) {
					t.Fatalf("error type = %T, want *PrecheckError", err)
				}
			case "protocol":
				var target *ProtocolError
				if !errors.As(err, &target) {
					t.Fatalf("error type = %T, want *ProtocolError", err)
				}
			case "api":
				var target *APIError
				if !errors.As(err, &target) {
					t.Fatalf("error type = %T, want *APIError", err)
				}
			default:
				t.Fatalf("bad test kind %q", tt.wantKind)
			}
			if tt.checkError != nil {
				tt.checkError(t, err)
			}
			if strings.Contains(err.Error(), "failure-user") ||
				strings.Contains(err.Error(), "failure-password") {
				t.Fatalf("error leaked credentials: %q", err)
			}
			log := readLog(t, srv.LogPath)
			if len(log) != 1 || log[0].OperationID != contractmock.ReadOperation ||
				log[0].Method != http.MethodGet {
				t.Fatalf("failed precheck request log = %#v, want exactly one GET", log)
			}
			if srv.Effects() != 0 {
				t.Fatalf("failed precheck produced %d mutation effects", srv.Effects())
			}
		})
	}
}

func TestLocalValidationMakesNoRequests(t *testing.T) {
	longDescription := strings.Repeat("é", 1025)
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	tests := []struct {
		name      string
		ctx       context.Context
		segmentID string
		request   EnableRequest
		wantIs    error
	}{
		{
			name:      "nil context",
			ctx:       nil,
			segmentID: "segment",
			request:   EnableRequest{ExpectedConnectivityPath: "/infra/tier-1s/t1"},
		},
		{
			name:      "cancelled context",
			ctx:       cancelled,
			segmentID: "segment",
			request:   EnableRequest{ExpectedConnectivityPath: "/infra/tier-1s/t1"},
			wantIs:    context.Canceled,
		},
		{
			name:      "blank segment id",
			ctx:       context.Background(),
			segmentID: " \t",
			request:   EnableRequest{ExpectedConnectivityPath: "/infra/tier-1s/t1"},
		},
		{
			name:      "negative revision",
			ctx:       context.Background(),
			segmentID: "segment",
			request:   EnableRequest{ExpectedRevision: -1, ExpectedConnectivityPath: "/infra/tier-1s/t1"},
		},
		{
			name:      "blank connectivity",
			ctx:       context.Background(),
			segmentID: "segment",
			request:   EnableRequest{ExpectedConnectivityPath: " "},
		},
		{
			name:      "blank explicit description",
			ctx:       context.Background(),
			segmentID: "segment",
			request:   EnableRequest{ExpectedConnectivityPath: "/infra/tier-1s/t1", Description: stringPointer(" ")},
		},
		{
			name:      "description exceeds unicode limit",
			ctx:       context.Background(),
			segmentID: "segment",
			request:   EnableRequest{ExpectedConnectivityPath: "/infra/tier-1s/t1", Description: &longDescription},
		},
	}

	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			srv := newMock(t, contractmock.Scenario{
				SegmentID: "segment",
				ReadBody:  segmentDocument("segment", 0, "/infra/tier-1s/t1", "DOWN", "NOT_PROTECTED"),
			})
			client, err := NewClient(Config{
				BaseURL:  srv.URL,
				Username: "local-user",
				Password: "local-password",
			})
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			_, err = client.EnableSegment(tt.ctx, tt.segmentID, tt.request)
			if err == nil {
				t.Fatal("invalid input unexpectedly succeeded")
			}
			if tt.wantIs != nil && !errors.Is(err, tt.wantIs) {
				t.Fatalf("errors.Is(%v, %v) = false", err, tt.wantIs)
			}
			if got := readLog(t, srv.LogPath); len(got) != 0 {
				t.Fatalf("local validation sent requests: %#v", got)
			}
			if srv.Effects() != 0 {
				t.Fatalf("local validation produced %d effects", srv.Effects())
			}
		})
	}
}

func TestNewClientValidation(t *testing.T) {
	tests := []struct {
		name    string
		config  Config
		wantErr bool
	}{
		{name: "valid root", config: Config{BaseURL: "http://127.0.0.1:1/", Username: "user", Password: "pass"}},
		{name: "valid https origin", config: Config{BaseURL: "https://nsx.example.com", Username: "user", Password: "pass"}},
		{name: "blank username", config: Config{BaseURL: "http://127.0.0.1:1", Username: " ", Password: "pass"}, wantErr: true},
		{name: "blank password", config: Config{BaseURL: "http://127.0.0.1:1", Username: "user", Password: ""}, wantErr: true},
		{name: "colon in username", config: Config{BaseURL: "http://127.0.0.1:1", Username: "user:name", Password: "pass"}, wantErr: true},
		{name: "wrong scheme", config: Config{BaseURL: "ftp://127.0.0.1", Username: "user", Password: "pass"}, wantErr: true},
		{name: "missing host", config: Config{BaseURL: "http://", Username: "user", Password: "pass"}, wantErr: true},
		{name: "embedded credentials", config: Config{BaseURL: "http://u:p@127.0.0.1", Username: "user", Password: "pass"}, wantErr: true},
		{name: "non-root path", config: Config{BaseURL: "http://127.0.0.1/policy/api/v1", Username: "user", Password: "pass"}, wantErr: true},
		{name: "query", config: Config{BaseURL: "http://127.0.0.1?x=1", Username: "user", Password: "pass"}, wantErr: true},
		{name: "dangling query", config: Config{BaseURL: "http://127.0.0.1?", Username: "user", Password: "pass"}, wantErr: true},
		{name: "fragment", config: Config{BaseURL: "http://127.0.0.1#x", Username: "user", Password: "pass"}, wantErr: true},
	}
	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			_, err := NewClient(tt.config)
			if (err != nil) != tt.wantErr {
				t.Fatalf("NewClient error = %v, wantErr %v", err, tt.wantErr)
			}
			if err != nil {
				for _, secret := range []string{tt.config.Username, tt.config.Password} {
					if strings.TrimSpace(secret) != "" && strings.Contains(err.Error(), secret) {
						t.Fatalf("configuration error leaked credentials: %q", err)
					}
				}
			}
		})
	}
}

func assertLoggedRequest(t *testing.T, got contractmock.LoggedRequest, operationID, method, target, authorization, contentType, body string) {
	t.Helper()
	if got.OperationID != operationID || got.Method != method || got.RequestURI != target ||
		got.Authorization != authorization || got.Accept != "application/json" ||
		got.ContentType != contentType || got.Body != body {
		t.Fatalf("wire request mismatch:\n got: %#v\nwant operation=%q method=%q target=%q auth=%q accept=application/json content-type=%q body=%q",
			got, operationID, method, target, authorization, contentType, body)
	}
}

func newMock(t *testing.T, scenario contractmock.Scenario) *contractmock.Server {
	t.Helper()
	srv, err := contractmock.New("docs/contract.json", t.TempDir()+"/requests.jsonl", scenario)
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

func readLog(t *testing.T, path string) []contractmock.LoggedRequest {
	t.Helper()
	log, err := contractmock.ReadLog(path)
	if err != nil {
		t.Fatalf("read request log: %v", err)
	}
	return log
}

func readJSONFile(t *testing.T, path string, dst any) {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(raw, dst); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func segmentDocument(id string, revision int32, connectivity, adminState, protection string) []byte {
	raw, err := json.Marshal(map[string]any{
		"resource_type":       "Segment",
		"id":                  id,
		"display_name":        "runtime fixture segment",
		"_revision":           revision,
		"connectivity_path":   connectivity,
		"admin_state":         adminState,
		"_protection":         protection,
		"description":         "existing description",
		"transport_zone_path": "/infra/sites/default/enforcement-points/default/transport-zones/tz-overlay",
		"subnets":             []any{map[string]any{"gateway_address": "192.0.2.1/24"}},
		"replication_mode":    "MTEP",
		"advanced_config":     map[string]any{"address_pool_paths": []any{"/infra/ip-pools/pool-1"}},
		"tags":                []any{map[string]any{"scope": "environment", "tag": "validation"}},
		"_create_time":        1720000000000,
		"_create_user":        "admin",
		"_last_modified_time": 1720000001000,
		"_last_modified_user": "admin",
		"_links":              []any{},
		"_system_owned":       false,
		"marked_for_delete":   false,
		"overridden":          false,
		"owner_id":            "owner",
		"origin_site_id":      "site",
		"parent_path":         "/infra",
		"path":                "/infra/segments/" + id,
		"realization_id":      "realization",
		"relative_path":       id,
		"remote_path":         "",
		"unique_id":           "unique",
	})
	if err != nil {
		panic(err)
	}
	return raw
}

func jsonKeys(t *testing.T, raw []byte) []string {
	t.Helper()
	var object map[string]json.RawMessage
	if err := json.Unmarshal(raw, &object); err != nil {
		t.Fatalf("decode JSON object: %v", err)
	}
	keys := make([]string, 0, len(object))
	for key := range object {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func stringPointer(value string) *string {
	return &value
}
