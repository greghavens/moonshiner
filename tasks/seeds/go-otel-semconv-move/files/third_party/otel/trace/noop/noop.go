// Package noop supplies a tracer provider that records nothing.
package noop

import (
	"context"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"
)

// TracerProvider creates no-op tracers.
type TracerProvider struct{}

// NewTracerProvider creates a no-op tracer provider.
func NewTracerProvider() TracerProvider { return TracerProvider{} }

// Tracer returns a no-op tracer. Its name is accepted for API compatibility.
func (TracerProvider) Tracer(string) trace.Tracer { return tracer{} }

type tracer struct{}

func (tracer) Start(ctx context.Context, _ string, _ ...trace.SpanStartOption) (context.Context, trace.Span) {
	span := span{}
	return trace.ContextWithSpan(ctx, span), span
}

type span struct{}

func (span) End()                                {}
func (span) IsRecording() bool                   { return false }
func (span) SetAttributes(...attribute.KeyValue) {}
func (span) SetStatus(codes.Code, string)        {}
func (span) SpanContext() trace.SpanContext      { return trace.SpanContext{} }
