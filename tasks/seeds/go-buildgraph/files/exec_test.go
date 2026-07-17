package buildgraph_test

// Execution semantics: content-hash incrementality with early cutoff,
// diamond dedup, failure/resume, output verification, and bounded parallel
// workers. Loading/validation/plan shape live in graph_test.go.

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	bg "go-buildgraph"
)

func mustLoad(t *testing.T, root string) *bg.Workspace {
	t.Helper()
	ws, err := bg.LoadWorkspace(root)
	if err != nil {
		t.Fatal(err)
	}
	return ws
}

func mustExecutor(t *testing.T, ws *bg.Workspace, workers int, actions map[string]bg.ActionFunc) *bg.Executor {
	t.Helper()
	ex, err := bg.NewExecutor(ws, bg.Options{Workers: workers, Actions: actions})
	if err != nil {
		t.Fatal(err)
	}
	return ex
}

func checkReport(t *testing.T, rep *bg.Report, ran, upToDate []string) {
	t.Helper()
	if rep == nil {
		t.Fatal("nil report")
	}
	if !equalStrings(rep.Ran, ran) {
		t.Fatalf("Ran = %v, want %v", rep.Ran, ran)
	}
	if !equalStrings(rep.UpToDate, upToDate) {
		t.Fatalf("UpToDate = %v, want %v", rep.UpToDate, upToDate)
	}
}

func readOut(t *testing.T, ws *bg.Workspace, label string) string {
	t.Helper()
	b, err := os.ReadFile(ws.OutPath(label))
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestExecuteIncrementalStory(t *testing.T) {
	root := diamondWorkspace(t)
	ws := mustLoad(t, root)
	c := newCounters()
	actions := map[string]bg.ActionFunc{"concat": concat(c), "linecount": linecount(c)}
	ex := mustExecutor(t, ws, 2, actions)

	all := []string{"app:bin", "lib:base", "lib:left", "lib:right"}

	// 1. cold build: everything runs, the shared dep exactly once
	rep, err := ex.Execute("app:bin")
	if err != nil {
		t.Fatal(err)
	}
	checkReport(t, rep, all, nil)
	for _, label := range all {
		if c.get(label) != 1 {
			t.Fatalf("%s ran %d times on a cold build", label, c.get(label))
		}
	}
	if got := readOut(t, ws, "lib:base"); got != "2 lines" {
		t.Fatalf("lib:base output = %q", got)
	}
	if got := readOut(t, ws, "lib:left"); got != "L|2 lines" {
		t.Fatalf("lib:left output = %q (inputs first, then dep outputs)", got)
	}
	// dep outputs feed dependents sorted by dep label: left before right
	if got := readOut(t, ws, "app:bin"); got != "M|L|2 lines|R|2 lines" {
		t.Fatalf("app:bin output = %q", got)
	}
	stateBytes, err := os.ReadFile(filepath.Join(root, "out", "state.json"))
	if err != nil || !json.Valid(stateBytes) {
		t.Fatalf("out/state.json missing or invalid: %v", err)
	}

	// 2. immediate rebuild: nothing to do
	rep, err = ex.Execute("app:bin")
	if err != nil {
		t.Fatal(err)
	}
	checkReport(t, rep, nil, all)

	// 3. brand-new executor over the same tree: state survives on disk
	rep, err = mustExecutor(t, mustLoad(t, root), 2, actions).Execute("app:bin")
	if err != nil {
		t.Fatal(err)
	}
	checkReport(t, rep, nil, all)

	// 4. rewriting a file with identical bytes must not trigger anything —
	// mtime is not a signal here
	writeFile(t, filepath.Join(root, "lib", "seed.txt"), "alpha\nbeta\n")
	rep, err = ex.Execute("app:bin")
	if err != nil {
		t.Fatal(err)
	}
	checkReport(t, rep, nil, all)

	// 5. early cutoff: the edit changes seed.txt but not base's output.
	// Plan is allowed to be pessimistic (a runner assumes new output)...
	writeFile(t, filepath.Join(root, "lib", "seed.txt"), "alpha\ngamm\n")
	steps, err := ex.Plan("app:bin")
	if err != nil {
		t.Fatal(err)
	}
	if got, want := bg.FormatPlan(steps), "run lib:base\nrun lib:left\nrun lib:right\nrun app:bin\n"; got != want {
		t.Fatalf("pessimistic plan = %q, want %q", got, want)
	}
	// ...but Execute compares real output hashes and stops the wave
	rep, err = ex.Execute("app:bin")
	if err != nil {
		t.Fatal(err)
	}
	checkReport(t, rep, []string{"lib:base"}, []string{"app:bin", "lib:left", "lib:right"})
	if got := readOut(t, ws, "lib:base"); got != "2 lines" {
		t.Fatalf("lib:base output changed: %q", got)
	}

	// 6. a change that alters base's output rebuilds the whole cone
	writeFile(t, filepath.Join(root, "lib", "seed.txt"), "alpha\nbeta\ngamma\n")
	rep, err = ex.Execute("app:bin")
	if err != nil {
		t.Fatal(err)
	}
	checkReport(t, rep, all, nil)
	if got := readOut(t, ws, "app:bin"); got != "M|L|3 lines|R|3 lines" {
		t.Fatalf("app:bin output = %q", got)
	}

	// 7. deleting one output rebuilds just that target; its rebuilt output
	// is identical, so dependents stay skipped
	if err := os.Remove(ws.OutPath("lib:left")); err != nil {
		t.Fatal(err)
	}
	rep, err = ex.Execute("app:bin")
	if err != nil {
		t.Fatal(err)
	}
	checkReport(t, rep, []string{"lib:left"}, []string{"app:bin", "lib:base", "lib:right"})

	// 8. an unreadable state file means a full rebuild, not an error
	writeFile(t, filepath.Join(root, "out", "state.json"), "junk")
	rep, err = ex.Execute("app:bin")
	if err != nil {
		t.Fatal(err)
	}
	checkReport(t, rep, all, nil)

	// 9. a fully current tree plans as all skips
	steps, err = ex.Plan("app:bin")
	if err != nil {
		t.Fatal(err)
	}
	if got, want := bg.FormatPlan(steps), "skip lib:base\nskip lib:left\nskip lib:right\nskip app:bin\n"; got != want {
		t.Fatalf("plan = %q, want %q", got, want)
	}
}

func TestMissingInputIsAnError(t *testing.T) {
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "x", "build.json"),
		`{"targets": [{"name": "one", "action": "concat", "inputs": ["nope.txt"]}]}`)
	ws := mustLoad(t, root)
	c := newCounters()
	ex := mustExecutor(t, ws, 1, map[string]bg.ActionFunc{"concat": concat(c)})

	rep, err := ex.Execute("x:one")
	if err == nil {
		t.Fatal("Execute with a missing input succeeded")
	}
	for _, sub := range []string{"x:one", "nope.txt"} {
		if !strings.Contains(err.Error(), sub) {
			t.Fatalf("error %q should mention %q", err, sub)
		}
	}
	if rep == nil || len(rep.Ran) != 0 {
		t.Fatalf("report after missing input = %+v", rep)
	}
	if _, err := ex.Plan("x:one"); err == nil || !strings.Contains(err.Error(), "nope.txt") {
		t.Fatalf("Plan error = %v", err)
	}
}

