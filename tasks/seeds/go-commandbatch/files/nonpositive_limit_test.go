package commandbatch

import (
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"
)

func TestNonPositiveLimitAllowsOnlyOneActiveChild(t *testing.T) {
	for _, limit := range []int{0, -4} {
		t.Run(strconv.Itoa(limit), func(t *testing.T) {
			dir := t.TempDir()
			commands := make([]Command, 3)
			for i := range commands {
				commands[i] = helperCommand(t, "gate", dir, strconv.Itoa(i))
			}

			done := make(chan []Result, 1)
			go func() {
				done <- Run(t.Context(), limit, commands)
			}()

			readyCount := func() int {
				matches, err := filepath.Glob(filepath.Join(dir, "ready-*"))
				if err != nil {
					t.Fatalf("glob ready files: %v", err)
				}
				return len(matches)
			}
			waitFor(t, 3*time.Second, "one command to start", func() bool {
				return readyCount() >= 1
			})
			time.Sleep(250 * time.Millisecond)
			if count := readyCount(); count != 1 {
				t.Fatalf("active children with limit %d = %d, want exactly 1 before release", limit, count)
			}

			if err := os.WriteFile(filepath.Join(dir, "release"), []byte("go"), 0o600); err != nil {
				t.Fatalf("release commands: %v", err)
			}

			select {
			case results := <-done:
				requireResultCount(t, results, len(commands))
				for i, result := range results {
					if result.Err != nil || result.ExitCode != 0 {
						t.Fatalf("result[%d] with limit %d: code=%d err=%v stderr=%q", i, limit, result.ExitCode, result.Err, result.Stderr)
					}
				}
			case <-time.After(4 * time.Second):
				t.Fatalf("Run with limit %d did not finish after release", limit)
			}
		})
	}
}
