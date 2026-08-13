// Package verification holds the protected, deterministic checks for this task.
// It drives the sddclcm client against the in-process contract fixture and
// asserts the exact wire shape of every request. No live VMware endpoint is
// contacted.
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
	"strings"
	"sync"
	"testing"
	"time"

	"vcf/lcm/internal/contractmock"
	"vcf/lcm/sddclcm"
)

const (
	specCommit    = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	specPath      = "specifications/sddc-lcm/sddc-lcm-openapi.yaml"
	specVersion   = "9.1.0.0"
	componentID   = "af6ef462-e192-4fe1-9522-67a50a2b3392"
	correlationID = "f0e9d8c7-b6a5-4321-fedc-ba9876543210"
	bearerToken   = "eyJhbGciOiJIUzI1NiJ9.sddc-lcm.signature"
	targetVersion = "9.1.0.0000.1234567"
	depotURL      = "https://fds.vsphere.com/component-x/v9.1.0/upgrade-manifest"
	depotCert     = "LS0tLS1CRUdJTiBDRVJUSUZJQ0FURQ=="
)

func minimalSpec() sddclcm.UpgradeSpec {
	return sddclcm.UpgradeSpec{
		ComponentSpec: sddclcm.ComponentDesiredSpec{
			Software: sddclcm.SoftwareSpec{Version: targetVersion},
			Depot:    sddclcm.DepotSpec{URL: depotURL},
		},
		CorrelationID: correlationID,
	}
}

const minimalBody = `{"componentSpec":{"software":{"version":"9.1.0.0000.1234567"},` +
	`"depot":{"url":"https://fds.vsphere.com/component-x/v9.1.0/upgrade-manifest"}},` +
	`"correlationId":"f0e9d8c7-b6a5-4321-fedc-ba9876543210"}`

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func response(status int, body string) *http.Response {
	return &http.Response{
		StatusCode: status,
		Header:     http.Header{"Content-Type": []string{"application/json"}},
		Body:       io.NopCloser(strings.NewReader(body)),
	}
}

type changingMarshaler struct {
	calls int
}

func (m *changingMarshaler) MarshalJSON() ([]byte, error) {
	m.calls++
	return []byte(fmt.Sprintf(`{"encoding":%d}`, m.calls)), nil
}

func newClient(t *testing.T, mock *contractmock.Mock, opts ...sddclcm.Option) *sddclcm.Client {
	t.Helper()
	base := []sddclcm.Option{
		sddclcm.WithHTTPClient(mock.HTTPClient()),
		sddclcm.WithMaxAttempts(4),
		sddclcm.WithRetryBackoff(func(int) time.Duration { return 0 }),
		sddclcm.WithPollInterval(0),
	}
	client, err := sddclcm.NewClient(mock.BaseURL(), bearerToken, append(base, opts...)...)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if client == nil {
		t.Fatal("NewClient returned a nil client and a nil error")
	}
	return client
}

func repoRoot(t *testing.T) string {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	return filepath.Dir(wd)
}

func readJSON(t *testing.T, path string) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	out := map[string]any{}
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
	return out
}

func dig(t *testing.T, doc map[string]any, keys ...string) any {
	t.Helper()
	var cur any = doc
	for _, key := range keys {
		obj, ok := cur.(map[string]any)
		if !ok {
			t.Fatalf("%s: not an object", strings.Join(keys, "."))
		}
		cur, ok = obj[key]
		if !ok {
			t.Fatalf("%s: missing", strings.Join(keys, "."))
		}
	}
	return cur
}

