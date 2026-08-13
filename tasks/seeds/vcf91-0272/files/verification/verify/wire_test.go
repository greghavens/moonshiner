package verify

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"reflect"
	"sort"
	"sync"
	"testing"

	"vcfops/internal/opsmock"
	"vcfops/opsadapter"
)

const (
	acquirePath  = opsmock.BasePath + "/api/auth/token/acquire"
	precheckPath = opsmock.BasePath + "/api/adapters/testConnection"
	createPath   = opsmock.BasePath + "/api/adapters"
)

func i32(v int32) *int32 { return &v }

func newClient(t *testing.T, baseURL string, httpClient *http.Client) *opsadapter.Client {
	t.Helper()
	c, err := opsadapter.NewClient(baseURL, httpClient)
	if err != nil {
		t.Fatalf("NewClient(%q): %v", baseURL, err)
	}
	if c == nil {
		t.Fatalf("NewClient(%q) returned a nil client and a nil error", baseURL)
	}
	return c
}

// objectKeys returns the sorted top level member names of a JSON object body.
func objectKeys(t *testing.T, body []byte) []string {
	t.Helper()
	obj := decodeObject(t, body)
	keys := make([]string, 0, len(obj))
	for k := range obj {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func decodeObject(t *testing.T, body []byte) map[string]any {
	t.Helper()
	var obj map[string]any
	if err := json.Unmarshal(body, &obj); err != nil {
		t.Fatalf("request body is not a JSON object: %v (body %q)", err, string(body))
	}
	return obj
}

// byOperation indexes the request log, failing if any request reached a path
// outside the contract.
func byOperation(t *testing.T, recs []opsmock.RequestRecord) map[string][]opsmock.RequestRecord {
	t.Helper()
	out := map[string][]opsmock.RequestRecord{}
	for _, r := range recs {
		if r.OperationID == "" {
			t.Fatalf("request %s %s reached a path the contract does not name", r.Method, r.Path)
		}
		out[r.OperationID] = append(out[r.OperationID], r)
	}
	return out
}

func operationOrder(recs []opsmock.RequestRecord) []string {
	out := make([]string, 0, len(recs))
	for _, r := range recs {
		out = append(out, r.OperationID)
	}
	return out
}

func requireHeader(t *testing.T, r opsmock.RequestRecord, name, want string) {
	t.Helper()
	if got := r.Header.Get(name); got != want {
		t.Errorf("%s: header %s = %q, want %q", r.OperationID, name, got, want)
	}
}

// TestRegisterHappyPath pins the full wire shape of a successful registration.
func TestRegisterHappyPath(t *testing.T) {
	t.Parallel()
	srv := opsmock.Start(opsmock.Config{})
	defer srv.Close()
	cfg := srv.Config()

	c := newClient(t, srv.URL, srv.HTTPClient())
	ctx := context.Background()

	token, err := c.AcquireToken(ctx, cfg.Username, cfg.Password, "")
	if err != nil {
		t.Fatalf("AcquireToken: %v", err)
	}
	if token != cfg.Token {
		t.Fatalf("AcquireToken returned %q, want %q", token, cfg.Token)
	}

	inst, err := c.Register(ctx, token, opsadapter.CreateAdapterInstance{
		Name:           "vc-prod-01",
		AdapterKindKey: "VMWARE",
	})
	if err != nil {
		t.Fatalf("Register: %v", err)
	}

	if inst.ID != cfg.InstanceID {
		t.Errorf("AdapterInstance.ID = %q, want %q", inst.ID, cfg.InstanceID)
	}
	if inst.Name != "vc-prod-01" {
		t.Errorf("AdapterInstance.Name = %q, want %q", inst.Name, "vc-prod-01")
	}
	if inst.AdapterKindKey != "VMWARE" {
		t.Errorf("AdapterInstance.AdapterKindKey = %q, want %q", inst.AdapterKindKey, "VMWARE")
	}
	if inst.ResourceKindKey != cfg.ResourceKindKey {
		t.Errorf("AdapterInstance.ResourceKindKey = %q, want %q", inst.ResourceKindKey, cfg.ResourceKindKey)
	}

	recs := srv.Requests()
	wantOrder := []string{"acquireToken", "testConnection", "createAdapterInstance"}
	if got := operationOrder(recs); !reflect.DeepEqual(got, wantOrder) {
		t.Fatalf("request log = %v, want %v", got, wantOrder)
	}
	idx := byOperation(t, recs)

	acquire := idx["acquireToken"][0]
	if acquire.Method != http.MethodPost || acquire.Path != acquirePath {
		t.Errorf("acquireToken sent %s %s, want POST %s", acquire.Method, acquire.Path, acquirePath)
	}
	if acquire.RawQuery != "" {
		t.Errorf("acquireToken sent query %q, want none", acquire.RawQuery)
	}
	if got := acquire.Header.Get("Authorization"); got != "" {
		t.Errorf("acquireToken sent Authorization %q, want none", got)
	}
	requireHeader(t, acquire, "Content-Type", "application/json")
	requireHeader(t, acquire, "Accept", "application/json")
	if got, want := objectKeys(t, acquire.Body), []string{"password", "username"}; !reflect.DeepEqual(got, want) {
		t.Errorf("acquireToken body members = %v, want %v (body %q)", got, want, string(acquire.Body))
	}

	precheck := idx["testConnection"][0]
	if precheck.Method != http.MethodPost || precheck.Path != precheckPath {
		t.Errorf("testConnection sent %s %s, want POST %s", precheck.Method, precheck.Path, precheckPath)
	}
	if precheck.RawQuery != "" {
		t.Errorf("testConnection sent query %q, want none", precheck.RawQuery)
	}
	requireHeader(t, precheck, "Authorization", opsmock.TokenScheme+" "+cfg.Token)
	requireHeader(t, precheck, "Content-Type", "application/json")
	requireHeader(t, precheck, "Accept", "application/json")

	create := idx["createAdapterInstance"][0]
	if create.Method != http.MethodPost || create.Path != createPath {
		t.Errorf("createAdapterInstance sent %s %s, want POST %s", create.Method, create.Path, createPath)
	}
	requireHeader(t, create, "Authorization", opsmock.TokenScheme+" "+cfg.Token)
	requireHeader(t, create, "Content-Type", "application/json")
	requireHeader(t, create, "Accept", "application/json")
	if len(create.Query) != 1 {
		t.Errorf("createAdapterInstance query = %v, want only force", create.Query)
	}
	if got := create.Query["force"]; len(got) != 1 || got[0] != "false" {
		t.Errorf("createAdapterInstance force = %v, want [false]", got)
	}
	if _, ok := create.Query["extractIdentifierDefaults"]; ok {
		t.Error("createAdapterInstance sent extractIdentifierDefaults, which was never set")
	}

	// The precheck and the create must describe the same adapter instance.
	if !reflect.DeepEqual(decodeObject(t, precheck.Body), decodeObject(t, create.Body)) {
		t.Errorf("precheck body %q differs from create body %q", string(precheck.Body), string(create.Body))
	}

	if got := srv.Instances(); len(got) != 1 {
		t.Fatalf("server holds %d adapter instances, want 1", len(got))
	}
}

// TestRequestBodyOmitsUnsetOptionalMembers is the core wire shape table: an
// optional member that was not set must be absent from the payload rather than
// present and empty.
func TestRequestBodyOmitsUnsetOptionalMembers(t *testing.T) {
	t.Parallel()

	full := opsadapter.CreateAdapterInstance{
		Name:                      "vc-prod-01",
		AdapterKindKey:            "VMWARE",
		Description:               "Production vCenter",
		CollectorID:               "1",
		CollectorGroupID:          "b1f0e1a1-1111-4111-8111-111111111111",
		PhysicalDatacenterID:      "e41ee4fc-8041-44f4-864e-7e1d17773ce2",
		MonitoringInterval:        i32(5),
		MonitoringIntervalSeconds: i32(30),
		ResourceIdentifiers: []opsadapter.NameValue{
			{Name: "AUTODISCOVERY", Value: "true"},
			{Name: "VCURL", Value: "vc-prod-01.example.com"},
		},
		Credential: &opsadapter.Credential{
			Name:              "Principal Credential",
			AdapterKindKey:    "VMWARE",
			CredentialKindKey: "PRINCIPALCREDENTIAL",
			Fields: []opsadapter.NameValue{
				{Name: "USER", Value: "svc-vcfops"},
				{Name: "PASSWORD", Value: "s3cr3t"},
			},
		},
	}

	tests := []struct {
		name     string
		spec     opsadapter.CreateAdapterInstance
		wantKeys []string
		check    func(t *testing.T, body map[string]any)
	}{
		{
			name:     "only the required members",
			spec:     opsadapter.CreateAdapterInstance{Name: "vc-prod-01", AdapterKindKey: "VMWARE"},
			wantKeys: []string{"adapterKindKey", "name"},
			check: func(t *testing.T, body map[string]any) {
				if body["name"] != "vc-prod-01" {
					t.Errorf("name = %v", body["name"])
				}
				if body["adapterKindKey"] != "VMWARE" {
					t.Errorf("adapterKindKey = %v", body["adapterKindKey"])
				}
			},
		},
		{
			name: "description only",
			spec: opsadapter.CreateAdapterInstance{
				Name: "vc-prod-01", AdapterKindKey: "VMWARE", Description: "Production vCenter",
			},
			wantKeys: []string{"adapterKindKey", "description", "name"},
		},
		{
			name: "collector placement only",
			spec: opsadapter.CreateAdapterInstance{
				Name: "vc-prod-01", AdapterKindKey: "VMWARE",
				CollectorID:      "1",
				CollectorGroupID: "b1f0e1a1-1111-4111-8111-111111111111",
			},
			wantKeys: []string{"adapterKindKey", "collectorGroupId", "collectorId", "name"},
			check: func(t *testing.T, body map[string]any) {
				if body["collectorId"] != "1" {
					t.Errorf("collectorId = %v, want \"1\"", body["collectorId"])
				}
			},
		},
		{
			name: "physical datacenter only",
			spec: opsadapter.CreateAdapterInstance{
				Name: "vc-prod-01", AdapterKindKey: "VMWARE",
				PhysicalDatacenterID: "e41ee4fc-8041-44f4-864e-7e1d17773ce2",
			},
			wantKeys: []string{"adapterKindKey", "name", "physicalDatacenterId"},
		},
		{
			name: "explicit zero monitoring interval is sent",
			spec: opsadapter.CreateAdapterInstance{
				Name: "vc-prod-01", AdapterKindKey: "VMWARE",
				MonitoringInterval: i32(0),
			},
			wantKeys: []string{"adapterKindKey", "monitoringInterval", "name"},
			check: func(t *testing.T, body map[string]any) {
				if got, ok := body["monitoringInterval"].(float64); !ok || got != 0 {
					t.Errorf("monitoringInterval = %v, want 0", body["monitoringInterval"])
				}
			},
		},
		{
			name: "seconds part only",
			spec: opsadapter.CreateAdapterInstance{
				Name: "vc-prod-01", AdapterKindKey: "VMWARE",
				MonitoringIntervalSeconds: i32(30),
			},
			wantKeys: []string{"adapterKindKey", "monitoringIntervalSeconds", "name"},
		},
		{
			name: "empty resource identifier slice is omitted",
			spec: opsadapter.CreateAdapterInstance{
				Name: "vc-prod-01", AdapterKindKey: "VMWARE",
				ResourceIdentifiers: []opsadapter.NameValue{},
			},
			wantKeys: []string{"adapterKindKey", "name"},
		},
		{
			name: "resource identifiers",
			spec: opsadapter.CreateAdapterInstance{
				Name: "vc-prod-01", AdapterKindKey: "VMWARE",
				ResourceIdentifiers: []opsadapter.NameValue{{Name: "VCURL", Value: "vc-prod-01.example.com"}},
			},
			wantKeys: []string{"adapterKindKey", "name", "resourceIdentifiers"},
			check: func(t *testing.T, body map[string]any) {
				ids, ok := body["resourceIdentifiers"].([]any)
				if !ok || len(ids) != 1 {
					t.Fatalf("resourceIdentifiers = %v, want one entry", body["resourceIdentifiers"])
				}
				entry, ok := ids[0].(map[string]any)
				if !ok {
					t.Fatalf("resourceIdentifiers[0] = %v, want an object", ids[0])
				}
				if !reflect.DeepEqual(entry, map[string]any{"name": "VCURL", "value": "vc-prod-01.example.com"}) {
					t.Errorf("resourceIdentifiers[0] = %v", entry)
				}
			},
		},
		{
			name: "credential without fields",
			spec: opsadapter.CreateAdapterInstance{
				Name: "vc-prod-01", AdapterKindKey: "VMWARE",
				Credential: &opsadapter.Credential{
					Name:              "Principal Credential",
					AdapterKindKey:    "VMWARE",
					CredentialKindKey: "PRINCIPALCREDENTIAL",
				},
			},
			wantKeys: []string{"adapterKindKey", "credential", "name"},
			check: func(t *testing.T, body map[string]any) {
				cred, ok := body["credential"].(map[string]any)
				if !ok {
					t.Fatalf("credential = %v, want an object", body["credential"])
				}
				want := map[string]any{
					"name":              "Principal Credential",
					"adapterKindKey":    "VMWARE",
					"credentialKindKey": "PRINCIPALCREDENTIAL",
				}
				if !reflect.DeepEqual(cred, want) {
					t.Errorf("credential = %v, want %v", cred, want)
				}
			},
		},
		{
			name: "credential with fields",
			spec: opsadapter.CreateAdapterInstance{
				Name: "vc-prod-01", AdapterKindKey: "VMWARE",
				Credential: &opsadapter.Credential{
					Name:              "Principal Credential",
					AdapterKindKey:    "VMWARE",
					CredentialKindKey: "PRINCIPALCREDENTIAL",
					Fields:            []opsadapter.NameValue{{Name: "USER", Value: "svc-vcfops"}},
				},
			},
			wantKeys: []string{"adapterKindKey", "credential", "name"},
			check: func(t *testing.T, body map[string]any) {
				cred, ok := body["credential"].(map[string]any)
				if !ok {
					t.Fatalf("credential = %v, want an object", body["credential"])
				}
				fields, ok := cred["fields"].([]any)
				if !ok || len(fields) != 1 {
					t.Fatalf("credential.fields = %v, want one entry", cred["fields"])
				}
			},
		},
		{
			name:     "every member set",
			spec:     full,
			wantKeys: append([]string{"name", "adapterKindKey"}, wantCreateOptionalFields...),
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := opsmock.Start(opsmock.Config{})
			defer srv.Close()
			cfg := srv.Config()

			c := newClient(t, srv.URL, srv.HTTPClient())
			ctx := context.Background()
			token, err := c.AcquireToken(ctx, cfg.Username, cfg.Password, "")
			if err != nil {
				t.Fatalf("AcquireToken: %v", err)
			}
			if _, err := c.Register(ctx, token, tc.spec); err != nil {
				t.Fatalf("Register: %v", err)
			}

			idx := byOperation(t, srv.Requests())
			if len(idx["testConnection"]) != 1 || len(idx["createAdapterInstance"]) != 1 {
				t.Fatalf("want one precheck and one create, got %d and %d",
					len(idx["testConnection"]), len(idx["createAdapterInstance"]))
			}

			wantKeys := sorted(tc.wantKeys)
			for _, rec := range []opsmock.RequestRecord{idx["testConnection"][0], idx["createAdapterInstance"][0]} {
				if got := objectKeys(t, rec.Body); !reflect.DeepEqual(got, wantKeys) {
					t.Errorf("%s body members = %v, want %v (body %q)", rec.OperationID, got, wantKeys, string(rec.Body))
				}
				if tc.check != nil {
					tc.check(t, decodeObject(t, rec.Body))
				}
			}
		})
	}
}

func TestAcquireTokenOmitsUnsetAuthSource(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name       string
		authSource string
		wantKeys   []string
	}{
		{name: "local user directory", authSource: "", wantKeys: []string{"password", "username"}},
		{name: "named auth source", authSource: "Corp LDAP", wantKeys: []string{"authSource", "password", "username"}},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := opsmock.Start(opsmock.Config{AuthSource: tc.authSource})
			defer srv.Close()
			cfg := srv.Config()

			c := newClient(t, srv.URL, srv.HTTPClient())
			token, err := c.AcquireToken(context.Background(), cfg.Username, cfg.Password, tc.authSource)
			if err != nil {
				t.Fatalf("AcquireToken: %v", err)
			}
			if token != cfg.Token {
				t.Fatalf("token = %q, want %q", token, cfg.Token)
			}

			rec := srv.Requests()[0]
			if got := objectKeys(t, rec.Body); !reflect.DeepEqual(got, tc.wantKeys) {
				t.Errorf("body members = %v, want %v (body %q)", got, tc.wantKeys, string(rec.Body))
			}
		})
	}
}

