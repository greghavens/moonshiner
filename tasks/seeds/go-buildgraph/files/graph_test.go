package buildgraph_test

// Workspace loading, labels, validation, cycle reporting, and dry-run plans.
// Execution and incrementality live in exec_test.go.

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"

	bg "go-buildgraph"
)

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

// counters tracks how many times each target's action ran.
type counters struct {
	mu   sync.Mutex
	runs map[string]int
}

func newCounters() *counters { return &counters{runs: map[string]int{}} }

func (c *counters) bump(label string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.runs[label]++
}

func (c *counters) get(label string) int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.runs[label]
}

func (c *counters) total() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	n := 0
	for _, v := range c.runs {
		n += v
	}
	return n
}

// concat writes every input file then every dep output, joined with "|".
func concat(c *counters) bg.ActionFunc {
	return func(tc *bg.TaskContext) error {
		c.bump(tc.Label)
		var parts []string
		for _, p := range tc.Inputs {
			b, err := os.ReadFile(p)
			if err != nil {
				return err
			}
			parts = append(parts, string(b))
		}
		for _, p := range tc.DepOutputs {
			b, err := os.ReadFile(p)
			if err != nil {
				return err
			}
			parts = append(parts, string(b))
		}
		return os.WriteFile(tc.OutPath, []byte(strings.Join(parts, "|")), 0o644)
	}
}

// linecount writes "<n> lines" where n counts newlines across all inputs.
func linecount(c *counters) bg.ActionFunc {
	return func(tc *bg.TaskContext) error {
		c.bump(tc.Label)
		n := 0
		for _, p := range append(append([]string{}, tc.Inputs...), tc.DepOutputs...) {
			b, err := os.ReadFile(p)
			if err != nil {
				return err
			}
			n += strings.Count(string(b), "\n")
		}
		return os.WriteFile(tc.OutPath, []byte(strconv.Itoa(n)+" lines"), 0o644)
	}
}

// diamondWorkspace: app:bin -> {lib:left, lib:right} -> lib:base.
func diamondWorkspace(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "lib", "build.json"), `{
	  "targets": [
	    {"name": "base",  "action": "linecount", "inputs": ["seed.txt"]},
	    {"name": "left",  "action": "concat", "inputs": ["l.txt"], "deps": ["base"]},
	    {"name": "right", "action": "concat", "inputs": ["r.txt"], "deps": ["base"]}
	  ]
	}`)
	writeFile(t, filepath.Join(root, "app", "build.json"), `{
	  "targets": [
	    {"name": "bin", "action": "concat", "inputs": ["main.txt"], "deps": ["lib:left", "lib:right"]}
	  ]
	}`)
	writeFile(t, filepath.Join(root, "lib", "seed.txt"), "alpha\nbeta\n")
	writeFile(t, filepath.Join(root, "lib", "l.txt"), "L")
	writeFile(t, filepath.Join(root, "lib", "r.txt"), "R")
	writeFile(t, filepath.Join(root, "app", "main.txt"), "M")
	return root
}

