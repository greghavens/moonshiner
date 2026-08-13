// Package verification is the protected acceptance suite for the sddcmanager
// package. It drives the client against the contract-pinned loopback mock and
// asserts both the reported outcome of a partially applied change and the exact
// wire shape of every request the client made.
//
// No live VMware endpoint is contacted.
package verification

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"reflect"
	"strings"
	"testing"
	"time"

	"example.com/vcf-sddc-onboarding/internal/contractmock"
	"example.com/vcf-sddc-onboarding/sddcmanager"
)

var (
	wantIPPoolTarget = "/v1/network-pools/" + url.PathEscape(contractmock.NetworkPoolID) +
		"/networks/" + url.PathEscape(contractmock.VsanNetworkID) + "/ip-pools"
	wantTaskTarget = "/v1/tasks/" + url.PathEscape(contractmock.TaskID)

	wantPersistedPool   = "network-pool:" + contractmock.NetworkPoolID
	wantPersistedIPPool = "ip-pool:" + contractmock.VsanNetworkID + ":172.20.32.20-172.20.32.60"
	wantPersistedHostA  = "host:" + contractmock.HostA
	wantPersistedHostB  = "host:" + contractmock.HostB
)

func str(s string) *string { return &s }

// testPlan is the change every case applies. Host A carries an SSL thumbprint
// and host B carries none, so per-element omission cannot be faked with a
// single global switch.
func testPlan() sddcmanager.Plan {
	return sddcmanager.Plan{
		NetworkPoolName: "np-ops-a01",
		Networks: []sddcmanager.NetworkSpec{
			{Type: "VMOTION", VlanID: 1631, MTU: 9000, Subnet: "172.20.31.0", Mask: "255.255.255.0", Gateway: "172.20.31.1"},
			{Type: "VSAN", VlanID: 1632, MTU: 9000, Subnet: "172.20.32.0", Mask: "255.255.255.0", Gateway: "172.20.32.1"},
		},
		IPRangeNetworkType: "VSAN",
		IPRange:            sddcmanager.IPRange{Start: "172.20.32.20", End: "172.20.32.60"},
		Hosts: []sddcmanager.HostSpec{
			{
				FQDN: contractmock.HostA, Username: "root", Password: "VMw@re1!VMw@re1!",
				StorageType:   "VSAN",
				SSLThumbprint: str("AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD"),
			},
			{
				FQDN: contractmock.HostB, Username: "root", Password: "VMw@re1!VMw@re1!",
				StorageType: "VSAN",
			},
		},
	}
}

func newClient(t *testing.T, srv *contractmock.Server) *sddcmanager.Client {
	t.Helper()
	c, err := sddcmanager.NewClient(srv.URL, contractmock.AccessToken, srv.Client())
	if err != nil {
		t.Fatalf("NewClient(%q, <token>, mock client) returned error: %v", srv.URL, err)
	}
	if c == nil {
		t.Fatal("NewClient returned a nil client and a nil error")
	}
	return c
}

type wantStep struct {
	operationID string
	status      sddcmanager.StepStatus
	// detailContains, when set, must appear in the step's Detail.
	detailContains string
}

