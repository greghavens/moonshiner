package nsxpolicy_test

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"

	"example.com/vcf91/nsxipblockretry/internal/contractmock"
	"example.com/vcf91/nsxipblockretry/nsxpolicy"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedSpec   = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
	expectedOp     = "CreateOrPatchIpAddressBlock"
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
			BlobSHA  string `json:"spec_blob_sha"`
			License  string `json:"license"`
		} `json:"source"`
		Info struct {
			Version string `json:"version"`
		} `json:"info"`
		BasePath   string `json:"basePath"`
		Operations []struct {
			operationSource
			Parameters []struct {
				Name      string `json:"name"`
				In        string `json:"in"`
				Required  bool   `json:"required"`
				SchemaRef string `json:"schema_ref"`
			} `json:"parameters"`
			Responses map[string]json.RawMessage `json:"responses"`
		} `json:"operations"`
	}
	readJSON(t, "../docs/contract.json", &contract)

	var sources struct {
		Commit     string `json:"repository_commit_sha"`
		SpecPath   string `json:"spec_path"`
		BlobSHA    string `json:"spec_blob_sha"`
		License    string `json:"license"`
		Operations []struct {
			operationSource
			Commit   string `json:"repository_commit_sha"`
			SpecPath string `json:"spec_path"`
		} `json:"operations"`
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
	if contract.Source.BlobSHA != "102d15fd342f6a45bb6d84a5b39a916c65929f4c" ||
		sources.BlobSHA != contract.Source.BlobSHA ||
		contract.Source.License != "Apache-2.0" ||
		sources.License != contract.Source.License {
		t.Fatal("protected specification blob or license provenance mismatch")
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
		OperationID: expectedOp,
		Method:      http.MethodPatch,
		Path:        "/infra/ip-blocks/{ip-block-id}",
	}
	if len(contract.Operations) != 1 ||
		contract.Operations[0].operationSource != wantOperation {
		t.Fatalf("contract operations mismatch: %#v", contract.Operations)
	}
	if len(sources.Operations) != 1 ||
		sources.Operations[0].operationSource != wantOperation ||
		sources.Operations[0].Commit != expectedCommit ||
		sources.Operations[0].SpecPath != expectedSpec {
		t.Fatalf("official source operation mismatch: %#v", sources.Operations)
	}

	parameters := contract.Operations[0].Parameters
	if len(parameters) != 2 ||
		parameters[0].Name != "ip-block-id" ||
		parameters[0].In != "path" ||
		!parameters[0].Required ||
		parameters[1].Name != "IpAddressBlock" ||
		parameters[1].In != "body" ||
		!parameters[1].Required ||
		parameters[1].SchemaRef != "#/definitions/IpAddressBlock" {
		t.Fatalf("operation parameter projection mismatch: %#v", parameters)
	}
	wantStatuses := []string{"200", "400", "403", "404", "412", "500", "503", "504"}
	for _, status := range wantStatuses {
		if _, ok := contract.Operations[0].Responses[status]; !ok {
			t.Fatalf("contract response %s is missing", status)
		}
	}
	if len(contract.Operations[0].Responses) != len(wantStatuses) {
		t.Fatalf("unexpected response projection: %#v", contract.Operations[0].Responses)
	}
}

