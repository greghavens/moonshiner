// Package verification drives credrotate against the contract-pinned loopback
// mock and asserts both what the client did to the store and the exact wire
// shape of every request it sent.
//
// No live VMware endpoint is contacted: the only server involved is the
// httptest listener the mock binds to an ephemeral 127.0.0.1 port.
package verification

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"reflect"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"example.com/vcf-cred-rotation/credrotate"
	"example.com/vcf-cred-rotation/internal/contractmock"
)

const pollInterval = time.Millisecond

var (
	keyESXiRoot = credrotate.Key{ResourceName: contractmock.ESXiHost, Username: contractmock.ESXiRootUser}
	keyESXiSvc  = credrotate.Key{ResourceName: contractmock.ESXiHost, Username: contractmock.ESXiSvcUser}
	keyVCenter  = credrotate.Key{ResourceName: contractmock.VCenter, Username: contractmock.VCenterUser}

	// requestOrder is the order the credentials appear in the rotate request,
	// which is the order Result.Rotated and Result.Retained must use.
	requestOrder = []credrotate.Key{keyESXiRoot, keyESXiSvc, keyVCenter}
)

func initialSecrets() map[credrotate.Key]credrotate.Secret {
	return map[credrotate.Key]credrotate.Secret{
		keyESXiRoot: {Username: contractmock.ESXiRootUser, Password: contractmock.OldESXiRootPassword},
		keyESXiSvc:  {Username: contractmock.ESXiSvcUser, Password: contractmock.OldESXiSvcPassword},
		keyVCenter:  {Username: contractmock.VCenterUser, Password: contractmock.OldVCenterPassword},
	}
}

// rotateRequest is the change under test. It deliberately leaves optional
// members unset in an uneven way: the ESXi element omits resourceId while the
// vCenter element carries one, and the ESXi service credential omits
// accountType while its sibling on the same resource carries one.
func rotateRequest(operationType string, policy *credrotate.AutoRotatePolicy) credrotate.RotateRequest {
	esxRoot := credrotate.CredentialSpec{Username: contractmock.ESXiRootUser, CredentialType: "SSH", AccountType: "USER"}
	esxSvc := credrotate.CredentialSpec{Username: contractmock.ESXiSvcUser, CredentialType: "SSH"}
	vc := credrotate.CredentialSpec{Username: contractmock.VCenterUser, CredentialType: "SSO", AccountType: "USER"}

	if operationType == "UPDATE" {
		esxRoot.Password = contractmock.NewESXiRootPassword
		esxSvc.Password = contractmock.NewESXiSvcPassword
		vc.Password = contractmock.NewVCenterPassword
	}

	return credrotate.RotateRequest{
		OperationType: operationType,
		AutoRotate:    policy,
		Resources: []credrotate.ResourceSpec{
			{
				ResourceName: contractmock.ESXiHost,
				ResourceType: "ESXI",
				Credentials:  []credrotate.CredentialSpec{esxRoot, esxSvc},
			},
			{
				ResourceName: contractmock.VCenter,
				ResourceID:   contractmock.VCenterResourceID,
				ResourceType: "VCENTER",
				Credentials:  []credrotate.CredentialSpec{vc},
			},
		},
	}
}

func newStore(t *testing.T) *credrotate.Store {
	t.Helper()
	store, err := credrotate.NewStore(initialSecrets())
	if err != nil {
		t.Fatalf("NewStore: %v", err)
	}
	return store
}

func newClient(t *testing.T, mock *contractmock.Server) *credrotate.Client {
	t.Helper()
	c, err := credrotate.NewClient(mock.URL, contractmock.AccessToken, mock.HTTPClient)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return c
}

// -----------------------------------------------------------------------------
// Scenario behaviour
// -----------------------------------------------------------------------------