// TestOnboardReportsPartiallyAppliedChange is the core table: for each failure
// point it pins how far the change got, what the appliance was left holding,
// and which steps never ran.
func TestOnboardReportsPartiallyAppliedChange(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name     string
		scenario contractmock.Scenario

		wantOps   []string
		wantSteps []wantStep

		wantSucceeded          bool
		wantNetworkPoolCreated bool
		wantNetworkPoolID      string
		wantNetworkID          string
		wantIPRangeAdded       bool
		wantTaskID             string
		wantTaskStatus         string
		wantHosts              []sddcmanager.HostOutcome
		wantPersisted          []string

		checkErr func(t *testing.T, err error)
	}{
		{
			name:     "whole change applies",
			scenario: contractmock.ScenarioAllSucceed,
			wantOps: []string{
				"createNetworkPool", "addIpPoolToNetworkOfNetworkPool", "commissionHosts", "getTask", "getTask",
			},
			wantSteps: []wantStep{
				{operationID: "createNetworkPool", status: sddcmanager.StepSucceeded},
				{operationID: "addIpPoolToNetworkOfNetworkPool", status: sddcmanager.StepSucceeded},
				{operationID: "commissionHosts", status: sddcmanager.StepSucceeded},
				{operationID: "getTask", status: sddcmanager.StepSucceeded},
			},
			wantSucceeded:          true,
			wantNetworkPoolCreated: true,
			wantNetworkPoolID:      contractmock.NetworkPoolID,
			wantNetworkID:          contractmock.VsanNetworkID,
			wantIPRangeAdded:       true,
			wantTaskID:             contractmock.TaskID,
			wantTaskStatus:         "SUCCESSFUL",
			wantHosts: []sddcmanager.HostOutcome{
				{FQDN: contractmock.HostA, Status: "SUCCESSFUL"},
				{FQDN: contractmock.HostB, Status: "SUCCESSFUL"},
			},
			wantPersisted: []string{wantPersistedPool, wantPersistedIPPool, wantPersistedHostA, wantPersistedHostB},
			checkErr: func(t *testing.T, err error) {
				if err != nil {
					t.Fatalf("Onboard returned error %v, want nil", err)
				}
			},
		},
		{
			// The last step fails asynchronously: HTTP 202 accepted the work and
			// every call returned a success status, but the task settled FAILED
			// with one host in and one host out. The two earlier steps stand.
			name:     "commission task fails after earlier steps applied",
			scenario: contractmock.ScenarioCommissionTaskFails,
			wantOps: []string{
				"createNetworkPool", "addIpPoolToNetworkOfNetworkPool", "commissionHosts", "getTask", "getTask",
			},
			wantSteps: []wantStep{
				{operationID: "createNetworkPool", status: sddcmanager.StepSucceeded},
				{operationID: "addIpPoolToNetworkOfNetworkPool", status: sddcmanager.StepSucceeded},
				{operationID: "commissionHosts", status: sddcmanager.StepSucceeded},
				{
					operationID:    "getTask",
					status:         sddcmanager.StepFailed,
					detailContains: contractmock.TaskFailureErrorCode,
				},
			},
			wantSucceeded:          false,
			wantNetworkPoolCreated: true,
			wantNetworkPoolID:      contractmock.NetworkPoolID,
			wantNetworkID:          contractmock.VsanNetworkID,
			wantIPRangeAdded:       true,
			wantTaskID:             contractmock.TaskID,
			wantTaskStatus:         "FAILED",
			wantHosts: []sddcmanager.HostOutcome{
				{FQDN: contractmock.HostA, Status: "SUCCESSFUL"},
				{
					FQDN:      contractmock.HostB,
					Status:    "FAILED",
					ErrorCode: contractmock.HostBErrorCode,
					Message:   contractmock.HostBErrorMessage,
				},
			},
			// Host B never joined the inventory, so it is not left behind.
			wantPersisted: []string{wantPersistedPool, wantPersistedIPPool, wantPersistedHostA},
			checkErr: func(t *testing.T, err error) {
				var cfe *sddcmanager.CommissionFailedError
				if !errors.As(err, &cfe) {
					t.Fatalf("Onboard returned %#v, want a *CommissionFailedError", err)
				}
				if cfe.TaskID != contractmock.TaskID {
					t.Errorf("CommissionFailedError.TaskID = %q, want %q", cfe.TaskID, contractmock.TaskID)
				}
				if cfe.TaskStatus != "FAILED" {
					t.Errorf("CommissionFailedError.TaskStatus = %q, want %q", cfe.TaskStatus, "FAILED")
				}
				if cfe.ErrorCode != contractmock.TaskFailureErrorCode {
					t.Errorf("CommissionFailedError.ErrorCode = %q, want %q", cfe.ErrorCode, contractmock.TaskFailureErrorCode)
				}
				if cfe.Message != contractmock.TaskFailureMessage {
					t.Errorf("CommissionFailedError.Message = %q, want %q", cfe.Message, contractmock.TaskFailureMessage)
				}
				want := []sddcmanager.HostOutcome{
					{FQDN: contractmock.HostA, Status: "SUCCESSFUL"},
					{FQDN: contractmock.HostB, Status: "FAILED", ErrorCode: contractmock.HostBErrorCode, Message: contractmock.HostBErrorMessage},
				}
				if !reflect.DeepEqual(cfe.Hosts, want) {
					t.Errorf("CommissionFailedError.Hosts = %#v, want %#v", cfe.Hosts, want)
				}
			},
		},
		{
			// A middle step fails outright. The pool is already created, so the
			// report has to own it, and the steps after must not run at all.
			name:     "ip range rejected after pool created",
			scenario: contractmock.ScenarioIPPoolRejected,
			wantOps:  []string{"createNetworkPool", "addIpPoolToNetworkOfNetworkPool"},
			wantSteps: []wantStep{
				{operationID: "createNetworkPool", status: sddcmanager.StepSucceeded},
				{
					operationID:    "addIpPoolToNetworkOfNetworkPool",
					status:         sddcmanager.StepFailed,
					detailContains: contractmock.IPPoolRejectedErrorCode,
				},
				{operationID: "commissionHosts", status: sddcmanager.StepSkipped},
				{operationID: "getTask", status: sddcmanager.StepSkipped},
			},
			wantSucceeded:          false,
			wantNetworkPoolCreated: true,
			wantNetworkPoolID:      contractmock.NetworkPoolID,
			wantNetworkID:          contractmock.VsanNetworkID,
			wantIPRangeAdded:       false,
			wantTaskID:             "",
			wantTaskStatus:         "",
			wantHosts:              nil,
			wantPersisted:          []string{wantPersistedPool},
			checkErr: func(t *testing.T, err error) {
				var ae *sddcmanager.APIError
				if !errors.As(err, &ae) {
					t.Fatalf("Onboard returned %#v, want an *APIError", err)
				}
				if ae.OperationID != "addIpPoolToNetworkOfNetworkPool" {
					t.Errorf("APIError.OperationID = %q, want %q", ae.OperationID, "addIpPoolToNetworkOfNetworkPool")
				}
				if ae.StatusCode != http.StatusBadRequest {
					t.Errorf("APIError.StatusCode = %d, want %d", ae.StatusCode, http.StatusBadRequest)
				}
				if ae.ErrorCode != contractmock.IPPoolRejectedErrorCode {
					t.Errorf("APIError.ErrorCode = %q, want %q", ae.ErrorCode, contractmock.IPPoolRejectedErrorCode)
				}
				if ae.Message != contractmock.IPPoolRejectedMessage {
					t.Errorf("APIError.Message = %q, want %q", ae.Message, contractmock.IPPoolRejectedMessage)
				}
			},
		},
		{
			// The first step fails, so nothing was applied and nothing persists.
			name:     "network pool rejected",
			scenario: contractmock.ScenarioNetworkPoolRejected,
			wantOps:  []string{"createNetworkPool"},
			wantSteps: []wantStep{
				{
					operationID:    "createNetworkPool",
					status:         sddcmanager.StepFailed,
					detailContains: contractmock.NetworkPoolRejectedErrorCode,
				},
				{operationID: "addIpPoolToNetworkOfNetworkPool", status: sddcmanager.StepSkipped},
				{operationID: "commissionHosts", status: sddcmanager.StepSkipped},
				{operationID: "getTask", status: sddcmanager.StepSkipped},
			},
			wantSucceeded:          false,
			wantNetworkPoolCreated: false,
			wantNetworkPoolID:      "",
			wantNetworkID:          "",
			wantIPRangeAdded:       false,
			wantTaskID:             "",
			wantTaskStatus:         "",
			wantHosts:              nil,
			wantPersisted:          nil,
			checkErr: func(t *testing.T, err error) {
				var ae *sddcmanager.APIError
				if !errors.As(err, &ae) {
					t.Fatalf("Onboard returned %#v, want an *APIError", err)
				}
				if ae.OperationID != "createNetworkPool" {
					t.Errorf("APIError.OperationID = %q, want %q", ae.OperationID, "createNetworkPool")
				}
				if ae.StatusCode != http.StatusBadRequest {
					t.Errorf("APIError.StatusCode = %d, want %d", ae.StatusCode, http.StatusBadRequest)
				}
				if ae.ErrorCode != contractmock.NetworkPoolRejectedErrorCode {
					t.Errorf("APIError.ErrorCode = %q, want %q", ae.ErrorCode, contractmock.NetworkPoolRejectedErrorCode)
				}
				if ae.Message != contractmock.NetworkPoolRejectedMessage {
					t.Errorf("APIError.Message = %q, want %q", ae.Message, contractmock.NetworkPoolRejectedMessage)
				}
			},
		},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			srv := contractmock.Start(t, tc.scenario)
			client := newClient(t, srv)

			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()

			report, err := client.Onboard(ctx, testPlan(), time.Millisecond)
			tc.checkErr(t, err)

			assertNoViolations(t, srv)
			assertOperationSequence(t, srv, tc.wantOps)
			assertSteps(t, report.Steps, tc.wantSteps)

			if report.Succeeded != tc.wantSucceeded {
				t.Errorf("Report.Succeeded = %t, want %t", report.Succeeded, tc.wantSucceeded)
			}
			if report.NetworkPoolCreated != tc.wantNetworkPoolCreated {
				t.Errorf("Report.NetworkPoolCreated = %t, want %t", report.NetworkPoolCreated, tc.wantNetworkPoolCreated)
			}
			if report.NetworkPoolID != tc.wantNetworkPoolID {
				t.Errorf("Report.NetworkPoolID = %q, want %q", report.NetworkPoolID, tc.wantNetworkPoolID)
			}
			if report.NetworkID != tc.wantNetworkID {
				t.Errorf("Report.NetworkID = %q, want %q", report.NetworkID, tc.wantNetworkID)
			}
			if report.IPRangeAdded != tc.wantIPRangeAdded {
				t.Errorf("Report.IPRangeAdded = %t, want %t", report.IPRangeAdded, tc.wantIPRangeAdded)
			}
			if report.TaskID != tc.wantTaskID {
				t.Errorf("Report.TaskID = %q, want %q", report.TaskID, tc.wantTaskID)
			}
			if report.TaskStatus != tc.wantTaskStatus {
				t.Errorf("Report.TaskStatus = %q, want %q", report.TaskStatus, tc.wantTaskStatus)
			}
			if !reflect.DeepEqual(report.Hosts, tc.wantHosts) && !(len(report.Hosts) == 0 && len(tc.wantHosts) == 0) {
				t.Errorf("Report.Hosts =\n  %#v\nwant\n  %#v", report.Hosts, tc.wantHosts)
			}
			if !equalStrings(report.PersistedResources, tc.wantPersisted) {
				t.Errorf("Report.PersistedResources = %q, want %q", report.PersistedResources, tc.wantPersisted)
			}
		})
	}
}

