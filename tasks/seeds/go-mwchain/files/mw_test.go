package mw

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// Compile-time pins: every provided middleware composes through Chain.
var (
	_ Middleware = RequestID
	_ Middleware = Recover
	_ Middleware = RequireHeader("X-Api-Key", "k")
	_ Middleware = CaptureStatus(func(status int, bytes int64) {})
)

func okHandler(body string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, body)
	})
}

func serve(h http.Handler, req *http.Request) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func get(path string) *http.Request { return httptest.NewRequest(http.MethodGet, path, nil) }

func TestChainOrderOutermostFirst(t *testing.T) {
	var order []string
	tag := func(name string) Middleware {
		return func(next http.Handler) http.Handler {
			return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				order = append(order, name+"-in")
				next.ServeHTTP(w, r)
				order = append(order, name+"-out")
			})
		}
	}
	h := Chain(tag("a"), tag("b"), tag("c"))(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		order = append(order, "handler")
		w.WriteHeader(http.StatusTeapot)
	}))
	rec := serve(h, get("/x"))
	if rec.Code != http.StatusTeapot {
		t.Fatalf("status = %d, want 418", rec.Code)
	}
	want := []string{"a-in", "b-in", "c-in", "handler", "c-out", "b-out", "a-out"}
	if len(order) != len(want) {
		t.Fatalf("order = %v, want %v", order, want)
	}
	for i := range want {
		if order[i] != want[i] {
			t.Fatalf("Chain(a,b,c) must run a outermost: got %v, want %v", order, want)
		}
	}
}

func TestChainEmptyAndNested(t *testing.T) {
	rec := serve(Chain()(okHandler("plain")), get("/"))
	if rec.Code != 200 || rec.Body.String() != "plain" {
		t.Fatalf("empty Chain must be the identity: %d %q", rec.Code, rec.Body.String())
	}

	var order []string
	tag := func(name string) Middleware {
		return func(next http.Handler) http.Handler {
			return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				order = append(order, name)
				next.ServeHTTP(w, r)
			})
		}
	}
	// A Chain is itself a Middleware, so chains must nest.
	h := Chain(Chain(tag("a"), tag("b")), tag("c"))(okHandler("ok"))
	serve(h, get("/"))
	if strings.Join(order, ",") != "a,b,c" {
		t.Fatalf("nested chains must flatten in order, got %v", order)
	}
}

func TestRequestIDGenerated(t *testing.T) {
	var seenInCtx string
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seenInCtx = RequestIDFrom(r.Context())
		w.WriteHeader(200)
	})
	rec := serve(RequestID(inner), get("/a"))
	id := rec.Header().Get("X-Request-ID")
	if id == "" {
		t.Fatal("RequestID must set a non-empty X-Request-ID response header")
	}
	if seenInCtx != id {
		t.Fatalf("handler saw request id %q via context, response header says %q — they must match", seenInCtx, id)
	}

	seen := map[string]bool{}
	for i := 0; i < 50; i++ {
		rec := serve(RequestID(inner), get("/a"))
		id := rec.Header().Get("X-Request-ID")
		if seen[id] {
			t.Fatalf("generated request id %q repeated within 50 requests", id)
		}
		seen[id] = true
	}
}

func TestRequestIDHonorsIncomingHeader(t *testing.T) {
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := RequestIDFrom(r.Context()); got != "trace-abc-123" {
			t.Fatalf("context id = %q, want the incoming trace-abc-123", got)
		}
	})
	req := get("/a")
	req.Header.Set("X-Request-ID", "trace-abc-123")
	rec := serve(RequestID(inner), req)
	if got := rec.Header().Get("X-Request-ID"); got != "trace-abc-123" {
		t.Fatalf("response header = %q, want the incoming id echoed back", got)
	}
}

func TestRequestIDFromBareContext(t *testing.T) {
	if got := RequestIDFrom(context.Background()); got != "" {
		t.Fatalf("RequestIDFrom without middleware = %q, want \"\"", got)
	}
}

