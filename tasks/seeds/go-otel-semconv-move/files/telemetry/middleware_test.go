package telemetry

import (
	"context"
	"errors"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"net/http"
	"strconv"
	"testing"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"
	"go.opentelemetry.io/otel/trace/noop"
)

func TestMiddlewareUsesStableSemconvAPI(t *testing.T) {
	const stableImport = "go.opentelemetry.io/otel/semconv/v1.26.0"

	file, err := parser.ParseFile(token.NewFileSet(), "middleware.go", nil, 0)
	if err != nil {
		t.Fatalf("parse middleware.go: %v", err)
	}

	stableName := ""
	for _, spec := range file.Imports {
		path, err := strconv.Unquote(spec.Path.Value)
		if err != nil {
			t.Fatalf("unquote import %q: %v", spec.Path.Value, err)
		}
		if path == "go.opentelemetry.io/otel/semconv/v1.17.0" ||
			path == "go.opentelemetry.io/otel/semconv/v1.17.0/httpconv" {
			t.Errorf("middleware.go still imports legacy semantic-convention package %q", path)
		}
		if path == stableImport {
			stableName = "semconv"
			if spec.Name != nil {
				stableName = spec.Name.Name
			}
			if stableName == "_" || stableName == "." {
				t.Fatalf("stable semconv package must be imported normally, not as %q", stableName)
			}
		}
	}
	if stableName == "" {
		t.Fatalf("middleware.go does not import %q", stableImport)
	}

	used := make(map[string]bool)
	ast.Inspect(file, func(node ast.Node) bool {
		selector, ok := node.(*ast.SelectorExpr)
		if !ok {
			return true
		}
		packageName, ok := selector.X.(*ast.Ident)
		if ok && packageName.Name == stableName {
			used[selector.Sel.Name] = true
		}
		return true
	})
	for _, key := range []string{
		"HTTPRequestMethodKey",
		"HTTPRouteKey",
		"NetworkProtocolVersionKey",
		"URLSchemeKey",
		"ServerAddressKey",
		"HTTPResponseStatusCodeKey",
	} {
		if !used[key] {
			t.Errorf("middleware.go does not use semconv.%s", key)
		}
	}
}

