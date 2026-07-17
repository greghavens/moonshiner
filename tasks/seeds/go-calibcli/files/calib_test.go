package calibcli

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

// runCalib executes a fresh command tree in-process, the way the multitool
// will host it: new tree per invocation, state lives only in the --db file.
func runCalib(t *testing.T, args ...string) (stdout, stderr string, err error) {
	t.Helper()
	var out, errb bytes.Buffer
	var cmd *cobra.Command = NewRootCmd()
	cmd.SetOut(&out)
	cmd.SetErr(&errb)
	cmd.SetArgs(args)
	err = cmd.Execute()
	return out.String(), errb.String(), err
}

func dbPath(t *testing.T) string {
	t.Helper()
	return filepath.Join(t.TempDir(), "calib.json")
}

func TestAddAndList(t *testing.T) {
	db := dbPath(t)

	out, errOut, err := runCalib(t, "add", "PMP-104", "--db", db, "--name", "pressure gauge", "--interval-days", "90")
	if err != nil {
		t.Fatalf("add: %v", err)
	}
	if out != "registered PMP-104 (every 90d)\n" {
		t.Fatalf("add output = %q", out)
	}
	if errOut != "" {
		t.Fatalf("add wrote to stderr: %q", errOut)
	}

	if _, _, err := runCalib(t, "add", "HYG-002", "--db", db, "--name", "humidity probe", "--interval-days", "30"); err != nil {
		t.Fatalf("second add: %v", err)
	}

	out, _, err = runCalib(t, "list", "--db", db)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	want := "HYG-002  humidity probe  every 30d  last never\n" +
		"PMP-104  pressure gauge  every 90d  last never\n"
	if out != want {
		t.Fatalf("list output:\n%q\nwant:\n%q", out, want)
	}
}

func TestListEmpty(t *testing.T) {
	out, _, err := runCalib(t, "list", "--db", dbPath(t))
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	if out != "no instruments\n" {
		t.Fatalf("list output = %q, want %q", out, "no instruments\n")
	}
}

func TestAddValidation(t *testing.T) {
	db := dbPath(t)
	if _, _, err := runCalib(t, "add", "PMP-104", "--db", db, "--name", "pressure gauge", "--interval-days", "90"); err != nil {
		t.Fatalf("seed add: %v", err)
	}

	cases := []struct {
		name    string
		args    []string
		wantErr string // substring
	}{
		{
			name:    "no ID argument",
			args:    []string{"add", "--db", db, "--name", "x", "--interval-days", "30"},
			wantErr: "accepts 1 arg(s), received 0",
		},
		{
			name:    "missing name flag",
			args:    []string{"add", "SCL-001", "--db", db, "--interval-days", "30"},
			wantErr: `required flag(s) "name" not set`,
		},
		{
			name:    "zero interval",
			args:    []string{"add", "SCL-001", "--db", db, "--name", "bench scale", "--interval-days", "0"},
			wantErr: "interval-days must be positive",
		},
		{
			name:    "duplicate id",
			args:    []string{"add", "PMP-104", "--db", db, "--name", "again", "--interval-days", "30"},
			wantErr: `instrument "PMP-104" already registered`,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, _, err := runCalib(t, tc.args...)
			if err == nil {
				t.Fatal("command succeeded, want error")
			}
			if !strings.Contains(err.Error(), tc.wantErr) {
				t.Fatalf("error = %q, want substring %q", err.Error(), tc.wantErr)
			}
		})
	}
}

func TestLogCalibration(t *testing.T) {
	db := dbPath(t)
	if _, _, err := runCalib(t, "add", "PMP-104", "--db", db, "--name", "pressure gauge", "--interval-days", "90"); err != nil {
		t.Fatalf("seed add: %v", err)
	}

	out, _, err := runCalib(t, "log", "PMP-104", "--db", db, "--date", "2026-01-15")
	if err != nil {
		t.Fatalf("log: %v", err)
	}
	if out != "logged PMP-104 at 2026-01-15\n" {
		t.Fatalf("log output = %q", out)
	}

	out, _, err = runCalib(t, "list", "--db", db)
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	if out != "PMP-104  pressure gauge  every 90d  last 2026-01-15\n" {
		t.Fatalf("list after log = %q", out)
	}

	t.Run("unknown instrument", func(t *testing.T) {
		_, _, err := runCalib(t, "log", "PMP-999", "--db", db, "--date", "2026-01-15")
		if err == nil || !strings.Contains(err.Error(), `unknown instrument "PMP-999"`) {
			t.Fatalf("error = %v, want unknown instrument", err)
		}
	})
	t.Run("bad date", func(t *testing.T) {
		_, _, err := runCalib(t, "log", "PMP-104", "--db", db, "--date", "01/15/2026")
		if err == nil || !strings.Contains(err.Error(), `invalid date "01/15/2026": expected YYYY-MM-DD`) {
			t.Fatalf("error = %v, want invalid date", err)
		}
	})
}

