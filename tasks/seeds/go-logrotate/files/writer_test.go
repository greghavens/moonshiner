package logrotate

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func readOrEmpty(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return ""
	}
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func mustNotExist(t *testing.T, path string) {
	t.Helper()
	if _, err := os.Lstat(path); !os.IsNotExist(err) {
		t.Fatalf("%s should not exist (stat err=%v)", path, err)
	}
}

func mustWrite(t *testing.T, w *Writer, s string) {
	t.Helper()
	n, err := w.Write([]byte(s))
	if err != nil {
		t.Fatalf("Write(%q): %v", s, err)
	}
	if n != len(s) {
		t.Fatalf("Write(%q) reported %d bytes, want %d", s, n, len(s))
	}
}

func TestNewValidatesAndCreatesFile(t *testing.T) {
	dir := t.TempDir()
	log := filepath.Join(dir, "app.log")

	if _, err := New(log, 0, 3); err == nil {
		t.Fatal("maxSize 0 accepted")
	}
	if _, err := New(log, -5, 3); err == nil {
		t.Fatal("negative maxSize accepted")
	}
	if _, err := New(log, 10, -1); err == nil {
		t.Fatal("negative backups accepted")
	}
	if _, err := New(filepath.Join(dir, "missing", "app.log"), 10, 1); err == nil {
		t.Fatal("nonexistent parent directory accepted — New must not mkdir")
	}

	w, err := New(log, 10, 3)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	defer w.Close()
	st, err := os.Stat(log)
	if err != nil {
		t.Fatalf("New should create the log file immediately: %v", err)
	}
	if st.Size() != 0 {
		t.Fatalf("fresh log file has size %d, want 0", st.Size())
	}
}

func TestRotatesOnlyWhenThresholdExceeded(t *testing.T) {
	dir := t.TempDir()
	log := filepath.Join(dir, "app.log")
	w, err := New(log, 10, 3)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	defer w.Close()

	mustWrite(t, w, "0123456789") // exactly maxSize: no rotation
	mustNotExist(t, log+".1")
	if got := readOrEmpty(t, log); got != "0123456789" {
		t.Fatalf("current = %q, want the full first write", got)
	}

	mustWrite(t, w, "x") // 10+1 > 10: rotate first, then write
	if got := readOrEmpty(t, log+".1"); got != "0123456789" {
		t.Fatalf("backup .1 = %q, want the rotated-out content", got)
	}
	if got := readOrEmpty(t, log); got != "x" {
		t.Fatalf("current after rotation = %q, want %q", got, "x")
	}
}

func TestBackupChainShiftsAndPrunes(t *testing.T) {
	dir := t.TempDir()
	log := filepath.Join(dir, "svc.log")
	w, err := New(log, 4, 2)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	defer w.Close()

	for _, s := range []string{"aaaa", "bbbb", "cccc", "dddd"} {
		mustWrite(t, w, s)
	}
	if got := readOrEmpty(t, log); got != "dddd" {
		t.Fatalf("current = %q, want %q", got, "dddd")
	}
	if got := readOrEmpty(t, log+".1"); got != "cccc" {
		t.Fatalf(".1 = %q, want most recently rotated %q", got, "cccc")
	}
	if got := readOrEmpty(t, log+".2"); got != "bbbb" {
		t.Fatalf(".2 = %q, want %q", got, "bbbb")
	}
	mustNotExist(t, log+".3") // "aaaa" fell off the end
}

func TestOversizedWriteStaysWhole(t *testing.T) {
	dir := t.TempDir()
	log := filepath.Join(dir, "big.log")
	w, err := New(log, 10, 3)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	defer w.Close()

	big := strings.Repeat("B", 30)
	mustWrite(t, w, "12345")
	mustWrite(t, w, big)     // rotates "12345" out, lands whole
	mustWrite(t, w, "done!") // rotates the oversized file out

	if got := readOrEmpty(t, log); got != "done!" {
		t.Fatalf("current = %q, want %q", got, "done!")
	}
	if got := readOrEmpty(t, log+".1"); got != big {
		t.Fatalf(".1 = %q, want the oversized write intact in its own file", got)
	}
	if got := readOrEmpty(t, log+".2"); got != "12345" {
		t.Fatalf(".2 = %q, want %q", got, "12345")
	}
}

