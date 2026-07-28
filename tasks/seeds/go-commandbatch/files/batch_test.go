package commandbatch

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"
)

func TestCommandBatchHelper(t *testing.T) {
	separator := -1
	for i, arg := range os.Args {
		if arg == "--" {
			separator = i
			break
		}
	}
	if separator < 0 || separator+1 >= len(os.Args) {
		return
	}

	mode := os.Args[separator+1]
	args := os.Args[separator+2:]
	switch mode {
	case "argv":
		if err := json.NewEncoder(os.Stdout).Encode(args); err != nil {
			os.Exit(111)
		}
		os.Exit(0)
	case "emit":
		fmt.Fprintf(os.Stdout, "stdout-%s\n", args[0])
		fmt.Fprintf(os.Stderr, "stderr-%s\n", args[0])
		os.Exit(0)
	case "sleep-emit":
		delay, err := time.ParseDuration(args[0])
		if err != nil {
			os.Exit(112)
		}
		time.Sleep(delay)
		fmt.Fprintf(os.Stdout, "%s\n", args[1])
		os.Exit(0)
	case "gate":
		dir, id := args[0], args[1]
		if err := os.WriteFile(filepath.Join(dir, "ready-"+id), []byte("ready"), 0o600); err != nil {
			os.Exit(113)
		}
		deadline := time.Now().Add(5 * time.Second)
		for {
			if _, err := os.Stat(filepath.Join(dir, "release")); err == nil {
				fmt.Fprintf(os.Stdout, "%s\n", id)
				os.Exit(0)
			}
			if time.Now().After(deadline) {
				fmt.Fprintln(os.Stderr, "gate timed out")
				os.Exit(114)
			}
			time.Sleep(5 * time.Millisecond)
		}
	case "block":
		fmt.Fprintln(os.Stdout, "active-stdout")
		fmt.Fprintln(os.Stderr, "active-stderr")
		if err := os.WriteFile(args[0], []byte("ready"), 0o600); err != nil {
			os.Exit(115)
		}
		time.Sleep(1500 * time.Millisecond)
		fmt.Fprintln(os.Stdout, "block escaped cancellation")
		os.Exit(0)
	case "mark":
		if err := os.WriteFile(args[0], []byte("launched"), 0o600); err != nil {
			os.Exit(116)
		}
		os.Exit(0)
	case "fail":
		code, err := strconv.Atoi(args[0])
		if err != nil {
			os.Exit(117)
		}
		fmt.Fprintf(os.Stdout, "before-%s\n", args[1])
		fmt.Fprintf(os.Stderr, "failure-%s\n", args[1])
		os.Exit(code)
	default:
		os.Exit(118)
	}
}

func helperCommand(t *testing.T, mode string, args ...string) Command {
	t.Helper()
	executable, err := os.Executable()
	if err != nil {
		t.Fatalf("locate test executable: %v", err)
	}
	argv := []string{"-test.run=^TestCommandBatchHelper$", "--", mode}
	argv = append(argv, args...)
	return Command{Path: executable, Args: argv}
}

func requireResultCount(t *testing.T, got []Result, want int) {
	t.Helper()
	if len(got) != want {
		t.Fatalf("Run returned %d results, want %d: %+v", len(got), want, got)
	}
}

func waitFor(t *testing.T, timeout time.Duration, what string, ready func() bool) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for !ready() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func TestLiteralArgumentVectors(t *testing.T) {
	wantArgs := []string{
		"two words",
		"",
		"$literal",
		"semi;colon",
		`back\slash`,
		"*.go",
	}
	got := Run(t.Context(), 1, []Command{helperCommand(t, "argv", wantArgs...)})
	requireResultCount(t, got, 1)
	if got[0].Err != nil || got[0].ExitCode != 0 {
		t.Fatalf("literal argv command failed: code=%d err=%v stderr=%q", got[0].ExitCode, got[0].Err, got[0].Stderr)
	}
	var received []string
	if err := json.Unmarshal([]byte(got[0].Stdout), &received); err != nil {
		t.Fatalf("decode helper argv from %q: %v", got[0].Stdout, err)
	}
	if !reflect.DeepEqual(received, wantArgs) {
		t.Fatalf("child argv = %#v, want %#v", received, wantArgs)
	}
}

func TestResultsRemainInInputOrder(t *testing.T) {
	commands := []Command{
		helperCommand(t, "sleep-emit", "220ms", "first"),
		helperCommand(t, "sleep-emit", "15ms", "second"),
		helperCommand(t, "sleep-emit", "35ms", "third"),
	}
	got := Run(t.Context(), 3, commands)
	requireResultCount(t, got, len(commands))
	want := []string{"first\n", "second\n", "third\n"}
	for i := range want {
		if got[i].Err != nil || got[i].ExitCode != 0 || got[i].Stdout != want[i] {
			t.Fatalf("result[%d] = {stdout:%q stderr:%q code:%d err:%v}, want stdout %q success", i, got[i].Stdout, got[i].Stderr, got[i].ExitCode, got[i].Err, want[i])
		}
	}
}

