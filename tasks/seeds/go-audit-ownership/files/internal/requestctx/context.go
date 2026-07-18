// Package requestctx carries authenticated request identity through adapters.
package requestctx

import "context"

type key int

const (
	actorKey key = iota
	requestIDKey
)

// WithIdentity is the middleware seam used by HTTP and scheduled callers.
func WithIdentity(ctx context.Context, actor, requestID string) context.Context {
	ctx = context.WithValue(ctx, actorKey, actor)
	return context.WithValue(ctx, requestIDKey, requestID)
}

func Actor(ctx context.Context) string {
	actor, _ := ctx.Value(actorKey).(string)
	return actor
}

func RequestID(ctx context.Context) string {
	id, _ := ctx.Value(requestIDKey).(string)
	return id
}

// Middleware models the real request wrapper without a live HTTP server.
func Middleware(actor, requestID string, next func(context.Context) error) func(context.Context) error {
	return func(ctx context.Context) error {
		return next(WithIdentity(ctx, actor, requestID))
	}
}