func TestRotateScenarios(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name     string
		scenario contractmock.Scenario

		wantOps      []string
		wantTaskID   string
		wantStatus   string
		wantSucceed  bool
		wantCancel   bool
		wantOutcomes []credrotate.Outcome
		wantRotated  []credrotate.Key
		wantRetained []credrotate.Key
		wantSecrets  map[credrotate.Key]credrotate.Secret
		checkErr     func(t *testing.T, err error)
	}{
		{
			name:        "every credential changes",
			scenario:    contractmock.ScenarioSucceeds,
			wantOps:     []string{"updateOrRotatePasswords", "getCredentialsTask", "getCredentialsTask"},
			wantTaskID:  contractmock.TaskID,
			wantStatus:  "SUCCESSFUL",
			wantSucceed: true,
			wantOutcomes: []credrotate.Outcome{
				{Key: keyESXiRoot, Status: "SUCCESSFUL", SecretChanged: true},
				{Key: keyESXiSvc, Status: "SUCCESSFUL", SecretChanged: true},
				{Key: keyVCenter, Status: "SUCCESSFUL", SecretChanged: true},
			},
			wantRotated:  []credrotate.Key{keyESXiRoot, keyESXiSvc, keyVCenter},
			wantRetained: nil,
			wantSecrets: map[credrotate.Key]credrotate.Secret{
				keyESXiRoot: {Username: contractmock.ESXiRootUser, Password: contractmock.NewESXiRootPassword, Generation: 2},
				keyESXiSvc:  {Username: contractmock.ESXiSvcUser, Password: contractmock.NewESXiSvcPassword, Generation: 2},
				keyVCenter:  {Username: contractmock.VCenterUser, Password: contractmock.NewVCenterPassword, Generation: 2},
			},
			checkErr: func(t *testing.T, err error) {
				if err != nil {
					t.Fatalf("Rotate: unexpected error %v", err)
				}
			},
		},
		{
			name:       "task settles inconsistent and the changed secrets are still published",
			scenario:   contractmock.ScenarioPartial,
			wantOps:    []string{"updateOrRotatePasswords", "getCredentialsTask", "getCredentialsTask", "cancelCredentialsTask"},
			wantTaskID: contractmock.TaskID,
			wantStatus: "INCONSISTENT",
			wantCancel: true,
			wantOutcomes: []credrotate.Outcome{
				{Key: keyESXiRoot, Status: "SUCCESSFUL", SecretChanged: true},
				{Key: keyESXiSvc, Status: "SUCCESSFUL", SecretChanged: true},
				{
					Key:       keyVCenter,
					Status:    "FAILED",
					ErrorCode: contractmock.VCenterFailureErrorCode,
					Message:   contractmock.VCenterFailureMessage,
				},
			},
			wantRotated:  []credrotate.Key{keyESXiRoot, keyESXiSvc},
			wantRetained: []credrotate.Key{keyVCenter},
			wantSecrets: map[credrotate.Key]credrotate.Secret{
				keyESXiRoot: {Username: contractmock.ESXiRootUser, Password: contractmock.NewESXiRootPassword, Generation: 2},
				keyESXiSvc:  {Username: contractmock.ESXiSvcUser, Password: contractmock.NewESXiSvcPassword, Generation: 2},
				keyVCenter:  {Username: contractmock.VCenterUser, Password: contractmock.OldVCenterPassword, Generation: 1},
			},
			checkErr: func(t *testing.T, err error) {
				var rfe *credrotate.RotationFailedError
				if !errors.As(err, &rfe) {
					t.Fatalf("Rotate: want *credrotate.RotationFailedError, got %#v", err)
				}
				if rfe.TaskID != contractmock.TaskID {
					t.Errorf("RotationFailedError.TaskID = %q, want %q", rfe.TaskID, contractmock.TaskID)
				}
				if rfe.TaskStatus != "INCONSISTENT" {
					t.Errorf("RotationFailedError.TaskStatus = %q, want %q", rfe.TaskStatus, "INCONSISTENT")
				}
				if rfe.ErrorCode != contractmock.TaskFailureErrorCode {
					t.Errorf("RotationFailedError.ErrorCode = %q, want %q", rfe.ErrorCode, contractmock.TaskFailureErrorCode)
				}
				if rfe.Message != contractmock.TaskFailureMessage {
					t.Errorf("RotationFailedError.Message = %q, want %q", rfe.Message, contractmock.TaskFailureMessage)
				}
				if len(rfe.Outcomes) != 3 {
					t.Errorf("RotationFailedError.Outcomes has %d entries, want 3", len(rfe.Outcomes))
				}
				var apiErr *credrotate.APIError
				if errors.As(err, &apiErr) {
					t.Errorf("a task that settled unsuccessfully is not an *APIError: every HTTP call succeeded, got %#v", apiErr)
				}
			},
		},
		{
			name:         "the change is refused outright",
			scenario:     contractmock.ScenarioSubmitRejected,
			wantOps:      []string{"updateOrRotatePasswords"},
			wantStatus:   "",
			wantRetained: requestOrder,
			wantSecrets:  snapshotOf(initialSecrets(), 1),
			checkErr: func(t *testing.T, err error) {
				assertAPIError(t, err, "updateOrRotatePasswords", 400,
					contractmock.SubmitRejectedErrorCode, contractmock.SubmitRejectedMessage)
			},
		},
		{
			name:       "cancel rejection still reports the issued cancel and publishes partial changes",
			scenario:   contractmock.ScenarioCancelRejected,
			wantOps:    []string{"updateOrRotatePasswords", "getCredentialsTask", "getCredentialsTask", "cancelCredentialsTask"},
			wantTaskID: contractmock.TaskID,
			wantStatus: "INCONSISTENT",
			wantCancel: true,
			wantOutcomes: []credrotate.Outcome{
				{Key: keyESXiRoot, Status: "SUCCESSFUL", SecretChanged: true},
				{Key: keyESXiSvc, Status: "SUCCESSFUL", SecretChanged: true},
				{
					Key:       keyVCenter,
					Status:    "FAILED",
					ErrorCode: contractmock.VCenterFailureErrorCode,
					Message:   contractmock.VCenterFailureMessage,
				},
			},
			wantRotated:  []credrotate.Key{keyESXiRoot, keyESXiSvc},
			wantRetained: []credrotate.Key{keyVCenter},
			wantSecrets: map[credrotate.Key]credrotate.Secret{
				keyESXiRoot: {Username: contractmock.ESXiRootUser, Password: contractmock.NewESXiRootPassword, Generation: 2},
				keyESXiSvc:  {Username: contractmock.ESXiSvcUser, Password: contractmock.NewESXiSvcPassword, Generation: 2},
				keyVCenter:  {Username: contractmock.VCenterUser, Password: contractmock.OldVCenterPassword, Generation: 1},
			},
			checkErr: func(t *testing.T, err error) {
				assertAPIError(t, err, "cancelCredentialsTask", 500,
					contractmock.CancelRejectedErrorCode, contractmock.CancelRejectedMessage)
			},
		},
		{
			name:         "polling fails after the change was accepted",
			scenario:     contractmock.ScenarioPollRejected,
			wantOps:      []string{"updateOrRotatePasswords", "getCredentialsTask"},
			wantTaskID:   contractmock.TaskID,
			wantStatus:   "IN_PROGRESS",
			wantRetained: requestOrder,
			wantSecrets:  snapshotOf(initialSecrets(), 1),
			checkErr: func(t *testing.T, err error) {
				assertAPIError(t, err, "getCredentialsTask", 500,
					contractmock.PollRejectedErrorCode, contractmock.PollRejectedMessage)
			},
		},
		{
			name:        "a terminal accepted Task is still polled once",
			scenario:    contractmock.ScenarioAcceptedTerminal,
			wantOps:     []string{"updateOrRotatePasswords", "getCredentialsTask"},
			wantTaskID:  contractmock.TaskID,
			wantStatus:  "SUCCESSFUL",
			wantSucceed: true,
			wantOutcomes: []credrotate.Outcome{
				{Key: keyESXiRoot, Status: "SUCCESSFUL", SecretChanged: true},
				{Key: keyESXiSvc, Status: "SUCCESSFUL", SecretChanged: true},
				{Key: keyVCenter, Status: "SUCCESSFUL", SecretChanged: true},
			},
			wantRotated:  requestOrder,
			wantRetained: nil,
			wantSecrets: map[credrotate.Key]credrotate.Secret{
				keyESXiRoot: {Username: contractmock.ESXiRootUser, Password: contractmock.NewESXiRootPassword, Generation: 2},
				keyESXiSvc:  {Username: contractmock.ESXiSvcUser, Password: contractmock.NewESXiSvcPassword, Generation: 2},
				keyVCenter:  {Username: contractmock.VCenterUser, Password: contractmock.NewVCenterPassword, Generation: 2},
			},
			checkErr: func(t *testing.T, err error) {
				if err != nil {
					t.Fatalf("Rotate: unexpected error %v", err)
				}
			},
		},
		{
			name:         "accepted Task missing a required member retains known metadata",
			scenario:     contractmock.ScenarioAcceptedTaskMissingName,
			wantOps:      []string{"updateOrRotatePasswords"},
			wantTaskID:   contractmock.TaskID,
			wantStatus:   "SUCCESSFUL",
			wantRetained: requestOrder,
			wantSecrets:  snapshotOf(initialSecrets(), 1),
			checkErr: func(t *testing.T, err error) {
				if err == nil {
					t.Fatal("Rotate: want an error for an accepted Task missing a required member")
				}
			},
		},
		{
			name:         "unknown credentials task status is a contract violation",
			scenario:     contractmock.ScenarioUnknownStatus,
			wantOps:      []string{"updateOrRotatePasswords", "getCredentialsTask"},
			wantTaskID:   contractmock.TaskID,
			wantStatus:   "PAUSED",
			wantRetained: requestOrder,
			wantSecrets:  snapshotOf(initialSecrets(), 1),
			checkErr: func(t *testing.T, err error) {
				if err == nil {
					t.Fatal("Rotate: want an error for a status outside the pinned vocabulary")
				}
				var apiErr *credrotate.APIError
				var failed *credrotate.RotationFailedError
				if errors.As(err, &apiErr) || errors.As(err, &failed) {
					t.Fatalf("a contract violation is neither an API nor settled-task failure, got %#v", err)
				}
			},
		},
		{
			name:         "a mismatched polled id is rejected after recording its status",
			scenario:     contractmock.ScenarioMismatchedTaskID,
			wantOps:      []string{"updateOrRotatePasswords", "getCredentialsTask"},
			wantTaskID:   contractmock.TaskID,
			wantStatus:   "FAILED",
			wantRetained: requestOrder,
			wantSecrets:  snapshotOf(initialSecrets(), 1),
			checkErr: func(t *testing.T, err error) {
				if err == nil {
					t.Fatal("Rotate: want an error when the polled task id differs from the accepted id")
				}
				var apiErr *credrotate.APIError
				var failed *credrotate.RotationFailedError
				if errors.As(err, &apiErr) || errors.As(err, &failed) {
					t.Fatalf("an id mismatch is neither an API nor settled-task failure, got %#v", err)
				}
			},
		},
		{
			name:         "user-cancelled task is reported without a DELETE",
			scenario:     contractmock.ScenarioUserCancelled,
			wantOps:      []string{"updateOrRotatePasswords", "getCredentialsTask"},
			wantTaskID:   contractmock.TaskID,
			wantStatus:   "USER_CANCELLED",
			wantRetained: requestOrder,
			wantSecrets:  snapshotOf(initialSecrets(), 1),
			checkErr: func(t *testing.T, err error) {
				var failed *credrotate.RotationFailedError
				if !errors.As(err, &failed) || failed.TaskStatus != "USER_CANCELLED" {
					t.Fatalf("Rotate: want a USER_CANCELLED *RotationFailedError, got %#v", err)
				}
			},
		},
		{
			name:        "whitespace-only new password never becomes live",
			scenario:    contractmock.ScenarioSuccessfulBlankPassword,
			wantOps:     []string{"updateOrRotatePasswords", "getCredentialsTask", "getCredentialsTask"},
			wantTaskID:  contractmock.TaskID,
			wantStatus:  "SUCCESSFUL",
			wantSucceed: true,
			wantOutcomes: []credrotate.Outcome{
				{Key: keyESXiRoot, Status: "SUCCESSFUL"},
				{Key: keyESXiSvc, Status: "SUCCESSFUL", SecretChanged: true},
				{Key: keyVCenter, Status: "SUCCESSFUL", SecretChanged: true},
			},
			wantRotated:  []credrotate.Key{keyESXiSvc, keyVCenter},
			wantRetained: []credrotate.Key{keyESXiRoot},
			wantSecrets: map[credrotate.Key]credrotate.Secret{
				keyESXiRoot: {Username: contractmock.ESXiRootUser, Password: contractmock.OldESXiRootPassword, Generation: 1},
				keyESXiSvc:  {Username: contractmock.ESXiSvcUser, Password: contractmock.NewESXiSvcPassword, Generation: 2},
				keyVCenter:  {Username: contractmock.VCenterUser, Password: contractmock.NewVCenterPassword, Generation: 2},
			},
			checkErr: func(t *testing.T, err error) {
				if err != nil {
					t.Fatalf("Rotate: unexpected error %v", err)
				}
			},
		},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			mock := contractmock.Start(t, tc.scenario, contractmock.Hooks{})
			store := newStore(t)
			client := newClient(t, mock)

			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()

			got, err := client.Rotate(ctx, store, rotateRequest("ROTATE", nil), pollInterval)
			tc.checkErr(t, err)

			if got.TaskID != tc.wantTaskID {
				t.Errorf("Result.TaskID = %q, want %q", got.TaskID, tc.wantTaskID)
			}
			if got.TaskStatus != tc.wantStatus {
				t.Errorf("Result.TaskStatus = %q, want %q", got.TaskStatus, tc.wantStatus)
			}
			if got.Succeeded != tc.wantSucceed {
				t.Errorf("Result.Succeeded = %v, want %v", got.Succeeded, tc.wantSucceed)
			}
			if got.Cancelled != tc.wantCancel {
				t.Errorf("Result.Cancelled = %v, want %v", got.Cancelled, tc.wantCancel)
			}
			if !reflect.DeepEqual(got.Outcomes, tc.wantOutcomes) {
				t.Errorf("Result.Outcomes =\n %#v\nwant\n %#v", got.Outcomes, tc.wantOutcomes)
			}
			if !sameKeys(got.Rotated, tc.wantRotated) {
				t.Errorf("Result.Rotated = %v, want %v", got.Rotated, tc.wantRotated)
			}
			if !sameKeys(got.Retained, tc.wantRetained) {
				t.Errorf("Result.Retained = %v, want %v", got.Retained, tc.wantRetained)
			}
			if diff := diffSecrets(store.Snapshot(), tc.wantSecrets); diff != "" {
				t.Errorf("store after Rotate:\n%s", diff)
			}

			if ops := mock.OperationSequence(); !reflect.DeepEqual(ops, tc.wantOps) {
				t.Errorf("served operations = %v, want %v", ops, tc.wantOps)
			}
			assertNoViolations(t, mock)
			assertTargets(t, mock)
		})
	}
}

