package stockserver

import (
	"context"
	"errors"

	"go-grpc-api-shift/contracts/legacygrpc"
	"go-grpc-api-shift/internal/generated/stockpb"
	"go-grpc-api-shift/internal/rpc"
)

var ErrMissing = errors.New("stock record missing")

type Record struct {
	SKU       string
	Quantity  int32
	Warehouse string
}

type Repository interface {
	Lookup(context.Context, string) (Record, error)
	Snapshot(context.Context, string) ([]int32, error)
}

type Server struct {
	repository Repository
}

func New(repository Repository) *Server {
	return &Server{repository: repository}
}

func (s *Server) Lookup(
	ctx context.Context,
	request *stockpb.LookupRequest,
) (*stockpb.LookupResponse, error) {
	if err := ctx.Err(); err != nil {
		return nil, rpc.Status(rpc.CodeCanceled, "lookup canceled", err)
	}
	record, err := s.repository.Lookup(ctx, request.Sku)
	if contextError := ctx.Err(); contextError != nil {
		return nil, rpc.Status(rpc.CodeCanceled, "lookup canceled", contextError)
	}
	if errors.Is(err, context.Canceled) {
		return nil, rpc.Status(rpc.CodeCanceled, "lookup canceled", err)
	}
	if errors.Is(err, ErrMissing) {
		return nil, rpc.Status(rpc.CodeNotFound, "stock not found", err)
	}
	if err != nil {
		return nil, rpc.Status(rpc.CodeInternal, "stock repository failed", err)
	}
	return &stockpb.LookupResponse{
		Sku: record.SKU, Quantity: record.Quantity, Warehouse: record.Warehouse,
	}, nil
}

func (s *Server) Watch(
	request *stockpb.WatchRequest,
	stream legacygrpc.Stock_WatchServer,
) error {
	ctx := stream.Context()
	if err := ctx.Err(); err != nil {
		return rpc.Status(rpc.CodeCanceled, "watch canceled", err)
	}
	quantities, err := s.repository.Snapshot(ctx, request.Sku)
	if err != nil {
		if contextError := ctx.Err(); contextError != nil {
			return rpc.Status(rpc.CodeCanceled, "watch canceled", contextError)
		}
		return rpc.Status(rpc.CodeInternal, "stock snapshot failed", err)
	}
	for index, quantity := range quantities {
		if err := ctx.Err(); err != nil {
			return rpc.Status(rpc.CodeCanceled, "watch canceled", err)
		}
		if err := stream.Send(&stockpb.StockUpdate{
			Sku: request.Sku, Quantity: quantity, Sequence: int64(index + 1),
		}); err != nil {
			if contextError := ctx.Err(); contextError != nil {
				return rpc.Status(rpc.CodeCanceled, "watch canceled", contextError)
			}
			return err
		}
		if err := ctx.Err(); err != nil {
			return rpc.Status(rpc.CodeCanceled, "watch canceled", err)
		}
	}
	if err := ctx.Err(); err != nil {
		return rpc.Status(rpc.CodeCanceled, "watch canceled", err)
	}
	return nil
}
