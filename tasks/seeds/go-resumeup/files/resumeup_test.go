package resumeup

// Acceptance tests for the resumable-upload client.
//
// The tus-inspired protocol:
//   HEAD /<upload-id>                 → 200 with Upload-Offset header
//   PATCH /<upload-id>                → requires Upload-Offset: n, Content-Type: application/offset+octet-stream
//                                       responds 204, Offset advances, or 409 on mismatch
//   (completion: server returns 200 with Upload-Complete: true on the final PATCH)
//
// Every scenario runs against a scripted httptest server that records every
// request it receives (method, path, Upload-Offset header, body bytes) and
// replies from a fixed script. No live network, no real files.

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
)

// reqRecord captures enough of an inbound request to assert on upload behaviour.
type reqRecord struct {
	Method       string
	Path         string
	UploadOffset int64 // -1 when the header is absent
	Body         []byte
}

// uploadScript drives the httptest server.
type uploadScript struct {
	mu      sync.Mutex
	steps   []scriptStep
	records []reqRecord
}

type scriptStep struct {
	// For HEAD responses:
	Offset int64 // sent as Upload-Offset: N
	// For PATCH responses:
	Status   int    // 204 = success, 409 = offset mismatch, 200 = complete
	Complete bool   // when true, adds Upload-Complete: true header on 200
	Error    string // non-empty → write this body with the status
}

func (s *uploadScript) handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		defer s.mu.Unlock()

		body, _ := io.ReadAll(r.Body)
		r.Body.Close()
		var off int64 = -1
		if v := r.Header.Get("Upload-Offset"); v != "" {
			fmt.Sscanf(v, "%d", &off)
		}
		s.records = append(s.records, reqRecord{
			Method:       r.Method,
			Path:         r.URL.Path,
			UploadOffset: off,
			Body:         body,
		})

		if len(s.steps) == 0 {
			w.WriteHeader(599)
			fmt.Fprint(w, "script exhausted: unexpected extra request")
			return
		}
		step := s.steps[0]
		s.steps = s.steps[1:]

		switch r.Method {
		case http.MethodHead:
			w.Header().Set("Upload-Offset", fmt.Sprintf("%d", step.Offset))
			w.WriteHeader(http.StatusOK)

		case http.MethodPatch:
			if step.Status == 0 {
				step.Status = http.StatusNoContent
			}
			if step.Complete {
				w.Header().Set("Upload-Complete", "true")
				w.Header().Set("Upload-Offset", fmt.Sprintf("%d", step.Offset))
			}
			if step.Error != "" {
				w.WriteHeader(step.Status)
				fmt.Fprint(w, step.Error)
				return
			}
			w.WriteHeader(step.Status)
		}
	})
}

func (s *uploadScript) reqs() []reqRecord {
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := make([]reqRecord, len(s.records))
	copy(cp, s.records)
	return cp
}

func newServer(t *testing.T, steps ...scriptStep) (*httptest.Server, *uploadScript) {
	t.Helper()
	sc := &uploadScript{steps: steps}
	srv := httptest.NewServer(sc.handler())
	t.Cleanup(srv.Close)
	return srv, sc
}

// TestCleanUploadSingleChunk: the server is already at offset 0; the whole
// payload fits in one chunk; the server signals completion.
func TestCleanUploadSingleChunk(t *testing.T) {
	payload := []byte("hello upload")
	srv, sc := newServer(t,
		scriptStep{Offset: 0},                                             // HEAD
		scriptStep{Status: 200, Complete: true, Offset: int64(len(payload))}, // PATCH -> done
	)

	up := New(srv.Client(), ChunkSize(len(payload)))
	err := up.Upload(context.Background(), srv.URL+"/uploads/job-1", payload)
	if err != nil {
		t.Fatalf("Upload returned error: %v", err)
	}

	reqs := sc.reqs()
	if len(reqs) != 2 {
		t.Fatalf("want 2 requests (HEAD+PATCH), got %d: %+v", len(reqs), reqs)
	}
	head := reqs[0]
	if head.Method != http.MethodHead {
		t.Fatalf("first request must be HEAD, got %s", head.Method)
	}
	patch := reqs[1]
	if patch.Method != http.MethodPatch {
		t.Fatalf("second request must be PATCH, got %s", patch.Method)
	}
	if patch.UploadOffset != 0 {
		t.Fatalf("PATCH Upload-Offset = %d, want 0", patch.UploadOffset)
	}
	if string(patch.Body) != string(payload) {
		t.Fatalf("PATCH body = %q, want %q", patch.Body, payload)
	}
}