// -----------------------------------------------------------------------------
// Wire shape
// -----------------------------------------------------------------------------

func TestSubmitRequestWireShape(t *testing.T) {
	t.Parallel()

	baseMembers := map[string][]string{
		"$":                            {"elements", "operationType"},
		"$.elements[0]":                {"credentials", "resourceName", "resourceType"},
		"$.elements[0].credentials[0]": {"accountType", "credentialType", "username"},
		"$.elements[0].credentials[1]": {"credentialType", "username"},
		"$.elements[1]":                {"credentials", "resourceId", "resourceName", "resourceType"},
		"$.elements[1].credentials[0]": {"accountType", "credentialType", "username"},
	}

	cases := []struct {
		name          string
		operationType string
		policy        *credrotate.AutoRotatePolicy
		want          map[string][]string
	}{
		{
			name:          "rotate omits every unset optional member",
			operationType: "ROTATE",
			want:          baseMembers,
		},
		{
			name:          "update carries a password on every credential",
			operationType: "UPDATE",
			want: withMembers(baseMembers, map[string][]string{
				"$.elements[0].credentials[0]": {"accountType", "credentialType", "password", "username"},
				"$.elements[0].credentials[1]": {"credentialType", "password", "username"},
				"$.elements[1].credentials[0]": {"accountType", "credentialType", "password", "username"},
			}),
		},
		{
			name:          "a required boolean is sent even when it is false",
			operationType: "ROTATE",
			policy:        &credrotate.AutoRotatePolicy{Enable: false},
			want: withMembers(baseMembers, map[string][]string{
				"$":                  {"autoRotatePolicy", "elements", "operationType"},
				"$.autoRotatePolicy": {"enableAutoRotatePolicy"},
			}),
		},
		{
			name:          "an optional number is sent only when it is nonzero",
			operationType: "ROTATE",
			policy:        &credrotate.AutoRotatePolicy{Enable: true, FrequencyInDays: 90},
			want: withMembers(baseMembers, map[string][]string{
				"$":                  {"autoRotatePolicy", "elements", "operationType"},
				"$.autoRotatePolicy": {"enableAutoRotatePolicy", "frequencyInDays"},
			}),
		},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			mock := contractmock.Start(t, contractmock.ScenarioSucceeds, contractmock.Hooks{})
			store := newStore(t)
			client := newClient(t, mock)

			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()

			if _, err := client.Rotate(ctx, store, rotateRequest(tc.operationType, tc.policy), pollInterval); err != nil {
				t.Fatalf("Rotate: %v", err)
			}
			assertNoViolations(t, mock)

			submit := requestFor(t, mock, "updateOrRotatePasswords")

			var body any
			if err := json.Unmarshal(submit.Body, &body); err != nil {
				t.Fatalf("submit body is not valid JSON: %v\nbody: %s", err, submit.Body)
			}
			got := memberSets(body, "$")
			for _, path := range sortedPaths(unionPaths(got, tc.want)) {
				g, gok := got[path]
				w, wok := tc.want[path]
				switch {
				case !wok:
					t.Errorf("request body carries an unexpected object at %s with members %v", path, g)
				case !gok:
					t.Errorf("request body is missing the object at %s, expected members %v", path, w)
				case !reflect.DeepEqual(g, w):
					t.Errorf("members at %s = %v, want %v", path, g, w)
				}
			}

			if tc.operationType != "UPDATE" && strings.Contains(string(submit.Body), "password") {
				t.Errorf("a %s never carries a password: the appliance generates it; body was %s",
					tc.operationType, submit.Body)
			}
		})
	}
}