// TestFailedPrecheckChangesNothing is the gate: when the precheck rejects the
// adapter instance the create must never be sent and the server must hold no
// adapter instance.
func TestFailedPrecheckChangesNothing(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name        string
		status      int
		message     string
		wantKind    string // "precheck" or "api"
		wantStatus  int
		wantOpForID string
	}{
		{
			name:       "endpoint rejects the connection",
			status:     http.StatusBadRequest,
			message:    "Unable to establish a connection to the endpoint.",
			wantKind:   "precheck",
			wantStatus: http.StatusBadRequest,
		},
		{
			name:       "certificate not trusted",
			status:     http.StatusBadRequest,
			message:    "The certificate presented by the endpoint is not trusted.",
			wantKind:   "precheck",
			wantStatus: http.StatusBadRequest,
		},
		{
			name:        "token rejected",
			status:      http.StatusUnauthorized,
			message:     "Invalid or missing authorization token",
			wantKind:    "api",
			wantStatus:  http.StatusUnauthorized,
			wantOpForID: "testConnection",
		},
		{
			name:        "server error",
			status:      http.StatusInternalServerError,
			message:     "Internal Server Error",
			wantKind:    "api",
			wantStatus:  http.StatusInternalServerError,
			wantOpForID: "testConnection",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := opsmock.Start(opsmock.Config{PrecheckStatus: tc.status, PrecheckMessage: tc.message})
			defer srv.Close()
			cfg := srv.Config()

			c := newClient(t, srv.URL, srv.HTTPClient())
			ctx := context.Background()
			token, err := c.AcquireToken(ctx, cfg.Username, cfg.Password, "")
			if err != nil {
				t.Fatalf("AcquireToken: %v", err)
			}

			inst, err := c.Register(ctx, token, opsadapter.CreateAdapterInstance{
				Name: "vc-prod-01", AdapterKindKey: "VMWARE",
			})
			if err == nil {
				t.Fatal("Register succeeded, want an error")
			}
			if inst != (opsadapter.AdapterInstance{}) {
				t.Errorf("Register returned %+v, want the zero AdapterInstance", inst)
			}

			switch tc.wantKind {
			case "precheck":
				var pe *opsadapter.PrecheckError
				if !errors.As(err, &pe) {
					t.Fatalf("Register error is %T (%v), want *opsadapter.PrecheckError", err, err)
				}
				if pe.StatusCode != tc.wantStatus {
					t.Errorf("PrecheckError.StatusCode = %d, want %d", pe.StatusCode, tc.wantStatus)
				}
				if pe.Message != tc.message {
					t.Errorf("PrecheckError.Message = %q, want %q", pe.Message, tc.message)
				}
			case "api":
				var ae *opsadapter.APIError
				if !errors.As(err, &ae) {
					t.Fatalf("Register error is %T (%v), want *opsadapter.APIError", err, err)
				}
				if ae.StatusCode != tc.wantStatus {
					t.Errorf("APIError.StatusCode = %d, want %d", ae.StatusCode, tc.wantStatus)
				}
				if ae.OperationID != tc.wantOpForID {
					t.Errorf("APIError.OperationID = %q, want %q", ae.OperationID, tc.wantOpForID)
				}
			}

			idx := byOperation(t, srv.Requests())
			if n := len(idx["testConnection"]); n != 1 {
				t.Errorf("precheck was sent %d times, want 1", n)
			}
			if n := len(idx["createAdapterInstance"]); n != 0 {
				t.Errorf("createAdapterInstance was sent %d times after a failed precheck, want 0", n)
			}
			if got := srv.Instances(); len(got) != 0 {
				t.Errorf("server holds %d adapter instances after a failed precheck, want 0", len(got))
			}
		})
	}
}

