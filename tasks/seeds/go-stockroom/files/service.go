// Package stockroom wires the warehouse service together: HTTP API,
// store, and the scheduled jobs.
package stockroom

import (
	"net/http"

	"go-stockroom/api"
	"go-stockroom/jobs"
	"go-stockroom/store"
)

type Service struct {
	cfg     Config
	store   *store.Store
	handler http.Handler
	sched   *jobs.Scheduler
}

// New builds a service. The clock is injected so scheduled work runs at
// logical times under test and wall-clock time in production.
func New(cfg Config, clock jobs.Clock) *Service {
	cfg = cfg.withDefaults()
	st := store.New()
	return &Service{
		cfg:     cfg,
		store:   st,
		handler: api.NewServer(st),
		sched:   jobs.NewScheduler(clock, cfg.RetryDelay),
	}
}

// Handler is the HTTP surface of the service.
func (s *Service) Handler() http.Handler { return s.handler }

// Scheduler exposes the job queue; the run loop calls Tick on it.
func (s *Service) Scheduler() *jobs.Scheduler { return s.sched }

// ScheduleReconciliation queues the nightly reconciliation job for every
// warehouse known right now. Call it after the warehouses are created.
func (s *Service) ScheduleReconciliation() {
	for _, warehouse := range s.store.Warehouses() {
		s.sched.Add(jobs.NewReconcileJob(s.store, warehouse, s.cfg.ReconcileEvery))
	}
}
