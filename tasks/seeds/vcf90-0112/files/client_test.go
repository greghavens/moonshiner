// Protected acceptance verifier. All HTTP traffic stays on httptest loopback.
package installer_test

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"reflect"
	"testing"

	installer "vcf90installer"
	"vcf90installer/internal/mockinstaller"
)

func TestUpdateDepotSettingsWireShapeAndRetrySafety(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		settings installer.DepotSettings
		wantBody string
	}{
		{
			name: "download token omits every other optional account field",
			settings: installer.DepotSettings{
				VMwareAccount: &installer.DepotAccount{
					DownloadToken: "vcf-download-token-90",
				},
			},
			wantBody: `{"vmwareAccount":{"downloadToken":"vcf-download-token-90"}}`,
		},
		{
			name: "offline credentials omit unused accounts and preserve required false",
			settings: installer.DepotSettings{
				OfflineAccount: &installer.DepotAccount{
					Username: "offline-depot-user",
					Password: "offline-depot-password",
				},
				DepotConfiguration: &installer.DepotConfiguration{
					IsOfflineDepot: false,
					Hostname:       "depot.local.example",
					Port:           443,
				},
			},
			wantBody: `{"offlineAccount":{"username":"offline-depot-user","password":"offline-depot-password"},"depotConfiguration":{"isOfflineDepot":false,"hostname":"depot.local.example","port":443}}`,
		},
		{
			name: "Dell support account exposes every optional account member",
			settings: installer.DepotSettings{
				DellEmcSupportAccount: &installer.DepotAccount{
					Username:      "dell-support-user",
					Password:      "dell-support-password",
					Status:        "DEPOT_CONNECTION_SUCCESSFUL",
					Message:       "connected",
					DownloadToken: "dell-download-token",
				},
			},
			wantBody: `{"dellEmcSupportAccount":{"username":"dell-support-user","password":"dell-support-password","status":"DEPOT_CONNECTION_SUCCESSFUL","message":"connected","downloadToken":"dell-download-token"}}`,
		},
		{
			name: "required configuration members preserve all zero values",
			settings: installer.DepotSettings{
				VMwareAccount: &installer.DepotAccount{
					DownloadToken: "vcf-download-token-90",
				},
				DepotConfiguration: &installer.DepotConfiguration{
					IsOfflineDepot: false,
					Hostname:       "",
					Port:           0,
				},
			},
			wantBody: `{"vmwareAccount":{"downloadToken":"vcf-download-token-90"},"depotConfiguration":{"isOfflineDepot":false,"hostname":"","port":0}}`,
		},
	}

	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			mock := mockinstaller.New()
			t.Cleanup(mock.Close)
			client := installer.NewClient(mock.URL(), mock.Client())

			for call := 1; call <= 2; call++ {
				got, err := client.UpdateDepotSettings(context.Background(), tt.settings)
				if err != nil {
					t.Fatalf("call %d: UpdateDepotSettings: %v", call, err)
				}
				if !reflect.DeepEqual(got, tt.settings) {
					t.Fatalf("call %d response mismatch:\n got: %#v\nwant: %#v", call, got, tt.settings)
				}
			}

			requests := mock.Requests()
			if len(requests) != 2 {
				t.Fatalf("retry should produce exactly two observed PUTs, got %d", len(requests))
			}
			for i, got := range requests {
				if got.OperationID != "updateDepotSettings" {
					t.Errorf("request %d operationId = %q", i+1, got.OperationID)
				}
				if got.Method != http.MethodPut || got.Path != "/v1/system/settings/depot" || got.RawQuery != "" {
					t.Errorf("request %d target = %s %s?%s", i+1, got.Method, got.Path, got.RawQuery)
				}
				if got.ContentType != "application/json" {
					t.Errorf("request %d Content-Type = %q", i+1, got.ContentType)
				}
				assertJSONEqual(t, i+1, got.Body, tt.wantBody)
			}
			if !reflect.DeepEqual(requests[0].Body, requests[1].Body) {
				t.Fatalf("retry changed request bytes: first=%s second=%s", requests[0].Body, requests[1].Body)
			}
			if got := mock.EffectCount(); got != 1 {
				t.Fatalf("identical full-representation PUTs produced %d distinct effects, want 1", got)
			}
		})
	}
}

