package vcenter_test

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"example.com/vcf91/vcenterlibraryretry/internal/contractmock"
	"example.com/vcf91/vcenterlibraryretry/vcenter"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedSpec   = "specifications/vsphere/openapi/automation/vcenter.yaml"
	expectedBlob   = "8028b0824c4ff3503d05f44814f967938a795c40"
	expectedOp     = "Content.LocalLibrary_create"
	sessionToken   = "session-protected-value"
	clientToken    = "b8a2a2e3-2314-43cd-a871-6ede0f429751"
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
		OpenAPI string `json:"openapi"`
		Info    struct {
			Title   string `json:"title"`
			Version string `json:"version"`
		} `json:"info"`
		BasePath        string `json:"base_path"`
		SecuritySchemes map[string]struct {
			Type string `json:"type"`
			Name string `json:"name"`
			In   string `json:"in"`
		} `json:"security_schemes"`
		Operations []struct {
			operationSource
			Parameters []struct {
				Name     string `json:"name"`
				In       string `json:"in"`
				Required bool   `json:"required"`
				Schema   struct {
					Type string `json:"type"`
				} `json:"schema"`
			} `json:"parameters"`
			RequestBody struct {
				Required bool `json:"required"`
				Content  map[string]struct {
					SchemaRef string `json:"schema_ref"`
				} `json:"content"`
			} `json:"requestBody"`
			Responses map[string]json.RawMessage `json:"responses"`
			Security  []map[string][]string      `json:"security"`
		} `json:"operations"`
		Schemas map[string]struct {
			Type       string `json:"type"`
			Enum       []string
			Properties map[string]json.RawMessage `json:"properties"`
		} `json:"schemas"`
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
			Pointer  string `json:"yaml_pointer"`
		} `json:"operations"`
		SchemaProjections []struct {
			Name     string `json:"name"`
			Commit   string `json:"repository_commit_sha"`
			SpecPath string `json:"spec_path"`
			Pointer  string `json:"yaml_pointer"`
		} `json:"schema_projections"`
		Derivation string `json:"derivation"`
	}
	readJSON(t, "../docs/official_sources.json", &sources)

	if contract.Source.Commit != expectedCommit ||
		sources.Commit != expectedCommit ||
		contract.Source.SpecPath != expectedSpec ||
		sources.SpecPath != expectedSpec ||
		contract.Source.BlobSHA != expectedBlob ||
		sources.BlobSHA != expectedBlob ||
		contract.Source.License != "Apache-2.0" ||
		sources.License != "Apache-2.0" {
		t.Fatalf(
			"protected source mismatch: contract=%#v sources=%#v",
			contract.Source,
			sources,
		)
	}
	if contract.OpenAPI != "3.0.3" ||
		contract.Info.Title != "vSphere Automation API" ||
		contract.Info.Version != "9.1.0.0" ||
		contract.BasePath != "/api" {
		t.Fatalf(
			"unexpected OpenAPI identity: openapi=%q info=%#v base=%q",
			contract.OpenAPI,
			contract.Info,
			contract.BasePath,
		)
	}
	apiKey, ok := contract.SecuritySchemes["api_key_auth"]
	if !ok ||
		apiKey.Type != "apiKey" ||
		apiKey.Name != "vmware-api-session-id" ||
		apiKey.In != "header" {
		t.Fatalf("unexpected security projection: %#v", contract.SecuritySchemes)
	}

	wantOperation := operationSource{
		OperationID: expectedOp,
		Method:      http.MethodPost,
		Path:        "/content/local-library",
	}
	if len(contract.Operations) != 1 ||
		contract.Operations[0].operationSource != wantOperation {
		t.Fatalf("contract operations mismatch: %#v", contract.Operations)
	}
	op := contract.Operations[0]
	if len(op.Parameters) != 1 ||
		op.Parameters[0].Name != "Client-Token" ||
		op.Parameters[0].In != "header" ||
		op.Parameters[0].Required ||
		op.Parameters[0].Schema.Type != "string" {
		t.Fatalf("Client-Token projection mismatch: %#v", op.Parameters)
	}
	media, ok := op.RequestBody.Content["application/json"]
	if !op.RequestBody.Required ||
		!ok ||
		media.SchemaRef != "#/components/schemas/Content.LibraryModel" {
		t.Fatalf("request body projection mismatch: %#v", op.RequestBody)
	}
	if len(op.Responses) != 2 {
		t.Fatalf("unexpected response projection: %#v", op.Responses)
	}
	for _, status := range []string{"201", "400"} {
		if _, ok := op.Responses[status]; !ok {
			t.Fatalf("response %s missing from contract", status)
		}
	}
	if len(op.Security) != 1 {
		t.Fatalf("operation security mismatch: %#v", op.Security)
	}
	if _, ok := op.Security[0]["api_key_auth"]; !ok {
		t.Fatalf("operation API-key security missing: %#v", op.Security)
	}

	model := contract.Schemas["Content.LibraryModel"]
	backing := contract.Schemas["Content.Library.StorageBacking"]
	backingType := contract.Schemas["Content.Library.StorageBacking.Type"]
	if model.Type != "object" ||
		backing.Type != "object" ||
		backingType.Type != "string" ||
		!reflect.DeepEqual(backingType.Enum, []string{"DATASTORE", "OTHER"}) {
		t.Fatalf("schema identity mismatch: model=%#v backing=%#v enum=%#v", model, backing, backingType)
	}
	for _, property := range []string{
		"name",
		"storage_backings",
		"description",
		"id",
		"creation_time",
		"type",
		"publish_info",
		"subscription_info",
		"server_guid",
		"state_info",
		"configuration_info",
	} {
		if _, ok := model.Properties[property]; !ok {
			t.Fatalf("Content.LibraryModel property %q missing", property)
		}
	}
	for _, property := range []string{"type", "datastore_id", "storage_uri"} {
		if _, ok := backing.Properties[property]; !ok {
			t.Fatalf("StorageBacking property %q missing", property)
		}
	}

	if len(sources.Operations) != 1 ||
		sources.Operations[0].operationSource != wantOperation ||
		sources.Operations[0].Commit != expectedCommit ||
		sources.Operations[0].SpecPath != expectedSpec ||
		sources.Operations[0].Pointer != "#/paths/~1content~1local-library/post" {
		t.Fatalf("official operation source mismatch: %#v", sources.Operations)
	}
	if len(sources.SchemaProjections) != 3 ||
		!strings.Contains(sources.Derivation, "OpenAPI 3.0.3 YAML") {
		t.Fatalf("official schema derivation mismatch: %#v", sources)
	}
	for _, schema := range sources.SchemaProjections {
		if schema.Commit != expectedCommit ||
			schema.SpecPath != expectedSpec ||
			!strings.HasPrefix(schema.Pointer, "#/components/schemas/") {
			t.Fatalf("schema source is not pinned: %#v", schema)
		}
	}

	ids, err := contractmock.ContractOperations("../docs/contract.json")
	if err != nil {
		t.Fatalf("ContractOperations() error = %v", err)
	}
	if !reflect.DeepEqual(ids, []string{expectedOp}) {
		t.Fatalf("mock allow-list = %#v, want only %q", ids, expectedOp)
	}
}

