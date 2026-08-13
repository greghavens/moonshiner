// Wire-contract verification for the host-commissioning workflow.
//
// These tests drive the client against the loopback mock and assert the exact
// serialized request shape by reading the mock's request log. Nothing here
// contacts a VMware endpoint.
package commission_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"net/http"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	hostcommission "vcf90.local/hostcommission"
	"vcf90.local/hostcommission/commission"
	"vcf90.local/hostcommission/mockserver"
)

const (
	opPrecheckSubmit = "postHostsPrechecks_1"
	opPrecheckStatus = "getHostsPrechecksResponse"
	opCommission     = "commissionHosts"
)

// requiredOnly is a spec with every optional field left unset.
func requiredOnly(fqdn string) commission.HostSpec {
	return commission.HostSpec{
		FQDN:          fqdn,
		Username:      "root",
		Password:      "VMw@re123!",
		StorageType:   "VSAN",
		NetworkPoolID: "np-0001",
	}
}

func newClient(t *testing.T, srv *mockserver.Server) *commission.Client {
	t.Helper()
	c := commission.NewClient(srv.URL(), srv.Client())
	c.PollInterval = time.Millisecond
	c.MaxPolls = 20
	return c
}

// hostObjects decodes the host objects out of a recorded request body for the
// given operation, honouring that operation's body shape.
func hostObjects(t *testing.T, r mockserver.Recorded, shape string) []map[string]any {
	t.Helper()
	switch shape {
	case "object":
		var wrapper map[string]json.RawMessage
		if err := json.Unmarshal(r.Body, &wrapper); err != nil {
			t.Fatalf("%s body is not a JSON object: %v (body=%s)", r.Path, err, r.Body)
		}
		raw, ok := wrapper["hosts"]
		if !ok {
			t.Fatalf("%s body has no \"hosts\" property; got keys %v", r.Path, keysOfRaw(wrapper))
		}
		if len(wrapper) != 1 {
			t.Errorf("%s body must carry only \"hosts\"; got keys %v", r.Path, keysOfRaw(wrapper))
		}
		var hosts []map[string]any
		if err := json.Unmarshal(raw, &hosts); err != nil {
			t.Fatalf("%s \"hosts\" is not an array of objects: %v", r.Path, err)
		}
		return hosts
	case "array":
		var hosts []map[string]any
		if err := json.Unmarshal(r.Body, &hosts); err != nil {
			t.Fatalf("%s body is not a bare JSON array of objects: %v (body=%s)", r.Path, err, r.Body)
		}
		return hosts
	default:
		t.Fatalf("unknown body shape %q", shape)
		return nil
	}
}

