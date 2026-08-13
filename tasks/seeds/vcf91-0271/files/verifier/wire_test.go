// Package verifier checks opsrollout against the contract in docs/contract.json
// using the loopback mock in internal/opsmock. It contacts no VMware endpoint.
package verifier

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"reflect"
	"strings"
	"sync"
	"testing"

	"example.com/vcfopsrollout/internal/opsmock"
	"example.com/vcfopsrollout/opsrollout"
)

// wantSequence is contract.changeSequence: the steps of the change, in order.
var wantSequence = []struct {
	name        string
	operationID string
	method      string
	successCode int
}{
	{opsrollout.StepAcquireToken, "acquireToken", http.MethodPost, http.StatusOK},
	{opsrollout.StepCreateGroup, "createCustomGroup", http.MethodPost, http.StatusCreated},
	{opsrollout.StepAssignPolicy, "assignPolicy", http.MethodPut, http.StatusOK},
	{opsrollout.StepCreateRule, "createNotificationPluginRule", http.MethodPost, http.StatusCreated},
}

// goodChange is a change every step of which the appliance accepts.
func goodChange() opsrollout.Change {
	return opsrollout.Change{
		Credentials: opsrollout.Credentials{
			Username: opsmock.ValidUsername,
			Password: opsmock.ValidPassword,
		},
		Group: opsrollout.GroupSpec{
			Name:                  "Production Workloads",
			AdapterKindKey:        opsmock.KnownAdapterKind,
			ResourceKindKey:       opsmock.KnownResourceKind,
			AutoResolveMembership: true,
		},
		PolicyID: opsmock.KnownPolicyID,
		Rule: opsrollout.RuleSpec{
			Name:     "Production Workloads notifications",
			PluginID: opsmock.KnownPluginID,
			Enabled:  true,
		},
	}
}

func newClient(t *testing.T) (*opsrollout.Client, *opsmock.Server) {
	t.Helper()
	srv := opsmock.New()
	t.Cleanup(srv.Close)
	return opsrollout.New(srv.URL(), srv.Client()), srv
}

// -----------------------------------------------------------------------------
// The mock is pinned to the contract
// -----------------------------------------------------------------------------

// TestOnlyContractOperationsAreServed pins the mock to the contract: a route the
// contract does not name is not served, and a contract route answers only to its
// declared method.
func TestOnlyContractOperationsAreServed(t *testing.T) {
	srv := opsmock.New()
	defer srv.Close()

	cases := []struct {
		name   string
		method string
		path   string
		want   int
	}{
		{"acquireToken route", http.MethodPost, "/api/auth/token/acquire", http.StatusUnsupportedMediaType},
		{"createCustomGroup route", http.MethodPost, "/api/resources/groups", http.StatusUnauthorized},
		{"assignPolicy route", http.MethodPut, "/api/policies/" + opsmock.KnownPolicyID + "/assign", http.StatusUnauthorized},
		{"createNotificationPluginRule route", http.MethodPost, "/api/notifications/rules", http.StatusUnauthorized},
		{"wrong method on acquireToken", http.MethodGet, "/api/auth/token/acquire", http.StatusMethodNotAllowed},
		{"wrong method on createCustomGroup", http.MethodGet, "/api/resources/groups", http.StatusMethodNotAllowed},
		{"unnamed operation on a contract prefix", http.MethodGet, "/api/policies", http.StatusNotFound},
		{"unnamed policy sub-operation", http.MethodPut, "/api/policies/" + opsmock.KnownPolicyID + "/unassign", http.StatusNotFound},
		{"unnamed alerts operation", http.MethodGet, "/api/alerts", http.StatusNotFound},
		{"unnamed symptom operation", http.MethodGet, "/api/symptomdefinitions", http.StatusNotFound},
		{"unnamed log management operation", http.MethodGet, "/api/logs/queryconfigs", http.StatusNotFound},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(tc.method, srv.URL()+tc.path, strings.NewReader("{}"))
			if err != nil {
				t.Fatalf("build request: %v", err)
			}
			resp, err := srv.Client().Do(req)
			if err != nil {
				t.Fatalf("do request: %v", err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != tc.want {
				t.Fatalf("%s %s: status = %d, want %d", tc.method, tc.path, resp.StatusCode, tc.want)
			}
		})
	}
}

