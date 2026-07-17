package gcsup

// Acceptance tests for the gcsup resumable uploader. Everything runs against a
// local httptest server that speaks the Cloud Storage JSON API v1 resumable
// protocol as pinned in docs/contract.json; no real bucket, no real
// credentials, no sleeps.

import (
	"bytes"
	"context"
	"crypto/md5"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"hash/crc32"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

const (
	testBucket = "vault-archive"
	testObject = "backups/2026-07/plan a.bin"
	testToken  = "dummy-gcs-token-8843"
	testCType  = "application/octet-stream"
)

func testPayload(n int) []byte {
	b := make([]byte, n)
	s := uint32(0x2c1e4d17)
	for i := range b {
		s = s*1664525 + 1013904223
		b[i] = byte(s >> 24)
	}
	return b
}

func b64CRC32C(data []byte) string {
	sum := crc32.Checksum(data, crc32.MakeTable(crc32.Castagnoli))
	var raw [4]byte
	binary.BigEndian.PutUint32(raw[:], sum)
	return base64.StdEncoding.EncodeToString(raw[:])
}

func b64MD5(data []byte) string {
	sum := md5.Sum(data)
	return base64.StdEncoding.EncodeToString(sum[:])
}

func objectJSONWith(payload []byte, generation, size, crc string) string {
	genField := ""
	if generation != "" {
		genField = fmt.Sprintf(`"generation":%q,`, generation)
	}
	return fmt.Sprintf(`{"kind":"storage#object","name":%q,"bucket":%q,%s"metageneration":"1","size":%q,"contentType":%q,"crc32c":%q,"md5Hash":%q}`,
		testObject, testBucket, genField, size, testCType, crc, b64MD5(payload))
}

func finalObjectJSON(payload []byte) string {
	return objectJSONWith(payload, "31337000042", fmt.Sprint(len(payload)), b64CRC32C(payload))
}

type rec struct {
	method string
	path   string
	query  url.Values
	header http.Header
	body   []byte
}

type mock struct {
	mu   sync.Mutex
	reqs []rec
}

func (m *mock) count() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return len(m.reqs)
}

func (m *mock) req(i int) rec {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.reqs[i]
}

func newMock(t *testing.T, serve func(n int, r rec, w http.ResponseWriter)) (*mock, *httptest.Server) {
	t.Helper()
	m := &mock{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("reading request body: %v", err)
		}
		m.mu.Lock()
		n := len(m.reqs)
		got := rec{method: r.Method, path: r.URL.Path, query: r.URL.Query(), header: r.Header.Clone(), body: body}
		m.reqs = append(m.reqs, got)
		m.mu.Unlock()
		serve(n, got, w)
	}))
	t.Cleanup(srv.Close)
	return m, srv
}

func eq[T comparable](t *testing.T, what string, got, want T) {
	t.Helper()
	if got != want {
		t.Fatalf("%s: got %v, want %v", what, got, want)
	}
}

func mustUploader(t *testing.T, base string, chunk int64, maxRec int) *Uploader {
	t.Helper()
	u, err := NewUploader(Config{
		BaseURL:       base,
		Bucket:        testBucket,
		Token:         testToken,
		HTTPClient:    http.DefaultClient,
		ChunkSize:     chunk,
		MaxRecoveries: maxRec,
	})
	if err != nil {
		t.Fatalf("NewUploader: %v", err)
	}
	return u
}