func TestContractIsPinnedToTheOfficialSpecification(t *testing.T) {
	root := repoRoot(t)
	contract := readJSON(t, filepath.Join(root, "docs", "contract.json"))
	sources := readJSON(t, filepath.Join(root, "docs", "official_sources.json"))

	for _, tc := range []struct {
		name string
		got  any
		want any
	}{
		{"contract commit", dig(t, contract, "source", "commit"), specCommit},
		{"contract spec path", dig(t, contract, "source", "path"), specPath},
		{"contract api version", dig(t, contract, "source", "api_version"), specVersion},
		{"contract openapi version", dig(t, contract, "source", "openapi"), "3.0.4"},
		{"contract license", dig(t, contract, "source", "repository_license"), "Apache-2.0"},
		{"sources commit", sources["repository_commit_sha"], specCommit},
		{"sources spec path", sources["spec_path"], specPath},
		{"sources spec version", sources["spec_version"], specVersion},
		{"sources license", sources["repository_license"], "Apache-2.0"},
		{"sources repository", sources["repository"], "https://github.com/vmware/vcf-api-specs"},
	} {
		if tc.got != tc.want {
			t.Errorf("%s = %v, want %v", tc.name, tc.got, tc.want)
		}
	}

	wantOps := map[string][2]string{
		"performComponentAction": {"POST", "/v1/components/{componentId}"},
		"getTask":                {"GET", "/v1/tasks/{taskId}"},
	}
	gotOps := map[string][2]string{}
	for _, entry := range contract["operations"].([]any) {
		op := entry.(map[string]any)
		gotOps[op["operationId"].(string)] = [2]string{op["method"].(string), op["path"].(string)}
	}
	if fmt.Sprint(gotOps) != fmt.Sprint(wantOps) {
		t.Errorf("contract operations = %v, want %v", gotOps, wantOps)
	}

	gotSourceOps := map[string]bool{}
	for _, entry := range sources["operations"].([]any) {
		gotSourceOps[entry.(map[string]any)["operationId"].(string)] = true
	}
	for id := range wantOps {
		if !gotSourceOps[id] {
			t.Errorf("official_sources.json does not record operationId %q", id)
		}
	}
	if len(gotSourceOps) != len(wantOps) {
		t.Errorf("official_sources.json records %d operations, want %d", len(gotSourceOps), len(wantOps))
	}
}

func TestFixtureIsBoundToTheContract(t *testing.T) {
	mock := contractmock.Start(t, contractmock.Options{})
	commit, path := mock.ContractSource()
	if commit != specCommit || path != specPath {
		t.Fatalf("fixture pinned to %s %s, want %s %s", commit, path, specCommit, specPath)
	}
	if got := len(mock.ContractOperations()); got != 2 {
		t.Fatalf("fixture serves %d operations, want 2", got)
	}

	for _, tc := range []struct {
		name   string
		method string
		path   string
	}{
		{"collection create is not in the contract", http.MethodPost, "/v1/components"},
		{"depot configuration is not in the contract", http.MethodPost, "/v1/depot"},
		{"task list is not in the contract", http.MethodGet, "/v1/tasks"},
		{"component read is not in the contract", http.MethodGet, "/v1/components/" + componentID},
	} {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(tc.method, mock.BaseURL()+tc.path, strings.NewReader("{}"))
			if err != nil {
				t.Fatalf("new request: %v", err)
			}
			resp, err := mock.HTTPClient().Do(req)
			if err != nil {
				t.Fatalf("do: %v", err)
			}
			defer func() { _ = resp.Body.Close() }()
			if resp.StatusCode != http.StatusNotFound {
				t.Fatalf("status = %d, want 404", resp.StatusCode)
			}
		})
	}
}

func TestNewClientRejectsMissingConnectionInputs(t *testing.T) {
	for _, tc := range []struct {
		name    string
		baseURL string
		token   string
	}{
		{"empty base URL", "", bearerToken},
		{"blank base URL", " \t ", bearerToken},
		{"empty bearer token", "http://contractmock.local", ""},
		{"blank bearer token", "http://contractmock.local", " \t "},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if client, err := sddclcm.NewClient(tc.baseURL, tc.token); err == nil || client != nil {
				t.Fatalf("NewClient(%q, token) = (%v, %v), want (nil, error)", tc.baseURL, client, err)
			}
		})
	}
}

