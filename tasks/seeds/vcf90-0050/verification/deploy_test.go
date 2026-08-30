package vcfovf_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"vcfovf"
	"vcfovf/internal/mockvc"
	"vcfovf/internal/wirecheck"
)

const (
	sessionID = "b0b1e5f4-8f6a-4a2e-9b3d-5a1c7e9d0f22"
	itemID    = "b1a2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
	poolID    = "resgroup-42"
)

var uuidRe = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

// recorder captures the backoff durations the client asks to sleep for,
// without any test ever waiting on a real clock.
type recorder struct {
	mu     sync.Mutex
	slept  []time.Duration
	called int
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func (r *recorder) sleep(d time.Duration) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.slept = append(r.slept, d)
	r.called++
}

func (r *recorder) durations() []time.Duration {
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.slept) == 0 {
		return nil
	}
	out := make([]time.Duration, len(r.slept))
	copy(out, r.slept)
	return out
}

func newClient(t *testing.T, srv *mockvc.Server, rec *recorder, opts vcfovf.Options) *vcfovf.Client {
	t.Helper()
	opts.Sleep = rec.sleep
	c, err := vcfovf.NewClient(srv.URL(), sessionID, opts)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return c
}

func newServer(t *testing.T, faults ...mockvc.Fault) *mockvc.Server {
	t.Helper()
	return mockvc.New(t, mockvc.Config{
		SessionID:     sessionID,
		LibraryItemID: itemID,
		DefaultVMName: "ovf-default-name",
		DeployFaults:  faults,
	})
}

// report fails the test with every violation the verifier found.
func report(t *testing.T, what string, violations []string) {
	t.Helper()
	if len(violations) == 0 {
		return
	}
	t.Errorf("%s is off contract:\n  - %s", what, strings.Join(violations, "\n  - "))
}

// assertOnlyContractOperations fails if the client touched anything the
// contract does not name.
func assertOnlyContractOperations(t *testing.T, srv *mockvc.Server) {
	t.Helper()
	for i, r := range srv.Log() {
		if r.Op == "" {
			t.Errorf("request %d (%s %s?%s) matched no operation named in docs/contract.json",
				i, r.Method, r.Path, r.RawQuery)
		}
	}
}

// TestContractConstantsMatchPinnedContract keeps the package's notion of the
// vSphere Automation surface tied to docs/contract.json, which is derived from
// vcenter.yaml at tag 9.0.0.0 of vmware/vcf-api-specs.
func TestContractConstantsMatchPinnedContract(t *testing.T) {
	t.Parallel()

	raw, err := os.ReadFile("docs/contract.json")
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var contract struct {
		BasePath string `json:"base_path"`
		Auth     struct {
			Header string `json:"header"`
		} `json:"auth"`
		DerivedFrom struct {
			Tag         string `json:"tag"`
			CommitSHA   string `json:"commit_sha"`
			SpecPath    string `json:"spec_path"`
			InfoVersion string `json:"info_version"`
		} `json:"derived_from"`
		Operations map[string]struct {
			Idempotency struct {
				Header string `json:"header"`
			} `json:"idempotency"`
		} `json:"operations"`
		RetryPolicy struct {
			MaxAttempts int `json:"max_attempts"`
		} `json:"retry_policy"`
	}
	if err := json.Unmarshal(raw, &contract); err != nil {
		t.Fatalf("parse contract: %v", err)
	}

	ops := make([]string, 0, len(contract.Operations))
	for id := range contract.Operations {
		ops = append(ops, id)
	}
	sort.Strings(ops)
	if want := []string{"Vcenter.Ovf.LibraryItem_deploy", "Vcenter.VM_list"}; !reflect.DeepEqual(ops, want) {
		t.Fatalf("contract names operations %v, want %v", ops, want)
	}
	if got := contract.DerivedFrom.Tag; got != "9.0.0.0" {
		t.Fatalf("contract is derived from tag %q, want 9.0.0.0", got)
	}
	if got := contract.DerivedFrom.InfoVersion; got != "9.0.0.0" {
		t.Fatalf("contract records info.version %q, want 9.0.0.0", got)
	}

	for _, tc := range []struct {
		name string
		got  string
		want string
	}{
		{"BasePath", vcfovf.BasePath, contract.BasePath},
		{"SessionHeader", vcfovf.SessionHeader, contract.Auth.Header},
		{"ClientTokenHeader", vcfovf.ClientTokenHeader, contract.Operations["Vcenter.Ovf.LibraryItem_deploy"].Idempotency.Header},
		{"DeployOperationID", vcfovf.DeployOperationID, "Vcenter.Ovf.LibraryItem_deploy"},
		{"ListVMsOperationID", vcfovf.ListVMsOperationID, "Vcenter.VM_list"},
	} {
		if tc.got != tc.want {
			t.Errorf("vcfovf.%s = %q, contract says %q", tc.name, tc.got, tc.want)
		}
	}
}