func TestUploadHappyPathPinsWireContract(t *testing.T) {
	payload := testPayload(787432)
	var srvURL atomic.Value
	m, srv := newMock(t, func(n int, r rec, w http.ResponseWriter) {
		switch n {
		case 0:
			w.Header().Set("Location", srvURL.Load().(string)+"/upload/session/alpha?upload_id=tok-1")
			w.WriteHeader(http.StatusOK)
		case 1:
			w.Header().Set("Range", "bytes=0-524287")
			w.WriteHeader(308)
		case 2:
			w.Header().Set("Content-Type", "application/json; charset=UTF-8")
			w.WriteHeader(http.StatusOK)
			io.WriteString(w, finalObjectJSON(payload))
		default:
			w.WriteHeader(http.StatusInternalServerError)
		}
	})
	srvURL.Store(srv.URL)

	up := mustUploader(t, srv.URL, 524288, 0)
	obj, err := up.Upload(context.Background(), testObject, testCType, payload)
	if err != nil {
		t.Fatalf("Upload: %v", err)
	}

	eq(t, "request count", m.count(), 3)

	init := m.req(0)
	eq(t, "initiation method", init.method, http.MethodPost)
	eq(t, "initiation path", init.path, "/upload/storage/v1/b/"+testBucket+"/o")
	eq(t, "uploadType query parameter", init.query.Get("uploadType"), "resumable")
	eq(t, "name query parameter decodes to the object name", init.query.Get("name"), testObject)
	eq(t, "initiation bearer auth", init.header.Get("Authorization"), "Bearer "+testToken)
	if ct := init.header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		t.Fatalf("initiation Content-Type = %q, want application/json metadata", ct)
	}
	eq(t, "X-Upload-Content-Type", init.header.Get("X-Upload-Content-Type"), testCType)
	eq(t, "X-Upload-Content-Length", init.header.Get("X-Upload-Content-Length"), "787432")
	var meta map[string]any
	if err := json.Unmarshal(init.body, &meta); err != nil {
		t.Fatalf("initiation body is not JSON metadata: %v", err)
	}
	eq(t, "metadata name", fmt.Sprint(meta["name"]), testObject)
	eq(t, "metadata contentType", fmt.Sprint(meta["contentType"]), testCType)

	for i := 1; i <= 2; i++ {
		chunk := m.req(i)
		eq(t, fmt.Sprintf("chunk %d method", i), chunk.method, http.MethodPut)
		eq(t, fmt.Sprintf("chunk %d hits the session URI path", i), chunk.path, "/upload/session/alpha")
		eq(t, fmt.Sprintf("chunk %d preserves the session URI query", i), chunk.query.Get("upload_id"), "tok-1")
		eq(t, fmt.Sprintf("chunk %d bearer auth", i), chunk.header.Get("Authorization"), "Bearer "+testToken)
	}
	eq(t, "chunk 1 Content-Range", m.req(1).header.Get("Content-Range"), "bytes 0-524287/787432")
	eq(t, "chunk 2 Content-Range", m.req(2).header.Get("Content-Range"), "bytes 524288-787431/787432")
	if !bytes.Equal(m.req(1).body, payload[:524288]) {
		t.Fatalf("chunk 1 bytes differ from payload[0:524288]")
	}
	if !bytes.Equal(m.req(2).body, payload[524288:]) {
		t.Fatalf("chunk 2 bytes differ from payload[524288:]")
	}

	eq(t, "object name", obj.Name, testObject)
	eq(t, "object bucket", obj.Bucket, testBucket)
	eq(t, "generation parsed from its string wire form", obj.Generation, int64(31337000042))
	eq(t, "size parsed from its string wire form", obj.Size, int64(787432))
	eq(t, "crc32c preserved", obj.CRC32C, b64CRC32C(payload))
	eq(t, "md5Hash preserved", obj.MD5Hash, b64MD5(payload))
}