func TestCreateLocalLibraryRetryContract(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name         string
		first        contractmock.FirstBehavior
		wantAttempts int
	}{
		{
			name:         "lost response after commit replays with client token",
			first:        contractmock.DropAfterCommit,
			wantAttempts: 2,
		},
		{
			name:         "loss while reading 201 body replays with client token",
			first:        contractmock.Truncate201AfterCommit,
			wantAttempts: 2,
		},
		{
			name:         "immediate 201 is not replayed",
			first:        contractmock.Succeed,
			wantAttempts: 1,
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
					First:               tt.first,
					ExpectedSession:     sessionToken,
					ExpectedClientToken: clientToken,
					LibraryID:           "library-7",
				},
			)
			defer mock.Close()

			client := mustClient(t, mock, sessionToken)
			datastoreID := "datastore-42"
			got, err := client.CreateLocalLibrary(
				context.Background(),
				clientToken,
				vcenter.LocalLibrarySpec{
					Name: "Operations Library",
					StorageBackings: []vcenter.StorageBacking{
						{
							Type:        vcenter.StorageBackingDatastore,
							DatastoreID: &datastoreID,
						},
					},
				},
			)
			if err != nil {
				t.Fatalf("CreateLocalLibrary() error = %v", err)
			}
			want := vcenter.CreateResult{
				OperationID: expectedOp,
				LibraryID:   "library-7",
				ClientToken: clientToken,
				Attempts:    tt.wantAttempts,
			}
			if got != want {
				t.Fatalf("CreateLocalLibrary() = %#v, want %#v", got, want)
			}

			records := readLog(t, logPath)
			wantBody := []byte(
				`{"name":"Operations Library","storage_backings":[{"type":"DATASTORE","datastore_id":"datastore-42"}]}`,
			)
			assertExactWire(
				t,
				records,
				tt.wantAttempts,
				sessionToken,
				clientToken,
				wantBody,
			)
			assertUnsetMembersOmitted(t, wantBody)
			if effects := mock.EffectCount(); effects != 1 {
				t.Fatalf("library effects = %d, want exactly 1", effects)
			}
		})
	}
}

