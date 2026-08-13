package verify

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"vcfops.local/opssync/mock"
	"vcfops.local/opssync/opsapi"
	"vcfops.local/opssync/vcfops"
)

// inventory is the fixture the mock pages over.
var inventory = []opsapi.Resource{
	{Identifier: "0a1f0a2c-1e5a-4a8f-8b1c-2f8a9c0d1e01", Name: "esx-01.lab", AdapterKindKey: "VMWARE", ResourceKindKey: "HostSystem"},
	{Identifier: "0a1f0a2c-1e5a-4a8f-8b1c-2f8a9c0d1e02", Name: "esx-02.lab", AdapterKindKey: "VMWARE", ResourceKindKey: "HostSystem"},
	{Identifier: "0a1f0a2c-1e5a-4a8f-8b1c-2f8a9c0d1e03", Name: "payroll-db", AdapterKindKey: "VMWARE", ResourceKindKey: "VirtualMachine"},
	{Identifier: "0a1f0a2c-1e5a-4a8f-8b1c-2f8a9c0d1e04", Name: "payroll-web", AdapterKindKey: "VMWARE", ResourceKindKey: "VirtualMachine"},
	{Identifier: "0a1f0a2c-1e5a-4a8f-8b1c-2f8a9c0d1e05", Name: "vsan-cluster-a", AdapterKindKey: "VMWARE", ResourceKindKey: "ClusterComputeResource"},
	{Identifier: "0a1f0a2c-1e5a-4a8f-8b1c-2f8a9c0d1e06", Name: "nsx-edge-01", AdapterKindKey: "NSXTAdapter", ResourceKindKey: "EdgeNode"},
	{Identifier: "0a1f0a2c-1e5a-4a8f-8b1c-2f8a9c0d1e07", Name: "nsx-edge-02", AdapterKindKey: "NSXTAdapter", ResourceKindKey: "EdgeNode"},
}

// loopbackOnly refuses to dial anything but the loopback interface, so a
// failing test can never reach a real appliance.
func loopbackOnly(t *testing.T) *http.Client {
	t.Helper()
	dialer := &net.Dialer{Timeout: 5 * time.Second}
	return &http.Client{
		Timeout: 20 * time.Second,
		Transport: &http.Transport{
			DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
				host, _, err := net.SplitHostPort(address)
				if err != nil {
					return nil, err
				}
				ip := net.ParseIP(host)
				if ip == nil || !ip.IsLoopback() {
					return nil, fmt.Errorf("refusing to dial non-loopback address %q", address)
				}
				return dialer.DialContext(ctx, network, address)
			},
		},
	}
}

func startMock(t *testing.T, script mock.Script) *mock.Server {
	t.Helper()
	return mock.Start(t, contractPath, script)
}

func newClient(t *testing.T, srv *mock.Server, authSource string) *vcfops.Client {
	t.Helper()
	client, err := vcfops.New(opsapi.Config{
		BaseURL:    srv.URL(),
		Username:   "svc-opssync",
		Password:   "Pa55w0rd!",
		AuthSource: authSource,
		HTTPClient: loopbackOnly(t),
	})
	if err != nil {
		t.Fatalf("vcfops.New: %v", err)
	}
	if client == nil {
		t.Fatal("vcfops.New returned a nil client and a nil error")
	}
	return client
}

func samplesFor(resources []opsapi.Resource) []opsapi.PropertySample {
	var out []opsapi.PropertySample
	for i, res := range resources {
		out = append(out,
			opsapi.PropertySample{
				ResourceID: res.Identifier,
				StatKey:    "summary|opssync|state",
				Timestamps: []int64{mock.ClockMillis},
				Values:     []string{"RECONCILED"},
			},
			opsapi.PropertySample{
				ResourceID: res.Identifier,
				StatKey:    "summary|opssync|revision",
				Timestamps: []int64{mock.ClockMillis},
				Data:       []float64{float64(i + 1)},
			},
		)
	}
	return out
}