func TestUnknownExecuteTarget(t *testing.T) {
	root := diamondWorkspace(t)
	c := newCounters()
	ex := mustExecutor(t, mustLoad(t, root), 1, map[string]bg.ActionFunc{
		"concat": concat(c), "linecount": linecount(c)})
	if _, err := ex.Execute("ghost:x"); err == nil || !strings.Contains(err.Error(), "ghost:x") {
		t.Fatalf("Execute(ghost:x) err = %v", err)
	}
}

func TestActionMustProduceItsOutput(t *testing.T) {
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "y", "build.json"),
		`{"targets": [{"name": "lazy", "action": "noop"}]}`)
	ws := mustLoad(t, root)
	noop := func(tc *bg.TaskContext) error { return nil } // never writes OutPath
	ex := mustExecutor(t, ws, 1, map[string]bg.ActionFunc{"noop": noop})
	_, err := ex.Execute("y:lazy")
	if err == nil {
		t.Fatal("action that wrote nothing was accepted")
	}
	for _, sub := range []string{"y:lazy", "output"} {
		if !strings.Contains(err.Error(), sub) {
			t.Fatalf("error %q should mention %q", err, sub)
		}
	}
}

func TestFailureStopsDependentsAndResumes(t *testing.T) {
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "c", "build.json"), `{"targets": [
	  {"name": "one",   "action": "ok", "inputs": ["a.txt"]},
	  {"name": "two",   "action": "flaky", "deps": ["one"]},
	  {"name": "three", "action": "ok", "deps": ["two"]}
	]}`)
	writeFile(t, filepath.Join(root, "c", "a.txt"), "seed")

	okAction := func(tc *bg.TaskContext) error {
		return os.WriteFile(tc.OutPath, []byte(tc.Label), 0o644)
	}
	errBoom := errors.New("tool crashed")
	boom := func(tc *bg.TaskContext) error { return errBoom }

	ws := mustLoad(t, root)
	ex := mustExecutor(t, ws, 1, map[string]bg.ActionFunc{"ok": okAction, "flaky": boom})
	rep, err := ex.Execute("c:three")
	if err == nil {
		t.Fatal("failing action did not fail the build")
	}
	if !errors.Is(err, errBoom) {
		t.Fatalf("err %v should wrap the action's error", err)
	}
	if !strings.Contains(err.Error(), "c:two") {
		t.Fatalf("err %q should name the failing target", err)
	}
	checkReport(t, rep, []string{"c:one"}, nil)

	// same tree, fixed action: the survivor is skipped, the rest completes
	ex = mustExecutor(t, mustLoad(t, root), 1, map[string]bg.ActionFunc{"ok": okAction, "flaky": okAction})
	rep, err = ex.Execute("c:three")
	if err != nil {
		t.Fatal(err)
	}
	checkReport(t, rep, []string{"c:three", "c:two"}, []string{"c:one"})
}