func TestInterruptionRecoversFromCommittedRange(t *testing.T) {
	payload := testPayload(787432)
	var srvURL atomic.Value
	m, srv := newMock(t, func(n int, r rec, w http.ResponseWriter) {
		switch n {
		case 0:
			w.Header().Set("Location", srvURL.Load().(string)+"/upload/session/beta?upload_id=tok-2")
			w.WriteHeader(http.StatusOK)
		case 1:
			w.WriteHeader(http.StatusServiceUnavailable)
			io.WriteString(w, `{"error":{"code":503,"message":"backend hiccup"}}`)
		case 2:
			w.Header().Set("Range", "bytes=0-262143")
			w.WriteHeader(308)
		case 3:
			w.Header().Set("Range", "bytes=0-786431")
			w.WriteHeader(308)
		case 4:
			w.WriteHeader(http.StatusOK)
			io.WriteString(w, finalObjectJSON(payload))
		default:
			w.WriteHeader(http.StatusInternalServerError)
		}
	})
	srvURL.Store(srv.URL)

	up := mustUploader(t, srv.URL, 524288, 3)
	obj, err := up.Upload(context.Background(), testObject, testCType, payload)
	if err != nil {
		t.Fatalf("Upload after interruption: %v", err)
	}

	eq(t, "request count", m.count(), 5)
	status := m.req(2)
	eq(t, "status check method", status.method, http.MethodPut)
	eq(t, "status check Content-Range", status.header.Get("Content-Range"), "bytes */787432")
	eq(t, "status check has an empty body", len(status.body), 0)

	eq(t, "resume starts after the committed range", m.req(3).header.Get("Content-Range"), "bytes 262144-786431/787432")
	if !bytes.Equal(m.req(3).body, payload[262144:786432]) {
		t.Fatalf("resumed chunk bytes differ from payload[262144:786432]")
	}
	eq(t, "final chunk Content-Range", m.req(4).header.Get("Content-Range"), "bytes 786432-787431/787432")
	if !bytes.Equal(m.req(4).body, payload[786432:]) {
		t.Fatalf("final chunk bytes differ from payload[786432:]")
	}
	eq(t, "final object generation", obj.Generation, int64(31337000042))
}

func TestRecoveryWithNothingCommittedRestartsFromZero(t *testing.T) {
	payload := testPayload(787432)
	var srvURL atomic.Value
	m, srv := newMock(t, func(n int, r rec, w http.ResponseWriter) {
		switch n {
		case 0:
			w.Header().Set("Location", srvURL.Load().(string)+"/upload/session/gamma?upload_id=tok-3")
			w.WriteHeader(http.StatusOK)
		case 1:
			w.WriteHeader(http.StatusServiceUnavailable)
		case 2:
			// 308 with no Range header: nothing persisted yet.
			w.WriteHeader(308)
		case 3:
			w.Header().Set("Range", "bytes=0-524287")
			w.WriteHeader(308)
		case 4:
			w.WriteHeader(http.StatusOK)
			io.WriteString(w, finalObjectJSON(payload))
		default:
			w.WriteHeader(http.StatusInternalServerError)
		}
	})
	srvURL.Store(srv.URL)

	up := mustUploader(t, srv.URL, 524288, 3)
	if _, err := up.Upload(context.Background(), testObject, testCType, payload); err != nil {
		t.Fatalf("Upload: %v", err)
	}
	eq(t, "request count", m.count(), 5)
	eq(t, "status check Content-Range", m.req(2).header.Get("Content-Range"), "bytes */787432")
	eq(t, "restart re-sends the first chunk", m.req(3).header.Get("Content-Range"), "bytes 0-524287/787432")
	if !bytes.Equal(m.req(3).body, payload[:524288]) {
		t.Fatalf("restarted chunk bytes differ from payload[0:524288]")
	}
}

func TestStatusCheckReturningObjectFinishesUpload(t *testing.T) {
	payload := testPayload(1000)
	var srvURL atomic.Value
	m, srv := newMock(t, func(n int, r rec, w http.ResponseWriter) {
		switch n {
		case 0:
			w.Header().Set("Location", srvURL.Load().(string)+"/upload/session/delta?upload_id=tok-4")
			w.WriteHeader(http.StatusOK)
		case 1:
			w.WriteHeader(http.StatusServiceUnavailable)
		case 2:
			w.WriteHeader(http.StatusOK)
			io.WriteString(w, finalObjectJSON(payload))
		default:
			w.WriteHeader(http.StatusInternalServerError)
		}
	})
	srvURL.Store(srv.URL)

	up := mustUploader(t, srv.URL, 262144, 2)
	obj, err := up.Upload(context.Background(), testObject, testCType, payload)
	if err != nil {
		t.Fatalf("Upload: %v", err)
	}
	eq(t, "request count", m.count(), 3)
	eq(t, "status check found the completed object", obj.Size, int64(1000))
	eq(t, "generation on the recovered object", obj.Generation, int64(31337000042))
}

