// Package httpapi adapts authenticated requests to account commands.
package httpapi

import (
	"context"

	"go-audit-ownership/internal/account"
	"go-audit-ownership/internal/audit"
	"go-audit-ownership/internal/requestctx"
)

type Handler struct {
	commands account.Commands
	audit    audit.Sink
}

// NewHandler retains the deployed wiring signature.
func NewHandler(commands account.Commands, sink audit.Sink) *Handler {
	return &Handler{commands: commands, audit: sink}
}

func (h *Handler) ChangeEmail(ctx context.Context, accountID, email string) error {
	if err := h.commands.ChangeEmail(ctx, accountID, email); err != nil {
		return err
	}
	return h.audit.Record(ctx, audit.Event{
		Action:    "account.email_changed",
		AccountID: accountID,
		Actor:     requestctx.Actor(ctx),
		RequestID: requestctx.RequestID(ctx),
		Details:   map[string]string{"email": email},
	})
}

func (h *Handler) Deactivate(ctx context.Context, accountID string) error {
	if err := h.commands.Deactivate(ctx, accountID); err != nil {
		return err
	}
	return h.audit.Record(ctx, audit.Event{
		Action:    "account.disabled",
		AccountID: accountID,
		Actor:     "system",
		Details:   map[string]string{},
	})
}
