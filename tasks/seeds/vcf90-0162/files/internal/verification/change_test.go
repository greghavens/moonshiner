package verification_test

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"reflect"
	"strings"
	"sync"
	"testing"

	"example.com/vcfa-change/internal/vcfmock"
	"example.com/vcfa-change/vcfa"
)

func pointer[T any](v T) *T { return &v }

func TestApplyChangeReportsLaterFailureAndExactWireShape(t *testing.T) {
	mock := vcfmock.New(t)
	client, err := vcfa.NewClient(mock.URL(), "token-abc", mock.Client())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	report, err := client.ApplyChange(context.Background(), vcfa.Change{
		DeploymentID: "dep-42",
		ResourceID:   "machine/with space",
		DeploymentUpdate: vcfa.DeploymentUpdate{
			Name: pointer("payments-prod-renamed"),
		},
		DeploymentAction: vcfa.ActionRequest{
			ActionID: pointer("Deployment.ChangeOwner"),
			Inputs:   map[string]any{"New Owner": "alice@example.com"},
		},
		ResourceAction: vcfa.ActionRequest{
			ActionID: pointer("Cloud.vSphere.Machine.PowerOff"),
		},
	})
	if err == nil {
		t.Fatal("ApplyChange returned nil error")
	}
	var stepErr *vcfa.StepError
	if !errors.As(err, &stepErr) {
		t.Fatalf("error type = %T, want *vcfa.StepError", err)
	}
	if stepErr.Step != vcfa.StepSubmitResourceAction || stepErr.HTTPStatus != 409 || stepErr.Message != "resource is busy" {
		t.Fatalf("StepError = %#v", stepErr)
	}

	wantSteps := []vcfa.StepResult{
		{Step: vcfa.StepPatchDeployment, Status: vcfa.StepSucceeded, HTTPStatus: 200, ResponseID: "dep-42"},
		{Step: vcfa.StepSubmitDeploymentAction, Status: vcfa.StepSucceeded, HTTPStatus: 200, ResponseID: "req-owner-7"},
		{Step: vcfa.StepSubmitResourceAction, Status: vcfa.StepFailed, HTTPStatus: 409, Message: "resource is busy"},
	}
	if !reflect.DeepEqual(report.Steps, wantSteps) {
		t.Fatalf("report steps = %#v, want %#v", report.Steps, wantSteps)
	}

	wantRequests := []vcfmock.LoggedRequest{
		{
			Operation: "Patch Deployment", Method: "PATCH", Path: "/deployment/api/deployments/dep-42",
			Authorization: "Bearer token-abc", ContentType: "application/json", Accept: "application/json",
			Body: `{"name":"payments-prod-renamed"}`,
		},
		{
			Operation: "Submit Deployment Action Request", Method: "POST", Path: "/deployment/api/deployments/dep-42/requests",
			Authorization: "Bearer token-abc", ContentType: "application/json", Accept: "application/json",
			Body: `{"actionId":"Deployment.ChangeOwner","inputs":{"New Owner":"alice@example.com"}}`,
		},
		{
			Operation: "Submit Resource Action Request", Method: "POST", Path: "/deployment/api/resources/machine%2Fwith%20space/requests",
			Authorization: "Bearer token-abc", ContentType: "application/json", Accept: "application/json",
			Body: `{"actionId":"Cloud.vSphere.Machine.PowerOff"}`,
		},
	}
	gotRequests := mock.Requests()
	if len(gotRequests) != len(wantRequests) {
		t.Fatalf("request count = %d, want %d", len(gotRequests), len(wantRequests))
	}
	for i, want := range wantRequests {
		t.Run(want.Operation, func(t *testing.T) {
			if got := gotRequests[i]; !reflect.DeepEqual(got, want) {
				t.Fatalf("request =\n%#v\nwant\n%#v", got, want)
			}
		})
	}
}