func TestApplyIPBlockRetryContract(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name         string
		first        contractmock.FirstBehavior
		wantAttempts int
	}{
		{
			name:         "lost response after apply replays the mutation",
			first:        contractmock.DropAfterApply,
			wantAttempts: 2,
		},
		{
			name:         "service unavailable is retried once",
			first:        contractmock.Return503,
			wantAttempts: 2,
		},
		{
			name:         "gateway timeout is retried once",
			first:        contractmock.Return504,
			wantAttempts: 2,
		},
		{
			name:         "immediate success is not retried",
			first:        contractmock.Succeed,
			wantAttempts: 1,
		},
	}

	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()

			const (
				blockID  = "prod/east +%"
				username = "svc-ipam"
				password = "p@ss/token +%"
			)
			logPath := t.TempDir() + "/requests.jsonl"
			mock := contractmock.Start(
				t,
				"../docs/contract.json",
				logPath,
				contractmock.Scenario{
					IPBlockID:    blockID,
					First:        tt.first,
					ExpectedUser: username,
					ExpectedPass: password,
				},
			)
			defer mock.Close()

			client, err := nsxpolicy.NewClient(nsxpolicy.Config{
				BaseURL:    mock.URL,
				Username:   username,
				Password:   password,
				HTTPClient: mock.Client,
			})
			if err != nil {
				t.Fatalf("NewClient() error = %v", err)
			}

			block := nsxpolicy.IPAddressBlock{
				DisplayName: "Edge block",
				CIDRs:       []string{"10.44.0.0/16", "2001:db8::/64"},
			}
			got, err := client.ApplyIPBlock(context.Background(), blockID, block)
			if err != nil {
				t.Fatalf("ApplyIPBlock() error = %v", err)
			}
			want := nsxpolicy.Result{
				OperationID: expectedOp,
				IPBlockID:   blockID,
				Attempts:    tt.wantAttempts,
			}
			if got != want {
				t.Fatalf("ApplyIPBlock() = %#v, want %#v", got, want)
			}

			records, err := contractmock.ReadLog(logPath)
			if err != nil {
				t.Fatalf("ReadLog() error = %v", err)
			}
			wantBody := []byte(
				`{"display_name":"Edge block","cidrs":["10.44.0.0/16","2001:db8::/64"]}`,
			)
			assertExactWire(
				t,
				records,
				tt.wantAttempts,
				"/policy/api/v1/infra/ip-blocks/prod%2Feast%20+%25",
				"Basic "+base64.StdEncoding.EncodeToString(
					[]byte(username+":"+password),
				),
				wantBody,
				[]string{
					"description",
					"subnet_exclusive",
					"id",
					"resource_type",
					"tags",
				},
			)
			if effects := mock.EffectCount(); effects != 1 {
				t.Fatalf("resource effects = %d, want exactly 1", effects)
			}
		})
	}
}

func TestExplicitFalseRemainsPresent(t *testing.T) {
	t.Parallel()

	const (
		blockID  = "reserved"
		username = "svc"
		password = "secret"
	)
	logPath := t.TempDir() + "/requests.jsonl"
	mock := contractmock.Start(
		t,
		"../docs/contract.json",
		logPath,
		contractmock.Scenario{
			IPBlockID:    blockID,
			ExpectedUser: username,
			ExpectedPass: password,
		},
	)
	defer mock.Close()

	client, err := nsxpolicy.NewClient(nsxpolicy.Config{
		BaseURL:    mock.URL,
		Username:   username,
		Password:   password,
		HTTPClient: mock.Client,
	})
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}
	description := "reserved for edge"
	subnetExclusive := false
	_, err = client.ApplyIPBlock(
		context.Background(),
		blockID,
		nsxpolicy.IPAddressBlock{
			DisplayName:     "Reserved",
			CIDRs:           []string{"172.20.0.0/16"},
			Description:     &description,
			SubnetExclusive: &subnetExclusive,
		},
	)
	if err != nil {
		t.Fatalf("ApplyIPBlock() error = %v", err)
	}

	records, err := contractmock.ReadLog(logPath)
	if err != nil {
		t.Fatalf("ReadLog() error = %v", err)
	}
	wantBody := []byte(
		`{"display_name":"Reserved","cidrs":["172.20.0.0/16"],"description":"reserved for edge","subnet_exclusive":false}`,
	)
	assertExactWire(
		t,
		records,
		1,
		"/policy/api/v1/infra/ip-blocks/reserved",
		"Basic "+base64.StdEncoding.EncodeToString([]byte(username+":"+password)),
		wantBody,
		[]string{"id", "resource_type", "tags"},
	)
}

