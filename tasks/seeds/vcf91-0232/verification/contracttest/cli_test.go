package contracttest

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"example.com/vcf/fleetlcm/internal/mocklcm"
)

// buildCLI builds the command line tool once for the tests that drive it.
func buildCLI(t *testing.T) string {
	t.Helper()
	bin := filepath.Join(t.TempDir(), "vcf-fleet-run")
	cmd := exec.Command("go", "build", "-buildvcs=false", "-o", bin, "./cmd/vcf-fleet-run")
	cmd.Dir = ".."
	cmd.Env = append(os.Environ(), "GOFLAGS=-mod=mod", "GOPROXY=off", "GOTOOLCHAIN=local", "GOWORK=off")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("build vcf-fleet-run: %v\n%s", err, out)
	}
	return bin
}

// runCLI runs the tool from the repository root and returns its exit code and
// output.
func runCLI(t *testing.T, bin string, args ...string) (int, string, string) {
	t.Helper()
	cmd := exec.Command(bin, args...)
	cmd.Dir = ".."
	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	code := 0
	if err != nil {
		exit, ok := err.(*exec.ExitError)
		if !ok {
			t.Fatalf("run vcf-fleet-run: %v", err)
		}
		code = exit.ExitCode()
	}
	return code, stdout.String(), stderr.String()
}

// writeTokenFile writes the access tokens an operator provisioned for a run.
func writeTokenFile(t *testing.T, tokens ...string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "tokens")
	if err := os.WriteFile(path, []byte(strings.Join(tokens, "\n")+"\n"), 0o600); err != nil {
		t.Fatalf("write token file: %v", err)
	}
	return path
}

// TestCLI drives the command line tool end to end against the mock.
func TestCLI(t *testing.T) {
	t.Parallel()
	bin := buildCLI(t)

	cases := []struct {
		name      string
		task      mocklcm.TaskScript
		tokens    []string
		tokenUses int
		// cliTokens are the tokens handed to the tool.
		cliTokens []string
		wantCode  int
		// wantOutcome is checked when a report was written.
		wantOutcome   string
		wantRefreshes int
		wantStdout    []string
	}{
		{
			// The credential expires twice during the run and the tool carries
			// on without losing what the service already accepted.
			name:          "install succeeds across two credential refreshes",
			task:          mocklcm.DefaultTaskScript(),
			tokens:        []string{"tok-alpha", "tok-beta", "tok-gamma"},
			tokenUses:     3,
			cliTokens:     []string{"tok-alpha", "tok-beta", "tok-gamma"},
			wantCode:      0,
			wantOutcome:   "succeeded",
			wantRefreshes: 2,
			wantStdout: []string{
				"succeeded",
				"VCF_OPERATIONS",
				"VCF_AUTOMATION",
				"VCF_OPERATIONS_FLEET_MANAGEMENT",
				"refreshed 2 time(s)",
			},
		},
		{
			// An install that fails is reported, and is not an error in the
			// tool: exit 1, with a report.
			name:        "a failed install exits 1",
			task:        mocklcm.TerminalFailureScript(),
			tokens:      []string{"tok-alpha"},
			tokenUses:   0,
			cliTokens:   []string{"tok-alpha"},
			wantCode:    1,
			wantOutcome: "failed",
			wantStdout:  []string{"failed", "package-deploy"},
		},
		{
			// The credential runs out and cannot be replaced, so the run could
			// not be carried out at all: exit 2, no report.
			name:      "an unrenewable credential exits 2",
			task:      mocklcm.DefaultTaskScript(),
			tokens:    []string{"tok-alpha", "tok-beta"},
			tokenUses: 1,
			cliTokens: []string{"tok-alpha"},
			wantCode:  2,
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
			reportPath := filepath.Join(t.TempDir(), "report.json")
			code, stdout, stderr := runCLI(t, bin,
				"-plan", "fixtures/install-plan.json",
				"-base-url", m.URL(),
				"-token-file", writeTokenFile(t, tc.cliTokens...),
				"-report", reportPath,
				"-poll-interval", "1ms",
				"-poll-timeout", "20s",
			)
			if code != tc.wantCode {
				t.Fatalf("exit %d, want %d\nstdout:\n%s\nstderr:\n%s", code, tc.wantCode, stdout, stderr)
			}
			for _, want := range tc.wantStdout {
				if !strings.Contains(stdout, want) {
					t.Errorf("stdout does not mention %q:\n%s", want, stdout)
				}
			}
			if tc.wantOutcome == "" {
				return
			}
			raw, err := os.ReadFile(reportPath)
			if err != nil {
				t.Fatalf("read report: %v", err)
			}
			var report struct {
				Outcome             string `json:"outcome"`
				CredentialRefreshes int    `json:"credentialRefreshes"`
			}
			if err := json.Unmarshal(raw, &report); err != nil {
				t.Fatalf("parse report: %v\n%s", err, raw)
			}
			if report.Outcome != tc.wantOutcome {
				t.Errorf("report outcome %q, want %q", report.Outcome, tc.wantOutcome)
			}
			if report.CredentialRefreshes != tc.wantRefreshes {
				t.Errorf("report credentialRefreshes %d, want %d", report.CredentialRefreshes, tc.wantRefreshes)
			}
			requireNoViolations(t, m)
		})
	}
}

