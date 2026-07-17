package dashfeed

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func instantRender(body string) RenderFunc {
	return func(ctx context.Context, name string) ([]byte, error) {
		return []byte(body), nil
	}
}

func getBody(t *testing.T, client *http.Client, url string) (*http.Response, []byte) {
	t.Helper()
	resp, err := client.Get(url)
	if err != nil {
		t.Fatalf("GET %s: %v", url, err)
	}
	raw, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		t.Fatalf("reading %s: %v", url, err)
	}
	return resp, raw
}

func TestSummaryRelaysStats(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"deploys":7,"failures":1}`)
	}))
	t.Cleanup(upstream.Close)

	srv := httptest.NewServer(New(upstream.URL, instantRender(`{}`)))
	t.Cleanup(srv.Close)

	resp, raw := getBody(t, srv.Client(), srv.URL+"/summary")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("summary body not JSON: %v (%q)", err, raw)
	}
	if m["deploys"] != float64(7) || m["failures"] != float64(1) {
		t.Fatalf("summary = %v", m)
	}
}

func TestReportRendersForPatientClients(t *testing.T) {
	srv := httptest.NewServer(New("http://stats.invalid", instantRender(`{"report":"daily","rows":3}`)))
	t.Cleanup(srv.Close)

	resp, raw := getBody(t, srv.Client(), srv.URL+"/report/daily")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	if string(raw) != `{"report":"daily","rows":3}` {
		t.Fatalf("report body = %q", raw)
	}
}

func TestReportRenderErrorIs500(t *testing.T) {
	render := func(ctx context.Context, name string) ([]byte, error) {
		return nil, context.DeadlineExceeded // any error will do
	}
	srv := httptest.NewServer(New("http://stats.invalid", render))
	t.Cleanup(srv.Close)

	resp, raw := getBody(t, srv.Client(), srv.URL+"/report/daily")
	if resp.StatusCode != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", resp.StatusCode)
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil || m["error"] != "render failed" {
		t.Fatalf("500 body = %q", raw)
	}
}

func TestUnknownPathIs404(t *testing.T) {
	srv := httptest.NewServer(New("http://stats.invalid", instantRender(`{}`)))
	t.Cleanup(srv.Close)
	resp, _ := getBody(t, srv.Client(), srv.URL+"/nope")
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", resp.StatusCode)
	}
}

// Reproduces the deploy-drain corruption: the pod starts draining (every
// in-flight request context gets cancelled) while a report render is still
// running. The client is still connected and must receive exactly one clean
// JSON error document — nothing else before, after, or glued onto it.
func TestDrainedReportRespondsWithSingleCleanJSONError(t *testing.T) {
	started := make(chan struct{})
	release := make(chan struct{})
	render := func(ctx context.Context, name string) ([]byte, error) {
		close(started)
		<-release
		return []byte(`{"report":"weekly","rows":[1,2,3]}`), nil
	}

	baseCtx, drain := context.WithCancel(context.Background())
	defer drain()
	srv := httptest.NewUnstartedServer(New("http://stats.invalid", render))
	srv.Config.BaseContext = func(net.Listener) context.Context { return baseCtx }
	srv.Start()
	t.Cleanup(srv.Close)

	type result struct {
		resp *http.Response
		body []byte
		err  error
	}
	resCh := make(chan result, 1)
	go func() {
		resp, err := srv.Client().Get(srv.URL + "/report/weekly")
		if err != nil {
			resCh <- result{err: err}
			return
		}
		body, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		resCh <- result{resp: resp, body: body, err: err}
	}()

	<-started      // the render is in flight for this request
	drain()        // deploy begins: server-side contexts are cancelled
	close(release) // the renderer eventually finishes its (now unwanted) work

	res := <-resCh
	if res.err != nil {
		t.Fatalf("client never got a usable response: %v", res.err)
	}
	if res.resp.StatusCode != http.StatusGatewayTimeout {
		t.Fatalf("status = %d, want 504 for a drained render", res.resp.StatusCode)
	}
	var m map[string]any
	if err := json.Unmarshal(res.body, &m); err != nil {
		t.Fatalf("client must receive a single well-formed JSON document, got %q (%v)", res.body, err)
	}
	if m["error"] != "report timed out" {
		t.Fatalf("error body = %v, want the timeout error", m)
	}
}

// Reproduces the stats-backend complaint: when their service answers 500s
// for a stretch, our summary endpoint must (a) keep answering every poll
// with a 502 and (b) hold ONE keep-alive connection to the backend instead
// of burning a fresh one per poll.
func TestSummarySurvivesDegradedStatsBackend(t *testing.T) {
	var conns atomic.Int32
	upstream := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		io.WriteString(w, "overloaded, go away\n")
	}))
	upstream.Config.ConnState = func(c net.Conn, st http.ConnState) {
		if st == http.StateNew {
			conns.Add(1)
		}
	}
	upstream.Start()
	t.Cleanup(upstream.Close)

	srv := httptest.NewServer(New(upstream.URL, instantRender(`{}`)))
	t.Cleanup(srv.Close)

	client := &http.Client{Timeout: 5 * time.Second}
	for i := 1; i <= 3; i++ {
		resp, err := client.Get(srv.URL + "/summary")
		if err != nil {
			t.Fatalf("summary poll %d never came back (%v) — the edge stopped answering after an upstream error", i, err)
		}
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode != http.StatusBadGateway {
			t.Fatalf("poll %d: status = %d, want 502 while the backend is degraded", i, resp.StatusCode)
		}
		var m map[string]any
		if err := json.Unmarshal(raw, &m); err != nil || m["error"] != "stats backend degraded" {
			t.Fatalf("poll %d: body = %q", i, raw)
		}
	}
	if got := conns.Load(); got != 1 {
		t.Fatalf("stats backend saw %d TCP connections for 3 polls, want 1 reused keep-alive connection", got)
	}
}
