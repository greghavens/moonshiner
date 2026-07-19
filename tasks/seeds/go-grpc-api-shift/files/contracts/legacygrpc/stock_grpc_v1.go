// Package legacygrpc is a protected snapshot of the pre-regeneration stream shape.
package legacygrpc

import (
	"context"

	"go-grpc-api-shift/internal/generated/stockpb"
)

type Stock_WatchServer interface {
	Context() context.Context
	Send(*stockpb.StockUpdate) error
}