func TestLoadWorkspaceLabelsAndOutPaths(t *testing.T) {
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "build.json"), `{
	  "targets": [{"name": "gen", "action": "concat", "inputs": ["seed.txt"]}]
	}`)
	writeFile(t, filepath.Join(root, "lib", "util", "build.json"), `{
	  "targets": [
	    {"name": "codegen", "action": "concat", "inputs": ["tpl.txt"], "deps": [":gen"]},
	    {"name": "pack", "action": "concat", "deps": ["codegen"]}
	  ]
	}`)
	// anything under out/ is build output and must be ignored by the walker
	writeFile(t, filepath.Join(root, "out", "build.json"), `{this is not json`)

	ws, err := bg.LoadWorkspace(root)
	if err != nil {
		t.Fatalf("LoadWorkspace: %v", err)
	}
	want := []string{":gen", "lib/util:codegen", "lib/util:pack"}
	if got := ws.Targets(); !equalStrings(got, want) {
		t.Fatalf("Targets() = %v, want %v", got, want)
	}
	pack, ok := ws.Target("lib/util:pack")
	if !ok {
		t.Fatal("lib/util:pack not found")
	}
	if !equalStrings(pack.Deps, []string{"lib/util:codegen"}) {
		t.Fatalf("bare dep not canonicalized: %v", pack.Deps)
	}
	codegen, _ := ws.Target("lib/util:codegen")
	if !equalStrings(codegen.Deps, []string{":gen"}) {
		t.Fatalf("root-label dep mangled: %v", codegen.Deps)
	}
	if !equalStrings(codegen.Inputs, []string{"tpl.txt"}) {
		t.Fatalf("inputs should stay as declared: %v", codegen.Inputs)
	}
	if _, ok := ws.Target("lib/util:ghost"); ok {
		t.Fatal("ghost target reported as present")
	}
	if got, want := ws.OutPath(":gen"), filepath.Join(root, "out", "gen"); got != want {
		t.Fatalf("OutPath(:gen) = %q, want %q", got, want)
	}
	if got, want := ws.OutPath("lib/util:codegen"), filepath.Join(root, "out", "lib", "util", "codegen"); got != want {
		t.Fatalf("OutPath(lib/util:codegen) = %q, want %q", got, want)
	}
}

func TestManifestValidation(t *testing.T) {
	cases := []struct {
		name     string
		manifest string
		wantSub  []string
	}{
		{"bad json", `{nope`, []string{"build.json"}},
		{"duplicate name", `{"targets": [
			{"name": "dup", "action": "a"}, {"name": "dup", "action": "a"}]}`,
			[]string{"dup"}},
		{"colon in name", `{"targets": [{"name": "we:ird", "action": "a"}]}`,
			[]string{"we:ird"}},
		{"slash in name", `{"targets": [{"name": "a/b", "action": "a"}]}`,
			[]string{"a/b"}},
		{"empty name", `{"targets": [{"name": "", "action": "a"}]}`, nil},
		{"missing action", `{"targets": [{"name": "x"}]}`, []string{"x"}},
		{"absolute input", `{"targets": [{"name": "x", "action": "a", "inputs": ["/abs/seed.txt"]}]}`,
			[]string{"input"}},
		{"escaping input", `{"targets": [{"name": "x", "action": "a", "inputs": ["../escape.txt"]}]}`,
			[]string{"input"}},
		{"unknown field", `{"targets": [{"name": "x", "action": "a", "commandz": true}]}`,
			[]string{"unknown field"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			root := t.TempDir()
			writeFile(t, filepath.Join(root, "m", "build.json"), tc.manifest)
			_, err := bg.LoadWorkspace(root)
			if err == nil {
				t.Fatalf("LoadWorkspace accepted manifest: %s", tc.manifest)
			}
			for _, sub := range tc.wantSub {
				if !strings.Contains(err.Error(), sub) {
					t.Fatalf("error %q should mention %q", err, sub)
				}
			}
		})
	}
}

func TestUnknownDepNamesBothSides(t *testing.T) {
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "app", "build.json"), `{
	  "targets": [{"name": "main", "action": "a", "deps": ["lib:nope"]}]
	}`)
	_, err := bg.LoadWorkspace(root)
	if err == nil {
		t.Fatal("unknown dep accepted")
	}
	for _, sub := range []string{"app:main", "lib:nope"} {
		if !strings.Contains(err.Error(), sub) {
			t.Fatalf("error %q should mention %q", err, sub)
		}
	}
}

