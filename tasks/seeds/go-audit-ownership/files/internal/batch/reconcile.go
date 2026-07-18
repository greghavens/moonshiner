// Package batch contains the non-HTTP reconciliation caller.
package batch

import (
	"context"

	"go-audit-ownership/internal/account"
	"go-audit-ownership/internal/requestctx"
)

func DeactivateDormant(ctx context.Context, commands account.Commands, accountID, runID string) error {
	ctx = requestctx.WithIdentity(ctx, "reconciliation-job", runID)
	return commands.Deactivate(ctx, accountID)
}