func TestRequestTransport(t *testing.T) {
	t.Parallel()

	mock := contractmock.Start(t, contractmock.ScenarioPartial, contractmock.Hooks{})
	store := newStore(t)
	client := newClient(t, mock)

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	if _, err := client.Rotate(ctx, store, rotateRequest("ROTATE", nil), pollInterval); err == nil {
		t.Fatal("Rotate: want an error for a task that settled INCONSISTENT")
	}
	assertNoViolations(t, mock)
	assertTargets(t, mock)

	for i, r := range mock.Requests() {
		label := fmt.Sprintf("request %d (%s %s)", i, r.Method, r.RawTarget)

		if n := len(r.Header.Values("Authorization")); n != 1 {
			t.Errorf("%s: %d Authorization headers, want exactly 1", label, n)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer "+contractmock.AccessToken {
			t.Errorf("%s: Authorization = %q, want %q", label, got, "Bearer "+contractmock.AccessToken)
		}
		if n := len(r.Header.Values("Accept")); n != 1 {
			t.Errorf("%s: %d Accept headers, want exactly 1", label, n)
		}
		if got := r.Header.Get("Accept"); got != "application/json" {
			t.Errorf("%s: Accept = %q, want %q", label, got, "application/json")
		}
		if r.Query != "" || strings.Contains(r.RawTarget, "?") {
			t.Errorf("%s: no operation in the contract takes a query parameter", label)
		}

		switch r.OperationID {
		case "updateOrRotatePasswords":
			if n := len(r.Header.Values("Content-Type")); n != 1 {
				t.Errorf("%s: %d Content-Type headers, want exactly 1", label, n)
			}
			if got := r.Header.Get("Content-Type"); got != "application/json" {
				t.Errorf("%s: Content-Type = %q, want %q", label, got, "application/json")
			}
			if len(r.Body) == 0 {
				t.Errorf("%s: want a request body", label)
			}
		case "getCredentialsTask", "cancelCredentialsTask":
			if n := len(r.Header.Values("Content-Type")); n != 0 {
				t.Errorf("%s: sends no body, so it must carry no Content-Type header, got %d", label, n)
			}
			if len(r.Body) != 0 {
				t.Errorf("%s: sends no body, got %d bytes: %s", label, len(r.Body), r.Body)
			}
		default:
			t.Errorf("%s: matched no contract operation", label)
		}
	}
}

// -----------------------------------------------------------------------------
// In-flight borrowers
// -----------------------------------------------------------------------------