func TestIdentifiersAreEncodedAsSinglePathSegments(t *testing.T) {
	const (
		specialComponent = "rack/edge ?#%"
		specialTask      = "task/edge ?#%"
	)
	var requests []*http.Request
	transport := roundTripFunc(func(req *http.Request) (*http.Response, error) {
		requests = append(requests, req)
		if req.Method == http.MethodPost {
			return response(http.StatusAccepted, `{"id":"`+specialTask+`","status":"PENDING"}`), nil
		}
		return response(http.StatusOK, `{"id":"`+specialTask+`","status":"RUNNING"}`), nil
	})
	client, err := sddclcm.NewClient("http://contractmock.local/", bearerToken,
		sddclcm.WithHTTPClient(&http.Client{Transport: transport}),
		sddclcm.WithMaxAttempts(1),
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	if _, err := client.ApplyComponentUpgrade(context.Background(), specialComponent, minimalSpec()); err != nil {
		t.Fatalf("ApplyComponentUpgrade: %v", err)
	}
	if _, err := client.GetTask(context.Background(), specialTask); err != nil {
		t.Fatalf("GetTask: %v", err)
	}
	if len(requests) != 2 {
		t.Fatalf("requests = %d, want 2", len(requests))
	}
	if got, want := requests[0].URL.EscapedPath(), "/v1/components/rack%2Fedge%20%3F%23%25"; got != want {
		t.Errorf("apply escaped path = %q, want %q", got, want)
	}
	if got := requests[0].URL.RawQuery; got != "action=apply" {
		t.Errorf("apply query = %q, want action=apply", got)
	}
	if got, want := requests[1].URL.EscapedPath(), "/v1/tasks/task%2Fedge%20%3F%23%25"; got != want {
		t.Errorf("getTask escaped path = %q, want %q", got, want)
	}
	if requests[1].URL.RawQuery != "" {
		t.Errorf("getTask query = %q, want empty", requests[1].URL.RawQuery)
	}
}

func TestApplyRetryPolicy(t *testing.T) {
	for _, tc := range []struct {
		name   string
		status int
	}{
		{"429 too many requests", http.StatusTooManyRequests},
		{"500 internal server error", http.StatusInternalServerError},
		{"502 bad gateway", http.StatusBadGateway},
		{"503 service unavailable", http.StatusServiceUnavailable},
		{"504 gateway timeout", http.StatusGatewayTimeout},
	} {
		t.Run("retries "+tc.name, func(t *testing.T) {
			var bodies [][]byte
			var correlations []string
			calls := 0
			transport := roundTripFunc(func(req *http.Request) (*http.Response, error) {
				calls++
				raw, err := io.ReadAll(req.Body)
				if err != nil {
					return nil, err
				}
				bodies = append(bodies, raw)
				correlations = append(correlations, req.Header.Get("X-Correlation-Id"))
				if calls == 1 {
					return response(tc.status, `{}`), nil
				}
				return response(http.StatusAccepted, `{"id":"task-1","status":"PENDING"}`), nil
			})
			var backoffCalls []int
			client, err := sddclcm.NewClient("http://contractmock.local", bearerToken,
				sddclcm.WithHTTPClient(&http.Client{Transport: transport}),
				sddclcm.WithMaxAttempts(3),
				sddclcm.WithRetryBackoff(func(attempt int) time.Duration {
					backoffCalls = append(backoffCalls, attempt)
					return 0
				}),
			)
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			if _, err := client.ApplyComponentUpgrade(context.Background(), componentID, minimalSpec()); err != nil {
				t.Fatalf("ApplyComponentUpgrade: %v", err)
			}
			if calls != 2 {
				t.Fatalf("attempts = %d, want 2", calls)
			}
			if len(backoffCalls) != 1 || backoffCalls[0] != 1 {
				t.Errorf("backoff calls = %v, want [1]", backoffCalls)
			}
			if string(bodies[0]) != string(bodies[1]) {
				t.Errorf("retry body changed\nfirst: %s\nretry: %s", bodies[0], bodies[1])
			}
			if correlations[0] != correlationID || correlations[1] != correlationID {
				t.Errorf("retry correlation ids = %q, want %q twice", correlations, correlationID)
			}
		})
	}

	for _, tc := range []struct {
		name   string
		status int
	}{
		{"400 bad request", http.StatusBadRequest},
		{"401 unauthorized", http.StatusUnauthorized},
		{"403 forbidden", http.StatusForbidden},
		{"404 not found", http.StatusNotFound},
		{"408 request timeout", http.StatusRequestTimeout},
		{"409 conflict", http.StatusConflict},
		{"422 unprocessable entity", http.StatusUnprocessableEntity},
		{"501 not implemented", http.StatusNotImplemented},
	} {
		t.Run("does not retry "+tc.name, func(t *testing.T) {
			calls := 0
			transport := roundTripFunc(func(*http.Request) (*http.Response, error) {
				calls++
				return response(tc.status, `{}`), nil
			})
			client, err := sddclcm.NewClient("http://contractmock.local", bearerToken,
				sddclcm.WithHTTPClient(&http.Client{Transport: transport}),
				sddclcm.WithMaxAttempts(3),
				sddclcm.WithRetryBackoff(func(int) time.Duration { return 0 }),
			)
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			_, applyErr := client.ApplyComponentUpgrade(context.Background(), componentID, minimalSpec())
			var apiErr *sddclcm.APIError
			if !errors.As(applyErr, &apiErr) || apiErr.StatusCode != tc.status {
				t.Fatalf("error = %v, want *APIError with status %d", applyErr, tc.status)
			}
			if calls != 1 {
				t.Fatalf("attempts = %d, want 1", calls)
			}
		})
	}

	t.Run("retries a transport failure", func(t *testing.T) {
		calls := 0
		transport := roundTripFunc(func(*http.Request) (*http.Response, error) {
			calls++
			if calls == 1 {
				return nil, errors.New("connection reset")
			}
			return response(http.StatusAccepted, `{"id":"task-1","status":"PENDING"}`), nil
		})
		client, err := sddclcm.NewClient("http://contractmock.local", bearerToken,
			sddclcm.WithHTTPClient(&http.Client{Transport: transport}),
			sddclcm.WithMaxAttempts(2),
			sddclcm.WithRetryBackoff(func(int) time.Duration { return 0 }),
		)
		if err != nil {
			t.Fatalf("NewClient: %v", err)
		}
		if _, err := client.ApplyComponentUpgrade(context.Background(), componentID, minimalSpec()); err != nil {
			t.Fatalf("ApplyComponentUpgrade: %v", err)
		}
		if calls != 2 {
			t.Fatalf("attempts = %d, want 2", calls)
		}
	})

	t.Run("encodes once before retrying", func(t *testing.T) {
		mock := contractmock.Start(t, contractmock.Options{LostAcceptResponses: 2})
		client := newClient(t, mock)
		probe := &changingMarshaler{}
		spec := minimalSpec()
		spec.ComponentSpec.Policy = map[string]any{"probe": probe}

		if _, err := client.ApplyComponentUpgrade(context.Background(), componentID, spec); err != nil {
			t.Fatalf("ApplyComponentUpgrade: %v", err)
		}
		if probe.calls != 1 {
			t.Fatalf("MarshalJSON calls = %d, want exactly 1 across all attempts", probe.calls)
		}
		if got := len(mock.Requests()); got != 3 {
			t.Fatalf("attempts = %d, want 3", got)
		}
	})

	t.Run("default attempt cap is four", func(t *testing.T) {
		calls := 0
		transport := roundTripFunc(func(*http.Request) (*http.Response, error) {
			calls++
			return response(http.StatusInternalServerError, `{}`), nil
		})
		client, err := sddclcm.NewClient("http://contractmock.local", bearerToken,
			sddclcm.WithHTTPClient(&http.Client{Transport: transport}),
			sddclcm.WithRetryBackoff(func(int) time.Duration { return 0 }),
		)
		if err != nil {
			t.Fatalf("NewClient: %v", err)
		}
		if _, err := client.ApplyComponentUpgrade(context.Background(), componentID, minimalSpec()); err == nil {
			t.Fatal("error = nil after every attempt failed")
		}
		if calls != 4 {
			t.Fatalf("attempts = %d, want default cap 4", calls)
		}
	})
}

func TestResponseErrorsPreserveContractDetails(t *testing.T) {
	const errorBody = `{"code":"SDDC_LCM_CONFLICT","message":{"defaultMessage":"upgrade already running"},"referenceId":"ref-123"}`
	transport := roundTripFunc(func(*http.Request) (*http.Response, error) {
		return response(http.StatusConflict, errorBody), nil
	})
	client, err := sddclcm.NewClient("http://contractmock.local", bearerToken,
		sddclcm.WithHTTPClient(&http.Client{Transport: transport}),
		sddclcm.WithMaxAttempts(1),
	)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	_, err = client.ApplyComponentUpgrade(context.Background(), componentID, minimalSpec())
	var apiErr *sddclcm.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("error = %v, want *APIError", err)
	}
	if apiErr.StatusCode != http.StatusConflict || apiErr.Code != "SDDC_LCM_CONFLICT" ||
		apiErr.ReferenceID != "ref-123" || apiErr.Message != "upgrade already running" {
		t.Errorf("APIError = %+v, contract fields were not preserved", apiErr)
	}
	if string(apiErr.Body) != errorBody {
		t.Errorf("APIError.Body = %q, want %q", apiErr.Body, errorBody)
	}
}

