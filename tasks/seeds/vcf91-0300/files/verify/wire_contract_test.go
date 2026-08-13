// Package verify holds the protected contract verification for the onboarding
// client. It drives the client against the loopback mock in internal/mockni and
// asserts the request wire shape against docs/contract.json.
//
// Nothing here contacts a live VMware endpoint. The only server involved is the
// 127.0.0.1 mock.
//
// This file is part of the protected verification harness. Do not edit it.
package verify

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"testing"

	"vcf9.local/opsnet/internal/mockni"
	"vcf9.local/opsnet/onboarding"
)

const token = "0f4c1a7e-3b62-4d9a-9c31-6f2e5a8b0d47"

func ptr(b bool) *bool { return &b }

// baseRequest is a well-formed onboarding request with every optional field
// left unset. Tests copy it and set one thing at a time.
func baseRequest() onboarding.VCenterOnboardRequest {
	return onboarding.VCenterOnboardRequest{
		IP:       "10.197.17.68",
		ProxyID:  "18230:901:1585583463",
		Nickname: "My vCenter",
		Credentials: onboarding.Credentials{
			Username: "administrator@vsphere.local",
			Password: "VMware1!",
		},
	}
}

func newClient(t *testing.T, srv *mockni.Server) *onboarding.Client {
	t.Helper()
	c, err := onboarding.New(srv.URL(), token, nil)
	if err != nil {
		t.Fatalf("onboarding.New: %v", err)
	}
	return c
}

