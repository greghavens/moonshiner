package networkpoolensure_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	npe "vcf91-0036"
	"vcf91-0036/internal/contractmock"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedSpec   = "specifications/sddc-manager/sddc-manager-openapi.json"
	contractSHA256 = "d78da0565db23a765e3f4088adc180538fb8772f95e804dd03f98c2a5c46234a"
	sourcesSHA256  = "d44f06889979af8059a12f889ff77562cfd80326fac1fac6707830ab710ade88"
)

type operationSource struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
	JSONPointer string `json:"json_pointer"`
}

func TestProtectedContractProvenance(t *testing.T) {
	assertFileHash(t, "docs/contract.json", contractSHA256)
	assertFileHash(t, "docs/official_sources.json", sourcesSHA256)

	var contract struct {
		DerivedFrom struct {
			Commit   string `json:"repository_commit_sha"`
			SpecPath string `json:"spec_path"`
			OpenAPI  string `json:"openapi"`
			Version  string `json:"info_version"`
			License  string `json:"repository_license"`
		} `json:"derived_from"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
			RequestBody *struct {
				Required  bool   `json:"required"`
				MediaType string `json:"media_type"`
				SchemaRef string `json:"schema_ref"`
			} `json:"request_body"`
			Responses map[string]struct {
				MediaType string `json:"media_type"`
				SchemaRef string `json:"schema_ref"`
			} `json:"responses"`
		} `json:"operations"`
		Schemas map[string]json.RawMessage `json:"schemas"`
	}
	readJSON(t, "docs/contract.json", &contract)

	var sources struct {
		Repository struct {
			Commit  string `json:"commit_sha"`
			License string `json:"license"`
		} `json:"repository"`
		Specification struct {
			Path    string `json:"path"`
			OpenAPI string `json:"openapi_version"`
			Version string `json:"info_version"`
		} `json:"specification"`
		Operations []operationSource `json:"operations"`
		Derivation string            `json:"derivation"`
	}
	readJSON(t, "docs/official_sources.json", &sources)

	if contract.DerivedFrom.Commit != expectedCommit ||
		sources.Repository.Commit != expectedCommit ||
		contract.DerivedFrom.SpecPath != expectedSpec ||
		sources.Specification.Path != expectedSpec {
		t.Fatalf(
			"wrong pinned specification: contract=%+v sources=%+v",
			contract.DerivedFrom,
			sources,
		)
	}
	if contract.DerivedFrom.OpenAPI != "3.0.1" ||
		sources.Specification.OpenAPI != "3.0.1" ||
		contract.DerivedFrom.Version != "9.1.0.0" ||
		sources.Specification.Version != "9.1.0.0" ||
		contract.DerivedFrom.License != "Apache-2.0" ||
		sources.Repository.License != "Apache-2.0" {
		t.Fatalf("wrong OpenAPI product metadata or license")
	}
	if !strings.Contains(sources.Derivation, "OpenAPI specification") ||
		!strings.Contains(sources.Derivation, "No rendered documentation page") {
		t.Fatalf("derivation is not explicit: %q", sources.Derivation)
	}

	if len(contract.Operations) != 2 {
		t.Fatalf("contract operations = %d, want 2", len(contract.Operations))
	}
	wantOperations := map[string][2]string{
		"getNetworkPool":    {http.MethodGet, "/v1/network-pools"},
		"createNetworkPool": {http.MethodPost, "/v1/network-pools"},
	}
	for _, operation := range contract.Operations {
		want, ok := wantOperations[operation.OperationID]
		if !ok ||
			operation.Method != want[0] ||
			operation.Path != want[1] {
			t.Fatalf("unexpected operation projection: %+v", operation)
		}
		switch operation.OperationID {
		case "getNetworkPool":
			if operation.RequestBody != nil ||
				operation.Responses["200"].MediaType != "application/json" ||
				operation.Responses["200"].SchemaRef !=
					"#/components/schemas/PageOfNetworkPool" ||
				operation.Responses["404"].SchemaRef !=
					"#/components/schemas/Error" ||
				operation.Responses["500"].SchemaRef !=
					"#/components/schemas/Error" {
				t.Fatalf("getNetworkPool projection mismatch: %+v", operation)
			}
		case "createNetworkPool":
			if operation.RequestBody == nil ||
				!operation.RequestBody.Required ||
				operation.RequestBody.MediaType != "application/json" ||
				operation.RequestBody.SchemaRef !=
					"#/components/schemas/NetworkPool" ||
				operation.Responses["201"].MediaType != "application/json" ||
				operation.Responses["201"].SchemaRef !=
					"#/components/schemas/NetworkPool" ||
				operation.Responses["400"].SchemaRef !=
					"#/components/schemas/Error" ||
				operation.Responses["500"].SchemaRef !=
					"#/components/schemas/Error" {
				t.Fatalf("createNetworkPool projection mismatch: %+v", operation)
			}
		}
		delete(wantOperations, operation.OperationID)
	}
	if len(wantOperations) != 0 {
		t.Fatalf("missing contract operations: %#v", wantOperations)
	}

	wantSources := []operationSource{
		{
			OperationID: "getNetworkPool",
			Method:      http.MethodGet,
			Path:        "/v1/network-pools",
			JSONPointer: "/paths/~1v1~1network-pools/get",
		},
		{
			OperationID: "createNetworkPool",
			Method:      http.MethodPost,
			Path:        "/v1/network-pools",
			JSONPointer: "/paths/~1v1~1network-pools/post",
		},
	}
	if !reflect.DeepEqual(sources.Operations, wantSources) {
		t.Fatalf(
			"official source operations = %#v, want %#v",
			sources.Operations,
			wantSources,
		)
	}
	assertSchemaProjection(t, contract.Schemas)
}

func TestListNetworkPoolsSortsEveryAlternatingResponse(t *testing.T) {
	server := newServer(t, contractmock.ModeOK)
	runtime := server.Runtime()
	client := newClient(t, server, runtime.AccessToken)

	first, err := client.ListNetworkPools(context.Background())
	if err != nil {
		t.Fatalf("first ListNetworkPools: %T %v", err, err)
	}
	second, err := client.ListNetworkPools(context.Background())
	if err != nil {
		t.Fatalf("second ListNetworkPools: %T %v", err, err)
	}

	want := publicPools(runtime.Pools)
	sort.Slice(want, func(left, right int) bool {
		if want[left].Name != want[right].Name {
			return want[left].Name < want[right].Name
		}
		return want[left].ID < want[right].ID
	})
	if !reflect.DeepEqual(first, want) || !reflect.DeepEqual(second, want) {
		t.Fatalf(
			"alternating collection was not normalized\nfirst: %#v\nsecond: %#v\nwant: %#v",
			first,
			second,
			want,
		)
	}
	firstJSON, _ := json.Marshal(first)
	secondJSON, _ := json.Marshal(second)
	if string(firstJSON) != string(secondJSON) {
		t.Fatalf("stable calls differ:\n%s\n%s", firstJSON, secondJSON)
	}

	requests := server.Requests()
	if len(requests) != 2 {
		t.Fatalf("request count = %d, want 2", len(requests))
	}
	for index, request := range requests {
		assertListWire(t, request, runtime.AccessToken)
		wantReversed := index%2 == 0
		if request.Reversed != wantReversed {
			t.Errorf(
				"fixture reversal %d = %v, want %v",
				index,
				request.Reversed,
				wantReversed,
			)
		}
	}
}

func TestListNetworkPoolsReturnsNonNilEmptySlice(t *testing.T) {
	server := newServer(t, contractmock.ModeEmpty)
	client := newClient(t, server, server.Runtime().AccessToken)
	pools, err := client.ListNetworkPools(context.Background())
	if err != nil {
		t.Fatalf("ListNetworkPools: %v", err)
	}
	if pools == nil || len(pools) != 0 {
		t.Fatalf("empty collection = %#v, want non-nil empty", pools)
	}
}

func TestEnsureNetworkPoolCreatesOnceThenAdopts(t *testing.T) {
	server := newServer(t, contractmock.ModeOK)
	runtime := server.Runtime()
	client := newClient(t, server, runtime.AccessToken)
	spec := targetSpec(runtime.TargetName)

	first, err := client.EnsureNetworkPool(context.Background(), spec)
	if err != nil {
		t.Fatalf("first EnsureNetworkPool: %T %v", err, err)
	}
	second, err := client.EnsureNetworkPool(context.Background(), spec)
	if err != nil {
		t.Fatalf("second EnsureNetworkPool: %T %v", err, err)
	}
	if !first.Created || second.Created ||
		first.Pool.ID == "" ||
		second.Pool.ID != first.Pool.ID ||
		first.Pool.Name != spec.Name {
		t.Fatalf("unexpected ensure results: first=%+v second=%+v", first, second)
	}
	if got := server.EffectCount(); got != 1 {
		t.Fatalf("mutation effects = %d, want 1", got)
	}

	requests := server.Requests()
	if len(requests) != 3 {
		t.Fatalf("request count = %d, want GET POST GET", len(requests))
	}
	assertListWire(t, requests[0], runtime.AccessToken)
	assertCreateWire(t, requests[1], runtime.AccessToken, spec)
	assertListWire(t, requests[2], runtime.AccessToken)
}

func TestRetryAfterCommittedLostResponseDoesNotDuplicateEffect(t *testing.T) {
	server := newServer(t, contractmock.ModeCommitThenDrop)
	runtime := server.Runtime()
	client := newClient(t, server, runtime.AccessToken)
	spec := targetSpec(runtime.TargetName)

	_, err := client.EnsureNetworkPool(context.Background(), spec)
	var transportError *npe.TransportError
	if !errors.As(err, &transportError) ||
		transportError.OperationID != "createNetworkPool" {
		t.Fatalf("first error = %T %v, want create TransportError", err, err)
	}
	result, err := client.EnsureNetworkPool(context.Background(), spec)
	if err != nil {
		t.Fatalf("retry EnsureNetworkPool: %T %v", err, err)
	}
	if result.Created || result.Pool.ID == "" {
		t.Fatalf("retry result = %+v, want adopted committed pool", result)
	}
	if got := server.EffectCount(); got != 1 {
		t.Fatalf("retry mutation effects = %d, want 1", got)
	}
	requests := server.Requests()
	if len(requests) != 3 {
		t.Fatalf("retry request count = %d, want GET POST GET", len(requests))
	}
	postCount := 0
	for _, request := range requests {
		if request.Method == http.MethodPost {
			postCount++
		}
	}
	if postCount != 1 {
		t.Fatalf("POST count = %d, want 1", postCount)
	}
}

func TestConcurrentEnsuresSerializeWithoutDuplicateEffects(t *testing.T) {
	server := newServer(t, contractmock.ModeOK)
	runtime := server.Runtime()
	client := newClient(t, server, runtime.AccessToken)
	spec := targetSpec(runtime.TargetName)

	const workers = 8
	var wait sync.WaitGroup
	var created atomic.Int32
	errorsSeen := make(chan error, workers)
	for index := 0; index < workers; index++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			result, err := client.EnsureNetworkPool(
				context.Background(),
				spec,
			)
			if err != nil {
				errorsSeen <- err
				return
			}
			if result.Created {
				created.Add(1)
			}
		}()
	}
	wait.Wait()
	close(errorsSeen)
	for err := range errorsSeen {
		t.Errorf("concurrent ensure: %T %v", err, err)
	}
	if got := created.Load(); got != 1 {
		t.Fatalf("Created result count = %d, want 1", got)
	}
	if got := server.EffectCount(); got != 1 {
		t.Fatalf("concurrent mutation effects = %d, want 1", got)
	}
	if got := len(server.Requests()); got != workers+1 {
		t.Fatalf("request count = %d, want %d GETs plus one POST", got, workers)
	}
}

func TestExistingDriftAndAmbiguityAreTableDriven(t *testing.T) {
	cases := []struct {
		name          string
		mode          contractmock.Mode
		wantCreated   bool
		wantDrift     bool
		wantAmbiguous bool
	}{
		{name: "equal existing pool", mode: contractmock.ModeExisting},
		{name: "configuration drift", mode: contractmock.ModeDrift, wantDrift: true},
		{name: "ambiguous name", mode: contractmock.ModeAmbiguous, wantAmbiguous: true},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			server := newServer(t, testCase.mode)
			runtime := server.Runtime()
			client := newClient(t, server, runtime.AccessToken)
			result, err := client.EnsureNetworkPool(
				context.Background(),
				targetSpec(runtime.TargetName),
			)
			var drift *npe.DriftError
			var ambiguous *npe.AmbiguousMatchError
			switch {
			case testCase.wantDrift:
				if !errors.As(err, &drift) ||
					drift.Existing.Name != runtime.TargetName {
					t.Fatalf("error = %T %v, want DriftError", err, err)
				}
			case testCase.wantAmbiguous:
				if !errors.As(err, &ambiguous) ||
					ambiguous.Name != runtime.TargetName ||
					ambiguous.Count != 2 {
					t.Fatalf("error = %T %v, want AmbiguousMatchError", err, err)
				}
			default:
				if err != nil || result.Created || result.Pool.ID == "" {
					t.Fatalf("equal existing result = %+v, err=%v", result, err)
				}
			}
			if got := server.EffectCount(); got != 0 {
				t.Fatalf("non-create case effects = %d, want 0", got)
			}
			if got := len(server.Requests()); got != 1 {
				t.Fatalf("non-create request count = %d, want one GET", got)
			}
		})
	}
}

func TestNewClientValidationIsLocalAndTableDriven(t *testing.T) {
	server := newServer(t, contractmock.ModeOK)
	validURL := server.URL()
	cases := []struct {
		name   string
		config npe.Config
		wantOK bool
	}{
		{
			name: "valid",
			config: npe.Config{
				BaseURL:     validURL,
				AccessToken: "token",
				HTTPClient:  server.Client(),
			},
			wantOK: true,
		},
		{
			name: "valid root slash",
			config: npe.Config{
				BaseURL:     validURL + "/",
				AccessToken: "token",
			},
			wantOK: true,
		},
		{name: "blank URL", config: npe.Config{AccessToken: "token"}},
		{name: "non HTTP scheme", config: npe.Config{
			BaseURL:     "ftp://127.0.0.1",
			AccessToken: "token",
		}},
		{name: "embedded credentials", config: npe.Config{
			BaseURL:     "http://user@127.0.0.1",
			AccessToken: "token",
		}},
		{name: "non-root path", config: npe.Config{
			BaseURL:     validURL + "/sddc",
			AccessToken: "token",
		}},
		{name: "query", config: npe.Config{
			BaseURL:     validURL + "?x=1",
			AccessToken: "token",
		}},
		{name: "dangling query", config: npe.Config{
			BaseURL:     validURL + "?",
			AccessToken: "token",
		}},
		{name: "fragment", config: npe.Config{
			BaseURL:     validURL + "#fragment",
			AccessToken: "token",
		}},
		{name: "blank token", config: npe.Config{BaseURL: validURL}},
		{name: "whitespace token", config: npe.Config{
			BaseURL:     validURL,
			AccessToken: "tok en",
		}},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			client, err := npe.NewClient(testCase.config)
			if testCase.wantOK {
				if err != nil || client == nil {
					t.Fatalf("NewClient = (%v, %v), want client", client, err)
				}
				return
			}
			if err == nil || client != nil {
				t.Fatalf("NewClient = (%v, %v), want local error", client, err)
			}
		})
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("NewClient sent %d requests", got)
	}
}

func TestEnsureValidationIsLocalAndTableDriven(t *testing.T) {
	server := newServer(t, contractmock.ModeOK)
	client := newClient(t, server, server.Runtime().AccessToken)
	valid := targetSpec(server.Runtime().TargetName)
	blank := ""
	spaced := " IPv4"
	badVersion := "IPv5"
	badMode := "DYNAMIC"
	cases := []struct {
		name string
		spec npe.NetworkPoolSpec
	}{
		{name: "blank name", spec: replaceName(valid, "")},
		{name: "spaced name", spec: replaceName(valid, " pool")},
		{name: "nil networks", spec: npe.NetworkPoolSpec{Name: "pool"}},
		{name: "blank type", spec: replaceNetwork(valid, func(n *npe.NetworkSpec) {
			n.Type = ""
		})},
		{name: "spaced type", spec: replaceNetwork(valid, func(n *npe.NetworkSpec) {
			n.Type = " VMOTION"
		})},
		{name: "VLAN outside int32", spec: replaceNetwork(valid, func(n *npe.NetworkSpec) {
			n.VLANID = int(math.MaxInt32) + 1
		})},
		{name: "MTU outside int32", spec: replaceNetwork(valid, func(n *npe.NetworkSpec) {
			n.MTU = int(math.MinInt32) - 1
		})},
		{name: "empty optional", spec: replaceNetwork(valid, func(n *npe.NetworkSpec) {
			n.Subnet = &blank
		})},
		{name: "spaced optional", spec: replaceNetwork(valid, func(n *npe.NetworkSpec) {
			n.IPAddressVersion = &spaced
		})},
		{name: "unsupported address version", spec: replaceNetwork(valid, func(n *npe.NetworkSpec) {
			n.IPAddressVersion = &badVersion
		})},
		{name: "unsupported assignment mode", spec: replaceNetwork(valid, func(n *npe.NetworkSpec) {
			n.IPAddressAssignmentMode = &badMode
		})},
		{name: "blank IP start", spec: replaceNetwork(valid, func(n *npe.NetworkSpec) {
			n.IPPools[0].Start = " "
		})},
		{name: "spaced IP end", spec: replaceNetwork(valid, func(n *npe.NetworkSpec) {
			n.IPPools[0].End += " "
		})},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			if _, err := client.EnsureNetworkPool(
				context.Background(),
				testCase.spec,
			); err == nil {
				t.Fatal("EnsureNetworkPool returned nil error")
			}
		})
	}

	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := client.EnsureNetworkPool(cancelled, valid); !errors.Is(
		err,
		context.Canceled,
	) {
		t.Fatalf("cancelled ensure error = %v, want context.Canceled", err)
	}
	if _, err := client.EnsureNetworkPool(nil, valid); err == nil {
		t.Fatal("nil context ensure returned nil error")
	}
	var nilClient *npe.Client
	if _, err := nilClient.EnsureNetworkPool(
		context.Background(),
		valid,
	); err == nil {
		t.Fatal("nil client ensure returned nil error")
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("invalid ensure inputs sent %d requests", got)
	}
}

func TestListRejectsInvalidContextLocally(t *testing.T) {
	server := newServer(t, contractmock.ModeOK)
	client := newClient(t, server, server.Runtime().AccessToken)
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	cases := []struct {
		name          string
		client        *npe.Client
		ctx           context.Context
		wantCancelled bool
	}{
		{name: "nil context", client: client},
		{
			name:          "cancelled context",
			client:        client,
			ctx:           cancelled,
			wantCancelled: true,
		},
		{name: "nil client", ctx: context.Background()},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			_, err := testCase.client.ListNetworkPools(testCase.ctx)
			if err == nil {
				t.Fatal("ListNetworkPools returned nil error")
			}
			if testCase.wantCancelled &&
				!errors.Is(err, context.Canceled) {
				t.Fatalf("error = %v, want context.Canceled", err)
			}
		})
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("invalid list contexts sent %d requests", got)
	}
}

func TestProtocolFailuresAreTableDriven(t *testing.T) {
	cases := []struct {
		name         string
		mode         contractmock.Mode
		ensure       bool
		wantOp       string
		wantRequests int
	}{
		{name: "malformed list", mode: contractmock.ModeMalformedList, wantOp: "getNetworkPool", wantRequests: 1},
		{name: "wrong list media", mode: contractmock.ModeWrongListMedia, wantOp: "getNetworkPool", wantRequests: 1},
		{name: "trailing list JSON", mode: contractmock.ModeTrailingList, wantOp: "getNetworkPool", wantRequests: 1},
		{name: "oversized list", mode: contractmock.ModeOversizedList, wantOp: "getNetworkPool", wantRequests: 1},
		{name: "bad metadata", mode: contractmock.ModeBadMetadata, wantOp: "getNetworkPool", wantRequests: 1},
		{name: "malformed create", mode: contractmock.ModeMalformedCreate, ensure: true, wantOp: "createNetworkPool", wantRequests: 2},
		{name: "wrong create media", mode: contractmock.ModeWrongCreateMedia, ensure: true, wantOp: "createNetworkPool", wantRequests: 2},
		{name: "trailing create JSON", mode: contractmock.ModeTrailingCreate, ensure: true, wantOp: "createNetworkPool", wantRequests: 2},
		{name: "oversized create", mode: contractmock.ModeOversizedCreate, ensure: true, wantOp: "createNetworkPool", wantRequests: 2},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			server := newServer(t, testCase.mode)
			client := newClient(t, server, server.Runtime().AccessToken)
			var err error
			if testCase.ensure {
				_, err = client.EnsureNetworkPool(
					context.Background(),
					targetSpec(server.Runtime().TargetName),
				)
			} else {
				_, err = client.ListNetworkPools(context.Background())
			}
			var protocolError *npe.ProtocolError
			if !errors.As(err, &protocolError) ||
				protocolError.OperationID != testCase.wantOp {
				t.Fatalf("error = %T %v, want %s ProtocolError", err, err, testCase.wantOp)
			}
			if got := len(server.Requests()); got != testCase.wantRequests {
				t.Fatalf("request count = %d, want %d", got, testCase.wantRequests)
			}
		})
	}
}

func TestStructuredAPIErrorsAreTableDrivenAndRedacted(t *testing.T) {
	cases := []struct {
		name         string
		mode         contractmock.Mode
		ensure       bool
		wantOp       string
		wantRequests int
	}{
		{name: "list", mode: contractmock.ModeListAPIError, wantOp: "getNetworkPool", wantRequests: 1},
		{name: "create", mode: contractmock.ModeCreateAPIError, ensure: true, wantOp: "createNetworkPool", wantRequests: 2},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			server := newServer(t, testCase.mode)
			runtime := server.Runtime()
			client := newClient(t, server, runtime.AccessToken)
			var err error
			if testCase.ensure {
				_, err = client.EnsureNetworkPool(
					context.Background(),
					targetSpec(runtime.TargetName),
				)
			} else {
				_, err = client.ListNetworkPools(context.Background())
			}
			var apiError *npe.APIError
			if !errors.As(err, &apiError) {
				t.Fatalf("error = %T %v, want APIError", err, err)
			}
			if apiError.OperationID != testCase.wantOp ||
				apiError.Status != http.StatusInternalServerError ||
				apiError.ErrorCode != runtime.ErrorCode ||
				apiError.Message != runtime.ErrorMessage ||
				apiError.RemediationMessage != runtime.Remediation ||
				apiError.ReferenceToken != runtime.ReferenceToken {
				t.Fatalf("APIError did not preserve fields: %+v", apiError)
			}
			assertRedacted(
				t,
				err.Error(),
				runtime.AccessToken,
				runtime.ErrorCode,
				runtime.ErrorMessage,
				runtime.Remediation,
				runtime.ReferenceToken,
			)
			if got := len(server.Requests()); got != testCase.wantRequests {
				t.Fatalf("request count = %d, want %d", got, testCase.wantRequests)
			}
		})
	}
}

func TestTransportErrorPreservesCauseAndRedactsText(t *testing.T) {
	cause := errors.New("transport-detail-that-must-not-leak")
	token := "transport-token-that-must-not-leak"
	client, err := npe.NewClient(npe.Config{
		BaseURL:     "http://127.0.0.1:1",
		AccessToken: token,
		HTTPClient: &http.Client{
			Transport: roundTripperFunc(func(*http.Request) (*http.Response, error) {
				return nil, cause
			}),
		},
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	_, err = client.ListNetworkPools(context.Background())
	var transportError *npe.TransportError
	if !errors.As(err, &transportError) ||
		transportError.OperationID != "getNetworkPool" ||
		!errors.Is(err, cause) {
		t.Fatalf("transport error = %#v (%v)", transportError, err)
	}
	assertRedacted(t, err.Error(), token, cause.Error())
}

func TestRedirectsAreNotFollowed(t *testing.T) {
	server := newServer(t, contractmock.ModeRedirect)
	var redirectCalls atomic.Int32
	baseClient := server.Client()
	baseClient.CheckRedirect = func(
		_ *http.Request,
		_ []*http.Request,
	) error {
		redirectCalls.Add(1)
		return nil
	}
	client, err := npe.NewClient(npe.Config{
		BaseURL:     server.URL(),
		AccessToken: server.Runtime().AccessToken,
		HTTPClient:  baseClient,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	_, err = client.ListNetworkPools(context.Background())
	var apiError *npe.APIError
	if !errors.As(err, &apiError) ||
		apiError.Status != http.StatusFound {
		t.Fatalf("redirect error = %T %v, want HTTP 302 APIError", err, err)
	}
	if got := redirectCalls.Load(); got != 0 {
		t.Fatalf("caller's redirect hook invoked %d times", got)
	}
	if got := len(server.Requests()); got != 1 {
		t.Fatalf("redirect request count = %d, want 1", got)
	}
}

func assertSchemaProjection(
	t *testing.T,
	schemas map[string]json.RawMessage,
) {
	t.Helper()
	for _, name := range []string{
		"PageOfNetworkPool",
		"PageMetadata",
		"NetworkPool",
		"Network",
		"IpPool",
		"Error",
	} {
		if len(schemas[name]) == 0 {
			t.Fatalf("contract schema %s is absent", name)
		}
	}

	var page struct {
		Properties map[string]struct {
			Type  string `json:"type"`
			Ref   string `json:"$ref"`
			Items struct {
				Ref string `json:"$ref"`
			} `json:"items"`
		} `json:"properties"`
	}
	if err := json.Unmarshal(schemas["PageOfNetworkPool"], &page); err != nil {
		t.Fatalf("decode PageOfNetworkPool: %v", err)
	}
	if page.Properties["elements"].Type != "array" ||
		page.Properties["elements"].Items.Ref !=
			"#/components/schemas/NetworkPool" ||
		page.Properties["pageMetadata"].Ref !=
			"#/components/schemas/PageMetadata" {
		t.Fatalf("PageOfNetworkPool projection mismatch: %+v", page)
	}

	var metadata struct {
		ReadOnly   bool `json:"readOnly"`
		Properties map[string]struct {
			Type     string `json:"type"`
			Format   string `json:"format"`
			ReadOnly bool   `json:"readOnly"`
		} `json:"properties"`
	}
	if err := json.Unmarshal(schemas["PageMetadata"], &metadata); err != nil {
		t.Fatalf("decode PageMetadata: %v", err)
	}
	for _, name := range []string{
		"pageNumber",
		"pageSize",
		"totalElements",
		"totalPages",
	} {
		property := metadata.Properties[name]
		if property.Type != "integer" ||
			property.Format != "int32" ||
			!property.ReadOnly {
			t.Fatalf("PageMetadata.%s mismatch: %+v", name, property)
		}
	}
	if !metadata.ReadOnly {
		t.Fatal("PageMetadata must be readOnly")
	}

	var pool struct {
		Required   []string `json:"required"`
		Properties map[string]struct {
			Type     string `json:"type"`
			Format   string `json:"format"`
			ReadOnly bool   `json:"readOnly"`
			Items    struct {
				Ref string `json:"$ref"`
			} `json:"items"`
		} `json:"properties"`
	}
	if err := json.Unmarshal(schemas["NetworkPool"], &pool); err != nil {
		t.Fatalf("decode NetworkPool: %v", err)
	}
	if !reflect.DeepEqual(pool.Required, []string{"name", "networks"}) ||
		pool.Properties["id"].Type != "string" ||
		!pool.Properties["id"].ReadOnly ||
		pool.Properties["name"].Type != "string" ||
		pool.Properties["networks"].Type != "array" ||
		pool.Properties["networks"].Items.Ref !=
			"#/components/schemas/Network" ||
		pool.Properties["hostsCount"].Type != "integer" ||
		pool.Properties["hostsCount"].Format != "int32" ||
		!pool.Properties["hostsCount"].ReadOnly {
		t.Fatalf("NetworkPool projection mismatch: %+v", pool)
	}

	var network struct {
		Required   []string `json:"required"`
		ReadOnly   bool     `json:"readOnly"`
		Deprecated bool     `json:"deprecated"`
		Properties map[string]struct {
			Type     string `json:"type"`
			Format   string `json:"format"`
			ReadOnly bool   `json:"readOnly"`
			Items    struct {
				Ref string `json:"$ref"`
			} `json:"items"`
		} `json:"properties"`
	}
	if err := json.Unmarshal(schemas["Network"], &network); err != nil {
		t.Fatalf("decode Network: %v", err)
	}
	if !reflect.DeepEqual(
		network.Required,
		[]string{"mtu", "type", "vlanId"},
	) ||
		!network.ReadOnly ||
		!network.Deprecated ||
		network.Properties["vlanId"].Format != "int32" ||
		network.Properties["mtu"].Format != "int32" ||
		network.Properties["ipPools"].Items.Ref !=
			"#/components/schemas/IpPool" {
		t.Fatalf("Network projection mismatch: %+v", network)
	}
	for _, name := range []string{
		"id",
		"freeIps",
		"usedIps",
		"usedIpCount",
		"freeIpCount",
	} {
		if !network.Properties[name].ReadOnly {
			t.Fatalf("Network.%s must be readOnly", name)
		}
	}

	var ipPool struct {
		Required   []string `json:"required"`
		Properties map[string]struct {
			Type string `json:"type"`
		} `json:"properties"`
	}
	if err := json.Unmarshal(schemas["IpPool"], &ipPool); err != nil {
		t.Fatalf("decode IpPool: %v", err)
	}
	if !reflect.DeepEqual(ipPool.Required, []string{"end", "start"}) ||
		ipPool.Properties["start"].Type != "string" ||
		ipPool.Properties["end"].Type != "string" {
		t.Fatalf("IpPool projection mismatch: %+v", ipPool)
	}
}

func assertListWire(
	t *testing.T,
	request contractmock.Request,
	token string,
) {
	t.Helper()
	if request.Method != http.MethodGet ||
		request.Path != "/v1/network-pools" ||
		request.RawQuery != "" ||
		request.Header.Get("Accept") != "application/json" ||
		request.Header.Get("Authorization") != "Bearer "+token ||
		request.Header.Get("Content-Type") != "" ||
		len(request.Body) != 0 ||
		len(request.TransferEncoding) != 0 {
		t.Fatalf("getNetworkPool wire mismatch: %+v", request)
	}
}

func assertCreateWire(
	t *testing.T,
	request contractmock.Request,
	token string,
	spec npe.NetworkPoolSpec,
) {
	t.Helper()
	wantBody, err := json.Marshal(spec)
	if err != nil {
		t.Fatalf("marshal expected request: %v", err)
	}
	if request.Method != http.MethodPost ||
		request.Path != "/v1/network-pools" ||
		request.RawQuery != "" ||
		request.Header.Get("Accept") != "application/json" ||
		request.Header.Get("Authorization") != "Bearer "+token ||
		request.Header.Get("Content-Type") != "application/json" ||
		!reflect.DeepEqual(request.Body, wantBody) ||
		len(request.TransferEncoding) != 0 {
		t.Fatalf(
			"createNetworkPool wire mismatch:\nrequest=%+v\nbody=%s\nwant=%s",
			request,
			request.Body,
			wantBody,
		)
	}

	var envelope map[string]json.RawMessage
	if err := json.Unmarshal(request.Body, &envelope); err != nil {
		t.Fatalf("decode create envelope: %v", err)
	}
	if !sameKeys(envelope, "name", "networks") {
		t.Fatalf("create top-level members = %v", mapKeys(envelope))
	}
	var networks []map[string]json.RawMessage
	if err := json.Unmarshal(envelope["networks"], &networks); err != nil ||
		len(networks) != 1 {
		t.Fatalf("decode create networks: %v (%d)", err, len(networks))
	}
	wantMembers := []string{
		"type",
		"ipAddressVersion",
		"ipAddressAssignmentMode",
		"vlanId",
		"mtu",
		"subnet",
		"mask",
		"gateway",
		"ipPools",
	}
	if !sameKeys(networks[0], wantMembers...) {
		t.Fatalf("create network members = %v", mapKeys(networks[0]))
	}
	for _, forbidden := range []string{
		"id",
		"hostsCount",
		"freeIps",
		"usedIps",
		"usedIpCount",
		"freeIpCount",
	} {
		if _, found := envelope[forbidden]; found {
			t.Fatalf("read-only member %q was sent at top level", forbidden)
		}
		if _, found := networks[0][forbidden]; found {
			t.Fatalf("read-only member %q was sent in network", forbidden)
		}
	}
}

func targetSpec(name string) npe.NetworkPoolSpec {
	ipv4 := "IPv4"
	static := "STATIC"
	subnet := "10.0.0.0"
	mask := "255.255.255.0"
	gateway := "10.0.0.1"
	return npe.NetworkPoolSpec{
		Name: name,
		Networks: []npe.NetworkSpec{{
			Type:                    "VMOTION",
			IPAddressVersion:        &ipv4,
			IPAddressAssignmentMode: &static,
			VLANID:                  120,
			MTU:                     9000,
			Subnet:                  &subnet,
			Mask:                    &mask,
			Gateway:                 &gateway,
			IPPools: []npe.IPPool{{
				Start: "10.0.0.10",
				End:   "10.0.0.20",
			}},
		}},
	}
}

func publicPools(input []contractmock.NetworkPool) []npe.NetworkPool {
	data, err := json.Marshal(input)
	if err != nil {
		panic(err)
	}
	var output []npe.NetworkPool
	if err := json.Unmarshal(data, &output); err != nil {
		panic(err)
	}
	return output
}

func replaceName(
	input npe.NetworkPoolSpec,
	name string,
) npe.NetworkPoolSpec {
	output := cloneSpec(input)
	output.Name = name
	return output
}

func replaceNetwork(
	input npe.NetworkPoolSpec,
	change func(*npe.NetworkSpec),
) npe.NetworkPoolSpec {
	output := cloneSpec(input)
	change(&output.Networks[0])
	return output
}

func cloneSpec(input npe.NetworkPoolSpec) npe.NetworkPoolSpec {
	data, err := json.Marshal(input)
	if err != nil {
		panic(err)
	}
	var output npe.NetworkPoolSpec
	if err := json.Unmarshal(data, &output); err != nil {
		panic(err)
	}
	return output
}

func newServer(t *testing.T, mode contractmock.Mode) *contractmock.Server {
	t.Helper()
	server, err := contractmock.New(mode)
	if err != nil {
		t.Fatalf("contractmock.New: %v", err)
	}
	t.Cleanup(server.Close)
	if !strings.HasPrefix(server.URL(), "http://127.0.0.1:") {
		t.Fatalf("fixture did not bind IPv4 loopback: %q", server.URL())
	}
	return server
}

func newClient(
	t *testing.T,
	server *contractmock.Server,
	token string,
) *npe.Client {
	t.Helper()
	client, err := npe.NewClient(npe.Config{
		BaseURL:     server.URL(),
		AccessToken: token,
		HTTPClient:  server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func sameKeys(value map[string]json.RawMessage, names ...string) bool {
	if len(value) != len(names) {
		return false
	}
	for _, name := range names {
		if _, ok := value[name]; !ok {
			return false
		}
	}
	return true
}

func mapKeys(value map[string]json.RawMessage) []string {
	keys := make([]string, 0, len(value))
	for key := range value {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func readJSON(t *testing.T, path string, out any) {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(content, out); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func assertFileHash(t *testing.T, path, want string) {
	t.Helper()
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	sum := sha256.Sum256(content)
	got := hex.EncodeToString(sum[:])
	if got != want {
		t.Fatalf("%s SHA-256 = %s, want %s", path, got, want)
	}
}

func assertRedacted(t *testing.T, text string, secrets ...string) {
	t.Helper()
	for _, secret := range secrets {
		if secret != "" && strings.Contains(text, secret) {
			t.Fatalf("error text leaked %q: %q", secret, text)
		}
	}
}

type roundTripperFunc func(*http.Request) (*http.Response, error)

func (function roundTripperFunc) RoundTrip(
	request *http.Request,
) (*http.Response, error) {
	return function(request)
}

func ExampleClient_EnsureNetworkPool() {
	fmt.Println("EnsureNetworkPool lists before creating, so a later retry can adopt")
	// Output: EnsureNetworkPool lists before creating, so a later retry can adopt
}