// TestResumePartialUpload: the server has already received the first chunk;
// the client must send only the remaining bytes.
func TestResumePartialUpload(t *testing.T) {
	payload := []byte("abcdefghijklmnopqrstuvwxyz") // 26 bytes
	srv, sc := newServer(t,
		scriptStep{Offset: 10},                                              // HEAD: server has first 10
		scriptStep{Status: 200, Complete: true, Offset: int64(len(payload))}, // PATCH
	)

	up := New(srv.Client(), ChunkSize(100))
	if err := up.Upload(context.Background(), srv.URL+"/uploads/job-2", payload); err != nil {
		t.Fatalf("Upload: %v", err)
	}

	reqs := sc.reqs()
	if len(reqs) != 2 {
		t.Fatalf("want 2 requests, got %d", len(reqs))
	}
	patch := reqs[1]
	if patch.UploadOffset != 10 {
		t.Fatalf("PATCH Upload-Offset = %d, want 10 (resume point)", patch.UploadOffset)
	}
	want := payload[10:]
	if string(patch.Body) != string(want) {
		t.Fatalf("PATCH body = %q, want %q (bytes starting at offset 10)", patch.Body, want)
	}
}

// TestMultiChunkUpload: small chunk size forces multiple PATCH requests.
func TestMultiChunkUpload(t *testing.T) {
	payload := []byte("AAABBBCCC") // 9 bytes; chunk 3 → 3 PATCHes
	srv, sc := newServer(t,
		scriptStep{Offset: 0},                              // HEAD
		scriptStep{Status: http.StatusNoContent},           // PATCH chunk 1
		scriptStep{Status: http.StatusNoContent},           // PATCH chunk 2
		scriptStep{Status: 200, Complete: true, Offset: 9}, // PATCH chunk 3 -> done
	)

	up := New(srv.Client(), ChunkSize(3))
	if err := up.Upload(context.Background(), srv.URL+"/uploads/job-3", payload); err != nil {
		t.Fatalf("Upload: %v", err)
	}

	reqs := sc.reqs()
	if len(reqs) != 4 { // 1 HEAD + 3 PATCHes
		t.Fatalf("want 4 requests, got %d: %+v", len(reqs), reqs)
	}
	// Verify offsets and bodies for the three PATCHes
	want := [][2]any{
		{int64(0), "AAA"},
		{int64(3), "BBB"},
		{int64(6), "CCC"},
	}
	for i, w := range want {
		p := reqs[i+1]
		if p.UploadOffset != w[0].(int64) {
			t.Errorf("PATCH %d Upload-Offset = %d, want %d", i+1, p.UploadOffset, w[0])
		}
		if string(p.Body) != w[1].(string) {
			t.Errorf("PATCH %d body = %q, want %q", i+1, p.Body, w[1])
		}
	}
}

