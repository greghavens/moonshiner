package credentialgate

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"vcf91-0031/internal/contractmock"
)

const (
	contractSHA256 = "a47a6f6d7ba6705cb08f4715681ba8f8469e13cc54ea0156725ff7af493e5a0d"
	sourcesSHA256  = "e18d486cb13e19218c1a77d59574859c17e8828f70f4fd5b8f617f9017f549e7"
)

func TestProtectedSpecificationProvenance(t *testing.T) {
	for _, test := range []struct {
		path string
		want string
	}{
		{path: "docs/contract.json", want: contractSHA256},
		{path: "docs/official_sources.json", want: sourcesSHA256},
	} {
		test := test
		t.Run(test.path, func(t *testing.T) {
			data, err := os.ReadFile(test.path)
			if err != nil {
				t.Fatal(err)
			}
			sum := sha256.Sum256(data)
			if got := hex.EncodeToString(sum[:]); got != test.want {
				t.Fatalf("protected source hash = %s, want %s", got, test.want)
			}
		})
	}

	var contract struct {
		DerivedFrom struct {
			Repository          string `json:"repository"`
			RepositoryCommitSHA string `json:"repository_commit_sha"`
			SpecPath            string `json:"spec_path"`
			OpenAPI             string `json:"openapi"`
			InfoVersion         string `json:"info_version"`
			RepositoryLicense   string `json:"repository_license"`
		} `json:"derived_from"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operations"`
		Schemas map[string]json.RawMessage `json:"schemas"`
	}
	readJSONFile(t, "docs/contract.json", &contract)
	if got, want := contract.DerivedFrom.Repository,
		"https://github.com/vmware/vcf-api-specs"; got != want {
		t.Fatalf("repository = %q, want %q", got, want)
	}
	if got, want := contract.DerivedFrom.RepositoryCommitSHA,
		"3949fc33339fc5ea1b77eadb258f1cf49aa88e26"; got != want {
		t.Fatalf("commit = %q, want %q", got, want)
	}
	if got, want := contract.DerivedFrom.SpecPath,
		"specifications/sddc-manager/sddc-manager-openapi.json"; got != want {
		t.Fatalf("spec path = %q, want %q", got, want)
	}
	if contract.DerivedFrom.OpenAPI != "3.0.1" ||
		contract.DerivedFrom.InfoVersion != "9.1.0.0" ||
		contract.DerivedFrom.RepositoryLicense != "Apache-2.0" {
		t.Fatalf("unexpected specification metadata: %+v", contract.DerivedFrom)
	}
	wantOperations := []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	}{
		{
			OperationID: "updateOrRotatePasswords",
			Method:      http.MethodPatch,
			Path:        "/v1/credentials",
		},
		{
			OperationID: "getCredentialsTask",
			Method:      http.MethodGet,
			Path:        "/v1/credentials/tasks/{id}",
		},
	}
	if !reflect.DeepEqual(contract.Operations, wantOperations) {
		t.Fatalf("contract operations = %#v, want %#v", contract.Operations, wantOperations)
	}
	for _, schema := range []string{
		"CredentialsUpdateSpec",
		"ResourceCredentials",
		"BaseCredential",
		"Task",
		"CredentialsTask",
		"CredentialsSubTask",
		"Error",
	} {
		if len(contract.Schemas[schema]) == 0 {
			t.Fatalf("focused contract is missing schema %q", schema)
		}
	}

	var sources struct {
		SourceType          string   `json:"source_type"`
		Repository          string   `json:"repository"`
		RepositoryLicense   string   `json:"repository_license"`
		RepositoryCommitSHA string   `json:"repository_commit_sha"`
		SpecPath            string   `json:"spec_path"`
		OperationIDs        []string `json:"operationIds"`
	}
	readJSONFile(t, "docs/official_sources.json", &sources)
	if sources.SourceType != "OpenAPI specification" ||
		sources.Repository != contract.DerivedFrom.Repository ||
		sources.RepositoryLicense != "Apache-2.0" ||
		sources.RepositoryCommitSHA != contract.DerivedFrom.RepositoryCommitSHA ||
		sources.SpecPath != contract.DerivedFrom.SpecPath ||
		!reflect.DeepEqual(
			sources.OperationIDs,
			[]string{"updateOrRotatePasswords", "getCredentialsTask"},
		) {
		t.Fatalf("official source record does not mirror the contract: %+v", sources)
	}
}

