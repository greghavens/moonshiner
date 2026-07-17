package statuspoll

// Acceptance tests for the async-job client against the Foundry batch
// platform's submit/poll/result protocol. Every scenario runs against a
// scripted httptest server on loopback: responses come from a fixed queue
// and every request is recorded, so ordering and counts are exact. All
// waiting goes through the injectable sleeper — nothing in a correct
// implementation sleeps for real in these tests.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

type step struct {
	status   int    // 0 means 200
	location string // emitted as the Location header when non-empty
	body     string
}

type script struct {
	mu     sync.Mutex
	steps  []step
	reqs   []string         // "METHOD <path?query>" in arrival order
	bodies []map[string]any // decoded JSON body per request (nil when empty)
}

func (s *script) requests() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]string(nil), s.reqs...)
}

func (s *script) requestCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.reqs)
}

func (s *script) body(i int) map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.bodies[i]
}

func newPlatform(t *testing.T, steps ...step) (*httptest.Server, *script) {
	t.Helper()
	sc := &script{steps: steps}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		var decoded map[string]any
		if len(raw) > 0 {
			_ = json.Unmarshal(raw, &decoded)
		}
		sc.mu.Lock()
		sc.reqs = append(sc.reqs, r.Method+" "+r.URL.RequestURI())
		sc.bodies = append(sc.bodies, decoded)
		var st step
		if len(sc.steps) > 0 {
			st = sc.steps[0]
			sc.steps = sc.steps[1:]
		} else {
			st = step{status: 599, body: `{"error":"script exhausted: unexpected extra request"}`}
		}
		sc.mu.Unlock()
		if st.location != "" {
			w.Header().Set("Location", st.location)
		}
		w.Header().Set("Content-Type", "application/json")
		if st.status == 0 {
			st.status = http.StatusOK
		}
		w.WriteHeader(st.status)
		fmt.Fprint(w, st.body)
	}))
	t.Cleanup(srv.Close)
	return srv, sc
}

func recordingSleeper() (*[]time.Duration, func(context.Context, time.Duration) error) {
	var waits []time.Duration
	return &waits, func(ctx context.Context, d time.Duration) error {
		waits = append(waits, d)
		return nil
	}
}

func ms(v ...int) []time.Duration {
	out := make([]time.Duration, len(v))
	for i, n := range v {
		out[i] = time.Duration(n) * time.Millisecond
	}
	return out
}

const resultBody = `{"state":"complete","rows":1234,"download":"/exports/quarterly.csv"}`

var wantResult = map[string]any{
	"state":    "complete",
	"rows":     float64(1234),
	"download": "/exports/quarterly.csv",
}

func TestSubmitPostsKindAndParamsAndResolvesRelativeLocation(t *testing.T) {
	srv, sc := newPlatform(t, step{status: 202, location: "/jobs/j-101", body: `{"id":"j-101","state":"queued"}`})
	c := New(srv.URL, srv.Client(), Options{Schedule: ms(10), MaxPolls: 5})
	job, err := c.Submit(context.Background(), "export", map[string]any{"report": "quarterly", "format": "csv"})
	if err != nil {
		t.Fatalf("Submit() error = %v", err)
	}
	if want := srv.URL + "/jobs/j-101"; job.StatusURL != want {
		t.Fatalf("StatusURL = %q, want the relative Location resolved to %q", job.StatusURL, want)
	}
	if got, want := sc.requests(), []string{"POST /jobs"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("requests = %v, want %v", got, want)
	}
	wantBody := map[string]any{
		"kind":   "export",
		"params": map[string]any{"report": "quarterly", "format": "csv"},
	}
	if got := sc.body(0); !reflect.DeepEqual(got, wantBody) {
		t.Fatalf("submit body = %v, want exactly %v", got, wantBody)
	}
}

