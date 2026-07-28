package correlation_boundary_test

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"example.com/correlation-boundary/internal/correlation"
	"example.com/correlation-boundary/internal/httpapi"
	"example.com/correlation-boundary/internal/jobs"
	"example.com/correlation-boundary/internal/telemetry"
)

func sourceOf(id string) correlation.Source {
	return func() (string, error) { return id, nil }
}

func requestExport(t *testing.T, handler http.Handler, values []string, account string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/exports?account="+account, nil)
	if values != nil {
		req.Header[http.CanonicalHeaderKey(correlation.Header)] = append([]string(nil), values...)
	}
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	return rec
}

func TestValidUsesBoundedASCIIGrammar(t *testing.T) {
	valid := []string{
		"a234567b",
		"client-ABC_1234",
		"01HV7YV0Y4.tenant_export-9",
		strings.Repeat("z", 64),
	}
	for _, id := range valid {
		if !correlation.Valid(id) {
			t.Errorf("Valid(%q) = false", id)
		}
	}

	invalid := []string{
		"",
		"1234567",
		strings.Repeat("x", 65),
		"-1234567",
		"1234567-",
		"abc defgh",
		"abc/defgh",
		"abc,defgh",
		"abc\r\ndef",
		"ébcdefgh",
	}
	for _, id := range invalid {
		if correlation.Valid(id) {
			t.Errorf("Valid(%q) = true", id)
		}
	}
}

func TestHTTPBoundaryAcceptsOneValidValueAndSnapshotsIt(t *testing.T) {
	queue := &jobs.MemoryQueue{}
	log := &telemetry.Recorder{}
	var generated atomic.Int32
	handler := httpapi.ExportRoute(func() (string, error) {
		generated.Add(1)
		return "generated-ID-999", nil
	}, queue, log)

	const incoming = "client.req_12345"
	rec := requestExport(t, handler, []string{incoming}, "acme")

	if rec.Code != http.StatusAccepted {
		t.Fatalf("status = %d, want %d", rec.Code, http.StatusAccepted)
	}
	if got := rec.Result().Header.Get(correlation.Header); got != incoming {
		t.Fatalf("response correlation = %q, want %q", got, incoming)
	}
	if got := generated.Load(); got != 0 {
		t.Fatalf("source called %d times for valid inbound id", got)
	}
	gotJobs := queue.Jobs()
	if len(gotJobs) != 1 || gotJobs[0].CorrelationID != incoming {
		t.Fatalf("jobs = %#v, want one with correlation %q", gotJobs, incoming)
	}
	gotLogs := log.Entries()
	if len(gotLogs) != 1 || gotLogs[0].Event != "export.accepted" || gotLogs[0].CorrelationID != incoming {
		t.Fatalf("logs = %#v, want accepted with correlation %q", gotLogs, incoming)
	}
}

func TestHTTPBoundaryReplacesUntrustedAndAmbiguousValues(t *testing.T) {
	tests := []struct {
		name   string
		values []string
	}{
		{name: "missing", values: nil},
		{name: "too short", values: []string{"short"}},
		{name: "leading delimiter", values: []string{"-client-1234"}},
		{name: "whitespace", values: []string{"client id 123"}},
		{name: "control characters", values: []string{"client\r\nInjected: yes"}},
		{name: "unicode", values: []string{"client-é-123"}},
		{name: "two valid fields", values: []string{"client-one-123", "client-two-456"}},
		{name: "valid plus hostile", values: []string{"client-one-123", "hostile value"}},
	}

	for i, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			generated := fmt.Sprintf("generated-%02d-ID", i)
			queue := &jobs.MemoryQueue{}
			log := &telemetry.Recorder{}
			var calls atomic.Int32
			handler := httpapi.ExportRoute(func() (string, error) {
				calls.Add(1)
				return generated, nil
			}, queue, log)

			rec := requestExport(t, handler, tc.values, "account")
			if rec.Code != http.StatusAccepted {
				t.Fatalf("status = %d, body = %q", rec.Code, rec.Body.String())
			}
			if got := rec.Result().Header.Get(correlation.Header); got != generated {
				t.Errorf("response correlation = %q, want %q", got, generated)
			}
			if got := calls.Load(); got != 1 {
				t.Errorf("source calls = %d, want 1", got)
			}
			gotJobs := queue.Jobs()
			if len(gotJobs) != 1 || gotJobs[0].CorrelationID != generated {
				t.Errorf("jobs = %#v", gotJobs)
			}
			gotLogs := log.Entries()
			if len(gotLogs) != 1 || gotLogs[0].CorrelationID != generated {
				t.Errorf("logs = %#v", gotLogs)
			}
			if strings.Contains(rec.Body.String(), "hostile") || rec.Result().Header.Get("Injected") != "" {
				t.Errorf("rejected metadata leaked: headers=%v body=%q", rec.Result().Header, rec.Body.String())
			}
		})
	}
}