func TestNewManagerValidationIsLocal(t *testing.T) {
	type mutation func(*Config)
	tests := []struct {
		name   string
		mutate mutation
	}{
		{"malformed URL", func(c *Config) { c.BaseURL = "://broken" }},
		{"unsupported scheme", func(c *Config) { c.BaseURL = "ftp://127.0.0.1" }},
		{"embedded credentials", func(c *Config) {
			c.BaseURL = "http://user:pass@127.0.0.1"
		}},
		{"path", func(c *Config) { c.BaseURL = "http://127.0.0.1/v1" }},
		{"escaped path", func(c *Config) { c.BaseURL = "http://127.0.0.1/%2e" }},
		{"query", func(c *Config) { c.BaseURL = "http://127.0.0.1/?x=1" }},
		{"empty query", func(c *Config) { c.BaseURL = "http://127.0.0.1/?" }},
		{"fragment", func(c *Config) { c.BaseURL = "http://127.0.0.1/#x" }},
		{"empty token", func(c *Config) { c.AccessToken = "" }},
		{"ASCII token whitespace", func(c *Config) { c.AccessToken = "a b" }},
		{"Unicode token whitespace", func(c *Config) { c.AccessToken = "a\u00a0b" }},
		{"empty current password", func(c *Config) { c.CurrentPassword = "" }},
		{"zero polls", func(c *Config) { c.MaxPolls = 0 }},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			var calls atomic.Int32
			config := Config{
				BaseURL:         "http://127.0.0.1:1/",
				AccessToken:     "token",
				CurrentPassword: "password",
				HTTPClient: &http.Client{Transport: roundTripperFunc(
					func(*http.Request) (*http.Response, error) {
						calls.Add(1)
						return nil, errors.New("unexpected network call")
					},
				)},
				MaxPolls: 1,
			}
			test.mutate(&config)
			if _, err := NewManager(config); err == nil {
				t.Fatal("NewManager succeeded, want validation error")
			}
			if calls.Load() != 0 {
				t.Fatalf("constructor made %d network calls", calls.Load())
			}
		})
	}

	for _, baseURL := range []string{
		"http://127.0.0.1:1",
		"http://127.0.0.1:1/",
		"https://[::1]:8443",
	} {
		if _, err := NewManager(Config{
			BaseURL:         baseURL,
			AccessToken:     "token",
			CurrentPassword: "password with allowed spaces",
			MaxPolls:        1,
		}); err != nil {
			t.Fatalf("NewManager(%q): %v", baseURL, err)
		}
	}
}

func TestNilContextsAndUninitializedManagersAreRejected(t *testing.T) {
	manager, err := NewManager(Config{
		BaseURL:         "http://127.0.0.1:1",
		AccessToken:     "token",
		CurrentPassword: "password",
		MaxPolls:        1,
	})
	if err != nil {
		t.Fatal(err)
	}
	target := RotationTarget{ResourceType: "VCENTER", Username: "administrator"}
	if _, err := manager.Acquire(nil); err == nil {
		t.Fatal("Acquire(nil) succeeded")
	}
	if _, err := manager.Rotate(nil, target); err == nil {
		t.Fatal("Rotate(nil) succeeded")
	}
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := manager.Acquire(cancelled); !errors.Is(err, context.Canceled) {
		t.Fatalf("Acquire(cancelled) = %v", err)
	}
	if _, err := manager.Rotate(cancelled, target); !errors.Is(err, context.Canceled) {
		t.Fatalf("Rotate(cancelled) = %v", err)
	}

	var zero Manager
	if _, err := zero.Acquire(context.Background()); err == nil {
		t.Fatal("zero Manager Acquire succeeded")
	}
	if _, err := zero.Rotate(context.Background(), target); err == nil {
		t.Fatal("zero Manager Rotate succeeded")
	}
	var nilManager *Manager
	if _, err := nilManager.Acquire(context.Background()); err == nil {
		t.Fatal("nil Manager Acquire succeeded")
	}
	if _, err := nilManager.Rotate(context.Background(), target); err == nil {
		t.Fatal("nil Manager Rotate succeeded")
	}
}

