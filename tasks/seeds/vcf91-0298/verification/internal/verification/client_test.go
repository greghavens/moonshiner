package verification_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"

	"example.com/vcf-operations-networks-datasource-onboarder/internal/contractmock"
	"example.com/vcf-operations-networks-datasource-onboarder/vcfnetworks"
)

const (
	pinnedCommit = "c3f3b52c845dd967cabbc21680e893292077d5ba"
	specPath     = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
	serviceRoot  = "/api/ni"

	loginUser     = "admin@local"
	loginPassword = "loopback-login-secret"
	issuedToken   = "1rT7tm4riiACSfxrO2BvkA=="

	existingIPID   = "18230:902:993642895"
	existingFQDNID = "18230:902:627340998"
	mintedID       = "18230:902:100000001"
)

func protectedPath(parts ...string) string {
	return filepath.Join(append([]string{"..", ".."}, parts...)...)
}

func pointer[T any](value T) *T { return &value }

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

// ---------------------------------------------------------------------------
// Contract-pinned fake backend
// ---------------------------------------------------------------------------

// backend is a small stateful implementation of the four contract operations.
// An override, when supplied, is consulted first so failure paths can be driven
// precisely.
type backend struct {
	mu       sync.Mutex
	sources  []vcfnetworks.VcenterDataSource
	minted   int
	override func(contractmock.Request) (contractmock.Response, bool)
}

func (b *backend) seed(sources ...vcfnetworks.VcenterDataSource) {
	b.sources = append(b.sources, sources...)
}

func (b *backend) responder(t *testing.T) contractmock.Responder {
	t.Helper()
	return func(request contractmock.Request) contractmock.Response {
		if b.override != nil {
			if response, handled := b.override(request); handled {
				return response
			}
		}
		switch request.OperationID {
		case "create":
			return contractmock.JSONResponse(t, http.StatusOK, map[string]any{
				"token": issuedToken, "expiry": 1605201960327,
			})
		case "listVcenters":
			b.mu.Lock()
			defer b.mu.Unlock()
			results := make([]map[string]string, 0, len(b.sources))
			for _, source := range b.sources {
				results = append(results, map[string]string{
					"entity_id": source.EntityID, "entity_type": source.EntityType,
				})
			}
			return contractmock.JSONResponse(t, http.StatusOK, map[string]any{
				"results": results, "total_count": len(results),
			})
		case "getVcenter":
			b.mu.Lock()
			defer b.mu.Unlock()
			for _, source := range b.sources {
				if source.EntityID == request.PathParams["id"] {
					return contractmock.JSONResponse(t, http.StatusOK, source)
				}
			}
			return contractmock.JSONResponse(t, http.StatusNotFound, map[string]any{
				"code": 404, "message": "data source not found",
			})
		case "addVcenterDatasource":
			var body struct {
				IP          string  `json:"ip"`
				FQDN        string  `json:"fqdn"`
				ProxyID     string  `json:"proxy_id"`
				Nickname    string  `json:"nickname"`
				Enabled     *bool   `json:"enabled"`
				Notes       *string `json:"notes"`
				Credentials struct {
					Username string `json:"username"`
				} `json:"credentials"`
			}
			if err := json.Unmarshal(request.Body, &body); err != nil {
				return contractmock.JSONResponse(t, http.StatusBadRequest, map[string]any{
					"code": 400, "message": "malformed body",
				})
			}
			b.mu.Lock()
			defer b.mu.Unlock()
			b.minted++
			created := vcfnetworks.VcenterDataSource{
				EntityID:   fmt.Sprintf("18230:902:%d", 100000000+b.minted),
				EntityType: "VCenterDataSource",
				IP:         body.IP,
				FQDN:       body.FQDN,
				ProxyID:    body.ProxyID,
				Nickname:   body.Nickname,
				Enabled:    body.Enabled == nil || *body.Enabled,
			}
			if body.Notes != nil {
				created.Notes = *body.Notes
			}
			b.sources = append(b.sources, created)
			return contractmock.JSONResponse(t, http.StatusCreated, created)
		}
		t.Errorf("responder reached for unnamed operation %q", request.OperationID)
		return contractmock.Response{Status: http.StatusInternalServerError}
	}
}

func newHarness(t *testing.T, b *backend) (*vcfnetworks.Client, *contractmock.Server) {
	t.Helper()
	server := contractmock.New(t, protectedPath("docs", "contract.json"), b.responder(t))
	client, err := vcfnetworks.NewClient(server.URL(), loginUser, loginPassword, nil)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if client == nil {
		t.Fatal("NewClient returned a nil client and a nil error")
	}
	return client, server
}

func vcenterWithIP(id, ip, nickname string) vcfnetworks.VcenterDataSource {
	return vcfnetworks.VcenterDataSource{
		EntityID: id, EntityType: "VCenterDataSource", IP: ip,
		ProxyID: "18230:901:1585583463", Nickname: nickname, Enabled: true,
	}
}

func vcenterWithFQDN(id, fqdn, nickname string) vcfnetworks.VcenterDataSource {
	return vcfnetworks.VcenterDataSource{
		EntityID: id, EntityType: "VCenterDataSource", FQDN: fqdn,
		ProxyID: "18230:901:1585583463", Nickname: nickname, Enabled: true,
	}
}

// ---------------------------------------------------------------------------
// Assertion helpers
// ---------------------------------------------------------------------------

