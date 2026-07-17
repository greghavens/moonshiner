package migrunner

import (
	"errors"
	"strings"
	"testing"
)

func TestUpFailureMidSequenceLeavesConsistentState(t *testing.T) {
	path := statePath(t)
	boom := errors.New("column already exists")
	broken := true
	var log []string
	migs := []Migration{
		{ID: "m1", Up: rec(&log, "up:m1"), Down: rec(&log, "down:m1")},
		{ID: "m2", Up: func() error {
			log = append(log, "up:m2")
			if broken {
				return boom
			}
			return nil
		}, Down: rec(&log, "down:m2")},
		{ID: "m3", Up: rec(&log, "up:m3"), Down: rec(&log, "down:m3")},
	}

	r := newRunner(t, path, migs)
	applied, err := r.Up()
	if err == nil {
		t.Fatal("Up must fail when a migration fails")
	}
	if !strings.Contains(err.Error(), "m2") {
		t.Fatalf("Up error %q must identify the failing migration m2", err)
	}
	wantStrings(t, "Up() applied before failure", applied, []string{"m1"})
	wantStrings(t, "execution log", log, []string{"up:m1", "up:m2"}) // m3 never attempted
	wantStrings(t, "Applied()", r.Applied(), []string{"m1"})

	// The state file must agree: a fresh runner resumes exactly at m2.
	r2 := newRunner(t, path, migs)
	wantStrings(t, "fresh Applied()", r2.Applied(), []string{"m1"})
	wantStrings(t, "fresh Plan()", r2.Plan(), []string{"m2", "m3"})

	// Once the migration is fixed, retry picks up where it left off.
	broken = false
	applied, err = r2.Up()
	if err != nil {
		t.Fatalf("retry Up: %v", err)
	}
	wantStrings(t, "retry Up() applied", applied, []string{"m2", "m3"})
	wantStrings(t, "final log", log, []string{"up:m1", "up:m2", "up:m2", "up:m3"})
	wantStrings(t, "final Applied()", r2.Applied(), []string{"m1", "m2", "m3"})
}

func TestDownRollsBackInReverseOrder(t *testing.T) {
	path := statePath(t)
	var log []string
	r := newRunner(t, path, threeMigs(&log))
	if _, err := r.Up(); err != nil {
		t.Fatalf("Up: %v", err)
	}

	rolled, err := r.Down(2)
	if err != nil {
		t.Fatalf("Down(2): %v", err)
	}
	wantStrings(t, "Down(2) rolled back", rolled, []string{"0003_backfill", "0002_add_email"})
	wantStrings(t, "execution log", log, []string{"up:0001", "up:0002", "up:0003", "down:0003", "down:0002"})
	wantStrings(t, "Applied()", r.Applied(), []string{"0001_create_users"})

	var log2 []string
	r2 := newRunner(t, path, threeMigs(&log2))
	wantStrings(t, "fresh Plan()", r2.Plan(), []string{"0002_add_email", "0003_backfill"})
}

func TestDownRejectsBadCounts(t *testing.T) {
	path := statePath(t)
	var log []string
	r := newRunner(t, path, threeMigs(&log))
	if _, err := r.Up(); err != nil {
		t.Fatalf("Up: %v", err)
	}
	before := len(log)

	if _, err := r.Down(4); err == nil {
		t.Fatal("Down(4) with only 3 applied must return an error")
	}
	if _, err := r.Down(0); err == nil {
		t.Fatal("Down(0) must return an error")
	}
	if _, err := r.Down(-1); err == nil {
		t.Fatal("Down(-1) must return an error")
	}
	if len(log) != before {
		t.Fatalf("rejected Down must not run anything, log grew: %v", log[before:])
	}
	wantStrings(t, "Applied()", r.Applied(), threeIDs)
}

func TestDownFailureMidWayKeepsConsistentState(t *testing.T) {
	path := statePath(t)
	boom := errors.New("cannot drop: table has rows")
	var log []string
	migs := []Migration{
		{ID: "m1", Up: rec(&log, "up:m1"), Down: rec(&log, "down:m1")},
		{ID: "m2", Up: rec(&log, "up:m2"), Down: func() error {
			log = append(log, "down:m2")
			return boom
		}},
		{ID: "m3", Up: rec(&log, "up:m3"), Down: rec(&log, "down:m3")},
	}
	r := newRunner(t, path, migs)
	if _, err := r.Up(); err != nil {
		t.Fatalf("Up: %v", err)
	}

	rolled, err := r.Down(3)
	if err == nil {
		t.Fatal("Down must fail when a rollback step fails")
	}
	if !strings.Contains(err.Error(), "m2") {
		t.Fatalf("Down error %q must identify the failing migration m2", err)
	}
	wantStrings(t, "Down rolled back before failure", rolled, []string{"m3"})
	wantStrings(t, "Applied()", r.Applied(), []string{"m1", "m2"})

	// m1 was never rolled back.
	wantStrings(t, "execution log", log,
		[]string{"up:m1", "up:m2", "up:m3", "down:m3", "down:m2"})

	r2 := newRunner(t, path, migs)
	wantStrings(t, "fresh Applied()", r2.Applied(), []string{"m1", "m2"})
	wantStrings(t, "fresh Plan()", r2.Plan(), []string{"m3"})
}

func TestIrreversibleMigrationBlocksRollbackUpFront(t *testing.T) {
	path := statePath(t)
	var log []string
	migs := []Migration{
		{ID: "m1", Up: rec(&log, "up:m1"), Down: rec(&log, "down:m1")},
		{ID: "m2", Up: rec(&log, "up:m2"), Down: nil}, // irreversible
		{ID: "m3", Up: rec(&log, "up:m3"), Down: rec(&log, "down:m3")},
	}
	r := newRunner(t, path, migs)
	if _, err := r.Up(); err != nil {
		t.Fatalf("Up: %v", err)
	}
	before := len(log)

	// Rolling back 2 would need m2.Down — refuse before touching m3.
	if _, err := r.Down(2); err == nil {
		t.Fatal("Down spanning an irreversible migration must return an error")
	}
	if len(log) != before {
		t.Fatalf("failed Down must not run any Down funcs, log grew: %v", log[before:])
	}
	wantStrings(t, "Applied()", r.Applied(), []string{"m1", "m2", "m3"})

	// Rolling back just m3 is still fine.
	rolled, err := r.Down(1)
	if err != nil {
		t.Fatalf("Down(1): %v", err)
	}
	wantStrings(t, "Down(1) rolled back", rolled, []string{"m3"})
	wantStrings(t, "Applied()", r.Applied(), []string{"m1", "m2"})
}
