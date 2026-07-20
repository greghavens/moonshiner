// Package semconv contains the legacy HTTP semantic-convention attributes.
package semconv

import "go.opentelemetry.io/otel/attribute"

const (
	HTTPRouteKey      attribute.Key = "http.route"
	HTTPStatusCodeKey attribute.Key = "http.status_code"
)
