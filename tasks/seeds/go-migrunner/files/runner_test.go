package migrunner

import (
	"path/filepath"
	"testing"
)

func statePath(t *testing.T) string {
	t.Helper()
	return filepath.Join(t.TempDir(), "migrations.state")
}

// rec returns a step func that appends name to log and succeeds.
func rec(log *[]string, name string) func() error {
	return func() error {
		*log = append(*log, name)
		return nil
	}
}

func newRunner(t *testing.T, path string, migs []Migration) *Runner {
	t.Helper()
	r, err := NewRunner(path, migs)
	if err != nil {
		t.Fatalf("NewRunner: %v", err)
	}
	return r
}

func wantStrings(t *testing.T, what string, got, want []string) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("%s = %v, want %v", what, got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("%s = %v, want %v", what, got, want)
		}
	}
}

func threeMigs(log *[]string) []Migration {
	return []Migration{
		{ID: "0001_create_users", Up: rec(log, "up:0001"), Down: rec(log, "down:0001")},
		{ID: "0002_add_email", Up: rec(log, "up:0002"), Down: rec(log, "down:0002")},
		{ID: "0003_backfill", Up: rec(log, "up:0003"), Down: rec(log, "down:0003")},
	}
}

var threeIDs = []string{"0001_create_users", "0002_add_email", "0003_backfill"}

func TestFreshRunnerPlansEverything(t *testing.T) {
	var log []string
	r := newRunner(t, statePath(t), threeMigs(&log))

	wantStrings(t, "Plan()", r.Plan(), threeIDs)
	wantStrings(t, "Applied()", r.Applied(), nil)
	if len(log) != 0 {
		t.Fatalf("Plan must be a dry run, but migration funcs ran: %v", log)
	}
}

func TestUpAppliesInOrderAndIsIdempotent(t *testing.T) {
	var log []string
	r := newRunner(t, statePath(t), threeMigs(&log))

	applied, err := r.Up()
	if err != nil {
		t.Fatalf("Up: %v", err)
	}
	wantStrings(t, "Up() applied", applied, threeIDs)
	wantStrings(t, "execution log", log, []string{"up:0001", "up:0002", "up:0003"})
	wantStrings(t, "Plan() after Up", r.Plan(), nil)
	wantStrings(t, "Applied() after Up", r.Applied(), threeIDs)

	applied, err = r.Up()
	if err != nil {
		t.Fatalf("second Up: %v", err)
	}
	if len(applied) != 0 {
		t.Fatalf("second Up applied %v, want nothing", applied)
	}
	wantStrings(t, "execution log after second Up", log, []string{"up:0001", "up:0002", "up:0003"})
}

func TestAppliedStateSurvivesNewRunner(t *testing.T) {
	path := statePath(t)
	var log []string
	r1 := newRunner(t, path, threeMigs(&log))
	if _, err := r1.Up(); err != nil {
		t.Fatalf("Up: %v", err)
	}

	// A brand-new runner over the same state file must see the work as done.
	var log2 []string
	r2 := newRunner(t, path, threeMigs(&log2))
	wantStrings(t, "Plan() from fresh runner", r2.Plan(), nil)
	wantStrings(t, "Applied() from fresh runner", r2.Applied(), threeIDs)
	if _, err := r2.Up(); err != nil {
		t.Fatalf("Up on fresh runner: %v", err)
	}
	if len(log2) != 0 {
		t.Fatalf("fresh runner re-ran migrations: %v", log2)
	}
}

func TestNewMigrationsExtendThePlan(t *testing.T) {
	path := statePath(t)
	var log []string
	migs := threeMigs(&log)

	r1 := newRunner(t, path, migs[:2])
	if _, err := r1.Up(); err != nil {
		t.Fatalf("Up: %v", err)
	}

	r2 := newRunner(t, path, migs)
	wantStrings(t, "Plan()", r2.Plan(), []string{"0003_backfill"})
	applied, err := r2.Up()
	if err != nil {
		t.Fatalf("Up: %v", err)
	}
	wantStrings(t, "Up() applied", applied, []string{"0003_backfill"})
	wantStrings(t, "execution log", log, []string{"up:0001", "up:0002", "up:0003"})
}

func TestConfigValidation(t *testing.T) {
	var log []string
	ok := rec(&log, "x")

	if _, err := NewRunner(statePath(t), []Migration{
		{ID: "a", Up: ok, Down: ok},
		{ID: "a", Up: ok, Down: ok},
	}); err == nil {
		t.Fatal("duplicate migration ids must be rejected")
	}
	if _, err := NewRunner(statePath(t), []Migration{
		{ID: "", Up: ok, Down: ok},
	}); err == nil {
		t.Fatal("empty migration id must be rejected")
	}
	if _, err := NewRunner(statePath(t), []Migration{
		{ID: "a", Up: nil, Down: ok},
	}); err == nil {
		t.Fatal("nil Up func must be rejected")
	}
}

func TestAppliedStateMustBePrefixOfConfiguredOrder(t *testing.T) {
	path := statePath(t)
	var log []string
	migs := threeMigs(&log)
	r := newRunner(t, path, migs[:2])
	if _, err := r.Up(); err != nil {
		t.Fatalf("Up: %v", err)
	}

	// Reordered history: applied [0001, 0002] but configured [0002, 0001].
	if _, err := NewRunner(path, []Migration{migs[1], migs[0]}); err == nil {
		t.Fatal("runner must reject a state file that is not a prefix of the configured migration order")
	}
	// Shrunk history: state knows 0002 but the config no longer lists it.
	if _, err := NewRunner(path, migs[:1]); err == nil {
		t.Fatal("runner must reject a state file referencing an unknown migration")
	}
	// Superset is fine: same prefix, new migrations appended.
	if _, err := NewRunner(path, migs); err != nil {
		t.Fatalf("appending new migrations must be accepted, got: %v", err)
	}
}
