package rollout_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"sort"
	"strings"
	"testing"

	"vcfops/netrollout/internal/mockni"
	"vcfops/netrollout/rollout"
)

const testToken = "0d3f1a7c-tok"

// appID and tierID mirror the mock's deterministic entity_id allocation.
const appID = "18230:561:271275766"

func tierID(n int) string { return fmt.Sprintf("18230:562:%d", 1266458745+n) }

// ---------------------------------------------------------------------------
// contract loading
// ---------------------------------------------------------------------------

type contractDoc struct {
	Server struct {
		BasePath string `json:"base_path"`
	} `json:"server"`
	Security struct {
		Name        string `json:"name"`
		ValueFormat string `json:"value_format"`
	} `json:"security"`
	Operations []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	} `json:"operations"`
}

func loadContract(t *testing.T) contractDoc {
	t.Helper()
	raw, err := os.ReadFile("../docs/contract.json")
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var c contractDoc
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatalf("parse contract: %v", err)
	}
	if len(c.Operations) == 0 {
		t.Fatal("contract names no operations")
	}
	return c
}

// ---------------------------------------------------------------------------
// scenario fixtures
// ---------------------------------------------------------------------------

func threeTierPlan() rollout.Plan {
	return rollout.Plan{
		ApplicationName: "payments-prod",
		Tiers: []rollout.TierPlan{
			{
				Name: "payments-web",
				Criteria: []rollout.Criterion{{
					Type:             rollout.SearchMembership,
					SearchEntityType: "VirtualMachine",
					SearchFilter:     "security_groups.entity_id = '18230:82:604573173'",
				}},
			},
			{
				Name: "payments-app",
				Criteria: []rollout.Criterion{{
					Type:        rollout.IPAddressMembership,
					IPAddresses: []string{"10.0.0.1", "10.0.0.0/24", "10.0.0.1-10.0.0.200"},
				}},
			},
			{
				Name: "payments-db",
				Criteria: []rollout.Criterion{
					{
						Type:             rollout.SearchMembership,
						SearchEntityType: "VirtualMachine",
						SearchFilter:     "name like 'pay-db'",
					},
					{
						Type:        rollout.IPAddressMembership,
						IPAddresses: []string{"10.9.0.0/24"},
					},
				},
			},
		},
	}
}

func newServer(t *testing.T, reject map[string]mockni.Rejection) *mockni.Server {
	t.Helper()
	s := mockni.New(mockni.Config{Token: testToken, RejectTiers: reject})
	t.Cleanup(s.Close)
	return s
}

// ---------------------------------------------------------------------------
// multi-step outcome reporting
// ---------------------------------------------------------------------------

