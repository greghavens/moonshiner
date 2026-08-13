package contracttest

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"example.com/vcf/restoredrill/internal/mocklcm"
)

var (
	buildOnce sync.Once
	binPath   string
	buildErr  error
)

// binary builds cmd/vcf-restore-drill once and returns the path to it.
func binary(t *testing.T) string {
	t.Helper()
	buildOnce.Do(func() {
		dir, err := os.MkdirTemp("", "vcf-restore-drill")
		if err != nil {
			buildErr = err
			return
		}
		binPath = filepath.Join(dir, "vcf-restore-drill")
		cmd := exec.Command("go", "build", "-buildvcs=false", "-o", binPath, "./cmd/vcf-restore-drill")
		cmd.Dir = ".."
		var stderr bytes.Buffer
		cmd.Stderr = &stderr
		if err := cmd.Run(); err != nil {
			buildErr = err
			binPath = strings.TrimSpace(stderr.String())
		}
	})
	if buildErr != nil {
		t.Fatalf("cmd/vcf-restore-drill does not build: %v\n%s", buildErr, binPath)
	}
	return binPath
}

// TestCLI checks the exit code the command reports for each outcome, that it
// writes the report where it was told to, and that it says something about
// every planned component on stdout.
func TestCLI(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name        string
		plan        map[string]any
		mock        mocklcm.Options
		contract    string
		wantExit    int
		wantOutcome string
		wantStdout  []string
	}{
		{
			name: "a drill that succeeds exits 0",
			plan: map[string]any{
				"scope": "FLEET",
				"components": []any{
					map[string]any{"componentType": "vidb"},
					map[string]any{"componentType": "opscp"},
				},
			},
			mock:        mocklcm.Options{PollsBeforeTerminal: 2},
			wantExit:    0,
			wantOutcome: "succeeded",
			wantStdout:  []string{"vidb", "opscp", "succeeded"},
		},
		{
			name: "a drill that fails exits 1",
			plan: map[string]any{
				"scope": "FLEET",
				"components": []any{
					map[string]any{"componentType": "vidb"},
					map[string]any{"componentType": "opscp"},
				},
			},
			mock: mocklcm.Options{
				PollsBeforeTerminal: 2,
				Restores: map[string]mocklcm.TaskOutcome{
					vidbID: {
						Status:      "FAILED",
						FailedStage: "restore-precheck",
						Errors: []mocklcm.Message{
							{ID: "com.broadcom.lcm.restore.precheck.failed", DefaultMessage: "Target is not quiesced"},
						},
					},
				},
			},
			wantExit:    1,
			wantOutcome: "failed",
			wantStdout:  []string{"vidb", "opscp", "failed"},
		},
		{
			name: "a run that cannot be carried out exits 2",
			plan: map[string]any{
				"scope":      "FLEET",
				"components": []any{map[string]any{"componentType": "vcfa"}},
			},
			mock:     mocklcm.Options{PollsBeforeTerminal: 2},
			wantExit: 2,
		},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			bin := binary(t)
			server := startMock(t, tc.mock)
			planPath := writePlan(t, tc.plan)
			reportPath := filepath.Join(t.TempDir(), "report.json")

			cmd := exec.Command(bin,
				"-plan", planPath,
				"-base-url", server.URL,
				"-token", token,
				"-report", reportPath,
				"-poll-interval", "2ms",
				"-poll-timeout", "30s",
			)
			// Run from the repository root so the default -contract path
			// resolves the way an operator would see it.
			cmd.Dir = ".."
			var stdout, stderr bytes.Buffer
			cmd.Stdout = &stdout
			cmd.Stderr = &stderr
			err := cmd.Run()

			exit := 0
			if err != nil {
				exitErr, ok := err.(*exec.ExitError)
				if !ok {
					t.Fatalf("running the command failed: %v", err)
				}
				exit = exitErr.ExitCode()
			}
			if exit != tc.wantExit {
				t.Fatalf("exit code is %d, want %d\nstdout:\n%s\nstderr:\n%s",
					exit, tc.wantExit, stdout.String(), stderr.String())
			}
			requireNoViolations(t, server)

			if tc.wantOutcome == "" {
				return
			}
			raw, err := os.ReadFile(reportPath)
			if err != nil {
				t.Fatalf("the command wrote no report: %v", err)
			}
			var report struct {
				Outcome string `json:"outcome"`
			}
			if err := json.Unmarshal(raw, &report); err != nil {
				t.Fatalf("the report is not JSON: %v", err)
			}
			if report.Outcome != tc.wantOutcome {
				t.Errorf("report outcome is %q, want %q", report.Outcome, tc.wantOutcome)
			}
			for _, want := range tc.wantStdout {
				if !strings.Contains(stdout.String(), want) {
					t.Errorf("the summary on stdout does not mention %q:\n%s", want, stdout.String())
				}
			}
		})
	}
}
