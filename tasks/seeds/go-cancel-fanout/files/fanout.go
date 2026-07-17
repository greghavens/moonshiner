// Package fanout races a search query across replicated document
// backends and reports the first hit.
package fanout

import (
	"context"
	"errors"
	"sync"
)

// Backend queries one replica. Implementations must honor ctx
// cancellation and return promptly once ctx is done.
type Backend func(ctx context.Context, query string) (string, error)

// Outcome is one backend's report.
type Outcome struct {
	Doc string
	Err error
}

// Search fans query out to every backend and returns the first
// document found. When every backend fails, the joined failures come
// back as one error; when ctx is cancelled first, ctx's error does.
// The returned wait function blocks until every backend goroutine has
// finished — callers use it to bound the request's resource lifetime,
// and it must always return once the backends respect cancellation.
func Search(ctx context.Context, backends []Backend, query string) (string, func(), error) {
	results := make(chan Outcome)
	var wg sync.WaitGroup
	for _, b := range backends {
		wg.Add(1)
		go func(b Backend) {
			defer wg.Done()
			doc, err := b(ctx, query)
			results <- Outcome{Doc: doc, Err: err}
		}(b)
	}
	wait := wg.Wait
	var errs []error
	for range backends {
		select {
		case r := <-results:
			if r.Err == nil {
				return r.Doc, wait, nil
			}
			errs = append(errs, r.Err)
		case <-ctx.Done():
			return "", wait, ctx.Err()
		}
	}
	return "", wait, errors.Join(errs...)
}

// Collect streams every backend's outcome and closes the channel once
// all backends have reported. The channel is owned and closed here;
// callers only receive.
func Collect(ctx context.Context, backends []Backend, query string) <-chan Outcome {
	out := make(chan Outcome)
	var wg sync.WaitGroup
	for _, b := range backends {
		wg.Add(1)
		go func(b Backend) {
			defer wg.Done()
			doc, err := b(ctx, query)
			out <- Outcome{Doc: doc, Err: err}
		}(b)
	}
	go func() {
		wg.Wait()
		close(out)
	}()
	return out
}