func TestApplyReportsEveryStepOfAMultiStepChange(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name        string
		reject      map[string]mockni.Rejection
		wantFailed  bool
		wantAppID   string
		wantSteps   []rollout.Step
		wantOnHost  []string // tiers the server actually holds afterwards
		wantList    int      // expected listApplicationTiers confirmation calls
		wantErr     bool
		wantErrHint string
	}{
		{
			name:       "all steps apply",
			wantFailed: false,
			wantAppID:  appID,
			wantSteps: []rollout.Step{
				{OperationID: "addApplication", Target: "payments-prod", Status: rollout.StatusApplied, EntityID: appID, StatusCode: 201},
				{OperationID: "addTier", Target: "payments-web", Status: rollout.StatusApplied, EntityID: tierID(1), StatusCode: 201},
				{OperationID: "addTier", Target: "payments-app", Status: rollout.StatusApplied, EntityID: tierID(2), StatusCode: 201},
				{OperationID: "addTier", Target: "payments-db", Status: rollout.StatusApplied, EntityID: tierID(3), StatusCode: 201},
			},
			wantOnHost: []string{"payments-web", "payments-app", "payments-db"},
			wantList:   0,
		},
		{
			name: "last step fails, the two applied tiers are reported",
			reject: map[string]mockni.Rejection{
				"payments-db": {Status: 400, Code: 400, Message: "membership criteria references an unknown entity"},
			},
			wantFailed: true,
			wantAppID:  appID,
			wantSteps: []rollout.Step{
				{OperationID: "addApplication", Target: "payments-prod", Status: rollout.StatusApplied, EntityID: appID, StatusCode: 201},
				{OperationID: "addTier", Target: "payments-web", Status: rollout.StatusApplied, EntityID: tierID(1), StatusCode: 201},
				{OperationID: "addTier", Target: "payments-app", Status: rollout.StatusApplied, EntityID: tierID(2), StatusCode: 201},
				{OperationID: "addTier", Target: "payments-db", Status: rollout.StatusFailed, StatusCode: 400, Message: "membership criteria references an unknown entity"},
			},
			wantOnHost:  []string{"payments-web", "payments-app"},
			wantList:    1,
			wantErr:     true,
			wantErrHint: "payments-db",
		},
		{
			name: "middle step fails, the later tier is never attempted",
			reject: map[string]mockni.Rejection{
				"payments-app": {Status: 403, Code: 403, Message: "insufficient privilege to modify this application"},
			},
			wantFailed: true,
			wantAppID:  appID,
			wantSteps: []rollout.Step{
				{OperationID: "addApplication", Target: "payments-prod", Status: rollout.StatusApplied, EntityID: appID, StatusCode: 201},
				{OperationID: "addTier", Target: "payments-web", Status: rollout.StatusApplied, EntityID: tierID(1), StatusCode: 201},
				{OperationID: "addTier", Target: "payments-app", Status: rollout.StatusFailed, StatusCode: 403, Message: "insufficient privilege to modify this application"},
				{OperationID: "addTier", Target: "payments-db", Status: rollout.StatusNotAttempted},
			},
			wantOnHost:  []string{"payments-web"},
			wantList:    1,
			wantErr:     true,
			wantErrHint: "payments-app",
		},
		{
			name: "first tier fails, application is still reported as applied",
			reject: map[string]mockni.Rejection{
				"payments-web": {Status: 500, Code: 500, Message: "internal error"},
			},
			wantFailed: true,
			wantAppID:  appID,
			wantSteps: []rollout.Step{
				{OperationID: "addApplication", Target: "payments-prod", Status: rollout.StatusApplied, EntityID: appID, StatusCode: 201},
				{OperationID: "addTier", Target: "payments-web", Status: rollout.StatusFailed, StatusCode: 500, Message: "internal error"},
				{OperationID: "addTier", Target: "payments-app", Status: rollout.StatusNotAttempted},
				{OperationID: "addTier", Target: "payments-db", Status: rollout.StatusNotAttempted},
			},
			wantOnHost:  nil,
			wantList:    1,
			wantErr:     true,
			wantErrHint: "payments-web",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := newServer(t, tc.reject)
			c := rollout.NewClient(srv.URL(), testToken, srv.Client())

			rep, err := c.Apply(context.Background(), threeTierPlan())

			if rep == nil {
				t.Fatal("Apply returned a nil Report; the report must describe every planned step even when the rollout fails")
			}
			if tc.wantErr && err == nil {
				t.Error("Apply returned a nil error for a rollout that did not fully apply")
			}
			if !tc.wantErr && err != nil {
				t.Errorf("Apply returned an unexpected error: %v", err)
			}
			if tc.wantErr && err != nil && !strings.Contains(err.Error(), tc.wantErrHint) {
				t.Errorf("error %q does not name the failing step %q", err, tc.wantErrHint)
			}
			if rep.Failed != tc.wantFailed {
				t.Errorf("Report.Failed = %v, want %v", rep.Failed, tc.wantFailed)
			}
			if rep.ApplicationID != tc.wantAppID {
				t.Errorf("Report.ApplicationID = %q, want %q", rep.ApplicationID, tc.wantAppID)
			}
			if !reflect.DeepEqual(rep.Steps, tc.wantSteps) {
				t.Errorf("Report.Steps mismatch\n got: %s\nwant: %s", fmtSteps(rep.Steps), fmtSteps(tc.wantSteps))
			}

			// The report must agree with the state the server actually holds.
			if got := srv.TierNames(appID); !reflect.DeepEqual(got, tc.wantOnHost) {
				t.Errorf("tiers on server = %v, want %v", got, tc.wantOnHost)
			}
			assertReportMatchesServer(t, srv, rep)

			// Nothing may be sent after the failing step.
			assertNoRequestsAfterFailure(t, srv, rep)

			// A partly-applied rollout must confirm state against the server.
			assertConfirmationCalls(t, srv, tc.wantList)
		})
	}
}