func TestRotationDrainsOldLeasesAndPublishesAtomically(t *testing.T) {
	server := newMock(t, contractmock.Plan{
		SubmitTaskStatus: "SUCCESSFUL",
		Polls: []contractmock.PollReply{
			{TaskStatus: "IN_PROGRESS"},
			{TaskStatus: " Successful "},
		},
	})
	runtime := server.Runtime()
	var paceMu sync.Mutex
	var paceCalls []int
	manager := newManager(t, server, 3, func(
		_ context.Context,
		operationID string,
		completedPolls int,
	) error {
		if operationID != "getCredentialsTask" {
			t.Errorf("Pace operationId = %q", operationID)
		}
		paceMu.Lock()
		paceCalls = append(paceCalls, completedPolls)
		paceMu.Unlock()
		return nil
	})

	oldLease, err := manager.Acquire(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if oldLease.Password() != runtime.CurrentPassword {
		t.Fatalf("old lease password = %q", oldLease.Password())
	}

	type rotationResult struct {
		task CredentialsTask
		err  error
	}
	rotationDone := make(chan rotationResult, 1)
	go func() {
		task, rotateErr := manager.Rotate(context.Background(), RotationTarget{
			ResourceType: "VCENTER",
			Username:     runtime.Username,
		})
		rotationDone <- rotationResult{task: task, err: rotateErr}
	}()
	waitForClosedGate(t, manager)
	if requests := server.Requests(); len(requests) != 0 {
		t.Fatalf("rotation sent %d requests before old lease drained", len(requests))
	}

	waitingLease := make(chan *Lease, 1)
	waitingErr := make(chan error, 1)
	go func() {
		lease, acquireErr := manager.Acquire(context.Background())
		if acquireErr != nil {
			waitingErr <- acquireErr
			return
		}
		waitingLease <- lease
	}()

	oldLease.Release()
	oldLease.Release()
	result := receive(t, rotationDone)
	if result.err != nil {
		t.Fatalf("Rotate: %v", result.err)
	}
	if result.task.ID != runtime.TaskID ||
		result.task.Status != " Successful " {
		t.Fatalf("returned task = %+v", result.task)
	}
	var newLease *Lease
	select {
	case newLease = <-waitingLease:
	case err := <-waitingErr:
		t.Fatalf("waiting acquisition failed: %v", err)
	case <-time.After(2 * time.Second):
		t.Fatal("waiting acquisition did not resume")
	}
	defer newLease.Release()
	if newLease.Password() != runtime.NewPassword {
		t.Fatalf("new lease did not receive generated password")
	}

	paceMu.Lock()
	gotPace := append([]int(nil), paceCalls...)
	paceMu.Unlock()
	if !reflect.DeepEqual(gotPace, []int{1}) {
		t.Fatalf("Pace calls = %v, want [1]", gotPace)
	}
	assertHappyWire(t, server.Requests(), runtime)
}

func TestOptionalRotationFieldsArePresentOnlyWhenSet(t *testing.T) {
	server := newMock(t, contractmock.Plan{})
	runtime := server.Runtime()
	manager := newManager(t, server, 1, nil)
	resourceName := "management-vcenter"
	resourceID := "f84f2c89-672a-4d61-9bb8-cd1bd4ad7c80"
	credentialType := "SSO"
	accountType := "SERVICE"
	if _, err := manager.Rotate(context.Background(), RotationTarget{
		ResourceName:   &resourceName,
		ResourceID:     &resourceID,
		ResourceType:   "VCENTER",
		CredentialType: &credentialType,
		AccountType:    &accountType,
		Username:       runtime.Username,
	}); err != nil {
		t.Fatal(err)
	}
	requests := server.Requests()
	if len(requests) != 2 {
		t.Fatalf("request count = %d, want 2", len(requests))
	}
	usernameJSON, _ := json.Marshal(runtime.Username)
	want := `{"operationType":"ROTATE","elements":[{` +
		`"resourceName":"management-vcenter",` +
		`"resourceId":"f84f2c89-672a-4d61-9bb8-cd1bd4ad7c80",` +
		`"resourceType":"VCENTER","credentials":[{` +
		`"credentialType":"SSO","accountType":"SERVICE","username":` +
		string(usernameJSON) + `}]}]}`
	if got := string(requests[0].Body); got != want {
		t.Fatalf("PATCH body = %s, want %s", got, want)
	}
	for _, forbidden := range []string{"autoRotatePolicy", "password"} {
		if strings.Contains(string(requests[0].Body), `"`+forbidden+`"`) {
			t.Fatalf("PATCH body unexpectedly contains %q", forbidden)
		}
	}
}

func TestRotationTargetValidationPrecedesGateAndTraffic(t *testing.T) {
	server := newMock(t, contractmock.Plan{})
	runtime := server.Runtime()
	tests := []struct {
		name   string
		target RotationTarget
	}{
		{"blank resource type", RotationTarget{Username: runtime.Username}},
		{"trimmed resource type", RotationTarget{
			ResourceType: " VCENTER",
			Username:     runtime.Username,
		}},
		{"blank username", RotationTarget{ResourceType: "VCENTER"}},
		{"trimmed username", RotationTarget{
			ResourceType: "VCENTER",
			Username:     runtime.Username + " ",
		}},
		{"empty resource name", targetWith(runtime.Username, "ResourceName", "")},
		{"trimmed resource id", targetWith(runtime.Username, "ResourceID", " id")},
		{"empty credential type", targetWith(runtime.Username, "CredentialType", "")},
		{"trimmed account type", targetWith(runtime.Username, "AccountType", "USER ")},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			manager := newManager(t, server, 1, nil)
			if _, err := manager.Rotate(context.Background(), test.target); err == nil {
				t.Fatal("Rotate succeeded, want validation error")
			}
			lease, err := manager.Acquire(context.Background())
			if err != nil {
				t.Fatalf("gate remained unavailable: %v", err)
			}
			if lease.Password() != runtime.CurrentPassword {
				t.Fatal("validation changed the current password")
			}
			lease.Release()
		})
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("invalid targets produced %d requests", got)
	}
}