func keysOfRaw(m map[string]json.RawMessage) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func keysOf(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// TestContractProvenance checks that the shipped contract is the one derived
// from the 9.0.0.0 specification revision, not the 9.1.0.0 one.
func TestContractProvenance(t *testing.T) {
	c := hostcommission.MustLoad()

	const (
		wantPath   = "specifications/sddc-manager/sddc-manager-openapi.json"
		wantTag    = "9.0.0.0"
		wantCommit = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
	)

	if got := c.Source.SpecPath; got != wantPath {
		t.Errorf("contract spec path = %q, want %q", got, wantPath)
	}
	if got := c.Source.Tag; got != wantTag {
		t.Errorf("contract tag = %q, want %q", got, wantTag)
	}
	if got := c.Source.Commit; got != wantCommit {
		t.Errorf("contract commit = %q, want %q", got, wantCommit)
	}
	if got := c.Source.InfoVersion; got != wantTag {
		t.Errorf("contract info.version = %q, want %q", got, wantTag)
	}

	// The contract must name exactly the three operations in scope, with the
	// method and path the specification gives them.
	wantOps := map[string]struct{ method, path string }{
		opPrecheckSubmit: {"POST", "/v1/hosts/prechecks"},
		opPrecheckStatus: {"GET", "/v1/hosts/prechecks/{id}"},
		opCommission:     {"POST", "/v1/hosts"},
	}
	if len(c.Operations) != len(wantOps) {
		t.Errorf("contract names %d operations, want exactly %d: %v",
			len(c.Operations), len(wantOps), c.Operations)
	}
	for id, want := range wantOps {
		op, err := c.Op(id)
		if err != nil {
			t.Errorf("contract is missing operationId %q", id)
			continue
		}
		if op.Method != want.method || op.Path != want.path {
			t.Errorf("operation %s = %s %s, want %s %s", id, op.Method, op.Path, want.method, want.path)
		}
	}

	// Body shapes: the precheck submission wraps, the commissioning call does not.
	if got := c.MustOp(opPrecheckSubmit).RequestBody.Shape; got != "object" {
		t.Errorf("%s body shape = %q, want \"object\"", opPrecheckSubmit, got)
	}
	if got := c.MustOp(opCommission).RequestBody.Shape; got != "array" {
		t.Errorf("%s body shape = %q, want \"array\"", opCommission, got)
	}

	// The storage-type set is the 9.0 one. NFS41 and FC are 9.1 additions and
	// must not appear.
	wantStorage := []string{"VSAN", "VSAN_ESA", "VSAN_REMOTE", "VSAN_MAX", "NFS", "VMFS_FC", "VVOL", "VMFS"}
	got := c.AllowedStorageTypes()
	gotSorted, wantSorted := append([]string(nil), got...), append([]string(nil), wantStorage...)
	sort.Strings(gotSorted)
	sort.Strings(wantSorted)
	if strings.Join(gotSorted, ",") != strings.Join(wantSorted, ",") {
		t.Errorf("allowed storage types = %v, want %v (the 9.0.0.0 set)", got, wantStorage)
	}
	for _, v := range []string{"NFS41", "FC"} {
		if c.StorageTypeAllowed(v) {
			t.Errorf("storage type %q is a 9.1.0.0 addition and must not be accepted at 9.0.0.0", v)
		}
	}

	// HostCommissionSpec required/optional split.
	schema := c.Schemas["HostCommissionSpec"]
	wantReq := []string{"fqdn", "networkPoolId", "password", "storageType", "username"}
	wantOpt := []string{"networkPoolName", "sshThumbprint", "sslThumbprint", "vvolStorageProtocolType"}
	if strings.Join(schema.Required, ",") != strings.Join(wantReq, ",") {
		t.Errorf("HostCommissionSpec required = %v, want %v", schema.Required, wantReq)
	}
	if strings.Join(schema.Optional, ",") != strings.Join(wantOpt, ",") {
		t.Errorf("HostCommissionSpec optional = %v, want %v", schema.Optional, wantOpt)
	}

	// official_sources.json must record the path, the tag's commit and each
	// operationId used.
	src := string(hostcommission.OfficialSourcesJSON())
	for _, want := range []string{wantPath, wantCommit, opPrecheckSubmit, opPrecheckStatus, opCommission} {
		if !strings.Contains(src, want) {
			t.Errorf("docs/official_sources.json does not record %q", want)
		}
	}
}

// TestPrecheckGatesMutation is the core behavioural check: the mutating call
// happens when and only when the precheck succeeded.
func TestPrecheckGatesMutation(t *testing.T) {
	tests := []struct {
		name            string
		cfg             mockserver.Config
		specs           []commission.HostSpec
		wantCommitted   bool
		wantPrecheckErr bool
		wantPolls       int
		wantHostErrs    int
	}{
		{
			name:          "succeeded commissions immediately",
			cfg:           mockserver.Config{PrecheckResult: "SUCCEEDED", TaskID: "task-abc"},
			specs:         []commission.HostSpec{requiredOnly("esx-01.vcf.local")},
			wantCommitted: true,
			wantPolls:     1,
		},
		{
			name:          "succeeded after several in-progress polls",
			cfg:           mockserver.Config{PrecheckResult: "SUCCEEDED", InProgressPolls: 3, TaskID: "task-def"},
			specs:         []commission.HostSpec{requiredOnly("esx-01.vcf.local"), requiredOnly("esx-02.vcf.local")},
			wantCommitted: true,
			wantPolls:     4,
		},
		{
			name:            "failed precheck changes nothing",
			cfg:             mockserver.Config{PrecheckResult: "FAILED"},
			specs:           []commission.HostSpec{requiredOnly("esx-01.vcf.local")},
			wantCommitted:   false,
			wantPrecheckErr: true,
			wantPolls:       1,
			wantHostErrs:    1,
		},
		{
			name:            "failed precheck after in-progress polls changes nothing",
			cfg:             mockserver.Config{PrecheckResult: "FAILED", InProgressPolls: 2},
			specs:           []commission.HostSpec{requiredOnly("esx-01.vcf.local"), requiredOnly("esx-02.vcf.local")},
			wantCommitted:   false,
			wantPrecheckErr: true,
			wantPolls:       3,
			wantHostErrs:    2,
		},
		{
			name: "one bad host fails the whole precheck and commissions nothing",
			cfg: mockserver.Config{
				PrecheckResult: "FAILED",
				HostOutcomes: []mockserver.HostPrecheckOutcome{
					{FQDN: "esx-01.vcf.local", Result: "SUCCEEDED"},
					{FQDN: "esx-02.vcf.local", Result: "FAILED", Error: "insufficient memory"},
				},
			},
			specs:           []commission.HostSpec{requiredOnly("esx-01.vcf.local"), requiredOnly("esx-02.vcf.local")},
			wantCommitted:   false,
			wantPrecheckErr: true,
			wantPolls:       1,
			wantHostErrs:    1, // only the host that did not succeed
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv := mockserver.New(tc.cfg)
			defer srv.Close()

			res, err := newClient(t, srv).CommissionHosts(context.Background(), tc.specs)

			if tc.wantPrecheckErr {
				if !errors.Is(err, commission.ErrPrecheckFailed) {
					t.Fatalf("error = %v, want one wrapping ErrPrecheckFailed", err)
				}
			} else if err != nil {
				t.Fatalf("CommissionHosts: %v", err)
			}
			if res == nil {
				t.Fatal("Result is nil; it must be returned even when the precheck fails")
			}

			// The gate: the mutating request is present exactly when expected.
			gotCommission := srv.Count(opCommission)
			if tc.wantCommitted && gotCommission != 1 {
				t.Errorf("%s issued %d times, want exactly 1", opCommission, gotCommission)
			}
			if !tc.wantCommitted && gotCommission != 0 {
				t.Errorf("precheck failed but %s was issued %d times; nothing must be changed",
					opCommission, gotCommission)
			}
			if res.Committed != tc.wantCommitted {
				t.Errorf("Result.Committed = %v, want %v", res.Committed, tc.wantCommitted)
			}

			// Ordering: the precheck is submitted first, and any mutating call
			// comes after a completed status read.
			log := srv.Requests()
			if len(log) == 0 || log[0].Matched != opPrecheckSubmit {
				t.Fatalf("first request = %+v, want %s", firstOf(log), opPrecheckSubmit)
			}
			for i, r := range log {
				if r.Matched == opCommission && i == 0 {
					t.Error("commissioning was issued before any precheck")
				}
			}
			if tc.wantCommitted && log[len(log)-1].Matched != opCommission {
				t.Errorf("last request = %s, want %s", log[len(log)-1].Matched, opCommission)
			}

			if srv.Count(opPrecheckSubmit) != 1 {
				t.Errorf("%s issued %d times, want exactly 1", opPrecheckSubmit, srv.Count(opPrecheckSubmit))
			}
			if got := srv.Count(opPrecheckStatus); got != tc.wantPolls {
				t.Errorf("%s issued %d times, want %d", opPrecheckStatus, got, tc.wantPolls)
			}
			if res.Polls != tc.wantPolls {
				t.Errorf("Result.Polls = %d, want %d", res.Polls, tc.wantPolls)
			}

			// Only contract operations may be called.
			if un := srv.Unmatched(); len(un) != 0 {
				t.Errorf("requests hit endpoints the contract does not name: %+v", un)
			}
			for _, r := range log {
				if r.Status >= 400 {
					t.Errorf("%s %s was rejected by the contract-pinned mock with %d: %s",
						r.Method, r.Path, r.Status, r.Body)
				}
			}

			if tc.wantCommitted {
				wantTask := tc.cfg.TaskID
				if res.TaskID != wantTask {
					t.Errorf("Result.TaskID = %q, want %q", res.TaskID, wantTask)
				}
			} else if res.TaskID != "" {
				t.Errorf("Result.TaskID = %q, want empty when nothing was commissioned", res.TaskID)
			}
			if got := len(res.HostErrors); got != tc.wantHostErrs {
				t.Errorf("len(Result.HostErrors) = %d, want %d", got, tc.wantHostErrs)
			}
		})
	}
}