func objectKeys(t *testing.T, body []byte) []string {
	t.Helper()
	var object map[string]json.RawMessage
	if err := json.Unmarshal(body, &object); err != nil {
		t.Fatalf("request body is not a JSON object: %v (%s)", err, body)
	}
	keys := make([]string, 0, len(object))
	for key := range object {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func member(t *testing.T, body []byte, name string) (json.RawMessage, bool) {
	t.Helper()
	var object map[string]json.RawMessage
	if err := json.Unmarshal(body, &object); err != nil {
		t.Fatalf("request body is not a JSON object: %v (%s)", err, body)
	}
	value, ok := object[name]
	return value, ok
}

func wantHeader(t *testing.T, request contractmock.Request, name string, want []string) {
	t.Helper()
	if got := request.Header.Values(name); !reflect.DeepEqual(got, want) {
		t.Errorf("%s %s header %s = %v, want %v", request.Method, request.Path, name, got, want)
	}
}

func wantBodyless(t *testing.T, request contractmock.Request) {
	t.Helper()
	if len(request.Body) != 0 {
		t.Errorf("%s %s carried a body %q", request.Method, request.Path, request.Body)
	}
	if request.ContentLength > 0 {
		t.Errorf("%s %s declared Content-Length %d", request.Method, request.Path, request.ContentLength)
	}
	if len(request.TransferEncoding) != 0 {
		t.Errorf("%s %s used transfer encoding %v", request.Method, request.Path, request.TransferEncoding)
	}
	if values := request.Header.Values("Content-Type"); len(values) != 0 {
		t.Errorf("bodyless %s %s sent Content-Type %v", request.Method, request.Path, values)
	}
}

func wantNoQuery(t *testing.T, request contractmock.Request) {
	t.Helper()
	if strings.Contains(request.RequestURI, "?") {
		t.Errorf("%s carried a query string; the contract declares no parameters", request.RequestURI)
	}
}

func operations(server *contractmock.Server) []string { return server.OperationIDs() }

func countOperation(server *contractmock.Server, operationID string) int {
	total := 0
	for _, id := range operations(server) {
		if id == operationID {
			total++
		}
	}
	return total
}

func requestsFor(server *contractmock.Server, operationID string) []contractmock.Request {
	var matched []contractmock.Request
	for _, request := range server.Requests() {
		if request.OperationID == operationID {
			matched = append(matched, request)
		}
	}
	return matched
}

func wantNoSecretLeak(t *testing.T, err error) {
	t.Helper()
	if err == nil {
		return
	}
	for _, secret := range []string{loginPassword, issuedToken} {
		if strings.Contains(err.Error(), secret) {
			t.Errorf("error message discloses a secret: %v", err)
		}
	}
}

// ---------------------------------------------------------------------------
// Provenance and mock surface
// ---------------------------------------------------------------------------

func TestPinnedContractProvenanceAndMockSurface(t *testing.T) {
	t.Parallel()

	var sources struct {
		Repository   string   `json:"repository"`
		License      string   `json:"license"`
		CommitSHA    string   `json:"commitSha"`
		SpecPath     string   `json:"specPath"`
		SpecVersion  string   `json:"specVersion"`
		ServiceRoot  string   `json:"serviceRoot"`
		OperationIDs []string `json:"operationIds"`
		Operations   []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
			Source      string `json:"source"`
		} `json:"operations"`
	}
	data, err := os.ReadFile(protectedPath("docs", "official_sources.json"))
	if err != nil {
		t.Fatalf("read official sources: %v", err)
	}
	if err := json.Unmarshal(data, &sources); err != nil {
		t.Fatalf("decode official sources: %v", err)
	}
	if sources.Repository != "https://github.com/vmware/vcf-api-specs" ||
		sources.License != "Apache-2.0" || sources.CommitSHA != pinnedCommit ||
		sources.SpecPath != specPath || sources.SpecVersion != "9.1.0.0" ||
		sources.ServiceRoot != serviceRoot {
		t.Fatalf("unexpected source provenance: %+v", sources)
	}
	if !strings.HasSuffix(sources.SpecPath, ".yaml") {
		t.Fatalf("contract basis %q is not the specification document", sources.SpecPath)
	}

	wantRoutes := map[string][2]string{
		"create":               {http.MethodPost, "/auth/token"},
		"listVcenters":         {http.MethodGet, "/data-sources/vcenters"},
		"getVcenter":           {http.MethodGet, "/data-sources/vcenters/{id}"},
		"addVcenterDatasource": {http.MethodPost, "/data-sources/vcenters"},
	}
	recorded := map[string][2]string{}
	for _, operation := range sources.Operations {
		recorded[operation.OperationID] = [2]string{operation.Method, operation.Path}
		if !strings.Contains(operation.Source, pinnedCommit+"/"+specPath) {
			t.Errorf("operation %q is not pinned to the specification revision: %q",
				operation.OperationID, operation.Source)
		}
	}
	if !reflect.DeepEqual(recorded, wantRoutes) {
		t.Fatalf("official_sources operations = %v, want %v", recorded, wantRoutes)
	}
	declared := append([]string(nil), sources.OperationIDs...)
	sort.Strings(declared)
	wantIDs := []string{"addVcenterDatasource", "create", "getVcenter", "listVcenters"}
	if !reflect.DeepEqual(declared, wantIDs) {
		t.Fatalf("official_sources operationIds = %v, want %v", declared, wantIDs)
	}

	var served int
	server := contractmock.New(t, protectedPath("docs", "contract.json"),
		func(contractmock.Request) contractmock.Response {
			served++
			return contractmock.Response{Status: http.StatusOK}
		})

	mockRoutes := map[string][2]string{}
	for _, route := range server.Routes() {
		mockRoutes[route.OperationID] = [2]string{route.Method, route.Path}
	}
	if !reflect.DeepEqual(mockRoutes, wantRoutes) {
		t.Fatalf("mock callable surface = %v, want %v", mockRoutes, wantRoutes)
	}
	if server.BasePath() != serviceRoot {
		t.Fatalf("mock service root = %q, want %q", server.BasePath(), serviceRoot)
	}
	parsed, err := url.Parse(server.URL())
	if err != nil || parsed.Hostname() != "127.0.0.1" {
		t.Fatalf("mock is not IPv4 loopback-only: %q (%v)", server.URL(), err)
	}

	for _, rejected := range []string{
		serviceRoot + "/data-sources/nsxt",             // absent from the contract
		"/data-sources/vcenters",                       // outside the service root
		serviceRoot + "/data-sources/vcenters/a/tiers", // deeper than any route
	} {
		response, err := http.Get(server.URL() + rejected)
		if err != nil {
			t.Fatalf("call %s: %v", rejected, err)
		}
		response.Body.Close()
		if response.StatusCode != http.StatusNotFound {
			t.Errorf("uncontracted route %s returned %d, want 404", rejected, response.StatusCode)
		}
	}
	if served != 0 {
		t.Errorf("mock responder served %d operations absent from the contract", served)
	}
	if got := len(server.Requests()); got != 3 {
		t.Errorf("request log captured %d rejected calls, want 3", got)
	}
}

// ---------------------------------------------------------------------------
// Retry safety
// ---------------------------------------------------------------------------

func TestEnsureIsSafeToRepeat(t *testing.T) {
	t.Parallel()

	ipSpec := vcfnetworks.VcenterSpec{
		Nickname: "vc-dc1", ProxyID: "18230:901:1585583463",
		IP: "10.197.17.68", Username: "administrator@vsphere.local", Password: "VMware1!",
	}
	fqdnSpec := vcfnetworks.VcenterSpec{
		Nickname: "vc-dc2", ProxyID: "18230:901:1585583463",
		FQDN: "vc2.corp.local", Username: "administrator@vsphere.local", Password: "VMware1!",
	}

	cases := []struct {
		name         string
		seed         []vcfnetworks.VcenterDataSource
		spec         vcfnetworks.VcenterSpec
		wantCreated  bool
		wantEntityID string
		wantFirstOps []string
	}{
		{
			name:         "absent target is created once",
			spec:         ipSpec,
			wantCreated:  true,
			wantEntityID: mintedID,
			wantFirstOps: []string{"create", "listVcenters", "addVcenterDatasource"},
		},
		{
			name:         "absent target among unrelated data sources",
			seed:         []vcfnetworks.VcenterDataSource{vcenterWithIP(existingIPID, "10.197.17.6", "other")},
			spec:         ipSpec,
			wantCreated:  true,
			wantEntityID: mintedID,
			wantFirstOps: []string{"create", "listVcenters", "getVcenter", "addVcenterDatasource"},
		},
		{
			name:         "pre-existing IP target is adopted without mutating",
			seed:         []vcfnetworks.VcenterDataSource{vcenterWithIP(existingIPID, "10.197.17.68", "vc-dc1")},
			spec:         ipSpec,
			wantCreated:  false,
			wantEntityID: existingIPID,
			wantFirstOps: []string{"create", "listVcenters", "getVcenter"},
		},
		{
			name:         "pre-existing FQDN target matches case-insensitively",
			seed:         []vcfnetworks.VcenterDataSource{vcenterWithFQDN(existingFQDNID, "VC2.Corp.Local", "vc-dc2")},
			spec:         fqdnSpec,
			wantCreated:  false,
			wantEntityID: existingFQDNID,
			wantFirstOps: []string{"create", "listVcenters", "getVcenter"},
		},
	}

	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()
			b := &backend{}
			b.seed(testCase.seed...)
			client, server := newHarness(t, b)

			first, err := client.EnsureVcenterDataSource(context.Background(), testCase.spec)
			if err != nil {
				t.Fatalf("first EnsureVcenterDataSource: %v", err)
			}
			if first.Created != testCase.wantCreated {
				t.Errorf("first call Created = %v, want %v", first.Created, testCase.wantCreated)
			}
			if first.DataSource.EntityID != testCase.wantEntityID {
				t.Errorf("first call entity_id = %q, want %q",
					first.DataSource.EntityID, testCase.wantEntityID)
			}
			wantSource := vcfnetworks.VcenterDataSource{
				EntityID: testCase.wantEntityID, EntityType: "VCenterDataSource",
				IP: testCase.spec.IP, FQDN: testCase.spec.FQDN,
				ProxyID: testCase.spec.ProxyID, Nickname: testCase.spec.Nickname, Enabled: true,
			}
			if !testCase.wantCreated {
				for _, seeded := range testCase.seed {
					if seeded.EntityID == testCase.wantEntityID {
						wantSource = seeded
						break
					}
				}
			}
			if !reflect.DeepEqual(first.DataSource, wantSource) {
				t.Errorf("first call data source = %+v, want %+v", first.DataSource, wantSource)
			}
			if got := operations(server); !reflect.DeepEqual(got, testCase.wantFirstOps) {
				t.Errorf("first call issued %v, want %v", got, testCase.wantFirstOps)
			}

			// The retry: the same call repeated must converge on the same data
			// source and must not mutate the service a second time.
			second, err := client.EnsureVcenterDataSource(context.Background(), testCase.spec)
			if err != nil {
				t.Fatalf("repeated EnsureVcenterDataSource: %v", err)
			}
			if second.Created {
				t.Error("repeated call reported Created = true; the effect was duplicated")
			}
			if !reflect.DeepEqual(second.DataSource, first.DataSource) {
				t.Errorf("repeated call resolved %+v, want %+v",
					second.DataSource, first.DataSource)
			}

			wantPosts := 0
			if testCase.wantCreated {
				wantPosts = 1
			}
			if got := countOperation(server, "addVcenterDatasource"); got != wantPosts {
				t.Errorf("two identical calls issued %d addVcenterDatasource requests, want %d",
					got, wantPosts)
			}
			if got := countOperation(server, "create"); got != 1 {
				t.Errorf("client authenticated %d times, want 1", got)
			}
			b.mu.Lock()
			total := len(b.sources)
			b.mu.Unlock()
			if want := len(testCase.seed) + wantPosts; total != want {
				t.Errorf("backend holds %d data sources after two calls, want %d", total, want)
			}
		})
	}
}