// TestNewClientBaseURL keeps the contract's /api prefix owned by the client.
// A caller supplies only an origin; accepting a path or query would silently
// move both operations away from their pinned wire paths.
func TestNewClientBaseURL(t *testing.T) {
	t.Parallel()

	const origin = "http://127.0.0.1:443"
	for _, baseURL := range []string{
		origin + "/already/api",
		origin + "?tenant=payments",
		origin + "#fragment",
	} {
		baseURL := baseURL
		t.Run(baseURL, func(t *testing.T) {
			t.Parallel()
			if _, err := vcfovf.NewClient(baseURL, sessionID, vcfovf.Options{}); err == nil {
				t.Errorf("NewClient(%q) succeeded; baseURL must be scheme and host only", baseURL)
			}
		})
	}
}

// TestDeployRequestWireShape pins the bytes on the wire. The interesting half
// of each case is what is NOT there: an optional property the caller left unset
// is omitted, never sent as an empty string, an empty map or null. The required
// accept_all_eula is sent even when it is false.
func TestDeployRequestWireShape(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name       string
		target     vcfovf.DeploymentTarget
		spec       vcfovf.DeploymentSpec
		wantTarget map[string]any
		wantSpec   map[string]any
	}{
		{
			name:       "only required properties",
			target:     vcfovf.DeploymentTarget{ResourcePoolID: poolID},
			spec:       vcfovf.DeploymentSpec{AcceptAllEULA: true},
			wantTarget: map[string]any{"resource_pool_id": poolID},
			wantSpec:   map[string]any{"accept_all_eula": true},
		},
		{
			name:       "required eula false is still sent",
			target:     vcfovf.DeploymentTarget{ResourcePoolID: poolID},
			spec:       vcfovf.DeploymentSpec{AcceptAllEULA: false},
			wantTarget: map[string]any{"resource_pool_id": poolID},
			wantSpec:   map[string]any{"accept_all_eula": false},
		},
		{
			name:   "host set, folder left unset",
			target: vcfovf.DeploymentTarget{ResourcePoolID: poolID, HostID: "host-19"},
			spec:   vcfovf.DeploymentSpec{Name: "payments-api-01", AcceptAllEULA: true},
			wantTarget: map[string]any{
				"resource_pool_id": poolID,
				"host_id":          "host-19",
			},
			wantSpec: map[string]any{
				"name":            "payments-api-01",
				"accept_all_eula": true,
			},
		},
		{
			name: "every supported property set",
			target: vcfovf.DeploymentTarget{
				ResourcePoolID: poolID,
				HostID:         "host-19",
				FolderID:       "group-v88",
			},
			spec: vcfovf.DeploymentSpec{
				Name:                "payments-api-02",
				Annotation:          "deployed by the release pipeline",
				AcceptAllEULA:       true,
				NetworkMappings:     map[string]string{"net-0": "network-77", "net-1": "network-78"},
				StorageProvisioning: "thin",
				StorageProfileID:    "storage-profile-3",
				DefaultDatastoreID:  "datastore-31",
			},
			wantTarget: map[string]any{
				"resource_pool_id": poolID,
				"host_id":          "host-19",
				"folder_id":        "group-v88",
			},
			wantSpec: map[string]any{
				"name":                 "payments-api-02",
				"annotation":           "deployed by the release pipeline",
				"accept_all_eula":      true,
				"network_mappings":     map[string]any{"net-0": "network-77", "net-1": "network-78"},
				"storage_provisioning": "thin",
				"storage_profile_id":   "storage-profile-3",
				"default_datastore_id": "datastore-31",
			},
		},
		{
			name:   "empty network map is not an empty object on the wire",
			target: vcfovf.DeploymentTarget{ResourcePoolID: poolID},
			spec: vcfovf.DeploymentSpec{
				AcceptAllEULA:   true,
				NetworkMappings: map[string]string{},
			},
			wantTarget: map[string]any{"resource_pool_id": poolID},
			wantSpec:   map[string]any{"accept_all_eula": true},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := newServer(t)
			rec := &recorder{}
			c := newClient(t, srv, rec, vcfovf.Options{})

			res, err := c.DeployLibraryItem(context.Background(), itemID, "", tc.target, tc.spec)
			if err != nil {
				t.Fatalf("DeployLibraryItem: %v", err)
			}
			if !res.Succeeded {
				t.Fatalf("DeployResult.Succeeded = false, want true")
			}
			if res.Resource.Type != "VirtualMachine" || res.Resource.ID == "" {
				t.Errorf("DeployResult.Resource = %+v, want type VirtualMachine and a non-empty id", res.Resource)
			}
			if res.Attempts != 1 {
				t.Errorf("DeployResult.Attempts = %d, want 1", res.Attempts)
			}
			if !uuidRe.MatchString(res.ClientToken) {
				t.Errorf("DeployResult.ClientToken = %q, want a generated UUID", res.ClientToken)
			}

			reqs := srv.Requests(mockvc.OpDeploy)
			if len(reqs) != 1 {
				t.Fatalf("recorded %d deploy requests, want 1", len(reqs))
			}
			report(t, "deploy request", wirecheck.DeployRequest(reqs[0], wirecheck.Deploy{
				SessionID:     sessionID,
				LibraryItemID: itemID,
				Token:         res.ClientToken,
				Target:        tc.wantTarget,
				Spec:          tc.wantSpec,
			}))
			if n := len(rec.durations()); n != 0 {
				t.Errorf("client slept %d times on a clean deploy, want 0", n)
			}
			assertOnlyContractOperations(t, srv)
		})
	}
}

