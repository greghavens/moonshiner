package contracttest

import (
	"encoding/json"
	"fmt"
	"reflect"
	"sort"
	"testing"

	"example.com/vcf/fleetlcm/fleetrun"
	"example.com/vcf/fleetlcm/internal/mocklcm"
)

// reportView is the report as it marshals to JSON, so that a field which must
// be absent is seen to be absent rather than merely zero.
type reportView struct {
	Outcome             string          `json:"outcome"`
	Scope               string          `json:"scope"`
	Skipped             []string        `json:"skipped"`
	Installed           []installedView `json:"installed"`
	Task                *taskView       `json:"task"`
	CredentialRefreshes int             `json:"credentialRefreshes"`
	Failure             *failureView    `json:"failure"`

	// raw is the same document as a map, for checking which keys exist.
	raw map[string]any
}

type installedView struct {
	ComponentType string `json:"componentType"`
	FQDN          string `json:"fqdn"`
	Version       string `json:"version"`
	DownloadURL   string `json:"downloadUrl"`
}

type taskView struct {
	ID      string `json:"id"`
	Status  string `json:"status"`
	Retried bool   `json:"retried"`
}

type failureView struct {
	TaskID      string `json:"taskId"`
	FailedStage string `json:"failedStage"`
	Errors      []struct {
		ID             string `json:"id"`
		DefaultMessage string `json:"defaultMessage"`
	} `json:"errors"`
}

// viewReport marshals the report and reads it back, which is how an operator
// and the command line tool see it.
func viewReport(t *testing.T, report *fleetrun.Report) reportView {
	t.Helper()
	raw, err := json.Marshal(report)
	if err != nil {
		t.Fatalf("the report does not marshal to JSON: %v", err)
	}
	var view reportView
	if err := json.Unmarshal(raw, &view); err != nil {
		t.Fatalf("the report does not read back: %v", err)
	}
	if err := json.Unmarshal(raw, &view.raw); err != nil {
		t.Fatalf("the report is not a JSON object: %v", err)
	}
	return view
}

func (r reportView) has(key string) bool {
	_, ok := r.raw[key]
	return ok
}

// nested reaches into the raw document.
func nested(t *testing.T, v any, path ...string) map[string]any {
	t.Helper()
	cur, ok := v.(map[string]any)
	if !ok {
		t.Fatalf("%v is not a JSON object", v)
	}
	for _, key := range path {
		next, ok := cur[key]
		if !ok {
			t.Fatalf("no %q under %v", key, cur)
		}
		cur, ok = next.(map[string]any)
		if !ok {
			t.Fatalf("%q is not a JSON object", key)
		}
	}
	return cur
}