func TestNonRetryableStatuses(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name   string
		first  contractmock.FirstBehavior
		status int
	}{
		{name: "bad request", first: contractmock.Return400, status: 400},
		{name: "forbidden", first: contractmock.Return403, status: 403},
		{name: "not found", first: contractmock.Return404, status: 404},
		{name: "precondition failed", first: contractmock.Return412, status: 412},
		{name: "internal error", first: contractmock.Return500, status: 500},
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
					IPBlockID:    "one",
					First:        tt.first,
					ExpectedUser: "user",
					ExpectedPass: "password",
				},
			)
			defer mock.Close()
			client := mustClient(t, mock, "user", "password")

			_, err := client.ApplyIPBlock(
				context.Background(),
				"one",
				nsxpolicy.IPAddressBlock{
					DisplayName: "One",
					CIDRs:       []string{"192.0.2.0/24"},
				},
			)
			var apiErr *nsxpolicy.APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("error = %T %v, want *APIError", err, err)
			}
			if apiErr.StatusCode != tt.status ||
				apiErr.ErrorCode != 97001 ||
				apiErr.ErrorMessage != "fixture failure payload must remain private" ||
				apiErr.ModuleName != "contractmock" ||
				apiErr.Details != "projected detail" {
				t.Fatalf("APIError projection = %#v", apiErr)
			}
			for _, secret := range []string{
				"user",
				"password",
				"fixture failure payload must remain private",
				"projected detail",
			} {
				if strings.Contains(err.Error(), secret) {
					t.Fatalf("error string exposed %q: %q", secret, err)
				}
			}
			records, readErr := contractmock.ReadLog(logPath)
			if readErr != nil {
				t.Fatalf("ReadLog() error = %v", readErr)
			}
			if len(records) != 1 {
				t.Fatalf("request count = %d, want 1", len(records))
			}
			if effects := mock.EffectCount(); effects != 0 {
				t.Fatalf("resource effects = %d, want 0", effects)
			}
		})
	}
}

func TestRetryLimitStopsAfterTwoAttempts(t *testing.T) {
	t.Parallel()

	logPath := t.TempDir() + "/requests.jsonl"
	mock := contractmock.Start(
		t,
		"../docs/contract.json",
		logPath,
		contractmock.Scenario{
			IPBlockID:    "bounded",
			First:        contractmock.Return503,
			RepeatFirst:  true,
			ExpectedUser: "user",
			ExpectedPass: "password",
		},
	)
	defer mock.Close()
	client := mustClient(t, mock, "user", "password")

	_, err := client.ApplyIPBlock(
		context.Background(),
		"bounded",
		nsxpolicy.IPAddressBlock{
			DisplayName: "Bounded",
			CIDRs:       []string{"203.0.113.0/24"},
		},
	)
	var apiErr *nsxpolicy.APIError
	if !errors.As(err, &apiErr) || apiErr.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("error = %T %v, want final 503 APIError", err, err)
	}
	records, readErr := contractmock.ReadLog(logPath)
	if readErr != nil {
		t.Fatalf("ReadLog() error = %v", readErr)
	}
	if len(records) != 2 {
		t.Fatalf("request count = %d, want exactly 2", len(records))
	}
	if effects := mock.EffectCount(); effects != 0 {
		t.Fatalf("resource effects = %d, want 0", effects)
	}
}