func TestOptionalMemberShapes(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		spec     func() vcenter.LocalLibrarySpec
		wantBody string
	}{
		{
			name: "explicit empty description remains present",
			spec: func() vcenter.LocalLibrarySpec {
				datastoreID := "datastore-8"
				description := ""
				return vcenter.LocalLibrarySpec{
					Name: "Empty description",
					StorageBackings: []vcenter.StorageBacking{
						{
							Type:        vcenter.StorageBackingDatastore,
							DatastoreID: &datastoreID,
						},
					},
					Description: &description,
				}
			},
			wantBody: `{"name":"Empty description","storage_backings":[{"type":"DATASTORE","datastore_id":"datastore-8"}],"description":""}`,
		},
		{
			name: "other backing omits datastore id",
			spec: func() vcenter.LocalLibrarySpec {
				storageURI := "nfs://storage.example/library"
				description := "replicated content"
				return vcenter.LocalLibrarySpec{
					Name: "Remote Library",
					StorageBackings: []vcenter.StorageBacking{
						{
							Type:       vcenter.StorageBackingOther,
							StorageURI: &storageURI,
						},
					},
					Description: &description,
				}
			},
			wantBody: `{"name":"Remote Library","storage_backings":[{"type":"OTHER","storage_uri":"nfs://storage.example/library"}],"description":"replicated content"}`,
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
					ExpectedSession:     sessionToken,
					ExpectedClientToken: clientToken,
				},
			)
			defer mock.Close()

			client := mustClient(t, mock, sessionToken)
			if _, err := client.CreateLocalLibrary(
				context.Background(),
				clientToken,
				tt.spec(),
			); err != nil {
				t.Fatalf("CreateLocalLibrary() error = %v", err)
			}
			records := readLog(t, logPath)
			assertExactWire(
				t,
				records,
				1,
				sessionToken,
				clientToken,
				[]byte(tt.wantBody),
			)
		})
	}
}

func TestHTTPStatusesAreNeverRetried(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name   string
		first  contractmock.FirstBehavior
		status int
	}{
		{
			name:   "declared invalid argument",
			first:  contractmock.Return400,
			status: http.StatusBadRequest,
		},
		{
			name:   "undeclared service unavailable",
			first:  contractmock.Return503,
			status: http.StatusServiceUnavailable,
		},
		{
			name:   "redirect is returned and not followed",
			first:  contractmock.Return307,
			status: http.StatusTemporaryRedirect,
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
					First:               tt.first,
					ExpectedSession:     sessionToken,
					ExpectedClientToken: clientToken,
				},
			)
			defer mock.Close()

			client := mustClient(t, mock, sessionToken)
			datastoreID := "datastore-1"
			_, err := client.CreateLocalLibrary(
				context.Background(),
				clientToken,
				vcenter.LocalLibrarySpec{
					Name: "One",
					StorageBackings: []vcenter.StorageBacking{
						{
							Type:        vcenter.StorageBackingDatastore,
							DatastoreID: &datastoreID,
						},
					},
				},
			)
			var apiErr *vcenter.APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("error = %T %v, want *APIError", err, err)
			}
			if apiErr.OperationID != expectedOp ||
				apiErr.StatusCode != tt.status ||
				apiErr.Attempts != 1 {
				t.Fatalf("APIError = %#v", apiErr)
			}
			assertRedacted(t, err)
			if got := len(readLog(t, logPath)); got != 1 {
				t.Fatalf("request count = %d, want 1", got)
			}
			if effects := mock.EffectCount(); effects != 0 {
				t.Fatalf("library effects = %d, want 0", effects)
			}
		})
	}
}