func TestMalformedSuccessResponsesAreProtocolErrors(t *testing.T) {
	for _, tc := range []struct {
		name      string
		method    string
		status    int
		body      string
		invoke    func(*sddclcm.Client) error
		wantCalls int
	}{
		{
			name:   "apply invalid JSON",
			method: http.MethodPost,
			status: http.StatusAccepted,
			body:   `{`,
			invoke: func(client *sddclcm.Client) error {
				_, err := client.ApplyComponentUpgrade(context.Background(), componentID, minimalSpec())
				return err
			},
			wantCalls: 1,
		},
		{
			name:   "apply task missing required id",
			method: http.MethodPost,
			status: http.StatusAccepted,
			body:   `{"status":"PENDING"}`,
			invoke: func(client *sddclcm.Client) error {
				_, err := client.ApplyComponentUpgrade(context.Background(), componentID, minimalSpec())
				return err
			},
			wantCalls: 1,
		},
		{
			name:   "getTask task missing required id",
			method: http.MethodGet,
			status: http.StatusOK,
			body:   `null`,
			invoke: func(client *sddclcm.Client) error {
				_, err := client.GetTask(context.Background(), "task-1")
				return err
			},
			wantCalls: 1,
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			transport := roundTripFunc(func(req *http.Request) (*http.Response, error) {
				calls++
				if req.Method != tc.method {
					t.Errorf("method = %s, want %s", req.Method, tc.method)
				}
				return response(tc.status, tc.body), nil
			})
			client, err := sddclcm.NewClient("http://contractmock.local", bearerToken,
				sddclcm.WithHTTPClient(&http.Client{Transport: transport}),
				sddclcm.WithMaxAttempts(3),
				sddclcm.WithRetryBackoff(func(int) time.Duration { return 0 }),
			)
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			var protocolErr *sddclcm.ProtocolError
			if err := tc.invoke(client); !errors.As(err, &protocolErr) {
				t.Fatalf("error = %v, want *ProtocolError", err)
			}
			if calls != tc.wantCalls {
				t.Fatalf("calls = %d, want %d", calls, tc.wantCalls)
			}
		})
	}
}