// keysOf returns the sorted top-level member names of a JSON object.
func keysOf(t *testing.T, obj map[string]json.RawMessage) []string {
	t.Helper()
	out := make([]string, 0, len(obj))
	for k := range obj {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// assertExactKeys is the core wire-shape assertion. It fails on a missing key
// and equally on an extra key, which is how "unset optional fields are omitted
// rather than sent empty" gets enforced: a field serialized as "" or false or
// null still shows up as a key here.
func assertExactKeys(t *testing.T, what string, obj map[string]json.RawMessage, want ...string) {
	t.Helper()
	sort.Strings(want)
	got := keysOf(t, obj)
	if strings.Join(got, ",") == strings.Join(want, ",") {
		return
	}
	wantSet := map[string]bool{}
	for _, k := range want {
		wantSet[k] = true
	}
	gotSet := map[string]bool{}
	for _, k := range got {
		gotSet[k] = true
	}
	var missing, extra []string
	for _, k := range want {
		if !gotSet[k] {
			missing = append(missing, k)
		}
	}
	for _, k := range got {
		if !wantSet[k] {
			extra = append(extra, fmt.Sprintf("%s=%s", k, obj[k]))
		}
	}
	t.Errorf("%s: wrong JSON members\n  got:     %v\n  want:    %v\n  missing: %v\n  extra:   %v\n"+
		"(an optional field the caller did not set must be omitted, not sent as \"\"/0/false/null)",
		what, got, want, missing, extra)
}

func assertMember(t *testing.T, what string, obj map[string]json.RawMessage, key, wantJSON string) {
	t.Helper()
	raw, ok := obj[key]
	if !ok {
		t.Errorf("%s: member %q is absent, want %s", what, key, wantJSON)
		return
	}
	if strings.TrimSpace(string(raw)) != wantJSON {
		t.Errorf("%s: member %q = %s, want %s", what, key, raw, wantJSON)
	}
}

func decodeOnly(t *testing.T, what string, reqs []mockni.LoggedRequest) map[string]json.RawMessage {
	t.Helper()
	if len(reqs) != 1 {
		t.Fatalf("%s: expected exactly 1 request, got %d", what, len(reqs))
	}
	obj, err := reqs[0].JSONBody()
	if err != nil {
		t.Fatalf("%s: %v", what, err)
	}
	return obj
}

// TestPrecheckGatesMutation is the central behavioural requirement: the
// mutating call must not happen unless the precheck passed.
func TestPrecheckGatesMutation(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name        string
		outcome     mockni.ValidateOutcome
		wantOps     []string
		wantCreated int
		wantCode    int
	}{
		{
			name:        "precheck passes then the data source is created",
			outcome:     mockni.ValidationSucceeds(),
			wantOps:     []string{mockni.OpValidateVCenter, mockni.OpAddVcenterDatasource},
			wantCreated: 1,
		},
		{
			name:        "precheck fails in the body with HTTP 200",
			outcome:     mockni.ValidationFailsInBody(500, "Unable to connect to vCenter: connection refused."),
			wantOps:     []string{mockni.OpValidateVCenter},
			wantCreated: 0,
			wantCode:    500,
		},
		{
			name:        "precheck fails in the body with a credential verdict",
			outcome:     mockni.ValidationFailsInBody(400, "Cannot complete login due to an incorrect user name or password."),
			wantOps:     []string{mockni.OpValidateVCenter},
			wantCreated: 0,
			wantCode:    400,
		},
		{
			name:        "precheck fails with HTTP 400",
			outcome:     mockni.ValidationFailsWithStatus(400, "You must provide one of IP or FQDN."),
			wantOps:     []string{mockni.OpValidateVCenter},
			wantCreated: 0,
			wantCode:    400,
		},
		{
			name:        "precheck fails with HTTP 403",
			outcome:     mockni.ValidationFailsWithStatus(403, "Insufficient privileges on the collector."),
			wantOps:     []string{mockni.OpValidateVCenter},
			wantCreated: 0,
			wantCode:    403,
		},
		{
			name:        "precheck fails with HTTP 500",
			outcome:     mockni.ValidationFailsWithStatus(500, "Internal error."),
			wantOps:     []string{mockni.OpValidateVCenter},
			wantCreated: 0,
			wantCode:    500,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := mockni.Start(t, mockni.Options{Token: token, Validate: tc.outcome})
			res, err := newClient(t, srv).OnboardVCenter(context.Background(), baseRequest())

			if tc.wantCreated == 0 {
				var pe *onboarding.PrecheckError
				if !errors.As(err, &pe) {
					t.Fatalf("OnboardVCenter err = %v (%T), want *onboarding.PrecheckError", err, err)
				}
				if pe.Code != tc.wantCode {
					t.Errorf("PrecheckError.Code = %d, want %d", pe.Code, tc.wantCode)
				}
				if pe.Message != tc.outcome.Message {
					t.Errorf("PrecheckError.Message = %q, want the API message %q",
						pe.Message, tc.outcome.Message)
				}
				if res != nil {
					t.Errorf("OnboardVCenter result = %+v, want nil when the precheck fails", res)
				}
			} else {
				if err != nil {
					t.Fatalf("OnboardVCenter: unexpected error: %v", err)
				}
				if res == nil {
					t.Fatal("OnboardVCenter returned a nil result and a nil error")
				}
				if res.EntityID == "" {
					t.Error("OnboardResult.EntityID is empty, want the entity_id from the 201 body")
				}
				if res.EntityType != "VCenterDataSource" {
					t.Errorf("OnboardResult.EntityType = %q, want %q", res.EntityType, "VCenterDataSource")
				}
			}

			if got := srv.OperationOrder(); strings.Join(got, ",") != strings.Join(tc.wantOps, ",") {
				t.Errorf("operations called = %v, want %v", got, tc.wantOps)
			}
			if got := len(srv.Created()); got != tc.wantCreated {
				t.Errorf("data sources created = %d, want %d "+
					"(a failed precheck must leave the appliance unchanged)", got, tc.wantCreated)
			}
		})
	}
}