func TestTaskOutcomesKeepOrReplacePassword(t *testing.T) {
	tests := []struct {
		name         string
		plan         contractmock.Plan
		maxPolls     int
		wantRequests int
		wantNew      bool
		checkError   func(*testing.T, error)
	}{
		{
			name:         "success is still polled immediately",
			plan:         contractmock.Plan{SubmitTaskStatus: "SUCCESSFUL"},
			maxPolls:     2,
			wantRequests: 2,
			wantNew:      true,
			checkError:   noError,
		},
		{
			name: "failed",
			plan: contractmock.Plan{Polls: []contractmock.PollReply{{
				TaskStatus: "FAILED",
				TaskErrors: []contractmock.VCFError{{
					ErrorCode: "ROTATE_FAILED",
				}},
				SubTaskErrors: []contractmock.VCFError{{
					ErrorCode: "ACCOUNT_REJECTED",
				}},
			}}},
			maxPolls:     2,
			wantRequests: 2,
			checkError: func(t *testing.T, err error) {
				t.Helper()
				var terminal *TaskTerminalError
				if !errors.As(err, &terminal) {
					t.Fatalf("error = %T, want *TaskTerminalError", err)
				}
				if terminal.Task.Status != "FAILED" ||
					len(terminal.Task.Errors) != 1 ||
					terminal.Task.Errors[0].ErrorCode != "ROTATE_FAILED" ||
					len(terminal.Task.SubTasks) != 1 ||
					len(terminal.Task.SubTasks[0].Errors) != 1 ||
					terminal.Task.SubTasks[0].Errors[0].ErrorCode != "ACCOUNT_REJECTED" {
					t.Fatalf("terminal task did not preserve errors: %+v", terminal.Task)
				}
			},
		},
		{
			name:         "user cancelled normalized",
			plan:         contractmock.Plan{Polls: []contractmock.PollReply{{TaskStatus: " user cancelled "}}},
			maxPolls:     2,
			wantRequests: 2,
			checkError:   wantErrorType[*TaskTerminalError],
		},
		{
			name:         "inconsistent",
			plan:         contractmock.Plan{Polls: []contractmock.PollReply{{TaskStatus: "INCONSISTENT"}}},
			maxPolls:     2,
			wantRequests: 2,
			checkError:   wantErrorType[*TaskTerminalError],
		},
		{
			name: "bounded polling",
			plan: contractmock.Plan{Polls: []contractmock.PollReply{
				{TaskStatus: "PENDING"},
				{TaskStatus: "IN_PROGRESS"},
			}},
			maxPolls:     2,
			wantRequests: 3,
			checkError: func(t *testing.T, err error) {
				t.Helper()
				var limit *PollLimitError
				if !errors.As(err, &limit) {
					t.Fatalf("error = %T, want *PollLimitError", err)
				}
				if limit.MaxPolls != 2 ||
					limit.LastStatus != "IN_PROGRESS" ||
					limit.TaskID == "" {
					t.Fatalf("poll limit = %+v", limit)
				}
			},
		},
		{
			name:         "unknown status",
			plan:         contractmock.Plan{Polls: []contractmock.PollReply{{TaskStatus: "PAUSED"}}},
			maxPolls:     2,
			wantRequests: 2,
			checkError:   wantErrorType[*ProtocolError],
		},
		{
			name:         "accepted task missing id",
			plan:         contractmock.Plan{OmitSubmitTaskID: true},
			maxPolls:     2,
			wantRequests: 1,
			checkError:   wantErrorType[*ProtocolError],
		},
		{
			name: "wrong task id",
			plan: contractmock.Plan{Polls: []contractmock.PollReply{{
				TaskStatus: "SUCCESSFUL",
				TaskID:     "different-task",
			}}},
			maxPolls:     2,
			wantRequests: 2,
			checkError:   wantErrorType[*ProtocolError],
		},
		{
			name: "missing generated password",
			plan: contractmock.Plan{Polls: []contractmock.PollReply{{
				TaskStatus:      "SUCCESSFUL",
				OmitNewPassword: true,
			}}},
			maxPolls:     2,
			wantRequests: 2,
			checkError:   wantErrorType[*ProtocolError],
		},
		{
			name: "ambiguous generated password",
			plan: contractmock.Plan{Polls: []contractmock.PollReply{{
				TaskStatus:               "SUCCESSFUL",
				DuplicateMatchingSubTask: true,
			}}},
			maxPolls:     2,
			wantRequests: 2,
			checkError:   wantErrorType[*ProtocolError],
		},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			server := newMock(t, test.plan)
			runtime := server.Runtime()
			manager := newManager(t, server, test.maxPolls, nil)
			_, err := manager.Rotate(context.Background(), RotationTarget{
				ResourceType: "VCENTER",
				Username:     runtime.Username,
			})
			test.checkError(t, err)
			if got := len(server.Requests()); got != test.wantRequests {
				t.Fatalf("request count = %d, want %d", got, test.wantRequests)
			}
			lease, acquireErr := manager.Acquire(context.Background())
			if acquireErr != nil {
				t.Fatal(acquireErr)
			}
			defer lease.Release()
			wantPassword := runtime.CurrentPassword
			if test.wantNew {
				wantPassword = runtime.NewPassword
			}
			if lease.Password() != wantPassword {
				t.Fatal("password publication did not match task outcome")
			}
		})
	}
}

