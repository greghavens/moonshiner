// Package commandbatch runs a bounded group of native commands.
package commandbatch

import (
	"bytes"
	"context"
	"os/exec"
	"strings"
	"sync"
)

// Command is an executable path and its already-tokenized argument vector.
type Command struct {
	Path string
	Args []string
}

// Result is the complete outcome for one Command.
type Result struct {
	Stdout   string
	Stderr   string
	ExitCode int
	Err      error
}

// Run executes commands with at most maxParallel children active at once.
// Results are documented to correspond to commands by index.
func Run(ctx context.Context, maxParallel int, commands []Command) []Result {
	if maxParallel < 1 {
		maxParallel = 1
	}

	results := make([]Result, 0, len(commands))
	slots := make(chan struct{}, maxParallel)
	var wg sync.WaitGroup

	for _, command := range commands {
		wg.Add(1)
		go func(spec Command) {
			defer wg.Done()

			select {
			case slots <- struct{}{}:
				defer func() { <-slots }()
			case <-ctx.Done():
				return
			}

			parts := append([]string{spec.Path}, spec.Args...)
			cmd := exec.Command("sh", "-c", strings.Join(parts, " "))
			var output bytes.Buffer
			cmd.Stdout = &output
			cmd.Stderr = &output
			err := cmd.Run()

			exitCode := 0
			if err != nil {
				exitCode = 1
			}
			results = append(results, Result{
				Stdout:   output.String(),
				ExitCode: exitCode,
				Err:      err,
			})
		}(command)
	}

	wg.Wait()
	return results
}
