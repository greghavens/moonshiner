// Package trace defines the minimal tracing interfaces needed by the example.
package trace

import (
	"context"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
)

// SpanContext identifies a trace and span.
type SpanContext struct {
	TraceID string
	SpanID  string
}

// IsValid reports whether the context identifies a real span.
func (s SpanContext) IsValid() bool { return s.TraceID != "" && s.SpanID != "" }

// Span is the portion of the OpenTelemetry span API used by the middleware.
type Span interface {
	End()
	IsRecording() bool
	SetAttributes(...attribute.KeyValue)
	SetStatus(codes.Code, string)
	SpanContext() SpanContext
}

// SpanStartOption is reserved for API compatibility.
type SpanStartOption interface{ spanStartOption() }

// Tracer starts spans and installs them in returned contexts.
type Tracer interface {
	Start(context.Context, string, ...SpanStartOption) (context.Context, Span)
}

type spanContextKey struct{}

// ContextWithSpan returns a context carrying span.
func ContextWithSpan(ctx context.Context, span Span) context.Context {
	return context.WithValue(ctx, spanContextKey{}, span)
}

// SpanFromContext returns the current span, or a non-recording invalid span.
func SpanFromContext(ctx context.Context) Span {
	if span, ok := ctx.Value(spanContextKey{}).(Span); ok {
		return span
	}
	return invalidSpan{}
}

type invalidSpan struct{}

func (invalidSpan) End()                                  {}
func (invalidSpan) IsRecording() bool                     { return false }
func (invalidSpan) SetAttributes(...attribute.KeyValue)   {}
func (invalidSpan) SetStatus(codes.Code, string)          {}
func (invalidSpan) SpanContext() SpanContext              { return SpanContext{} }