func TestNoBorrowedSecretIsStrandedBySubmit(t *testing.T) {
	t.Parallel()

	store := newStore(t)

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	var outstanding atomic.Int64
	// latecomer is a borrower that shows up after the seal is definitely in
	// place, which is what reaching the submit handler proves.
	latecomer := make(chan credrotate.Secret, 1)
	var latecomerOnce sync.Once
	var latecomerWG sync.WaitGroup

	mock := contractmock.Start(t, contractmock.ScenarioSucceeds, contractmock.Hooks{
		BeforeSubmit: func() {
			if n := outstanding.Load(); n != 0 {
				t.Errorf("updateOrRotatePasswords was sent while %d borrower(s) still held an old secret", n)
			}

			latecomerOnce.Do(func() {
				latecomerWG.Add(1)
				go func() {
					defer latecomerWG.Done()
					secret, release, err := store.Acquire(ctx, keyESXiRoot)
					if err != nil {
						close(latecomer)
						return
					}
					release()
					latecomer <- secret
				}()

				// From here until the task settles the credential is being
				// changed on the appliance, so nobody may be handed it.
				select {
				case secret := <-latecomer:
					t.Errorf("Acquire handed out %+v while that credential was being changed on the appliance", secret)
				case <-time.After(200 * time.Millisecond):
				}
			})
		},
	})
	client := newClient(t, mock)

	// One borrower is already using the old ESXi root secret.
	secret, release, err := store.Acquire(ctx, keyESXiRoot)
	if err != nil {
		t.Fatalf("Acquire: %v", err)
	}
	outstanding.Add(1)
	if secret.Password != contractmock.OldESXiRootPassword || secret.Generation != 1 {
		t.Fatalf("Acquire returned %+v, want the generation 1 secret", secret)
	}

	type outcome struct {
		res credrotate.Result
		err error
	}
	done := make(chan outcome, 1)
	go func() {
		res, err := client.Rotate(ctx, store, rotateRequest("ROTATE", nil), pollInterval)
		done <- outcome{res, err}
	}()

	select {
	case got := <-done:
		t.Fatalf("Rotate finished while a borrower still held the old secret (err=%v)", got.err)
	case <-time.After(250 * time.Millisecond):
	}

	if n := len(mock.Requests()); n != 0 {
		t.Fatalf("the appliance saw %d request(s) before the borrowed secret was handed back; "+
			"a rotation must drain in-flight borrowers first", n)
	}

	outstanding.Add(-1)
	release()

	select {
	case got := <-done:
		if got.err != nil {
			t.Fatalf("Rotate: %v", got.err)
		}
		if !got.res.Succeeded {
			t.Errorf("Result.Succeeded = false, want true")
		}
	case <-time.After(30 * time.Second):
		t.Fatal("Rotate did not finish after the borrowed secret was handed back")
	}

	latecomerWG.Wait()
	select {
	case secret, ok := <-latecomer:
		if !ok {
			t.Fatal("the waiting borrower failed instead of being handed the new secret")
		}
		if secret.Password != contractmock.NewESXiRootPassword || secret.Generation != 2 {
			t.Errorf("the waiting borrower got %+v, want the generation 2 secret", secret)
		}
	default:
		t.Fatal("the waiting borrower never ran")
	}

	assertNoViolations(t, mock)
}