func TestApplyChangeStopsAfterFirstFailedOperation(t *testing.T) {
	tests := []struct {
		name           string
		failedOp       string
		failedStep     vcfa.Step
		status         int
		body           string
		message        string
		wantSteps      []vcfa.StepResult
		wantOperations []string
	}{
		{
			name:       "patch deployment",
			failedOp:   "Patch Deployment",
			failedStep: vcfa.StepPatchDeployment,
			status:     http.StatusForbidden,
			body:       `{"details":"deployment is read-only"}`,
			message:    "deployment is read-only",
			wantSteps: []vcfa.StepResult{
				{Step: vcfa.StepPatchDeployment, Status: vcfa.StepFailed, HTTPStatus: 403, Message: "deployment is read-only"},
			},
			wantOperations: []string{"Patch Deployment"},
		},
		{
			name:       "deployment action",
			failedOp:   "Submit Deployment Action Request",
			failedStep: vcfa.StepSubmitDeploymentAction,
			status:     http.StatusConflict,
			body:       `{"message":"action conflicts"}`,
			message:    "action conflicts",
			wantSteps: []vcfa.StepResult{
				{Step: vcfa.StepPatchDeployment, Status: vcfa.StepSucceeded, HTTPStatus: 200, ResponseID: "dep-42"},
				{Step: vcfa.StepSubmitDeploymentAction, Status: vcfa.StepFailed, HTTPStatus: 409, Message: "action conflicts"},
			},
			wantOperations: []string{"Patch Deployment", "Submit Deployment Action Request"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			mock := vcfmock.NewWithResponses(t, map[string]vcfmock.Response{
				tt.failedOp: {StatusCode: tt.status, Body: tt.body},
			})
			client, err := vcfa.NewClient(mock.URL(), "token-stop", mock.Client())
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}

			report, err := client.ApplyChange(context.Background(), completeChange("dep-stop", "res-stop"))
			assertStepError(t, err, tt.failedStep, tt.status, tt.message)
			if !reflect.DeepEqual(report.Steps, tt.wantSteps) {
				t.Fatalf("report steps = %#v, want %#v", report.Steps, tt.wantSteps)
			}
			requests := mock.Requests()
			if len(requests) != len(tt.wantOperations) {
				t.Fatalf("request count = %d, want %d", len(requests), len(tt.wantOperations))
			}
			for i, operation := range tt.wantOperations {
				if requests[i].Operation != operation {
					t.Fatalf("request %d operation = %q, want %q", i, requests[i].Operation, operation)
				}
			}
		})
	}
}

func TestApplyChangeSuccessOmitsEveryUnsetOptionalField(t *testing.T) {
	mock := vcfmock.NewWithResponses(t, nil)
	client, err := vcfa.NewClient(mock.URL(), "token-empty", mock.Client())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	report, err := client.ApplyChange(context.Background(), vcfa.Change{
		DeploymentID: "dep/50 %",
		ResourceID:   "resource?#/%",
	})
	if err != nil {
		t.Fatalf("ApplyChange: %v", err)
	}
	wantSteps := []vcfa.StepResult{
		{Step: vcfa.StepPatchDeployment, Status: vcfa.StepSucceeded, HTTPStatus: 200, ResponseID: "dep-42"},
		{Step: vcfa.StepSubmitDeploymentAction, Status: vcfa.StepSucceeded, HTTPStatus: 200, ResponseID: "req-owner-7"},
		{Step: vcfa.StepSubmitResourceAction, Status: vcfa.StepSucceeded, HTTPStatus: 200, ResponseID: "req-power-9"},
	}
	if !reflect.DeepEqual(report.Steps, wantSteps) {
		t.Fatalf("report steps = %#v, want %#v", report.Steps, wantSteps)
	}

	wantPaths := []string{
		"/deployment/api/deployments/dep%2F50%20%25",
		"/deployment/api/deployments/dep%2F50%20%25/requests",
		"/deployment/api/resources/resource%3F%23%2F%25/requests",
	}
	requests := mock.Requests()
	if len(requests) != len(wantPaths) {
		t.Fatalf("request count = %d, want %d", len(requests), len(wantPaths))
	}
	for i, request := range requests {
		if request.Path != wantPaths[i] {
			t.Errorf("request %d path = %q, want %q", i, request.Path, wantPaths[i])
		}
		if request.RawQuery != "" {
			t.Errorf("request %d query = %q, want empty", i, request.RawQuery)
		}
		if request.Body != "{}" {
			t.Errorf("request %d body = %q, want {}", i, request.Body)
		}
		if request.Authorization != "Bearer token-empty" || request.ContentType != "application/json" || request.Accept != "application/json" {
			t.Errorf("request %d headers = Authorization %q, Content-Type %q, Accept %q", i, request.Authorization, request.ContentType, request.Accept)
		}
	}
}