func firstOf(log []mockserver.Recorded) any {
	if len(log) == 0 {
		return "no requests at all"
	}
	return log[0]
}

// TestRequestWireShape asserts the exact serialized shape of both request
// bodies, including that unset optional properties are omitted rather than sent
// as empty strings.
func TestRequestWireShape(t *testing.T) {
	allOptional := commission.HostSpec{
		FQDN:                    "esx-09.vcf.local",
		Username:                "root",
		Password:                "VMw@re123!",
		StorageType:             "VVOL",
		NetworkPoolID:           "np-0009",
		VvolStorageProtocolType: "FC",
		NetworkPoolName:         "pool-nine",
		SSHThumbprint:           "SHA256:ssh-nine",
		SSLThumbprint:           "SHA256:ssl-nine",
	}

	tests := []struct {
		name     string
		spec     commission.HostSpec
		wantKeys []string
		wantVals map[string]string
	}{
		{
			name: "only required fields are serialized when optionals are unset",
			spec: requiredOnly("esx-01.vcf.local"),
			wantKeys: []string{
				"fqdn", "networkPoolId", "password", "storageType", "username",
			},
			wantVals: map[string]string{
				"fqdn":          "esx-01.vcf.local",
				"username":      "root",
				"password":      "VMw@re123!",
				"storageType":   "VSAN",
				"networkPoolId": "np-0001",
			},
		},
		{
			name: "one optional field set is the only optional serialized",
			spec: func() commission.HostSpec {
				s := requiredOnly("esx-02.vcf.local")
				s.SSLThumbprint = "SHA256:abc"
				return s
			}(),
			wantKeys: []string{
				"fqdn", "networkPoolId", "password", "sslThumbprint", "storageType", "username",
			},
			wantVals: map[string]string{"sslThumbprint": "SHA256:abc"},
		},
		{
			name: "network pool name alongside id",
			spec: func() commission.HostSpec {
				s := requiredOnly("esx-03.vcf.local")
				s.NetworkPoolName = "pool-a"
				return s
			}(),
			wantKeys: []string{
				"fqdn", "networkPoolId", "networkPoolName", "password", "storageType", "username",
			},
			wantVals: map[string]string{"networkPoolName": "pool-a"},
		},
		{
			name: "every optional field set",
			spec: allOptional,
			wantKeys: []string{
				"fqdn", "networkPoolId", "networkPoolName", "password", "sshThumbprint",
				"sslThumbprint", "storageType", "username", "vvolStorageProtocolType",
			},
			wantVals: map[string]string{
				"vvolStorageProtocolType": "FC",
				"networkPoolName":         "pool-nine",
				"sshThumbprint":           "SHA256:ssh-nine",
				"sslThumbprint":           "SHA256:ssl-nine",
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv := mockserver.New(mockserver.Config{PrecheckResult: "SUCCEEDED"})
			defer srv.Close()

			if _, err := newClient(t, srv).CommissionHosts(context.Background(), []commission.HostSpec{tc.spec}); err != nil {
				t.Fatalf("CommissionHosts: %v", err)
			}

			// Both request bodies carry the same host object, but in different
			// envelopes: an object for the precheck, a bare array for the
			// commissioning call.
			for _, step := range []struct {
				op    string
				shape string
			}{
				{opPrecheckSubmit, "object"},
				{opCommission, "array"},
			} {
				reqs := srv.RequestsFor(step.op)
				if len(reqs) != 1 {
					t.Fatalf("%s issued %d times, want 1", step.op, len(reqs))
				}
				r := reqs[0]

				if ct := r.ContentType; !strings.HasPrefix(ct, "application/json") {
					t.Errorf("%s Content-Type = %q, want application/json", step.op, ct)
				}

				// Envelope check, straight off the wire.
				trimmed := strings.TrimSpace(string(r.Body))
				switch step.shape {
				case "object":
					if !strings.HasPrefix(trimmed, "{") {
						t.Errorf("%s body must be a JSON object with a \"hosts\" array, got: %s", step.op, trimmed)
					}
				case "array":
					if !strings.HasPrefix(trimmed, "[") {
						t.Errorf("%s body must be a bare JSON array, not wrapped in an object, got: %s", step.op, trimmed)
					}
				}

				hosts := hostObjects(t, r, step.shape)
				if len(hosts) != 1 {
					t.Fatalf("%s carried %d hosts, want 1", step.op, len(hosts))
				}
				h := hosts[0]

				if got := keysOf(h); strings.Join(got, ",") != strings.Join(tc.wantKeys, ",") {
					t.Errorf("%s host keys = %v, want exactly %v", step.op, got, tc.wantKeys)
				}

				// An unset optional must be absent, not present-and-empty.
				for _, k := range []string{"vvolStorageProtocolType", "networkPoolName", "sshThumbprint", "sslThumbprint"} {
					v, present := h[k]
					if !present {
						continue
					}
					if s, ok := v.(string); ok && s == "" {
						t.Errorf("%s serialized unset optional %q as an empty string; it must be omitted", step.op, k)
					}
				}
				for k, v := range h {
					if s, ok := v.(string); ok && s == "" {
						t.Errorf("%s serialized %q as an empty string; empty properties must be omitted", step.op, k)
					}
				}

				for k, want := range tc.wantVals {
					if got, _ := h[k].(string); got != want {
						t.Errorf("%s host[%q] = %q, want %q", step.op, k, got, want)
					}
				}
			}
		})
	}
}