func TestBackupsZeroDiscardsOldContent(t *testing.T) {
	dir := t.TempDir()
	log := filepath.Join(dir, "drop.log")
	w, err := New(log, 10, 0)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	defer w.Close()

	mustWrite(t, w, "aaaaaaaa")
	mustWrite(t, w, "bbbbbbbb")
	if got := readOrEmpty(t, log); got != "bbbbbbbb" {
		t.Fatalf("current = %q, want only the newest write", got)
	}
	mustNotExist(t, log+".1")
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 {
		t.Fatalf("backups=0 must keep exactly the live file, dir has %d entries", len(entries))
	}
}

func TestReopenCountsExistingSize(t *testing.T) {
	dir := t.TempDir()
	log := filepath.Join(dir, "app.log")

	w1, err := New(log, 100, 2)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	first := strings.Repeat("1", 60)
	mustWrite(t, w1, first)
	if err := w1.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	w2, err := New(log, 100, 2)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer w2.Close()
	second := strings.Repeat("2", 60)
	mustWrite(t, w2, second) // 60+60 > 100: must rotate, so the old size was honored
	if got := readOrEmpty(t, log+".1"); got != first {
		t.Fatalf("after reopen+write, .1 = %q..., want the pre-restart content (reopen ignored existing size?)", got[:min(len(got), 12)])
	}
	if got := readOrEmpty(t, log); got != second {
		t.Fatalf("current = %q..., want only the post-restart write", got[:min(len(got), 12)])
	}
}

func TestWriteAfterCloseFails(t *testing.T) {
	dir := t.TempDir()
	w, err := New(filepath.Join(dir, "x.log"), 10, 1)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if err := w.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if _, err := w.Write([]byte("late")); err == nil {
		t.Fatal("Write after Close must fail")
	}
}

func TestConcurrentWritersLoseNothing(t *testing.T) {
	dir := t.TempDir()
	log := filepath.Join(dir, "conc.log")
	const (
		goroutines = 8
		perG       = 50
		lineLen    = 20
		maxSize    = 1000
	)
	w, err := New(log, maxSize, 100)
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	var wg sync.WaitGroup
	writeErrs := make(chan error, goroutines*perG)
	for g := 0; g < goroutines; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			for i := 0; i < perG; i++ {
				line := fmt.Sprintf("g%02d-%04d-0123456789\n", g, i)
				if len(line) != lineLen {
					writeErrs <- fmt.Errorf("test bug: line length %d", len(line))
					return
				}
				if _, err := w.Write([]byte(line)); err != nil {
					writeErrs <- err
					return
				}
			}
		}(g)
	}
	wg.Wait()
	close(writeErrs)
	for err := range writeErrs {
		t.Fatalf("concurrent write failed: %v", err)
	}
	if err := w.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	// Collect every line from the live file plus all numbered backups.
	seen := map[string]int{}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		p := filepath.Join(dir, e.Name())
		st, err := os.Stat(p)
		if err != nil {
			t.Fatal(err)
		}
		if st.Size() > maxSize {
			t.Fatalf("%s is %d bytes, over the %d threshold", e.Name(), st.Size(), maxSize)
		}
		content := readOrEmpty(t, p)
		if len(content)%lineLen != 0 {
			t.Fatalf("%s holds a partial/torn line (size %d not a multiple of %d)", e.Name(), len(content), lineLen)
		}
		for i := 0; i < len(content); i += lineLen {
			seen[content[i:i+lineLen]]++
		}
	}
	total := 0
	for g := 0; g < goroutines; g++ {
		for i := 0; i < perG; i++ {
			line := fmt.Sprintf("g%02d-%04d-0123456789\n", g, i)
			if seen[line] != 1 {
				t.Fatalf("line %q appears %d times, want exactly once", strings.TrimSpace(line), seen[line])
			}
			total++
		}
	}
	if got := len(seen); got != total {
		t.Fatalf("found %d distinct lines, want %d (torn or interleaved writes present)", got, total)
	}
}
