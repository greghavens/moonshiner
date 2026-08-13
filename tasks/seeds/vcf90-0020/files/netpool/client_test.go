// Verifier for the SDDC Manager 9.0 network pool client.
//
// Every case runs against the loopback double in netpool/mock. No VMware
// endpoint is contacted. The suite checks three things:
//
//   - the client puts exactly the request bytes on the wire that
//     docs/contract.json describes, including omitting optional properties the
//     caller left unset rather than sending them empty;
//   - EnsureNetworkPool is safe to repeat and safe to retry, so no sequence of
//     failures and retries leaves two pools of one name behind;
//   - the client never issues a request outside the two operations the contract
//     names.
package netpool_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"sync"
	"testing"
	"time"

	"vcfnetpool/netpool"
	"vcfnetpool/netpool/mock"
)

const (
	testToken = "test-token"
	poolName  = "vcf-np-01"
)

// ---------------------------------------------------------------------------
// specimen inputs
// ---------------------------------------------------------------------------

// minimalSpec asks for a pool whose single network leaves the one optional
// property, ipPools, unset.
func minimalSpec() netpool.NetworkPoolSpec {
	return netpool.NetworkPoolSpec{
		Name: poolName,
		Networks: []netpool.NetworkSpec{{
			Type:    "VSAN",
			VLANID:  1421,
			MTU:     9000,
			Subnet:  "172.16.21.0",
			Mask:    "255.255.255.0",
			Gateway: "172.16.21.253",
		}},
	}
}

// fullSpec populates the optional property as well.
func fullSpec() netpool.NetworkPoolSpec {
	spec := minimalSpec()
	spec.Networks[0].IPPools = []netpool.IPRange{{Start: "172.16.21.10", End: "172.16.21.60"}}
	return spec
}

// twoNetworkSpec mixes a network that sets ipPools with one that does not.
func twoNetworkSpec() netpool.NetworkPoolSpec {
	spec := fullSpec()
	spec.Networks = append(spec.Networks, netpool.NetworkSpec{
		Type:    "VMOTION",
		VLANID:  1422,
		MTU:     9000,
		Subnet:  "172.16.22.0",
		Mask:    "255.255.255.0",
		Gateway: "172.16.22.253",
	})
	return spec
}

// ---------------------------------------------------------------------------
// contract provenance
// ---------------------------------------------------------------------------

type contractDoc struct {
	Source struct {
		Repository  string `json:"repository"`
		License     string `json:"license"`
		Tag         string `json:"tag"`
		Commit      string `json:"commit"`
		SpecPath    string `json:"specPath"`
		InfoVersion string `json:"infoVersion"`
	} `json:"source"`
	Operations []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	} `json:"operations"`
	RequestEncoding struct {
		CreateNetworkPool struct {
			RequiredKeys  []string `json:"requiredKeys"`
			AllowedKeys   []string `json:"allowedKeys"`
			ForbiddenKeys []string `json:"forbiddenKeys"`
			Networks      struct {
				RequiredKeys  []string `json:"requiredKeys"`
				AllowedKeys   []string `json:"allowedKeys"`
				OptionalKeys  []string `json:"optionalKeys"`
				ForbiddenKeys []string `json:"forbiddenKeys"`
				IPPools       struct {
					RequiredKeys []string `json:"requiredKeys"`
					AllowedKeys  []string `json:"allowedKeys"`
				} `json:"ipPools"`
			} `json:"networks"`
		} `json:"createNetworkPool"`
		RevisionGuard struct {
			PropertiesAbsentAt90 []string `json:"propertiesAbsentAt9_0"`
			NetworkRequiredAt90  []string `json:"networkRequiredAt9_0"`
		} `json:"revisionGuard"`
	} `json:"requestEncoding"`
}

type sourcesDoc struct {
	Sources []struct {
		Tag          string `json:"tag"`
		Commit       string `json:"commit"`
		SpecPath     string `json:"specPath"`
		OperationIDs []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operationIds"`
	} `json:"sources"`
}

