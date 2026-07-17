package stockclient

// Acceptance tests for the stockkeeper client.
//
// A scripted httptest server counts every inbound request so we can assert
// exactly how many times each adjustment was sent. The 500 + <ErrInfo>
// response is the critical case: it must surface as *PermanentError after
// exactly one request — the service already processed that record.

import (
	"encoding/xml"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
)

// errInfoBody returns a raw XML <ErrInfo> body for use in scripted responses.
func errInfoBody(code, detail string) []byte {
	type ei struct {
		XMLName xml.Name `xml:"ErrInfo"`
		Code    string   `xml:"Code"`
		Detail  string   `xml:"Detail"`
	}
	b, _ := xml.Marshal(ei{Code: code, Detail: detail})
	return b
}

// appliedBody returns a raw XML <AdjustResponse> body.
func appliedBody(recordID, ref string) []byte {
	type ar struct {
		XMLName  xml.Name `xml:"AdjustResponse"`
		RecordID string   `xml:"RecordId"`
		Status   string   `xml:"Status"`
		Ref      string   `xml:"Ref"`
	}
	b, _ := xml.Marshal(ar{RecordID: recordID, Status: "applied", Ref: ref})
	return b
}

func testAdjust(id string) AdjustRequest {
	return AdjustRequest{RecordID: id, Location: "A-04", Delta: 12}
}

// TestAppliedAdjustmentIsSentOnce: on a 200 the adjustment goes through with
// no extra requests.
func TestAppliedAdjustmentIsSentOnce(t *testing.T) {
	var count int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt64(&count, 1)
		w.Header().Set("Content-Type", "application/xml")
		w.WriteHeader(http.StatusOK)
		w.Write(appliedBody("REC-100", "TXN-1"))
	}))
	t.Cleanup(srv.Close)

	c := New(srv.URL, srv.Client(), 3)
	resp, err := c.Apply(testAdjust("REC-100"))
	if err != nil {
		t.Fatalf("Apply returned error: %v", err)
	}
	if resp.Ref != "TXN-1" {
		t.Fatalf("Ref = %q, want TXN-1", resp.Ref)
	}
	if n := atomic.LoadInt64(&count); n != 1 {
		t.Fatalf("service received %d requests, want exactly 1", n)
	}
}

// TestApplicationErrorIsNotRetried: a 500 carrying an <ErrInfo> body means
// the service processed the record and could not apply it. The client must
// stop after one request. This is the symptom from the ops report: failing
// records show up three times in the access log.
func TestApplicationErrorIsNotRetried(t *testing.T) {
	var count int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := atomic.AddInt64(&count, 1)
		w.Header().Set("Content-Type", "application/xml")
		w.WriteHeader(http.StatusInternalServerError)
		w.Write(errInfoBody("UNKNOWN_LOCATION", fmt.Sprintf("location B-914 is not in the warehouse layout (attempt %d)", n)))
	}))
	t.Cleanup(srv.Close)

	c := New(srv.URL, srv.Client(), 2) // retries configured, but none should happen
	_, err := c.Apply(testAdjust("REC-200"))
	if err == nil {
		t.Fatal("Apply should have returned an error for an application error response")
	}
	if _, ok := err.(*PermanentError); !ok {
		t.Fatalf("expected *PermanentError, got %T: %v", err, err)
	}
	perr := err.(*PermanentError)
	if perr.Code != "UNKNOWN_LOCATION" {
		t.Fatalf("Code = %q, want UNKNOWN_LOCATION", perr.Code)
	}
	// The critical assertion: the service must receive the record exactly once.
	if n := atomic.LoadInt64(&count); n != 1 {
		t.Fatalf("service received %d requests for a record it already processed, want exactly 1", n)
	}
}