func TestApplyRequestWireShape(t *testing.T) {
	full := minimalSpec()
	full.ComponentSpec.Policy = map[string]any{"haltOnPrecheckWarning": true}
	full.ComponentSpec.Depot.Certificate = []string{depotCert}
	full.ComponentSpec.UserInput = map[string]any{"acceptEula": true}
	full.ComponentSpec.AdditionalInput = map[string]any{"maintenanceWindow": "PT4H"}
	full.LcmPlatform = &sddclcm.LcmPlatformSpec{PerformBackup: true}

	backupOff := minimalSpec()
	backupOff.LcmPlatform = &sddclcm.LcmPlatformSpec{PerformBackup: false}

	emptyOptionals := minimalSpec()
	emptyOptionals.ComponentSpec.Policy = map[string]any{}
	emptyOptionals.ComponentSpec.Depot.Certificate = []string{}
	emptyOptionals.ComponentSpec.UserInput = map[string]any{}
	emptyOptionals.ComponentSpec.AdditionalInput = map[string]any{}

	for _, tc := range []struct {
		name string
		spec sddclcm.UpgradeSpec
		want string
	}{
		{
			name: "unset optionals are omitted",
			spec: minimalSpec(),
			want: minimalBody,
		},
		{
			name: "every optional member populated",
			spec: full,
			want: `{"componentSpec":{"software":{"version":"9.1.0.0000.1234567"},` +
				`"policy":{"haltOnPrecheckWarning":true},` +
				`"depot":{"url":"https://fds.vsphere.com/component-x/v9.1.0/upgrade-manifest",` +
				`"certificate":["LS0tLS1CRUdJTiBDRVJUSUZJQ0FURQ=="]},` +
				`"userInput":{"acceptEula":true},` +
				`"additionalInput":{"maintenanceWindow":"PT4H"}},` +
				`"lcmPlatformSpec":{"performBackup":true},` +
				`"correlationId":"f0e9d8c7-b6a5-4321-fedc-ba9876543210"}`,
		},
		{
			name: "false is a value not an unset member",
			spec: backupOff,
			want: `{"componentSpec":{"software":{"version":"9.1.0.0000.1234567"},` +
				`"depot":{"url":"https://fds.vsphere.com/component-x/v9.1.0/upgrade-manifest"}},` +
				`"lcmPlatformSpec":{"performBackup":false},` +
				`"correlationId":"f0e9d8c7-b6a5-4321-fedc-ba9876543210"}`,
		},
		{
			name: "empty optional containers are omitted not sent empty",
			spec: emptyOptionals,
			want: minimalBody,
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			mock := contractmock.Start(t, contractmock.Options{})
			client := newClient(t, mock)

			task, err := client.ApplyComponentUpgrade(context.Background(), componentID, tc.spec)
			if err != nil {
				t.Fatalf("ApplyComponentUpgrade: %v", err)
			}
			if task == nil || task.ID == "" {
				t.Fatal("accepted apply returned no task id")
			}

			log := mock.Requests()
			if len(log) != 1 {
				t.Fatalf("recorded %d requests, want 1", len(log))
			}
			rec := log[0]
			if rec.Method != http.MethodPost {
				t.Errorf("method = %s, want POST", rec.Method)
			}
			if want := "/v1/components/" + componentID; rec.RawPath != want {
				t.Errorf("path = %q, want %q", rec.RawPath, want)
			}
			if rec.RawQuery != "action=apply" {
				t.Errorf("query = %q, want %q", rec.RawQuery, "action=apply")
			}
			if got := rec.Header.Get("Authorization"); got != "Bearer "+bearerToken {
				t.Errorf("Authorization = %q, want %q", got, "Bearer "+bearerToken)
			}
			if got := rec.Header.Get("Content-Type"); got != "application/json" {
				t.Errorf("Content-Type = %q, want application/json", got)
			}
			if got := rec.Header.Get("Accept"); got != "application/json" {
				t.Errorf("Accept = %q, want application/json", got)
			}
			if got := rec.Header.Get("X-Correlation-Id"); got != correlationID {
				t.Errorf("X-Correlation-Id = %q, want %q", got, correlationID)
			}
			if got := string(rec.Body); got != tc.want {
				t.Errorf("request body mismatch\n got: %s\nwant: %s", got, tc.want)
			}
		})
	}
}