// TestValidateRequestWireShape pins the body of validateVCenter.
func TestValidateRequestWireShape(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		mutate   func(*onboarding.VCenterOnboardRequest)
		wantKeys []string
		members  map[string]string
	}{
		{
			name:     "IP target, no optional fields",
			mutate:   func(*onboarding.VCenterOnboardRequest) {},
			wantKeys: []string{"ip", "proxy_id", "credentials"},
			members: map[string]string{
				"ip":       `"10.197.17.68"`,
				"proxy_id": `"18230:901:1585583463"`,
			},
		},
		{
			name: "FQDN target omits ip",
			mutate: func(r *onboarding.VCenterOnboardRequest) {
				r.IP = ""
				r.FQDN = "vc01.corp.example.com"
			},
			wantKeys: []string{"fqdn", "proxy_id", "credentials"},
			members:  map[string]string{"fqdn": `"vc01.corp.example.com"`},
		},
		{
			name: "IPFIX requested adds ipfix_enabled",
			mutate: func(r *onboarding.VCenterOnboardRequest) {
				r.IPFIXEnabled = true
			},
			wantKeys: []string{"ip", "proxy_id", "credentials", "ipfix_enabled"},
			members:  map[string]string{"ipfix_enabled": "true"},
		},
		{
			name: "add-side optional fields never reach the precheck",
			mutate: func(r *onboarding.VCenterOnboardRequest) {
				r.Notes = "Located in DC1"
				r.Enabled = ptr(false)
			},
			wantKeys: []string{"ip", "proxy_id", "credentials"},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := mockni.Start(t, mockni.Options{Token: token})
			req := baseRequest()
			tc.mutate(&req)

			if _, err := newClient(t, srv).OnboardVCenter(context.Background(), req); err != nil {
				t.Fatalf("OnboardVCenter: %v", err)
			}

			obj := decodeOnly(t, "validateVCenter body", srv.RequestsFor(mockni.OpValidateVCenter))
			assertExactKeys(t, "validateVCenter body", obj, tc.wantKeys...)
			for k, v := range tc.members {
				assertMember(t, "validateVCenter body", obj, k, v)
			}
		})
	}
}

// TestAddRequestWireShape pins the body of addVcenterDatasource.
func TestAddRequestWireShape(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		mutate   func(*onboarding.VCenterOnboardRequest)
		wantKeys []string
		members  map[string]string
	}{
		{
			name:     "IP target, no optional fields",
			mutate:   func(*onboarding.VCenterOnboardRequest) {},
			wantKeys: []string{"ip", "proxy_id", "nickname", "credentials"},
			members: map[string]string{
				"ip":       `"10.197.17.68"`,
				"proxy_id": `"18230:901:1585583463"`,
				"nickname": `"My vCenter"`,
			},
		},
		{
			name: "FQDN target omits ip",
			mutate: func(r *onboarding.VCenterOnboardRequest) {
				r.IP = ""
				r.FQDN = "vc01.corp.example.com"
			},
			wantKeys: []string{"fqdn", "proxy_id", "nickname", "credentials"},
			members:  map[string]string{"fqdn": `"vc01.corp.example.com"`},
		},
		{
			name: "notes set",
			mutate: func(r *onboarding.VCenterOnboardRequest) {
				r.Notes = "Located in DC1"
			},
			wantKeys: []string{"ip", "proxy_id", "nickname", "notes", "credentials"},
			members:  map[string]string{"notes": `"Located in DC1"`},
		},
		{
			name: "enabled explicitly false is sent",
			mutate: func(r *onboarding.VCenterOnboardRequest) {
				r.Enabled = ptr(false)
			},
			wantKeys: []string{"ip", "proxy_id", "nickname", "enabled", "credentials"},
			members:  map[string]string{"enabled": "false"},
		},
		{
			name: "enabled explicitly true is sent",
			mutate: func(r *onboarding.VCenterOnboardRequest) {
				r.Enabled = ptr(true)
			},
			wantKeys: []string{"ip", "proxy_id", "nickname", "enabled", "credentials"},
			members:  map[string]string{"enabled": "true"},
		},
		{
			name: "precheck-only IPFIX flag never reaches the mutating call",
			mutate: func(r *onboarding.VCenterOnboardRequest) {
				r.IPFIXEnabled = true
			},
			wantKeys: []string{"ip", "proxy_id", "nickname", "credentials"},
		},
		{
			name: "every optional field set",
			mutate: func(r *onboarding.VCenterOnboardRequest) {
				r.Notes = "Located in DC1"
				r.Enabled = ptr(true)
				r.IPFIXEnabled = true
			},
			wantKeys: []string{"ip", "proxy_id", "nickname", "notes", "enabled", "credentials"},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := mockni.Start(t, mockni.Options{Token: token})
			req := baseRequest()
			tc.mutate(&req)

			if _, err := newClient(t, srv).OnboardVCenter(context.Background(), req); err != nil {
				t.Fatalf("OnboardVCenter: %v", err)
			}

			obj := decodeOnly(t, "addVcenterDatasource body", srv.RequestsFor(mockni.OpAddVcenterDatasource))
			assertExactKeys(t, "addVcenterDatasource body", obj, tc.wantKeys...)
			for k, v := range tc.members {
				assertMember(t, "addVcenterDatasource body", obj, k, v)
			}
		})
	}
}