// TestPlainServerErrorIsRetried: a 5xx without an <ErrInfo> body is a real
// transient failure and should be retried.
func TestPlainServerErrorIsRetried(t *testing.T) {
	const wantAttempts = 3 // initial + 2 retries out of maxRetries=2
	var count int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt64(&count, 1)
		w.Header().Set("Content-Type", "text/plain")
		w.WriteHeader(http.StatusServiceUnavailable)
		// Plain text body — no ErrInfo XML
		w.Write([]byte("upstream unavailable"))
	}))
	t.Cleanup(srv.Close)

	c := New(srv.URL, srv.Client(), wantAttempts-1)
	_, err := c.Apply(testAdjust("REC-300"))
	if err == nil {
		t.Fatal("expected an error after all retries exhausted on a 503")
	}
	if _, ok := err.(*PermanentError); ok {
		t.Fatal("a plain 503 without an ErrInfo body must not be treated as an application error")
	}
	if n := atomic.LoadInt64(&count); n != int64(wantAttempts) {
		t.Fatalf("service received %d requests, want %d (initial + %d retries)",
			n, wantAttempts, wantAttempts-1)
	}
}

// TestErrorCodeAndDetailArePreserved: the error object carries both fields.
func TestErrorCodeAndDetailArePreserved(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/xml")
		w.WriteHeader(http.StatusInternalServerError)
		w.Write(errInfoBody("NEGATIVE_ON_HAND", "delta would take on-hand below zero"))
	}))
	t.Cleanup(srv.Close)

	c := New(srv.URL, srv.Client(), 0)
	_, err := c.Apply(testAdjust("REC-400"))
	perr, ok := err.(*PermanentError)
	if !ok {
		t.Fatalf("expected *PermanentError, got %T: %v", err, err)
	}
	if perr.Code != "NEGATIVE_ON_HAND" {
		t.Errorf("Code = %q, want NEGATIVE_ON_HAND", perr.Code)
	}
	if perr.Detail != "delta would take on-hand below zero" {
		t.Errorf("Detail = %q, want the on-hand message", perr.Detail)
	}
}

// TestTransientThenSuccess: a transient failure followed by a success
// resolves correctly.
func TestTransientThenSuccess(t *testing.T) {
	var count int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := atomic.AddInt64(&count, 1)
		w.Header().Set("Content-Type", "application/xml")
		if n == 1 {
			// First attempt: plain 503 (transient, no ErrInfo body)
			w.WriteHeader(http.StatusServiceUnavailable)
			w.Write([]byte("temporarily unavailable"))
			return
		}
		w.WriteHeader(http.StatusOK)
		w.Write(appliedBody("REC-500", "TXN-5"))
	}))
	t.Cleanup(srv.Close)

	c := New(srv.URL, srv.Client(), 2)
	resp, err := c.Apply(testAdjust("REC-500"))
	if err != nil {
		t.Fatalf("Apply returned error: %v", err)
	}
	if resp.Ref != "TXN-5" {
		t.Fatalf("Ref = %q, want TXN-5", resp.Ref)
	}
	if n := atomic.LoadInt64(&count); n != 2 {
		t.Fatalf("service received %d requests, want 2 (one transient failure + one success)", n)
	}
}

// TestApplicationErrorWithGenerousRetriesConfigured: even with a high retry
// budget, an application error response gets exactly one request.
func TestApplicationErrorWithGenerousRetriesConfigured(t *testing.T) {
	var count int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt64(&count, 1)
		w.Header().Set("Content-Type", "application/xml")
		w.WriteHeader(http.StatusInternalServerError)
		w.Write(errInfoBody("RECORD_LOCKED", "record is locked by an open stocktake"))
	}))
	t.Cleanup(srv.Close)

	c := New(srv.URL, srv.Client(), 5) // generous budget to expose any repeat
	_, err := c.Apply(testAdjust("REC-600"))
	if _, ok := err.(*PermanentError); !ok {
		t.Fatalf("expected *PermanentError, got %T", err)
	}
	if n := atomic.LoadInt64(&count); n != 1 {
		t.Fatalf("service received %d requests with maxRetries=5, want exactly 1 for an application error", n)
	}
}