// TestReportShape checks the report says what happened, and leaves out what did
// not.
func TestReportShape(t *testing.T) {
	t.Parallel()

	t.Run("every optional input set", func(t *testing.T) {
		t.Parallel()
		m := startMock(t, mocklcm.Config{
			Tokens:    []string{"tok-alpha", "tok-beta", "tok-gamma"},
			TokenUses: 3,
			Task:      mocklcm.DefaultTaskScript(),
		})
		report, err := runPlan(t, m, planPath, newCredentials("tok-alpha", "tok-beta", "tok-gamma"))
		if err != nil {
			t.Fatalf("run: %v", err)
		}
		view := viewReport(t, report)
		checkKeys(t, "successful report", view.raw,
			"outcome", "scope", "skipped", "installed", "task", "credentialRefreshes")

		if view.Outcome != "succeeded" {
			t.Errorf("outcome %q, want succeeded", view.Outcome)
		}
		if view.Scope != "FLEET" {
			t.Errorf("scope %q, want FLEET", view.Scope)
		}
		if view.CredentialRefreshes != 2 {
			t.Errorf("credentialRefreshes %d, want 2", view.CredentialRefreshes)
		}
		if view.has("failure") {
			t.Errorf("a run that succeeded reports a failure: %s", pretty(view.Failure))
		}
		if !reflect.DeepEqual(view.Skipped, []string{"VCF_OPERATIONS_FLEET_MANAGEMENT"}) {
			t.Errorf("skipped %v, want [VCF_OPERATIONS_FLEET_MANAGEMENT]", view.Skipped)
		}

		wantInstalled := []installedView{
			{
				ComponentType: "VCF_OPERATIONS",
				FQDN:          "ops-a.vcf.example.com",
				Version:       "9.1.0.0",
				DownloadURL:   opsBinaryURL,
			},
			{
				ComponentType: "VCF_AUTOMATION",
				FQDN:          "auto-a.vcf.example.com",
				Version:       "9.1.0.0",
			},
		}
		if !reflect.DeepEqual(view.Installed, wantInstalled) {
			t.Errorf("installed\n%s\nwant\n%s", pretty(view.Installed), pretty(wantInstalled))
		}
		// The depot published no binary for the second component, so the
		// report carries no downloadUrl for it either.
		rawInstalled, _ := view.raw["installed"].([]any)
		if len(rawInstalled) != 2 {
			t.Fatalf("installed has %d entries, want 2", len(rawInstalled))
		}
		second := nested(t, rawInstalled[1])
		checkKeys(t, "installed[0]", rawInstalled[0], "componentType", "fqdn", "version", "downloadUrl")
		checkKeys(t, "installed[1]", rawInstalled[1], "componentType", "fqdn", "version")
		if _, ok := second["downloadUrl"]; ok {
			t.Errorf("a component the depot published no binary for reports downloadUrl %v", second["downloadUrl"])
		}
		if view.Task == nil || view.Task.ID != taskID || view.Task.Status != "SUCCEEDED" || view.Task.Retried {
			t.Errorf("task %s, want id %s status SUCCEEDED and no retry", pretty(view.Task), taskID)
		}
		checkKeys(t, "task", view.raw["task"], "id", "status", "retried")
	})

	t.Run("no optional input set", func(t *testing.T) {
		t.Parallel()
		m := startMock(t, mocklcm.Config{
			Tokens: []string{"tok-alpha"},
			Task:   mocklcm.DefaultTaskScript(),
		})
		plan := map[string]any{
			"depot": depotSpec(),
			"components": []any{
				map[string]any{
					"componentType": "VCF_AUTOMATION",
					"fqdn":          "auto-a.vcf.example.com",
					"password":      "VMw@re123!Auto",
				},
			},
		}
		report, err := runPlan(t, m, writePlan(t, plan), newCredentials("tok-alpha"))
		if err != nil {
			t.Fatalf("run: %v", err)
		}
		view := viewReport(t, report)
		if view.has("scope") {
			t.Errorf("a plan with no scope reports scope %v", view.raw["scope"])
		}
		if view.has("failure") {
			t.Errorf("a run that succeeded reports a failure")
		}
		if view.CredentialRefreshes != 0 {
			t.Errorf("credentialRefreshes %d, want 0", view.CredentialRefreshes)
		}
		// skipped and installed are always present, so a reader can tell an
		// empty list from a missing one.
		for _, key := range []string{"outcome", "skipped", "installed", "credentialRefreshes"} {
			if !view.has(key) {
				t.Errorf("the report has no %q", key)
			}
		}
		if len(view.Skipped) != 0 {
			t.Errorf("skipped %v, want empty", view.Skipped)
		}
	})
}

// TestReportKeys checks the report carries no keys beyond the ones described.
func TestReportKeys(t *testing.T) {
	t.Parallel()
	m := startMock(t, mocklcm.Config{
		Tokens: []string{"tok-alpha"},
		Task:   mocklcm.TerminalFailureScript(),
	})
	report, err := runPlan(t, m, writePlan(t, singleComponentPlan()), newCredentials("tok-alpha"))
	if err != nil {
		t.Fatalf("run: %v", err)
	}
	view := viewReport(t, report)
	checkKeys(t, "failed report", view.raw,
		"outcome", "scope", "skipped", "installed", "task", "credentialRefreshes", "failure")

	allowed := map[string]bool{
		"outcome": true, "scope": true, "skipped": true, "installed": true,
		"task": true, "credentialRefreshes": true, "failure": true,
	}
	var extra []string
	for key := range view.raw {
		if !allowed[key] {
			extra = append(extra, key)
		}
	}
	sort.Strings(extra)
	if len(extra) > 0 {
		t.Errorf("the report carries unexpected keys %v", extra)
	}
	if view.Outcome != "failed" {
		t.Errorf("outcome %q, want failed", view.Outcome)
	}
	if view.Failure == nil {
		t.Fatalf("a failed run carries no failure")
	}
	if view.Task == nil || view.Task.Status != "FAILED" {
		t.Errorf("task %s, want status FAILED", pretty(view.Task))
	}
	checkKeys(t, "task", view.raw["task"], "id", "status", "retried")
	failure := checkKeys(t, "failure", view.raw["failure"], "taskId", "failedStage", "errors")
	errors, ok := failure["errors"].([]any)
	if !ok || len(errors) == 0 {
		t.Fatalf("failure.errors is %v, want a non-empty array", failure["errors"])
	}
	for i, item := range errors {
		checkKeys(t, "failure.errors["+fmt.Sprint(i)+"]", item, "id", "defaultMessage")
	}
}