func TestApplyIsRetriedWithoutStartingASecondUpgrade(t *testing.T) {
	mock := contractmock.Start(t, contractmock.Options{
		LostAcceptResponses: 2,
		TaskStatuses:        []string{"RUNNING", "SUCCEEDED"},
	})
	client := newClient(t, mock)

	task, err := client.ApplyUpgradeAndWait(context.Background(), componentID, minimalSpec(), time.Hour)
	if err != nil {
		t.Fatalf("ApplyUpgradeAndWait: %v", err)
	}
	if task.Status != "SUCCEEDED" {
		t.Fatalf("terminal status = %q, want SUCCEEDED", task.Status)
	}

	created := mock.CreatedTaskIDs()
	if len(created) != 1 {
		t.Fatalf("the service started %d upgrades, want exactly 1: %v", len(created), created)
	}
	if task.ID != created[0] {
		t.Errorf("returned task %q, want the single started upgrade %q", task.ID, created[0])
	}

	var posts, polls []contractmock.Record
	for _, rec := range mock.Requests() {
		if rec.Method == http.MethodPost {
			posts = append(posts, rec)
		} else {
			polls = append(polls, rec)
		}
	}
	if len(posts) != 3 {
		t.Fatalf("sent %d apply attempts, want 3 (two lost responses then success)", len(posts))
	}
	for i, rec := range posts[1:] {
		if string(rec.Body) != string(posts[0].Body) {
			t.Errorf("retry %d changed the payload\n got: %s\nwant: %s", i+1, rec.Body, posts[0].Body)
		}
		if got := rec.Header.Get("X-Correlation-Id"); got != correlationID {
			t.Errorf("retry %d used correlation id %q, want the original %q", i+1, got, correlationID)
		}
		if rec.RawQuery != "action=apply" {
			t.Errorf("retry %d query = %q, want action=apply", i+1, rec.RawQuery)
		}
	}
	if len(polls) != 2 {
		t.Fatalf("sent %d polls, want 2", len(polls))
	}
	for _, rec := range polls {
		if rec.Method != http.MethodGet {
			t.Errorf("poll method = %s, want GET", rec.Method)
		}
		if want := "/v1/tasks/" + created[0]; rec.RawPath != want {
			t.Errorf("poll path = %q, want %q", rec.RawPath, want)
		}
		if rec.RawQuery != "" {
			t.Errorf("poll query = %q, want empty", rec.RawQuery)
		}
		if len(rec.Body) != 0 {
			t.Errorf("poll body = %q, want empty", rec.Body)
		}
		if got := rec.Header.Get("Accept"); got != "application/json" {
			t.Errorf("poll Accept = %q, want application/json", got)
		}
		if got := rec.Header.Get("Content-Type"); got != "" {
			t.Errorf("poll sent Content-Type %q on a body-less GET", got)
		}
		if got := rec.Header.Get("X-Correlation-Id"); got != "" {
			t.Errorf("poll sent apply-only X-Correlation-Id %q", got)
		}
		if got := rec.Header.Get("Authorization"); got != "Bearer "+bearerToken {
			t.Errorf("poll Authorization = %q, want the bearer token", got)
		}
	}
}