func TestExistingTargetResolutionRules(t *testing.T) {
	t.Parallel()

	spec := vcfnetworks.VcenterSpec{
		Nickname: "vc-dc1", ProxyID: "18230:901:1585583463",
		IP: "10.197.17.68", Username: "administrator@vsphere.local",
	}

	t.Run("IP comparison is exact, not a prefix", func(t *testing.T) {
		t.Parallel()
		b := &backend{}
		b.seed(vcenterWithIP(existingIPID, "10.197.17.6", "near-miss"))
		client, server := newHarness(t, b)

		result, err := client.EnsureVcenterDataSource(context.Background(), spec)
		if err != nil {
			t.Fatalf("EnsureVcenterDataSource: %v", err)
		}
		if !result.Created {
			t.Fatal("a near-miss address was adopted as an existing data source")
		}
		if got := countOperation(server, "addVcenterDatasource"); got != 1 {
			t.Errorf("issued %d create requests, want 1", got)
		}
	})

	t.Run("resolution stops at the first match", func(t *testing.T) {
		t.Parallel()
		b := &backend{}
		b.seed(
			vcenterWithIP(existingIPID, "10.197.17.68", "vc-dc1"),
			vcenterWithIP("18230:902:111111111", "10.197.17.99", "vc-dc9"),
		)
		client, server := newHarness(t, b)

		if _, err := client.EnsureVcenterDataSource(context.Background(), spec); err != nil {
			t.Fatalf("EnsureVcenterDataSource: %v", err)
		}
		want := []string{"create", "listVcenters", "getVcenter"}
		if got := operations(server); !reflect.DeepEqual(got, want) {
			t.Errorf("issued %v, want %v: resolution must stop at the first match", got, want)
		}
		if requests := requestsFor(server, "getVcenter"); len(requests) == 1 &&
			requests[0].PathParams["id"] != existingIPID {
			t.Errorf("getVcenter id path parameter = %q, want %q",
				requests[0].PathParams["id"], existingIPID)
		}
	})

	t.Run("non-vCenter and identifierless entries are not fetched", func(t *testing.T) {
		t.Parallel()
		b := &backend{}
		b.seed(
			vcfnetworks.VcenterDataSource{EntityID: "18230:903:222222222", EntityType: "NSXTManagerDataSource"},
			vcfnetworks.VcenterDataSource{EntityID: "", EntityType: "VCenterDataSource"},
			vcfnetworks.VcenterDataSource{EntityID: "   ", EntityType: "VCenterDataSource"},
			vcenterWithIP(existingIPID, "10.197.17.68", "vc-dc1"),
		)
		client, server := newHarness(t, b)

		result, err := client.EnsureVcenterDataSource(context.Background(), spec)
		if err != nil {
			t.Fatalf("EnsureVcenterDataSource: %v", err)
		}
		if result.Created || result.DataSource.EntityID != existingIPID {
			t.Fatalf("resolution = %+v, want the existing vCenter %q", result, existingIPID)
		}
		want := []string{"create", "listVcenters", "getVcenter"}
		if got := operations(server); !reflect.DeepEqual(got, want) {
			t.Errorf("issued %v, want %v: ineligible list entries must not be fetched", got, want)
		}
		for _, request := range requestsFor(server, "getVcenter") {
			if id := request.PathParams["id"]; id != existingIPID {
				t.Errorf("fetched ineligible list entry %q", id)
			}
		}
	})

	t.Run("an identifier that vanished between list and fetch is skipped", func(t *testing.T) {
		t.Parallel()
		b := &backend{}
		b.seed(vcenterWithIP("18230:902:999999999", "10.0.0.1", "ghost"))
		b.override = func(request contractmock.Request) (contractmock.Response, bool) {
			if request.OperationID == "getVcenter" {
				return contractmock.JSONResponse(t, http.StatusNotFound,
					map[string]any{"code": 404, "message": "not found"}), true
			}
			return contractmock.Response{}, false
		}
		client, server := newHarness(t, b)

		result, err := client.EnsureVcenterDataSource(context.Background(), spec)
		if err != nil {
			t.Fatalf("EnsureVcenterDataSource: %v", err)
		}
		if !result.Created {
			t.Error("a 404 during resolution must not block the create")
		}
		if got := countOperation(server, "addVcenterDatasource"); got != 1 {
			t.Errorf("issued %d create requests, want 1", got)
		}
	})
}

