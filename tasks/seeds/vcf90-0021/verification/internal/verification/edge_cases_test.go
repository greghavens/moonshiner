package verification

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"testing"
	"time"

	"example.com/vcf-sddc-onboarding/sddcmanager"
)

type responseStep struct {
	status int
	body   any
	check  func(*http.Request) error
}

type sequenceTransport struct {
	steps []responseStep
	calls int
}

func (s *sequenceTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if s.calls >= len(s.steps) {
		return nil, fmt.Errorf("unexpected request %s %s", req.Method, req.URL.RequestURI())
	}
	step := s.steps[s.calls]
	s.calls++
	if step.check != nil {
		if err := step.check(req); err != nil {
			return nil, err
		}
	}
	raw, err := json.Marshal(step.body)
	if err != nil {
		return nil, err
	}
	return &http.Response{
		StatusCode: step.status,
		Status:     fmt.Sprintf("%d %s", step.status, http.StatusText(step.status)),
		Header:     http.Header{"Content-Type": []string{"application/json"}},
		Body:       io.NopCloser(strings.NewReader(string(raw))),
		Request:    req,
	}, nil
}

func clientForSequence(t *testing.T, token string, transport *sequenceTransport) *sddcmanager.Client {
	t.Helper()
	client, err := sddcmanager.NewClient("http://sddc.test", token, &http.Client{Transport: transport})
	if err != nil {
		t.Fatalf("NewClient returned error: %v", err)
	}
	return client
}

func createResponse(poolID, networkID string) map[string]any {
	// The target network is first even though it is second in testPlan. Position
	// is therefore not a usable substitute for matching the response's type.
	return map[string]any{
		"id": poolID,
		"networks": []any{
			map[string]any{"id": networkID, "type": "VSAN"},
			map[string]any{"id": "vmotion/id", "type": "VMOTION"},
		},
	}
}

func taskBody(id, status string) map[string]any {
	return map[string]any{
		"id":                id,
		"name":              "Commissioning Hosts",
		"status":            status,
		"creationTimestamp": "2025-06-17T09:14:02.331Z",
	}
}

func TestTerminalAcceptedTaskIsStillReadAndESXiResourceIsSelected(t *testing.T) {
	poolID := "pool/with?reserved"
	networkID := "network/with#reserved"
	taskID := "task/with?reserved"
	hostFQDN := "esx-edge.vcf.local"

	settled := taskBody(taskID, " \t successful  ")
	settled["subTasks"] = []any{map[string]any{
		"status": " successful ",
		"resources": []any{
			map[string]any{"resourceId": "management-1", "type": "MANAGEMENT", "fqdn": "not-an-esxi-host.vcf.local"},
			map[string]any{"resourceId": "esxi-1", "type": "ESXI", "fqdn": hostFQDN},
		},
	}}

	transport := &sequenceTransport{steps: []responseStep{
		{status: http.StatusCreated, body: createResponse(poolID, networkID)},
		{status: http.StatusOK, body: map[string]any{}, check: func(req *http.Request) error {
			want := "/v1/network-pools/" + url.PathEscape(poolID) + "/networks/" + url.PathEscape(networkID) + "/ip-pools"
			if req.URL.EscapedPath() != want {
				return fmt.Errorf("IP-pool escaped path = %q, want %q", req.URL.EscapedPath(), want)
			}
			return nil
		}},
		{status: http.StatusAccepted, body: taskBody(taskID, " successful ")},
		{status: http.StatusOK, body: settled, check: func(req *http.Request) error {
			want := "/v1/tasks/" + url.PathEscape(taskID)
			if req.URL.EscapedPath() != want {
				return fmt.Errorf("task escaped path = %q, want %q", req.URL.EscapedPath(), want)
			}
			return nil
		}},
	}}

	report, err := clientForSequence(t, "fixture-token", transport).Onboard(context.Background(), testPlan(), 0)
	if err != nil {
		t.Fatalf("Onboard returned error: %v", err)
	}
	if transport.calls != 4 {
		t.Fatalf("request count = %d, want 4 including one mandatory getTask", transport.calls)
	}
	if report.TaskStatus != "SUCCESSFUL" || !report.Succeeded {
		t.Fatalf("task outcome = (%q, %t), want (SUCCESSFUL, true)", report.TaskStatus, report.Succeeded)
	}
	wantHosts := []sddcmanager.HostOutcome{{FQDN: hostFQDN, Status: "SUCCESSFUL"}}
	if len(report.Hosts) != 1 || report.Hosts[0] != wantHosts[0] {
		t.Fatalf("Report.Hosts = %#v, want %#v", report.Hosts, wantHosts)
	}
}