func loadJSON(t *testing.T, name string, into any) {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "docs", name))
	if err != nil {
		t.Fatalf("read docs/%s: %v", name, err)
	}
	if err := json.Unmarshal(raw, into); err != nil {
		t.Fatalf("parse docs/%s: %v", name, err)
	}
}

// TestContractPinsTheDeclaredSpecRevision keeps the contract, its provenance
// record and the double from drifting apart, and keeps all three pinned to the
// 9.0.0.0 revision rather than the 9.1.0.0 revision of the same file.
func TestContractPinsTheDeclaredSpecRevision(t *testing.T) {
	var contract contractDoc
	var sources sourcesDoc
	loadJSON(t, "contract.json", &contract)
	loadJSON(t, "official_sources.json", &sources)

	const (
		wantTag    = "9.0.0.0"
		wantCommit = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
		wantPath   = "specifications/sddc-manager/sddc-manager-openapi.json"
	)
	if len(sources.Sources) != 1 {
		t.Fatalf("official_sources.json: want exactly 1 source, got %d", len(sources.Sources))
	}
	src := sources.Sources[0]

	for _, tc := range []struct{ name, got, want string }{
		{"contract.source.tag", contract.Source.Tag, wantTag},
		{"contract.source.commit", contract.Source.Commit, wantCommit},
		{"contract.source.specPath", contract.Source.SpecPath, wantPath},
		{"contract.source.infoVersion", contract.Source.InfoVersion, wantTag},
		{"official_sources.tag", src.Tag, wantTag},
		{"official_sources.commit", src.Commit, wantCommit},
		{"official_sources.specPath", src.SpecPath, wantPath},
	} {
		if tc.got != tc.want {
			t.Errorf("%s = %q, want %q", tc.name, tc.got, tc.want)
		}
	}

	wantOps := []struct{ id, method, path string }{
		{netpool.OpCreateNetworkPool, http.MethodPost, "/v1/network-pools"},
		{netpool.OpGetNetworkPool, http.MethodGet, "/v1/network-pools"},
	}
	if len(contract.Operations) != len(wantOps) {
		t.Fatalf("contract names %d operations, want %d", len(contract.Operations), len(wantOps))
	}
	for _, want := range wantOps {
		found := false
		for _, got := range contract.Operations {
			if got.OperationID == want.id {
				found = true
				if got.Method != want.method || got.Path != want.path {
					t.Errorf("contract operation %s = %s %s, want %s %s",
						want.id, got.Method, got.Path, want.method, want.path)
				}
			}
		}
		if !found {
			t.Errorf("contract does not name operationId %q", want.id)
		}
		recorded := false
		for _, got := range src.OperationIDs {
			if got.OperationID == want.id && got.Method == want.method && got.Path == want.path {
				recorded = true
			}
		}
		if !recorded {
			t.Errorf("official_sources.json does not record operationId %q as %s %s", want.id, want.method, want.path)
		}
	}

	// The double enforces the contract, so its key sets must be the contract's.
	enc := contract.RequestEncoding.CreateNetworkPool
	for _, tc := range []struct {
		name      string
		got, want []string
	}{
		{"pool.requiredKeys", mock.Contract.PoolRequiredKeys, enc.RequiredKeys},
		{"pool.allowedKeys", mock.Contract.PoolAllowedKeys, enc.AllowedKeys},
		{"pool.forbiddenKeys", mock.Contract.PoolForbiddenKeys, enc.ForbiddenKeys},
		{"network.requiredKeys", mock.Contract.NetworkRequiredKeys, enc.Networks.RequiredKeys},
		{"network.allowedKeys", mock.Contract.NetworkAllowedKeys, enc.Networks.AllowedKeys},
		{"network.optionalKeys", mock.Contract.NetworkOptionalKeys, enc.Networks.OptionalKeys},
		{"network.forbiddenKeys", mock.Contract.NetworkForbiddenKeys, enc.Networks.ForbiddenKeys},
		{"ipPool.requiredKeys", mock.Contract.IPPoolRequiredKeys, enc.Networks.IPPools.RequiredKeys},
		{"ipPool.allowedKeys", mock.Contract.IPPoolAllowedKeys, enc.Networks.IPPools.AllowedKeys},
		{"revisionGuard.propertiesAbsentAt9_0", mock.Contract.KeysAbsentAt90,
			contract.RequestEncoding.RevisionGuard.PropertiesAbsentAt90},
	} {
		if !sameSet(tc.got, tc.want) {
			t.Errorf("double and contract disagree on %s: double has %v, contract has %v", tc.name, tc.got, tc.want)
		}
	}

	// Network.required is the sharpest discriminator between the two revisions:
	// 9.1.0.0 keeps only mtu, type and vlanId.
	wantRequired := []string{"gateway", "mask", "mtu", "subnet", "type", "vlanId"}
	if !sameSet(contract.RequestEncoding.RevisionGuard.NetworkRequiredAt90, wantRequired) {
		t.Errorf("contract records Network.required at 9.0.0.0 as %v, want %v",
			contract.RequestEncoding.RevisionGuard.NetworkRequiredAt90, wantRequired)
	}
}