func TestHTTPBoundaryFailsClosedWhenGenerationIsUnsafe(t *testing.T) {
	sourceFailure := errors.New("entropy temporarily unavailable")
	tests := []struct {
		name   string
		source correlation.Source
		want   error
	}{
		{
			name: "nil source",
			want: correlation.ErrNoSource,
		},
		{
			name:   "source failure",
			source: func() (string, error) { return "", sourceFailure },
			want:   sourceFailure,
		},
		{
			name:   "invalid generated value",
			source: sourceOf("../generated value\r\nX-Bad: yes"),
			want:   correlation.ErrInvalidGeneratedID,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := correlation.Ensure("bad value", tc.source); !errors.Is(err, tc.want) {
				t.Fatalf("Ensure error = %v, want errors.Is(_, %v)", err, tc.want)
			}

			queue := &jobs.MemoryQueue{}
			log := &telemetry.Recorder{}
			handler := httpapi.ExportRoute(tc.source, queue, log)
			rec := requestExport(t, handler, []string{"bad value"}, "acme")
			if rec.Code != http.StatusInternalServerError {
				t.Fatalf("status = %d, want 500", rec.Code)
			}
			if got := rec.Result().Header.Get(correlation.Header); got != "" {
				t.Errorf("unsafe response correlation = %q", got)
			}
			if len(queue.Jobs()) != 0 || len(log.Entries()) != 0 {
				t.Errorf("unsafe request reached downstream: jobs=%v logs=%v", queue.Jobs(), log.Entries())
			}
			if strings.Contains(rec.Body.String(), "generated value") || strings.Contains(rec.Body.String(), "entropy") {
				t.Errorf("failure body leaked source detail: %q", rec.Body.String())
			}
		})
	}
}

func TestContextValuesAreImmutableAndOperationScoped(t *testing.T) {
	const first = "request-first-100"
	const second = "request-second-200"
	ctxFirst := correlation.WithID(context.Background(), first)
	ctxSecond := correlation.WithID(context.Background(), second)

	if got := correlation.FromContext(ctxFirst); got != first {
		t.Errorf("first context changed to %q", got)
	}
	if got := correlation.FromContext(ctxSecond); got != second {
		t.Errorf("second context = %q", got)
	}
	if got := correlation.FromContext(context.Background()); got != "" {
		t.Errorf("background context inherited ambient id %q", got)
	}

	const count = 32
	ready := make(chan struct{}, count)
	release := make(chan struct{})
	errs := make(chan error, count)
	var wg sync.WaitGroup
	for i := 0; i < count; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			want := fmt.Sprintf("concurrent-request-%02d", i)
			ctx := correlation.WithID(context.Background(), want)
			ready <- struct{}{}
			<-release
			if got := correlation.FromContext(ctx); got != want {
				errs <- fmt.Errorf("context %d = %q, want %q", i, got, want)
			}
		}(i)
	}
	for i := 0; i < count; i++ {
		<-ready
	}
	close(release)
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Error(err)
	}
}