func TestSubmissionErrorsAreClassified(t *testing.T) {
	t.Run("missing correlation id never reaches the wire", func(t *testing.T) {
		mock := contractmock.Start(t, contractmock.Options{})
		client := newClient(t, mock)

		spec := minimalSpec()
		spec.CorrelationID = ""
		if _, err := client.ApplyComponentUpgrade(context.Background(), componentID, spec); !errors.Is(err, sddclcm.ErrMissingCorrelationID) {
			t.Fatalf("error = %v, want ErrMissingCorrelationID", err)
		}
		if got := len(mock.Requests()); got != 0 {
			t.Fatalf("sent %d requests for an unreplayable submission, want 0", got)
		}
	})

	t.Run("reusing a correlation id for different bytes is rejected and not retried", func(t *testing.T) {
		mock := contractmock.Start(t, contractmock.Options{})
		client := newClient(t, mock)

		if _, err := client.ApplyComponentUpgrade(context.Background(), componentID, minimalSpec()); err != nil {
			t.Fatalf("first apply: %v", err)
		}
		changed := minimalSpec()
		changed.ComponentSpec.Software.Version = "9.1.0.0000.7654321"

		_, err := client.ApplyComponentUpgrade(context.Background(), componentID, changed)
		var apiErr *sddclcm.APIError
		if !errors.As(err, &apiErr) {
			t.Fatalf("error = %v, want *sddclcm.APIError", err)
		}
		if apiErr.StatusCode != http.StatusBadRequest {
			t.Errorf("status = %d, want 400", apiErr.StatusCode)
		}
		if apiErr.Code != "SDDC_LCM_IDEMPOTENCY_CONFLICT" {
			t.Errorf("code = %q, want SDDC_LCM_IDEMPOTENCY_CONFLICT", apiErr.Code)
		}
		if apiErr.ReferenceID == "" {
			t.Error("APIError.ReferenceID is empty; the ErrorResponse carried one")
		}
		if got := len(mock.Requests()); got != 2 {
			t.Fatalf("sent %d requests, want 2 (a 400 must not be retried)", got)
		}
		if got := len(mock.CreatedTaskIDs()); got != 1 {
			t.Fatalf("the service started %d upgrades, want 1", got)
		}
	})

	t.Run("exhausted retries surface the last server error", func(t *testing.T) {
		mock := contractmock.Start(t, contractmock.Options{LostAcceptResponses: 9})
		client := newClient(t, mock, sddclcm.WithMaxAttempts(2))

		_, err := client.ApplyComponentUpgrade(context.Background(), componentID, minimalSpec())
		var apiErr *sddclcm.APIError
		if !errors.As(err, &apiErr) {
			t.Fatalf("error = %v, want *sddclcm.APIError", err)
		}
		if apiErr.StatusCode != http.StatusInternalServerError {
			t.Errorf("status = %d, want 500", apiErr.StatusCode)
		}
		if got := len(mock.Requests()); got != 2 {
			t.Fatalf("sent %d attempts, want 2", got)
		}
		if got := len(mock.CreatedTaskIDs()); got != 1 {
			t.Fatalf("the service started %d upgrades, want 1", got)
		}
	})
}

func TestPollingStopsAtTerminalStatuses(t *testing.T) {
	for _, tc := range []struct {
		name      string
		statuses  []string
		wantPolls int
		check     func(t *testing.T, task *sddclcm.Task, err error)
	}{
		{
			name:      "succeeded is a success",
			statuses:  []string{"PENDING", "SCHEDULED", "RUNNING", "SUCCEEDED"},
			wantPolls: 4,
			check: func(t *testing.T, task *sddclcm.Task, err error) {
				if err != nil {
					t.Fatalf("err = %v, want nil", err)
				}
				if task.Status != "SUCCEEDED" {
					t.Errorf("status = %q, want SUCCEEDED", task.Status)
				}
			},
		},
		{
			name:      "failed is terminal and reported",
			statuses:  []string{"RUNNING", "FAILED"},
			wantPolls: 2,
			check: func(t *testing.T, _ *sddclcm.Task, err error) {
				var failed *sddclcm.TaskFailedError
				if !errors.As(err, &failed) {
					t.Fatalf("err = %v, want *sddclcm.TaskFailedError", err)
				}
				if failed.Task == nil || failed.Task.Status != "FAILED" {
					t.Errorf("TaskFailedError carried %+v, want a FAILED task", failed.Task)
				}
			},
		},
		{
			name:      "canceled is terminal and reported",
			statuses:  []string{"CANCELED"},
			wantPolls: 1,
			check: func(t *testing.T, _ *sddclcm.Task, err error) {
				var failed *sddclcm.TaskFailedError
				if !errors.As(err, &failed) {
					t.Fatalf("err = %v, want *sddclcm.TaskFailedError", err)
				}
				if failed.Task == nil || failed.Task.Status != "CANCELED" {
					t.Errorf("TaskFailedError carried %+v, want a CANCELED task", failed.Task)
				}
			},
		},
		{
			name:      "a status outside the enum is a protocol error",
			statuses:  []string{"AWAITING_ORACLE"},
			wantPolls: 1,
			check: func(t *testing.T, _ *sddclcm.Task, err error) {
				var protocol *sddclcm.ProtocolError
				if !errors.As(err, &protocol) {
					t.Fatalf("err = %v, want *sddclcm.ProtocolError", err)
				}
			},
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			mock := contractmock.Start(t, contractmock.Options{TaskStatuses: tc.statuses})
			client := newClient(t, mock)

			task, err := client.ApplyUpgradeAndWait(context.Background(), componentID, minimalSpec(), time.Hour)
			tc.check(t, task, err)

			polls := 0
			for _, rec := range mock.Requests() {
				if rec.Method == http.MethodGet {
					polls++
				}
			}
			if polls != tc.wantPolls {
				t.Errorf("sent %d polls, want %d", polls, tc.wantPolls)
			}
		})
	}
}

