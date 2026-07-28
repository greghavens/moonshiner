package jobs

import (
	"context"
	"errors"
	"sync"

	"example.com/correlation-boundary/internal/correlation"
	"example.com/correlation-boundary/internal/telemetry"
)

type Job struct {
	Account       string
	CorrelationID string
}

type Enqueuer interface {
	Enqueue(Job) error
}

// MemoryQueue is a concurrency-safe queue used by the local service.
type MemoryQueue struct {
	mu   sync.Mutex
	jobs []Job
}

func (q *MemoryQueue) Enqueue(job Job) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.jobs = append(q.jobs, job)
	return nil
}

func (q *MemoryQueue) Jobs() []Job {
	q.mu.Lock()
	defer q.mu.Unlock()
	return append([]Job(nil), q.jobs...)
}

type Runner func(context.Context, Job) error

type Worker struct {
	Source correlation.Source
	Log    *telemetry.Recorder
}

var ErrNilRunner = errors.New("job runner is nil")

func (w Worker) Process(ctx context.Context, job Job, run Runner) error {
	if run == nil {
		return ErrNilRunner
	}

	ctx = correlation.WithID(ctx, correlation.FromContext(ctx))
	w.Log.Record(ctx, "export.started", job.Account)
	err := run(ctx, job)
	if err != nil {
		w.Log.Record(ctx, "export.failed", job.Account)
		return err
	}
	w.Log.Record(ctx, "export.completed", job.Account)
	return nil
}