// ---------------------------------------------------------------------------
// Exact request wire shape
// ---------------------------------------------------------------------------

func TestCreateRequestWireShape(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name            string
		spec            vcfnetworks.VcenterSpec
		wantKeys        []string
		wantCredentials []string
		wantRaw         map[string]string
	}{
		{
			name: "IP target with credentials, no optional members",
			spec: vcfnetworks.VcenterSpec{
				Nickname: "vc-dc1", ProxyID: "18230:901:1585583463",
				IP: "10.197.17.68", Username: "administrator@vsphere.local", Password: "VMware1!",
			},
			wantKeys:        []string{"credentials", "ip", "nickname", "proxy_id"},
			wantCredentials: []string{"password", "username"},
			wantRaw: map[string]string{
				"ip": `"10.197.17.68"`, "nickname": `"vc-dc1"`,
				"proxy_id": `"18230:901:1585583463"`,
			},
		},
		{
			name: "FQDN target omits ip and an unset password",
			spec: vcfnetworks.VcenterSpec{
				Nickname: "vc-dc2", ProxyID: "18230:901:1585583463",
				FQDN: "vc2.corp.local", Username: "readonly",
			},
			wantKeys:        []string{"credentials", "fqdn", "nickname", "proxy_id"},
			wantCredentials: []string{"username"},
			wantRaw:         map[string]string{"fqdn": `"vc2.corp.local"`},
		},
		{
			name: "explicit false and empty string survive serialization",
			spec: vcfnetworks.VcenterSpec{
				Nickname: "vc-dc3", ProxyID: "18230:901:1585583463",
				IP: "10.197.17.70", Username: "readonly",
				Enabled: pointer(false), Notes: pointer(""),
			},
			wantKeys:        []string{"credentials", "enabled", "ip", "nickname", "notes", "proxy_id"},
			wantCredentials: []string{"username"},
			wantRaw:         map[string]string{"enabled": "false", "notes": `""`},
		},
		{
			name: "explicit true and populated notes are preserved",
			spec: vcfnetworks.VcenterSpec{
				Nickname: "vc-dc4", ProxyID: "18230:901:1585583463",
				IP: "10.197.17.71", Username: "readonly", Password: "VMware1!",
				Enabled: pointer(true), Notes: pointer("Located in DC1"),
			},
			wantKeys:        []string{"credentials", "enabled", "ip", "nickname", "notes", "proxy_id"},
			wantCredentials: []string{"password", "username"},
			wantRaw:         map[string]string{"enabled": "true", "notes": `"Located in DC1"`},
		},
	}

	// Members of VCenterDataSourceRequest that this contract projection never
	// populates, plus response-only members that must never be echoed back.
	neverSent := []string{
		"antrea_ipfix_request", "ds_sub_type", "enable_ds_associated_tags", "entity_id",
		"entity_type", "ipfix_request", "is_vmc", "tags", "certificate", "sha_thumbprint",
	}

	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()
			b := &backend{}
			client, server := newHarness(t, b)

			if _, err := client.EnsureVcenterDataSource(context.Background(), testCase.spec); err != nil {
				t.Fatalf("EnsureVcenterDataSource: %v", err)
			}
			posts := requestsFor(server, "addVcenterDatasource")
			if len(posts) != 1 {
				t.Fatalf("issued %d addVcenterDatasource requests, want 1", len(posts))
			}
			post := posts[0]

			if post.Method != http.MethodPost {
				t.Errorf("create used method %s, want POST", post.Method)
			}
			if want := serviceRoot + "/data-sources/vcenters"; post.RequestURI != want {
				t.Errorf("create target = %q, want %q", post.RequestURI, want)
			}
			wantNoQuery(t, post)
			wantHeader(t, post, "Content-Type", []string{"application/json"})
			wantHeader(t, post, "Accept", []string{"application/json"})
			wantHeader(t, post, "Authorization", []string{"NetworkInsight " + issuedToken})
			if post.ContentLength != int64(len(post.Body)) {
				t.Errorf("create declared Content-Length %d for a %d byte body",
					post.ContentLength, len(post.Body))
			}
			if len(post.TransferEncoding) != 0 {
				t.Errorf("create used transfer encoding %v", post.TransferEncoding)
			}

			if got := objectKeys(t, post.Body); !reflect.DeepEqual(got, testCase.wantKeys) {
				t.Errorf("create body members = %v, want exactly %v (body %s)",
					got, testCase.wantKeys, post.Body)
			}
			for name, want := range testCase.wantRaw {
				value, ok := member(t, post.Body, name)
				if !ok {
					t.Errorf("create body is missing %q", name)
					continue
				}
				if string(value) != want {
					t.Errorf("create body %q = %s, want %s", name, value, want)
				}
			}
			for name, want := range map[string]string{
				"nickname": testCase.spec.Nickname,
				"proxy_id": testCase.spec.ProxyID,
			} {
				value, ok := member(t, post.Body, name)
				encoded, _ := json.Marshal(want)
				if !ok || string(value) != string(encoded) {
					t.Errorf("create body %q = %s, want %s", name, value, encoded)
				}
			}
			targetName, targetValue := "ip", testCase.spec.IP
			if testCase.spec.FQDN != "" {
				targetName, targetValue = "fqdn", testCase.spec.FQDN
			}
			target, _ := member(t, post.Body, targetName)
			wantTarget, _ := json.Marshal(targetValue)
			if string(target) != string(wantTarget) {
				t.Errorf("create body %q = %s, want %s", targetName, target, wantTarget)
			}
			credentials, ok := member(t, post.Body, "credentials")
			if !ok {
				t.Fatalf("create body is missing credentials")
			}
			if got := objectKeys(t, credentials); !reflect.DeepEqual(got, testCase.wantCredentials) {
				t.Errorf("credentials members = %v, want exactly %v", got, testCase.wantCredentials)
			}
			username, _ := member(t, credentials, "username")
			wantUsername, _ := json.Marshal(testCase.spec.Username)
			if string(username) != string(wantUsername) {
				t.Errorf("credentials username = %s, want %s", username, wantUsername)
			}
			if testCase.spec.Password != "" {
				password, _ := member(t, credentials, "password")
				wantPassword, _ := json.Marshal(testCase.spec.Password)
				if string(password) != string(wantPassword) {
					t.Errorf("credentials password = %s, want %s", password, wantPassword)
				}
			}
			for _, forbidden := range neverSent {
				if _, present := member(t, post.Body, forbidden); present {
					t.Errorf("create body sent unrequested member %q", forbidden)
				}
			}
		})
	}
}