// ---------------------------------------------------------------------------
// wire shape
// ---------------------------------------------------------------------------

// TestCreateRequestWireShape asserts the exact bytes of the mutating request.
// The comparison is against a whole decoded object, so an extra property is a
// failure just as surely as a missing one.
func TestCreateRequestWireShape(t *testing.T) {
	cases := []struct {
		name string
		spec netpool.NetworkPoolSpec
		// wantBody is the complete request body. Any difference fails, including
		// a property present here but absent on the wire or the other way round.
		wantBody string
	}{
		{
			name: "optional ipPools left unset is omitted, not sent empty",
			spec: minimalSpec(),
			wantBody: `{
			  "name": "vcf-np-01",
			  "networks": [
			    {
			      "type": "VSAN",
			      "vlanId": 1421,
			      "mtu": 9000,
			      "subnet": "172.16.21.0",
			      "mask": "255.255.255.0",
			      "gateway": "172.16.21.253"
			    }
			  ]
			}`,
		},
		{
			name: "optional ipPools set is sent",
			spec: fullSpec(),
			wantBody: `{
			  "name": "vcf-np-01",
			  "networks": [
			    {
			      "type": "VSAN",
			      "vlanId": 1421,
			      "mtu": 9000,
			      "subnet": "172.16.21.0",
			      "mask": "255.255.255.0",
			      "gateway": "172.16.21.253",
			      "ipPools": [{"start": "172.16.21.10", "end": "172.16.21.60"}]
			    }
			  ]
			}`,
		},
		{
			name: "one network omits ipPools while its sibling sends them",
			spec: twoNetworkSpec(),
			wantBody: `{
			  "name": "vcf-np-01",
			  "networks": [
			    {
			      "type": "VSAN",
			      "vlanId": 1421,
			      "mtu": 9000,
			      "subnet": "172.16.21.0",
			      "mask": "255.255.255.0",
			      "gateway": "172.16.21.253",
			      "ipPools": [{"start": "172.16.21.10", "end": "172.16.21.60"}]
			    },
			    {
			      "type": "VMOTION",
			      "vlanId": 1422,
			      "mtu": 9000,
			      "subnet": "172.16.22.0",
			      "mask": "255.255.255.0",
			      "gateway": "172.16.22.253"
			    }
			  ]
			}`,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			server, client := newFixture(t, mock.Options{})

			if _, err := client.EnsureNetworkPool(context.Background(), tc.spec); err != nil {
				t.Fatalf("EnsureNetworkPool: unexpected error: %v", err)
			}

			creates := server.RequestsFor(mock.OpCreateNetworkPool)
			if len(creates) != 1 {
				t.Fatalf("want exactly 1 createNetworkPool request, got %d", len(creates))
			}
			got := creates[0]

			if got.Method != http.MethodPost {
				t.Errorf("method = %s, want POST", got.Method)
			}
			if got.Path != "/v1/network-pools" {
				t.Errorf("path = %s, want /v1/network-pools", got.Path)
			}
			if got.RawQuery != "" {
				t.Errorf("query = %q, want empty; createNetworkPool declares no parameters", got.RawQuery)
			}
			for header, want := range map[string]string{
				"Content-Type":  "application/json",
				"Accept":        "application/json",
				"Authorization": "Bearer " + testToken,
			} {
				if v := got.Header.Get(header); v != want {
					t.Errorf("header %s = %q, want %q", header, v, want)
				}
			}

			assertJSONEqual(t, "createNetworkPool body", got.Body, tc.wantBody)
			assertNoContractViolations(t, server)
		})
	}
}