func TestCreateFailureIsReportedAsAPIError(t *testing.T) {
	t.Parallel()
	srv := opsmock.Start(opsmock.Config{
		CreateStatus:  http.StatusConflict,
		CreateMessage: "An adapter instance with that name already exists.",
	})
	defer srv.Close()
	cfg := srv.Config()

	c := newClient(t, srv.URL, srv.HTTPClient())
	ctx := context.Background()
	token, err := c.AcquireToken(ctx, cfg.Username, cfg.Password, "")
	if err != nil {
		t.Fatalf("AcquireToken: %v", err)
	}

	_, err = c.Register(ctx, token, opsadapter.CreateAdapterInstance{Name: "vc-prod-01", AdapterKindKey: "VMWARE"})
	var ae *opsadapter.APIError
	if !errors.As(err, &ae) {
		t.Fatalf("Register error is %T (%v), want *opsadapter.APIError", err, err)
	}
	if ae.OperationID != "createAdapterInstance" {
		t.Errorf("APIError.OperationID = %q, want %q", ae.OperationID, "createAdapterInstance")
	}
	if ae.StatusCode != http.StatusConflict {
		t.Errorf("APIError.StatusCode = %d, want %d", ae.StatusCode, http.StatusConflict)
	}
	if ae.Message != "An adapter instance with that name already exists." {
		t.Errorf("APIError.Message = %q", ae.Message)
	}
	if got := srv.Instances(); len(got) != 0 {
		t.Errorf("server holds %d adapter instances, want 0", len(got))
	}
}