// -----------------------------------------------------------------------------
// Request wire shape
// -----------------------------------------------------------------------------

// TestRequestWireShape asserts the exact request each step produces, including
// that an optional field the caller left unset is absent from the body rather
// than sent as an empty value, and that a required field is sent even when its
// value is a zero value.
func TestRequestWireShape(t *testing.T) {
	full := goodChange()
	full.Credentials.AuthSource = opsmock.ValidAuthSource
	full.Group.IncludedResourceIDs = []string{opsmock.KnownResourceID}
	full.Group.ExcludedResourceIDs = []string{"1f2c3d44-5e6f-4708-91a2-b3c4d5e6f708"}
	full.ResourceAssignments = []opsrollout.ResourceAssignment{{ResourceID: opsmock.KnownResourceID, Depth: 0}}
	full.Rule.TemplateID = "3b6e0f52-9a71-4c0d-8f2b-1d47ae95c6b0"
	full.Rule.AlertDefinitionIDs = []string{opsmock.KnownAlertDefinitionID}
	full.Rule.Criticalities = []string{"CRITICAL", "IMMEDIATE"}

	cases := []struct {
		name   string
		change opsrollout.Change
		// wantBodyKeys is the exact sorted set of top-level body keys per step,
		// keyed by operation id.
		wantBodyKeys map[string][]string
		// wantBodies is the exact decoded body per step, keyed by operation id.
		wantBodies map[string]string
	}{
		{
			name:   "unset optional fields are omitted",
			change: goodChange(),
			wantBodyKeys: map[string][]string{
				"acquireToken":                 {"password", "username"},
				"createCustomGroup":            {"autoResolveMembership", "membershipDefinition", "resourceKey"},
				"assignPolicy":                 {"groupIds"},
				"createNotificationPluginRule": {"enabled", "name", "pluginId", "ruleType"},
			},
			wantBodies: map[string]string{
				"acquireToken": `{"password":"Rq4-still-lantern-88","username":"svc-ops-admin"}`,
				"createCustomGroup": `{"autoResolveMembership":true,"membershipDefinition":{},` +
					`"resourceKey":{"adapterKindKey":"Container","name":"Production Workloads","resourceKindKey":"Environment"}}`,
				"assignPolicy": `{"groupIds":["9613f1e4-6b93-4d9d-ba82-09beb46d75a6"]}`,
				"createNotificationPluginRule": `{"enabled":true,"name":"Production Workloads notifications",` +
					`"pluginId":"8e9b3d17-2c40-4f6a-9e51-a7bd0c62f3aa","ruleType":"ALERT"}`,
			},
		},
		{
			name:   "set optional fields are sent, and a required zero value is not dropped",
			change: full,
			wantBodyKeys: map[string][]string{
				"acquireToken":      {"authSource", "password", "username"},
				"createCustomGroup": {"autoResolveMembership", "membershipDefinition", "resourceKey"},
				"assignPolicy":      {"groupIds", "resourceAssignments"},
				"createNotificationPluginRule": {
					"alertDefinitionIdFilters", "criticalities", "enabled", "name",
					"pluginId", "ruleType", "templateId",
				},
			},
			wantBodies: map[string]string{
				"acquireToken": `{"authSource":"Imported LDAP Server","password":"Rq4-still-lantern-88","username":"svc-ops-admin"}`,
				"createCustomGroup": `{"autoResolveMembership":true,` +
					`"membershipDefinition":{"excludedResources":["1f2c3d44-5e6f-4708-91a2-b3c4d5e6f708"],` +
					`"includedResources":["529c2a31-a993-430f-ae30-e467d04f8d6e"]},` +
					`"resourceKey":{"adapterKindKey":"Container","name":"Production Workloads","resourceKindKey":"Environment"}}`,
				// depth is required by resource-assignment, so depth 0 is sent.
				"assignPolicy": `{"groupIds":["9613f1e4-6b93-4d9d-ba82-09beb46d75a6"],` +
					`"resourceAssignments":[{"depth":0,"resourceId":"529c2a31-a993-430f-ae30-e467d04f8d6e"}]}`,
				"createNotificationPluginRule": `{"alertDefinitionIdFilters":{"values":["AlertDefinition-VMWARE-VirtualMachine-CpuContention"]},` +
					`"criticalities":["CRITICAL","IMMEDIATE"],"enabled":true,"name":"Production Workloads notifications",` +
					`"pluginId":"8e9b3d17-2c40-4f6a-9e51-a7bd0c62f3aa","ruleType":"ALERT","templateId":"3b6e0f52-9a71-4c0d-8f2b-1d47ae95c6b0"}`,
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c, srv := newClient(t)

			rep, err := c.Apply(context.Background(), tc.change)
			if err != nil {
				t.Fatalf("Apply: %v\n%s", err, summarize(srv.Requests()))
			}
			assertReportShape(t, rep)

			reqs := srv.Requests()
			if len(reqs) != len(wantSequence) {
				t.Fatalf("request count = %d, want %d:%s", len(reqs), len(wantSequence), summarize(reqs))
			}
			for i, want := range wantSequence {
				r := reqs[i]
				if r.OperationID != want.operationID {
					t.Fatalf("request %d is %s, want %s:%s", i, r.OperationID, want.operationID, summarize(reqs))
				}
				assertCommonRequestShape(t, r, want.method, want.operationID)
				if r.Status != want.successCode {
					t.Errorf("%s: mock answered %d (%s), want %d",
						want.operationID, r.Status, r.Rejection, want.successCode)
				}
				if got := tc.wantBodyKeys[want.operationID]; !reflect.DeepEqual(r.BodyKeys, got) {
					t.Errorf("%s: body keys = %v, want %v (body: %s)",
						want.operationID, r.BodyKeys, got, r.Body)
				}
				assertBodyEquals(t, want.operationID, r.Body, tc.wantBodies[want.operationID])
				assertNoEmptyValues(t, want.operationID, r.Body)
			}

			// assignPolicy addresses the policy by its id in the path, and the
			// group by the identifier the appliance assigned in step 2.
			if got, want := reqs[2].Path, opsmock.AssignPolicyPath(tc.change.PolicyID); got != want {
				t.Errorf("assignPolicy path = %q, want %q", got, want)
			}
		})
	}
}