func TestAuthAndBodylessRequestWireShape(t *testing.T) {
	t.Parallel()

	b := &backend{}
	b.seed(vcenterWithIP(existingIPID, "10.197.17.68", "vc-dc1"))
	client, server := newHarness(t, b)

	spec := vcfnetworks.VcenterSpec{
		Nickname: "vc-dc1", ProxyID: "18230:901:1585583463",
		IP: "10.197.17.68", Username: "administrator@vsphere.local",
	}
	if _, err := client.EnsureVcenterDataSource(context.Background(), spec); err != nil {
		t.Fatalf("EnsureVcenterDataSource: %v", err)
	}

	logins := requestsFor(server, "create")
	if len(logins) != 1 {
		t.Fatalf("issued %d token requests, want 1", len(logins))
	}
	login := logins[0]
	if want := serviceRoot + "/auth/token"; login.RequestURI != want {
		t.Errorf("token target = %q, want %q", login.RequestURI, want)
	}
	wantNoQuery(t, login)
	wantHeader(t, login, "Content-Type", []string{"application/json"})
	wantHeader(t, login, "Accept", []string{"application/json"})
	wantHeader(t, login, "Authorization", nil)
	if login.ContentLength != int64(len(login.Body)) {
		t.Errorf("token request declared Content-Length %d for a %d byte body",
			login.ContentLength, len(login.Body))
	}
	if len(login.TransferEncoding) != 0 {
		t.Errorf("token request used transfer encoding %v", login.TransferEncoding)
	}
	if got := objectKeys(t, login.Body); !reflect.DeepEqual(got, []string{"password", "username"}) {
		t.Errorf("token body members = %v, want exactly [password username] (body %s)", got, login.Body)
	}
	for name, want := range map[string]string{
		"username": `"` + loginUser + `"`, "password": `"` + loginPassword + `"`,
	} {
		value, _ := member(t, login.Body, name)
		if string(value) != want {
			t.Errorf("token body %q = %s, want %s", name, value, want)
		}
	}

	list := requestsFor(server, "listVcenters")
	if len(list) != 1 {
		t.Fatalf("issued %d listVcenters requests, want 1", len(list))
	}
	if want := serviceRoot + "/data-sources/vcenters"; list[0].RequestURI != want {
		t.Errorf("list target = %q, want %q", list[0].RequestURI, want)
	}

	fetch := requestsFor(server, "getVcenter")
	if len(fetch) != 1 {
		t.Fatalf("issued %d getVcenter requests, want 1", len(fetch))
	}
	if want := serviceRoot + "/data-sources/vcenters/" + existingIPID; fetch[0].RequestURI != want {
		t.Errorf("fetch target = %q, want %q", fetch[0].RequestURI, want)
	}

	for _, request := range append(list, fetch...) {
		if request.Method != http.MethodGet {
			t.Errorf("%s used method %s, want GET", request.Path, request.Method)
		}
		wantNoQuery(t, request)
		wantBodyless(t, request)
		wantHeader(t, request, "Accept", []string{"application/json"})
		wantHeader(t, request, "Authorization", []string{"NetworkInsight " + issuedToken})
	}
}