// TestListRequestWireShape pins the read side: getNetworkPool declares no
// parameters at 9.0.0.0, so the request carries no query string and no body.
func TestListRequestWireShape(t *testing.T) {
	server, client := newFixture(t, mock.Options{})

	if _, err := client.ListNetworkPools(context.Background()); err != nil {
		t.Fatalf("ListNetworkPools: unexpected error: %v", err)
	}

	reads := server.RequestsFor(mock.OpGetNetworkPool)
	if len(reads) != 1 {
		t.Fatalf("want exactly 1 getNetworkPool request, got %d", len(reads))
	}
	got := reads[0]
	if got.Method != http.MethodGet {
		t.Errorf("method = %s, want GET", got.Method)
	}
	if got.RawQuery != "" {
		t.Errorf("query = %q, want empty; getNetworkPool declares no parameters at 9.0.0.0", got.RawQuery)
	}
	if len(got.Body) != 0 {
		t.Errorf("body = %q, want none", got.Body)
	}
	if v := got.Header.Get("Accept"); v != "application/json" {
		t.Errorf("Accept = %q, want application/json", v)
	}
	if v := got.Header.Get("Authorization"); v != "Bearer "+testToken {
		t.Errorf("Authorization = %q, want bearer token", v)
	}
	assertNoContractViolations(t, server)
}

// ---------------------------------------------------------------------------
// retry safety
// ---------------------------------------------------------------------------