// assertCommonRequestShape checks the transport rules every operation shares.
func assertCommonRequestShape(t *testing.T, r opsmock.Request, method, operationID string) {
	t.Helper()

	if r.Method != method {
		t.Errorf("%s: method = %q, want %q", operationID, r.Method, method)
	}
	if r.RawQuery != "" {
		t.Errorf("%s: raw query = %q, want empty: the operation declares no query parameters",
			operationID, r.RawQuery)
	}
	if got := mediaType(r.ContentType); got != "application/json" {
		t.Errorf("%s: Content-Type = %q, want application/json", operationID, r.ContentType)
	}
	if !acceptsJSON(r.Accept) {
		t.Errorf("%s: Accept = %q, want it to allow application/json", operationID, r.Accept)
	}

	if operationID == "acquireToken" {
		// acquireToken declares security: [] and must be unauthenticated.
		if r.AuthorizationPresent {
			t.Errorf("acquireToken carried Authorization %q; it declares security: []", r.Authorization)
		}
		return
	}
	if !r.AuthorizationPresent {
		t.Errorf("%s: missing Authorization header", operationID)
		return
	}
	if want := opsmock.TokenPrefix + opsmock.IssuedToken; r.Authorization != want {
		t.Errorf("%s: Authorization = %q, want %q", operationID, r.Authorization, want)
	}
}