// TestDeployRetryIsIdempotent is the heart of the task. A deploy that the
// client did not get an answer to is re-sent with the identical Client-Token,
// and vCenter replays the original result rather than deploying a second time.
// One logical deploy leaves exactly one virtual machine behind, whatever the
// network did on the way.
func TestDeployRetryIsIdempotent(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name         string
		faults       []mockvc.Fault
		wantAttempts int
		wantBackoff  []time.Duration
	}{
		{
			name:         "no faults",
			faults:       nil,
			wantAttempts: 1,
			wantBackoff:  nil,
		},
		{
			name:         "response lost after the deploy committed",
			faults:       []mockvc.Fault{mockvc.FaultDropAfterCommit},
			wantAttempts: 2,
			wantBackoff:  []time.Duration{100 * time.Millisecond},
		},
		{
			name:         "server busy then the response is lost",
			faults:       []mockvc.Fault{mockvc.FaultServiceUnavailable, mockvc.FaultDropAfterCommit},
			wantAttempts: 3,
			wantBackoff:  []time.Duration{100 * time.Millisecond, 200 * time.Millisecond},
		},
		{
			name: "busy twice then the response is lost",
			faults: []mockvc.Fault{
				mockvc.FaultServiceUnavailable,
				mockvc.FaultServiceUnavailable,
				mockvc.FaultDropAfterCommit,
			},
			wantAttempts: 4,
			wantBackoff:  []time.Duration{100 * time.Millisecond, 200 * time.Millisecond, 400 * time.Millisecond},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := newServer(t, tc.faults...)
			rec := &recorder{}
			c := newClient(t, srv, rec, vcfovf.Options{})

			res, err := c.DeployLibraryItem(context.Background(), itemID, "",
				vcfovf.DeploymentTarget{ResourcePoolID: poolID},
				vcfovf.DeploymentSpec{Name: "orders-api-01", AcceptAllEULA: true})
			if err != nil {
				t.Fatalf("DeployLibraryItem: %v", err)
			}
			if !res.Succeeded {
				t.Fatalf("DeployResult.Succeeded = false, want true")
			}
			if res.Attempts != tc.wantAttempts {
				t.Errorf("DeployResult.Attempts = %d, want %d", res.Attempts, tc.wantAttempts)
			}
			if got := rec.durations(); !reflect.DeepEqual(got, tc.wantBackoff) {
				t.Errorf("backoff schedule = %v, want %v", got, tc.wantBackoff)
			}

			reqs := srv.Requests(mockvc.OpDeploy)
			if len(reqs) != tc.wantAttempts {
				t.Fatalf("recorded %d deploy requests, want %d", len(reqs), tc.wantAttempts)
			}
			token, violations := wirecheck.SameToken(reqs)
			report(t, "retry token", violations)
			if token != res.ClientToken {
				t.Errorf("wire Client-Token %q != DeployResult.ClientToken %q", token, res.ClientToken)
			}
			for i, req := range reqs {
				report(t, fmt.Sprintf("deploy attempt %d", i+1), wirecheck.DeployRequest(req, wirecheck.Deploy{
					SessionID:     sessionID,
					LibraryItemID: itemID,
					Token:         token,
					Target:        map[string]any{"resource_pool_id": poolID},
					Spec:          map[string]any{"name": "orders-api-01", "accept_all_eula": true},
				}))
			}

			// The effect happened exactly once.
			if vms := srv.VMs(); len(vms) != 1 {
				t.Fatalf("mock inventory holds %d virtual machines, want exactly 1: %+v", len(vms), vms)
			}
			got, err := c.ListVMs(context.Background(), []string{"orders-api-01"})
			if err != nil {
				t.Fatalf("ListVMs: %v", err)
			}
			if len(got) != 1 {
				t.Fatalf("ListVMs returned %d summaries, want 1: %+v", len(got), got)
			}
			if got[0].VM != res.Resource.ID || got[0].Name != "orders-api-01" {
				t.Errorf("ListVMs returned %+v, want the deployed %s named orders-api-01", got[0], res.Resource.ID)
			}
			if got[0].PowerState != "POWERED_OFF" {
				t.Errorf("PowerState = %q, want POWERED_OFF", got[0].PowerState)
			}
			if got[0].CPUCount != 2 || got[0].MemorySizeMiB != 4096 {
				t.Errorf("CPUCount/MemorySizeMiB = %d/%d, want 2/4096", got[0].CPUCount, got[0].MemorySizeMiB)
			}
			assertOnlyContractOperations(t, srv)
		})
	}
}