// TestEnsureNetworkPoolIsRetrySafe drives the mutating call through the failure
// modes that make a naive create unsafe to repeat. In every case the appliance
// must end up holding exactly one pool of the requested name.
func TestEnsureNetworkPoolIsRetrySafe(t *testing.T) {
	cases := []struct {
		name string
		// seeded pools already on the appliance.
		seeded []mock.Pool
		// faults script consecutive createNetworkPool requests.
		faults []mock.Fault
		// calls is how many times the caller invokes EnsureNetworkPool.
		calls int
		// wantErrOnCall is the 1-based index of the call expected to fail, or 0
		// when every call is expected to succeed.
		wantErrOnCall int
		// wantCreateRequests counts createNetworkPool requests reaching the server.
		wantCreateRequests int
		// wantCreatedFlags is the Created flag of each successful call, in order.
		wantCreatedFlags []bool
	}{
		{
			name:               "first call on an empty appliance creates the pool",
			calls:              1,
			wantCreateRequests: 1,
			wantCreatedFlags:   []bool{true},
		},
		{
			name:               "calling again is a no-op and sends no second create",
			calls:              3,
			wantCreateRequests: 1,
			wantCreatedFlags:   []bool{true, false, false},
		},
		{
			name:               "a pool that already exists is adopted without any create",
			seeded:             []mock.Pool{{Name: poolName, Networks: []mock.Network{{Type: "VSAN", VLANID: 1421, MTU: 9000, Subnet: "172.16.21.0", Mask: "255.255.255.0", Gateway: "172.16.21.253"}}}},
			calls:              2,
			wantCreateRequests: 0,
			wantCreatedFlags:   []bool{false, false},
		},
		{
			name:               "an unrelated pool does not block the create",
			seeded:             []mock.Pool{{Name: "some-other-pool"}},
			calls:              2,
			wantCreateRequests: 1,
			wantCreatedFlags:   []bool{true, false},
		},
		{
			name:               "a create that lands but answers 503 is reconciled, not repeated",
			faults:             []mock.Fault{mock.FaultApplyThenUnavailable},
			calls:              2,
			wantCreateRequests: 1,
			wantCreatedFlags:   []bool{false, false},
		},
		{
			name:               "a create that lands but loses its response is reconciled, not repeated",
			faults:             []mock.Fault{mock.FaultApplyThenHangUp},
			calls:              2,
			wantCreateRequests: 1,
			wantCreatedFlags:   []bool{false, false},
		},
		{
			name:               "a duplicate-name rejection converges on the existing pool",
			faults:             []mock.Fault{mock.FaultLoseRaceThenDuplicate},
			calls:              2,
			wantCreateRequests: 1,
			wantCreatedFlags:   []bool{false, false},
		},
		{
			name:               "a create that genuinely failed surfaces the error and the retry succeeds",
			faults:             []mock.Fault{mock.FaultRejectUnapplied},
			calls:              2,
			wantErrOnCall:      1,
			wantCreateRequests: 2,
			wantCreatedFlags:   []bool{true},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			server, client := newFixture(t, mock.Options{Pools: tc.seeded, CreateFaults: tc.faults})

			var createdFlags []bool
			for call := 1; call <= tc.calls; call++ {
				result, err := client.EnsureNetworkPool(context.Background(), minimalSpec())
				switch {
				case call == tc.wantErrOnCall && err == nil:
					t.Fatalf("call %d: want an error, got none", call)
				case call == tc.wantErrOnCall:
					// The failure must be reported as a server-side error, not
					// swallowed. The retry below is the caller's job.
					var apiErr *netpool.APIError
					if !errors.As(err, &apiErr) {
						t.Fatalf("call %d: want an *netpool.APIError, got %#v", call, err)
					}
				case err != nil:
					t.Fatalf("call %d: unexpected error: %v", call, err)
				default:
					createdFlags = append(createdFlags, result.Created)
					if result.Pool.Name != poolName {
						t.Errorf("call %d: pool name = %q, want %q", call, result.Pool.Name, poolName)
					}
					if result.Pool.ID == "" {
						t.Errorf("call %d: pool ID is empty; the server-assigned identifier was not returned", call)
					}
				}
			}

			// The property that matters: one pool, no matter the path taken.
			if got := len(server.PoolsNamed(poolName)); got != 1 {
				t.Fatalf("appliance holds %d pools named %q, want exactly 1; the mutation was duplicated", got, poolName)
			}
			if got := server.AppliedCreates(); got > 1 {
				t.Errorf("%d createNetworkPool requests changed state, want at most 1", got)
			}
			if got := len(server.RequestsFor(mock.OpCreateNetworkPool)); got != tc.wantCreateRequests {
				t.Errorf("createNetworkPool requests = %d, want %d (log: %s)", got, tc.wantCreateRequests, summarize(server))
			}
			if !reflect.DeepEqual(createdFlags, tc.wantCreatedFlags) {
				t.Errorf("Created flags = %v, want %v", createdFlags, tc.wantCreatedFlags)
			}
			assertNoContractViolations(t, server)
		})
	}
}

// TestEnsureNetworkPoolChecksBeforeMutating pins the ordering: the very first
// request of a call is the read, so a pool that is already there is never
// mutated toward.
func TestEnsureNetworkPoolChecksBeforeMutating(t *testing.T) {
	server, client := newFixture(t, mock.Options{})

	if _, err := client.EnsureNetworkPool(context.Background(), minimalSpec()); err != nil {
		t.Fatalf("EnsureNetworkPool: unexpected error: %v", err)
	}

	log := server.Requests()
	if len(log) == 0 {
		t.Fatal("the double received no requests at all")
	}
	if log[0].OperationID != mock.OpGetNetworkPool {
		t.Fatalf("first request was %s, want %s; the mutation must be preceded by a read (log: %s)",
			log[0].OperationID, mock.OpGetNetworkPool, summarize(server))
	}
}