// TestRequestWireShape pins the exact bytes of the change. It runs the failing
// scenario so that all five requests are on the wire.
func TestRequestWireShape(t *testing.T) {
	t.Parallel()

	srv := contractmock.Start(t, contractmock.ScenarioCommissionTaskFails)
	client := newClient(t, srv)

	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()

	if _, err := client.Onboard(ctx, testPlan(), time.Millisecond); err == nil {
		t.Fatal("Onboard returned a nil error for a task that ended FAILED")
	}
	assertNoViolations(t, srv)

	got := srv.Requests()
	if len(got) != 5 {
		t.Fatalf("mock logged %d requests, want 5: %s", len(got), summarize(got))
	}

	wantTargets := []struct {
		method string
		target string
	}{
		{http.MethodPost, "/v1/network-pools"},
		{http.MethodPost, wantIPPoolTarget},
		{http.MethodPost, "/v1/hosts"},
		{http.MethodGet, wantTaskTarget},
		{http.MethodGet, wantTaskTarget},
	}
	for i, want := range wantTargets {
		if got[i].Method != want.method {
			t.Errorf("request %d method = %s, want %s", i, got[i].Method, want.method)
		}
		if got[i].RawTarget != want.target {
			t.Errorf("request %d raw target = %q, want %q", i, got[i].RawTarget, want.target)
		}
		if got[i].Query != "" || strings.Contains(got[i].RawTarget, "?") {
			t.Errorf("request %d carries a query string: %q", i, got[i].RawTarget)
		}
		assertSingleHeader(t, i, got[i], "Authorization", "Bearer "+contractmock.AccessToken)
		assertSingleHeader(t, i, got[i], "Accept", "application/json")
	}

	for i := 0; i < 3; i++ {
		assertSingleHeader(t, i, got[i], "Content-Type", "application/json")
	}
	for _, i := range []int{3, 4} {
		if len(got[i].Body) != 0 {
			t.Errorf("request %d (getTask) carries a %d byte body, want none", i, len(got[i].Body))
		}
		if v := got[i].Header.Values("Content-Type"); len(v) != 0 {
			t.Errorf("request %d (getTask) has no body but sent Content-Type %q", i, v)
		}
	}

	// createNetworkPool: every writable Network member is required by the
	// 9.0.0.0 revision, no readOnly member is sent, and the unset optional
	// ipPools member is absent rather than an empty array.
	assertJSONBody(t, got[0], "createNetworkPool", `{
		"name": "np-ops-a01",
		"networks": [
			{"type":"VMOTION","vlanId":1631,"mtu":9000,"subnet":"172.20.31.0","mask":"255.255.255.0","gateway":"172.20.31.1"},
			{"type":"VSAN","vlanId":1632,"mtu":9000,"subnet":"172.20.32.0","mask":"255.255.255.0","gateway":"172.20.32.1"}
		]
	}`)

	assertJSONBody(t, got[1], "addIpPoolToNetworkOfNetworkPool", `{
		"start": "172.20.32.20",
		"end": "172.20.32.60"
	}`)

	// commissionHosts: a bare array, networkPoolId threaded from the create
	// response, and optional members omitted per element. Host A carries only
	// the SSL thumbprint it was given; host B carries no optional member.
	assertJSONBody(t, got[2], "commissionHosts", `[
		{
			"fqdn": "`+contractmock.HostA+`",
			"username": "root",
			"password": "VMw@re1!VMw@re1!",
			"storageType": "VSAN",
			"networkPoolId": "`+contractmock.NetworkPoolID+`",
			"sslThumbprint": "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD"
		},
		{
			"fqdn": "`+contractmock.HostB+`",
			"username": "root",
			"password": "VMw@re1!VMw@re1!",
			"storageType": "VSAN",
			"networkPoolId": "`+contractmock.NetworkPoolID+`"
		}
	]`)
}

