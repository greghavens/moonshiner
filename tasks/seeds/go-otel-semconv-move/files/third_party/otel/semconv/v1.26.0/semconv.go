// Package semconv contains generated v1.26.0 semantic-convention attributes.
package semconv

import "go.opentelemetry.io/otel/attribute"

const (
	HTTPRequestMethodKey      attribute.Key = "http.request.method"
	HTTPRouteKey              attribute.Key = "http.route"
	NetworkProtocolVersionKey attribute.Key = "network.protocol.version"
	URLSchemeKey              attribute.Key = "url.scheme"
	ServerAddressKey          attribute.Key = "server.address"
	HTTPResponseStatusCodeKey attribute.Key = "http.response.status_code"
)