func TestParallelismIsPresentAndBounded(t *testing.T) {
	dir := t.TempDir()
	commands := make([]Command, 4)
	for i := range commands {
		commands[i] = helperCommand(t, "gate", dir, strconv.Itoa(i))
	}

	done := make(chan []Result, 1)
	go func() {
		done <- Run(t.Context(), 2, commands)
	}()

	readyCount := func() int {
		matches, err := filepath.Glob(filepath.Join(dir, "ready-*"))
		if err != nil {
			t.Fatalf("glob ready files: %v", err)
		}
		return len(matches)
	}
	waitFor(t, 3*time.Second, "two commands to overlap", func() bool {
		return readyCount() >= 2
	})
	time.Sleep(150 * time.Millisecond)
	if n := readyCount(); n != 2 {
		t.Fatalf("active children at limit 2 = %d, want exactly 2 before release", n)
	}
	if err := os.WriteFile(filepath.Join(dir, "release"), []byte("go"), 0o600); err != nil {
		t.Fatalf("release commands: %v", err)
	}

	var got []Result
	select {
	case got = <-done:
	case <-time.After(4 * time.Second):
		t.Fatal("Run did not finish after releasing gated commands")
	}
	requireResultCount(t, got, len(commands))
	for i, result := range got {
		if result.Err != nil || result.ExitCode != 0 {
			t.Fatalf("result[%d] after gate: code=%d err=%v stderr=%q", i, result.ExitCode, result.Err, result.Stderr)
		}
	}
	if n := readyCount(); n != len(commands) {
		t.Fatalf("eventually launched %d commands, want %d", n, len(commands))
	}
}

func TestCancellationStopsActiveAndQueuedCommands(t *testing.T) {
	dir := t.TempDir()
	activeReady := filepath.Join(dir, "active-ready")
	queuedMarker := filepath.Join(dir, "queued-launched")
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	commands := []Command{
		helperCommand(t, "block", activeReady),
		helperCommand(t, "mark", queuedMarker),
	}

	done := make(chan []Result, 1)
	go func() {
		done <- Run(ctx, 1, commands)
	}()
	waitFor(t, 3*time.Second, "the active command to start", func() bool {
		_, err := os.Stat(activeReady)
		return err == nil
	})

	cancelledAt := time.Now()
	cancel()
	var got []Result
	select {
	case got = <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("Run did not return after cancellation")
	}
	if elapsed := time.Since(cancelledAt); elapsed >= time.Second {
		t.Fatalf("cancellation took %v; active child was not terminated promptly", elapsed)
	}
	if _, err := os.Stat(queuedMarker); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("queued command was launched after cancellation: stat error %v", err)
	}
	requireResultCount(t, got, 2)
	for i, result := range got {
		if result.ExitCode != -1 {
			t.Errorf("canceled result[%d].ExitCode = %d, want -1", i, result.ExitCode)
		}
		if !errors.Is(result.Err, context.Canceled) {
			t.Errorf("canceled result[%d].Err = %v, want context.Canceled", i, result.Err)
		}
	}
	if got[0].Stdout != "active-stdout\n" || got[0].Stderr != "active-stderr\n" {
		t.Errorf("active command streams after cancellation = stdout %q stderr %q", got[0].Stdout, got[0].Stderr)
	}
	if got[1].Stdout != "" || got[1].Stderr != "" {
		t.Errorf("unstarted command has streams: stdout %q stderr %q", got[1].Stdout, got[1].Stderr)
	}
}

func TestAlreadyCanceledContextLaunchesNothing(t *testing.T) {
	dir := t.TempDir()
	ctx, cancel := context.WithCancel(t.Context())
	cancel()
	got := Run(ctx, 3, []Command{
		helperCommand(t, "mark", filepath.Join(dir, "one")),
		helperCommand(t, "mark", filepath.Join(dir, "two")),
	})
	requireResultCount(t, got, 2)
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("read marker directory: %v", err)
	}
	if len(entries) != 0 {
		t.Fatalf("commands launched with an already canceled context: %v", entries)
	}
	for i, result := range got {
		if result.ExitCode != -1 || !errors.Is(result.Err, context.Canceled) {
			t.Errorf("result[%d] after pre-cancel = {code:%d err:%v}, want {-1, context.Canceled}", i, result.ExitCode, result.Err)
		}
	}
}