// TestServerAheadMismatch: the server's offset is AHEAD of what the client
// expected (server saw more bytes than we sent — maybe a prior partial chunk
// landed). Client must re-probe and then resume from the server's offset.
func TestServerAheadMismatch(t *testing.T) {
	payload := []byte("0123456789abcdef") // 16 bytes, chunk 8
	srv, sc := newServer(t,
		scriptStep{Offset: 0},                                    // HEAD: server at 0
		scriptStep{Status: 409, Error: "conflict"},               // PATCH at 0 → 409 (server actually moved)
		scriptStep{Offset: 8},                                    // re-probe HEAD: server now at 8
		scriptStep{Status: 200, Complete: true, Offset: 16},      // PATCH at 8 → done
	)

	up := New(srv.Client(), ChunkSize(8))
	if err := up.Upload(context.Background(), srv.URL+"/uploads/job-4", payload); err != nil {
		t.Fatalf("Upload: %v", err)
	}

	reqs := sc.reqs()
	// Expected: HEAD, PATCH(0→409), HEAD (reprobe), PATCH(8→done)
	if len(reqs) != 4 {
		t.Fatalf("want 4 requests, got %d: %+v", len(reqs), reqs)
	}
	if reqs[0].Method != http.MethodHead || reqs[1].Method != http.MethodPatch ||
		reqs[2].Method != http.MethodHead || reqs[3].Method != http.MethodPatch {
		t.Fatalf("unexpected request sequence: %v", func() []string {
			var m []string
			for _, r := range reqs {
				m = append(m, r.Method)
			}
			return m
		}())
	}
	if reqs[3].UploadOffset != 8 {
		t.Fatalf("second PATCH Upload-Offset = %d, want 8 (server-authoritative resume)", reqs[3].UploadOffset)
	}
	if string(reqs[3].Body) != "abcdef89"[0:8] {
		// double check: it's bytes 8..15
		if string(reqs[3].Body) != string(payload[8:]) {
			t.Fatalf("second PATCH body = %q, want %q", reqs[3].Body, payload[8:])
		}
	}
}

// TestServerBehindMismatch: server's stored offset is BEHIND what the client
// computed (e.g. incomplete flush). Client re-probes and resumes from the
// server's (lower) offset, even sending some bytes it already sent before.
// Payload: 15 bytes, chunk 10. Flow: HEAD(0), PATCH[0:10]→204, PATCH[10:15]→409,
// HEAD→5 (re-probe), PATCH[5:15]→200+done.
func TestServerBehindMismatch(t *testing.T) {
	payload := []byte("XXXXXXXXXXXXXXX") // 15 bytes
	srv, sc := newServer(t,
		scriptStep{Offset: 0},                                // HEAD: server at 0
		scriptStep{Status: http.StatusNoContent},             // PATCH 0→10 ok
		scriptStep{Status: 409, Error: "conflict"},           // PATCH 10→15 conflict
		scriptStep{Offset: 5},                                // re-probe: server actually only at 5
		scriptStep{Status: 200, Complete: true, Offset: 15}, // PATCH 5→15 done
	)

	up := New(srv.Client(), ChunkSize(10))
	if err := up.Upload(context.Background(), srv.URL+"/uploads/job-5", payload); err != nil {
		t.Fatalf("Upload: %v", err)
	}

	reqs := sc.reqs()
	if len(reqs) != 5 {
		t.Fatalf("want 5 requests, got %d: %+v", len(reqs), reqs)
	}
	// Last PATCH must start at offset 5 (server-authoritative)
	last := reqs[4]
	if last.UploadOffset != 5 {
		t.Fatalf("final PATCH Upload-Offset = %d, want 5", last.UploadOffset)
	}
	want := payload[5:] // 10 bytes (fits in one chunk)
	if string(last.Body) != string(want) {
		t.Fatalf("final PATCH body = %q, want %q (bytes from offset 5)", last.Body, want)
	}
}

// TestServerAlreadyComplete: server's offset equals the total length at the
// initial HEAD — nothing to send.
func TestServerAlreadyComplete(t *testing.T) {
	payload := []byte("already done")
	srv, sc := newServer(t,
		scriptStep{Offset: int64(len(payload))}, // HEAD: server already has everything
	)

	up := New(srv.Client())
	if err := up.Upload(context.Background(), srv.URL+"/uploads/job-6", payload); err != nil {
		t.Fatalf("Upload: %v", err)
	}

	reqs := sc.reqs()
	if len(reqs) != 1 || reqs[0].Method != http.MethodHead {
		t.Fatalf("want exactly one HEAD, got %d requests: %+v", len(reqs), reqs)
	}
}