func TestMockServesOnlyContractOperation(t *testing.T) {
	t.Parallel()
	mock := mockinstaller.New()
	t.Cleanup(mock.Close)

	tests := []struct {
		name   string
		method string
		path   string
		status int
	}{
		{name: "unnamed path", method: http.MethodPut, path: "/v1/tokens", status: http.StatusNotFound},
		{name: "unnamed method", method: http.MethodGet, path: "/v1/system/settings/depot", status: http.StatusMethodNotAllowed},
	}
	for _, tt := range tests {
		req, err := http.NewRequest(tt.method, mock.URL()+tt.path, nil)
		if err != nil {
			t.Fatal(err)
		}
		resp, err := mock.Client().Do(req)
		if err != nil {
			t.Fatalf("%s: %v", tt.name, err)
		}
		_, _ = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()
		if resp.StatusCode != tt.status {
			t.Errorf("%s status = %d, want %d", tt.name, resp.StatusCode, tt.status)
		}
	}
	if got := len(mock.Requests()); got != 0 {
		t.Fatalf("unsupported requests entered named-operation log: %d", got)
	}
}

func TestUpdateDepotSettingsRejectsNon202AndMalformedSuccess(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name   string
		status int
		body   string
	}{
		{name: "documented bad request", status: http.StatusBadRequest, body: `{"errorCode":"INVALID_DEPOT_SETTINGS"}`},
		{name: "documented server error", status: http.StatusInternalServerError, body: `{"errorCode":"VCF_SYSTEM_ERROR"}`},
		{name: "undocumented OK response", status: http.StatusOK, body: `{}`},
		{name: "malformed accepted body", status: http.StatusAccepted, body: `{"vmwareAccount":`},
		{name: "empty accepted body", status: http.StatusAccepted, body: ``},
		{name: "null accepted body", status: http.StatusAccepted, body: `null`},
	}
	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			mock := mockinstaller.New()
			t.Cleanup(mock.Close)
			mock.SetResponse(tt.status, []byte(tt.body))
			client := installer.NewClient(mock.URL(), mock.Client())
			_, err := client.UpdateDepotSettings(context.Background(), installer.DepotSettings{
				VMwareAccount: &installer.DepotAccount{DownloadToken: "vcf-download-token-90"},
			})
			if err == nil {
				t.Fatal("UpdateDepotSettings returned nil error")
			}
			if got := len(mock.Requests()); got != 1 {
				t.Fatalf("request log length = %d, want 1", got)
			}
		})
	}
}

func assertJSONEqual(t *testing.T, requestNumber int, got []byte, want string) {
	t.Helper()
	var gotValue, wantValue any
	if err := json.Unmarshal(got, &gotValue); err != nil {
		t.Errorf("request %d body is not valid JSON: %v\nbody: %s", requestNumber, err, got)
		return
	}
	if err := json.Unmarshal([]byte(want), &wantValue); err != nil {
		t.Fatalf("invalid verifier wantBody: %v", err)
	}
	if !reflect.DeepEqual(gotValue, wantValue) {
		t.Errorf("request %d body:\n got: %s\nwant: %s", requestNumber, got, want)
	}
}

func TestPinnedSpecificationProvenance(t *testing.T) {
	t.Parallel()

	type source struct {
		Path      string `json:"path"`
		Tag       string `json:"tag"`
		CommitSHA string `json:"commit_sha"`
		Version   string `json:"version"`
	}
	type operation struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	}
	var contract struct {
		Source     source      `json:"source"`
		Operations []operation `json:"operations"`
	}
	readJSON(t, "docs/contract.json", &contract)

	wantSource := source{
		Path:      "specifications/vcf-installer/vcf-installer-openapi.json",
		Tag:       "9.0.0.0",
		CommitSHA: "85151f6b1bb58f13b6ac0304bfec53904bea085f",
		Version:   "9.0.0.0",
	}
	if contract.Source != wantSource {
		t.Fatalf("contract source = %#v, want %#v", contract.Source, wantSource)
	}
	wantOperations := []operation{{
		OperationID: "updateDepotSettings",
		Method:      http.MethodPut,
		Path:        "/v1/system/settings/depot",
	}}
	if !reflect.DeepEqual(contract.Operations, wantOperations) {
		t.Fatalf("contract operations = %#v, want %#v", contract.Operations, wantOperations)
	}

	var sources struct {
		Specification struct {
			Path         string   `json:"path"`
			Tag          string   `json:"tag"`
			TagCommitSHA string   `json:"tag_commit_sha"`
			OperationIDs []string `json:"operationIds"`
		} `json:"specification"`
	}
	readJSON(t, "docs/official_sources.json", &sources)
	if sources.Specification.Path != wantSource.Path ||
		sources.Specification.Tag != wantSource.Tag ||
		sources.Specification.TagCommitSHA != wantSource.CommitSHA ||
		!reflect.DeepEqual(sources.Specification.OperationIDs, []string{"updateDepotSettings"}) {
		t.Fatalf("official source does not pin the exact 9.0 operation: %#v", sources.Specification)
	}
}

func readJSON(t *testing.T, path string, dst any) {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(raw, dst); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}