func TestMalformed201IsNotRetried(t *testing.T) {
	t.Parallel()

	logPath := t.TempDir() + "/requests.jsonl"
	mock := contractmock.Start(
		t,
		"../docs/contract.json",
		logPath,
		contractmock.Scenario{
			First:               contractmock.Malformed201,
			ExpectedSession:     sessionToken,
			ExpectedClientToken: clientToken,
		},
	)
	defer mock.Close()

	client := mustClient(t, mock, sessionToken)
	datastoreID := "datastore-9"
	_, err := client.CreateLocalLibrary(
		context.Background(),
		clientToken,
		vcenter.LocalLibrarySpec{
			Name: "Malformed",
			StorageBackings: []vcenter.StorageBacking{
				{
					Type:        vcenter.StorageBackingDatastore,
					DatastoreID: &datastoreID,
				},
			},
		},
	)
	var protocolErr *vcenter.ProtocolError
	if !errors.As(err, &protocolErr) {
		t.Fatalf("error = %T %v, want *ProtocolError", err, err)
	}
	if protocolErr.OperationID != expectedOp || protocolErr.Attempts != 1 {
		t.Fatalf("ProtocolError = %#v", protocolErr)
	}
	assertRedacted(t, err)
	if got := len(readLog(t, logPath)); got != 1 {
		t.Fatalf("request count = %d, want 1", got)
	}
}

func TestRetryIsBounded(t *testing.T) {
	t.Parallel()

	logPath := t.TempDir() + "/requests.jsonl"
	mock := contractmock.Start(
		t,
		"../docs/contract.json",
		logPath,
		contractmock.Scenario{
			First:               contractmock.DropEveryResponse,
			ExpectedSession:     sessionToken,
			ExpectedClientToken: clientToken,
		},
	)
	defer mock.Close()

	client := mustClient(t, mock, sessionToken)
	datastoreID := "datastore-3"
	_, err := client.CreateLocalLibrary(
		context.Background(),
		clientToken,
		vcenter.LocalLibrarySpec{
			Name: "Bounded",
			StorageBackings: []vcenter.StorageBacking{
				{
					Type:        vcenter.StorageBackingDatastore,
					DatastoreID: &datastoreID,
				},
			},
		},
	)
	var transportErr *vcenter.TransportError
	if !errors.As(err, &transportErr) {
		t.Fatalf("error = %T %v, want *TransportError", err, err)
	}
	if transportErr.OperationID != expectedOp || transportErr.Attempts != 2 {
		t.Fatalf("TransportError = %#v", transportErr)
	}
	assertRedacted(t, err)

	records := readLog(t, logPath)
	if len(records) != 2 {
		t.Fatalf("request count = %d, want 2", len(records))
	}
	if records[0].BodyBase64 != records[1].BodyBase64 ||
		!reflect.DeepEqual(records[0].Headers, records[1].Headers) ||
		records[0].Target != records[1].Target {
		t.Fatal("bounded replay was not byte-for-byte stable")
	}
	if effects := mock.EffectCount(); effects != 1 {
		t.Fatalf("library effects = %d, want exactly 1", effects)
	}
}

func TestCanceledContextIsFinalAndSendsNothing(t *testing.T) {
	t.Parallel()

	logPath := t.TempDir() + "/requests.jsonl"
	mock := contractmock.Start(
		t,
		"../docs/contract.json",
		logPath,
		contractmock.Scenario{
			ExpectedSession:     sessionToken,
			ExpectedClientToken: clientToken,
		},
	)
	defer mock.Close()

	client := mustClient(t, mock, sessionToken)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	datastoreID := "datastore-4"
	_, err := client.CreateLocalLibrary(
		ctx,
		clientToken,
		vcenter.LocalLibrarySpec{
			Name: "Canceled",
			StorageBackings: []vcenter.StorageBacking{
				{
					Type:        vcenter.StorageBackingDatastore,
					DatastoreID: &datastoreID,
				},
			},
		},
	)
	var transportErr *vcenter.TransportError
	if !errors.As(err, &transportErr) ||
		!errors.Is(err, context.Canceled) {
		t.Fatalf("error = %T %v, want context-preserving TransportError", err, err)
	}
	assertRedacted(t, err)
	if got := len(readLog(t, logPath)); got != 0 {
		t.Fatalf("request count = %d, want 0", got)
	}
}