func TestApplyChangeReportsRequestEncodingFailure(t *testing.T) {
	mock := vcfmock.NewWithResponses(t, nil)
	client, err := vcfa.NewClient(mock.URL(), "token-encode", mock.Client())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	change := completeChange("dep-encode", "res-encode")
	change.DeploymentAction.Inputs = map[string]any{"unsupported": make(chan int)}

	report, err := client.ApplyChange(context.Background(), change)
	var stepErr *vcfa.StepError
	if !errors.As(err, &stepErr) {
		t.Fatalf("error type = %T, want *vcfa.StepError", err)
	}
	if stepErr.Step != vcfa.StepSubmitDeploymentAction || stepErr.HTTPStatus != 0 || !strings.Contains(stepErr.Message, "encode request") {
		t.Fatalf("StepError = %#v", stepErr)
	}
	if len(report.Steps) != 2 || report.Steps[0] != (vcfa.StepResult{Step: vcfa.StepPatchDeployment, Status: vcfa.StepSucceeded, HTTPStatus: 200, ResponseID: "dep-42"}) {
		t.Fatalf("report steps = %#v", report.Steps)
	}
	if got := report.Steps[1]; got.Step != vcfa.StepSubmitDeploymentAction || got.Status != vcfa.StepFailed || got.HTTPStatus != 0 || !strings.Contains(got.Message, "encode request") {
		t.Fatalf("failed result = %#v", got)
	}
	if got := len(mock.Requests()); got != 1 {
		t.Fatalf("request count = %d, want 1", got)
	}
}

func TestClientSupportsConcurrentApplyChange(t *testing.T) {
	mock := vcfmock.NewWithResponses(t, nil)
	client, err := vcfa.NewClient(mock.URL(), "token-concurrent", mock.Client())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}

	const calls = 16
	var wg sync.WaitGroup
	errs := make(chan error, calls)
	for i := 0; i < calls; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			report, err := client.ApplyChange(context.Background(), completeChange(fmt.Sprintf("dep-%d", i), fmt.Sprintf("res-%d", i)))
			if err != nil {
				errs <- fmt.Errorf("call %d: %w", i, err)
				return
			}
			if len(report.Steps) != 3 {
				errs <- fmt.Errorf("call %d returned %d steps", i, len(report.Steps))
			}
		}(i)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Error(err)
	}

	requests := mock.Requests()
	if len(requests) != calls*3 {
		t.Fatalf("request count = %d, want %d", len(requests), calls*3)
	}
	for i := 0; i < calls; i++ {
		patchPath := fmt.Sprintf("/deployment/api/deployments/dep-%d", i)
		deploymentActionPath := patchPath + "/requests"
		resourceActionPath := fmt.Sprintf("/deployment/api/resources/res-%d/requests", i)
		indices := map[string]int{patchPath: -1, deploymentActionPath: -1, resourceActionPath: -1}
		for j, request := range requests {
			if _, ok := indices[request.Path]; ok {
				indices[request.Path] = j
			}
		}
		if indices[patchPath] < 0 || indices[deploymentActionPath] <= indices[patchPath] || indices[resourceActionPath] <= indices[deploymentActionPath] {
			t.Errorf("call %d request order indices = %#v", i, indices)
		}
	}
}

func completeChange(deploymentID, resourceID string) vcfa.Change {
	return vcfa.Change{
		DeploymentID:     deploymentID,
		ResourceID:       resourceID,
		DeploymentUpdate: vcfa.DeploymentUpdate{Name: pointer("new-name")},
		DeploymentAction: vcfa.ActionRequest{ActionID: pointer("Deployment.ChangeOwner")},
		ResourceAction:   vcfa.ActionRequest{ActionID: pointer("Cloud.vSphere.Machine.PowerOff")},
	}
}

func assertStepError(t *testing.T, err error, step vcfa.Step, status int, message string) {
	t.Helper()
	if err == nil {
		t.Fatal("ApplyChange returned nil error")
	}
	var stepErr *vcfa.StepError
	if !errors.As(err, &stepErr) {
		t.Fatalf("error type = %T, want *vcfa.StepError", err)
	}
	if stepErr.Step != step || stepErr.HTTPStatus != status || stepErr.Message != message {
		t.Fatalf("StepError = %#v, want step %q, status %d, message %q", stepErr, step, status, message)
	}
}