func TestRequireHeaderShortCircuits(t *testing.T) {
	called := false
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
	})
	guard := RequireHeader("X-Api-Key", "s3cret")(inner)

	rec := serve(guard, get("/admin"))
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("missing header: status = %d, want 401", rec.Code)
	}
	if called {
		t.Fatal("inner handler ran even though the guard rejected the request")
	}

	req := get("/admin")
	req.Header.Set("X-Api-Key", "wrong")
	rec = serve(guard, req)
	if rec.Code != http.StatusUnauthorized || called {
		t.Fatalf("wrong value: status = %d, called = %v; want 401 and no call", rec.Code, called)
	}

	req = get("/admin")
	req.Header.Set("X-Api-Key", "s3cret")
	rec = serve(guard, req)
	if rec.Code != 200 || !called {
		t.Fatalf("correct value: status = %d, called = %v; want 200 and a call", rec.Code, called)
	}
}

func TestShortCircuitStillGetsOuterMiddleware(t *testing.T) {
	inner := okHandler("secret stuff")
	h := Chain(RequestID, RequireHeader("X-Api-Key", "s3cret"))(inner)
	rec := serve(h, get("/admin"))
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", rec.Code)
	}
	if rec.Header().Get("X-Request-ID") == "" {
		t.Fatal("outer RequestID ran before the guard, so the 401 must still carry X-Request-ID")
	}
	if strings.Contains(rec.Body.String(), "secret stuff") {
		t.Fatal("guarded handler body leaked through a 401")
	}
}

func TestRecoverTurnsPanicInto500(t *testing.T) {
	h := Recover(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		panic("boom: nil map write")
	}))
	rec := serve(h, get("/crashy")) // must not panic out of ServeHTTP
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", rec.Code)
	}
	if rec.Body.Len() == 0 {
		t.Fatal("500 from a recovered panic should carry some body")
	}
	if strings.Contains(rec.Body.String(), "nil map write") {
		t.Fatal("panic details must not leak into the response body")
	}
}

func TestRecoverLeavesHealthyRequestsAlone(t *testing.T) {
	rec := serve(Recover(okHandler("fine")), get("/"))
	if rec.Code != 200 || rec.Body.String() != "fine" {
		t.Fatalf("got %d %q, want 200 \"fine\"", rec.Code, rec.Body.String())
	}
}

func TestRecoverRepanicsErrAbortHandler(t *testing.T) {
	h := Recover(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		panic(http.ErrAbortHandler)
	}))
	defer func() {
		if got := recover(); got != http.ErrAbortHandler {
			t.Fatalf("recovered %v, want http.ErrAbortHandler re-panicked (net/http relies on it)", got)
		}
	}()
	h.ServeHTTP(httptest.NewRecorder(), get("/abort"))
	t.Fatal("ErrAbortHandler must propagate, not be swallowed")
}

func TestRecoverKeepsServingOverRealServer(t *testing.T) {
	h := Chain(Recover)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("boom") == "1" {
			panic("kaboom")
		}
		io.WriteString(w, "steady")
	}))
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)

	resp, err := http.Get(srv.URL + "/?boom=1")
	if err != nil {
		t.Fatalf("panicking request must still get an HTTP response, got %v", err)
	}
	io.Copy(io.Discard, resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", resp.StatusCode)
	}

	resp, err = http.Get(srv.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 200 || string(body) != "steady" {
		t.Fatalf("request after a recovered panic: %d %q, want 200 \"steady\"", resp.StatusCode, body)
	}
}

func TestCaptureStatusExplicitCode(t *testing.T) {
	var gotStatus int
	var gotBytes int64
	h := CaptureStatus(func(status int, bytes int64) { gotStatus, gotBytes = status, bytes })(
		http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusNotFound)
			io.WriteString(w, "nope")
		}))
	serve(h, get("/missing"))
	if gotStatus != 404 || gotBytes != 4 {
		t.Fatalf("onDone(%d, %d), want (404, 4)", gotStatus, gotBytes)
	}
}

