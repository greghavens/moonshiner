package verifier_test

import (
	"context"
	"encoding/json"
	"net/http"
	"path/filepath"
	"reflect"
	"testing"

	vcfautomation "example.com/vcfautomation"
	"example.com/vcfautomation/internal/mockvcf"
)

const contractPath = "../docs/contract.json"

func TestSubmitDeploymentActionAndWait_WireAndTerminalStates(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name         string
		requestID    string
		submitStatus string
		pollStatuses []string
	}{
		{
			name:         "successful after every nonterminal state",
			requestID:    "request-successful",
			submitStatus: "CREATED",
			pollStatuses: []string{
				"PENDING",
				"INITIALIZATION",
				"CHECKING_APPROVAL",
				"APPROVAL_PENDING",
				"USER_INTERACTION_PENDING",
				"INPROGRESS",
				"COMPLETION",
				"SUCCESSFUL",
			},
		},
		{
			name:         "failed",
			requestID:    "request-failed",
			submitStatus: "INPROGRESS",
			pollStatuses: []string{"FAILED"},
		},
		{
			name:         "approval rejected",
			requestID:    "request-rejected",
			submitStatus: "CHECKING_APPROVAL",
			pollStatuses: []string{"APPROVAL_PENDING", "APPROVAL_REJECTED"},
		},
		{
			name:         "aborted",
			requestID:    "request-aborted",
			submitStatus: "PENDING",
			pollStatuses: []string{"INPROGRESS", "COMPLETION", "ABORTED"},
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server := mockvcf.New(t, filepath.Clean(contractPath), mockvcf.Scenario{
				RequestID:    test.requestID,
				SubmitStatus: test.submitStatus,
				PollStatuses: test.pollStatuses,
			})
			server.AssertLoopback(t)

			client := vcfautomation.NewClient(server.URL+"/", "fixture-token", serverClient(server), 0)

			got, err := client.SubmitDeploymentActionAndWait(context.Background(), "deployment-7", vcfautomation.ActionRequest{
				ActionID: "Deployment.PowerOff",
			})
			if err != nil {
				t.Fatalf("SubmitDeploymentActionAndWait() error = %v", err)
			}
			terminal := test.pollStatuses[len(test.pollStatuses)-1]
			assertDecodedRequest(t, got, test.requestID, terminal)

			requests := server.Requests()
			wantRequestCount := 1 + len(test.pollStatuses)
			if len(requests) != wantRequestCount {
				t.Fatalf("request count = %d, want %d; log: %v", len(requests), wantRequestCount, requests)
			}
			assertSubmitWire(t, requests[0], `{"actionId":"Deployment.PowerOff"}`)
			for i, request := range requests[1:] {
				assertPollWire(t, request, test.requestID, i)
			}
		})
	}
}

func TestSubmitDeploymentActionAndWait_OptionalFields(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name string
		body vcfautomation.ActionRequest
		want string
	}{
		{
			name: "all unset fields are absent",
			body: vcfautomation.ActionRequest{},
			want: `{}`,
		},
		{
			name: "only action id is set",
			body: vcfautomation.ActionRequest{ActionID: "Deployment.PowerOff"},
			want: `{"actionId":"Deployment.PowerOff"}`,
		},
		{
			name: "set fields are serialized",
			body: vcfautomation.ActionRequest{
				ActionID: "Deployment.Resize",
				Inputs:   map[string]any{"cpu": float64(4)},
				Reason:   "capacity",
			},
			want: `{"actionId":"Deployment.Resize","inputs":{"cpu":4},"reason":"capacity"}`,
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server := mockvcf.New(t, filepath.Clean(contractPath), mockvcf.Scenario{SubmitStatus: "SUCCESSFUL"})
			client := vcfautomation.NewClient(server.URL, "fixture-token", serverClient(server), 0)

			got, err := client.SubmitDeploymentActionAndWait(context.Background(), "deployment-7", test.body)
			if err != nil {
				t.Fatalf("SubmitDeploymentActionAndWait() error = %v", err)
			}
			assertDecodedRequest(t, got, "request-42", "SUCCESSFUL")
			requests := server.Requests()
			if len(requests) != 1 {
				t.Fatalf("immediately terminal submit made %d requests, want 1", len(requests))
			}
			assertSubmitWire(t, requests[0], test.want)
		})
	}
}

