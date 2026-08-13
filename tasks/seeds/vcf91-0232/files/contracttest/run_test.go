package contracttest

import (
	"context"
	"net/http"
	"reflect"
	"testing"

	"example.com/vcf/fleetlcm/fleetrun"
	"example.com/vcf/fleetlcm/internal/mocklcm"
)

// singleComponentPlan is a plan naming one component that is not installed and
// that the depot carries.
func singleComponentPlan() map[string]any {
	return map[string]any{
		"scope": "FLEET",
		"depot": depotSpec(),
		"components": []any{
			map[string]any{
				"componentType": "VCF_OPERATIONS",
				"fqdn":          "ops-a.vcf.example.com",
				"password":      "VMw@re123!Ops",
			},
		},
	}
}

func count401(m *mocklcm.Mock) int {
	n := 0
	for _, s := range statuses(m) {
		if s == http.StatusUnauthorized {
			n++
		}
	}
	return n
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

// TestRunOutcomes covers what a run does with the answers the service gives it,
// and what it must not do when a credential goes stale.
func TestRunOutcomes(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name      string
		plan      map[string]any
		task      mocklcm.TaskScript
		tokens    []string
		tokenUses int
		// refuseRefresh makes the credential source unable to produce a
		// replacement.
		refuseRefresh bool
		// credTokens overrides the tokens the run is given, so it can be handed
		// a replacement the service will not accept.
		credTokens []string

		wantErr        bool
		wantOutcome    string
		wantOperations []string
		wantRefreshes  int
		check          func(t *testing.T, m *mocklcm.Mock, report reportView)
	}{
		{
			// A retriable failure is retried once, in place. The install is
			// raised once and only once.
			name:        "retried task succeeds",
			plan:        singleComponentPlan(),
			task:        mocklcm.FailThenSucceedScript(),
			tokens:      []string{"tok-alpha"},
			tokenUses:   0,
			wantOutcome: "succeeded",
			wantOperations: []string{
				"getComponents", "resolveDepotComponents", "createComponents",
				"getTask", "getTask",
				"retryTask",
				"getTask", "getTask",
			},
			check: func(t *testing.T, m *mocklcm.Mock, report reportView) {
				retry := only(t, m, "retryTask")
				// The specification pins this action in the query string.
				if want := taskTarget + "?action=retry"; retry.Target != want {
					t.Errorf("retryTask target %q, want %q", retry.Target, want)
				}
				if len(retry.BodyRaw) != 0 {
					t.Errorf("retryTask carries a body: %s", retry.BodyRaw)
				}
				if report.Task == nil || !report.Task.Retried {
					t.Errorf("report does not record that the task was retried: %s", pretty(report))
				}
				if report.Task != nil && report.Task.Status != "SUCCEEDED" {
					t.Errorf("task status %q, want SUCCEEDED", report.Task.Status)
				}
			},
		},
		{
			// A failure the service will not retry is a result, not an error.
			name:        "unretriable failure is reported",
			plan:        singleComponentPlan(),
			task:        mocklcm.TerminalFailureScript(),
			tokens:      []string{"tok-alpha"},
			tokenUses:   0,
			wantOutcome: "failed",
			wantOperations: []string{
				"getComponents", "resolveDepotComponents", "createComponents",
				"getTask", "getTask",
			},
			check: func(t *testing.T, m *mocklcm.Mock, report reportView) {
				if report.Failure == nil {
					t.Fatalf("a failed run carries no failure: %s", pretty(report))
				}
				if report.Failure.TaskID != taskID {
					t.Errorf("failure names task %q, want %q", report.Failure.TaskID, taskID)
				}
				if report.Failure.FailedStage != "package-deploy" {
					t.Errorf("failedStage %q, want package-deploy", report.Failure.FailedStage)
				}
				want := []string{
					"com.broadcom.lcm.ops.component.deploy.insufficient_capacity",
					"com.broadcom.lcm.ops.component.deploy.rollback_done",
				}
				var got []string
				for _, e := range report.Failure.Errors {
					got = append(got, e.ID)
				}
				if !reflect.DeepEqual(got, want) {
					t.Errorf("failure errors %v, want %v (the failed stage's ERROR messages, those only)", got, want)
				}
			},
		},
		{
			// The answer that raises the task never settles it, whatever status
			// it carries.
			name: "the raising answer does not settle the task",
			plan: singleComponentPlan(),
			task: mocklcm.TaskScript{
				Accepted: mocklcm.StatusSucceeded,
				Poll:     []string{mocklcm.StatusRunning, mocklcm.StatusSucceeded},
			},
			tokens:      []string{"tok-alpha"},
			tokenUses:   0,
			wantOutcome: "succeeded",
			wantOperations: []string{
				"getComponents", "resolveDepotComponents", "createComponents",
				"getTask", "getTask",
			},
		},
		{
			// When no stage is marked failed, the report falls back to the task's
			// own ERROR messages and leaves failedStage absent.
			name: "task-level failure messages are reported",
			plan: singleComponentPlan(),
			task: mocklcm.TaskScript{
				Accepted: mocklcm.StatusPending,
				Poll:     []string{mocklcm.StatusRunning, mocklcm.StatusFailed},
				Failure: &mocklcm.TaskFailure{Errors: []mocklcm.TaskError{
					{ID: "com.broadcom.lcm.task.failed", DefaultMessage: "The task failed outside a named stage."},
				}},
			},
			tokens:      []string{"tok-alpha"},
			tokenUses:   0,
			wantOutcome: "failed",
			wantOperations: []string{
				"getComponents", "resolveDepotComponents", "createComponents", "getTask", "getTask",
			},
			check: func(t *testing.T, m *mocklcm.Mock, report reportView) {
				if report.Failure == nil {
					t.Fatalf("a failed task carries no failure: %s", pretty(report))
				}
				if report.Failure.FailedStage != "" {
					t.Errorf("failedStage %q, want it absent when no stage failed", report.Failure.FailedStage)
				}
				if len(report.Failure.Errors) != 1 || report.Failure.Errors[0].ID != "com.broadcom.lcm.task.failed" {
					t.Errorf("task-level errors are %v, want the task's ERROR message", report.Failure.Errors)
				}
			},
		},
		{
			// CANCELED is terminal and is a failed outcome; it must not be polled
			// forever or retried.
			name: "canceled task is terminal",
			plan: singleComponentPlan(),
			task: mocklcm.TaskScript{
				Accepted: mocklcm.StatusPending,
				Poll:     []string{mocklcm.StatusScheduled, mocklcm.StatusCanceled},
			},
			tokens:      []string{"tok-alpha"},
			tokenUses:   0,
			wantOutcome: "failed",
			wantOperations: []string{
				"getComponents", "resolveDepotComponents", "createComponents", "getTask", "getTask",
			},
			check: func(t *testing.T, m *mocklcm.Mock, report reportView) {
				if report.Task == nil || report.Task.Status != mocklcm.StatusCanceled || report.Task.Retried {
					t.Errorf("canceled task report is %s", pretty(report.Task))
				}
			},
		},
		{
			// A credential that holds for the whole run is never replaced.
			name:        "a credential that holds is not refreshed",
			plan:        singleComponentPlan(),
			task:        mocklcm.DefaultTaskScript(),
			tokens:      []string{"tok-alpha", "tok-beta"},
			tokenUses:   0,
			wantOutcome: "succeeded",
			wantOperations: []string{
				"getComponents", "resolveDepotComponents", "createComponents",
				"getTask", "getTask", "getTask", "getTask",
			},
			wantRefreshes: 0,
		},
		{
			// Nothing to install means nothing is sent beyond the inventory
			// lookup. A task raised here would be work invented.
			name: "nothing to install",
			plan: map[string]any{
				"scope": "FLEET",
				"depot": depotSpec(),
				"components": []any{
					map[string]any{
						"componentType": "VCF_OPERATIONS_FLEET_MANAGEMENT",
						"fqdn":          "fleet-mgmt.vcf.example.com",
						"password":      "VMw@re123!Fleet",
					},
				},
			},
			task:           mocklcm.DefaultTaskScript(),
			tokens:         []string{"tok-alpha"},
			tokenUses:      0,
			wantOutcome:    "succeeded",
			wantOperations: []string{"getComponents"},
			check: func(t *testing.T, m *mocklcm.Mock, report reportView) {
				if !reflect.DeepEqual(report.Skipped, []string{"VCF_OPERATIONS_FLEET_MANAGEMENT"}) {
					t.Errorf("skipped %v, want [VCF_OPERATIONS_FLEET_MANAGEMENT]", report.Skipped)
				}
				if len(report.Installed) != 0 {
					t.Errorf("installed %v, want nothing", report.Installed)
				}
				if report.Task != nil {
					t.Errorf("a task was reported for a run with nothing to do: %s", pretty(report.Task))
				}
			},
		},
		{
			// A component the depot does not carry makes the run impossible, and
			// the install must not be raised anyway.
			name: "the depot does not carry a planned component",
			plan: map[string]any{
				"depot": depotSpec(),
				"components": []any{
					map[string]any{
						"componentType": "VCF_NOT_IN_DEPOT",
						"fqdn":          "nope.vcf.example.com",
						"password":      "VMw@re123!",
					},
				},
			},
			task:           mocklcm.DefaultTaskScript(),
			tokens:         []string{"tok-alpha"},
			tokenUses:      0,
			wantErr:        true,
			wantOperations: []string{"getComponents", "resolveDepotComponents"},
		},
		{
			// The credential goes stale and cannot be replaced. The run stops
			// there; it does not push on with a credential it knows is dead.
			name:           "the credential cannot be refreshed",
			plan:           singleComponentPlan(),
			task:           mocklcm.DefaultTaskScript(),
			tokens:         []string{"tok-alpha", "tok-beta"},
			tokenUses:      1,
			refuseRefresh:  true,
			wantErr:        true,
			wantOperations: []string{"getComponents", "resolveDepotComponents"},
			wantRefreshes:  1,
		},
		{
			// The replacement is rejected too. The run gives up rather than
			// refreshing round and round.
			name:           "the replacement credential is rejected as well",
			plan:           singleComponentPlan(),
			task:           mocklcm.DefaultTaskScript(),
			tokens:         []string{"tok-alpha"},
			tokenUses:      1,
			credTokens:     []string{"tok-alpha", "tok-never-issued"},
			wantErr:        true,
			wantOperations: []string{"getComponents", "resolveDepotComponents", "resolveDepotComponents"},
			wantRefreshes:  1,
		},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			m := startMock(t, mocklcm.Config{
				Tokens:    tc.tokens,
				TokenUses: tc.tokenUses,
				Task:      tc.task,
			})
			credTokens := tc.credTokens
			if credTokens == nil {
				credTokens = tc.tokens
			}
			creds := newCredentials(credTokens...)
			creds.refuse = tc.refuseRefresh

			report, err := runPlan(t, m, writePlan(t, tc.plan), creds)
			if tc.wantErr {
				if err == nil {
					t.Fatalf("want an error, got report %s", pretty(report))
				}
			} else if err != nil {
				t.Fatalf("run: %v", err)
			}

			if got := operations(m); !equalStrings(got, tc.wantOperations) {
				t.Errorf("operations\n  got  %v\n  want %v", got, tc.wantOperations)
			}
			// However the run ended, the install may never be raised twice.
			if n := len(requestsFor(m, "createComponents")); n > 1 {
				var accepted int
				for _, r := range requestsFor(m, "createComponents") {
					if r.Status == http.StatusAccepted {
						accepted++
					}
				}
				if accepted > 1 {
					t.Errorf("the install was raised %d times; accepted work must not be repeated", accepted)
				}
			}
			if got := creds.refreshCount(); got != tc.wantRefreshes {
				t.Errorf("the credential was refreshed %d time(s), want %d", got, tc.wantRefreshes)
			}
			// A refresh happens because the service rejected a credential, not
			// on a schedule, so there can never be more refreshes than
			// rejections. There may be fewer: the run gives up rather than
			// refreshing round and round.
			if got, rejections := creds.refreshCount(), count401(m); got > rejections {
				t.Errorf("the credential was refreshed %d time(s) but the service rejected only %d request(s); "+
					"refresh only in answer to a rejection", got, rejections)
			}

			if err == nil {
				view := viewReport(t, report)
				if view.Outcome != tc.wantOutcome {
					t.Errorf("outcome %q, want %q", view.Outcome, tc.wantOutcome)
				}
				if view.CredentialRefreshes != tc.wantRefreshes {
					t.Errorf("report says %d refresh(es), want %d", view.CredentialRefreshes, tc.wantRefreshes)
				}
				if tc.check != nil {
					tc.check(t, m, view)
				}
			}
			requireNoViolations(t, m)
		})
	}
}

