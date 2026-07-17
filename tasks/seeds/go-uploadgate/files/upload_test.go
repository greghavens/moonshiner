package upload

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/textproto"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func defaultPolicy() Policy {
	return Policy{
		MaxPartBytes:  64 * 1024,
		MaxTotalBytes: 100 * 1024,
		AllowedTypes:  []string{"text/plain", "application/octet-stream", "image/png"},
	}
}

type partSpec struct {
	field, filename, ctype string
	data                   []byte
}

func buildMultipart(t *testing.T, parts []partSpec, fields map[string]string) (io.Reader, string) {
	t.Helper()
	var buf bytes.Buffer
	w := multipart.NewWriter(&buf)
	for k, v := range fields {
		if err := w.WriteField(k, v); err != nil {
			t.Fatal(err)
		}
	}
	for _, p := range parts {
		h := textproto.MIMEHeader{}
		h.Set("Content-Disposition",
			fmt.Sprintf(`form-data; name=%q; filename=%q`, p.field, p.filename))
		if p.ctype != "" {
			h.Set("Content-Type", p.ctype)
		}
		pw, err := w.CreatePart(h)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := pw.Write(p.data); err != nil {
			t.Fatal(err)
		}
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return &buf, w.FormDataContentType()
}

func post(t *testing.T, h http.Handler, body io.Reader, contentType string) (*httptest.ResponseRecorder, []byte) {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/upload", body)
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	raw, err := io.ReadAll(rec.Result().Body)
	if err != nil {
		t.Fatal(err)
	}
	return rec, raw
}

func decodeFiles(t *testing.T, raw []byte) []map[string]any {
	t.Helper()
	var out []map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("success body must be a JSON array: %v (%q)", err, raw)
	}
	return out
}

func wantErr(t *testing.T, rec *httptest.ResponseRecorder, raw []byte, status int) map[string]any {
	t.Helper()
	if rec.Code != status {
		t.Fatalf("status = %d, want %d (body %q)", rec.Code, status, raw)
	}
	if ct := rec.Header().Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		t.Fatalf("error responses must be application/json, got %q", ct)
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		t.Fatalf("error body is not JSON: %v (%q)", err, raw)
	}
	if msg, _ := m["error"].(string); msg == "" {
		t.Fatalf(`error body needs a non-empty "error" field: %q`, raw)
	}
	return m
}

func dirEntries(t *testing.T, dir string) []string {
	t.Helper()
	ents, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	var names []string
	for _, e := range ents {
		names = append(names, e.Name())
	}
	return names
}

func patternBytes(n int) []byte {
	b := make([]byte, n)
	for i := range b {
		b[i] = byte(i % 251)
	}
	return b
}

