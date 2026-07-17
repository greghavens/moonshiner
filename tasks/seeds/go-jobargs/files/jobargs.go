// Package jobargs builds the command lines the CI runner uses to launch
// sandboxed build jobs. Every job shares the same base invocation (runtime
// binary plus global flags); per-job flags are appended on top.
package jobargs

import (
	"fmt"
	"strings"
)

// Launcher knows the base invocation for one runner host.
type Launcher struct {
	baseArgs []string
}

// NewLauncher prepares the shared part of every job command line.
func NewLauncher(runtime string, sandboxed bool, cpuLimit int) *Launcher {
	args := make([]string, 0, 8) // typical command line is well under 8 tokens
	args = append(args, runtime, "--quiet")
	if sandboxed {
		args = append(args, "--sandbox")
	}
	if cpuLimit > 0 {
		args = append(args, fmt.Sprintf("--cpus=%d", cpuLimit))
	}
	return &Launcher{baseArgs: args}
}

// Command returns the full argv for one job: the launcher's base invocation,
// the job selector, then any job-specific flags.
func (l *Launcher) Command(job string, extra ...string) []string {
	argv := append(l.baseArgs, "--job", job)
	argv = append(argv, extra...)
	return argv
}

// CommandLine renders the argv the way it appears in the launch log.
func (l *Launcher) CommandLine(job string, extra ...string) string {
	return strings.Join(l.Command(job, extra...), " ")
}