// ---------------------------------------------------------------------------
// Rejection, failure and concurrency
// ---------------------------------------------------------------------------

func TestInvalidInputIsRejectedBeforeAnyRequest(t *testing.T) {
	t.Parallel()

	valid := vcfnetworks.VcenterSpec{
		Nickname: "vc-dc1", ProxyID: "18230:901:1585583463",
		IP: "10.197.17.68", Username: "administrator@vsphere.local",
	}
	mutate := func(change func(*vcfnetworks.VcenterSpec)) vcfnetworks.VcenterSpec {
		spec := valid
		change(&spec)
		return spec
	}

	cases := []struct {
		name string
		spec vcfnetworks.VcenterSpec
		ctx  context.Context
	}{
		{name: "nil context", spec: valid},
		{name: "blank nickname", spec: mutate(func(s *vcfnetworks.VcenterSpec) { s.Nickname = "" }), ctx: context.Background()},
		{name: "blank proxy id", spec: mutate(func(s *vcfnetworks.VcenterSpec) { s.ProxyID = "" }), ctx: context.Background()},
		{name: "blank username", spec: mutate(func(s *vcfnetworks.VcenterSpec) { s.Username = "" }), ctx: context.Background()},
		{name: "whitespace nickname", spec: mutate(func(s *vcfnetworks.VcenterSpec) { s.Nickname = "  " }), ctx: context.Background()},
		{name: "whitespace proxy id", spec: mutate(func(s *vcfnetworks.VcenterSpec) { s.ProxyID = "\t" }), ctx: context.Background()},
		{name: "whitespace username", spec: mutate(func(s *vcfnetworks.VcenterSpec) { s.Username = "\n" }), ctx: context.Background()},
		{name: "neither ip nor fqdn", spec: mutate(func(s *vcfnetworks.VcenterSpec) { s.IP = "" }), ctx: context.Background()},
		{name: "both ip and fqdn", spec: mutate(func(s *vcfnetworks.VcenterSpec) { s.FQDN = "vc1.corp.local" }), ctx: context.Background()},
	}

	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()
			b := &backend{}
			client, server := newHarness(t, b)

			result, err := client.EnsureVcenterDataSource(testCase.ctx, testCase.spec)
			if err == nil {
				t.Fatalf("accepted invalid input, returned %+v", result)
			}
			wantNoSecretLeak(t, err)
			if !reflect.DeepEqual(result, vcfnetworks.EnsureResult{}) {
				t.Errorf("rejected call returned %+v, want the zero result", result)
			}
			if got := len(server.Requests()); got != 0 {
				t.Errorf("rejected call issued %d requests, want 0", got)
			}
		})
	}

	t.Run("service root rejection", func(t *testing.T) {
		t.Parallel()
		for _, baseURL := range []string{
			"", "   ", "ftp://vrni.corp.local", "vrni.corp.local", "http://",
			"https://vrni.corp.local?token=x", "https://vrni.corp.local#frag",
			"https://user@vrni.corp.local", "https://vrni.corp.local/api/ni",
			"https://vrni.corp.local//", "https://vrni.corp.local#",
		} {
			if _, err := vcfnetworks.NewClient(baseURL, loginUser, loginPassword, nil); err == nil {
				t.Errorf("NewClient accepted service root %q", baseURL)
			}
		}
		if _, err := vcfnetworks.NewClient("https://vrni.corp.local", "", loginPassword, nil); err == nil {
			t.Error("NewClient accepted a blank username")
		}
		if _, err := vcfnetworks.NewClient("https://vrni.corp.local", " \t", loginPassword, nil); err == nil {
			t.Error("NewClient accepted a whitespace-only username")
		}
		if _, err := vcfnetworks.NewClient("https://vrni.corp.local/", loginUser, loginPassword, nil); err != nil {
			t.Errorf("NewClient rejected a valid service root: %v", err)
		}
	})
}