// TestPrecheckAndCommissionCarryIdenticalHosts checks the two calls describe
// the same hosts, in the same order.
func TestPrecheckAndCommissionCarryIdenticalHosts(t *testing.T) {
	srv := mockserver.New(mockserver.Config{PrecheckResult: "SUCCEEDED"})
	defer srv.Close()

	specs := []commission.HostSpec{
		requiredOnly("esx-01.vcf.local"),
		func() commission.HostSpec {
			s := requiredOnly("esx-02.vcf.local")
			s.StorageType = "NFS"
			s.SSHThumbprint = "SHA256:two"
			return s
		}(),
		requiredOnly("esx-03.vcf.local"),
	}

	if _, err := newClient(t, srv).CommissionHosts(context.Background(), specs); err != nil {
		t.Fatalf("CommissionHosts: %v", err)
	}

	pre := hostObjects(t, srv.RequestsFor(opPrecheckSubmit)[0], "object")
	post := hostObjects(t, srv.RequestsFor(opCommission)[0], "array")

	if len(pre) != len(specs) || len(post) != len(specs) {
		t.Fatalf("precheck carried %d hosts and commissioning carried %d, want %d each",
			len(pre), len(post), len(specs))
	}
	for i := range specs {
		a, _ := json.Marshal(pre[i])
		b, _ := json.Marshal(post[i])
		if string(a) != string(b) {
			t.Errorf("host %d differs between the two calls:\n precheck:     %s\n commission:   %s", i, a, b)
		}
		if got, _ := pre[i]["fqdn"].(string); got != specs[i].FQDN {
			t.Errorf("host %d fqdn = %q, want %q (order must be preserved)", i, got, specs[i].FQDN)
		}
	}
}

