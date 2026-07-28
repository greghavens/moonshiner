package httpapi

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"example.com/go-interface-slimming/internal/dispatch"
)

type requestKey struct{}

func TestHandlerPropagatesRequestContext(t *testing.T) {
	const marker = "trace-41"
	fake := &FakeRunService{
		GetRunFunc: func(ctx context.Context, id string) (dispatch.Run, error) {
			if got := ctx.Value(requestKey{}); got != marker {
				t.Fatalf("GetRun context marker = %v, want %q", got, marker)
			}
			return dispatch.Run{ID: id, Tenant: "north", State: "ready"}, nil
		},
		CancelRunFunc: func(ctx context.Context, id string) error {
			if got := ctx.Value(requestKey{}); got != marker {
				t.Fatalf("CancelRun context marker = %v, want %q", got, marker)
			}
			return nil
		},
	}
	handler := New(fake)

	for _, tc := range []struct {
		method string
		path   string
		status int
	}{
		{method: http.MethodGet, path: "/runs/r-17", status: http.StatusOK},
		{method: http.MethodPost, path: "/runs/r-17/cancel", status: http.StatusAccepted},
	} {
		request := httptest.NewRequest(tc.method, tc.path, nil)
		request = request.WithContext(context.WithValue(request.Context(), requestKey{}, marker))
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != tc.status {
			t.Fatalf("%s %s status = %d, want %d; body=%s",
				tc.method, tc.path, response.Code, tc.status, response.Body.String())
		}
		if got := response.Header().Get("Content-Type"); got != "application/json" {
			t.Fatalf("Content-Type = %q, want application/json", got)
		}
	}
}

func TestHandlerClassifiesWrappedErrorsAndCancellation(t *testing.T) {
	notFound := &FakeRunService{
		GetRunFunc: func(context.Context, string) (dispatch.Run, error) {
			return dispatch.Run{}, fmt.Errorf("storage lookup: %w", dispatch.ErrNotFound)
		},
	}
	response := httptest.NewRecorder()
	New(notFound).ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/runs/missing", nil))
	if response.Code != http.StatusNotFound || !strings.Contains(response.Body.String(), "run not found") {
		t.Fatalf("wrapped not-found response = %d %q", response.Code, response.Body.String())
	}

	conflict := &FakeRunService{
		CancelRunFunc: func(context.Context, string) error {
			return fmt.Errorf("cancel rejected: %w", dispatch.ErrConflict)
		},
	}
	response = httptest.NewRecorder()
	New(conflict).ServeHTTP(response, httptest.NewRequest(http.MethodPost, "/runs/r-2/cancel", nil))
	if response.Code != http.StatusConflict || !strings.Contains(response.Body.String(), "run state conflict") {
		t.Fatalf("wrapped conflict response = %d %q", response.Code, response.Body.String())
	}

	cancelled := &FakeRunService{
		GetRunFunc: func(ctx context.Context, _ string) (dispatch.Run, error) {
			return dispatch.Run{}, fmt.Errorf("read aborted: %w", ctx.Err())
		},
	}
	request := httptest.NewRequest(http.MethodGet, "/runs/r-3", nil)
	ctx, cancel := context.WithCancel(request.Context())
	cancel()
	response = httptest.NewRecorder()
	New(cancelled).ServeHTTP(response, request.WithContext(ctx))
	if response.Code != statusClientClosedRequest || !strings.Contains(response.Body.String(), "request cancelled") {
		t.Fatalf("cancelled response = %d %q", response.Code, response.Body.String())
	}
}

func TestFakeRunServiceRecordsConcurrentCalls(t *testing.T) {
	fake := &FakeRunService{
		GetRunFunc: func(_ context.Context, id string) (dispatch.Run, error) {
			return dispatch.Run{ID: id}, nil
		},
	}
	handler := New(fake)

	const requests = 64
	var group sync.WaitGroup
	group.Add(requests)
	for i := 0; i < requests; i++ {
		go func(i int) {
			defer group.Done()
			response := httptest.NewRecorder()
			request := httptest.NewRequest(http.MethodGet, fmt.Sprintf("/runs/r-%d", i), nil)
			handler.ServeHTTP(response, request)
			if response.Code != http.StatusOK {
				t.Errorf("request %d status = %d", i, response.Code)
			}
		}(i)
	}
	group.Wait()

	if got := len(fake.GetRunCalls); got != requests {
		t.Fatalf("recorded GetRun calls = %d, want %d", got, requests)
	}
}