func TestAPIErrorAndPacingFailurePreserveOldPassword(t *testing.T) {
	tests := []struct {
		name         string
		plan         contractmock.Plan
		pace         func(context.Context, string, int) error
		wantRequests int
		check        func(*testing.T, error, contractmock.RuntimeValues)
	}{
		{
			name: "submit API error",
			plan: contractmock.Plan{
				SubmitStatus: http.StatusForbidden,
				SubmitError: contractmock.VCFError{
					ErrorCode:          "FORBIDDEN",
					Message:            "server-message",
					RemediationMessage: "server-remediation",
					ReferenceToken:     "reference-token",
				},
			},
			wantRequests: 1,
			check: func(t *testing.T, err error, runtime contractmock.RuntimeValues) {
				t.Helper()
				var apiError *APIError
				if !errors.As(err, &apiError) {
					t.Fatalf("error = %T, want *APIError", err)
				}
				if apiError.OperationID != "updateOrRotatePasswords" ||
					apiError.StatusCode != http.StatusForbidden ||
					apiError.ErrorCode != "FORBIDDEN" ||
					apiError.Message != "server-message" ||
					apiError.RemediationMessage != "server-remediation" ||
					apiError.ReferenceToken != "reference-token" {
					t.Fatalf("APIError = %+v", apiError)
				}
				assertRedacted(t, err.Error(), runtime, "server-message")
			},
		},
		{
			name: "poll API error",
			plan: contractmock.Plan{Polls: []contractmock.PollReply{{
				HTTPStatus: http.StatusInternalServerError,
				APIError: contractmock.VCFError{
					ErrorCode: "POLL_FAILED",
					Message:   "poll-server-message",
				},
			}}},
			wantRequests: 2,
			check: func(t *testing.T, err error, runtime contractmock.RuntimeValues) {
				t.Helper()
				var apiError *APIError
				if !errors.As(err, &apiError) ||
					apiError.OperationID != "getCredentialsTask" ||
					apiError.ErrorCode != "POLL_FAILED" {
					t.Fatalf("APIError = %+v (%T)", apiError, err)
				}
				assertRedacted(t, err.Error(), runtime, "poll-server-message")
			},
		},
		{
			name: "pace failure",
			plan: contractmock.Plan{Polls: []contractmock.PollReply{
				{TaskStatus: "PENDING"},
				{TaskStatus: "SUCCESSFUL"},
			}},
			pace: func(context.Context, string, int) error {
				return errors.New("pace-secret")
			},
			wantRequests: 2,
			check: func(t *testing.T, err error, _ contractmock.RuntimeValues) {
				t.Helper()
				if err == nil || err.Error() != "pace-secret" {
					t.Fatalf("pace error = %v", err)
				}
			},
		},
		{
			name: "redirect is not followed",
			plan: contractmock.Plan{
				SubmitStatus:   http.StatusTemporaryRedirect,
				SubmitLocation: "/v1/credentials/tasks/not-followed",
			},
			wantRequests: 1,
			check: func(t *testing.T, err error, _ contractmock.RuntimeValues) {
				t.Helper()
				var apiError *APIError
				if !errors.As(err, &apiError) ||
					apiError.StatusCode != http.StatusTemporaryRedirect ||
					apiError.OperationID != "updateOrRotatePasswords" {
					t.Fatalf("redirect error = %+v (%T)", apiError, err)
				}
			},
		},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			server := newMock(t, test.plan)
			runtime := server.Runtime()
			manager := newManager(t, server, 3, test.pace)
			_, err := manager.Rotate(context.Background(), RotationTarget{
				ResourceType: "VCENTER",
				Username:     runtime.Username,
			})
			if err == nil {
				t.Fatal("Rotate succeeded, want error")
			}
			test.check(t, err, runtime)
			if got := len(server.Requests()); got != test.wantRequests {
				t.Fatalf("request count = %d, want %d", got, test.wantRequests)
			}
			assertCurrentPassword(t, manager, runtime.CurrentPassword)
		})
	}
}

