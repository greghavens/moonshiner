package poller

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"sync"
	"testing"
)

// trackedBody records whether the poller drained it to EOF and closed it.
type trackedBody struct {
	mu      sync.Mutex
	r       *strings.Reader
	drained bool
	closed  bool
}

func (b *trackedBody) Read(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	n, err := b.r.Read(p)
	if err == io.EOF {
		b.drained = true
	}
	return n, err
}

func (b *trackedBody) Close() error {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.closed = true
	return nil
}

// step scripts one loopback response.
type step struct {
	status int
	body   string
}

// fakeTransport is a loopback transport with a tiny connection pool:
// a response body left unclosed keeps its connection checked out, and
// once the pool is empty new requests fail the way a starved
// production transport eventually does.
type fakeTransport struct {
	mu      sync.Mutex
	script  []step
	next    int
	bodies  []*trackedBody
	maxOpen int
}

func (f *fakeTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if err := req.Context().Err(); err != nil {
		return nil, err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	open := 0
	for _, b := range f.bodies {
		b.mu.Lock()
		if !b.closed {
			open++
		}
		b.mu.Unlock()
	}
	if open >= f.maxOpen {
		return nil, errors.New("transport stalled: connection pool exhausted by unclosed response bodies")
	}
	if f.next >= len(f.script) {
		return nil, errors.New("unexpected request past end of script")
	}
	s := f.script[f.next]
	f.next++
	b := &trackedBody{r: strings.NewReader(s.body)}
	f.bodies = append(f.bodies, b)
	return &http.Response{
		StatusCode: s.status,
		Status:     http.StatusText(s.status),
		Header:     make(http.Header),
		Body:       b,
	}, nil
}

func newClient(ft *fakeTransport, attempts int) *Client {
	return &Client{HTTPClient: &http.Client{Transport: ft}, MaxAttempts: attempts}
}

// requireBodiesFinished asserts every issued body was drained to EOF
// and closed, i.e. its connection went back to the pool reusable.
func requireBodiesFinished(t *testing.T, ft *fakeTransport) {
	t.Helper()
	for i, b := range ft.bodies {
		b.mu.Lock()
		drained, closed := b.drained, b.closed
		b.mu.Unlock()
		if !closed {
			t.Errorf("response body %d was never closed", i+1)
		}
		if !drained {
			t.Errorf("response body %d was not read to EOF before being handed back", i+1)
		}
	}
}

func TestSuccessReturnsStatusAndReleasesConnection(t *testing.T) {
	ft := &fakeTransport{
		script:  []step{{200, `{"state":"running","progress":41}`}},
		maxOpen: 2,
	}
	st, err := newClient(ft, 3).Poll(context.Background(), "http://ingest.local/jobs/7")
	if err != nil {
		t.Fatalf("Poll: %v", err)
	}
	if st.State != "running" || st.Progress != 41 {
		t.Fatalf("status = %+v, want state running progress 41", st)
	}
	requireBodiesFinished(t, ft)
}

func TestRetryableStatusesEventuallySucceed(t *testing.T) {
	ft := &fakeTransport{
		script: []step{
			{503, `{"error":"warming up"}`},
			{429, `{"error":"slow down"}`},
			{200, `{"state":"done","progress":100}`},
		},
		maxOpen: 2,
	}
	st, err := newClient(ft, 5).Poll(context.Background(), "http://ingest.local/jobs/7")
	if err != nil {
		t.Fatalf("Poll failed after retryable statuses: %v", err)
	}
	if st.State != "done" || st.Progress != 100 {
		t.Fatalf("status = %+v, want state done progress 100", st)
	}
	if ft.next != 3 {
		t.Fatalf("served %d responses, want 3", ft.next)
	}
	requireBodiesFinished(t, ft)
}

func TestMalformedStatusBodyReportsAndCleansUp(t *testing.T) {
	ft := &fakeTransport{
		script:  []step{{200, `{"state":`}},
		maxOpen: 2,
	}
	st, err := newClient(ft, 3).Poll(context.Background(), "http://ingest.local/jobs/7")
	if err == nil {
		t.Fatalf("Poll accepted a malformed body, returned %+v", st)
	}
	if st != nil {
		t.Fatalf("status = %+v, want nil on decode failure", st)
	}
	requireBodiesFinished(t, ft)
}

func TestTerminalStatusKeepsStructuredError(t *testing.T) {
	ft := &fakeTransport{
		script:  []step{{404, `{"error":"job vanished"}`}},
		maxOpen: 2,
	}
	_, err := newClient(ft, 3).Poll(context.Background(), "http://ingest.local/jobs/7")
	var se *StatusError
	if !errors.As(err, &se) {
		t.Fatalf("error = %v (%T), want *StatusError", err, err)
	}
	if se.Code != 404 || se.Message != "job vanished" {
		t.Fatalf("structured error = %+v, want code 404 message %q", se, "job vanished")
	}
	requireBodiesFinished(t, ft)
}

func TestRetryBudgetExhaustedStillCleansUp(t *testing.T) {
	ft := &fakeTransport{
		script: []step{
			{503, `{"error":"warming up"}`},
			{503, `{"error":"warming up"}`},
		},
		maxOpen: 4,
	}
	_, err := newClient(ft, 2).Poll(context.Background(), "http://ingest.local/jobs/7")
	var se *StatusError
	if !errors.As(err, &se) {
		t.Fatalf("error = %v (%T), want *StatusError", err, err)
	}
	if se.Code != 503 || se.Message != "retry budget exhausted" {
		t.Fatalf("structured error = %+v, want code 503 retry budget exhausted", se)
	}
	if ft.next != 2 {
		t.Fatalf("served %d responses, want 2", ft.next)
	}
	requireBodiesFinished(t, ft)
}

func TestCancelledContextPropagates(t *testing.T) {
	ft := &fakeTransport{
		script:  []step{{200, `{"state":"running","progress":1}`}},
		maxOpen: 2,
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := newClient(ft, 3).Poll(ctx, "http://ingest.local/jobs/7")
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context.Canceled identity preserved", err)
	}
	if ft.next != 0 {
		t.Fatalf("cancelled poll still consumed %d scripted responses", ft.next)
	}
	requireBodiesFinished(t, ft)
}