// assertBodyEquals compares a request body against want after re-encoding both
// with sorted keys, so formatting differences do not matter but the exact set of
// properties and values does.
func assertBodyEquals(t *testing.T, operationID string, body []byte, want string) {
	t.Helper()
	got, err := canonicalJSON(body)
	if err != nil {
		t.Errorf("%s: body is not valid JSON: %s", operationID, body)
		return
	}
	wantCanonical, err := canonicalJSON([]byte(want))
	if err != nil {
		t.Fatalf("%s: want body is not valid JSON: %s", operationID, want)
	}
	if got != wantCanonical {
		t.Errorf("%s: body =\n  %s\nwant\n  %s", operationID, got, wantCanonical)
	}
}

// canonicalJSON re-encodes a JSON document with object keys sorted, which
// encoding/json does for a map.
func canonicalJSON(b []byte) (string, error) {
	var v any
	if err := json.Unmarshal(b, &v); err != nil {
		return "", err
	}
	out, err := json.Marshal(v)
	if err != nil {
		return "", err
	}
	return string(out), nil
}

// assertNoEmptyValues rejects a body that encodes an unset optional property as
// null, as an empty string, or as an empty array anywhere in the document. The
// one legitimate empty object is custom-group.membershipDefinition, which is a
// required property whose own properties are all optional.
func assertNoEmptyValues(t *testing.T, operationID string, body []byte) {
	t.Helper()
	var doc any
	if err := json.Unmarshal(body, &doc); err != nil {
		return
	}
	var walk func(path string, v any)
	walk = func(path string, v any) {
		switch tv := v.(type) {
		case nil:
			t.Errorf("%s: %s is null; an unset optional property must be omitted (body: %s)",
				operationID, path, body)
		case string:
			if tv == "" {
				t.Errorf("%s: %s is an empty string; an unset optional property must be omitted (body: %s)",
					operationID, path, body)
			}
		case []any:
			if len(tv) == 0 {
				t.Errorf("%s: %s is an empty array; an unset optional property must be omitted (body: %s)",
					operationID, path, body)
			}
			for i, e := range tv {
				walk(fmt.Sprintf("%s[%d]", path, i), e)
			}
		case map[string]any:
			if len(tv) == 0 && path != "membershipDefinition" {
				t.Errorf("%s: %s is an empty object; an unset optional property must be omitted (body: %s)",
					operationID, path, body)
			}
			for k, e := range tv {
				child := k
				if path != "" {
					child = path + "." + k
				}
				walk(child, e)
			}
		}
	}
	walk("", doc)
}

// -----------------------------------------------------------------------------
// Reporting a partially applied change
// -----------------------------------------------------------------------------

