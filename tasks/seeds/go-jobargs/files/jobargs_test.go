package jobargs

import (
	"reflect"
	"testing"
)

func TestSingleJobCommand(t *testing.T) {
	l := NewLauncher("runc", true, 2)
	got := l.Command("compile-api")
	want := []string{"runc", "--quiet", "--sandbox", "--cpus=2", "--job", "compile-api"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Command(compile-api) = %v, want %v", got, want)
	}
}

func TestQueuedJobsKeepTheirOwnArgs(t *testing.T) {
	l := NewLauncher("runc", true, 0)
	first := l.Command("compile-api", "--cache=warm")
	second := l.Command("compile-web")

	wantFirst := []string{"runc", "--quiet", "--sandbox", "--job", "compile-api", "--cache=warm"}
	if !reflect.DeepEqual(first, wantFirst) {
		t.Fatalf("after preparing a second job, first argv = %v, want %v", first, wantFirst)
	}
	wantSecond := []string{"runc", "--quiet", "--sandbox", "--job", "compile-web"}
	if !reflect.DeepEqual(second, wantSecond) {
		t.Fatalf("second argv = %v, want %v", second, wantSecond)
	}
}

func TestManyJobsDoNotInterfere(t *testing.T) {
	l := NewLauncher("firecracker", false, 4)
	jobs := []string{"lint", "unit", "integration", "package"}
	argvs := make([][]string, len(jobs))
	for i, j := range jobs {
		argvs[i] = l.Command(j)
	}
	for i, j := range jobs {
		want := []string{"firecracker", "--quiet", "--cpus=4", "--job", j}
		if !reflect.DeepEqual(argvs[i], want) {
			t.Fatalf("argv for %q = %v, want %v", j, argvs[i], want)
		}
	}
}

func TestCommandLineRendering(t *testing.T) {
	l := NewLauncher("runc", false, 0)
	got := l.CommandLine("deploy", "--env=staging")
	want := "runc --quiet --job deploy --env=staging"
	if got != want {
		t.Fatalf("CommandLine = %q, want %q", got, want)
	}
}
