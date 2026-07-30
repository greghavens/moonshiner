package supervisorvks

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
)

var ErrNotImplemented = errors.New("supervisorvks: not implemented")

// TokenSource returns the current Kubernetes access token. When forceRefresh is
// true, it must obtain a replacement rather than return a cached token.
type TokenSource interface {
	Token(ctx context.Context, forceRefresh bool) (string, error)
}

type TokenSourceFunc func(context.Context, bool) (string, error)

func (f TokenSourceFunc) Token(ctx context.Context, forceRefresh bool) (string, error) {
	return f(ctx, forceRefresh)
}

type NamespaceSpec struct {
	Name       string
	Supervisor string
}

type ClusterSpec struct {
	Name                 string
	Class                string
	Version              string
	ControlPlaneReplicas int
	WorkerClass          string
	WorkerName           string
	WorkerReplicas       int
	VMClass              string
	StorageClass         string
}

type Result struct {
	NamespaceCreated bool
	ClusterCreated   bool
}

type APIError struct {
	OperationID string
	StatusCode  int
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s returned HTTP %d", e.OperationID, e.StatusCode)
}

type Client struct {
	vcenterOrigin *url.URL
	kubeOrigin    *url.URL
	sessionID     string
	tokens        TokenSource
	httpClient    *http.Client
}
