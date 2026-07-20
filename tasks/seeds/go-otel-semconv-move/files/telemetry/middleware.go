// Package telemetry instruments HTTP-style handlers with OpenTelemetry spans.
package telemetry

import (
	"context"
	"net/http"

	"go.opentelemetry.io/otel/codes"
	semconv "go.opentelemetry.io/otel/semconv/v1.17.0"
	"go.opentelemetry.io/otel/semconv/v1.17.0/httpconv"
	"go.opentelemetry.io/otel/trace"
)

// Handler is the operation wrapped by Middleware. It returns the HTTP status
// that will be sent to the caller.
type Handler func(context.Context, *http.Request) (int, error)

// Middleware creates server spans for handlers.
type Middleware struct {
	tracer     trace.Tracer
	serverName string
}

// NewMiddleware constructs HTTP server instrumentation.
func NewMiddleware(tracer trace.Tracer, serverName string) Middleware {
	return Middleware{tracer: tracer, serverName: serverName}
}

// Instrument invokes next in a server span. The route must be the low-cardinality
// route template, rather than the raw request path.
func (m Middleware) Instrument(
	ctx context.Context,
	route string,
	req *http.Request,
	next Handler,
) (int, error) {
	ctx, span := m.tracer.Start(ctx, spanName(req.Method, route))
	defer span.End()

	if span.IsRecording() {
		attributes := httpconv.ServerRequest(m.serverName, req)
		attributes = append(attributes, semconv.HTTPRouteKey.String(route))
		span.SetAttributes(attributes...)
	}

	status, err := next(ctx, req)
	if !span.IsRecording() {
		return status, err
	}

	span.SetAttributes(semconv.HTTPStatusCodeKey.Int(status))
	code, description := httpconv.ServerStatus(status)
	span.SetStatus(code, description)
	if err != nil {
		span.SetStatus(codes.Error, err.Error())
	}

	return status, err
}

func spanName(method, route string) string {
	if route == "" {
		return method
	}
	return method + " " + route
}