// TestNewClientRejectsUnusableInput keeps the constructor honest.
func TestNewClientRejectsUnusableInput(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name    string
		baseURL string
		token   string
	}{
		{name: "empty base url", baseURL: "", token: contractmock.AccessToken},
		{name: "missing scheme", baseURL: "sddc.vcf.local", token: contractmock.AccessToken},
		{name: "http url missing host", baseURL: "http://", token: contractmock.AccessToken},
		{name: "non http scheme", baseURL: "ftp://sddc.vcf.local", token: contractmock.AccessToken},
		{name: "blank access token", baseURL: "https://sddc.vcf.local", token: "   "},
		{name: "header unsafe access token", baseURL: "https://sddc.vcf.local", token: "abc\ndef"},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if _, err := sddcmanager.NewClient(tc.baseURL, tc.token, nil); err == nil {
				t.Fatalf("NewClient(%q, %q, nil) returned a nil error", tc.baseURL, tc.token)
			}
		})
	}

	if _, err := sddcmanager.NewClient("HTTP://sddc.vcf.local", contractmock.AccessToken, &http.Client{}); err != nil {
		t.Errorf("NewClient rejected an HTTP URL with a case-insensitive scheme: %v", err)
	}
	unsafeToken := "header-secret\r\ninjected"
	_, err := sddcmanager.NewClient("https://sddc.vcf.local", unsafeToken, nil)
	if err == nil {
		t.Fatal("NewClient accepted a CRLF-bearing access token")
	}
	if strings.Contains(err.Error(), "header-secret") {
		t.Errorf("NewClient error exposes the rejected access token: %q", err)
	}
}