func TestApplyReportsApplicationFailureAndSkipsEveryTier(t *testing.T) {
	t.Parallel()

	srv := newServer(t, nil)
	// The stand-in rejects the very first call, before an application exists.
	c := rollout.NewClient(srv.URL(), "wrong-token", srv.Client())
	rep, err := c.Apply(context.Background(), threeTierPlan())

	if err == nil || !strings.Contains(err.Error(), "payments-prod") {
		t.Fatalf("error = %v, want a non-nil error naming payments-prod", err)
	}
	want := &rollout.Report{
		Failed: true,
		Steps: []rollout.Step{
			{OperationID: "addApplication", Target: "payments-prod", Status: rollout.StatusFailed, StatusCode: 401, Message: "unauthorized"},
			{OperationID: "addTier", Target: "payments-web", Status: rollout.StatusNotAttempted},
			{OperationID: "addTier", Target: "payments-app", Status: rollout.StatusNotAttempted},
			{OperationID: "addTier", Target: "payments-db", Status: rollout.StatusNotAttempted},
		},
	}
	if !reflect.DeepEqual(rep, want) {
		t.Fatalf("Report mismatch\n got: %#v\nwant: %#v", rep, want)
	}
	if got := len(srv.RequestsFor("addApplication")); got != 1 {
		t.Errorf("addApplication called %d times, want 1", got)
	}
	if got := len(srv.RequestsFor("addTier")); got != 0 {
		t.Errorf("addTier called %d times after application failure, want 0", got)
	}
	assertConfirmationCalls(t, srv, 0)
}

func TestApplyReportsTransportFailureWithoutInventingServerFields(t *testing.T) {
	t.Parallel()

	httpc := &http.Client{Transport: roundTripperFunc(func(*http.Request) (*http.Response, error) {
		return nil, errors.New("connection lost")
	})}
	c := rollout.NewClient("http://127.0.0.1", testToken, httpc)
	rep, err := c.Apply(context.Background(), threeTierPlan())

	if err == nil || !strings.Contains(err.Error(), "payments-prod") {
		t.Fatalf("error = %v, want a non-nil error naming payments-prod", err)
	}
	want := &rollout.Report{
		Failed: true,
		Steps: []rollout.Step{
			{OperationID: "addApplication", Target: "payments-prod", Status: rollout.StatusFailed},
			{OperationID: "addTier", Target: "payments-web", Status: rollout.StatusNotAttempted},
			{OperationID: "addTier", Target: "payments-app", Status: rollout.StatusNotAttempted},
			{OperationID: "addTier", Target: "payments-db", Status: rollout.StatusNotAttempted},
		},
	}
	if !reflect.DeepEqual(rep, want) {
		t.Fatalf("Report mismatch\n got: %#v\nwant: %#v", rep, want)
	}
}

