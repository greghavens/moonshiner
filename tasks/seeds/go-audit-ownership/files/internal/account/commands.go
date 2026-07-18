package account

import "context"

// Commands is consumed by HTTP handlers, jobs, and generated mocks.
type Commands interface {
	ChangeEmail(context.Context, string, string) error
	Deactivate(context.Context, string) error
}

// Store owns the persisted account mutation.
type Store interface {
	ChangeEmail(context.Context, string, string) error
	Deactivate(context.Context, string) error
}