// TestDeployDoesNotRetryFinalOutcomes: a rejected request and a completed-but-
// failed deploy are answers, not weather. Re-sending them is pointless, and in
// the succeeded=false case it would be wrong.
func TestDeployDoesNotRetryFinalOutcomes(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name          string
		fault         mockvc.Fault
		wantStatus    int
		wantErrorType string
		wantOvf       []vcfovf.OvfError
	}{
		{
			name:          "invalid argument",
			fault:         mockvc.FaultInvalidArgument,
			wantStatus:    400,
			wantErrorType: "INVALID_ARGUMENT",
		},
		{
			name:          "unauthorized",
			fault:         mockvc.FaultUnauthorized,
			wantStatus:    403,
			wantErrorType: "UNAUTHORIZED",
		},
		{
			name:          "not found",
			fault:         mockvc.FaultNotFound,
			wantStatus:    404,
			wantErrorType: "NOT_FOUND",
		},
		{
			name:          "resource inaccessible",
			fault:         mockvc.FaultResourceInaccessible,
			wantStatus:    500,
			wantErrorType: "RESOURCE_INACCESSIBLE",
		},
		{
			name:  "http 200 with succeeded false",
			fault: mockvc.FaultDeployFailed,
			wantOvf: []vcfovf.OvfError{{
				Category: "INPUT",
				Message:  "the OVF package requires a network mapping for section net-0",
			}},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := newServer(t, tc.fault)
			rec := &recorder{}
			c := newClient(t, srv, rec, vcfovf.Options{})

			_, err := c.DeployLibraryItem(context.Background(), itemID, "",
				vcfovf.DeploymentTarget{ResourcePoolID: poolID},
				vcfovf.DeploymentSpec{Name: "billing-api-01", AcceptAllEULA: true})
			if err == nil {
				t.Fatalf("DeployLibraryItem succeeded, want an error")
			}

			if tc.wantOvf != nil {
				var failed *vcfovf.DeployFailedError
				if !errors.As(err, &failed) {
					t.Fatalf("error %v (%T), want a *vcfovf.DeployFailedError", err, err)
				}
				if !reflect.DeepEqual(failed.Errors, tc.wantOvf) {
					t.Errorf("DeployFailedError.Errors = %+v, want %+v", failed.Errors, tc.wantOvf)
				}
				if !uuidRe.MatchString(failed.ClientToken) {
					t.Errorf("DeployFailedError.ClientToken = %q, want the UUID that was sent", failed.ClientToken)
				}
				if failed.LibraryItemID != itemID {
					t.Errorf("DeployFailedError.LibraryItemID = %q, want %q", failed.LibraryItemID, itemID)
				}
				reqs := srv.Requests(mockvc.OpDeploy)
				if len(reqs) == 1 && failed.ClientToken != reqs[0].Token {
					t.Errorf("DeployFailedError.ClientToken = %q, wire carried %q", failed.ClientToken, reqs[0].Token)
				}
			} else {
				var apiErr *vcfovf.APIError
				if !errors.As(err, &apiErr) {
					t.Fatalf("error %v (%T), want a *vcfovf.APIError", err, err)
				}
				if apiErr.StatusCode != tc.wantStatus {
					t.Errorf("APIError.StatusCode = %d, want %d", apiErr.StatusCode, tc.wantStatus)
				}
				if apiErr.ErrorType != tc.wantErrorType {
					t.Errorf("APIError.ErrorType = %q, want %q", apiErr.ErrorType, tc.wantErrorType)
				}
				if apiErr.Message == "" {
					t.Errorf("APIError.Message is empty; the vAPI envelope carries messages[0].default_message")
				}
				if apiErr.OperationID != vcfovf.DeployOperationID {
					t.Errorf("APIError.OperationID = %q, want %q", apiErr.OperationID, vcfovf.DeployOperationID)
				}
			}

			if n := len(srv.Requests(mockvc.OpDeploy)); n != 1 {
				t.Errorf("recorded %d deploy requests, want 1 — a final outcome is not retried", n)
			}
			if n := len(rec.durations()); n != 0 {
				t.Errorf("client slept %d times before giving up, want 0", n)
			}
			if vms := srv.VMs(); len(vms) != 0 {
				t.Errorf("mock inventory holds %d virtual machines, want 0: %+v", len(vms), vms)
			}
			assertOnlyContractOperations(t, srv)
		})
	}
}

