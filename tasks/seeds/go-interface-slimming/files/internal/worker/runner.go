package worker

import (
	"context"
	"sync"

	"example.com/go-interface-slimming/internal/dispatch"
)

type Runner struct {
	queue       dispatch.Service
	parallelism int
}

func New(queue dispatch.Service, parallelism int) *Runner {
	if parallelism < 1 {
		parallelism = 1
	}
	return &Runner{queue: queue, parallelism: parallelism}
}

func (r *Runner) Run(ctx context.Context, limit int) error {
	ready, err := r.queue.ListReady(ctx, limit)
	if err != nil {
		return err
	}
	if len(ready) == 0 {
		return ctx.Err()
	}

	workCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	jobs := make(chan dispatch.Run)
	var workers sync.WaitGroup
	var first sync.Once
	var firstErr error

	count := r.parallelism
	if count > len(ready) {
		count = len(ready)
	}
	workers.Add(count)
	for i := 0; i < count; i++ {
		go func() {
			defer workers.Done()
			for {
				select {
				case <-workCtx.Done():
					return
				case run, ok := <-jobs:
					if !ok {
						return
					}
					if err := r.queue.MarkDispatched(workCtx, run.ID); err != nil {
						first.Do(func() {
							firstErr = err
							cancel()
						})
						return
					}
				}
			}
		}()
	}

feed:
	for _, run := range ready {
		select {
		case jobs <- run:
		case <-workCtx.Done():
			break feed
		}
	}
	close(jobs)
	workers.Wait()

	if firstErr != nil {
		return firstErr
	}
	return ctx.Err()
}