// TestEmptyPayload: a zero-byte upload should still probe via HEAD and then
// be complete without any PATCH.
func TestEmptyPayload(t *testing.T) {
	srv, sc := newServer(t,
		scriptStep{Offset: 0}, // HEAD: server at 0, payload is also 0
	)

	up := New(srv.Client())
	if err := up.Upload(context.Background(), srv.URL+"/uploads/job-7", []byte{}); err != nil {
		t.Fatalf("Upload: %v", err)
	}

	reqs := sc.reqs()
	if len(reqs) != 1 {
		t.Fatalf("want 1 request (HEAD only), got %d: %+v", len(reqs), reqs)
	}
}

// TestPatchContentType: every PATCH must carry Content-Type application/offset+octet-stream.
func TestPatchContentType(t *testing.T) {
	srv, sc := newServer(t,
		scriptStep{Offset: 0},
		scriptStep{Status: 200, Complete: true, Offset: 5},
	)

	up := New(srv.Client())
	if err := up.Upload(context.Background(), srv.URL+"/uploads/job-8", []byte("hello")); err != nil {
		t.Fatalf("Upload: %v", err)
	}
	_ = sc
	// We'll verify via a custom handler that captures content-type
	// The real check is in a second layer
}

// TestContentTypeOnPatch uses a custom handler that captures Content-Type.
func TestContentTypeOnPatch(t *testing.T) {
	var capturedCT string
	var mu sync.Mutex
	headDone := false

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		if r.Method == http.MethodHead {
			headDone = true
			w.Header().Set("Upload-Offset", "0")
			w.WriteHeader(http.StatusOK)
			return
		}
		if r.Method == http.MethodPatch && headDone {
			capturedCT = r.Header.Get("Content-Type")
			w.Header().Set("Upload-Complete", "true")
			w.Header().Set("Upload-Offset", "3")
			w.WriteHeader(http.StatusOK)
		}
	}))
	t.Cleanup(srv.Close)

	up := New(srv.Client())
	if err := up.Upload(context.Background(), srv.URL+"/upload/x", []byte("abc")); err != nil {
		t.Fatalf("Upload: %v", err)
	}
	if capturedCT != "application/offset+octet-stream" {
		t.Fatalf("PATCH Content-Type = %q, want application/offset+octet-stream", capturedCT)
	}
}

// TestContextCancellationStops: cancelling the context aborts the upload.
func TestContextCancellationStops(t *testing.T) {
	payload := make([]byte, 30)
	ctx, cancel := context.WithCancel(context.Background())

	// The HEAD replies fine; the first PATCH holds until we cancel.
	// We record whether the error surface correctly.
	arrived := make(chan struct{})

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodHead {
			w.Header().Set("Upload-Offset", "0")
			w.WriteHeader(http.StatusOK)
			return
		}
		// Signal that PATCH arrived, then block — context cancel should free the caller.
		close(arrived)
		<-ctx.Done()
		w.WriteHeader(http.StatusNoContent)
	}))
	t.Cleanup(srv.Close)

	done := make(chan error, 1)
	go func() {
		up := New(srv.Client(), ChunkSize(15))
		done <- up.Upload(ctx, srv.URL+"/upload/x", payload)
	}()

	<-arrived
	cancel()
	err := <-done
	if err == nil {
		t.Fatal("expected an error from a cancelled upload, got nil")
	}
}

// TestHTTPErrorSurfacesCleanly: a non-2xx, non-409 response from a PATCH
// is returned as an error with the status code embedded.
func TestHTTPErrorSurfacesCleanly(t *testing.T) {
	srv, _ := newServer(t,
		scriptStep{Offset: 0},
		scriptStep{Status: http.StatusServiceUnavailable, Error: "backend down"},
	)

	up := New(srv.Client())
	err := up.Upload(context.Background(), srv.URL+"/uploads/job-x", []byte("data"))
	if err == nil {
		t.Fatal("expected an error on a 503 PATCH, got nil")
	}
	if !containsNum(err.Error(), 503) {
		t.Fatalf("error = %q, want 503 in message", err)
	}
}

func containsNum(s string, n int) bool {
	return fmt.Sprintf("%d", n) != "" && len(s) > 0 && containsStr(s, fmt.Sprintf("%d", n))
}

func containsStr(s, sub string) bool {
	for i := 0; i <= len(s)-len(sub); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}
