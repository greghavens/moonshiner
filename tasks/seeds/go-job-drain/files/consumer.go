// Package consumer drains the export queue and hands each job to a sink.
// One consumer runs per tenant inside the exporter process; the process
// manager cancels their contexts when it wants a clean shutdown, then
// waits for every Run to return before exiting.
package consumer

import "context"

// Job is one export request pulled off the tenant's queue.
type Job struct {
	ID      int
	Payload string
}

// Sink receives completed jobs (writes rows, uploads files, etc.).
type Sink func(Job)

// Run processes jobs from the queue until the queue is closed (returns
// nil) or ctx is cancelled (returns ctx.Err()). It reports how many jobs
// were handed to the sink.
func Run(ctx context.Context, jobs <-chan Job, sink Sink) (int, error) {
	processed := 0
	for job := range jobs {
		sink(job)
		processed++
	}
	if err := ctx.Err(); err != nil {
		return processed, err
	}
	return processed, nil
}