func TestSubmitKeepsAnAbsoluteLocation(t *testing.T) {
	srv, _ := newPlatform(t)
	srv2, sc2 := newPlatform(t, step{status: 202, location: srv.URL + "/jobs/j-7", body: `{}`})
	c := New(srv2.URL, srv2.Client(), Options{Schedule: ms(10), MaxPolls: 5})
	job, err := c.Submit(context.Background(), "export", nil)
	if err != nil {
		t.Fatalf("Submit() error = %v", err)
	}
	if want := srv.URL + "/jobs/j-7"; job.StatusURL != want {
		t.Fatalf("StatusURL = %q, want the absolute Location kept as %q", job.StatusURL, want)
	}
	if sc2.requestCount() != 1 {
		t.Fatalf("submit host saw %d requests, want 1", sc2.requestCount())
	}
}

func TestSubmitRejectionIsATypedAPIError(t *testing.T) {
	srv, sc := newPlatform(t, step{status: 400, body: `{"error":"unknown kind \"exprot\""}`})
	c := New(srv.URL, srv.Client(), Options{Schedule: ms(10), MaxPolls: 5})
	_, err := c.Submit(context.Background(), "exprot", nil)
	var ae *APIError
	if !errors.As(err, &ae) {
		t.Fatalf("Submit() error = %v (%T), want a *APIError", err, err)
	}
	if ae.Status != 400 {
		t.Fatalf("APIError.Status = %d, want 400", ae.Status)
	}
	if !strings.Contains(ae.Body, "exprot") {
		t.Fatalf("APIError.Body = %q, want the platform's response body preserved", ae.Body)
	}
	if sc.requestCount() != 1 {
		t.Fatalf("server saw %d requests, want 1 — a rejected submit must not be polled", sc.requestCount())
	}
}

func TestRunPollsOnTheScheduleThenFetchesTheResult(t *testing.T) {
	srv, sc := newPlatform(t,
		step{status: 202, location: "/jobs/j-1", body: `{"id":"j-1","state":"queued"}`},
		step{body: `{"state":"queued"}`},
		step{body: `{"state":"running"}`},
		step{status: 303, location: "/jobs/j-1/result"},
		step{body: resultBody},
	)
	waits, sleep := recordingSleeper()
	c := New(srv.URL, srv.Client(), Options{Schedule: ms(100, 250), MaxPolls: 10, Sleep: sleep})
	got, err := c.Run(context.Background(), "export", map[string]any{"report": "quarterly"})
	if err != nil {
		t.Fatalf("Run() error = %v", err)
	}
	if !reflect.DeepEqual(got, wantResult) {
		t.Fatalf("result = %v, want %v", got, wantResult)
	}
	want := []string{
		"POST /jobs",
		"GET /jobs/j-1",
		"GET /jobs/j-1",
		"GET /jobs/j-1",
		"GET /jobs/j-1/result",
	}
	if !reflect.DeepEqual(sc.requests(), want) {
		t.Fatalf("requests = %v, want %v", sc.requests(), want)
	}
	if !reflect.DeepEqual(*waits, ms(100, 250, 250)) {
		t.Fatalf("sleeps = %v, want one wait before every poll: schedule then its last entry repeating %v", *waits, ms(100, 250, 250))
	}
}

func TestWaitWorksFromAStoredStatusURL(t *testing.T) {
	srv, sc := newPlatform(t,
		step{body: `{"state":"running"}`},
		step{status: 303, location: "/jobs/j-42/result"},
		step{body: resultBody},
	)
	waits, sleep := recordingSleeper()
	c := New(srv.URL, srv.Client(), Options{Schedule: ms(50), MaxPolls: 10, Sleep: sleep})
	got, err := c.Wait(context.Background(), &Job{StatusURL: srv.URL + "/jobs/j-42"})
	if err != nil {
		t.Fatalf("Wait() error = %v", err)
	}
	if !reflect.DeepEqual(got, wantResult) {
		t.Fatalf("result = %v, want %v", got, wantResult)
	}
	want := []string{"GET /jobs/j-42", "GET /jobs/j-42", "GET /jobs/j-42/result"}
	if !reflect.DeepEqual(sc.requests(), want) {
		t.Fatalf("requests = %v, want %v (no submit — Wait starts from the stored URL)", sc.requests(), want)
	}
	if !reflect.DeepEqual(*waits, ms(50, 50)) {
		t.Fatalf("sleeps = %v, want %v", *waits, ms(50, 50))
	}
}

