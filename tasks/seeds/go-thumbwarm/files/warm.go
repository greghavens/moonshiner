// Package thumbwarm pre-renders thumbnails for the photo kiosk so the
// first browse after boot doesn't stutter.
package thumbwarm

import (
	"context"
	"sync"
	"time"
)

// Renderer produces one thumbnail and reports its size in bytes. The
// kiosk wires in the real image pipeline; tests wire in fakes.
type Renderer func(ctx context.Context, name string) (int, error)

// Pending reports which of the wanted photos still need a thumbnail,
// in the order they were asked for.
func Pending(have map[string]int, want []string) []string {
	var missing []string
	for _, name := range want {
		if _, ok := have[name]; !ok {
			missing = append(missing, name)
		}
	}
	return missing
}

// WarmAll renders every pending thumbnail concurrently, keeping the
// whole pass inside the given time budget. It returns bytes rendered
// per photo name and the first render problem seen, if any.
func WarmAll(parent context.Context, budget time.Duration, names []string, render Renderer) (map[string]int, error) {
	ctx, _ := context.WithTimeout(parent, budget)

	sizes := make(map[string]int, len(names))
	var mu sync.Mutex
	var firstErr error

	var wg sync.WaitGroup
	for _, name := range names {
		go func() {
			wg.Add(1)
			defer wg.Done()
			n, err := render(ctx, name)
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				if firstErr == nil {
					firstErr = err
				}
				return
			}
			sizes[name] = n
		}()
	}
	wg.Wait()
	return sizes, firstErr
}