func TestCancellationReportsLastPolledStatusPromptly(t *testing.T) {
	const (
		poolID    = "pool-id"
		networkID = "network-id"
		taskID    = "task-id"
	)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	transport := &sequenceTransport{steps: []responseStep{
		{status: http.StatusCreated, body: createResponse(poolID, networkID)},
		{status: http.StatusOK, body: map[string]any{}},
		{status: http.StatusAccepted, body: taskBody(taskID, "queued")},
		{status: http.StatusOK, body: taskBody(taskID, " in   progress "), check: func(*http.Request) error {
			cancel()
			return nil
		}},
	}}

	report, err := clientForSequence(t, "fixture-token", transport).Onboard(ctx, testPlan(), time.Hour)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("Onboard error = %#v, want context.Canceled", err)
	}
	if report.TaskStatus != "IN_PROGRESS" {
		t.Errorf("Report.TaskStatus = %q, want last observed IN_PROGRESS", report.TaskStatus)
	}
	if len(report.Steps) != 4 || report.Steps[3].Status != sddcmanager.StepFailed {
		t.Fatalf("Report.Steps = %#v, want four entries with getTask FAILED", report.Steps)
	}
}

func TestAcceptedTaskRequiresEveryContractFieldAndStillPopulatesReport(t *testing.T) {
	cases := []struct {
		name   string
		member string
	}{
		{name: "id", member: "id"},
		{name: "name", member: "name"},
		{name: "status", member: "status"},
		{name: "creation timestamp", member: "creationTimestamp"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			accepted := taskBody("accepted-task-id", "queued")
			delete(accepted, tc.member)
			transport := &sequenceTransport{steps: []responseStep{
				{status: http.StatusCreated, body: createResponse("pool-id", "network-id")},
				{status: http.StatusOK, body: map[string]any{}},
				{status: http.StatusAccepted, body: accepted},
			}}
			report, err := clientForSequence(t, "fixture-token", transport).Onboard(context.Background(), testPlan(), 0)
			if err == nil {
				t.Fatal("Onboard returned nil error for an incomplete accepted Task")
			}
			if transport.calls != 3 {
				t.Fatalf("request count = %d, want 3 and no getTask", transport.calls)
			}
			if len(report.Steps) != 4 || report.Steps[2].Status != sddcmanager.StepFailed || report.Steps[3].Status != sddcmanager.StepSkipped {
				t.Fatalf("Report.Steps = %#v", report.Steps)
			}
			if tc.member != "id" && report.TaskID != "accepted-task-id" {
				t.Errorf("Report.TaskID = %q, want the id already returned by commissionHosts", report.TaskID)
			}
			if tc.member != "status" && report.TaskStatus != "QUEUED" {
				t.Errorf("Report.TaskStatus = %q, want the status already returned by commissionHosts", report.TaskStatus)
			}
		})
	}
}

func TestEveryNamedUnsuccessfulStatusIsTerminal(t *testing.T) {
	cases := []struct {
		wire string
		want string
	}{
		{wire: " failed ", want: "FAILED"},
		{wire: "cancelled", want: "CANCELLED"},
		{wire: " completed   with\twarning ", want: "COMPLETED_WITH_WARNING"},
		{wire: "skipped", want: "SKIPPED"},
		{wire: "timed out", want: "TIMED_OUT"},
	}
	for _, tc := range cases {
		t.Run(tc.want, func(t *testing.T) {
			terminal := taskBody("task-id", tc.wire)
			terminal["errors"] = []any{map[string]any{"errorCode": "TERMINAL_TASK", "message": "task did not succeed"}}
			transport := &sequenceTransport{steps: []responseStep{
				{status: http.StatusCreated, body: createResponse("pool-id", "network-id")},
				{status: http.StatusOK, body: map[string]any{}},
				{status: http.StatusAccepted, body: taskBody("task-id", "pending")},
				{status: http.StatusOK, body: terminal},
			}}
			report, err := clientForSequence(t, "fixture-token", transport).Onboard(context.Background(), testPlan(), 0)
			var failed *sddcmanager.CommissionFailedError
			if !errors.As(err, &failed) {
				t.Fatalf("Onboard error = %#v, want *CommissionFailedError", err)
			}
			if report.TaskStatus != tc.want || failed.TaskStatus != tc.want {
				t.Fatalf("task statuses = (%q, %q), want %q", report.TaskStatus, failed.TaskStatus, tc.want)
			}
			if transport.calls != 4 {
				t.Fatalf("request count = %d, want 4", transport.calls)
			}
		})
	}
}