func TestDefaultScheduleIsOneSecond(t *testing.T) {
	srv, _ := newPlatform(t,
		step{status: 202, location: "/jobs/j-2", body: `{}`},
		step{body: `{"state":"running"}`},
		step{status: 303, location: "/jobs/j-2/result"},
		step{body: resultBody},
	)
	waits, sleep := recordingSleeper()
	c := New(srv.URL, srv.Client(), Options{MaxPolls: 10, Sleep: sleep})
	if _, err := c.Run(context.Background(), "export", nil); err != nil {
		t.Fatalf("Run() error = %v", err)
	}
	if want := []time.Duration{time.Second, time.Second}; !reflect.DeepEqual(*waits, want) {
		t.Fatalf("sleeps = %v, want the documented 1s default %v", *waits, want)
	}
}

func TestPollBudgetExhaustionIsATypedTimeout(t *testing.T) {
	srv, sc := newPlatform(t,
		step{status: 202, location: "/jobs/j-3", body: `{}`},
		step{body: `{"state":"queued"}`},
		step{body: `{"state":"running"}`},
		step{body: `{"state":"running"}`},
	)
	waits, sleep := recordingSleeper()
	c := New(srv.URL, srv.Client(), Options{Schedule: ms(20), MaxPolls: 3, Sleep: sleep})
	_, err := c.Run(context.Background(), "export", nil)
	var te *PollTimeoutError
	if !errors.As(err, &te) {
		t.Fatalf("Run() error = %v (%T), want a *PollTimeoutError", err, err)
	}
	if te.Polls != 3 || te.LastState != "running" {
		t.Fatalf("PollTimeoutError = %+v, want Polls=3 and the last observed state", te)
	}
	if !strings.Contains(err.Error(), "3") {
		t.Fatalf("Error() = %q, want the exhausted budget visible", err.Error())
	}
	if sc.requestCount() != 4 {
		t.Fatalf("server saw %d requests, want submit + exactly 3 polls", sc.requestCount())
	}
	if len(*waits) != 3 {
		t.Fatalf("sleeps = %v, want exactly 3 waits", *waits)
	}
}

func TestZeroMaxPollsMeansTheDocumentedDefaultOfTen(t *testing.T) {
	steps := []step{{status: 202, location: "/jobs/j-4", body: `{}`}}
	for i := 0; i < 10; i++ {
		steps = append(steps, step{body: `{"state":"queued"}`})
	}
	srv, sc := newPlatform(t, steps...)
	_, sleep := recordingSleeper()
	c := New(srv.URL, srv.Client(), Options{Schedule: ms(5), Sleep: sleep})
	_, err := c.Run(context.Background(), "export", nil)
	var te *PollTimeoutError
	if !errors.As(err, &te) || te.Polls != 10 {
		t.Fatalf("Run() error = %v, want a *PollTimeoutError after the default 10 polls", err)
	}
	if sc.requestCount() != 11 {
		t.Fatalf("server saw %d requests, want submit + 10 polls", sc.requestCount())
	}
}

func TestFailedJobIsTypedAndStopsPollingImmediately(t *testing.T) {
	srv, sc := newPlatform(t,
		step{status: 202, location: "/jobs/j-5", body: `{}`},
		step{body: `{"state":"failed","error":"disk quota exceeded on worker 7"}`},
	)
	_, sleep := recordingSleeper()
	c := New(srv.URL, srv.Client(), Options{Schedule: ms(20), MaxPolls: 10, Sleep: sleep})
	_, err := c.Run(context.Background(), "export", nil)
	var fe *JobFailedError
	if !errors.As(err, &fe) {
		t.Fatalf("Run() error = %v (%T), want a *JobFailedError", err, err)
	}
	if !strings.Contains(fe.Message, "disk quota exceeded on worker 7") {
		t.Fatalf("Message = %q, want the platform's error detail", fe.Message)
	}
	if sc.requestCount() != 2 {
		t.Fatalf("server saw %d requests, want submit + exactly 1 poll — failed is terminal", sc.requestCount())
	}
}