func TestCancellationWhileDrainingReopensGateWithoutTraffic(t *testing.T) {
	server := newMock(t, contractmock.Plan{})
	runtime := server.Runtime()
	manager := newManager(t, server, 1, nil)
	oldLease, err := manager.Acquire(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer oldLease.Release()

	ctx, cancel := context.WithCancel(context.Background())
	rotationDone := make(chan error, 1)
	go func() {
		_, rotateErr := manager.Rotate(ctx, RotationTarget{
			ResourceType: "VCENTER",
			Username:     runtime.Username,
		})
		rotationDone <- rotateErr
	}()
	waitForClosedGate(t, manager)
	cancel()
	if err := receive(t, rotationDone); !errors.Is(err, context.Canceled) {
		t.Fatalf("Rotate error = %v, want context.Canceled", err)
	}
	if got := len(server.Requests()); got != 0 {
		t.Fatalf("cancelled drain sent %d requests", got)
	}
	assertCurrentPassword(t, manager, runtime.CurrentPassword)

	cancelled, cancelNow := context.WithCancel(context.Background())
	cancelNow()
	if _, err := manager.Acquire(cancelled); !errors.Is(err, context.Canceled) {
		t.Fatalf("Acquire error = %v, want context.Canceled", err)
	}
	if _, err := manager.Rotate(cancelled, RotationTarget{
		ResourceType: "VCENTER",
		Username:     runtime.Username,
	}); !errors.Is(err, context.Canceled) {
		t.Fatalf("Rotate error = %v, want context.Canceled", err)
	}
}

func TestConcurrentRotationsAreSerialized(t *testing.T) {
	server := newMock(t, contractmock.Plan{
		Polls: []contractmock.PollReply{
			{TaskStatus: "PENDING"},
			{TaskStatus: "SUCCESSFUL"},
			{TaskStatus: "SUCCESSFUL"},
		},
	})
	runtime := server.Runtime()
	paceEntered := make(chan struct{})
	releasePace := make(chan struct{})
	var paceOnce sync.Once
	manager := newManager(t, server, 3, func(
		ctx context.Context,
		_ string,
		_ int,
	) error {
		paceOnce.Do(func() { close(paceEntered) })
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-releasePace:
			return nil
		}
	})
	target := RotationTarget{ResourceType: "VCENTER", Username: runtime.Username}
	first := make(chan error, 1)
	go func() {
		_, err := manager.Rotate(context.Background(), target)
		first <- err
	}()
	select {
	case <-paceEntered:
	case <-time.After(2 * time.Second):
		t.Fatal("first rotation did not reach pacing point")
	}
	waitForRequestCount(t, server, 2)
	secondContext, cancelSecond := context.WithTimeout(
		context.Background(),
		10*time.Millisecond,
	)
	_, secondErr := manager.Rotate(secondContext, target)
	cancelSecond()
	if !errors.Is(secondErr, context.DeadlineExceeded) {
		t.Fatalf("competing Rotate error = %v, want deadline", secondErr)
	}
	if got := len(server.Requests()); got != 2 {
		t.Fatalf("second rotation sent traffic while first owned gate: %d requests", got)
	}
	close(releasePace)
	if err := receive(t, first); err != nil {
		t.Fatalf("first Rotate: %v", err)
	}
	if _, err := manager.Rotate(context.Background(), target); err != nil {
		t.Fatalf("subsequent Rotate: %v", err)
	}
	requests := server.Requests()
	if len(requests) != 5 {
		t.Fatalf("serialized request count = %d, want 5", len(requests))
	}
	wantOperations := []string{
		"updateOrRotatePasswords",
		"getCredentialsTask",
		"getCredentialsTask",
		"updateOrRotatePasswords",
		"getCredentialsTask",
	}
	for index, want := range wantOperations {
		if requests[index].OperationID != want {
			t.Fatalf("request %d operation = %q, want %q", index, requests[index].OperationID, want)
		}
	}
}