func TestFailuresNeverLeaveAPartialResult(t *testing.T) {
	t.Parallel()

	spec := vcfnetworks.VcenterSpec{
		Nickname: "vc-dc1", ProxyID: "18230:901:1585583463",
		IP: "10.197.17.68", Username: "administrator@vsphere.local", Password: "VMware1!",
	}
	apiFailure := func(status, code int, message string) contractmock.Response {
		body, _ := json.Marshal(map[string]any{"code": code, "message": message})
		return contractmock.Response{Status: status, ContentType: "application/json", Body: body}
	}

	cases := []struct {
		name       string
		override   func(t *testing.T) func(contractmock.Request) (contractmock.Response, bool)
		wantAPI    *vcfnetworks.APIError
		wantProto  bool
		wantPosts  int
		wantOpsMax map[string]int
	}{
		{
			name: "token request is unauthorized",
			override: func(*testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					if r.OperationID == "create" {
						return apiFailure(http.StatusUnauthorized, 401, "invalid credentials"), true
					}
					return contractmock.Response{}, false
				}
			},
			wantAPI:    &vcfnetworks.APIError{StatusCode: 401, Code: 401, Message: "invalid credentials"},
			wantOpsMax: map[string]int{"listVcenters": 0, "getVcenter": 0, "addVcenterDatasource": 0},
		},
		{
			name: "token response carries no token",
			override: func(t *testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					if r.OperationID == "create" {
						return contractmock.JSONResponse(t, http.StatusOK,
							map[string]any{"token": "", "expiry": 1}), true
					}
					return contractmock.Response{}, false
				}
			},
			wantProto:  true,
			wantOpsMax: map[string]int{"listVcenters": 0, "addVcenterDatasource": 0},
		},
		{
			name: "token response uses an undeclared success status",
			override: func(t *testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					if r.OperationID == "create" {
						return contractmock.JSONResponse(t, http.StatusCreated,
							map[string]any{"token": issuedToken, "expiry": 1}), true
					}
					return contractmock.Response{}, false
				}
			},
			wantProto:  true,
			wantOpsMax: map[string]int{"listVcenters": 0, "addVcenterDatasource": 0},
		},
		{
			name: "token response has a non-JSON media type",
			override: func(*testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					if r.OperationID == "create" {
						return contractmock.Response{
							Status: http.StatusOK, ContentType: "text/plain",
							Body: []byte(`{"token":"` + issuedToken + `","expiry":1}`),
						}, true
					}
					return contractmock.Response{}, false
				}
			},
			wantProto:  true,
			wantOpsMax: map[string]int{"listVcenters": 0, "addVcenterDatasource": 0},
		},
		{
			name: "list fails",
			override: func(*testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					if r.OperationID == "listVcenters" {
						return apiFailure(http.StatusInternalServerError, 500, "backend down"), true
					}
					return contractmock.Response{}, false
				}
			},
			wantAPI:    &vcfnetworks.APIError{StatusCode: 500, Code: 500, Message: "backend down"},
			wantOpsMax: map[string]int{"addVcenterDatasource": 0},
		},
		{
			name: "list body is not JSON",
			override: func(*testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					if r.OperationID == "listVcenters" {
						return contractmock.Response{
							Status: http.StatusOK, ContentType: "text/html", Body: []byte("<html/>"),
						}, true
					}
					return contractmock.Response{}, false
				}
			},
			wantProto:  true,
			wantOpsMax: map[string]int{"addVcenterDatasource": 0},
		},
		{
			name: "list JSON body is not an object",
			override: func(*testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					if r.OperationID == "listVcenters" {
						return contractmock.Response{
							Status: http.StatusOK, ContentType: "application/json", Body: []byte("null"),
						}, true
					}
					return contractmock.Response{}, false
				}
			},
			wantProto:  true,
			wantOpsMax: map[string]int{"getVcenter": 0, "addVcenterDatasource": 0},
		},
		{
			name: "list response uses an undeclared success status",
			override: func(t *testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					if r.OperationID == "listVcenters" {
						return contractmock.JSONResponse(t, http.StatusCreated, map[string]any{
							"results": []any{}, "total_count": 0,
						}), true
					}
					return contractmock.Response{}, false
				}
			},
			wantProto:  true,
			wantOpsMax: map[string]int{"getVcenter": 0, "addVcenterDatasource": 0},
		},
		{
			name: "fetch fails for a reason other than absence",
			override: func(*testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					switch r.OperationID {
					case "listVcenters":
						body, _ := json.Marshal(map[string]any{
							"results": []map[string]string{{
								"entity_id": existingIPID, "entity_type": "VCenterDataSource",
							}},
							"total_count": 1,
						})
						return contractmock.Response{
							Status: http.StatusOK, ContentType: "application/json", Body: body,
						}, true
					case "getVcenter":
						return apiFailure(http.StatusForbidden, 403, "no privilege"), true
					}
					return contractmock.Response{}, false
				}
			},
			wantAPI:    &vcfnetworks.APIError{StatusCode: 403, Code: 403, Message: "no privilege"},
			wantOpsMax: map[string]int{"addVcenterDatasource": 0},
		},
		{
			name: "fetch response uses an undeclared success status",
			override: func(t *testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					switch r.OperationID {
					case "listVcenters":
						return contractmock.JSONResponse(t, http.StatusOK, map[string]any{
							"results": []map[string]string{{
								"entity_id": existingIPID, "entity_type": "VCenterDataSource",
							}}, "total_count": 1,
						}), true
					case "getVcenter":
						return contractmock.JSONResponse(t, http.StatusCreated,
							vcenterWithIP(existingIPID, spec.IP, spec.Nickname)), true
					}
					return contractmock.Response{}, false
				}
			},
			wantProto:  true,
			wantOpsMax: map[string]int{"addVcenterDatasource": 0},
		},
		{
			name: "fetch response body does not decode",
			override: func(t *testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					switch r.OperationID {
					case "listVcenters":
						return contractmock.JSONResponse(t, http.StatusOK, map[string]any{
							"results": []map[string]string{{
								"entity_id": existingIPID, "entity_type": "VCenterDataSource",
							}}, "total_count": 1,
						}), true
					case "getVcenter":
						return contractmock.Response{
							Status: http.StatusOK, ContentType: "application/json", Body: []byte("{"),
						}, true
					}
					return contractmock.Response{}, false
				}
			},
			wantProto:  true,
			wantOpsMax: map[string]int{"addVcenterDatasource": 0},
		},
		{
			name: "create is rejected",
			override: func(*testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					if r.OperationID == "addVcenterDatasource" {
						return apiFailure(http.StatusBadRequest, 400, "proxy unreachable"), true
					}
					return contractmock.Response{}, false
				}
			},
			wantAPI:   &vcfnetworks.APIError{StatusCode: 400, Code: 400, Message: "proxy unreachable"},
			wantPosts: 1,
		},
		{
			name: "create answers with a status the contract does not declare",
			override: func(t *testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					if r.OperationID == "addVcenterDatasource" {
						return contractmock.JSONResponse(t, http.StatusOK, map[string]any{
							"entity_id": mintedID, "entity_type": "VCenterDataSource",
						}), true
					}
					return contractmock.Response{}, false
				}
			},
			wantProto: true,
			wantPosts: 1,
		},
		{
			name: "create answers without an identifier",
			override: func(t *testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					if r.OperationID == "addVcenterDatasource" {
						return contractmock.JSONResponse(t, http.StatusCreated, map[string]any{
							"entity_id": "", "entity_type": "VCenterDataSource",
						}), true
					}
					return contractmock.Response{}, false
				}
			},
			wantProto: true,
			wantPosts: 1,
		},
		{
			name: "create answers with a blank identifier",
			override: func(t *testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					if r.OperationID == "addVcenterDatasource" {
						return contractmock.JSONResponse(t, http.StatusCreated, map[string]any{
							"entity_id": "   ", "entity_type": "VCenterDataSource",
						}), true
					}
					return contractmock.Response{}, false
				}
			},
			wantProto: true,
			wantPosts: 1,
		},
		{
			name: "create success has a non-JSON media type",
			override: func(*testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					if r.OperationID == "addVcenterDatasource" {
						return contractmock.Response{
							Status: http.StatusCreated, ContentType: "text/plain",
							Body: []byte(`{"entity_id":"` + mintedID + `"}`),
						}, true
					}
					return contractmock.Response{}, false
				}
			},
			wantProto: true,
			wantPosts: 1,
		},
		{
			name: "error body is not JSON",
			override: func(*testing.T) func(contractmock.Request) (contractmock.Response, bool) {
				return func(r contractmock.Request) (contractmock.Response, bool) {
					if r.OperationID == "listVcenters" {
						return contractmock.Response{
							Status: http.StatusBadGateway, ContentType: "text/plain", Body: []byte("gateway"),
						}, true
					}
					return contractmock.Response{}, false
				}
			},
			wantAPI:    &vcfnetworks.APIError{StatusCode: 502},
			wantOpsMax: map[string]int{"addVcenterDatasource": 0},
		},
	}

	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			t.Parallel()
			b := &backend{override: testCase.override(t)}
			client, server := newHarness(t, b)

			result, err := client.EnsureVcenterDataSource(context.Background(), spec)
			if err == nil {
				t.Fatalf("failure produced no error, returned %+v", result)
			}
			wantNoSecretLeak(t, err)
			if !reflect.DeepEqual(result, vcfnetworks.EnsureResult{}) {
				t.Errorf("failed call returned %+v, want the zero result", result)
			}
			if testCase.wantAPI != nil {
				var apiError *vcfnetworks.APIError
				if !errors.As(err, &apiError) {
					t.Fatalf("error %v (%T) is not *APIError", err, err)
				}
				if *apiError != *testCase.wantAPI {
					t.Errorf("APIError = %+v, want %+v", *apiError, *testCase.wantAPI)
				}
			}
			if testCase.wantProto {
				var protocolError *vcfnetworks.ProtocolError
				if !errors.As(err, &protocolError) {
					t.Fatalf("error %v (%T) is not *ProtocolError", err, err)
				}
				if protocolError.Reason == "" {
					t.Error("ProtocolError carries no reason")
				}
			}
			if got := countOperation(server, "addVcenterDatasource"); got != testCase.wantPosts {
				t.Errorf("issued %d addVcenterDatasource requests, want %d", got, testCase.wantPosts)
			}
			for operationID, limit := range testCase.wantOpsMax {
				if got := countOperation(server, operationID); got > limit {
					t.Errorf("issued %d %s requests, want at most %d", got, operationID, limit)
				}
			}
		})
	}
}