// TestDeployRetryBudget: retries are bounded, the backoff is the pinned
// deterministic schedule, and the last failure is what the caller sees.
func TestDeployRetryBudget(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name         string
		opts         vcfovf.Options
		faults       []mockvc.Fault
		wantAttempts int
		wantBackoff  []time.Duration
		wantStatus   int
	}{
		{
			name:         "default budget of four attempts",
			opts:         vcfovf.Options{},
			faults:       repeat(mockvc.FaultServiceUnavailable, 6),
			wantAttempts: 4,
			wantBackoff:  []time.Duration{100 * time.Millisecond, 200 * time.Millisecond, 400 * time.Millisecond},
			wantStatus:   503,
		},
		{
			name:         "two attempts",
			opts:         vcfovf.Options{MaxAttempts: 2},
			faults:       repeat(mockvc.FaultServiceUnavailable, 6),
			wantAttempts: 2,
			wantBackoff:  []time.Duration{100 * time.Millisecond},
			wantStatus:   503,
		},
		{
			name:         "backoff is capped",
			opts:         vcfovf.Options{MaxAttempts: 6, BaseBackoff: time.Second, MaxBackoff: 2 * time.Second},
			faults:       repeat(mockvc.FaultServiceUnavailable, 6),
			wantAttempts: 6,
			wantBackoff:  []time.Duration{time.Second, 2 * time.Second, 2 * time.Second, 2 * time.Second, 2 * time.Second},
			wantStatus:   503,
		},
		{
			name:         "default backoff cap",
			opts:         vcfovf.Options{MaxAttempts: 7},
			faults:       repeat(mockvc.FaultServiceUnavailable, 7),
			wantAttempts: 7,
			wantBackoff: []time.Duration{
				100 * time.Millisecond,
				200 * time.Millisecond,
				400 * time.Millisecond,
				800 * time.Millisecond,
				1600 * time.Millisecond,
				2 * time.Second,
			},
			wantStatus: 503,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := newServer(t, tc.faults...)
			rec := &recorder{}
			c := newClient(t, srv, rec, tc.opts)

			_, err := c.DeployLibraryItem(context.Background(), itemID, "",
				vcfovf.DeploymentTarget{ResourcePoolID: poolID},
				vcfovf.DeploymentSpec{AcceptAllEULA: true})
			if err == nil {
				t.Fatalf("DeployLibraryItem succeeded, want an error")
			}

			var exhausted *vcfovf.RetriesExhaustedError
			if !errors.As(err, &exhausted) {
				t.Fatalf("error %v (%T), want a *vcfovf.RetriesExhaustedError", err, err)
			}
			if exhausted.Attempts != tc.wantAttempts {
				t.Errorf("RetriesExhaustedError.Attempts = %d, want %d", exhausted.Attempts, tc.wantAttempts)
			}
			if !uuidRe.MatchString(exhausted.ClientToken) {
				t.Errorf("RetriesExhaustedError.ClientToken = %q, want the UUID that was sent", exhausted.ClientToken)
			}
			var apiErr *vcfovf.APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("the exhausted error does not unwrap to a *vcfovf.APIError: %v", err)
			}
			if apiErr.StatusCode != tc.wantStatus {
				t.Errorf("last APIError.StatusCode = %d, want %d", apiErr.StatusCode, tc.wantStatus)
			}

			if got := rec.durations(); !reflect.DeepEqual(got, tc.wantBackoff) {
				t.Errorf("backoff schedule = %v, want %v", got, tc.wantBackoff)
			}
			reqs := srv.Requests(mockvc.OpDeploy)
			if len(reqs) != tc.wantAttempts {
				t.Fatalf("recorded %d deploy requests, want %d", len(reqs), tc.wantAttempts)
			}
			token, violations := wirecheck.SameToken(reqs)
			if len(violations) != 0 {
				report(t, "retry token", violations)
			}
			if token != exhausted.ClientToken {
				t.Errorf("RetriesExhaustedError.ClientToken = %q, wire carried %q", exhausted.ClientToken, token)
			}
			if vms := srv.VMs(); len(vms) != 0 {
				t.Errorf("mock inventory holds %d virtual machines, want 0: %+v", len(vms), vms)
			}
			assertOnlyContractOperations(t, srv)
		})
	}
}

