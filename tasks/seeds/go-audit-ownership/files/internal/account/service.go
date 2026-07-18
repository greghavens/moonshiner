package account

import (
	"context"

	"go-audit-ownership/internal/audit"
)

// Service is the command ownership boundary shared by every adapter.
type Service struct {
	store Store
	audit audit.Sink
}

func NewService(store Store, sink audit.Sink) *Service {
	return &Service{store: store, audit: sink}
}

func (s *Service) ChangeEmail(ctx context.Context, accountID, email string) error {
	if err := s.store.ChangeEmail(ctx, accountID, email); err != nil {
		return err
	}
	return nil
}

func (s *Service) Deactivate(ctx context.Context, accountID string) error {
	if err := s.store.Deactivate(ctx, accountID); err != nil {
		return err
	}
	return nil
}