func TestConcurrentEnsureAuthenticatesOnce(t *testing.T) {
	t.Parallel()

	b := &backend{}
	b.seed(vcenterWithIP(existingIPID, "10.197.17.68", "vc-dc1"))
	client, server := newHarness(t, b)

	spec := vcfnetworks.VcenterSpec{
		Nickname: "vc-dc1", ProxyID: "18230:901:1585583463",
		IP: "10.197.17.68", Username: "administrator@vsphere.local",
	}

	const callers = 8
	results := make([]vcfnetworks.EnsureResult, callers)
	errs := make([]error, callers)
	var start sync.WaitGroup
	var done sync.WaitGroup
	start.Add(1)
	for i := 0; i < callers; i++ {
		done.Add(1)
		go func(index int) {
			defer done.Done()
			start.Wait()
			results[index], errs[index] = client.EnsureVcenterDataSource(context.Background(), spec)
		}(i)
	}
	start.Done()
	done.Wait()

	for i, err := range errs {
		if err != nil {
			t.Fatalf("caller %d: %v", i, err)
		}
		if results[i].Created || results[i].DataSource.EntityID != existingIPID {
			t.Errorf("caller %d resolved %+v, want the existing data source %q",
				i, results[i], existingIPID)
		}
	}
	if got := countOperation(server, "create"); got != 1 {
		t.Errorf("%d concurrent callers authenticated %d times, want 1", callers, got)
	}
	if got := countOperation(server, "addVcenterDatasource"); got != 0 {
		t.Errorf("concurrent callers issued %d create requests against an existing target", got)
	}
}

func TestTransportErrorsCannotLeakSecrets(t *testing.T) {
	t.Parallel()

	b := &backend{}
	server := contractmock.New(t, protectedPath("docs", "contract.json"), b.responder(t))
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Path == serviceRoot+"/auth/token" {
			return http.DefaultTransport.RoundTrip(request)
		}
		return nil, fmt.Errorf("backend exposed %s and %s", loginPassword, issuedToken)
	})
	client, err := vcfnetworks.NewClient(server.URL(), loginUser, loginPassword,
		&http.Client{Transport: transport})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	spec := vcfnetworks.VcenterSpec{
		Nickname: "vc-dc1", ProxyID: "18230:901:1585583463",
		IP: "10.197.17.68", Username: "administrator@vsphere.local",
	}

	result, err := client.EnsureVcenterDataSource(context.Background(), spec)
	if err == nil {
		t.Fatalf("transport failure returned no error and result %+v", result)
	}
	wantNoSecretLeak(t, err)
	if !reflect.DeepEqual(result, vcfnetworks.EnsureResult{}) {
		t.Errorf("transport failure returned %+v, want the zero result", result)
	}
	if got := operations(server); !reflect.DeepEqual(got, []string{"create"}) {
		t.Errorf("server received %v, want only the token request", got)
	}
}