// TestPlanValidation checks every required part of the documented plan and the
// distinct-component invariant before any service work is attempted.
func TestPlanValidation(t *testing.T) {
	t.Parallel()
	validComponent := func() map[string]any {
		return map[string]any{
			"componentType": "VCF_OPERATIONS",
			"fqdn":          "ops-a.vcf.example.com",
			"password":      "VMw@re123!Ops",
		}
	}
	cases := []struct {
		name string
		plan map[string]any
	}{
		{
			name: "depot fqdn is required",
			plan: map[string]any{
				"depot":      map[string]any{"certificate": "certificate"},
				"components": []any{validComponent()},
			},
		},
		{
			name: "depot certificate is required",
			plan: map[string]any{
				"depot":      map[string]any{"fqdn": "depot.vcf.example.com"},
				"components": []any{validComponent()},
			},
		},
		{name: "components must not be empty", plan: map[string]any{"depot": depotSpec(), "components": []any{}}},
		{
			name: "component type is required",
			plan: map[string]any{
				"depot": depotSpec(),
				"components": []any{map[string]any{
					"fqdn": "ops-a.vcf.example.com", "password": "VMw@re123!Ops",
				}},
			},
		},
		{
			name: "component fqdn is required",
			plan: map[string]any{
				"depot": depotSpec(),
				"components": []any{map[string]any{
					"componentType": "VCF_OPERATIONS", "password": "VMw@re123!Ops",
				}},
			},
		},
		{
			name: "component password is required",
			plan: map[string]any{
				"depot": depotSpec(),
				"components": []any{map[string]any{
					"componentType": "VCF_OPERATIONS", "fqdn": "ops-a.vcf.example.com",
				}},
			},
		},
		{
			name: "component types must be distinct",
			plan: map[string]any{
				"depot":      depotSpec(),
				"components": []any{validComponent(), validComponent()},
			},
		},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			called := false
			httpClient := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
				called = true
				return nil, context.Canceled
			})}
			report, err := fleetrun.Run(context.Background(), fleetrun.Options{
				PlanPath:     writePlan(t, tc.plan),
				ContractPath: contractPath,
				BaseURL:      "http://127.0.0.1",
				Credentials:  fleetrun.StaticCredential("tok-alpha"),
				HTTPClient:   httpClient,
			})
			if err == nil {
				t.Errorf("invalid plan returned report %s, want an error", pretty(report))
			}
			if called {
				t.Errorf("invalid plan attempted a service request, want none")
			}
		})
	}
}

func TestStaticCredential(t *testing.T) {
	t.Parallel()
	credential := fleetrun.StaticCredential("tok-fixed")
	got, err := credential.Token(context.Background())
	if err != nil || got != "tok-fixed" {
		t.Errorf("StaticCredential.Token() = %q, %v; want tok-fixed, nil", got, err)
	}
	if replacement, err := credential.Refresh(context.Background(), "tok-fixed"); err == nil {
		t.Errorf("StaticCredential.Refresh() = %q, nil; want an error because a fixed token cannot be renewed", replacement)
	}
}