func TestMiddlewareUsesStableHTTPAttributesAndKeepsChildRelationship(t *testing.T) {
	recorder := newRecordingTracer()
	middleware := NewMiddleware(recorder, "orders.internal")
	req, err := http.NewRequest(http.MethodPost, "https://api.example.test/widgets/42?verbose=true", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Proto = "HTTP/2.0"
	req.ProtoMajor = 2
	req.ProtoMinor = 0

	status, gotErr := middleware.Instrument(
		context.Background(),
		"/widgets/{id}",
		req,
		func(ctx context.Context, _ *http.Request) (int, error) {
			childCtx, child := recorder.Start(ctx, "inventory.lookup")
			if trace.SpanFromContext(childCtx) != child {
				t.Fatal("child span was not installed in its returned context")
			}
			child.End()
			return http.StatusServiceUnavailable, nil
		},
	)
	if gotErr != nil || status != http.StatusServiceUnavailable {
		t.Fatalf("Instrument() = (%d, %v), want (%d, nil)", status, gotErr, http.StatusServiceUnavailable)
	}

	server := recorder.spanNamed(t, "POST /widgets/{id}")
	child := recorder.spanNamed(t, "inventory.lookup")
	if child.parent.SpanID != server.context.SpanID {
		t.Fatalf("child parent span = %q, want %q", child.parent.SpanID, server.context.SpanID)
	}
	if child.context.TraceID != server.context.TraceID {
		t.Fatalf("child trace = %q, want server trace %q", child.context.TraceID, server.context.TraceID)
	}

	wantStrings := map[attribute.Key]string{
		"http.request.method":      "POST",
		"http.route":               "/widgets/{id}",
		"network.protocol.version": "2.0",
		"url.scheme":               "https",
		"server.address":           "orders.internal",
	}
	for key, want := range wantStrings {
		value, ok := server.attributes[key]
		if !ok {
			t.Errorf("server span missing %q; attributes were %s", key, formatAttributes(server.attributes))
			continue
		}
		if got := value.AsString(); got != want {
			t.Errorf("attribute %q = %q, want %q", key, got, want)
		}
	}
	statusValue, ok := server.attributes["http.response.status_code"]
	if !ok {
		t.Errorf("server span missing %q; attributes were %s", "http.response.status_code", formatAttributes(server.attributes))
	} else if got := statusValue.AsInt64(); got != http.StatusServiceUnavailable {
		t.Errorf("http.response.status_code = %d, want %d", got, http.StatusServiceUnavailable)
	}

	for _, legacyKey := range []attribute.Key{
		"http.method",
		"http.flavor",
		"http.scheme",
		"http.status_code",
		"net.host.name",
	} {
		if _, ok := server.attributes[legacyKey]; ok {
			t.Errorf("server span still contains legacy attribute %q", legacyKey)
		}
	}
	if server.statusCode != codes.Error || server.statusDescription != "HTTP 503" {
		t.Errorf("server status = (%v, %q), want (%v, %q)", server.statusCode, server.statusDescription, codes.Error, "HTTP 503")
	}
	if !server.ended {
		t.Error("server span was not ended")
	}
}

func TestMiddlewarePreservesServerStatusMapping(t *testing.T) {
	tests := []struct {
		status      int
		wantCode    codes.Code
		wantMessage string
	}{
		{status: http.StatusOK, wantCode: codes.Unset},
		{status: http.StatusNotFound, wantCode: codes.Unset},
		{status: http.StatusInternalServerError, wantCode: codes.Error, wantMessage: "HTTP 500"},
	}

	for _, tt := range tests {
		t.Run(fmt.Sprint(tt.status), func(t *testing.T) {
			recorder := newRecordingTracer()
			middleware := NewMiddleware(recorder, "service.internal")
			req, err := http.NewRequest(http.MethodGet, "http://service.internal/items", nil)
			if err != nil {
				t.Fatal(err)
			}

			gotStatus, gotErr := middleware.Instrument(context.Background(), "/items", req, func(context.Context, *http.Request) (int, error) {
				return tt.status, nil
			})
			if gotStatus != tt.status || gotErr != nil {
				t.Fatalf("Instrument() = (%d, %v), want (%d, nil)", gotStatus, gotErr, tt.status)
			}
			span := recorder.spanNamed(t, "GET /items")
			if span.statusCode != tt.wantCode || span.statusDescription != tt.wantMessage {
				t.Errorf("status = (%v, %q), want (%v, %q)", span.statusCode, span.statusDescription, tt.wantCode, tt.wantMessage)
			}
		})
	}
}

func TestMiddlewarePreservesHandlerErrorAndResult(t *testing.T) {
	recorder := newRecordingTracer()
	middleware := NewMiddleware(recorder, "service.internal")
	req, err := http.NewRequest(http.MethodPut, "http://service.internal/items/7", nil)
	if err != nil {
		t.Fatal(err)
	}
	wantErr := errors.New("backend unavailable")

	status, gotErr := middleware.Instrument(context.Background(), "/items/{id}", req, func(context.Context, *http.Request) (int, error) {
		return http.StatusAccepted, wantErr
	})
	if status != http.StatusAccepted || !errors.Is(gotErr, wantErr) {
		t.Fatalf("Instrument() = (%d, %v), want (%d, %v)", status, gotErr, http.StatusAccepted, wantErr)
	}
	span := recorder.spanNamed(t, "PUT /items/{id}")
	if span.statusCode != codes.Error || span.statusDescription != wantErr.Error() {
		t.Errorf("status = (%v, %q), want (%v, %q)", span.statusCode, span.statusDescription, codes.Error, wantErr.Error())
	}
}

func TestMiddlewareIsTransparentWithNoopTracer(t *testing.T) {
	tracer := noop.NewTracerProvider().Tracer("test")
	middleware := NewMiddleware(tracer, "service.internal")
	req, err := http.NewRequest(http.MethodGet, "http://service.internal/health", nil)
	if err != nil {
		t.Fatal(err)
	}
	wantErr := errors.New("still returned")
	calls := 0

	status, gotErr := middleware.Instrument(context.Background(), "/health", req, func(ctx context.Context, gotReq *http.Request) (int, error) {
		calls++
		if gotReq != req {
			t.Error("handler received a different request")
		}
		span := trace.SpanFromContext(ctx)
		if span.IsRecording() {
			t.Error("no-op current span unexpectedly records")
		}
		if span.SpanContext().IsValid() {
			t.Error("no-op current span unexpectedly has a valid span context")
		}
		return http.StatusTeapot, wantErr
	})

	if calls != 1 {
		t.Fatalf("handler called %d times, want once", calls)
	}
	if status != http.StatusTeapot || !errors.Is(gotErr, wantErr) {
		t.Fatalf("Instrument() = (%d, %v), want (%d, %v)", status, gotErr, http.StatusTeapot, wantErr)
	}
}

type recordingTracer struct {
	next  int
	spans []*recordingSpan
}

func newRecordingTracer() *recordingTracer { return &recordingTracer{} }

func (r *recordingTracer) Start(ctx context.Context, name string, _ ...trace.SpanStartOption) (context.Context, trace.Span) {
	r.next++
	parent := trace.SpanFromContext(ctx).SpanContext()
	traceID := parent.TraceID
	if traceID == "" {
		traceID = fmt.Sprintf("trace-%03d", r.next)
	}
	span := &recordingSpan{
		name:       name,
		parent:     parent,
		context:    trace.SpanContext{TraceID: traceID, SpanID: fmt.Sprintf("span-%03d", r.next)},
		attributes: make(map[attribute.Key]attribute.Value),
	}
	r.spans = append(r.spans, span)
	return trace.ContextWithSpan(ctx, span), span
}

func (r *recordingTracer) spanNamed(t *testing.T, name string) *recordingSpan {
	t.Helper()
	for _, span := range r.spans {
		if span.name == name {
			return span
		}
	}
	t.Fatalf("no recorded span named %q", name)
	return nil
}

type recordingSpan struct {
	name              string
	parent            trace.SpanContext
	context           trace.SpanContext
	attributes        map[attribute.Key]attribute.Value
	statusCode        codes.Code
	statusDescription string
	ended             bool
}

func (s *recordingSpan) End()              { s.ended = true }
func (s *recordingSpan) IsRecording() bool { return true }
func (s *recordingSpan) SpanContext() trace.SpanContext {
	return s.context
}
func (s *recordingSpan) SetAttributes(attributes ...attribute.KeyValue) {
	for _, kv := range attributes {
		s.attributes[kv.Key] = kv.Value
	}
}
func (s *recordingSpan) SetStatus(code codes.Code, description string) {
	s.statusCode = code
	s.statusDescription = description
}

func formatAttributes(attributes map[attribute.Key]attribute.Value) string {
	return fmt.Sprint(attributes)
}