func assertSubmitWire(t *testing.T, got mockvcf.LoggedRequest, wantBody string) {
	t.Helper()
	if got.Method != http.MethodPost {
		t.Errorf("submit method = %q, want POST", got.Method)
	}
	if got.Path != "/deployment/api/deployments/deployment-7/requests" {
		t.Errorf("submit path = %q", got.Path)
	}
	if got.RawQuery != "" {
		t.Errorf("submit query = %q, want empty", got.RawQuery)
	}
	if got.Header.Get("Authorization") != "Bearer fixture-token" {
		t.Errorf("submit Authorization = %q", got.Header.Get("Authorization"))
	}
	if got.Header.Get("Accept") != "application/json" {
		t.Errorf("submit Accept = %q", got.Header.Get("Accept"))
	}
	if got.Header.Get("Content-Type") != "application/json" {
		t.Errorf("submit Content-Type = %q", got.Header.Get("Content-Type"))
	}
	if string(got.Body) != wantBody {
		t.Errorf("submit body = %q, want exact %q", got.Body, wantBody)
	}

	var fields map[string]json.RawMessage
	if err := json.Unmarshal(got.Body, &fields); err != nil {
		t.Fatalf("submit body is not JSON: %v", err)
	}
	if wantBody == `{"actionId":"Deployment.PowerOff"}` {
		if want := []string{"actionId"}; !reflect.DeepEqual(sortedKeys(fields), want) {
			t.Errorf("submit JSON fields = %v, want %v; unset inputs and reason must be omitted", sortedKeys(fields), want)
		}
	}
}

func assertPollWire(t *testing.T, got mockvcf.LoggedRequest, requestID string, poll int) {
	t.Helper()
	if got.Method != http.MethodGet {
		t.Errorf("poll %d method = %q, want GET", poll, got.Method)
	}
	if got.Path != "/deployment/api/requests/"+requestID {
		t.Errorf("poll %d path = %q", poll, got.Path)
	}
	if got.RawQuery != "" {
		t.Errorf("poll %d query = %q, want empty", poll, got.RawQuery)
	}
	if got.Header.Get("Authorization") != "Bearer fixture-token" {
		t.Errorf("poll %d Authorization = %q", poll, got.Header.Get("Authorization"))
	}
	if got.Header.Get("Accept") != "application/json" {
		t.Errorf("poll %d Accept = %q", poll, got.Header.Get("Accept"))
	}
	if got.Header.Get("Content-Type") != "" {
		t.Errorf("poll %d Content-Type = %q, want absent", poll, got.Header.Get("Content-Type"))
	}
	if len(got.Body) != 0 {
		t.Errorf("poll %d body = %q, want empty", poll, got.Body)
	}
}

func assertDecodedRequest(t *testing.T, got vcfautomation.Request, requestID, status string) {
	t.Helper()
	if got.ID != requestID || got.Status != status {
		t.Fatalf("terminal request = %+v, want id %q and status %q", got, requestID, status)
	}
	if got.Name != "deployment action" || got.RequestedBy != "fixture-user" || got.CompletedTasks != 0 || got.TotalTasks != 1 {
		t.Errorf("decoded terminal request = %+v, want all fixture response fields preserved", got)
	}
}

func sortedKeys(values map[string]json.RawMessage) []string {
	keys := make([]string, 0, len(values))
	for _, key := range []string{"actionId", "inputs", "reason"} {
		if _, ok := values[key]; ok {
			keys = append(keys, key)
		}
	}
	return keys
}

func serverClient(server *mockvcf.Server) *http.Client {
	// httptest.Server's client is not exposed through the wrapper. A plain client
	// is sufficient because the server URL is HTTP loopback.
	return &http.Client{}
}