// TestStorageTypeFromNewerReleaseIsRejected pins the contract to 9.0: a storage
// type introduced by the 9.1 revision must be refused before anything is sent.
func TestStorageTypeFromNewerReleaseIsRejected(t *testing.T) {
	for _, st := range []string{"NFS41", "FC", "NOT_A_TYPE", ""} {
		t.Run("storageType="+st, func(t *testing.T) {
			srv := mockserver.New(mockserver.Config{PrecheckResult: "SUCCEEDED"})
			defer srv.Close()

			spec := requiredOnly("esx-01.vcf.local")
			spec.StorageType = st

			res, err := newClient(t, srv).CommissionHosts(context.Background(), []commission.HostSpec{spec})
			if !errors.Is(err, commission.ErrUnsupportedStorageType) {
				t.Fatalf("error = %v, want one wrapping ErrUnsupportedStorageType", err)
			}
			if res != nil && res.Committed {
				t.Error("Result.Committed is true for a rejected storage type")
			}
			if n := len(srv.Requests()); n != 0 {
				t.Errorf("%d request(s) were sent for a storage type the 9.0 contract rejects; want 0: %+v",
					n, srv.Requests())
			}
		})
	}
}

// TestAcceptedStorageTypes checks every value the 9.0 specification allows is
// accepted and reaches the wire unchanged.
func TestAcceptedStorageTypes(t *testing.T) {
	for _, st := range hostcommission.MustLoad().AllowedStorageTypes() {
		t.Run("storageType="+st, func(t *testing.T) {
			srv := mockserver.New(mockserver.Config{PrecheckResult: "SUCCEEDED"})
			defer srv.Close()

			spec := requiredOnly("esx-01.vcf.local")
			spec.StorageType = st

			if _, err := newClient(t, srv).CommissionHosts(context.Background(), []commission.HostSpec{spec}); err != nil {
				t.Fatalf("CommissionHosts with storageType %q: %v", st, err)
			}
			hosts := hostObjects(t, srv.RequestsFor(opCommission)[0], "array")
			if got, _ := hosts[0]["storageType"].(string); got != st {
				t.Errorf("storageType on the wire = %q, want %q", got, st)
			}
		})
	}
}