func TestRequestValidationPrecedesTraffic(t *testing.T) {
	t.Parallel()

	var calls atomic.Int32
	httpClient := &http.Client{
		Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
			calls.Add(1)
			return nil, errors.New("private transport detail")
		}),
	}
	client, err := vcenter.NewClient(vcenter.Config{
		BaseURL:      "https://vcenter.example",
		SessionToken: sessionToken,
		HTTPClient:   httpClient,
	})
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}

	datastoreID := "datastore-1"
	storageURI := "nfs://storage.example/library"
	blank := " "
	valid := vcenter.LocalLibrarySpec{
		Name: "Valid",
		StorageBackings: []vcenter.StorageBacking{
			{
				Type:        vcenter.StorageBackingDatastore,
				DatastoreID: &datastoreID,
			},
		},
	}
	tests := []struct {
		name  string
		ctx   context.Context
		token string
		spec  vcenter.LocalLibrarySpec
	}{
		{
			name:  "nil context",
			ctx:   nil,
			token: clientToken,
			spec:  valid,
		},
		{
			name:  "non UUID token",
			ctx:   context.Background(),
			token: "retry-token",
			spec:  valid,
		},
		{
			name:  "UUID token with non hex digit",
			ctx:   context.Background(),
			token: "b8a2a2e3-2314-43cd-a871-6ede0f42975z",
			spec:  valid,
		},
		{
			name:  "blank name",
			ctx:   context.Background(),
			token: clientToken,
			spec: vcenter.LocalLibrarySpec{
				Name:            " ",
				StorageBackings: valid.StorageBackings,
			},
		},
		{
			name:  "missing storage backing",
			ctx:   context.Background(),
			token: clientToken,
			spec:  vcenter.LocalLibrarySpec{Name: "Missing"},
		},
		{
			name:  "multiple storage backings",
			ctx:   context.Background(),
			token: clientToken,
			spec: vcenter.LocalLibrarySpec{
				Name: "Multiple",
				StorageBackings: []vcenter.StorageBacking{
					valid.StorageBackings[0],
					valid.StorageBackings[0],
				},
			},
		},
		{
			name:  "unknown backing type",
			ctx:   context.Background(),
			token: clientToken,
			spec: vcenter.LocalLibrarySpec{
				Name: "Unknown",
				StorageBackings: []vcenter.StorageBacking{
					{Type: vcenter.StorageBackingType("UNKNOWN")},
				},
			},
		},
		{
			name:  "datastore backing missing id",
			ctx:   context.Background(),
			token: clientToken,
			spec: vcenter.LocalLibrarySpec{
				Name: "Datastore",
				StorageBackings: []vcenter.StorageBacking{
					{Type: vcenter.StorageBackingDatastore},
				},
			},
		},
		{
			name:  "datastore backing has blank id",
			ctx:   context.Background(),
			token: clientToken,
			spec: vcenter.LocalLibrarySpec{
				Name: "Datastore",
				StorageBackings: []vcenter.StorageBacking{
					{
						Type:        vcenter.StorageBackingDatastore,
						DatastoreID: &blank,
					},
				},
			},
		},
		{
			name:  "datastore backing also has URI",
			ctx:   context.Background(),
			token: clientToken,
			spec: vcenter.LocalLibrarySpec{
				Name: "Datastore",
				StorageBackings: []vcenter.StorageBacking{
					{
						Type:        vcenter.StorageBackingDatastore,
						DatastoreID: &datastoreID,
						StorageURI:  &storageURI,
					},
				},
			},
		},
		{
			name:  "other backing missing URI",
			ctx:   context.Background(),
			token: clientToken,
			spec: vcenter.LocalLibrarySpec{
				Name: "Other",
				StorageBackings: []vcenter.StorageBacking{
					{Type: vcenter.StorageBackingOther},
				},
			},
		},
		{
			name:  "other backing has relative URI",
			ctx:   context.Background(),
			token: clientToken,
			spec: vcenter.LocalLibrarySpec{
				Name: "Other",
				StorageBackings: []vcenter.StorageBacking{
					{
						Type:       vcenter.StorageBackingOther,
						StorageURI: stringPointer("/relative"),
					},
				},
			},
		},
		{
			name:  "other backing also has datastore id",
			ctx:   context.Background(),
			token: clientToken,
			spec: vcenter.LocalLibrarySpec{
				Name: "Other",
				StorageBackings: []vcenter.StorageBacking{
					{
						Type:        vcenter.StorageBackingOther,
						DatastoreID: &datastoreID,
						StorageURI:  &storageURI,
					},
				},
			},
		},
	}

	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			before := calls.Load()
			_, err := client.CreateLocalLibrary(tt.ctx, tt.token, tt.spec)
			var validationErr *vcenter.ValidationError
			if !errors.As(err, &validationErr) {
				t.Fatalf("error = %T %v, want *ValidationError", err, err)
			}
			if got := calls.Load(); got != before {
				t.Fatalf("round trips changed from %d to %d", before, got)
			}
		})
	}
}

