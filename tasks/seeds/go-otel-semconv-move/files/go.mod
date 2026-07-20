module example.com/go-otel-semconv-move

go 1.20

require go.opentelemetry.io/otel v0.0.0

replace go.opentelemetry.io/otel => ./third_party/otel