// TestConcurrentEnsureDoesNotDuplicate runs the mutating call from several
// goroutines at once. Exactly one create may take effect.
func TestConcurrentEnsureDoesNotDuplicate(t *testing.T) {
	server, client := newFixture(t, mock.Options{})

	const goroutines = 8
	var wg sync.WaitGroup
	errs := make([]error, goroutines)
	start := make(chan struct{})
	for i := 0; i < goroutines; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			_, errs[i] = client.EnsureNetworkPool(context.Background(), minimalSpec())
		}(i)
	}
	close(start)
	wg.Wait()

	for i, err := range errs {
		if err != nil {
			t.Errorf("goroutine %d: unexpected error: %v", i, err)
		}
	}
	if got := len(server.PoolsNamed(poolName)); got != 1 {
		t.Fatalf("appliance holds %d pools named %q, want exactly 1", got, poolName)
	}
	if got := server.AppliedCreates(); got != 1 {
		t.Errorf("%d createNetworkPool requests changed state, want exactly 1", got)
	}
	assertNoContractViolations(t, server)
}

// ---------------------------------------------------------------------------
// contract confinement
// ---------------------------------------------------------------------------

// TestOnlyContractOperationsAreUsed checks that the client stays inside the two
// operations the contract names, and that the double would in fact notice if it
// did not.
func TestOnlyContractOperationsAreUsed(t *testing.T) {
	server, client := newFixture(t, mock.Options{CreateFaults: []mock.Fault{mock.FaultApplyThenHangUp}})

	for i := 0; i < 2; i++ {
		if _, err := client.EnsureNetworkPool(context.Background(), fullSpec()); err != nil {
			t.Fatalf("EnsureNetworkPool: unexpected error: %v", err)
		}
	}
	if off := server.OffContractRequests(); len(off) != 0 {
		for _, r := range off {
			t.Errorf("off-contract request: %s %s", r.Method, r.Path)
		}
	}
	for _, r := range server.Requests() {
		switch r.OperationID {
		case mock.OpGetNetworkPool, mock.OpCreateNetworkPool:
		default:
			t.Errorf("request #%d matched no contract operation: %s %s", r.Seq, r.Method, r.Path)
		}
	}

	// The double serves nothing else, so the check above has teeth.
	for _, probe := range []struct{ method, path string }{
		{http.MethodDelete, "/v1/network-pools/np-0001"},
		{http.MethodPatch, "/v1/network-pools/np-0001"},
		{http.MethodGet, "/v1/network-pools/np-0001"},
		{http.MethodGet, "/v1/network-pools/np-0001/networks"},
	} {
		req, err := http.NewRequest(probe.method, server.URL+probe.path, nil)
		if err != nil {
			t.Fatalf("build probe: %v", err)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("probe %s %s: %v", probe.method, probe.path, err)
		}
		_ = resp.Body.Close()
		if resp.StatusCode != http.StatusNotFound && resp.StatusCode != http.StatusMethodNotAllowed {
			t.Errorf("probe %s %s returned %d; the double must serve only contract operations",
				probe.method, probe.path, resp.StatusCode)
		}
	}
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

func newFixture(t *testing.T, opts mock.Options) (*mock.Server, *netpool.Client) {
	t.Helper()
	if opts.Token == "" {
		opts.Token = testToken
	}
	server := mock.Start(opts)
	t.Cleanup(server.Close)
	httpClient := &http.Client{Timeout: 10 * time.Second}
	return server, netpool.New(server.URL, opts.Token, httpClient)
}

// assertNoContractViolations fails with every departure from docs/contract.json
// the double noticed, across every request of the case.
func assertNoContractViolations(t *testing.T, server *mock.Server) {
	t.Helper()
	for _, v := range server.Violations() {
		t.Errorf("contract violation: %s", v)
	}
}

// assertJSONEqual compares a request body against the expected wire shape as
// whole decoded values, so extra properties fail as loudly as missing ones.
func assertJSONEqual(t *testing.T, what string, got []byte, want string) {
	t.Helper()
	var gotValue, wantValue any
	if err := json.Unmarshal(got, &gotValue); err != nil {
		t.Fatalf("%s: body is not valid JSON: %v (raw: %s)", what, err, got)
	}
	if err := json.Unmarshal([]byte(want), &wantValue); err != nil {
		t.Fatalf("%s: expected shape is not valid JSON: %v", what, err)
	}
	if reflect.DeepEqual(gotValue, wantValue) {
		return
	}
	gotPretty, _ := json.MarshalIndent(gotValue, "", "  ")
	wantPretty, _ := json.MarshalIndent(wantValue, "", "  ")
	t.Errorf("%s does not match the contract wire shape.\n got: %s\nwant: %s\ndiff: %s",
		what, gotPretty, wantPretty, describeJSONDiff("", gotValue, wantValue))
}

// describeJSONDiff walks two decoded documents and reports the first
// disagreements it finds, so a failure names the offending property.
func describeJSONDiff(path string, got, want any) string {
	at := path
	if at == "" {
		at = "(root)"
	}
	switch wantTyped := want.(type) {
	case map[string]any:
		gotTyped, ok := got.(map[string]any)
		if !ok {
			return fmt.Sprintf("%s: want an object, got %T", at, got)
		}
		var notes []string
		for _, key := range sortedKeys(gotTyped) {
			if _, expected := wantTyped[key]; !expected {
				notes = append(notes, fmt.Sprintf("%s: unexpected property %q with value %v", at, key, gotTyped[key]))
			}
		}
		for _, key := range sortedKeys(wantTyped) {
			gotChild, present := gotTyped[key]
			if !present {
				notes = append(notes, fmt.Sprintf("%s: missing property %q", at, key))
				continue
			}
			if !reflect.DeepEqual(gotChild, wantTyped[key]) {
				notes = append(notes, describeJSONDiff(path+"."+key, gotChild, wantTyped[key]))
			}
		}
		return joinNonEmpty(notes)
	case []any:
		gotTyped, ok := got.([]any)
		if !ok {
			return fmt.Sprintf("%s: want an array, got %T", at, got)
		}
		if len(gotTyped) != len(wantTyped) {
			return fmt.Sprintf("%s: length %d, want %d", at, len(gotTyped), len(wantTyped))
		}
		var notes []string
		for i := range wantTyped {
			if !reflect.DeepEqual(gotTyped[i], wantTyped[i]) {
				notes = append(notes, describeJSONDiff(fmt.Sprintf("%s[%d]", path, i), gotTyped[i], wantTyped[i]))
			}
		}
		return joinNonEmpty(notes)
	default:
		return fmt.Sprintf("%s: got %#v, want %#v", at, got, want)
	}
}

func joinNonEmpty(notes []string) string {
	out := ""
	for _, n := range notes {
		if n == "" {
			continue
		}
		if out != "" {
			out += "; "
		}
		out += n
	}
	return out
}

func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func sameSet(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	x := append([]string(nil), a...)
	y := append([]string(nil), b...)
	sort.Strings(x)
	sort.Strings(y)
	return reflect.DeepEqual(x, y)
}

// summarize renders the request log for a failure message.
func summarize(server *mock.Server) string {
	out := ""
	for _, r := range server.Requests() {
		op := r.OperationID
		if op == "" {
			op = "off-contract"
		}
		if out != "" {
			out += ", "
		}
		out += fmt.Sprintf("#%d %s %s -> %d", r.Seq, op, r.Path, r.Status)
	}
	if out == "" {
		return "(empty)"
	}
	return out
}