// TestPartiallyAppliedChangeIsReported drives a failure at each step of the
// sequence and asserts that the steps already applied stay reported as
// succeeded, that the failing step carries the appliance's status, that later
// steps are reported as skipped, and that no request is issued after the
// failure.
func TestPartiallyAppliedChangeIsReported(t *testing.T) {
	// wantDetail is the detail each step reports on success. Every value comes
	// from the appliance's response, not from the requested change.
	wantDetail := map[string]string{
		opsrollout.StepAcquireToken: opsmock.TokenExpiresAt,
		opsrollout.StepCreateGroup:  opsmock.AssignedGroupID,
		opsrollout.StepAssignPolicy: opsmock.AssignedGroupID,
		opsrollout.StepCreateRule:   opsmock.AssignedRuleID,
	}

	cases := []struct {
		name string
		// mutate breaks the change so that step failIndex fails.
		mutate func(*opsrollout.Change)
		// failIndex is the step that must fail, or -1 when none does.
		failIndex int
		// wantStatus is the HTTP status the failing step must report.
		wantStatus int
		// wantMessage is a substring of the appliance message the failing step
		// must report.
		wantMessage string
	}{
		{
			name:      "every step succeeds",
			mutate:    func(*opsrollout.Change) {},
			failIndex: -1,
		},
		{
			name:        "the first step fails and nothing else is attempted",
			mutate:      func(ch *opsrollout.Change) { ch.Credentials.Password = "not-the-password" },
			failIndex:   0,
			wantStatus:  http.StatusUnauthorized,
			wantMessage: "authentication failed",
		},
		{
			name:        "the second step fails after the token was acquired",
			mutate:      func(ch *opsrollout.Change) { ch.Group.ResourceKindKey = "NoSuchResourceKind" },
			failIndex:   1,
			wantStatus:  http.StatusUnprocessableEntity,
			wantMessage: "unknown resource kind",
		},
		{
			name:        "the third step fails after the group was created",
			mutate:      func(ch *opsrollout.Change) { ch.PolicyID = "00000000-0000-0000-0000-000000000000" },
			failIndex:   2,
			wantStatus:  http.StatusNotFound,
			wantMessage: "no policy with id",
		},
		{
			name: "the last step fails after the group was created and the policy assigned",
			mutate: func(ch *opsrollout.Change) {
				ch.Rule.AlertDefinitionIDs = []string{"AlertDefinition-Does-Not-Exist"}
			},
			failIndex:   3,
			wantStatus:  http.StatusNotFound,
			wantMessage: "unknown alert definition identifier",
		},
		{
			name:        "the last step is rejected as unprocessable",
			mutate:      func(ch *opsrollout.Change) { ch.Rule.PluginID = "11111111-2222-3333-4444-555555555555" },
			failIndex:   3,
			wantStatus:  http.StatusUnprocessableEntity,
			wantMessage: "no notification plugin instance",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c, srv := newClient(t)
			ch := goodChange()
			tc.mutate(&ch)

			rep, err := c.Apply(context.Background(), ch)
			reqs := srv.Requests()

			// The report is returned whether or not the change succeeded.
			if rep == nil {
				t.Fatalf("Apply returned a nil report (err: %v):%s", err, summarize(reqs))
			}
			assertReportShape(t, rep)

			if tc.failIndex < 0 {
				if err != nil {
					t.Fatalf("Apply: %v:%s", err, summarize(reqs))
				}
				if rep.Failed {
					t.Errorf("report.Failed = true, want false")
				}
			} else if err == nil {
				t.Fatalf("Apply: want an error from step %d, got nil:%s", tc.failIndex, summarize(reqs))
			} else if !rep.Failed {
				t.Errorf("report.Failed = false, want true (err: %v)", err)
			}

			for i, want := range wantSequence {
				step := rep.Steps[i]
				switch {
				case tc.failIndex < 0 || i < tc.failIndex:
					// An applied step stays reported as succeeded even though a
					// later step failed: the appliance does not roll it back.
					if step.Status != opsrollout.StatusSucceeded {
						t.Errorf("step %q: status = %q, want %q",
							want.name, step.Status, opsrollout.StatusSucceeded)
					}
					if step.HTTPStatus != want.successCode {
						t.Errorf("step %q: http status = %d, want %d",
							want.name, step.HTTPStatus, want.successCode)
					}
					if step.Detail != wantDetail[want.name] {
						t.Errorf("step %q: detail = %q, want %q (the value the appliance returned)",
							want.name, step.Detail, wantDetail[want.name])
					}
					if step.Err != "" {
						t.Errorf("step %q: err = %q, want empty", want.name, step.Err)
					}
				case i == tc.failIndex:
					if step.Status != opsrollout.StatusFailed {
						t.Errorf("step %q: status = %q, want %q",
							want.name, step.Status, opsrollout.StatusFailed)
					}
					if step.HTTPStatus != tc.wantStatus {
						t.Errorf("step %q: http status = %d, want %d",
							want.name, step.HTTPStatus, tc.wantStatus)
					}
					if !strings.Contains(step.Err, tc.wantMessage) {
						t.Errorf("step %q: err = %q, want it to contain %q",
							want.name, step.Err, tc.wantMessage)
					}
					if step.Detail != "" {
						t.Errorf("step %q: detail = %q, want empty for a failed step",
							want.name, step.Detail)
					}
				default:
					if step.Status != opsrollout.StatusSkipped {
						t.Errorf("step %q: status = %q, want %q",
							want.name, step.Status, opsrollout.StatusSkipped)
					}
					if step.HTTPStatus != 0 {
						t.Errorf("step %q: http status = %d, want 0 for a step that was not attempted",
							want.name, step.HTTPStatus)
					}
					if step.Detail != "" || step.Err != "" {
						t.Errorf("step %q: detail = %q err = %q, want both empty for a skipped step",
							want.name, step.Detail, step.Err)
					}
				}
			}

			// No request is issued after the failure.
			wantRequests := len(wantSequence)
			if tc.failIndex >= 0 {
				wantRequests = tc.failIndex + 1
			}
			if len(reqs) != wantRequests {
				t.Fatalf("request count = %d, want %d:%s", len(reqs), wantRequests, summarize(reqs))
			}
			for i, r := range reqs {
				if r.OperationID != wantSequence[i].operationID {
					t.Errorf("request %d is %s, want %s", i, r.OperationID, wantSequence[i].operationID)
				}
			}
		})
	}
}