func TestExpiredJobIsTyped(t *testing.T) {
	srv, sc := newPlatform(t,
		step{status: 202, location: "/jobs/j-6", body: `{}`},
		step{body: `{"state":"expired"}`},
	)
	_, sleep := recordingSleeper()
	c := New(srv.URL, srv.Client(), Options{Schedule: ms(20), MaxPolls: 10, Sleep: sleep})
	_, err := c.Run(context.Background(), "export", nil)
	var ee *JobExpiredError
	if !errors.As(err, &ee) {
		t.Fatalf("Run() error = %v (%T), want a *JobExpiredError", err, err)
	}
	if sc.requestCount() != 2 {
		t.Fatalf("server saw %d requests, want submit + exactly 1 poll — expired is terminal", sc.requestCount())
	}
}

func TestUnknownStateSurfacesInTheError(t *testing.T) {
	srv, _ := newPlatform(t,
		step{status: 202, location: "/jobs/j-8", body: `{}`},
		step{body: `{"state":"paused"}`},
	)
	_, sleep := recordingSleeper()
	c := New(srv.URL, srv.Client(), Options{Schedule: ms(20), MaxPolls: 10, Sleep: sleep})
	_, err := c.Run(context.Background(), "export", nil)
	if err == nil || !strings.Contains(err.Error(), "paused") {
		t.Fatalf("Run() error = %v, want a failure naming the unrecognized state", err)
	}
}

func TestPollServerErrorIsATypedAPIErrorAndStops(t *testing.T) {
	srv, sc := newPlatform(t,
		step{status: 202, location: "/jobs/j-9", body: `{}`},
		step{body: `{"state":"running"}`},
		step{status: 500, body: `{"error":"status backend unavailable"}`},
	)
	_, sleep := recordingSleeper()
	c := New(srv.URL, srv.Client(), Options{Schedule: ms(20), MaxPolls: 10, Sleep: sleep})
	_, err := c.Run(context.Background(), "export", nil)
	var ae *APIError
	if !errors.As(err, &ae) || ae.Status != 500 {
		t.Fatalf("Run() error = %v (%T), want a *APIError with the 500", err, err)
	}
	if sc.requestCount() != 3 {
		t.Fatalf("server saw %d requests, want the loop to stop at the failing poll", sc.requestCount())
	}
}

func TestResultFetchFailureIsATypedAPIError(t *testing.T) {
	srv, sc := newPlatform(t,
		step{status: 202, location: "/jobs/j-10", body: `{}`},
		step{status: 303, location: "/jobs/j-10/result"},
		step{status: 500, body: `{"error":"result blob missing"}`},
	)
	_, sleep := recordingSleeper()
	c := New(srv.URL, srv.Client(), Options{Schedule: ms(20), MaxPolls: 10, Sleep: sleep})
	_, err := c.Run(context.Background(), "export", nil)
	var ae *APIError
	if !errors.As(err, &ae) || ae.Status != 500 {
		t.Fatalf("Run() error = %v (%T), want a *APIError from the result fetch", err, err)
	}
	if !strings.Contains(ae.Body, "result blob missing") {
		t.Fatalf("APIError.Body = %q, want the result endpoint's body", ae.Body)
	}
	if sc.requestCount() != 3 {
		t.Fatalf("server saw %d requests, want submit + poll + one result fetch", sc.requestCount())
	}
}

func TestCancellationDuringAWaitStopsPromptly(t *testing.T) {
	srv, sc := newPlatform(t,
		step{status: 202, location: "/jobs/j-11", body: `{}`},
		step{body: `{"state":"running"}`},
	)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	calls := 0
	sleep := func(c context.Context, d time.Duration) error {
		calls++
		if calls == 2 {
			cancel()
			return c.Err()
		}
		return nil
	}
	c := New(srv.URL, srv.Client(), Options{Schedule: ms(20), MaxPolls: 10, Sleep: sleep})
	_, err := c.Run(ctx, "export", nil)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("Run() error = %v, want the context cancellation surfaced (errors.Is context.Canceled)", err)
	}
	if sc.requestCount() != 2 {
		t.Fatalf("server saw %d requests, want no polls after the cancelled wait", sc.requestCount())
	}
	if calls != 2 {
		t.Fatalf("sleeper called %d times, want exactly 2", calls)
	}
}