func TestDueReport(t *testing.T) {
	db := dbPath(t)
	steps := [][]string{
		{"add", "HYG-002", "--db", db, "--name", "humidity probe", "--interval-days", "30"},
		{"add", "PMP-104", "--db", db, "--name", "pressure gauge", "--interval-days", "90"},
		{"add", "SCL-001", "--db", db, "--name", "bench scale", "--interval-days", "30"},
		{"add", "THM-220", "--db", db, "--name", "oven thermometer", "--interval-days", "365"},
		{"log", "PMP-104", "--db", db, "--date", "2026-01-15"},
		{"log", "SCL-001", "--db", db, "--date", "2026-03-16"},
		{"log", "THM-220", "--db", db, "--date", "2026-01-01"},
	}
	for _, s := range steps {
		if _, _, err := runCalib(t, s...); err != nil {
			t.Fatalf("step %v: %v", s, err)
		}
	}

	t.Run("boundary day counts as due with 0d overdue", func(t *testing.T) {
		out, _, err := runCalib(t, "due", "--db", db, "--as-of", "2026-04-15")
		if err != nil {
			t.Fatalf("due: %v", err)
		}
		want := "HYG-002 never calibrated\n" +
			"PMP-104 due 2026-04-15 (0d overdue)\n" +
			"SCL-001 due 2026-04-15 (0d overdue)\n"
		if out != want {
			t.Fatalf("due output:\n%q\nwant:\n%q", out, want)
		}
	})

	t.Run("overdue days counted from due date", func(t *testing.T) {
		out, _, err := runCalib(t, "due", "--db", db, "--as-of", "2026-05-30")
		if err != nil {
			t.Fatalf("due: %v", err)
		}
		want := "HYG-002 never calibrated\n" +
			"PMP-104 due 2026-04-15 (45d overdue)\n" +
			"SCL-001 due 2026-04-15 (45d overdue)\n"
		if out != want {
			t.Fatalf("due output:\n%q\nwant:\n%q", out, want)
		}
	})

	t.Run("nothing due", func(t *testing.T) {
		fresh := dbPath(t)
		if _, _, err := runCalib(t, "add", "THM-220", "--db", fresh, "--name", "oven thermometer", "--interval-days", "365"); err != nil {
			t.Fatal(err)
		}
		if _, _, err := runCalib(t, "log", "THM-220", "--db", fresh, "--date", "2026-01-01"); err != nil {
			t.Fatal(err)
		}
		out, _, err := runCalib(t, "due", "--db", fresh, "--as-of", "2026-06-01")
		if err != nil {
			t.Fatalf("due: %v", err)
		}
		if out != "nothing due\n" {
			t.Fatalf("due output = %q, want %q", out, "nothing due\n")
		}
	})

	t.Run("bad as-of date", func(t *testing.T) {
		_, _, err := runCalib(t, "due", "--db", db, "--as-of", "soon")
		if err == nil || !strings.Contains(err.Error(), `invalid date "soon": expected YYYY-MM-DD`) {
			t.Fatalf("error = %v, want invalid date", err)
		}
	})
}

func TestRootErrors(t *testing.T) {
	t.Run("db flag is required", func(t *testing.T) {
		_, _, err := runCalib(t, "list")
		if err == nil || !strings.Contains(err.Error(), `required flag(s) "db" not set`) {
			t.Fatalf("error = %v, want required --db", err)
		}
	})
	t.Run("unknown subcommand", func(t *testing.T) {
		_, _, err := runCalib(t, "frob", "--db", dbPath(t))
		if err == nil || !strings.Contains(err.Error(), `unknown command "frob"`) {
			t.Fatalf("error = %v, want unknown command", err)
		}
	})
}

func TestHelpListsSubcommands(t *testing.T) {
	out, _, err := runCalib(t, "--help")
	if err != nil {
		t.Fatalf("--help: %v", err)
	}
	for _, sub := range []string{"add", "log", "due", "list"} {
		if !strings.Contains(out, sub) {
			t.Errorf("help output missing subcommand %q:\n%s", sub, out)
		}
	}
}