func TestValidationPrecedesTraffic(t *testing.T) {
	t.Parallel()

	configTests := []struct {
		name string
		cfg  nsxpolicy.Config
	}{
		{name: "blank base URL", cfg: nsxpolicy.Config{Username: "u", Password: "p"}},
		{name: "unsupported scheme", cfg: nsxpolicy.Config{BaseURL: "ftp://example.com", Username: "u", Password: "p"}},
		{name: "missing host", cfg: nsxpolicy.Config{BaseURL: "https://", Username: "u", Password: "p"}},
		{name: "embedded credentials", cfg: nsxpolicy.Config{BaseURL: "https://x:y@example.com", Username: "u", Password: "p"}},
		{name: "non-root path", cfg: nsxpolicy.Config{BaseURL: "https://example.com/policy/api/v1", Username: "u", Password: "p"}},
		{name: "query", cfg: nsxpolicy.Config{BaseURL: "https://example.com?x=1", Username: "u", Password: "p"}},
		{name: "fragment", cfg: nsxpolicy.Config{BaseURL: "https://example.com#x", Username: "u", Password: "p"}},
		{name: "blank username", cfg: nsxpolicy.Config{BaseURL: "https://example.com", Password: "p"}},
		{name: "colon username", cfg: nsxpolicy.Config{BaseURL: "https://example.com", Username: "u:x", Password: "p"}},
		{name: "blank password", cfg: nsxpolicy.Config{BaseURL: "https://example.com", Username: "u"}},
	}
	for _, tt := range configTests {
		tt := tt
		t.Run("config/"+tt.name, func(t *testing.T) {
			t.Parallel()
			if _, err := nsxpolicy.NewClient(tt.cfg); err == nil {
				t.Fatal("NewClient() error = nil, want local validation error")
			}
		})
	}

	logPath := t.TempDir() + "/requests.jsonl"
	mock := contractmock.Start(
		t,
		"../docs/contract.json",
		logPath,
		contractmock.Scenario{
			IPBlockID:    "valid",
			ExpectedUser: "user",
			ExpectedPass: "password",
		},
	)
	defer mock.Close()
	client := mustClient(t, mock, "user", "password")
	longName := strings.Repeat("n", 256)
	longDescription := strings.Repeat("d", 1025)
	requestTests := []struct {
		name  string
		id    string
		block nsxpolicy.IPAddressBlock
	}{
		{
			name: "blank id",
			block: nsxpolicy.IPAddressBlock{
				DisplayName: "Valid",
				CIDRs:       []string{"192.0.2.0/24"},
			},
		},
		{
			name: "blank display name",
			id:   "valid",
			block: nsxpolicy.IPAddressBlock{
				CIDRs: []string{"192.0.2.0/24"},
			},
		},
		{
			name: "long display name",
			id:   "valid",
			block: nsxpolicy.IPAddressBlock{
				DisplayName: longName,
				CIDRs:       []string{"192.0.2.0/24"},
			},
		},
		{
			name: "nil cidrs",
			id:   "valid",
			block: nsxpolicy.IPAddressBlock{
				DisplayName: "Valid",
			},
		},
		{
			name: "empty cidrs",
			id:   "valid",
			block: nsxpolicy.IPAddressBlock{
				DisplayName: "Valid",
				CIDRs:       []string{},
			},
		},
		{
			name: "invalid cidr",
			id:   "valid",
			block: nsxpolicy.IPAddressBlock{
				DisplayName: "Valid",
				CIDRs:       []string{"192.0.2.1"},
			},
		},
		{
			name: "long description",
			id:   "valid",
			block: nsxpolicy.IPAddressBlock{
				DisplayName: "Valid",
				CIDRs:       []string{"192.0.2.0/24"},
				Description: &longDescription,
			},
		},
	}
	for _, tt := range requestTests {
		tt := tt
		t.Run("request/"+tt.name, func(t *testing.T) {
			if _, err := client.ApplyIPBlock(
				context.Background(),
				tt.id,
				tt.block,
			); err == nil {
				t.Fatal("ApplyIPBlock() error = nil, want local validation error")
			}
		})
	}
	records, err := contractmock.ReadLog(logPath)
	if err != nil {
		t.Fatalf("ReadLog() error = %v", err)
	}
	if len(records) != 0 {
		t.Fatalf("local validation emitted traffic: %#v", records)
	}
}

func TestContextCancellationIsFinal(t *testing.T) {
	t.Parallel()

	logPath := t.TempDir() + "/requests.jsonl"
	mock := contractmock.Start(
		t,
		"../docs/contract.json",
		logPath,
		contractmock.Scenario{
			IPBlockID:    "cancelled",
			ExpectedUser: "user",
			ExpectedPass: "password",
		},
	)
	defer mock.Close()
	client := mustClient(t, mock, "user", "password")

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := client.ApplyIPBlock(
		ctx,
		"cancelled",
		nsxpolicy.IPAddressBlock{
			DisplayName: "Cancelled",
			CIDRs:       []string{"198.51.100.0/24"},
		},
	)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %T %v, want errors.Is(context.Canceled)", err, err)
	}
	records, readErr := contractmock.ReadLog(logPath)
	if readErr != nil {
		t.Fatalf("ReadLog() error = %v", readErr)
	}
	if len(records) != 0 {
		t.Fatalf("cancelled call emitted traffic: %#v", records)
	}
}

func TestNewClientDoesNotMutateCallerHTTPClient(t *testing.T) {
	t.Parallel()

	called := false
	original := func(_ *http.Request, _ []*http.Request) error {
		called = true
		return errors.New("caller redirect policy")
	}
	callerClient := &http.Client{CheckRedirect: original}
	if _, err := nsxpolicy.NewClient(nsxpolicy.Config{
		BaseURL:    "https://example.com",
		Username:   "user",
		Password:   "password",
		HTTPClient: callerClient,
	}); err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}
	if callerClient.CheckRedirect == nil {
		t.Fatal("NewClient() cleared caller redirect policy")
	}
	if err := callerClient.CheckRedirect(nil, nil); err == nil {
		t.Fatal("caller redirect policy was replaced")
	}
	if !called {
		t.Fatal("caller redirect policy was not preserved")
	}
}