func TestTransportErrorsAreTypedAndRedacted(t *testing.T) {
	accessToken := "sensitive-access-token"
	oldPassword := "sensitive-old-password"
	transportText := accessToken + ":" + oldPassword + ":transport-detail"
	manager, err := NewManager(Config{
		BaseURL:         "http://127.0.0.1:1",
		AccessToken:     accessToken,
		CurrentPassword: oldPassword,
		HTTPClient: &http.Client{Transport: roundTripperFunc(
			func(*http.Request) (*http.Response, error) {
				return nil, errors.New(transportText)
			},
		)},
		MaxPolls: 1,
	})
	if err != nil {
		t.Fatal(err)
	}
	_, err = manager.Rotate(context.Background(), RotationTarget{
		ResourceType: "VCENTER",
		Username:     "administrator",
	})
	var transportError *TransportError
	if !errors.As(err, &transportError) ||
		transportError.OperationID != "updateOrRotatePasswords" {
		t.Fatalf("error = %v (%T), want submit TransportError", err, err)
	}
	for _, forbidden := range []string{
		accessToken,
		oldPassword,
		"transport-detail",
	} {
		if strings.Contains(err.Error(), forbidden) {
			t.Fatalf("transport error exposed %q: %q", forbidden, err)
		}
	}
	assertCurrentPassword(t, manager, oldPassword)
}

func assertHappyWire(
	t *testing.T,
	requests []contractmock.Request,
	runtime contractmock.RuntimeValues,
) {
	t.Helper()
	if len(requests) != 3 {
		t.Fatalf("request count = %d, want 3", len(requests))
	}
	patch := requests[0]
	if patch.OperationID != "updateOrRotatePasswords" ||
		patch.Method != http.MethodPatch ||
		patch.Path != "/v1/credentials" ||
		patch.RawQuery != "" ||
		patch.RequestURI != "/v1/credentials" {
		t.Fatalf("PATCH request target = %+v", patch)
	}
	assertSingleHeader(t, patch.Header, "Accept", "application/json")
	assertSingleHeader(
		t,
		patch.Header,
		"Authorization",
		"Bearer "+runtime.AccessToken,
	)
	assertSingleHeader(t, patch.Header, "Content-Type", "application/json")
	if patch.ContentLength != int64(len(patch.Body)) ||
		len(patch.TransferEncoding) != 0 {
		t.Fatalf(
			"PATCH framing: ContentLength=%d transfer=%v body=%d",
			patch.ContentLength,
			patch.TransferEncoding,
			len(patch.Body),
		)
	}
	usernameJSON, _ := json.Marshal(runtime.Username)
	wantBody := `{"operationType":"ROTATE","elements":[{` +
		`"resourceType":"VCENTER","credentials":[{"username":` +
		string(usernameJSON) + `}]}]}`
	if got := string(patch.Body); got != wantBody {
		t.Fatalf("PATCH body = %s, want %s", got, wantBody)
	}
	var body map[string]any
	if err := json.Unmarshal(patch.Body, &body); err != nil {
		t.Fatal(err)
	}
	assertJSONKeys(t, body, "operationType", "elements")
	elements := body["elements"].([]any)
	resource := elements[0].(map[string]any)
	assertJSONKeys(t, resource, "resourceType", "credentials")
	credentials := resource["credentials"].([]any)
	credential := credentials[0].(map[string]any)
	assertJSONKeys(t, credential, "username")

	for index, get := range requests[1:] {
		if get.OperationID != "getCredentialsTask" ||
			get.Method != http.MethodGet ||
			get.Path != "/v1/credentials/tasks/"+runtime.TaskID ||
			get.RawQuery != "" ||
			get.RequestURI != "/v1/credentials/tasks/"+
				url.PathEscape(runtime.TaskID) {
			t.Fatalf("GET request %d target = %+v", index, get)
		}
		assertSingleHeader(t, get.Header, "Accept", "application/json")
		assertSingleHeader(
			t,
			get.Header,
			"Authorization",
			"Bearer "+runtime.AccessToken,
		)
		if values := get.Header.Values("Content-Type"); len(values) != 0 {
			t.Fatalf("GET Content-Type = %v, want absent", values)
		}
		if get.ContentLength != 0 ||
			len(get.TransferEncoding) != 0 ||
			len(get.Body) != 0 {
			t.Fatalf(
				"GET framing: ContentLength=%d transfer=%v body=%q",
				get.ContentLength,
				get.TransferEncoding,
				get.Body,
			)
		}
	}
}

