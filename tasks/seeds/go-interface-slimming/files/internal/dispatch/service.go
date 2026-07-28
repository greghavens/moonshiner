package dispatch

import (
	"context"
	"errors"
	"sort"
	"sync"
)

var (
	ErrNotFound = errors.New("run not found")
	ErrConflict = errors.New("run state conflict")
)

type Run struct {
	ID     string `json:"id"`
	Tenant string `json:"tenant"`
	State  string `json:"state"`
}

// Service grew with the provider. Consumers should not have to implement
// administrative methods they never invoke.
type Service interface {
	GetRun(context.Context, string) (Run, error)
	CancelRun(context.Context, string) error
	ListReady(context.Context, int) ([]Run, error)
	MarkDispatched(context.Context, string) error
	RotateSigningKey(context.Context, string) error
	PurgeTenant(context.Context, string) error
}

type MemoryService struct {
	mu   sync.RWMutex
	runs map[string]Run
	keys map[string]uint64
}

func NewMemoryService(initial []Run) *MemoryService {
	service := &MemoryService{
		runs: make(map[string]Run, len(initial)),
		keys: make(map[string]uint64),
	}
	for _, run := range initial {
		service.runs[run.ID] = run
	}
	return service
}

func (s *MemoryService) GetRun(ctx context.Context, id string) (Run, error) {
	if err := ctx.Err(); err != nil {
		return Run{}, err
	}
	s.mu.RLock()
	defer s.mu.RUnlock()
	run, ok := s.runs[id]
	if !ok {
		return Run{}, ErrNotFound
	}
	return run, nil
}

func (s *MemoryService) CancelRun(ctx context.Context, id string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	run, ok := s.runs[id]
	if !ok {
		return ErrNotFound
	}
	if run.State == "dispatched" {
		return ErrConflict
	}
	run.State = "cancelled"
	s.runs[id] = run
	return nil
}

func (s *MemoryService) ListReady(ctx context.Context, limit int) ([]Run, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	s.mu.RLock()
	defer s.mu.RUnlock()
	ids := make([]string, 0, len(s.runs))
	for id, run := range s.runs {
		if run.State == "ready" {
			ids = append(ids, id)
		}
	}
	sort.Strings(ids)
	if limit > 0 && len(ids) > limit {
		ids = ids[:limit]
	}
	ready := make([]Run, 0, len(ids))
	for _, id := range ids {
		ready = append(ready, s.runs[id])
	}
	return ready, nil
}

func (s *MemoryService) MarkDispatched(ctx context.Context, id string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	run, ok := s.runs[id]
	if !ok {
		return ErrNotFound
	}
	if run.State != "ready" {
		return ErrConflict
	}
	run.State = "dispatched"
	s.runs[id] = run
	return nil
}

func (s *MemoryService) RotateSigningKey(ctx context.Context, tenant string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.keys[tenant]++
	return nil
}

func (s *MemoryService) PurgeTenant(ctx context.Context, tenant string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	for id, run := range s.runs {
		if run.Tenant == tenant {
			delete(s.runs, id)
		}
	}
	delete(s.keys, tenant)
	return nil
}