func TestAcquireTokenRejectsBadCredentials(t *testing.T) {
	t.Parallel()
	srv := opsmock.Start(opsmock.Config{})
	defer srv.Close()

	c := newClient(t, srv.URL, srv.HTTPClient())
	_, err := c.AcquireToken(context.Background(), srv.Config().Username, "wrong", "")
	var ae *opsadapter.APIError
	if !errors.As(err, &ae) {
		t.Fatalf("AcquireToken error is %T (%v), want *opsadapter.APIError", err, err)
	}
	if ae.OperationID != "acquireToken" {
		t.Errorf("APIError.OperationID = %q, want %q", ae.OperationID, "acquireToken")
	}
	if ae.StatusCode != http.StatusUnauthorized {
		t.Errorf("APIError.StatusCode = %d, want %d", ae.StatusCode, http.StatusUnauthorized)
	}
}

func TestNewClientRejectsUnusableBaseURL(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name    string
		baseURL string
		wantErr bool
	}{
		{name: "empty", baseURL: "", wantErr: true},
		{name: "no scheme", baseURL: "ops.example.com", wantErr: true},
		{name: "not http", baseURL: "ftp://ops.example.com", wantErr: true},
		{name: "base path", baseURL: "https://ops.example.com/suite-api", wantErr: true},
		{name: "query", baseURL: "https://ops.example.com?region=west", wantErr: true},
		{name: "fragment", baseURL: "https://ops.example.com#deployment", wantErr: true},
		{name: "userinfo", baseURL: "https://admin@ops.example.com", wantErr: true},
		{name: "https", baseURL: "https://ops.example.com", wantErr: false},
		{name: "trailing slash", baseURL: "https://ops.example.com/", wantErr: false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			_, err := opsadapter.NewClient(tc.baseURL, nil)
			if tc.wantErr && err == nil {
				t.Errorf("NewClient(%q) succeeded, want an error", tc.baseURL)
			}
			if !tc.wantErr && err != nil {
				t.Errorf("NewClient(%q): %v", tc.baseURL, err)
			}
		})
	}
}