func TestWaitingTimesOutOnANonTerminalTask(t *testing.T) {
	mock := contractmock.Start(t, contractmock.Options{TaskStatuses: []string{"RUNNING"}})
	client := newClient(t, mock, sddclcm.WithPollInterval(0))

	_, err := client.ApplyUpgradeAndWait(context.Background(), componentID, minimalSpec(), 0)
	if !errors.Is(err, sddclcm.ErrPollTimeout) {
		t.Fatalf("err = %v, want ErrPollTimeout", err)
	}
	if got := len(mock.CreatedTaskIDs()); got != 1 {
		t.Fatalf("the service started %d upgrades, want 1", got)
	}
}

func TestCancelledContextStopsWaiting(t *testing.T) {
	mock := contractmock.Start(t, contractmock.Options{TaskStatuses: []string{"RUNNING"}})
	inner := mock.HTTPClient().Transport
	ctx, cancel := context.WithCancel(context.Background())
	transport := roundTripFunc(func(req *http.Request) (*http.Response, error) {
		resp, err := inner.RoundTrip(req)
		if req.Method == http.MethodPost {
			cancel()
		}
		return resp, err
	})
	client := newClient(t, mock,
		sddclcm.WithHTTPClient(&http.Client{Transport: transport}),
		sddclcm.WithPollInterval(time.Hour),
	)

	if _, err := client.ApplyUpgradeAndWait(ctx, componentID, minimalSpec(), time.Hour); !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", err)
	}
	if got := len(mock.CreatedTaskIDs()); got != 1 {
		t.Fatalf("the service started %d upgrades, want 1 before cancellation", got)
	}
	if got := len(mock.Requests()); got != 1 {
		t.Fatalf("sent %d requests, want only the accepted apply", got)
	}
	if ctx.Err() == nil {
		t.Fatal("err = nil, want a context cancellation error")
	}
}

func TestClientIsSafeForConcurrentUse(t *testing.T) {
	t.Run("one correlation id collapses to one upgrade", func(t *testing.T) {
		mock := contractmock.Start(t, contractmock.Options{})
		client := newClient(t, mock)

		const goroutines = 8
		ids := make([]string, goroutines)
		errs := make([]error, goroutines)
		var wg sync.WaitGroup
		for i := 0; i < goroutines; i++ {
			wg.Add(1)
			go func(i int) {
				defer wg.Done()
				task, err := client.ApplyComponentUpgrade(context.Background(), componentID, minimalSpec())
				errs[i] = err
				if task != nil {
					ids[i] = task.ID
				}
			}(i)
		}
		wg.Wait()

		for i, err := range errs {
			if err != nil {
				t.Fatalf("goroutine %d: %v", i, err)
			}
			if ids[i] != ids[0] {
				t.Errorf("goroutine %d got task %q, want the shared %q", i, ids[i], ids[0])
			}
		}
		if got := len(mock.CreatedTaskIDs()); got != 1 {
			t.Fatalf("the service started %d upgrades, want 1", got)
		}
		if got := len(mock.Requests()); got != goroutines {
			t.Fatalf("sent %d requests, want %d", got, goroutines)
		}
	})

	t.Run("distinct correlation ids start distinct upgrades", func(t *testing.T) {
		mock := contractmock.Start(t, contractmock.Options{})
		client := newClient(t, mock)

		const goroutines = 8
		var wg sync.WaitGroup
		errs := make([]error, goroutines)
		for i := 0; i < goroutines; i++ {
			wg.Add(1)
			go func(i int) {
				defer wg.Done()
				spec := minimalSpec()
				spec.CorrelationID = fmt.Sprintf("f0e9d8c7-b6a5-4321-fedc-%012d", i)
				_, errs[i] = client.ApplyComponentUpgrade(context.Background(), componentID, spec)
			}(i)
		}
		wg.Wait()

		for i, err := range errs {
			if err != nil {
				t.Fatalf("goroutine %d: %v", i, err)
			}
		}
		if got := len(mock.CreatedTaskIDs()); got != goroutines {
			t.Fatalf("the service started %d upgrades, want %d", got, goroutines)
		}
	})
}