func TestNewClientValidationAndIsolation(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		cfg  vcenter.Config
	}{
		{name: "empty URL", cfg: vcenter.Config{SessionToken: sessionToken}},
		{name: "non HTTP scheme", cfg: vcenter.Config{BaseURL: "ftp://vcenter.example", SessionToken: sessionToken}},
		{name: "embedded credentials", cfg: vcenter.Config{BaseURL: "https://user:pass@vcenter.example", SessionToken: sessionToken}},
		{name: "path", cfg: vcenter.Config{BaseURL: "https://vcenter.example/sdk", SessionToken: sessionToken}},
		{name: "query", cfg: vcenter.Config{BaseURL: "https://vcenter.example?x=1", SessionToken: sessionToken}},
		{name: "fragment", cfg: vcenter.Config{BaseURL: "https://vcenter.example#x", SessionToken: sessionToken}},
		{name: "blank session", cfg: vcenter.Config{BaseURL: "https://vcenter.example", SessionToken: " "}},
		{name: "session control", cfg: vcenter.Config{BaseURL: "https://vcenter.example", SessionToken: "token\nvalue"}},
	}
	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			var calls atomic.Int32
			tt.cfg.HTTPClient = &http.Client{
				Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
					calls.Add(1)
					return nil, errors.New("unexpected request")
				}),
			}
			_, err := vcenter.NewClient(tt.cfg)
			var validationErr *vcenter.ValidationError
			if !errors.As(err, &validationErr) {
				t.Fatalf("error = %T %v, want *ValidationError", err, err)
			}
			if calls.Load() != 0 {
				t.Fatal("NewClient performed network traffic")
			}
		})
	}

	sentinel := errors.New("sentinel redirect policy")
	original := &http.Client{
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return sentinel
		},
	}
	if _, err := vcenter.NewClient(vcenter.Config{
		BaseURL:      "https://vcenter.example/",
		SessionToken: sessionToken,
		HTTPClient:   original,
	}); err != nil {
		t.Fatalf("NewClient(valid) error = %v", err)
	}
	if got := original.CheckRedirect(nil, nil); !errors.Is(got, sentinel) {
		t.Fatalf("caller-owned redirect policy was mutated: %v", got)
	}
}