func sum(b []byte) string {
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

func TestHappyPathStoresHashesAndReports(t *testing.T) {
	dir := t.TempDir()
	h := Handler(dir, defaultPolicy())

	docData := bytes.Repeat([]byte("A"), 1000)
	binData := patternBytes(2048)
	body, ct := buildMultipart(t, []partSpec{
		{"doc", "notes.txt", "text/plain; charset=utf-8", docData},
		{"blob", "data.bin", "application/octet-stream", binData},
	}, nil)

	rec, raw := post(t, h, body, ct)
	if rec.Code != http.StatusCreated {
		t.Fatalf("status = %d, want 201 (body %q)", rec.Code, raw)
	}
	if ctH := rec.Header().Get("Content-Type"); !strings.HasPrefix(ctH, "application/json") {
		t.Fatalf("Content-Type = %q, want application/json", ctH)
	}
	files := decodeFiles(t, raw)
	if len(files) != 2 {
		t.Fatalf("reported %d files, want 2: %v", len(files), files)
	}

	first := files[0]
	if first["field"] != "doc" || first["filename"] != "notes.txt" {
		t.Fatalf("first entry identity wrong: %v", first)
	}
	if first["media_type"] != "text/plain" {
		t.Fatalf("media_type = %v, want the parameters stripped: text/plain", first["media_type"])
	}
	if first["size"] != float64(len(docData)) {
		t.Fatalf("size = %v, want %d", first["size"], len(docData))
	}
	if first["sha256"] != sum(docData) {
		t.Fatalf("sha256 = %v, want %s", first["sha256"], sum(docData))
	}

	second := files[1]
	if second["field"] != "blob" || second["media_type"] != "application/octet-stream" ||
		second["size"] != float64(len(binData)) || second["sha256"] != sum(binData) {
		t.Fatalf("second entry wrong: %v", second)
	}

	// Files must actually be on disk, inside dir, with matching contents.
	for i, want := range [][]byte{docData, binData} {
		p, _ := files[i]["path"].(string)
		if p == "" {
			t.Fatalf("entry %d has no path: %v", i, files[i])
		}
		clean := filepath.Clean(p)
		if !strings.HasPrefix(clean, filepath.Clean(dir)+string(filepath.Separator)) {
			t.Fatalf("stored path %q escapes the upload dir %q", p, dir)
		}
		got, err := os.ReadFile(clean)
		if err != nil {
			t.Fatalf("reading stored file: %v", err)
		}
		if !bytes.Equal(got, want) {
			t.Fatalf("stored contents of entry %d differ (len %d vs %d)", i, len(got), len(want))
		}
	}
	if n := len(dirEntries(t, dir)); n != 2 {
		t.Fatalf("upload dir has %d entries, want exactly the 2 stored files", n)
	}
}

func TestEmptyFileAndExactLimits(t *testing.T) {
	dir := t.TempDir()
	p := Policy{MaxPartBytes: 5000, MaxTotalBytes: 8000, AllowedTypes: []string{"text/plain"}}
	h := Handler(dir, p)

	// exactly at the per-part limit and exactly at the total: 5000 + 3000
	body, ct := buildMultipart(t, []partSpec{
		{"a", "a.txt", "text/plain", bytes.Repeat([]byte("x"), 5000)},
		{"b", "b.txt", "text/plain", bytes.Repeat([]byte("y"), 3000)},
	}, nil)
	rec, raw := post(t, h, body, ct)
	if rec.Code != http.StatusCreated {
		t.Fatalf("sizes exactly at the limits must pass: %d %q", rec.Code, raw)
	}

	// zero-byte upload is legal
	dir2 := t.TempDir()
	h2 := Handler(dir2, p)
	body, ct = buildMultipart(t, []partSpec{{"empty", "empty.txt", "text/plain", nil}}, nil)
	rec, raw = post(t, h2, body, ct)
	if rec.Code != http.StatusCreated {
		t.Fatalf("empty file: %d %q", rec.Code, raw)
	}
	files := decodeFiles(t, raw)
	if files[0]["size"] != float64(0) || files[0]["sha256"] != sum(nil) {
		t.Fatalf("empty file entry: %v (want size 0, sha256 of empty input)", files[0])
	}
}

func TestPartOverLimitRejectsAndCleansUp(t *testing.T) {
	dir := t.TempDir()
	p := Policy{MaxPartBytes: 4096, MaxTotalBytes: 1 << 20, AllowedTypes: []string{"text/plain"}}
	h := Handler(dir, p)

	body, ct := buildMultipart(t, []partSpec{
		{"ok", "small.txt", "text/plain", bytes.Repeat([]byte("s"), 100)},
		{"doc", "big.txt", "text/plain", bytes.Repeat([]byte("B"), 4097)}, // one byte over
	}, nil)
	rec, raw := post(t, h, body, ct)
	m := wantErr(t, rec, raw, http.StatusRequestEntityTooLarge)
	if m["field"] != "doc" {
		t.Fatalf(`413 must name the offending part: field = %v, want "doc"`, m["field"])
	}
	if ents := dirEntries(t, dir); len(ents) != 0 {
		t.Fatalf("after a rejected upload the dir must be spotless, found %v", ents)
	}
}

func TestTotalOverLimitRejectsAndCleansUp(t *testing.T) {
	dir := t.TempDir()
	p := Policy{MaxPartBytes: 6000, MaxTotalBytes: 10000, AllowedTypes: []string{"text/plain"}}
	h := Handler(dir, p)

	body, ct := buildMultipart(t, []partSpec{
		{"one", "one.txt", "text/plain", bytes.Repeat([]byte("1"), 6000)},
		{"two", "two.txt", "text/plain", bytes.Repeat([]byte("2"), 4001)}, // total 10001
	}, nil)
	rec, raw := post(t, h, body, ct)
	m := wantErr(t, rec, raw, http.StatusRequestEntityTooLarge)
	if m["field"] != "two" {
		t.Fatalf(`total-limit 413 blames field %v, want "two"`, m["field"])
	}
	if ents := dirEntries(t, dir); len(ents) != 0 {
		t.Fatalf("first file must not survive a failed batch, found %v", ents)
	}
}

func TestDisallowedTypeRejectsAndCleansUp(t *testing.T) {
	dir := t.TempDir()
	h := Handler(dir, defaultPolicy())

	body, ct := buildMultipart(t, []partSpec{
		{"ok", "fine.txt", "text/plain", []byte("fine")},
		{"payload", "run.exe", "application/x-msdownload", []byte("MZ...")},
	}, nil)
	rec, raw := post(t, h, body, ct)
	m := wantErr(t, rec, raw, http.StatusUnsupportedMediaType)
	if m["field"] != "payload" {
		t.Fatalf(`415 must name the offending part: field = %v, want "payload"`, m["field"])
	}
	if ents := dirEntries(t, dir); len(ents) != 0 {
		t.Fatalf("dir not cleaned after 415, found %v", ents)
	}
}

func TestMissingPartContentTypeDefaultsToOctetStream(t *testing.T) {
	// allowed when the policy lists application/octet-stream ...
	dir := t.TempDir()
	h := Handler(dir, defaultPolicy())
	body, ct := buildMultipart(t, []partSpec{{"f", "raw.dat", "", []byte("rawbytes")}}, nil)
	rec, raw := post(t, h, body, ct)
	if rec.Code != http.StatusCreated {
		t.Fatalf("typeless part with octet-stream allowed: %d %q", rec.Code, raw)
	}
	if files := decodeFiles(t, raw); files[0]["media_type"] != "application/octet-stream" {
		t.Fatalf("typeless part must default to application/octet-stream, got %v", files[0]["media_type"])
	}

	// ... and rejected when it doesn't
	dir2 := t.TempDir()
	h2 := Handler(dir2, Policy{MaxPartBytes: 1024, MaxTotalBytes: 1024, AllowedTypes: []string{"text/plain"}})
	body, ct = buildMultipart(t, []partSpec{{"f", "raw.dat", "", []byte("rawbytes")}}, nil)
	rec, raw = post(t, h2, body, ct)
	wantErr(t, rec, raw, http.StatusUnsupportedMediaType)
	if ents := dirEntries(t, dir2); len(ents) != 0 {
		t.Fatalf("dir not cleaned, found %v", ents)
	}
}

func TestPlainFieldsAreIgnoredAndFree(t *testing.T) {
	dir := t.TempDir()
	p := Policy{MaxPartBytes: 4000, MaxTotalBytes: 4000, AllowedTypes: []string{"text/plain"}}
	h := Handler(dir, p)

	// The note field alone is bigger than the whole byte budget; it's not a
	// file, so it must not count.
	body, ct := buildMultipart(t,
		[]partSpec{{"doc", "doc.txt", "text/plain", bytes.Repeat([]byte("d"), 4000)}},
		map[string]string{"note": strings.Repeat("n", 5000)})
	rec, raw := post(t, h, body, ct)
	if rec.Code != http.StatusCreated {
		t.Fatalf("form fields must not count against the file budget: %d %q", rec.Code, raw)
	}
	files := decodeFiles(t, raw)
	if len(files) != 1 || files[0]["field"] != "doc" {
		t.Fatalf("only the file part belongs in the report: %v", files)
	}
}

func TestNoFilesIsBadRequest(t *testing.T) {
	dir := t.TempDir()
	h := Handler(dir, defaultPolicy())
	body, ct := buildMultipart(t, nil, map[string]string{"note": "just text"})
	rec, raw := post(t, h, body, ct)
	wantErr(t, rec, raw, http.StatusBadRequest)
}

func TestFilenamePathTraversalIsContained(t *testing.T) {
	dir := t.TempDir()
	h := Handler(dir, defaultPolicy())
	body, ct := buildMultipart(t, []partSpec{
		{"f", "../../evil.txt", "text/plain", []byte("gotcha")},
	}, nil)
	rec, raw := post(t, h, body, ct)
	if rec.Code != http.StatusCreated {
		t.Fatalf("hostile filename should be sanitized, not fatal: %d %q", rec.Code, raw)
	}
	files := decodeFiles(t, raw)
	p, _ := files[0]["path"].(string)
	clean := filepath.Clean(p)
	if !strings.HasPrefix(clean, filepath.Clean(dir)+string(filepath.Separator)) {
		t.Fatalf("stored path %q escaped the upload dir %q", p, dir)
	}
	if _, err := os.Stat(filepath.Join(filepath.Dir(filepath.Clean(dir)), "evil.txt")); !os.IsNotExist(err) {
		t.Fatalf("a file escaped above the upload dir: %v", err)
	}
	got, err := os.ReadFile(clean)
	if err != nil || string(got) != "gotcha" {
		t.Fatalf("stored file: %q, %v", got, err)
	}
}

func TestTruncatedBodyCleansUp(t *testing.T) {
	dir := t.TempDir()
	h := Handler(dir, defaultPolicy())

	var buf bytes.Buffer
	w := multipart.NewWriter(&buf)
	hdr := textproto.MIMEHeader{}
	hdr.Set("Content-Disposition", `form-data; name="doc"; filename="doc.txt"`)
	hdr.Set("Content-Type", "text/plain")
	pw, err := w.CreatePart(hdr)
	if err != nil {
		t.Fatal(err)
	}
	pw.Write(bytes.Repeat([]byte("t"), 8192))
	w.Close()

	// Chop the body off mid-part: the client hung up during the upload.
	truncated := buf.Bytes()[:buf.Len()/2]
	rec, raw := post(t, h, bytes.NewReader(truncated), w.FormDataContentType())
	wantErr(t, rec, raw, http.StatusBadRequest)
	if ents := dirEntries(t, dir); len(ents) != 0 {
		t.Fatalf("aborted upload left temp files behind: %v", ents)
	}
}

func TestRequestLevelRejections(t *testing.T) {
	dir := t.TempDir()
	h := Handler(dir, defaultPolicy())

	// wrong method
	req := httptest.NewRequest(http.MethodGet, "/upload", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusMethodNotAllowed {
		t.Fatalf("GET status = %d, want 405", rec.Code)
	}

	// not multipart at all
	rec2, raw := post(t, h, strings.NewReader(`{"not":"multipart"}`), "application/json")
	wantErr(t, rec2, raw, http.StatusUnsupportedMediaType)

	// multipart without a boundary parameter
	rec3, raw := post(t, h, strings.NewReader("--x--"), "multipart/form-data")
	wantErr(t, rec3, raw, http.StatusBadRequest)
}

func TestOverRealServerStreamsAndReportsConsistently(t *testing.T) {
	dir := t.TempDir()
	p := Policy{MaxPartBytes: 512 * 1024, MaxTotalBytes: 1 << 20, AllowedTypes: []string{"application/octet-stream"}}
	srv := httptest.NewServer(Handler(dir, p))
	t.Cleanup(srv.Close)

	data := patternBytes(256 * 1024)
	body, ct := buildMultipart(t, []partSpec{{"chunk", "chunk.bin", "application/octet-stream", data}}, nil)
	resp, err := http.Post(srv.URL, ct, body)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("status = %d (%q)", resp.StatusCode, raw)
	}
	files := decodeFiles(t, raw)
	if files[0]["sha256"] != sum(data) || files[0]["size"] != float64(len(data)) {
		t.Fatalf("large upload entry wrong: size=%v sha=%v", files[0]["size"], files[0]["sha256"])
	}
}