// TestCLIStaticCredentialAndDefaults covers the single-token form and leaves
// the contract and polling flags unset. A task that succeeds on its first poll
// keeps the documented 5s default from slowing the test down.
func TestCLIStaticCredentialAndDefaults(t *testing.T) {
	t.Parallel()
	bin := buildCLI(t)
	m := startMock(t, mocklcm.Config{
		Tokens: []string{"tok-alpha"},
		Task: mocklcm.TaskScript{
			Accepted: mocklcm.StatusPending,
			Poll:     []string{mocklcm.StatusSucceeded},
		},
	})
	reportPath := filepath.Join(t.TempDir(), "report.json")
	code, stdout, stderr := runCLI(t, bin,
		"-plan", "fixtures/install-plan.json",
		"-base-url", m.URL(),
		"-token", "tok-alpha",
		"-report", reportPath,
	)
	if code != 0 {
		t.Fatalf("exit %d, want 0\nstdout:\n%s\nstderr:\n%s", code, stdout, stderr)
	}
	for _, want := range []string{"succeeded", taskID, "refreshed 0 time(s)"} {
		if !strings.Contains(stdout, want) {
			t.Errorf("stdout does not mention %q:\n%s", want, stdout)
		}
	}
	if _, err := os.Stat(reportPath); err != nil {
		t.Errorf("report was not written: %v", err)
	}
	requireNoViolations(t, m)
}

// TestCLIUsage checks the tool refuses a run it cannot carry out.
func TestCLIUsage(t *testing.T) {
	t.Parallel()
	bin := buildCLI(t)
	cases := []struct {
		name string
		args []string
	}{
		{"no plan", []string{"-base-url", "http://127.0.0.1:1", "-token", "t", "-report", "r.json"}},
		{"no base url", []string{"-plan", "fixtures/install-plan.json", "-token", "t", "-report", "r.json"}},
		{"no report", []string{"-plan", "fixtures/install-plan.json", "-base-url", "http://127.0.0.1:1", "-token", "t"}},
		{"no credential", []string{"-plan", "fixtures/install-plan.json", "-base-url", "http://127.0.0.1:1", "-report", "r.json"}},
		{"both credential forms", []string{"-plan", "fixtures/install-plan.json", "-base-url", "http://127.0.0.1:1", "-report", "r.json", "-token", "t", "-token-file", "tokens"}},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if code, _, _ := runCLI(t, bin, tc.args...); code != 2 {
				t.Errorf("exit %d, want 2", code)
			}
		})
	}
}