func TestCaptureStatusImplicit200(t *testing.T) {
	var gotStatus int
	var gotBytes int64
	cb := func(status int, bytes int64) { gotStatus, gotBytes = status, bytes }

	serve(CaptureStatus(cb)(okHandler("hi")), get("/"))
	if gotStatus != 200 || gotBytes != 2 {
		t.Fatalf("write without WriteHeader: onDone(%d, %d), want (200, 2)", gotStatus, gotBytes)
	}

	serve(CaptureStatus(cb)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {})), get("/"))
	if gotStatus != 200 || gotBytes != 0 {
		t.Fatalf("handler that writes nothing: onDone(%d, %d), want (200, 0)", gotStatus, gotBytes)
	}
}

func TestCaptureStatusFirstHeaderWins(t *testing.T) {
	var gotStatus int
	cb := func(status int, bytes int64) { gotStatus = status }

	serve(CaptureStatus(cb)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
		w.WriteHeader(http.StatusOK) // late second call must not change the report
	})), get("/"))
	if gotStatus != 503 {
		t.Fatalf("double WriteHeader: reported %d, want the first (503)", gotStatus)
	}

	serve(CaptureStatus(cb)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "x")
		w.WriteHeader(http.StatusInternalServerError) // after a Write the status is already 200
	})), get("/"))
	if gotStatus != 200 {
		t.Fatalf("WriteHeader after Write: reported %d, want 200", gotStatus)
	}
}

func TestCaptureStatusSumsAllWrites(t *testing.T) {
	var gotBytes int64
	serve(CaptureStatus(func(status int, bytes int64) { gotBytes = bytes })(
		http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Write([]byte("abc"))
			w.Write(nil)
			w.Write([]byte("defgh"))
		})), get("/"))
	if gotBytes != 8 {
		t.Fatalf("bytes = %d, want 8 across three writes", gotBytes)
	}
}

func TestCaptureStatusCalledOncePerRequest(t *testing.T) {
	calls := 0
	h := CaptureStatus(func(status int, bytes int64) { calls++ })(okHandler("x"))
	for i := 0; i < 3; i++ {
		serve(h, get(fmt.Sprintf("/r%d", i)))
	}
	if calls != 3 {
		t.Fatalf("onDone ran %d times for 3 requests, want exactly once each", calls)
	}
}

func TestCaptureStatusSeesRecoveredPanicAs500(t *testing.T) {
	var gotStatus int
	h := Chain(
		CaptureStatus(func(status int, bytes int64) { gotStatus = status }),
		Recover,
	)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		panic("downstream blew up")
	}))
	serve(h, get("/"))
	if gotStatus != http.StatusInternalServerError {
		t.Fatalf("capture around Recover reported %d, want 500", gotStatus)
	}
}

func TestCaptureStatusPreservesFlusherOverRealServer(t *testing.T) {
	h := Chain(
		RequestID,
		CaptureStatus(func(status int, bytes int64) {}),
		Recover,
	)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if _, ok := w.(http.Flusher); !ok {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)

	resp, err := http.Get(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	io.Copy(io.Discard, resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusNoContent {
		t.Fatal("wrapped ResponseWriter lost http.Flusher — streaming handlers downstream will break")
	}
}

func TestHeadersSetByHandlerSurvive(t *testing.T) {
	h := Chain(RequestID, CaptureStatus(func(int, int64) {}), Recover)(
		http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("X-Custom", "42")
			w.WriteHeader(http.StatusAccepted)
		}))
	rec := serve(h, get("/"))
	if rec.Code != http.StatusAccepted || rec.Header().Get("X-Custom") != "42" {
		t.Fatalf("got %d X-Custom=%q, want 202 and \"42\"", rec.Code, rec.Header().Get("X-Custom"))
	}
}