func TestCallingAnOldReleaseTwiceCannotReleaseANewerLease(t *testing.T) {
	t.Parallel()

	store := newStore(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	_, oldRelease, err := store.Acquire(ctx, keyESXiRoot)
	if err != nil {
		t.Fatalf("first Acquire: %v", err)
	}
	oldRelease()

	_, currentRelease, err := store.Acquire(ctx, keyESXiRoot)
	if err != nil {
		t.Fatalf("second Acquire: %v", err)
	}
	// The first lease is already over. Calling its release function again must
	// not decrement the newer lease that happens to use the same key.
	oldRelease()

	mock := contractmock.Start(t, contractmock.ScenarioSucceeds, contractmock.Hooks{})
	client := newClient(t, mock)
	done := make(chan error, 1)
	go func() {
		_, err := client.Rotate(ctx, store, rotateRequest("ROTATE", nil), pollInterval)
		done <- err
	}()

	select {
	case err := <-done:
		currentRelease()
		t.Fatalf("Rotate finished while the newer lease was still held (err=%v)", err)
	case <-time.After(150 * time.Millisecond):
	}
	if n := len(mock.Requests()); n != 0 {
		currentRelease()
		t.Fatalf("the appliance saw %d request(s) while the newer lease was still held", n)
	}

	currentRelease()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("Rotate after releasing the newer lease: %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("Rotate did not finish after the newer lease was released")
	}
}

func TestConcurrentBorrowersAcrossRotation(t *testing.T) {
	t.Parallel()

	var outstanding atomic.Int64
	mock := contractmock.Start(t, contractmock.ScenarioPartial, contractmock.Hooks{
		BeforeSubmit: func() {
			if n := outstanding.Load(); n != 0 {
				t.Errorf("updateOrRotatePasswords was sent while %d borrower(s) still held an old secret", n)
			}
		},
	})
	store := newStore(t)
	client := newClient(t, mock)

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	// live maps every secret a borrower may legitimately observe. Anything else
	// means the store handed out a torn or retired value.
	live := map[credrotate.Key]map[uint64]string{
		keyESXiRoot: {1: contractmock.OldESXiRootPassword, 2: contractmock.NewESXiRootPassword},
		keyESXiSvc:  {1: contractmock.OldESXiSvcPassword, 2: contractmock.NewESXiSvcPassword},
		keyVCenter:  {1: contractmock.OldVCenterPassword},
	}

	stop := make(chan struct{})
	var wg sync.WaitGroup
	var mu sync.Mutex
	var bad []string

	for w := 0; w < 8; w++ {
		for _, key := range requestOrder {
			wg.Add(1)
			go func(key credrotate.Key) {
				defer wg.Done()
				for {
					select {
					case <-stop:
						return
					default:
					}
					secret, release, err := store.Acquire(ctx, key)
					if err != nil {
						mu.Lock()
						bad = append(bad, fmt.Sprintf("Acquire(%v): %v", key, err))
						mu.Unlock()
						return
					}
					// The counter is raised only while the lease is held and
					// lowered before it is handed back, so a nonzero reading
					// always means a real borrower is outstanding.
					outstanding.Add(1)
					want, ok := live[key][secret.Generation]
					if !ok || secret.Password != want || secret.Username != key.Username {
						mu.Lock()
						bad = append(bad, fmt.Sprintf("Acquire(%v) returned %+v, which is not a secret that was ever live", key, secret))
						mu.Unlock()
					}
					outstanding.Add(-1)
					release()
				}
			}(key)
		}
	}

	res, err := client.Rotate(ctx, store, rotateRequest("ROTATE", nil), pollInterval)
	close(stop)
	wg.Wait()

	var rfe *credrotate.RotationFailedError
	if !errors.As(err, &rfe) {
		t.Fatalf("Rotate: want *credrotate.RotationFailedError, got %#v", err)
	}
	if !sameKeys(res.Rotated, []credrotate.Key{keyESXiRoot, keyESXiSvc}) {
		t.Errorf("Result.Rotated = %v, want %v", res.Rotated, []credrotate.Key{keyESXiRoot, keyESXiSvc})
	}
	if !sameKeys(res.Retained, []credrotate.Key{keyVCenter}) {
		t.Errorf("Result.Retained = %v, want %v", res.Retained, []credrotate.Key{keyVCenter})
	}

	mu.Lock()
	defer mu.Unlock()
	for _, b := range bad {
		t.Error(b)
	}
	assertNoViolations(t, mock)
}

func TestRotateStopsWhenContextEnds(t *testing.T) {
	t.Parallel()

	mock := contractmock.Start(t, contractmock.ScenarioSucceeds, contractmock.Hooks{})
	store := newStore(t)
	client := newClient(t, mock)

	ctx, cancel := context.WithCancel(context.Background())

	_, release, err := store.Acquire(ctx, keyVCenter)
	if err != nil {
		t.Fatalf("Acquire: %v", err)
	}
	defer release()

	done := make(chan error, 1)
	go func() {
		_, err := client.Rotate(ctx, store, rotateRequest("ROTATE", nil), pollInterval)
		done <- err
	}()

	time.Sleep(50 * time.Millisecond)
	cancel()

	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Errorf("Rotate: want an error wrapping context.Canceled, got %v", err)
		}
	case <-time.After(30 * time.Second):
		t.Fatal("Rotate did not stop when its context was cancelled")
	}

	if n := len(mock.Requests()); n != 0 {
		t.Errorf("the appliance saw %d request(s) although the drain never completed", n)
	}

	// The seal must be lifted even though the rotation gave up, or every later
	// borrower is stranded waiting on a rotation that will never settle.
	waitCtx, waitCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer waitCancel()
	got, rel, err := store.Acquire(waitCtx, keyESXiRoot)
	if err != nil {
		t.Fatalf("Acquire after an abandoned rotation: %v", err)
	}
	rel()
	if got.Password != contractmock.OldESXiRootPassword || got.Generation != 1 {
		t.Errorf("Acquire after an abandoned rotation returned %+v, want the untouched generation 1 secret", got)
	}
}

func TestRotateStopsPromptlyWhenContextEndsDuringPolling(t *testing.T) {
	t.Parallel()

	mock := contractmock.Start(t, contractmock.ScenarioPendingForever, contractmock.Hooks{})
	store := newStore(t)
	client := newClient(t, mock)
	ctx, cancel := context.WithCancel(context.Background())

	type outcome struct {
		result credrotate.Result
		err    error
	}
	done := make(chan outcome, 1)
	go func() {
		result, err := client.Rotate(ctx, store, rotateRequest("ROTATE", nil), time.Hour)
		done <- outcome{result: result, err: err}
	}()

	deadline := time.NewTimer(5 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(time.Millisecond)
	defer ticker.Stop()
waitForPoll:
	for {
		select {
		case <-ticker.C:
			for _, op := range mock.OperationSequence() {
				if op == "getCredentialsTask" {
					break waitForPoll
				}
			}
		case <-deadline.C:
			cancel()
			t.Fatal("Rotate never began polling")
		}
	}

	// Let the first in-process or loopback response reach Rotate so it is
	// waiting on the deliberately huge poll interval when cancellation lands.
	time.Sleep(50 * time.Millisecond)
	cancel()

	select {
	case got := <-done:
		if !errors.Is(got.err, context.Canceled) {
			t.Errorf("Rotate: want an error wrapping context.Canceled, got %v", got.err)
		}
		if got.result.TaskID != contractmock.TaskID || got.result.TaskStatus != "PENDING" {
			t.Errorf("Result after cancellation = %#v, want accepted id and last status PENDING", got.result)
		}
	case <-time.After(time.Second):
		t.Fatal("Rotate remained asleep on pollInterval after its context was cancelled")
	}

	if ops := mock.OperationSequence(); !reflect.DeepEqual(ops, []string{"updateOrRotatePasswords", "getCredentialsTask"}) {
		t.Errorf("served operations after polling cancellation = %v", ops)
	}
	waitCtx, waitCancel := context.WithTimeout(context.Background(), time.Second)
	defer waitCancel()
	secret, release, err := store.Acquire(waitCtx, keyESXiRoot)
	if err != nil {
		t.Fatalf("Acquire after polling cancellation: %v", err)
	}
	release()
	if secret.Generation != 1 || secret.Password != contractmock.OldESXiRootPassword {
		t.Errorf("Acquire after polling cancellation returned %+v, want the old generation 1 secret", secret)
	}
}

// -----------------------------------------------------------------------------
// Validation
// -----------------------------------------------------------------------------

func TestClientConstruction(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name    string
		baseURL string
		token   string
		wantErr bool
	}{
		{name: "https service root", baseURL: "https://sddc-manager.vcf.local", token: "t"},
		{name: "http service root with a port", baseURL: "http://127.0.0.1:8443", token: "t"},
		{name: "empty service root", baseURL: "", token: "t", wantErr: true},
		{name: "non http scheme", baseURL: "ftp://sddc-manager.vcf.local", token: "t", wantErr: true},
		{name: "no scheme", baseURL: "sddc-manager.vcf.local", token: "t", wantErr: true},
		{name: "no host", baseURL: "https://", token: "t", wantErr: true},
		{name: "blank token", baseURL: "https://sddc-manager.vcf.local", token: "", wantErr: true},
		{name: "token with a newline", baseURL: "https://sddc-manager.vcf.local", token: "abc\ndef", wantErr: true},
		{name: "token with a carriage return", baseURL: "https://sddc-manager.vcf.local", token: "abc\rdef", wantErr: true},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			c, err := credrotate.NewClient(tc.baseURL, tc.token, nil)
			if tc.wantErr {
				if err == nil {
					t.Fatalf("NewClient(%q, %q): want an error, got %#v", tc.baseURL, tc.token, c)
				}
				return
			}
			if err != nil {
				t.Fatalf("NewClient(%q, %q): %v", tc.baseURL, tc.token, err)
			}
			if c == nil {
				t.Fatal("NewClient returned a nil client and a nil error")
			}
		})
	}
}