// TestBaseURLWithTrailingSlash guards against a doubled separator in the
// request path.
func TestBaseURLWithTrailingSlash(t *testing.T) {
	t.Parallel()
	srv := opsmock.Start(opsmock.Config{})
	defer srv.Close()
	cfg := srv.Config()

	c := newClient(t, srv.URL+"/", srv.HTTPClient())
	ctx := context.Background()
	token, err := c.AcquireToken(ctx, cfg.Username, cfg.Password, "")
	if err != nil {
		t.Fatalf("AcquireToken: %v", err)
	}
	if _, err := c.Register(ctx, token, opsadapter.CreateAdapterInstance{Name: "vc-prod-01", AdapterKindKey: "VMWARE"}); err != nil {
		t.Fatalf("Register: %v", err)
	}
	for _, rec := range srv.Requests() {
		if rec.OperationID == "" {
			t.Fatalf("request reached %q, which the contract does not name", rec.Path)
		}
	}
}

// TestConcurrentRegistrations exercises the client from several goroutines so
// that -race can observe shared state.
func TestConcurrentRegistrations(t *testing.T) {
	t.Parallel()
	srv := opsmock.Start(opsmock.Config{})
	defer srv.Close()
	cfg := srv.Config()

	c := newClient(t, srv.URL, srv.HTTPClient())
	ctx := context.Background()
	token, err := c.AcquireToken(ctx, cfg.Username, cfg.Password, "")
	if err != nil {
		t.Fatalf("AcquireToken: %v", err)
	}

	const n = 8
	var wg sync.WaitGroup
	errs := make([]error, n)
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_, errs[i] = c.Register(ctx, token, opsadapter.CreateAdapterInstance{
				Name:                "vc-prod-01",
				AdapterKindKey:      "VMWARE",
				MonitoringInterval:  i32(int32(i)),
				ResourceIdentifiers: []opsadapter.NameValue{{Name: "VCURL", Value: "vc.example.com"}},
			})
		}(i)
	}
	wg.Wait()

	for i, err := range errs {
		if err != nil {
			t.Errorf("Register %d: %v", i, err)
		}
	}
	if got := srv.Instances(); len(got) != n {
		t.Errorf("server holds %d adapter instances, want %d", len(got), n)
	}
}