func TestSessionExpired410IsTerminal(t *testing.T) {
	payload := testPayload(1000)
	var srvURL atomic.Value
	m, srv := newMock(t, func(n int, r rec, w http.ResponseWriter) {
		switch n {
		case 0:
			w.Header().Set("Location", srvURL.Load().(string)+"/upload/session/eps?upload_id=tok-5")
			w.WriteHeader(http.StatusOK)
		default:
			w.WriteHeader(http.StatusGone)
		}
	})
	srvURL.Store(srv.URL)

	up := mustUploader(t, srv.URL, 262144, 3)
	_, err := up.Upload(context.Background(), testObject, testCType, payload)
	if !errors.Is(err, ErrSessionExpired) {
		t.Fatalf("410 on a chunk: got %v, want ErrSessionExpired", err)
	}
	eq(t, "no retry after session expiry", m.count(), 2)
	if strings.Contains(err.Error(), testToken) {
		t.Fatalf("error text leaks the bearer token: %v", err)
	}
}

func TestSessionExpired404OnStatusCheck(t *testing.T) {
	payload := testPayload(1000)
	var srvURL atomic.Value
	m, srv := newMock(t, func(n int, r rec, w http.ResponseWriter) {
		switch n {
		case 0:
			w.Header().Set("Location", srvURL.Load().(string)+"/upload/session/zeta?upload_id=tok-6")
			w.WriteHeader(http.StatusOK)
		case 1:
			w.WriteHeader(http.StatusServiceUnavailable)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	})
	srvURL.Store(srv.URL)

	up := mustUploader(t, srv.URL, 262144, 3)
	_, err := up.Upload(context.Background(), testObject, testCType, payload)
	if !errors.Is(err, ErrSessionExpired) {
		t.Fatalf("404 on the status check: got %v, want ErrSessionExpired", err)
	}
	eq(t, "request count", m.count(), 3)
}

func TestRecoveryAttemptsAreBounded(t *testing.T) {
	payload := testPayload(1000)
	var srvURL atomic.Value
	m, srv := newMock(t, func(n int, r rec, w http.ResponseWriter) {
		if n == 0 {
			w.Header().Set("Location", srvURL.Load().(string)+"/upload/session/eta?upload_id=tok-7")
			w.WriteHeader(http.StatusOK)
			return
		}
		if strings.HasPrefix(r.header.Get("Content-Range"), "bytes */") {
			w.WriteHeader(308) // status check: nothing committed
			return
		}
		w.WriteHeader(http.StatusServiceUnavailable)
	})
	srvURL.Store(srv.URL)

	up := mustUploader(t, srv.URL, 262144, 2)
	_, err := up.Upload(context.Background(), testObject, testCType, payload)
	if !errors.Is(err, ErrTooManyRecoveries) {
		t.Fatalf("persistent 503s: got %v, want ErrTooManyRecoveries", err)
	}
	// init + chunk, status, chunk, status, chunk — the third failure exceeds
	// MaxRecoveries=2 and must give up without another status probe.
	eq(t, "bounded request count", m.count(), 6)
	if strings.Contains(err.Error(), testToken) {
		t.Fatalf("error text leaks the bearer token: %v", err)
	}
}

func TestChecksumMismatchIsRejected(t *testing.T) {
	payload := testPayload(1000)
	var srvURL atomic.Value
	m, srv := newMock(t, func(n int, r rec, w http.ResponseWriter) {
		switch n {
		case 0:
			w.Header().Set("Location", srvURL.Load().(string)+"/upload/session/theta?upload_id=tok-8")
			w.WriteHeader(http.StatusOK)
		case 1:
			w.WriteHeader(http.StatusOK)
			io.WriteString(w, objectJSONWith(payload, "31337000042", "1000", "AAAAAA=="))
		default:
			w.WriteHeader(http.StatusInternalServerError)
		}
	})
	srvURL.Store(srv.URL)

	up := mustUploader(t, srv.URL, 0, 0)
	_, err := up.Upload(context.Background(), testObject, testCType, payload)
	if !errors.Is(err, ErrChecksumMismatch) {
		t.Fatalf("bad crc32c: got %v, want ErrChecksumMismatch", err)
	}
	eq(t, "single-chunk Content-Range", m.req(1).header.Get("Content-Range"), "bytes 0-999/1000")
}

func TestSizeMismatchIsRejected(t *testing.T) {
	payload := testPayload(1000)
	var srvURL atomic.Value
	_, srv := newMock(t, func(n int, r rec, w http.ResponseWriter) {
		switch n {
		case 0:
			w.Header().Set("Location", srvURL.Load().(string)+"/upload/session/iota?upload_id=tok-9")
			w.WriteHeader(http.StatusOK)
		default:
			w.WriteHeader(http.StatusOK)
			io.WriteString(w, objectJSONWith(payload, "31337000042", "999", b64CRC32C(payload)))
		}
	})
	srvURL.Store(srv.URL)

	up := mustUploader(t, srv.URL, 0, 0)
	_, err := up.Upload(context.Background(), testObject, testCType, payload)
	if err == nil || !strings.Contains(err.Error(), "size") {
		t.Fatalf("size mismatch: got %v, want an error mentioning size", err)
	}
}

func TestMissingGenerationIsRejected(t *testing.T) {
	payload := testPayload(1000)
	var srvURL atomic.Value
	_, srv := newMock(t, func(n int, r rec, w http.ResponseWriter) {
		switch n {
		case 0:
			w.Header().Set("Location", srvURL.Load().(string)+"/upload/session/kappa?upload_id=tok-10")
			w.WriteHeader(http.StatusOK)
		default:
			w.WriteHeader(http.StatusOK)
			io.WriteString(w, objectJSONWith(payload, "", "1000", b64CRC32C(payload)))
		}
	})
	srvURL.Store(srv.URL)

	up := mustUploader(t, srv.URL, 0, 0)
	_, err := up.Upload(context.Background(), testObject, testCType, payload)
	if err == nil || !strings.Contains(err.Error(), "generation") {
		t.Fatalf("missing generation: got %v, want an error mentioning generation", err)
	}
}

func TestForeignSessionHostRefusedBeforeSendingBytes(t *testing.T) {
	payload := testPayload(1000)
	var foreignHits atomic.Int64
	foreign := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		foreignHits.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(foreign.Close)

	m, srv := newMock(t, func(n int, r rec, w http.ResponseWriter) {
		w.Header().Set("Location", foreign.URL+"/upload/session/rogue?upload_id=tok-11")
		w.WriteHeader(http.StatusOK)
	})

	up := mustUploader(t, srv.URL, 262144, 0)
	_, err := up.Upload(context.Background(), testObject, testCType, payload)
	if !errors.Is(err, ErrForeignSession) {
		t.Fatalf("cross-host session URI: got %v, want ErrForeignSession", err)
	}
	eq(t, "only the initiation request was sent", m.count(), 1)
	eq(t, "no bytes or credentials reached the foreign host", foreignHits.Load(), int64(0))
}

func TestConfigValidation(t *testing.T) {
	if _, err := NewUploader(Config{Bucket: testBucket, Token: testToken, ChunkSize: 1000}); err == nil || !strings.Contains(err.Error(), "256 KiB") {
		t.Fatalf("chunk size 1000: got %v, want an error mentioning the 256 KiB rule", err)
	}
	if _, err := NewUploader(Config{Bucket: testBucket, Token: testToken, ChunkSize: 262145}); err == nil || !strings.Contains(err.Error(), "256 KiB") {
		t.Fatalf("chunk size 262145: got %v, want an error mentioning the 256 KiB rule", err)
	}
	if _, err := NewUploader(Config{Token: testToken}); err == nil {
		t.Fatalf("missing bucket must be rejected")
	}
	if _, err := NewUploader(Config{Bucket: testBucket}); err == nil {
		t.Fatalf("missing token must be rejected")
	}
	up := mustUploader(t, "https://storage.googleapis.com", 0, 0)
	eq(t, "default chunk size follows the documented 8 MiB recommendation", up.ChunkSize(), int64(8*1024*1024))
	up = mustUploader(t, "https://storage.googleapis.com", 262144, 0)
	eq(t, "one-quantum chunk size accepted", up.ChunkSize(), int64(262144))
}