func targetWith(username, field, value string) RotationTarget {
	target := RotationTarget{ResourceType: "VCENTER", Username: username}
	switch field {
	case "ResourceName":
		target.ResourceName = &value
	case "ResourceID":
		target.ResourceID = &value
	case "CredentialType":
		target.CredentialType = &value
	case "AccountType":
		target.AccountType = &value
	default:
		panic("unknown test field")
	}
	return target
}

func waitForClosedGate(t *testing.T, manager *Manager) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Millisecond)
		lease, err := manager.Acquire(ctx)
		cancel()
		if errors.Is(err, context.DeadlineExceeded) {
			return
		}
		if err != nil {
			t.Fatalf("probing gate: %v", err)
		}
		lease.Release()
	}
	t.Fatal("rotation did not close the lease gate")
}

func waitForRequestCount(
	t *testing.T,
	server *contractmock.Server,
	want int,
) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if len(server.Requests()) >= want {
			return
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatalf("request count did not reach %d", want)
}

func assertCurrentPassword(t *testing.T, manager *Manager, want string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	lease, err := manager.Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer lease.Release()
	if got := lease.Password(); got != want {
		t.Fatalf("current password = %q, want preserved value", got)
	}
}

func assertSingleHeader(
	t *testing.T,
	header http.Header,
	name string,
	want string,
) {
	t.Helper()
	if got := header.Values(name); !reflect.DeepEqual(got, []string{want}) {
		t.Fatalf("%s header = %v, want [%q]", name, got, want)
	}
}

func assertJSONKeys(t *testing.T, value map[string]any, want ...string) {
	t.Helper()
	got := make(map[string]bool, len(value))
	for key := range value {
		got[key] = true
	}
	wantSet := make(map[string]bool, len(want))
	for _, key := range want {
		wantSet[key] = true
	}
	if !reflect.DeepEqual(got, wantSet) {
		t.Fatalf("JSON keys = %v, want %v", got, wantSet)
	}
}

func assertRedacted(
	t *testing.T,
	text string,
	runtime contractmock.RuntimeValues,
	extra ...string,
) {
	t.Helper()
	for _, forbidden := range append([]string{
		runtime.AccessToken,
		runtime.CurrentPassword,
		runtime.NewPassword,
	}, extra...) {
		if forbidden != "" && strings.Contains(text, forbidden) {
			t.Fatalf("error exposed %q: %q", forbidden, text)
		}
	}
}

func newMock(t *testing.T, plan contractmock.Plan) *contractmock.Server {
	t.Helper()
	server, err := contractmock.New("docs/contract.json", plan)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(server.Close)
	return server
}

func newManager(
	t *testing.T,
	server *contractmock.Server,
	maxPolls int,
	pace func(context.Context, string, int) error,
) *Manager {
	t.Helper()
	runtime := server.Runtime()
	manager, err := NewManager(Config{
		BaseURL:         server.URL(),
		AccessToken:     runtime.AccessToken,
		CurrentPassword: runtime.CurrentPassword,
		HTTPClient:      server.Client(),
		MaxPolls:        maxPolls,
		Pace:            pace,
	})
	if err != nil {
		t.Fatal(err)
	}
	return manager
}

func readJSONFile(t *testing.T, path string, destination any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(data, destination); err != nil {
		t.Fatal(err)
	}
}

func noError(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func wantErrorType[T error](t *testing.T, err error) {
	t.Helper()
	var target T
	if !errors.As(err, &target) {
		t.Fatalf("error = %T, want requested error type", err)
	}
}

func receive[T any](t *testing.T, values <-chan T) T {
	t.Helper()
	select {
	case value := <-values:
		return value
	case <-time.After(2 * time.Second):
		var zero T
		t.Fatal("timed out waiting for concurrent result")
		return zero
	}
}

type roundTripperFunc func(*http.Request) (*http.Response, error)

func (f roundTripperFunc) RoundTrip(
	request *http.Request,
) (*http.Response, error) {
	return f(request)
}