func assertExactWire(
	t *testing.T,
	records []contractmock.RequestRecord,
	wantAttempts int,
	wantSession string,
	wantClientToken string,
	wantBody []byte,
) {
	t.Helper()
	if len(records) != wantAttempts {
		t.Fatalf("request count = %d, want %d", len(records), wantAttempts)
	}
	for index, record := range records {
		if record.Sequence != index+1 {
			t.Fatalf("record %d sequence = %d", index, record.Sequence)
		}
		if record.Method != http.MethodPost ||
			record.Target != "/api/content/local-library" {
			t.Fatalf(
				"record %d method/target = %q %q",
				index,
				record.Method,
				record.Target,
			)
		}
		if !reflect.DeepEqual(record.SessionToken, []string{wantSession}) ||
			!reflect.DeepEqual(record.ClientToken, []string{wantClientToken}) ||
			!reflect.DeepEqual(record.Accept, []string{"application/json"}) ||
			!reflect.DeepEqual(record.ContentType, []string{"application/json"}) {
			t.Fatalf("record %d headers = %#v", index, record.Headers)
		}
		if len(record.Authorization) != 0 {
			t.Fatalf("record %d unexpectedly sent Authorization", index)
		}
		allowedHeaders := map[string]bool{
			"accept":                true,
			"accept-encoding":       true,
			"client-token":          true,
			"content-type":          true,
			"user-agent":            true,
			"vmware-api-session-id": true,
		}
		for name := range record.Headers {
			if !allowedHeaders[strings.ToLower(name)] {
				t.Fatalf("record %d sent unexpected header %q", index, name)
			}
		}
		if record.ContentLength != int64(len(wantBody)) ||
			len(record.Transfer) != 0 {
			t.Fatalf(
				"record %d framing = length %d transfer %#v",
				index,
				record.ContentLength,
				record.Transfer,
			)
		}
		body, err := base64.StdEncoding.DecodeString(record.BodyBase64)
		if err != nil {
			t.Fatalf("decode record %d body: %v", index, err)
		}
		if !bytes.Equal(body, wantBody) {
			t.Fatalf(
				"record %d body = %q, want %q",
				index,
				body,
				wantBody,
			)
		}
		if index > 0 &&
			(records[index-1].Target != record.Target ||
				records[index-1].BodyBase64 != record.BodyBase64 ||
				!reflect.DeepEqual(records[index-1].Headers, record.Headers)) {
			t.Fatalf("record %d is not an exact replay", index)
		}
	}
}

func assertUnsetMembersOmitted(t *testing.T, body []byte) {
	t.Helper()
	var decoded map[string]any
	if err := json.Unmarshal(body, &decoded); err != nil {
		t.Fatalf("decode request body: %v", err)
	}
	if len(decoded) != 2 {
		t.Fatalf("top-level JSON members = %#v", decoded)
	}
	for _, name := range []string{
		"description",
		"id",
		"creation_time",
		"last_modified_time",
		"last_sync_time",
		"type",
		"optimization_info",
		"version",
		"publish_info",
		"subscription_info",
		"server_guid",
		"security_policy_id",
		"unset_security_policy_id",
		"state_info",
		"configuration_info",
	} {
		if _, exists := decoded[name]; exists {
			t.Fatalf("unset or server-managed member %q was sent", name)
		}
	}
	backings, ok := decoded["storage_backings"].([]any)
	if !ok || len(backings) != 1 {
		t.Fatalf("storage_backings = %#v", decoded["storage_backings"])
	}
	backing, ok := backings[0].(map[string]any)
	if !ok || len(backing) != 2 {
		t.Fatalf("storage backing = %#v", backings[0])
	}
	if _, exists := backing["storage_uri"]; exists {
		t.Fatal("unset storage_uri was sent")
	}
}

func assertRedacted(t *testing.T, err error) {
	t.Helper()
	text := err.Error()
	for _, secret := range []string{
		sessionToken,
		clientToken,
		"fixture response body must remain private",
		"private transport detail",
	} {
		if strings.Contains(text, secret) {
			t.Fatalf("error text leaked %q: %q", secret, text)
		}
	}
}

func mustClient(
	t *testing.T,
	mock *contractmock.Server,
	token string,
) *vcenter.Client {
	t.Helper()
	client, err := vcenter.NewClient(vcenter.Config{
		BaseURL:      mock.URL,
		SessionToken: token,
		HTTPClient:   mock.Client,
	})
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}
	return client
}

func readLog(t *testing.T, path string) []contractmock.RequestRecord {
	t.Helper()
	records, err := contractmock.ReadLog(path)
	if err != nil {
		t.Fatalf("ReadLog() error = %v", err)
	}
	return records
}

func readJSON(t *testing.T, path string, target any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func stringPointer(value string) *string {
	return &value
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return fn(request)
}