func TestConfirmationCorrectsAcknowledgedButUncommittedTier(t *testing.T) {
	t.Parallel()

	srv := mockni.New(mockni.Config{
		Token: testToken,
		AcknowledgeWithoutCommit: map[string]bool{
			"payments-web": true,
		},
		RejectTiers: map[string]mockni.Rejection{
			"payments-app": {Status: 500, Code: 500, Message: "later tier failed"},
		},
	})
	t.Cleanup(srv.Close)
	c := rollout.NewClient(srv.URL(), testToken, srv.Client())
	rep, err := c.Apply(context.Background(), threeTierPlan())

	if rep == nil || err == nil {
		t.Fatalf("Apply returned report=%#v, error=%v; want a report and an error", rep, err)
	}
	if got := srv.TierNames(appID); got != nil {
		t.Fatalf("committed tiers = %v, want none", got)
	}
	if got := rep.Steps[1]; got.Status == rollout.StatusApplied || got.EntityID != "" {
		t.Errorf("acknowledged but uncommitted tier still reported as applied: %#v", got)
	}
	wantFailure := rollout.Step{
		OperationID: "addTier", Target: "payments-app", Status: rollout.StatusFailed,
		StatusCode: 500, Message: "later tier failed",
	}
	if got := rep.Steps[2]; !reflect.DeepEqual(got, wantFailure) {
		t.Errorf("actual failing step = %#v, want %#v", got, wantFailure)
	}
	if got := rep.Steps[3].Status; got != rollout.StatusNotAttempted {
		t.Errorf("later step status = %q, want %q", got, rollout.StatusNotAttempted)
	}
	assertReportMatchesServer(t, srv, rep)
	assertConfirmationCalls(t, srv, 1)
}

// assertReportMatchesServer checks the applied addTier steps are exactly the
// tiers the server committed, in the same order.
func assertReportMatchesServer(t *testing.T, srv *mockni.Server, rep *rollout.Report) {
	t.Helper()
	var claimed []string
	for _, s := range rep.Steps {
		if s.OperationID == "addTier" && s.Status == rollout.StatusApplied {
			claimed = append(claimed, s.Target)
		}
	}
	actual := srv.TierNames(rep.ApplicationID)
	if !reflect.DeepEqual(claimed, actual) {
		t.Errorf("report claims tiers %v were applied, but the server holds %v", claimed, actual)
	}
}

// assertNoRequestsAfterFailure checks the client stopped at the first failure:
// the number of addTier calls must equal the number of attempted addTier steps,
// and no not_attempted step may have produced a request.
func assertNoRequestsAfterFailure(t *testing.T, srv *mockni.Server, rep *rollout.Report) {
	t.Helper()
	var attempted []string
	for _, s := range rep.Steps {
		if s.OperationID == "addTier" && s.Status != rollout.StatusNotAttempted {
			attempted = append(attempted, s.Target)
		}
	}
	reqs := srv.RequestsFor("addTier")
	if len(reqs) != len(attempted) {
		t.Fatalf("server saw %d addTier calls but the report attempts %d (%v)", len(reqs), len(attempted), attempted)
	}
	for i, r := range reqs {
		body, err := r.DecodeBody()
		if err != nil {
			t.Fatalf("addTier call %d: %v", i, err)
		}
		if got, _ := body["name"].(string); got != attempted[i] {
			t.Errorf("addTier call %d was for tier %q, want %q", i, got, attempted[i])
		}
	}
}

// assertConfirmationCalls checks the client confirmed committed state with
// listApplicationTiers exactly when the rollout was partly applied.
func assertConfirmationCalls(t *testing.T, srv *mockni.Server, want int) {
	t.Helper()
	reqs := srv.RequestsFor("listApplicationTiers")
	if len(reqs) != want {
		t.Errorf("listApplicationTiers called %d times, want %d", len(reqs), want)
		return
	}
	for i, r := range reqs {
		if r.Method != http.MethodGet {
			t.Errorf("listApplicationTiers call %d method = %s, want GET", i, r.Method)
		}
		if want := "/api/ni/groups/applications/" + appID + "/tiers"; r.Path != want {
			t.Errorf("listApplicationTiers call %d path = %q, want %q", i, r.Path, want)
		}
		if len(r.Body) != 0 {
			t.Errorf("listApplicationTiers call %d carries a body %q, want none", i, r.Body)
		}
	}
}

func fmtSteps(steps []rollout.Step) string {
	var b strings.Builder
	b.WriteString("[")
	for i, s := range steps {
		if i > 0 {
			b.WriteString("\n       ")
		}
		fmt.Fprintf(&b, "{%s %s %s id=%q code=%d msg=%q}", s.OperationID, s.Target, s.Status, s.EntityID, s.StatusCode, s.Message)
	}
	b.WriteString("]")
	return b.String()
}