func TestRotateRejectsBadRequestsBeforeAnyCall(t *testing.T) {
	t.Parallel()

	mutate := func(f func(*credrotate.RotateRequest)) credrotate.RotateRequest {
		r := rotateRequest("ROTATE", nil)
		f(&r)
		return r
	}

	cases := []struct {
		name string
		req  credrotate.RotateRequest
	}{
		{
			name: "blank operation type",
			req:  mutate(func(r *credrotate.RotateRequest) { r.OperationType = "" }),
		},
		{
			name: "operation type outside the pinned vocabulary",
			req:  mutate(func(r *credrotate.RotateRequest) { r.OperationType = "ROLL" }),
		},
		{
			name: "resource type added only in the 9.1.0.0 revision (VSP)",
			req:  mutate(func(r *credrotate.RotateRequest) { r.Resources[1].ResourceType = "VSP" }),
		},
		{
			name: "resource type added only in the 9.1.0.0 revision (HCX_MANAGER)",
			req:  mutate(func(r *credrotate.RotateRequest) { r.Resources[1].ResourceType = "HCX_MANAGER" }),
		},
		{
			name: "blank resource type",
			req:  mutate(func(r *credrotate.RotateRequest) { r.Resources[0].ResourceType = "" }),
		},
		{
			name: "blank resource name",
			req:  mutate(func(r *credrotate.RotateRequest) { r.Resources[0].ResourceName = "" }),
		},
		{
			name: "blank username",
			req:  mutate(func(r *credrotate.RotateRequest) { r.Resources[0].Credentials[1].Username = "" }),
		},
		{
			name: "no resources",
			req:  mutate(func(r *credrotate.RotateRequest) { r.Resources = nil }),
		},
		{
			name: "a resource with no credentials",
			req:  mutate(func(r *credrotate.RotateRequest) { r.Resources[1].Credentials = nil }),
		},
		{
			name: "a rotate that supplies a password the appliance generates itself",
			req:  mutate(func(r *credrotate.RotateRequest) { r.Resources[0].Credentials[0].Password = "hunter2" }),
		},
		{
			name: "an update with a credential that supplies no password",
			req: func() credrotate.RotateRequest {
				r := rotateRequest("UPDATE", nil)
				r.Resources[0].Credentials[1].Password = ""
				return r
			}(),
		},
		{
			name: "a credential the store does not hold",
			req: mutate(func(r *credrotate.RotateRequest) {
				r.Resources[0].Credentials[0].Username = "nobody"
			}),
		},
		{
			name: "a duplicated credential",
			req: mutate(func(r *credrotate.RotateRequest) {
				r.Resources[0].Credentials[1].Username = contractmock.ESXiRootUser
			}),
		},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()

			mock := contractmock.Start(t, contractmock.ScenarioSucceeds, contractmock.Hooks{})
			store := newStore(t)
			client := newClient(t, mock)

			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()

			if _, err := client.Rotate(ctx, store, tc.req, pollInterval); err == nil {
				t.Fatal("Rotate: want an error")
			}
			if n := len(mock.Requests()); n != 0 {
				t.Errorf("a rejected request must not reach the appliance, got %d request(s)", n)
			}
			if diff := diffSecrets(store.Snapshot(), snapshotOf(initialSecrets(), 1)); diff != "" {
				t.Errorf("a rejected request must leave the store untouched:\n%s", diff)
			}

			// The seal, if one was ever taken, must be lifted.
			got, rel, err := store.Acquire(ctx, keyESXiSvc)
			if err != nil {
				t.Fatalf("Acquire after a rejected rotation: %v", err)
			}
			rel()
			if got.Generation != 1 {
				t.Errorf("Acquire after a rejected rotation returned generation %d, want 1", got.Generation)
			}
		})
	}
}

func TestRotateRejectsBadArguments(t *testing.T) {
	t.Parallel()

	mock := contractmock.Start(t, contractmock.ScenarioSucceeds, contractmock.Hooks{})
	client := newClient(t, mock)
	ctx := context.Background()

	if _, err := client.Rotate(ctx, nil, rotateRequest("ROTATE", nil), pollInterval); err == nil {
		t.Error("Rotate with a nil store: want an error")
	}
	if _, err := client.Rotate(ctx, newStore(t), rotateRequest("ROTATE", nil), 0); err == nil {
		t.Error("Rotate with a zero poll interval: want an error")
	}
	if _, err := client.Rotate(ctx, newStore(t), rotateRequest("ROTATE", nil), -time.Second); err == nil {
		t.Error("Rotate with a negative poll interval: want an error")
	}
	if n := len(mock.Requests()); n != 0 {
		t.Errorf("a rejected call must not reach the appliance, got %d request(s)", n)
	}
}

func TestSecretsNeverLeakIntoErrorsOrResults(t *testing.T) {
	t.Parallel()

	forbidden := []string{
		contractmock.AccessToken,
		contractmock.OldESXiRootPassword,
		contractmock.OldESXiSvcPassword,
		contractmock.OldVCenterPassword,
		contractmock.NewESXiRootPassword,
		contractmock.NewESXiSvcPassword,
		contractmock.NewVCenterPassword,
	}

	scenarios := []contractmock.Scenario{
		contractmock.ScenarioSucceeds,
		contractmock.ScenarioPartial,
		contractmock.ScenarioSubmitRejected,
		contractmock.ScenarioPollRejected,
	}

	for i, sc := range scenarios {
		sc := sc
		t.Run(fmt.Sprintf("scenario-%d", i), func(t *testing.T) {
			t.Parallel()

			mock := contractmock.Start(t, sc, contractmock.Hooks{})
			store := newStore(t)
			client := newClient(t, mock)

			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()

			res, err := client.Rotate(ctx, store, rotateRequest("UPDATE", nil), pollInterval)

			var text string
			if err != nil {
				text = err.Error()
			}
			text += fmt.Sprintf(" %+v", res.Outcomes)
			text += fmt.Sprintf(" %+v %+v", res.Rotated, res.Retained)
			text += " " + res.TaskID + " " + res.TaskStatus

			for _, secret := range forbidden {
				if strings.Contains(text, secret) {
					t.Errorf("a secret or the access token reached an error or result: %q", secret)
				}
			}
		})
	}
}

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------