func wideWorkspace(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "p", "build.json"), `{"targets": [
	  {"name": "w1", "action": "gate", "inputs": ["seed.txt"]},
	  {"name": "w2", "action": "gate", "inputs": ["seed.txt"]},
	  {"name": "w3", "action": "gate", "inputs": ["seed.txt"]},
	  {"name": "w4", "action": "gate", "inputs": ["seed.txt"]},
	  {"name": "all", "action": "gate", "deps": ["w1", "w2", "w3", "w4"]}
	]}`)
	writeFile(t, filepath.Join(root, "p", "seed.txt"), "s")
	return root
}

func TestBoundedWorkersRunWideLayersInParallel(t *testing.T) {
	root := wideWorkspace(t)
	ws := mustLoad(t, root)

	started := make(chan string, 16)
	release := make(chan struct{})
	var inFlight, maxInFlight atomic.Int64
	gate := func(tc *bg.TaskContext) error {
		cur := inFlight.Add(1)
		for {
			prev := maxInFlight.Load()
			if cur <= prev || maxInFlight.CompareAndSwap(prev, cur) {
				break
			}
		}
		started <- tc.Label
		<-release
		inFlight.Add(-1)
		return os.WriteFile(tc.OutPath, []byte(tc.Label), 0o644)
	}

	ex := mustExecutor(t, ws, 2, map[string]bg.ActionFunc{"gate": gate})
	type result struct {
		rep *bg.Report
		err error
	}
	done := make(chan result, 1)
	go func() {
		rep, err := ex.Execute("p:all")
		done <- result{rep, err}
	}()

	recv := func(what string) string {
		select {
		case label := <-started:
			return label
		case <-time.After(10 * time.Second):
			t.Fatalf("timed out waiting for %s", what)
			return ""
		}
	}
	first := recv("first start")
	second := recv("second start")
	if first == second {
		t.Fatalf("same target started twice: %s", first)
	}
	// two actions are now both inside the gate: the layer really is parallel
	if got := inFlight.Load(); got != 2 {
		t.Fatalf("in-flight while holding the gate = %d, want 2", got)
	}
	close(release)
	for i := 0; i < 3; i++ { // w-remaining x2, then p:all
		recv("drain")
	}
	var res result
	select {
	case res = <-done:
	case <-time.After(30 * time.Second):
		t.Fatal("Execute did not finish")
	}
	if res.err != nil {
		t.Fatal(res.err)
	}
	checkReport(t, res.rep, []string{"p:all", "p:w1", "p:w2", "p:w3", "p:w4"}, nil)
	if got := maxInFlight.Load(); got != 2 {
		t.Fatalf("max in-flight = %d, want exactly the worker limit 2", got)
	}
}

func TestSingleWorkerNeverOverlaps(t *testing.T) {
	root := wideWorkspace(t)
	ws := mustLoad(t, root)
	var inFlight, maxInFlight atomic.Int64
	gate := func(tc *bg.TaskContext) error {
		cur := inFlight.Add(1)
		for {
			prev := maxInFlight.Load()
			if cur <= prev || maxInFlight.CompareAndSwap(prev, cur) {
				break
			}
		}
		defer inFlight.Add(-1)
		return os.WriteFile(tc.OutPath, []byte(tc.Label), 0o644)
	}
	ex := mustExecutor(t, ws, 1, map[string]bg.ActionFunc{"gate": gate})
	if _, err := ex.Execute("p:all"); err != nil {
		t.Fatal(err)
	}
	if got := maxInFlight.Load(); got != 1 {
		t.Fatalf("max in-flight with one worker = %d", got)
	}
}