func TestEachOperationRequiresItsExactSuccessStatus(t *testing.T) {
	type testCase struct {
		name      string
		operation string
		status    int
		steps     []responseStep
	}
	errorBody := map[string]any{"errorCode": "WRONG_SUCCESS_STATUS", "message": "the status is not the contract status"}
	cases := []testCase{
		{
			name: "createNetworkPool must be 201", operation: "createNetworkPool", status: http.StatusOK,
			steps: []responseStep{{status: http.StatusOK, body: errorBody}},
		},
		{
			name: "addIpPool must be 200", operation: "addIpPoolToNetworkOfNetworkPool", status: http.StatusCreated,
			steps: []responseStep{
				{status: http.StatusCreated, body: createResponse("pool-id", "network-id")},
				{status: http.StatusCreated, body: errorBody},
			},
		},
		{
			name: "commissionHosts must be 202", operation: "commissionHosts", status: http.StatusOK,
			steps: []responseStep{
				{status: http.StatusCreated, body: createResponse("pool-id", "network-id")},
				{status: http.StatusOK, body: map[string]any{}},
				{status: http.StatusOK, body: errorBody},
			},
		},
		{
			name: "getTask must be 200", operation: "getTask", status: http.StatusAccepted,
			steps: []responseStep{
				{status: http.StatusCreated, body: createResponse("pool-id", "network-id")},
				{status: http.StatusOK, body: map[string]any{}},
				{status: http.StatusAccepted, body: taskBody("task-id", "queued")},
				{status: http.StatusAccepted, body: errorBody},
			},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			transport := &sequenceTransport{steps: tc.steps}
			report, err := clientForSequence(t, "fixture-token", transport).Onboard(context.Background(), testPlan(), 0)
			var apiErr *sddcmanager.APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("Onboard error = %#v, want *APIError", err)
			}
			if apiErr.OperationID != tc.operation || apiErr.StatusCode != tc.status || apiErr.ErrorCode != "WRONG_SUCCESS_STATUS" {
				t.Fatalf("APIError = %#v, want operation %q, status %d and ErrorCode WRONG_SUCCESS_STATUS", apiErr, tc.operation, tc.status)
			}
			if len(report.Steps) != 4 {
				t.Fatalf("Report.Steps has %d entries, want 4", len(report.Steps))
			}
		})
	}
}

func TestErrorsNeverExposeAccessTokenOrHostPassword(t *testing.T) {
	const (
		token    = "access-token-must-not-leak"
		password = "host-password-must-not-leak"
	)
	plan := testPlan()
	for i := range plan.Hosts {
		plan.Hosts[i].Password = password
	}
	transport := &sequenceTransport{steps: []responseStep{
		{status: http.StatusCreated, body: createResponse("pool-id", "network-id")},
		{status: http.StatusBadRequest, body: map[string]any{
			"errorCode": "IP_POOL_REJECTED",
			"message":   "reflected " + token + " and " + password,
		}},
	}}

	report, err := clientForSequence(t, token, transport).Onboard(context.Background(), plan, 0)
	var apiErr *sddcmanager.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("Onboard error = %#v, want *APIError", err)
	}
	texts := []string{err.Error(), apiErr.ErrorCode, apiErr.Message, report.Steps[1].Detail}
	for _, text := range texts {
		if strings.Contains(text, token) || strings.Contains(text, password) {
			t.Errorf("error material exposes a secret: %q", text)
		}
	}
}
