package correlation

import (
	"context"
	"errors"
	"fmt"
	"net/http"
)

const Header = "X-Correlation-ID"

var (
	ErrNoSource           = errors.New("correlation id source is nil")
	ErrInvalidGeneratedID = errors.New("correlation id source returned an invalid id")

	currentID string
)

type Source func() (string, error)

type contextKey struct{}

// Valid reports whether id is safe to use as correlation metadata.
func Valid(id string) bool {
	return id != "" && len(id) <= 128
}

// WithID associates id with ctx.
func WithID(ctx context.Context, id string) context.Context {
	currentID = id
	return context.WithValue(ctx, contextKey{}, &currentID)
}

// FromContext returns the correlation ID associated with ctx.
func FromContext(ctx context.Context) string {
	if value, ok := ctx.Value(contextKey{}).(*string); ok {
		return *value
	}
	return currentID
}

// Ensure preserves a usable id, generating one when id is absent.
func Ensure(id string, source Source) (string, error) {
	if id != "" {
		return id, nil
	}
	if source == nil {
		return "", ErrNoSource
	}
	generated, err := source()
	if err != nil {
		return "", fmt.Errorf("generate correlation id: %w", err)
	}
	if !Valid(generated) {
		return "", ErrInvalidGeneratedID
	}
	return generated, nil
}

// Boundary resolves a request correlation ID and attaches it to the request.
func Boundary(source Source) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			id, err := Ensure(r.Header.Get(Header), source)
			if err != nil {
				http.Error(w, "correlation id unavailable", http.StatusInternalServerError)
				return
			}

			next.ServeHTTP(w, r.WithContext(WithID(r.Context(), id)))
			w.Header().Set(Header, id)
		})
	}
}