func TestCycleErrorsNameTheCycle(t *testing.T) {
	root := t.TempDir()
	writeFile(t, filepath.Join(root, "a", "build.json"),
		`{"targets": [{"name": "one", "action": "a", "deps": ["b:two"]}]}`)
	writeFile(t, filepath.Join(root, "b", "build.json"),
		`{"targets": [{"name": "two", "action": "a", "deps": ["c:three"]}]}`)
	writeFile(t, filepath.Join(root, "c", "build.json"),
		`{"targets": [{"name": "three", "action": "a", "deps": ["a:one"]}]}`)
	_, err := bg.LoadWorkspace(root)
	if err == nil {
		t.Fatal("cycle accepted")
	}
	if want := "dependency cycle: a:one -> b:two -> c:three -> a:one"; !strings.Contains(err.Error(), want) {
		t.Fatalf("error %q should contain %q", err, want)
	}

	root = t.TempDir()
	writeFile(t, filepath.Join(root, "d", "build.json"),
		`{"targets": [{"name": "d", "action": "a", "deps": ["d"]}]}`)
	_, err = bg.LoadWorkspace(root)
	if err == nil || !strings.Contains(err.Error(), "dependency cycle: d:d -> d:d") {
		t.Fatalf("self-cycle error = %v", err)
	}

	// the cycle report starts at its lexicographically smallest member even
	// when the cycle hangs off a non-cyclic entry point
	root = t.TempDir()
	writeFile(t, filepath.Join(root, "m", "build.json"), `{"targets": [
	  {"name": "x", "action": "a", "deps": ["c"]},
	  {"name": "c", "action": "a", "deps": ["b"]},
	  {"name": "b", "action": "a", "deps": ["c"]}
	]}`)
	_, err = bg.LoadWorkspace(root)
	if err == nil || !strings.Contains(err.Error(), "dependency cycle: m:b -> m:c -> m:b") {
		t.Fatalf("hanging cycle error = %v", err)
	}
}

func TestExecutorOptionValidation(t *testing.T) {
	root := diamondWorkspace(t)
	ws, err := bg.LoadWorkspace(root)
	if err != nil {
		t.Fatal(err)
	}
	c := newCounters()
	if _, err := bg.NewExecutor(ws, bg.Options{Workers: 0, Actions: map[string]bg.ActionFunc{
		"concat": concat(c), "linecount": linecount(c)}}); err == nil ||
		!strings.Contains(err.Error(), "workers") {
		t.Fatalf("Workers 0 accepted: %v", err)
	}
	if _, err := bg.NewExecutor(ws, bg.Options{Workers: 2, Actions: map[string]bg.ActionFunc{
		"concat": concat(c)}}); err == nil ||
		!strings.Contains(err.Error(), "lib:base") || !strings.Contains(err.Error(), "linecount") {
		t.Fatalf("missing action accepted: %v", err)
	}
}

func TestPlanIsPureAndOrdered(t *testing.T) {
	root := diamondWorkspace(t)
	ws, err := bg.LoadWorkspace(root)
	if err != nil {
		t.Fatal(err)
	}
	c := newCounters()
	ex, err := bg.NewExecutor(ws, bg.Options{Workers: 2, Actions: map[string]bg.ActionFunc{
		"concat": concat(c), "linecount": linecount(c)}})
	if err != nil {
		t.Fatal(err)
	}
	steps, err := ex.Plan("app:bin")
	if err != nil {
		t.Fatal(err)
	}
	want := []bg.Step{
		{Label: "lib:base", Run: true},
		{Label: "lib:left", Run: true},
		{Label: "lib:right", Run: true},
		{Label: "app:bin", Run: true},
	}
	if len(steps) != len(want) {
		t.Fatalf("Plan = %+v, want %+v", steps, want)
	}
	for i := range want {
		if steps[i] != want[i] {
			t.Fatalf("Plan[%d] = %+v, want %+v", i, steps[i], want[i])
		}
	}
	if got, want := bg.FormatPlan(steps), "run lib:base\nrun lib:left\nrun lib:right\nrun app:bin\n"; got != want {
		t.Fatalf("FormatPlan = %q, want %q", got, want)
	}
	if c.total() != 0 {
		t.Fatalf("Plan invoked actions %d times", c.total())
	}
	if _, err := os.Stat(filepath.Join(root, "out")); !os.IsNotExist(err) {
		t.Fatalf("Plan must not create out/: %v", err)
	}
	if _, err := ex.Plan("ghost:x"); err == nil || !strings.Contains(err.Error(), "ghost:x") {
		t.Fatalf("Plan(ghost:x) = %v", err)
	}
}

func equalStrings(got, want []string) bool {
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