func TestMockRejectsOperationsOutsideContract(t *testing.T) {
	t.Parallel()

	logPath := t.TempDir() + "/requests.jsonl"
	mock := contractmock.Start(
		t,
		"../docs/contract.json",
		logPath,
		contractmock.Scenario{
			IPBlockID:    "only",
			ExpectedUser: "user",
			ExpectedPass: "password",
		},
	)
	defer mock.Close()

	req, err := http.NewRequest(
		http.MethodGet,
		mock.URL+"/policy/api/v1/infra/ip-blocks",
		nil,
	)
	if err != nil {
		t.Fatalf("NewRequest() error = %v", err)
	}
	req.SetBasicAuth("user", "password")
	response, err := mock.Client.Do(req)
	if err != nil {
		t.Fatalf("outside-contract request error = %v", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("outside-contract status = %d, want 404", response.StatusCode)
	}
	if effects := mock.EffectCount(); effects != 0 {
		t.Fatalf("outside-contract effects = %d, want 0", effects)
	}
}

func mustClient(
	t testing.TB,
	mock *contractmock.Server,
	username string,
	password string,
) *nsxpolicy.Client {
	t.Helper()
	client, err := nsxpolicy.NewClient(nsxpolicy.Config{
		BaseURL:    mock.URL,
		Username:   username,
		Password:   password,
		HTTPClient: mock.Client,
	})
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}
	return client
}

func assertExactWire(
	t testing.TB,
	records []contractmock.RequestRecord,
	wantCount int,
	wantTarget string,
	wantAuthorization string,
	wantBody []byte,
	absentMembers []string,
) {
	t.Helper()
	if len(records) != wantCount {
		t.Fatalf(
			"request count = %d, want %d; records = %#v",
			len(records),
			wantCount,
			records,
		)
	}
	wantBody64 := base64.StdEncoding.EncodeToString(wantBody)
	for i, record := range records {
		if record.Sequence != i+1 {
			t.Errorf("request %d sequence = %d, want %d", i, record.Sequence, i+1)
		}
		if record.Method != http.MethodPatch {
			t.Errorf("request %d method = %q, want PATCH", i, record.Method)
		}
		if record.Target != wantTarget {
			t.Errorf("request %d target = %q, want %q", i, record.Target, wantTarget)
		}
		if record.Authorization != wantAuthorization {
			t.Errorf(
				"request %d Authorization = %q, want exact Basic value",
				i,
				record.Authorization,
			)
		}
		if !reflect.DeepEqual(record.Accept, []string{"application/json"}) {
			t.Errorf("request %d Accept = %#v", i, record.Accept)
		}
		if !reflect.DeepEqual(record.ContentType, []string{"application/json"}) {
			t.Errorf("request %d Content-Type = %#v", i, record.ContentType)
		}
		if record.ContentLength != int64(len(wantBody)) {
			t.Errorf(
				"request %d Content-Length = %d, want %d",
				i,
				record.ContentLength,
				len(wantBody),
			)
		}
		if len(record.Transfer) != 0 {
			t.Errorf("request %d used transfer encoding %#v", i, record.Transfer)
		}
		if record.BodyBase64 != wantBody64 {
			got, _ := base64.StdEncoding.DecodeString(record.BodyBase64)
			t.Errorf("request %d body = %q, want exact %q", i, got, wantBody)
		}
		if !reflect.DeepEqual(record.Headers["Authorization"], []string{wantAuthorization}) {
			t.Errorf("request %d Authorization header multiplicity = %#v", i, record.Headers["Authorization"])
		}

		var members map[string]json.RawMessage
		body, err := base64.StdEncoding.DecodeString(record.BodyBase64)
		if err != nil {
			t.Fatalf("request %d body base64: %v", i, err)
		}
		if err := json.Unmarshal(body, &members); err != nil {
			t.Fatalf("request %d JSON: %v", i, err)
		}
		for _, name := range absentMembers {
			if _, present := members[name]; present {
				t.Errorf("request %d contains unset optional member %q", i, name)
			}
		}
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
