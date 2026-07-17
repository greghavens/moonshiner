// Package fetch wraps the user-directory backend with request deadlines.
// The backend client library has no context support (it's a generated
// thrift shim), so we run each lookup in the background and let the
// caller's context decide how long we're willing to wait for it.
package fetch

import "context"

// Profile is the directory record the sidecar serves to product teams.
type Profile struct {
	ID          string
	DisplayName string
	Email       string
}

// LookupFunc is the blocking, context-unaware backend call.
type LookupFunc func(id string) (Profile, error)

// Client adds deadline support on top of a blocking lookup.
type Client struct {
	lookup LookupFunc
}

// New wraps a blocking backend call.
func New(lookup LookupFunc) *Client {
	return &Client{lookup: lookup}
}

type outcome struct {
	p   Profile
	err error
}

// Get returns the profile for id, or ctx.Err() if the context ends before
// the backend answers. The backend call itself cannot be interrupted; we
// just stop waiting for it.
func (c *Client) Get(ctx context.Context, id string) (Profile, error) {
	ch := make(chan outcome)
	go func() {
		p, err := c.lookup(id)
		ch <- outcome{p, err}
	}()
	select {
	case out := <-ch:
		return out.p, out.err
	case <-ctx.Done():
		return Profile{}, ctx.Err()
	}
}