// TestTokenExpiryDoesNotLoseWork drives a full listing walk and property push
// across two token expiries and checks that nothing is repeated or dropped.
func TestTokenExpiryDoesNotLoseWork(t *testing.T) {
	srv := startMock(t, mock.Script{Resources: inventory, ExpiresAfter: []int{2, 2}})
	client := newClient(t, srv, "")
	ctx := context.Background()

	got, err := client.ListAllResources(ctx, opsapi.ResourceFilter{PageSize: 3})
	if err != nil {
		t.Fatalf("ListAllResources: %v", err)
	}
	if !reflect.DeepEqual(got, inventory) {
		t.Fatalf("ListAllResources returned %d resources:\n got %+v\nwant %+v", len(got), got, inventory)
	}

	pushed := inventory[:5]
	if err := client.PushProperties(ctx, samplesFor(pushed), 2); err != nil {
		t.Fatalf("PushProperties: %v", err)
	}

	t.Run("token_refreshed_twice", func(t *testing.T) {
		want := []string{"ops-token-1", "ops-token-2", "ops-token-3"}
		if got := srv.IssuedTokens(); !reflect.DeepEqual(got, want) {
			t.Errorf("issued tokens = %v, want %v", got, want)
		}
	})

	t.Run("pages_fetched_once_each", func(t *testing.T) {
		var succeeded []string
		for _, r := range srv.RequestsFor("getResources") {
			if r.Status == http.StatusOK {
				succeeded = append(succeeded, r.Query.Get("page"))
			}
		}
		want := []string{"0", "1", "2"}
		if !reflect.DeepEqual(succeeded, want) {
			t.Errorf("successful page sequence = %v, want %v (every page exactly once, none re-read after a refresh)", succeeded, want)
		}
	})

	t.Run("batches_accepted_once_each", func(t *testing.T) {
		batches := srv.AcceptedBatches()
		if len(batches) != 3 {
			t.Fatalf("accepted %d property batches, want 3", len(batches))
		}
		var flat []string
		for _, b := range batches {
			flat = append(flat, b.ResourceIDs...)
		}
		want := make([]string, 0, len(pushed))
		for _, res := range pushed {
			want = append(want, res.Identifier)
		}
		if !reflect.DeepEqual(flat, want) {
			t.Errorf("accepted resourceIds = %v, want %v (each exactly once, in order)", flat, want)
		}
		for _, b := range batches {
			for _, id := range b.ResourceIDs {
				keys := b.StatKeys[id]
				wantKeys := []string{"summary|opssync|state", "summary|opssync|revision"}
				if !reflect.DeepEqual(keys, wantKeys) {
					t.Errorf("statKeys for %s = %v, want %v", id, keys, wantKeys)
				}
			}
		}
	})

	t.Run("stats_agree_with_the_wire", func(t *testing.T) {
		want := opsapi.Stats{TokensAcquired: 3, ResourcePagesFetched: 3, PropertyBatchesSent: 3}
		if got := client.Stats(); got != want {
			t.Errorf("Stats() = %+v, want %+v", got, want)
		}
	})

	t.Run("each_401_is_followed_by_a_refresh_and_an_identical_retry", func(t *testing.T) {
		log := srv.Requests()
		unauthorized := 0
		for i, r := range log {
			if r.Status != http.StatusUnauthorized {
				continue
			}
			unauthorized++
			if i+2 >= len(log) {
				t.Fatalf("request %d got 401 but the log ends before a refresh and retry", i)
			}
			refresh, retry := log[i+1], log[i+2]
			if refresh.OperationID != "acquireToken" {
				t.Errorf("after the 401 at index %d the next call was %q, want acquireToken", i, refresh.OperationID)
			}
			if retry.OperationID != r.OperationID || retry.Method != r.Method || retry.Path != r.Path {
				t.Errorf("retry after the 401 at index %d was %s %s (%s), want a repeat of %s %s (%s)",
					i, retry.Method, retry.Path, retry.OperationID, r.Method, r.Path, r.OperationID)
			}
			if !reflect.DeepEqual(retry.Query, r.Query) {
				t.Errorf("retry after the 401 at index %d sent query %v, want the original %v", i, retry.Query, r.Query)
			}
			if string(retry.Body) != string(r.Body) {
				t.Errorf("retry after the 401 at index %d sent body %s, want the original %s", i, retry.Body, r.Body)
			}
			if retry.Token == r.Token {
				t.Errorf("retry after the 401 at index %d reused the expired token %q", i, r.Token)
			}
			if retry.Status != http.StatusOK {
				t.Errorf("retry after the 401 at index %d returned %d, want 200", i, retry.Status)
			}
		}
		if unauthorized != 2 {
			t.Errorf("the run saw %d rejected requests, want 2 (one per expiry)", unauthorized)
		}
	})

	t.Run("no_request_falls_outside_the_contract", func(t *testing.T) {
		for i, r := range srv.Requests() {
			if r.OperationID == "" {
				t.Errorf("request %d (%s %s) matched no contracted operation", i, r.Method, r.Path)
				continue
			}
			switch r.Status {
			case http.StatusOK, http.StatusUnauthorized:
			default:
				t.Errorf("request %d (%s %s) was rejected with %d", i, r.Method, r.Path, r.Status)
			}
		}
	})
}