func assertSteps(t *testing.T, got []sddcmanager.StepReport, want []wantStep) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("Report.Steps has %d entries, want %d: %#v", len(got), len(want), got)
	}
	for i := range want {
		if got[i].OperationID != want[i].operationID {
			t.Errorf("Report.Steps[%d].OperationID = %q, want %q", i, got[i].OperationID, want[i].operationID)
		}
		if got[i].Status != want[i].status {
			t.Errorf("Report.Steps[%d] (%s).Status = %q, want %q",
				i, want[i].operationID, got[i].Status, want[i].status)
		}
		if want[i].detailContains != "" && !strings.Contains(got[i].Detail, want[i].detailContains) {
			t.Errorf("Report.Steps[%d] (%s).Detail = %q, want it to mention %q",
				i, want[i].operationID, got[i].Detail, want[i].detailContains)
		}
	}
}

func assertOperationSequence(t *testing.T, srv *contractmock.Server, want []string) {
	t.Helper()
	got := srv.Requests()
	var ops []string
	for _, r := range got {
		ops = append(ops, r.OperationID)
	}
	if !equalStrings(ops, want) {
		t.Fatalf("mock served operations %q, want %q\n%s", ops, want, summarize(got))
	}
}

func assertNoViolations(t *testing.T, srv *contractmock.Server) {
	t.Helper()
	for i, r := range srv.Requests() {
		if r.Violation != "" {
			t.Errorf("request %d (%s %s) violated the contract: %s", i, r.Method, r.RawTarget, r.Violation)
		}
	}
}