func TestStreamsAndExactExitCodesStayAttributed(t *testing.T) {
	got := Run(t.Context(), 3, []Command{
		helperCommand(t, "emit", "alpha"),
		helperCommand(t, "fail", "23", "bravo"),
		helperCommand(t, "fail", "47", "charlie"),
	})
	requireResultCount(t, got, 3)

	if got[0].Stdout != "stdout-alpha\n" || got[0].Stderr != "stderr-alpha\n" || got[0].ExitCode != 0 || got[0].Err != nil {
		t.Errorf("successful result lost stream attribution: %+v", got[0])
	}
	for _, check := range []struct {
		index int
		code  int
		tag   string
	}{
		{index: 1, code: 23, tag: "bravo"},
		{index: 2, code: 47, tag: "charlie"},
	} {
		result := got[check.index]
		if result.Stdout != "before-"+check.tag+"\n" {
			t.Errorf("result[%d].Stdout = %q", check.index, result.Stdout)
		}
		if result.Stderr != "failure-"+check.tag+"\n" {
			t.Errorf("result[%d].Stderr = %q", check.index, result.Stderr)
		}
		if result.ExitCode != check.code {
			t.Errorf("result[%d].ExitCode = %d, want %d", check.index, result.ExitCode, check.code)
		}
		var exitErr *exec.ExitError
		if !errors.As(result.Err, &exitErr) {
			t.Errorf("result[%d].Err = %T %v, want *exec.ExitError", check.index, result.Err, result.Err)
		}
	}
}

func TestLaunchFailureUsesMinusOneAndRetainsError(t *testing.T) {
	missing := filepath.Join(t.TempDir(), "missing-command")
	got := Run(t.Context(), 1, []Command{{Path: missing, Args: []string{"literal arg"}}})
	requireResultCount(t, got, 1)
	if got[0].ExitCode != -1 {
		t.Fatalf("launch failure exit code = %d, want -1", got[0].ExitCode)
	}
	if got[0].Err == nil {
		t.Fatal("launch failure discarded its error")
	}
	var exitErr *exec.ExitError
	if errors.As(got[0].Err, &exitErr) {
		t.Fatalf("launch failure incorrectly reported as an exited process: %v", got[0].Err)
	}
	if got[0].Stdout != "" || got[0].Stderr != "" {
		t.Fatalf("launch failure streams = stdout %q stderr %q, want empty", got[0].Stdout, got[0].Stderr)
	}
}

func TestEmptyBatchAndNonPositiveLimit(t *testing.T) {
	empty := Run(t.Context(), 0, nil)
	if empty == nil || len(empty) != 0 {
		t.Fatalf("empty batch = %#v, want non-nil empty slice", empty)
	}
	got := Run(t.Context(), -4, []Command{helperCommand(t, "emit", "one")})
	requireResultCount(t, got, 1)
	if got[0].Err != nil || got[0].ExitCode != 0 {
		t.Fatalf("non-positive limit did not behave as one: %+v", got[0])
	}
}

func TestProductionUsesContextAwareDirectExec(t *testing.T) {
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatalf("read package directory: %v", err)
	}
	var files []string
	for _, entry := range entries {
		if !entry.IsDir() && strings.HasSuffix(entry.Name(), ".go") && !strings.HasSuffix(entry.Name(), "_test.go") {
			files = append(files, entry.Name())
		}
	}
	sort.Strings(files)

	directCalls := 0
	for _, name := range files {
		fset := token.NewFileSet()
		parsed, err := parser.ParseFile(fset, name, nil, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", name, err)
		}
		execAliases := map[string]bool{}
		for _, imp := range parsed.Imports {
			path, err := strconv.Unquote(imp.Path.Value)
			if err != nil || path != "os/exec" {
				continue
			}
			alias := "exec"
			if imp.Name != nil {
				alias = imp.Name.Name
			}
			if alias == "." || alias == "_" {
				t.Fatalf("%s: os/exec must use a named import so the process boundary remains reviewable", name)
			}
			execAliases[alias] = true
		}
		ast.Inspect(parsed, func(node ast.Node) bool {
			call, ok := node.(*ast.CallExpr)
			if !ok {
				return true
			}
			selector, ok := call.Fun.(*ast.SelectorExpr)
			if !ok {
				return true
			}
			ident, ok := selector.X.(*ast.Ident)
			if !ok || !execAliases[ident.Name] {
				return true
			}
			switch selector.Sel.Name {
			case "Command":
				t.Errorf("%s:%d uses exec.Command; commands must be tied to the supplied context", name, fset.Position(call.Pos()).Line)
			case "CommandContext":
				directCalls++
				if len(call.Args) < 3 || !call.Ellipsis.IsValid() {
					t.Errorf("%s:%d does not pass a per-command argument vector to exec.CommandContext", name, fset.Position(call.Pos()).Line)
				}
			}
			return true
		})
	}
	if directCalls == 0 {
		t.Fatal("production code has no direct exec.CommandContext argv boundary")
	}
}