// TestPrecheckStatusPathUsesReturnedID checks the status reads address the id
// the submission returned.
func TestPrecheckStatusPathUsesReturnedID(t *testing.T) {
	srv := mockserver.New(mockserver.Config{
		PrecheckID:      "precheck-xyz-42",
		PrecheckResult:  "SUCCEEDED",
		InProgressPolls: 2,
	})
	defer srv.Close()

	res, err := newClient(t, srv).CommissionHosts(context.Background(),
		[]commission.HostSpec{requiredOnly("esx-01.vcf.local")})
	if err != nil {
		t.Fatalf("CommissionHosts: %v", err)
	}
	if res.PrecheckID != "precheck-xyz-42" {
		t.Errorf("Result.PrecheckID = %q, want %q", res.PrecheckID, "precheck-xyz-42")
	}
	if res.PrecheckResult != "SUCCEEDED" {
		t.Errorf("Result.PrecheckResult = %q, want SUCCEEDED", res.PrecheckResult)
	}

	polls := srv.RequestsFor(opPrecheckStatus)
	if len(polls) != 3 {
		t.Fatalf("status reads = %d, want 3", len(polls))
	}
	for _, r := range polls {
		if r.Path != "/v1/hosts/prechecks/precheck-xyz-42" {
			t.Errorf("status read path = %q, want /v1/hosts/prechecks/precheck-xyz-42", r.Path)
		}
		if len(strings.TrimSpace(string(r.Body))) != 0 {
			t.Errorf("status read carried a body %q, want none", r.Body)
		}
	}
}