// TestTransportRetryBudget covers a transport failure with no HTTP response.
// The last transport error must remain discoverable through the exhausted
// error, and all attempts must still carry the one logical deploy's token.
func TestTransportRetryBudget(t *testing.T) {
	t.Parallel()

	transportErr := errors.New("connection reset before a complete response")
	var requests []*http.Request
	httpClient := &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
		requests = append(requests, req.Clone(req.Context()))
		return nil, transportErr
	})}
	rec := &recorder{}
	c, err := vcfovf.NewClient("http://127.0.0.1:443", sessionID, vcfovf.Options{
		MaxAttempts: 2,
		Sleep:       rec.sleep,
		HTTPClient:  httpClient,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	_, err = c.DeployLibraryItem(context.Background(), itemID, "",
		vcfovf.DeploymentTarget{ResourcePoolID: poolID},
		vcfovf.DeploymentSpec{AcceptAllEULA: true})
	if err == nil {
		t.Fatal("DeployLibraryItem succeeded, want a retries-exhausted error")
	}
	var exhausted *vcfovf.RetriesExhaustedError
	if !errors.As(err, &exhausted) || exhausted.Attempts != 2 || !errors.Is(err, transportErr) {
		t.Fatalf("error = %v, want two-attempt RetriesExhaustedError unwrapping the transport failure", err)
	}
	if len(requests) != 2 {
		t.Fatalf("transport saw %d requests, want 2", len(requests))
	}
	for i, req := range requests {
		if token := req.Header.Get(vcfovf.ClientTokenHeader); token != exhausted.ClientToken {
			t.Errorf("attempt %d token = %q, exhausted error reports %q", i+1, token, exhausted.ClientToken)
		}
	}
	if got, want := rec.durations(), []time.Duration{100 * time.Millisecond}; !reflect.DeepEqual(got, want) {
		t.Errorf("backoff schedule = %v, want %v", got, want)
	}
}

// TestCallerSuppliedToken: a caller that persists its own token (so a deploy
// survives a process restart) gets that exact token on the wire, and a token
// that is not a UUID is refused before anything is sent.
func TestCallerSuppliedToken(t *testing.T) {
	t.Parallel()

	t.Run("reused verbatim across retries", func(t *testing.T) {
		t.Parallel()
		const token = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
		srv := newServer(t, mockvc.FaultServiceUnavailable, mockvc.FaultDropAfterCommit)
		rec := &recorder{}
		c := newClient(t, srv, rec, vcfovf.Options{})

		res, err := c.DeployLibraryItem(context.Background(), itemID, token,
			vcfovf.DeploymentTarget{ResourcePoolID: poolID},
			vcfovf.DeploymentSpec{AcceptAllEULA: true})
		if err != nil {
			t.Fatalf("DeployLibraryItem: %v", err)
		}
		if res.ClientToken != token {
			t.Errorf("DeployResult.ClientToken = %q, want the supplied %q", res.ClientToken, token)
		}
		reqs := srv.Requests(mockvc.OpDeploy)
		if len(reqs) != 3 {
			t.Fatalf("recorded %d deploy requests, want 3", len(reqs))
		}
		for i, req := range reqs {
			if got := req.Header.Get(vcfovf.ClientTokenHeader); got != token {
				t.Errorf("attempt %d sent %s %q, want %q", i+1, vcfovf.ClientTokenHeader, got, token)
			}
		}
		if len(srv.VMs()) != 1 {
			t.Errorf("mock inventory holds %d virtual machines, want 1", len(srv.VMs()))
		}
		assertOnlyContractOperations(t, srv)
	})

	for _, token := range []string{
		"not-a-uuid",
		"3F2504E0-4F89-41D3-9A0C-0305E82C3301",
		"3f2504e04f89-41d3-9a0c-0305e82c3301",
	} {
		token := token
		t.Run("malformed token "+token, func(t *testing.T) {
			t.Parallel()
			srv := newServer(t)
			rec := &recorder{}
			c := newClient(t, srv, rec, vcfovf.Options{})

			_, err := c.DeployLibraryItem(context.Background(), itemID, token,
				vcfovf.DeploymentTarget{ResourcePoolID: poolID},
				vcfovf.DeploymentSpec{AcceptAllEULA: true})
			if err == nil {
				t.Fatalf("DeployLibraryItem accepted a non-UUID token, want an error")
			}
			var apiErr *vcfovf.APIError
			if errors.As(err, &apiErr) {
				t.Errorf("the token was sent to the server (got %v); reject it before the request", apiErr)
			}
			if n := len(srv.Log()); n != 0 {
				t.Errorf("the mock received %d requests, want 0", n)
			}
		})
	}
}

// TestListVMsQueryEncoding pins the names filter: style form, explode true, so
// several names travel as repeated names= pairs and no filter means no
// parameter at all.
func TestListVMsQueryEncoding(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name    string
		deploy  []string
		filter  []string
		wantVMs []string
	}{
		{
			name:    "no filter lists everything",
			deploy:  []string{"web-01", "web-02", "db-01"},
			filter:  nil,
			wantVMs: []string{"web-01", "web-02", "db-01"},
		},
		{
			name:    "empty filter is not a names parameter",
			deploy:  []string{"web-01", "web-02"},
			filter:  []string{},
			wantVMs: []string{"web-01", "web-02"},
		},
		{
			name:    "one name",
			deploy:  []string{"web-01", "web-02", "db-01"},
			filter:  []string{"db-01"},
			wantVMs: []string{"db-01"},
		},
		{
			name:    "several names repeat the parameter",
			deploy:  []string{"web-01", "web-02", "db-01"},
			filter:  []string{"web-01", "db-01"},
			wantVMs: []string{"web-01", "db-01"},
		},
		{
			name:    "a name needing escaping",
			deploy:  []string{"prod web/01", "web-02"},
			filter:  []string{"prod web/01"},
			wantVMs: []string{"prod web/01"},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := newServer(t)
			rec := &recorder{}
			c := newClient(t, srv, rec, vcfovf.Options{})

			for _, name := range tc.deploy {
				if _, err := c.DeployLibraryItem(context.Background(), itemID, "",
					vcfovf.DeploymentTarget{ResourcePoolID: poolID},
					vcfovf.DeploymentSpec{Name: name, AcceptAllEULA: true}); err != nil {
					t.Fatalf("DeployLibraryItem(%q): %v", name, err)
				}
			}

			got, err := c.ListVMs(context.Background(), tc.filter)
			if err != nil {
				t.Fatalf("ListVMs: %v", err)
			}
			var names []string
			for _, s := range got {
				names = append(names, s.Name)
			}
			sort.Strings(names)
			want := append([]string(nil), tc.wantVMs...)
			sort.Strings(want)
			if !reflect.DeepEqual(names, want) {
				t.Errorf("ListVMs returned %v, want %v", names, want)
			}

			reqs := srv.Requests(mockvc.OpListVMs)
			if len(reqs) != 1 {
				t.Fatalf("recorded %d list requests, want 1", len(reqs))
			}
			report(t, "list request", wirecheck.ListVMsRequest(reqs[0], sessionID, tc.filter))
			assertOnlyContractOperations(t, srv)
		})
	}
}

