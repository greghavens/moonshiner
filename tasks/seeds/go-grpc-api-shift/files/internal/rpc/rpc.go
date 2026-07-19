// Package rpc is the protected offline runtime surface used by generated code.
package rpc

import (
	"context"
	"errors"
	"fmt"
)

type Code string

const (
	CodeCanceled    Code = "Canceled"
	CodeNotFound    Code = "NotFound"
	CodeInternal    Code = "Internal"
	CodeUnavailable Code = "Unavailable"
	CodeUnimplemented Code = "Unimplemented"
)

type StatusError struct {
	Code    Code
	Message string
	Cause   error
}

func (e *StatusError) Error() string {
	return fmt.Sprintf("rpc %s: %s", e.Code, e.Message)
}

func (e *StatusError) Unwrap() error { return e.Cause }

func Status(code Code, message string, cause error) error {
	return &StatusError{Code: code, Message: message, Cause: cause}
}

func CodeOf(err error) Code {
	if err == nil {
		return ""
	}
	var status *StatusError
	if errors.As(err, &status) {
		return status.Code
	}
	if errors.Is(err, context.Canceled) {
		return CodeCanceled
	}
	return CodeInternal
}

type UnaryHandler func(context.Context, any) (any, error)

type UnaryServerInfo struct {
	FullMethod string
}

type UnaryServerInterceptor func(
	context.Context,
	any,
	UnaryServerInfo,
	UnaryHandler,
) (any, error)

type metadataKey struct{}

func WithActor(ctx context.Context, actor string) context.Context {
	return context.WithValue(ctx, metadataKey{}, actor)
}

func Actor(ctx context.Context) string {
	actor, _ := ctx.Value(metadataKey{}).(string)
	return actor
}

