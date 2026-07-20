// Package httpconv contains the legacy HTTP semantic-convention helpers.
package httpconv

import (
	"fmt"
	"net/http"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
)

const (
	httpMethodKey attribute.Key = "http.method"
	httpFlavorKey attribute.Key = "http.flavor"
	httpSchemeKey attribute.Key = "http.scheme"
	netHostNameKey attribute.Key = "net.host.name"
)

// ServerRequest returns request attributes using the legacy key names.
func ServerRequest(server string, req *http.Request) []attribute.KeyValue {
	return []attribute.KeyValue{
		httpMethodKey.String(req.Method),
		httpFlavorKey.String(protocolVersion(req)),
		httpSchemeKey.String(requestScheme(req)),
		netHostNameKey.String(server),
	}
}

// ServerStatus applies the OpenTelemetry server-span HTTP status mapping.
func ServerStatus(status int) (codes.Code, string) {
	if status >= http.StatusInternalServerError {
		return codes.Error, fmt.Sprintf("HTTP %d", status)
	}
	return codes.Unset, ""
}

func protocolVersion(req *http.Request) string {
	if req.ProtoMajor == 0 {
		return ""
	}
	return fmt.Sprintf("%d.%d", req.ProtoMajor, req.ProtoMinor)
}

func requestScheme(req *http.Request) string {
	if req.URL != nil && req.URL.Scheme != "" {
		return req.URL.Scheme
	}
	if req.TLS != nil {
		return "https"
	}
	return "http"
}