// ---------------------------------------------------------------------------
// wire shape
// ---------------------------------------------------------------------------

func TestRequestWireShape(t *testing.T) {
	t.Parallel()
	contract := loadContract(t)

	srv := newServer(t, nil)
	c := rollout.NewClient(srv.URL(), testToken, srv.Client())
	if _, err := c.Apply(context.Background(), threeTierPlan()); err != nil {
		t.Fatalf("Apply: %v", err)
	}

	reqs := srv.Requests()
	if len(reqs) == 0 {
		t.Fatal("client sent no requests")
	}

	t.Run("every call is a contract operation", func(t *testing.T) {
		allowed := map[string]bool{}
		for _, op := range contract.Operations {
			allowed[op.Method+" "+op.Path] = true
		}
		for i, r := range reqs {
			if r.OperationID == "" {
				t.Errorf("request %d (%s %s) is not an operation named by docs/contract.json", i, r.Method, r.Path)
			}
			if r.RawQuery != "" {
				t.Errorf("request %d (%s %s) carries query %q; the contract declares no query parameters for these operations",
					i, r.Method, r.Path, r.RawQuery)
			}
			if !strings.HasPrefix(r.Path, contract.Server.BasePath+"/") {
				t.Errorf("request %d path %q does not start with the contract base path %q", i, r.Path, contract.Server.BasePath)
			}
		}
	})

	t.Run("authorization header", func(t *testing.T) {
		want := strings.Replace(contract.Security.ValueFormat, "{token}", testToken, 1)
		for i, r := range reqs {
			if got := r.Header.Get(contract.Security.Name); got != want {
				t.Errorf("request %d %s header = %q, want %q", i, contract.Security.Name, got, want)
			}
		}
	})

	t.Run("addApplication", func(t *testing.T) {
		got := srv.RequestsFor("addApplication")
		if len(got) != 1 {
			t.Fatalf("addApplication called %d times, want 1", len(got))
		}
		r := got[0]
		if r.Method != http.MethodPost {
			t.Errorf("method = %s, want POST", r.Method)
		}
		if want := contract.Server.BasePath + "/groups/applications"; r.Path != want {
			t.Errorf("path = %q, want %q", r.Path, want)
		}
		if ct := r.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
			t.Errorf("Content-Type = %q, want application/json", ct)
		}
		body, err := r.DecodeBody()
		if err != nil {
			t.Fatal(err)
		}
		// ApplicationRequest declares exactly one property.
		assertExactKeys(t, "addApplication body", body, "name")
		if body["name"] != "payments-prod" {
			t.Errorf("name = %v, want payments-prod", body["name"])
		}
	})

	t.Run("addTier", func(t *testing.T) {
		got := srv.RequestsFor("addTier")
		if len(got) != 3 {
			t.Fatalf("addTier called %d times, want 3", len(got))
		}

		// TierRequest's properties are all optional. A field the plan does not
		// set must be absent from the JSON object, not present-and-empty.
		alwaysAbsent := []string{"entity_id", "member_list", "source_group_entity_id"}

		cases := []struct {
			tier         string
			wantCriteria []map[string]any
		}{
			{
				tier: "payments-web",
				wantCriteria: []map[string]any{{
					"membership_type": "SearchMembershipCriteria",
					"search_membership_criteria": map[string]any{
						"entity_type": "VirtualMachine",
						"filter":      "security_groups.entity_id = '18230:82:604573173'",
					},
				}},
			},
			{
				tier: "payments-app",
				wantCriteria: []map[string]any{{
					"membership_type": "IPAddressMembershipCriteria",
					"ip_address_membership_criteria": map[string]any{
						"ip_addresses": []any{"10.0.0.1", "10.0.0.0/24", "10.0.0.1-10.0.0.200"},
					},
				}},
			},
			{
				tier: "payments-db",
				wantCriteria: []map[string]any{
					{
						"membership_type": "SearchMembershipCriteria",
						"search_membership_criteria": map[string]any{
							"entity_type": "VirtualMachine",
							"filter":      "name like 'pay-db'",
						},
					},
					{
						"membership_type": "IPAddressMembershipCriteria",
						"ip_address_membership_criteria": map[string]any{
							"ip_addresses": []any{"10.9.0.0/24"},
						},
					},
				},
			},
		}

		for i, tc := range cases {
			r := got[i]
			label := "addTier[" + tc.tier + "]"

			if r.Method != http.MethodPost {
				t.Errorf("%s method = %s, want POST", label, r.Method)
			}
			if want := contract.Server.BasePath + "/groups/applications/" + appID + "/tiers"; r.Path != want {
				t.Errorf("%s path = %q, want %q", label, r.Path, want)
			}
			if ct := r.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
				t.Errorf("%s Content-Type = %q, want application/json", label, ct)
			}

			body, err := r.DecodeBody()
			if err != nil {
				t.Fatalf("%s: %v", label, err)
			}
			assertExactKeys(t, label+" body", body, "name", "group_membership_criteria")
			for _, k := range alwaysAbsent {
				if v, present := body[k]; present {
					t.Errorf("%s body sends unset optional field %q as %#v; it must be omitted", label, k, v)
				}
			}
			if body["name"] != tc.tier {
				t.Errorf("%s name = %v, want %v", label, body["name"], tc.tier)
			}

			raw, ok := body["group_membership_criteria"].([]any)
			if !ok {
				t.Fatalf("%s group_membership_criteria is %T, want a JSON array", label, body["group_membership_criteria"])
			}
			if len(raw) != len(tc.wantCriteria) {
				t.Fatalf("%s has %d criteria, want %d", label, len(raw), len(tc.wantCriteria))
			}
			for j, want := range tc.wantCriteria {
				crit, ok := raw[j].(map[string]any)
				if !ok {
					t.Fatalf("%s criterion %d is %T, want a JSON object", label, j, raw[j])
				}
				critLabel := fmt.Sprintf("%s criterion %d", label, j)
				// Only the discriminator and the matching criteria body: the
				// other criteria field must be omitted, not sent as null.
				assertExactKeys(t, critLabel, crit, keysOf(want)...)
				if !reflect.DeepEqual(normalize(crit), normalize(want)) {
					t.Errorf("%s = %#v, want %#v", critLabel, normalize(crit), normalize(want))
				}
			}
		}
	})
}