// TestRequestWireShape pins the exact bytes each operation puts on the wire.
func TestRequestWireShape(t *testing.T) {
	authHeaderCases := []struct {
		name       string
		authSource string
		wantKeys   []string
		wantSource string
	}{
		{
			name:     "auth_source_unset_is_omitted_not_empty",
			wantKeys: []string{"password", "username"},
		},
		{
			name:       "auth_source_set_is_sent",
			authSource: "vIDMAuthSource",
			wantKeys:   []string{"authSource", "password", "username"},
			wantSource: "vIDMAuthSource",
		},
	}
	for _, tc := range authHeaderCases {
		t.Run("acquire_token/"+tc.name, func(t *testing.T) {
			srv := startMock(t, mock.Script{Resources: inventory})
			client := newClient(t, srv, tc.authSource)
			if _, err := client.ListAllResources(context.Background(), opsapi.ResourceFilter{}); err != nil {
				t.Fatalf("ListAllResources: %v", err)
			}

			calls := srv.RequestsFor("acquireToken")
			if len(calls) != 1 {
				t.Fatalf("acquireToken was called %d times, want 1", len(calls))
			}
			call := calls[0]
			if got := call.Header.Get("Authorization"); got != "" {
				t.Errorf("acquireToken sent Authorization %q, want no credential on an unauthenticated operation", got)
			}
			assertJSONHeaders(t, call)

			body, err := call.DecodeBody()
			if err != nil {
				t.Fatalf("%v", err)
			}
			assertKeys(t, "acquireToken body", body, tc.wantKeys)
			if body["username"] != "svc-opssync" {
				t.Errorf("username = %v, want %q", body["username"], "svc-opssync")
			}
			if body["password"] != "Pa55w0rd!" {
				t.Errorf("password = %v, want %q", body["password"], "Pa55w0rd!")
			}
			if tc.wantSource != "" && body["authSource"] != tc.wantSource {
				t.Errorf("authSource = %v, want %q", body["authSource"], tc.wantSource)
			}
		})
	}

	filterCases := []struct {
		name      string
		filter    opsapi.ResourceFilter
		wantQuery url.Values
		wantCount int
	}{
		{
			name:      "empty_filter_sends_only_the_page_cursor",
			filter:    opsapi.ResourceFilter{},
			wantQuery: url.Values{"page": {"0"}},
			wantCount: 7,
		},
		{
			name:      "page_size_only",
			filter:    opsapi.ResourceFilter{PageSize: 10},
			wantQuery: url.Values{"page": {"0"}, "pageSize": {"10"}},
			wantCount: 7,
		},
		{
			name: "every_filter_field_set",
			filter: opsapi.ResourceFilter{
				Name:         []string{"esx-01.lab", "esx-02.lab"},
				AdapterKind:  []string{"VMWARE"},
				ResourceKind: []string{"HostSystem"},
				PageSize:     50,
			},
			wantQuery: url.Values{
				"page":         {"0"},
				"pageSize":     {"50"},
				"name":         {"esx-01.lab", "esx-02.lab"},
				"adapterKind":  {"VMWARE"},
				"resourceKind": {"HostSystem"},
			},
			wantCount: 2,
		},
		{
			name:      "filter_matching_nothing",
			filter:    opsapi.ResourceFilter{AdapterKind: []string{"NoSuchAdapter"}},
			wantQuery: url.Values{"page": {"0"}, "adapterKind": {"NoSuchAdapter"}},
			wantCount: 0,
		},
	}
	for _, tc := range filterCases {
		t.Run("get_resources/"+tc.name, func(t *testing.T) {
			srv := startMock(t, mock.Script{Resources: inventory})
			client := newClient(t, srv, "")

			got, err := client.ListAllResources(context.Background(), tc.filter)
			if err != nil {
				t.Fatalf("ListAllResources: %v", err)
			}
			if len(got) != tc.wantCount {
				t.Errorf("returned %d resources, want %d", len(got), tc.wantCount)
			}

			calls := srv.RequestsFor("getResources")
			if len(calls) != 1 {
				t.Fatalf("getResources was called %d times, want 1", len(calls))
			}
			call := calls[0]
			if !reflect.DeepEqual(call.Query, tc.wantQuery) {
				t.Errorf("query = %v, want exactly %v (unset filters must not appear at all)", call.Query, tc.wantQuery)
			}
			if got, want := call.Header.Get("Authorization"), mock.AuthScheme+" ops-token-1"; got != want {
				t.Errorf("Authorization = %q, want %q", got, want)
			}
			if len(call.Body) != 0 {
				t.Errorf("getResources sent a body of %d bytes, want none", len(call.Body))
			}
			assertJSONHeaders(t, call)
		})
	}

	propertyCases := []struct {
		name        string
		sample      opsapi.PropertySample
		wantContent []string
	}{
		{
			name: "string_sample_omits_data",
			sample: opsapi.PropertySample{
				ResourceID: inventory[0].Identifier,
				StatKey:    "summary|opssync|state",
				Timestamps: []int64{mock.ClockMillis},
				Values:     []string{"RECONCILED"},
			},
			wantContent: []string{"statKey", "timestamps", "values"},
		},
		{
			name: "numeric_sample_omits_values",
			sample: opsapi.PropertySample{
				ResourceID: inventory[0].Identifier,
				StatKey:    "summary|opssync|revision",
				Timestamps: []int64{mock.ClockMillis, mock.ClockMillis + 300000},
				Data:       []float64{1, 2},
			},
			wantContent: []string{"data", "statKey", "timestamps"},
		},
	}
	for _, tc := range propertyCases {
		t.Run("add_properties/"+tc.name, func(t *testing.T) {
			srv := startMock(t, mock.Script{Resources: inventory})
			client := newClient(t, srv, "")
			if err := client.PushProperties(context.Background(), []opsapi.PropertySample{tc.sample}, 10); err != nil {
				t.Fatalf("PushProperties: %v", err)
			}

			calls := srv.RequestsFor("addResourcesProperties")
			if len(calls) != 1 {
				t.Fatalf("addResourcesProperties was called %d times, want 1", len(calls))
			}
			call := calls[0]
			assertJSONHeaders(t, call)
			if got := call.Header.Get("Content-Type"); !strings.HasPrefix(got, "application/json") {
				t.Errorf("Content-Type = %q, want application/json", got)
			}

			body, err := call.DecodeBody()
			if err != nil {
				t.Fatalf("%v", err)
			}
			assertKeys(t, "request body", body, []string{"values"})

			values, ok := body["values"].([]any)
			if !ok || len(values) != 1 {
				t.Fatalf("values = %v, want a one-element array", body["values"])
			}
			entry, ok := values[0].(map[string]any)
			if !ok {
				t.Fatalf("values[0] = %v, want an object", values[0])
			}
			assertKeys(t, "values[0]", entry, []string{"property-contents", "resourceId"})
			if entry["resourceId"] != tc.sample.ResourceID {
				t.Errorf("resourceId = %v, want %q", entry["resourceId"], tc.sample.ResourceID)
			}

			contents, ok := entry["property-contents"].(map[string]any)
			if !ok {
				t.Fatalf("property-contents = %v, want an object", entry["property-contents"])
			}
			assertKeys(t, "property-contents", contents, []string{"property-content"})

			list, ok := contents["property-content"].([]any)
			if !ok || len(list) != 1 {
				t.Fatalf("property-content = %v, want a one-element array", contents["property-content"])
			}
			content, ok := list[0].(map[string]any)
			if !ok {
				t.Fatalf("property-content[0] = %v, want an object", list[0])
			}
			assertKeys(t, "property-content[0]", content, tc.wantContent)
			if content["statKey"] != tc.sample.StatKey {
				t.Errorf("statKey = %v, want %q", content["statKey"], tc.sample.StatKey)
			}
			if stamps, ok := content["timestamps"].([]any); !ok || len(stamps) != len(tc.sample.Timestamps) {
				t.Errorf("timestamps = %v, want %d entries", content["timestamps"], len(tc.sample.Timestamps))
			}
		})
	}
}