// TestListVMsAPIError verifies that list failures use the same typed vAPI
// envelope as deploy failures and identify the operation that failed.
func TestListVMsAPIError(t *testing.T) {
	t.Parallel()

	srv := newServer(t)
	c, err := vcfovf.NewClient(srv.URL(), "wrong-session", vcfovf.Options{})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	_, err = c.ListVMs(context.Background(), nil)
	if err == nil {
		t.Fatal("ListVMs succeeded with an invalid session, want an error")
	}
	var apiErr *vcfovf.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("error %v (%T), want a *vcfovf.APIError", err, err)
	}
	if apiErr.OperationID != vcfovf.ListVMsOperationID || apiErr.StatusCode != 401 ||
		apiErr.ErrorType != "UNAUTHENTICATED" || apiErr.Message == "" {
		t.Errorf("APIError = %+v, want list operation, HTTP 401 UNAUTHENTICATED, and a message", apiErr)
	}
}

// TestConcurrentDeploys runs independent deploys through one client at once.
// Each logical deploy owns its own token, so eight callers leave eight virtual
// machines and the token ledger is never confused. Run under -race.
func TestConcurrentDeploys(t *testing.T) {
	t.Parallel()

	const n = 8
	srv := newServer(t)
	rec := &recorder{}
	c := newClient(t, srv, rec, vcfovf.Options{})

	var wg sync.WaitGroup
	tokens := make([]string, n)
	errs := make([]error, n)
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			res, err := c.DeployLibraryItem(context.Background(), itemID, "",
				vcfovf.DeploymentTarget{ResourcePoolID: poolID},
				vcfovf.DeploymentSpec{Name: fmt.Sprintf("worker-%02d", i), AcceptAllEULA: true})
			tokens[i], errs[i] = res.ClientToken, err
		}(i)
	}
	wg.Wait()

	seen := map[string]bool{}
	for i, err := range errs {
		if err != nil {
			t.Fatalf("deploy %d: %v", i, err)
		}
		if !uuidRe.MatchString(tokens[i]) {
			t.Errorf("deploy %d used token %q, want a UUID", i, tokens[i])
		}
		if seen[tokens[i]] {
			t.Errorf("deploy %d reused token %q; independent deploys need independent tokens", i, tokens[i])
		}
		seen[tokens[i]] = true
	}
	if vms := srv.VMs(); len(vms) != n {
		t.Fatalf("mock inventory holds %d virtual machines, want %d", len(vms), n)
	}

	got, err := c.ListVMs(context.Background(), nil)
	if err != nil {
		t.Fatalf("ListVMs: %v", err)
	}
	if len(got) != n {
		t.Fatalf("ListVMs returned %d summaries, want %d", len(got), n)
	}
	assertOnlyContractOperations(t, srv)
}

// TestContextCancellation: a cancelled context stops the client, and a
// cancellation is not a transient condition to retry through.
func TestContextCancellation(t *testing.T) {
	t.Parallel()

	srv := newServer(t, repeat(mockvc.FaultServiceUnavailable, 6)...)
	rec := &recorder{}
	c := newClient(t, srv, rec, vcfovf.Options{})

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	_, err := c.DeployLibraryItem(ctx, itemID, "",
		vcfovf.DeploymentTarget{ResourcePoolID: poolID},
		vcfovf.DeploymentSpec{AcceptAllEULA: true})
	if err == nil {
		t.Fatalf("DeployLibraryItem succeeded on a cancelled context, want an error")
	}
	if !errors.Is(err, context.Canceled) {
		t.Errorf("error %v does not wrap context.Canceled", err)
	}
	if vms := srv.VMs(); len(vms) != 0 {
		t.Errorf("mock inventory holds %d virtual machines, want 0", len(vms))
	}
}

func repeat(f mockvc.Fault, n int) []mockvc.Fault {
	out := make([]mockvc.Fault, n)
	for i := range out {
		out[i] = f
	}
	return out
}