func TestUnsetTierRequestFieldsAreAbsent(t *testing.T) {
	t.Parallel()

	srv := newServer(t, nil)
	c := rollout.NewClient(srv.URL(), testToken, srv.Client())
	plan := rollout.Plan{
		ApplicationName: "optional-fields",
		Tiers:           []rollout.TierPlan{{}},
	}
	if _, err := c.Apply(context.Background(), plan); err != nil {
		t.Fatalf("Apply: %v", err)
	}

	reqs := srv.RequestsFor("addTier")
	if len(reqs) != 1 {
		t.Fatalf("addTier called %d times, want 1", len(reqs))
	}
	body, err := reqs[0].DecodeBody()
	if err != nil {
		t.Fatal(err)
	}
	assertExactKeys(t, "unset TierRequest body", body)
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

func keysOf(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func assertExactKeys(t *testing.T, label string, m map[string]any, want ...string) {
	t.Helper()
	got := keysOf(m)
	w := append([]string{}, want...)
	sort.Strings(w)
	if !reflect.DeepEqual(got, w) {
		t.Errorf("%s keys = %v, want exactly %v", label, got, w)
	}
}

// normalize converts json.Number values to strings so decoded bodies compare
// cleanly against literal expectations.
func normalize(v any) any {
	switch x := v.(type) {
	case map[string]any:
		out := make(map[string]any, len(x))
		for k, vv := range x {
			out[k] = normalize(vv)
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, vv := range x {
			out[i] = normalize(vv)
		}
		return out
	case json.Number:
		return x.String()
	default:
		return v
	}
}

type roundTripperFunc func(*http.Request) (*http.Response, error)

func (f roundTripperFunc) RoundTrip(r *http.Request) (*http.Response, error) {
	return f(r)
}