// TestContextCancellationStopsBeforeMutation checks a cancelled context aborts
// the workflow without commissioning anything.
func TestContextCancellationStopsBeforeMutation(t *testing.T) {
	srv := mockserver.New(mockserver.Config{PrecheckResult: "SUCCEEDED", InProgressPolls: 1000})
	defer srv.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Cancel immediately after the first IN_PROGRESS status response. A very
	// long poll interval makes this discriminate context-aware waiting without
	// relying on a wall-clock timeout.
	base := srv.Client().Transport
	httpc := &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
		resp, err := base.RoundTrip(req)
		if err == nil && req.Method == http.MethodGet && strings.HasPrefix(req.URL.Path, "/v1/hosts/prechecks/") {
			raw, readErr := io.ReadAll(resp.Body)
			_ = resp.Body.Close()
			if readErr != nil {
				return nil, readErr
			}
			resp.Body = io.NopCloser(bytes.NewReader(raw))
			cancel()
		}
		return resp, err
	})}
	c := commission.NewClient(srv.URL(), httpc)
	c.PollInterval = time.Hour
	c.MaxPolls = 20

	res, err := c.CommissionHosts(ctx, []commission.HostSpec{requiredOnly("esx-01.vcf.local")})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want one wrapping context.Canceled", err)
	}
	if res != nil && res.Committed {
		t.Error("Result.Committed is true after cancellation")
	}
	if n := srv.Count(opCommission); n != 0 {
		t.Errorf("%s was issued %d times after cancellation, want 0", opCommission, n)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

// TestFailedPrecheckReturnsHostDetails verifies that HostErrors contains the
// actual non-passing hosts, not merely the right number of placeholder rows.
func TestFailedPrecheckReturnsHostDetails(t *testing.T) {
	srv := mockserver.New(mockserver.Config{
		PrecheckResult: "FAILED",
		HostOutcomes: []mockserver.HostPrecheckOutcome{
			{FQDN: "esx-01.vcf.local", Result: "FAILED", Error: "DNS lookup failed"},
			{FQDN: "esx-02.vcf.local", Result: "SUCCEEDED"},
			{FQDN: "esx-03.vcf.local", Result: "FAILED", Error: "thumbprint mismatch"},
		},
	})
	defer srv.Close()

	res, err := newClient(t, srv).CommissionHosts(context.Background(), []commission.HostSpec{
		requiredOnly("esx-01.vcf.local"),
		requiredOnly("esx-02.vcf.local"),
		requiredOnly("esx-03.vcf.local"),
	})
	if !errors.Is(err, commission.ErrPrecheckFailed) {
		t.Fatalf("error = %v, want one wrapping ErrPrecheckFailed", err)
	}
	want := []commission.HostPrecheckError{
		{FQDN: "esx-01.vcf.local", Result: "FAILED", Error: "DNS lookup failed"},
		{FQDN: "esx-03.vcf.local", Result: "FAILED", Error: "thumbprint mismatch"},
	}
	if !reflect.DeepEqual(res.HostErrors, want) {
		t.Errorf("Result.HostErrors = %#v, want %#v", res.HostErrors, want)
	}
	if n := srv.Count(opCommission); n != 0 {
		t.Errorf("%s was issued %d times after failed host checks, want 0", opCommission, n)
	}
}

// TestOperationsRequireTheirContractStatuses rejects alternate 2xx codes: the
// contract names exact success statuses rather than a generic success class.
func TestOperationsRequireTheirContractStatuses(t *testing.T) {
	tests := []struct {
		name            string
		cfg             mockserver.Config
		wantSubmits     int
		wantStatusGets  int
		wantCommissions int
	}{
		{
			name:        "precheck submission requires 200",
			cfg:         mockserver.Config{PrecheckResult: "SUCCEEDED", PrecheckSubmitStatus: http.StatusCreated},
			wantSubmits: 1,
		},
		{
			name:           "precheck status requires 200",
			cfg:            mockserver.Config{PrecheckResult: "SUCCEEDED", PrecheckStatusStatus: http.StatusAccepted},
			wantSubmits:    1,
			wantStatusGets: 1,
		},
		{
			name:            "commission requires 202",
			cfg:             mockserver.Config{PrecheckResult: "SUCCEEDED", CommissionStatus: http.StatusOK},
			wantSubmits:     1,
			wantStatusGets:  1,
			wantCommissions: 1,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv := mockserver.New(tc.cfg)
			defer srv.Close()

			_, err := newClient(t, srv).CommissionHosts(context.Background(),
				[]commission.HostSpec{requiredOnly("esx-01.vcf.local")})
			if err == nil {
				t.Fatal("CommissionHosts accepted a status other than the operation's contract success status")
			}
			if got := srv.Count(opPrecheckSubmit); got != tc.wantSubmits {
				t.Errorf("%s issued %d times, want %d", opPrecheckSubmit, got, tc.wantSubmits)
			}
			if got := srv.Count(opPrecheckStatus); got != tc.wantStatusGets {
				t.Errorf("%s issued %d times, want %d", opPrecheckStatus, got, tc.wantStatusGets)
			}
			if got := srv.Count(opCommission); got != tc.wantCommissions {
				t.Errorf("%s issued %d times, want %d", opCommission, got, tc.wantCommissions)
			}
		})
	}
}

// TestImplementationDoesNotDuplicateTheContract enforces the stated single
// source of truth without dictating how the client structures its helpers.
func TestImplementationDoesNotDuplicateTheContract(t *testing.T) {
	f, err := parser.ParseFile(token.NewFileSet(), "commission.go", nil, 0)
	if err != nil {
		t.Fatalf("parse commission.go: %v", err)
	}

	forbiddenStrings := map[string]bool{
		"GET": true, "POST": true,
		"/v1/hosts/prechecks": true, "/v1/hosts/prechecks/{id}": true, "/v1/hosts": true,
		"VSAN": true, "VSAN_ESA": true, "VSAN_REMOTE": true, "VSAN_MAX": true,
		"NFS": true, "VMFS_FC": true, "VVOL": true, "VMFS": true,
	}
	ast.Inspect(f, func(n ast.Node) bool {
		switch n := n.(type) {
		case *ast.BasicLit:
			if n.Kind == token.STRING {
				if value, err := strconv.Unquote(n.Value); err == nil && forbiddenStrings[value] {
					t.Errorf("commission.go duplicates contract wire value %q", value)
				}
			}
			if n.Kind == token.INT && (n.Value == "200" || n.Value == "202") {
				t.Errorf("commission.go duplicates contract success status %s", n.Value)
			}
		case *ast.SelectorExpr:
			pkg, ok := n.X.(*ast.Ident)
			if ok && pkg.Name == "http" {
				switch n.Sel.Name {
				case "MethodGet", "MethodPost", "StatusOK", "StatusAccepted":
					t.Errorf("commission.go duplicates contract wire value http.%s", n.Sel.Name)
				}
			}
		}
		return true
	})
}