// TestPropertyBatching checks how samples are grouped and split.
func TestPropertyBatching(t *testing.T) {
	cases := []struct {
		name      string
		resources []opsapi.Resource
		batchSize int
		want      [][]string
	}{
		{
			name:      "one_resource_per_batch",
			resources: inventory[:3],
			batchSize: 1,
			want:      [][]string{{inventory[0].Identifier}, {inventory[1].Identifier}, {inventory[2].Identifier}},
		},
		{
			name:      "uneven_final_batch",
			resources: inventory[:5],
			batchSize: 2,
			want: [][]string{
				{inventory[0].Identifier, inventory[1].Identifier},
				{inventory[2].Identifier, inventory[3].Identifier},
				{inventory[4].Identifier},
			},
		},
		{
			name:      "batch_larger_than_the_input",
			resources: inventory[:2],
			batchSize: 64,
			want:      [][]string{{inventory[0].Identifier, inventory[1].Identifier}},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := startMock(t, mock.Script{Resources: inventory})
			client := newClient(t, srv, "")
			if err := client.PushProperties(context.Background(), samplesFor(tc.resources), tc.batchSize); err != nil {
				t.Fatalf("PushProperties: %v", err)
			}
			var got [][]string
			for _, b := range srv.AcceptedBatches() {
				got = append(got, b.ResourceIDs)
			}
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("batches = %v, want %v", got, tc.want)
			}
			if n := client.Stats().PropertyBatchesSent; n != len(tc.want) {
				t.Errorf("Stats().PropertyBatchesSent = %d, want %d", n, len(tc.want))
			}
		})
	}

	t.Run("no_samples_sends_nothing", func(t *testing.T) {
		srv := startMock(t, mock.Script{Resources: inventory})
		client := newClient(t, srv, "")
		if err := client.PushProperties(context.Background(), nil, 4); err != nil {
			t.Fatalf("PushProperties: %v", err)
		}
		if calls := srv.RequestsFor("addResourcesProperties"); len(calls) != 0 {
			t.Errorf("sent %d property requests for an empty sample set, want 0", len(calls))
		}
	})
}