func assertSingleHeader(t *testing.T, i int, r contractmock.Request, name, want string) {
	t.Helper()
	got := r.Header.Values(name)
	if len(got) != 1 {
		t.Errorf("request %d (%s %s) sent %d %s headers (%q), want exactly 1",
			i, r.Method, r.RawTarget, len(got), name, got)
		return
	}
	if got[0] != want {
		t.Errorf("request %d (%s %s) %s = %q, want %q", i, r.Method, r.RawTarget, name, got[0], want)
	}
}

// assertJSONBody compares the decoded request body against wantJSON. Member
// order is irrelevant; the member set is not, so any extra member such as a
// readOnly field, a 9.1-only field, or an optional field encoded as "" or null
// fails here.
func assertJSONBody(t *testing.T, r contractmock.Request, label, wantJSON string) {
	t.Helper()

	var got, want any
	if err := json.Unmarshal(r.Body, &got); err != nil {
		t.Errorf("%s body is not valid JSON (%v): %s", label, err, r.Body)
		return
	}
	if err := json.Unmarshal([]byte(wantJSON), &want); err != nil {
		t.Fatalf("%s: malformed expectation in the verifier: %v", label, err)
	}
	if reflect.DeepEqual(got, want) {
		return
	}

	gotPretty, _ := json.MarshalIndent(got, "", "  ")
	wantPretty, _ := json.MarshalIndent(want, "", "  ")
	t.Errorf("%s request body mismatch\ngot:\n%s\nwant:\n%s\n%s",
		label, gotPretty, wantPretty, explainDiff(got, want))
}

// explainDiff points at the members that differ, which is usually an optional
// field that was sent empty instead of being left out.
func explainDiff(got, want any) string {
	gotObj, gotOK := got.(map[string]any)
	wantObj, wantOK := want.(map[string]any)
	if gotOK && wantOK {
		return explainObject("", gotObj, wantObj)
	}

	gotArr, gotOK := got.([]any)
	wantArr, wantOK := want.([]any)
	if !gotOK || !wantOK {
		return ""
	}
	if len(gotArr) != len(wantArr) {
		return fmt.Sprintf("array has %d elements, want %d", len(gotArr), len(wantArr))
	}
	var b strings.Builder
	for i := range gotArr {
		a, aOK := gotArr[i].(map[string]any)
		e, eOK := wantArr[i].(map[string]any)
		if aOK && eOK {
			b.WriteString(explainObject(fmt.Sprintf("[%d]", i), a, e))
		}
	}
	return b.String()
}

func explainObject(prefix string, got, want map[string]any) string {
	var b strings.Builder
	for k, v := range got {
		if _, expected := want[k]; !expected {
			b.WriteString(fmt.Sprintf("unexpected member %s.%s = %#v (an unset optional member must be omitted)\n", prefix, k, v))
		}
	}
	for k, v := range want {
		if _, present := got[k]; !present {
			b.WriteString(fmt.Sprintf("missing member %s.%s, want %#v\n", prefix, k, v))
		}
	}
	return b.String()
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func summarize(rs []contractmock.Request) string {
	var b strings.Builder
	b.WriteString("request log:\n")
	for i, r := range rs {
		op := r.OperationID
		if op == "" {
			op = "<no contract operation>"
		}
		fmt.Fprintf(&b, "  %d %s %s -> %s", i, r.Method, r.RawTarget, op)
		if r.Violation != "" {
			fmt.Fprintf(&b, " [%s]", r.Violation)
		}
		b.WriteString("\n")
	}
	return b.String()
}