func TestWorkerUsesEachSerializedJobCorrelation(t *testing.T) {
	queue := &jobs.MemoryQueue{}
	requestLog := &telemetry.Recorder{}
	handler := httpapi.ExportRoute(sourceOf("unused-source-999"), queue, requestLog)

	firstID := "request-job-first"
	secondID := "request-job-second"
	requestExport(t, handler, []string{firstID}, "first")
	requestExport(t, handler, []string{secondID}, "second")
	queued := queue.Jobs()
	if len(queued) != 2 {
		t.Fatalf("queued %d jobs, want 2", len(queued))
	}

	workerLog := &telemetry.Recorder{}
	worker := jobs.Worker{
		Source: func() (string, error) {
			return "", errors.New("valid queued ids must not generate")
		},
		Log: workerLog,
	}
	type observed struct {
		account string
		context string
		job     string
	}
	var observations []observed
	for _, job := range []jobs.Job{queued[1], queued[0]} {
		err := worker.Process(context.Background(), job, func(ctx context.Context, got jobs.Job) error {
			observations = append(observations, observed{
				account: got.Account,
				context: correlation.FromContext(ctx),
				job:     got.CorrelationID,
			})
			return nil
		})
		if err != nil {
			t.Fatalf("Process(%s): %v", job.Account, err)
		}
	}

	want := []observed{
		{account: "second", context: secondID, job: secondID},
		{account: "first", context: firstID, job: firstID},
	}
	if fmt.Sprint(observations) != fmt.Sprint(want) {
		t.Errorf("runner observations = %#v, want %#v", observations, want)
	}

	entries := workerLog.Entries()
	if len(entries) != 4 {
		t.Fatalf("worker entries = %#v", entries)
	}
	wantEvents := []struct {
		event   string
		account string
		id      string
	}{
		{"export.started", "second", secondID},
		{"export.completed", "second", secondID},
		{"export.started", "first", firstID},
		{"export.completed", "first", firstID},
	}
	for i, want := range wantEvents {
		got := entries[i]
		if got.Event != want.event || got.Account != want.account || got.CorrelationID != want.id {
			t.Errorf("entry[%d] = %#v, want event=%q account=%q id=%q", i, got, want.event, want.account, want.id)
		}
	}
}

func TestWorkerRevalidatesJobBoundaryAndPreservesFailure(t *testing.T) {
	t.Run("invalid serialized id is replaced", func(t *testing.T) {
		const generated = "worker-generated-100"
		log := &telemetry.Recorder{}
		var sourceCalls atomic.Int32
		worker := jobs.Worker{
			Source: func() (string, error) {
				sourceCalls.Add(1)
				return generated, nil
			},
			Log: log,
		}

		err := worker.Process(context.Background(), jobs.Job{
			Account:       "acme",
			CorrelationID: "hostile job id",
		}, func(ctx context.Context, job jobs.Job) error {
			if got := correlation.FromContext(ctx); got != generated {
				t.Errorf("runner context = %q, want %q", got, generated)
			}
			if job.CorrelationID != generated {
				t.Errorf("runner job correlation = %q, want %q", job.CorrelationID, generated)
			}
			return nil
		})
		if err != nil {
			t.Fatalf("Process: %v", err)
		}
		if sourceCalls.Load() != 1 {
			t.Errorf("source calls = %d, want 1", sourceCalls.Load())
		}
		for i, entry := range log.Entries() {
			if entry.CorrelationID != generated {
				t.Errorf("entry[%d] correlation = %q, want %q", i, entry.CorrelationID, generated)
			}
		}
	})

	t.Run("runner failure retains identity and correlation", func(t *testing.T) {
		runFailure := errors.New("export backend failed")
		log := &telemetry.Recorder{}
		worker := jobs.Worker{Source: sourceOf("unused-worker-100"), Log: log}
		err := worker.Process(context.Background(), jobs.Job{
			Account:       "failure-account",
			CorrelationID: "worker-failure-200",
		}, func(context.Context, jobs.Job) error {
			return runFailure
		})
		if !errors.Is(err, runFailure) {
			t.Fatalf("Process error = %v, want runner identity", err)
		}
		entries := log.Entries()
		if len(entries) != 2 ||
			entries[0].Event != "export.started" ||
			entries[1].Event != "export.failed" ||
			entries[0].CorrelationID != "worker-failure-200" ||
			entries[1].CorrelationID != "worker-failure-200" {
			t.Fatalf("failure entries = %#v", entries)
		}
	})

	t.Run("unsafe generation stops before work and logs", func(t *testing.T) {
		for _, source := range []correlation.Source{
			nil,
			sourceOf("bad/generated/id"),
		} {
			log := &telemetry.Recorder{}
			worker := jobs.Worker{Source: source, Log: log}
			var ran atomic.Bool
			err := worker.Process(context.Background(), jobs.Job{
				Account:       "unsafe",
				CorrelationID: "",
			}, func(context.Context, jobs.Job) error {
				ran.Store(true)
				return nil
			})
			if err == nil {
				t.Error("Process error = nil")
			}
			if ran.Load() || len(log.Entries()) != 0 {
				t.Errorf("unsafe job ran=%v logs=%#v", ran.Load(), log.Entries())
			}
		}
	})
}