// assertReportShape checks that a report names every step of the change exactly
// once, in sequence order.
func assertReportShape(t *testing.T, rep *opsrollout.Report) {
	t.Helper()
	if len(rep.Steps) != len(wantSequence) {
		t.Fatalf("report has %d steps, want %d: %+v", len(rep.Steps), len(wantSequence), rep.Steps)
	}
	for i, want := range wantSequence {
		if rep.Steps[i].Name != want.name {
			t.Errorf("step %d name = %q, want %q", i, rep.Steps[i].Name, want.name)
		}
		if rep.Steps[i].OperationID != want.operationID {
			t.Errorf("step %d operation id = %q, want %q",
				i, rep.Steps[i].OperationID, want.operationID)
		}
	}
}

// TestFailuresAlwaysReturnReportAndKnownStatus covers failures discovered
// while interpreting a response and a failure before any response. Every case
// returns a report; the former carries the status that arrived, while only the
// latter uses status 0.
func TestFailuresAlwaysReturnReportAndKnownStatus(t *testing.T) {
	cases := []struct {
		name         string
		body         string
		transportErr error
		wantStatus   int
		wantErrPart  string
	}{
		{
			name:        "malformed success response",
			body:        `{`,
			wantStatus:  http.StatusOK,
			wantErrPart: "decode response",
		},
		{
			name:        "success response has no usable token",
			body:        `{"token":"","validity":1,"expiresAt":"returned expiry"}`,
			wantStatus:  http.StatusOK,
			wantErrPart: "empty token",
		},
		{
			name:         "transport fails before a response",
			transportErr: fmt.Errorf("transport unavailable"),
			wantStatus:   0,
			wantErrPart:  "transport unavailable",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var request *http.Request
			hc := &http.Client{Transport: roundTripFunc(func(r *http.Request) (*http.Response, error) {
				request = r
				if tc.transportErr != nil {
					return nil, tc.transportErr
				}
				return &http.Response{
					StatusCode: http.StatusOK,
					Header:     make(http.Header),
					Body:       io.NopCloser(strings.NewReader(tc.body)),
					Request:    r,
				}, nil
			})}
			c := opsrollout.New("http://127.0.0.1/suite-api", hc)

			rep, err := c.Apply(context.Background(), goodChange())
			if err == nil {
				t.Fatal("Apply returned nil error")
			}
			if rep == nil {
				t.Fatalf("Apply returned nil report (err: %v)", err)
			}
			assertReportShape(t, rep)
			if !rep.Failed {
				t.Error("report.Failed = false, want true")
			}
			first := rep.Steps[0]
			if first.Status != opsrollout.StatusFailed || first.HTTPStatus != tc.wantStatus {
				t.Errorf("first step = %+v, want FAILED with HTTP status %d", first, tc.wantStatus)
			}
			if !strings.Contains(first.Err, tc.wantErrPart) {
				t.Errorf("first step err = %q, want it to contain %q", first.Err, tc.wantErrPart)
			}
			for i, step := range rep.Steps[1:] {
				if step.Status != opsrollout.StatusSkipped || step.HTTPStatus != 0 {
					t.Errorf("step %d = %+v, want SKIPPED with HTTP status 0", i+1, step)
				}
			}
			if request == nil {
				t.Fatal("Apply issued no request")
			}
			if request.Method != http.MethodPost || request.URL.Path != opsmock.PathAcquireToken {
				t.Errorf("request = %s %s, want POST %s",
					request.Method, request.URL.Path, opsmock.PathAcquireToken)
			}
		})
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

// TestConcurrentChangesAreReportedIndependently applies changes concurrently on
// one client. Under -race this also catches report storage shared between calls.
func TestConcurrentChangesAreReportedIndependently(t *testing.T) {
	const goroutines = 8

	c, srv := newClient(t)

	reports := make([]*opsrollout.Report, goroutines)
	errs := make([]error, goroutines)
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < goroutines; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			ch := goodChange()
			// Half the changes fail at the last step, half succeed. Each caller
			// must see its own outcome.
			if i%2 == 1 {
				ch.Rule.AlertDefinitionIDs = []string{"AlertDefinition-Does-Not-Exist"}
			}
			<-start
			reports[i], errs[i] = c.Apply(context.Background(), ch)
		}(i)
	}
	close(start)
	wg.Wait()

	for i := 0; i < goroutines; i++ {
		rep := reports[i]
		if rep == nil {
			t.Fatalf("goroutine %d: nil report (err: %v)", i, errs[i])
		}
		assertReportShape(t, rep)
		wantFailed := i%2 == 1
		if rep.Failed != wantFailed {
			t.Fatalf("goroutine %d: report.Failed = %v, want %v (steps: %+v)",
				i, rep.Failed, wantFailed, rep.Steps)
		}
		if (errs[i] != nil) != wantFailed {
			t.Fatalf("goroutine %d: err = %v, want error: %v", i, errs[i], wantFailed)
		}
		last := rep.Steps[len(rep.Steps)-1]
		if wantFailed {
			if last.Status != opsrollout.StatusFailed || last.HTTPStatus != http.StatusNotFound {
				t.Errorf("goroutine %d: last step = %+v, want FAILED 404", i, last)
			}
		} else if last.Status != opsrollout.StatusSucceeded || last.Detail != opsmock.AssignedRuleID {
			t.Errorf("goroutine %d: last step = %+v, want SUCCEEDED %s", i, last, opsmock.AssignedRuleID)
		}
		// The first three steps are applied in every case.
		for j := 0; j < 3; j++ {
			if rep.Steps[j].Status != opsrollout.StatusSucceeded {
				t.Errorf("goroutine %d: step %d = %+v, want SUCCEEDED", i, j, rep.Steps[j])
			}
		}
	}

	// Each caller must get report storage of its own.
	for i := 1; i < goroutines; i++ {
		if &reports[i].Steps[0] == &reports[0].Steps[0] {
			t.Fatalf("goroutines 0 and %d share report storage", i)
		}
	}

	if got, want := len(srv.Requests()), goroutines*len(wantSequence); got != want {
		t.Errorf("request count = %d, want %d", got, want)
	}
}

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------

func mediaType(v string) string { return strings.TrimSpace(strings.SplitN(v, ";", 2)[0]) }

func acceptsJSON(accept string) bool {
	for _, part := range strings.Split(accept, ",") {
		switch mediaType(part) {
		case "application/json", "application/*", "*/*":
			return true
		}
	}
	return false
}

func summarize(reqs []opsmock.Request) string {
	var b strings.Builder
	b.WriteString("\n")
	for _, r := range reqs {
		fmt.Fprintf(&b, "  [%d] %s %s -> %d %s\n      body: %s\n",
			r.Seq, r.Method, r.Path, r.Status, r.Rejection, r.Body)
	}
	return b.String()
}