func assertAPIError(t *testing.T, err error, operationID string, status int, code, message string) {
	t.Helper()
	var apiErr *credrotate.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("want *credrotate.APIError, got %#v", err)
	}
	if apiErr.OperationID != operationID {
		t.Errorf("APIError.OperationID = %q, want %q", apiErr.OperationID, operationID)
	}
	if apiErr.StatusCode != status {
		t.Errorf("APIError.StatusCode = %d, want %d", apiErr.StatusCode, status)
	}
	if apiErr.ErrorCode != code {
		t.Errorf("APIError.ErrorCode = %q, want %q", apiErr.ErrorCode, code)
	}
	if apiErr.Message != message {
		t.Errorf("APIError.Message = %q, want %q", apiErr.Message, message)
	}
}

func assertNoViolations(t *testing.T, mock *contractmock.Server) {
	t.Helper()
	for _, v := range mock.Violations() {
		t.Errorf("contract violation: %s", v)
	}
}

func assertTargets(t *testing.T, mock *contractmock.Server) {
	t.Helper()
	taskPath := "/v1/credentials/tasks/" + url.PathEscape(contractmock.TaskID)
	for i, r := range mock.Requests() {
		label := fmt.Sprintf("request %d", i)
		switch r.OperationID {
		case "updateOrRotatePasswords":
			if r.Method != "PATCH" || r.RawTarget != "/v1/credentials" {
				t.Errorf("%s: %s %s, want PATCH /v1/credentials", label, r.Method, r.RawTarget)
			}
		case "getCredentialsTask":
			if r.Method != "GET" || r.RawTarget != taskPath {
				t.Errorf("%s: %s %s, want GET %s", label, r.Method, r.RawTarget, taskPath)
			}
			if r.PathParams["id"] != contractmock.TaskID {
				t.Errorf("%s: polled task id %q, want %q", label, r.PathParams["id"], contractmock.TaskID)
			}
		case "cancelCredentialsTask":
			if r.Method != "DELETE" || r.RawTarget != taskPath {
				t.Errorf("%s: %s %s, want DELETE %s", label, r.Method, r.RawTarget, taskPath)
			}
			if r.PathParams["id"] != contractmock.TaskID {
				t.Errorf("%s: cancelled task id %q, want %q", label, r.PathParams["id"], contractmock.TaskID)
			}
		default:
			t.Errorf("%s: %s %s matched no contract operation", label, r.Method, r.RawTarget)
		}
	}
}

func requestFor(t *testing.T, mock *contractmock.Server, operationID string) contractmock.Request {
	t.Helper()
	for _, r := range mock.Requests() {
		if r.OperationID == operationID {
			return r
		}
	}
	t.Fatalf("the appliance never saw %s", operationID)
	return contractmock.Request{}
}

// memberSets walks a decoded JSON body and records, for every object it
// reaches, the sorted names of the members actually present. A member the
// client omitted simply never appears.
func memberSets(v any, path string) map[string][]string {
	out := map[string][]string{}
	collectMembers(v, path, out)
	return out
}

func collectMembers(v any, path string, out map[string][]string) {
	switch t := v.(type) {
	case map[string]any:
		names := make([]string, 0, len(t))
		for k := range t {
			names = append(names, k)
		}
		sort.Strings(names)
		out[path] = names
		for _, k := range names {
			collectMembers(t[k], path+"."+k, out)
		}
	case []any:
		for i, e := range t {
			collectMembers(e, fmt.Sprintf("%s[%d]", path, i), out)
		}
	}
}

func unionPaths(a, b map[string][]string) map[string]bool {
	out := map[string]bool{}
	for k := range a {
		out[k] = true
	}
	for k := range b {
		out[k] = true
	}
	return out
}

func sortedPaths(set map[string]bool) []string {
	out := make([]string, 0, len(set))
	for k := range set {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func withMembers(base, overrides map[string][]string) map[string][]string {
	out := make(map[string][]string, len(base)+len(overrides))
	for k, v := range base {
		out[k] = v
	}
	for k, v := range overrides {
		out[k] = v
	}
	return out
}

func snapshotOf(secrets map[credrotate.Key]credrotate.Secret, generation uint64) map[credrotate.Key]credrotate.Secret {
	out := make(map[credrotate.Key]credrotate.Secret, len(secrets))
	for k, v := range secrets {
		v.Generation = generation
		out[k] = v
	}
	return out
}

func diffSecrets(got, want map[credrotate.Key]credrotate.Secret) string {
	var b strings.Builder
	seen := map[credrotate.Key]bool{}
	for k := range got {
		seen[k] = true
	}
	for k := range want {
		seen[k] = true
	}
	keys := make([]credrotate.Key, 0, len(seen))
	for k := range seen {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].ResourceName != keys[j].ResourceName {
			return keys[i].ResourceName < keys[j].ResourceName
		}
		return keys[i].Username < keys[j].Username
	})
	for _, k := range keys {
		g, gok := got[k]
		w, wok := want[k]
		switch {
		case !gok:
			fmt.Fprintf(&b, "  %v: missing, want %+v\n", k, w)
		case !wok:
			fmt.Fprintf(&b, "  %v: unexpected %+v\n", k, g)
		case g != w:
			fmt.Fprintf(&b, "  %v: got %+v, want %+v\n", k, g, w)
		}
	}
	return b.String()
}

func sameKeys(got, want []credrotate.Key) bool {
	if len(got) != len(want) {
		return false
	}
	for i := range got {
		if got[i] != want[i] {
			return false
		}
	}
	return true
}