// TestConcurrentUse exercises the client from several goroutines so the race
// detector can inspect the token refresh path.
func TestConcurrentUse(t *testing.T) {
	srv := startMock(t, mock.Script{Resources: inventory, ExpiresAfter: []int{3}})
	client := newClient(t, srv, "")

	const workers = 8
	var wg sync.WaitGroup
	results := make([][]opsapi.Resource, workers)
	errs := make([]error, workers)
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			results[i], errs[i] = client.ListAllResources(context.Background(), opsapi.ResourceFilter{PageSize: 2})
		}(i)
	}
	wg.Wait()

	for i := 0; i < workers; i++ {
		if errs[i] != nil {
			t.Fatalf("worker %d: %v", i, errs[i])
		}
		if !reflect.DeepEqual(results[i], inventory) {
			t.Errorf("worker %d returned %+v, want the full inventory", i, results[i])
		}
	}
	if got := client.Stats().TokensAcquired; got < 2 {
		t.Errorf("Stats().TokensAcquired = %d, want at least 2 after an expiry", got)
	}
}

// TestContextCancellation checks that a cancelled context stops the walk
// instead of retrying forever.
func TestContextCancellation(t *testing.T) {
	srv := startMock(t, mock.Script{Resources: inventory})
	client := newClient(t, srv, "")

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	if _, err := client.ListAllResources(ctx, opsapi.ResourceFilter{PageSize: 2}); !errors.Is(err, context.Canceled) {
		t.Errorf("ListAllResources with a cancelled context returned %v, want a context.Canceled error", err)
	}
	if n := len(srv.Requests()); n != 0 {
		t.Errorf("a cancelled walk still made %d requests, want 0", n)
	}
}

func assertJSONHeaders(t *testing.T, r mock.Request) {
	t.Helper()
	accept := r.Header.Get("Accept")
	if accept == "" {
		t.Errorf("%s %s sent no Accept header; the API answers XML unless JSON is negotiated", r.Method, r.Path)
		return
	}
	if !strings.Contains(accept, "application/json") {
		t.Errorf("%s %s sent Accept %q, want application/json", r.Method, r.Path, accept)
	}
}

func assertKeys(t *testing.T, what string, object map[string]any, want []string) {
	t.Helper()
	var got []string
	for key := range object {
		got = append(got, key)
	}
	sort.Strings(got)
	wantSorted := sortedCopy(want)
	if !reflect.DeepEqual(got, wantSorted) {
		t.Errorf("%s has properties %v, want exactly %v (unset optional properties must be omitted, not sent empty or null)", what, got, wantSorted)
	}
}