// TestCredentialsWireShape pins the nested PasswordCredentials object on both
// operations. username is required by the spec; password is not, so an unset
// password is omitted rather than sent as "".
func TestCredentialsWireShape(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		creds    onboarding.Credentials
		wantKeys []string
	}{
		{
			name:     "username and password",
			creds:    onboarding.Credentials{Username: "administrator@vsphere.local", Password: "VMware1!"},
			wantKeys: []string{"username", "password"},
		},
		{
			name:     "password unset is omitted",
			creds:    onboarding.Credentials{Username: "readonly@vsphere.local"},
			wantKeys: []string{"username"},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := mockni.Start(t, mockni.Options{Token: token})
			req := baseRequest()
			req.Credentials = tc.creds

			if _, err := newClient(t, srv).OnboardVCenter(context.Background(), req); err != nil {
				t.Fatalf("OnboardVCenter: %v", err)
			}

			for _, op := range []string{mockni.OpValidateVCenter, mockni.OpAddVcenterDatasource} {
				obj := decodeOnly(t, op+" body", srv.RequestsFor(op))
				raw, ok := obj["credentials"]
				if !ok {
					t.Fatalf("%s body: credentials member is absent", op)
				}
				var nested map[string]json.RawMessage
				if err := json.Unmarshal(raw, &nested); err != nil {
					t.Fatalf("%s body: credentials is not an object: %v", op, err)
				}
				assertExactKeys(t, op+" credentials", nested, tc.wantKeys...)
				assertMember(t, op+" credentials", nested, "username", `"`+tc.creds.Username+`"`)
				if tc.creds.Password != "" {
					assertMember(t, op+" credentials", nested, "password", `"`+tc.creds.Password+`"`)
				}
			}
		})
	}
}

// TestCredentialsOmittedWhenUnset covers credentials itself, which is an
// optional property on both request schemas. A zero Credentials value means
// the caller did not set that property, so the object must be absent rather
// than serialized as {"username":""} or rejected as though it were required.
func TestCredentialsOmittedWhenUnset(t *testing.T) {
	t.Parallel()

	srv := mockni.Start(t, mockni.Options{Token: token})
	req := baseRequest()
	req.Credentials = onboarding.Credentials{}

	if _, err := newClient(t, srv).OnboardVCenter(context.Background(), req); err != nil {
		t.Fatalf("OnboardVCenter: %v", err)
	}

	for _, tc := range []struct {
		op       string
		wantKeys []string
	}{
		{mockni.OpValidateVCenter, []string{"ip", "proxy_id"}},
		{mockni.OpAddVcenterDatasource, []string{"ip", "proxy_id", "nickname"}},
	} {
		obj := decodeOnly(t, tc.op+" body", srv.RequestsFor(tc.op))
		assertExactKeys(t, tc.op+" body", obj, tc.wantKeys...)
	}
}

// TestAuthorizationHeader pins the ApiKeyAuth scheme from the spec. The prefix
// is the literal word NetworkInsight, not Bearer.
func TestAuthorizationHeader(t *testing.T) {
	t.Parallel()

	srv := mockni.Start(t, mockni.Options{Token: token})
	if _, err := newClient(t, srv).OnboardVCenter(context.Background(), baseRequest()); err != nil {
		t.Fatalf("OnboardVCenter: %v", err)
	}

	reqs := srv.Requests()
	if len(reqs) == 0 {
		t.Fatal("no requests were logged")
	}
	for _, r := range reqs {
		if got, want := r.Header.Get("Authorization"), "NetworkInsight "+token; got != want {
			t.Errorf("%s: Authorization = %q, want %q", r.OperationID, got, want)
		}
		if got := r.Header.Get("Content-Type"); !strings.HasPrefix(got, "application/json") {
			t.Errorf("%s: Content-Type = %q, want application/json", r.OperationID, got)
		}
	}
}

// TestInvalidTargetMakesNoCalls checks that the mutually-exclusive ip/fqdn rule
// and the required fields are enforced before anything is put on the wire.
func TestInvalidTargetMakesNoCalls(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name   string
		mutate func(*onboarding.VCenterOnboardRequest)
	}{
		{
			name: "both ip and fqdn set",
			mutate: func(r *onboarding.VCenterOnboardRequest) {
				r.FQDN = "vc01.corp.example.com"
			},
		},
		{
			name: "neither ip nor fqdn set",
			mutate: func(r *onboarding.VCenterOnboardRequest) {
				r.IP = ""
			},
		},
		{
			name: "proxy_id missing",
			mutate: func(r *onboarding.VCenterOnboardRequest) {
				r.ProxyID = ""
			},
		},
		{
			name: "nickname missing",
			mutate: func(r *onboarding.VCenterOnboardRequest) {
				r.Nickname = ""
			},
		},
		{
			name: "credentials username missing",
			mutate: func(r *onboarding.VCenterOnboardRequest) {
				r.Credentials.Username = ""
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := mockni.Start(t, mockni.Options{Token: token})
			req := baseRequest()
			tc.mutate(&req)

			res, err := newClient(t, srv).OnboardVCenter(context.Background(), req)
			if !errors.Is(err, onboarding.ErrInvalidRequest) {
				t.Fatalf("OnboardVCenter err = %v, want one matching onboarding.ErrInvalidRequest", err)
			}
			if res != nil {
				t.Errorf("OnboardVCenter result = %+v, want nil", res)
			}
			if got := srv.Requests(); len(got) != 0 {
				t.Errorf("%d request(s) reached the API, want 0 "+
					"(a request that cannot be serialized legally must be rejected before any call)", len(got))
			}
		})
	}
}

// TestClientUsesOnlyContractOperations makes sure the client never touches a
// route outside the contract. The mock answers 404 for anything else and logs
// it with an empty operation ID.
func TestClientUsesOnlyContractOperations(t *testing.T) {
	t.Parallel()

	srv := mockni.Start(t, mockni.Options{Token: token})
	if _, err := newClient(t, srv).OnboardVCenter(context.Background(), baseRequest()); err != nil {
		t.Fatalf("OnboardVCenter: %v", err)
	}

	for _, r := range srv.Requests() {
		if r.OperationID == "" {
			t.Errorf("client called %s %s, which is not an operation named in docs/contract.json",
				r.Method, r.Path)
		}
		if r.Status == 401 {
			t.Errorf("%s %s was rejected as unauthorized", r.Method, r.Path)
		}
	}
}
