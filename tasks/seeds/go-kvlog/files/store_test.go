package kvlog

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func mkdirParent(path string) error {
	return os.MkdirAll(filepath.Dir(path), 0o755)
}

func openStore(t *testing.T, path string) *Store {
	t.Helper()
	s, err := Open(path)
	if err != nil {
		t.Fatalf("Open(%s): %v", path, err)
	}
	return s
}

func mustSet(t *testing.T, s *Store, k, v string) {
	t.Helper()
	if err := s.Set(k, v); err != nil {
		t.Fatalf("Set(%q): %v", k, err)
	}
}

func expectGet(t *testing.T, s *Store, k, wantVal string, wantOK bool) {
	t.Helper()
	v, ok := s.Get(k)
	if ok != wantOK || v != wantVal {
		t.Fatalf("Get(%q) = (%q, %v), want (%q, %v)", k, v, ok, wantVal, wantOK)
	}
}

func TestSetGetDeleteBasics(t *testing.T) {
	s := openStore(t, filepath.Join(t.TempDir(), "data.wal"))
	defer s.Close()

	expectGet(t, s, "missing", "", false)
	mustSet(t, s, "region", "us-east-1")
	mustSet(t, s, "replicas", "3")
	expectGet(t, s, "region", "us-east-1", true)

	mustSet(t, s, "region", "eu-west-2") // overwrite: last write wins
	expectGet(t, s, "region", "eu-west-2", true)

	if err := s.Delete("replicas"); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	expectGet(t, s, "replicas", "", false)
	if err := s.Delete("never-existed"); err != nil {
		t.Fatalf("Delete of a missing key must be a quiet no-op, got %v", err)
	}
	if n := s.Len(); n != 1 {
		t.Fatalf("Len = %d, want 1", n)
	}
}

func TestEmptyValueIsNotMissing(t *testing.T) {
	path := filepath.Join(t.TempDir(), "data.wal")
	s := openStore(t, path)
	mustSet(t, s, "flag", "")
	expectGet(t, s, "flag", "", true)
	if n := s.Len(); n != 1 {
		t.Fatalf("Len = %d, want 1", n)
	}
	if err := s.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	s2 := openStore(t, path)
	defer s2.Close()
	expectGet(t, s2, "flag", "", true) // still a real value after replay
}

func TestArbitraryBytesInKeysAndValues(t *testing.T) {
	path := filepath.Join(t.TempDir(), "data.wal")
	pairs := map[string]string{
		"key with\nnewline":     "value\twith\ttabs",
		"unicode-ключ-鍵":        "значение-値",
		"binary\x00key":         "binary\x00value\x01\x02",
		"spaces in key":         "spaces in value  ",
		strings.Repeat("k", 300): strings.Repeat("v", 10240),
	}
	s := openStore(t, path)
	for k, v := range pairs {
		mustSet(t, s, k, v)
	}
	for k, v := range pairs {
		expectGet(t, s, k, v, true)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}

	s2 := openStore(t, path)
	defer s2.Close()
	for k, v := range pairs {
		expectGet(t, s2, k, v, true)
	}
	if n := s2.Len(); n != len(pairs) {
		t.Fatalf("Len after replay = %d, want %d", n, len(pairs))
	}
}

func TestStatePersistsAcrossReopens(t *testing.T) {
	path := filepath.Join(t.TempDir(), "data.wal")

	s := openStore(t, path)
	mustSet(t, s, "a", "1")
	mustSet(t, s, "b", "2")
	if err := s.Delete("a"); err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}

	s = openStore(t, path)
	expectGet(t, s, "a", "", false)
	expectGet(t, s, "b", "2", true)
	mustSet(t, s, "c", "3") // appending after a replay must work
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}

	s = openStore(t, path)
	defer s.Close()
	expectGet(t, s, "b", "2", true)
	expectGet(t, s, "c", "3", true)
	if n := s.Len(); n != 2 {
		t.Fatalf("Len = %d, want 2", n)
	}
}

func TestOpenMissingFileStartsEmpty(t *testing.T) {
	path := filepath.Join(t.TempDir(), "fresh", "data.wal")
	// parent exists, file does not
	if err := mkdirParent(path); err != nil {
		t.Fatal(err)
	}
	s := openStore(t, path)
	defer s.Close()
	if n := s.Len(); n != 0 {
		t.Fatalf("fresh store Len = %d, want 0", n)
	}
	mustSet(t, s, "first", "write")
	expectGet(t, s, "first", "write", true)
}

func TestOpsAfterCloseFail(t *testing.T) {
	s := openStore(t, filepath.Join(t.TempDir(), "data.wal"))
	mustSet(t, s, "k", "v")
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	if err := s.Set("k", "v2"); err == nil {
		t.Fatal("Set after Close must error")
	}
	if err := s.Delete("k"); err == nil {
		t.Fatal("Delete after Close must error")
	}
	if err := s.Compact(); err == nil {
		t.Fatal("Compact after Close must error")
	}
}

func TestConcurrentWritersAndReaders(t *testing.T) {
	path := filepath.Join(t.TempDir(), "data.wal")
	s := openStore(t, path)

	const writers, perW = 4, 100
	var wg sync.WaitGroup
	errs := make(chan error, writers*perW)
	for w := 0; w < writers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			for i := 0; i < perW; i++ {
				k := fmt.Sprintf("w%d-key%03d", w, i)
				if err := s.Set(k, fmt.Sprintf("val-%d-%d", w, i)); err != nil {
					errs <- err
					return
				}
				s.Get(k) // concurrent reads while other writers append
				s.Len()
			}
		}(w)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		t.Fatalf("concurrent op failed: %v", err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}

	s2 := openStore(t, path)
	defer s2.Close()
	if n := s2.Len(); n != writers*perW {
		t.Fatalf("replayed Len = %d, want %d", n, writers*perW)
	}
	for w := 0; w < writers; w++ {
		for i := 0; i < perW; i++ {
			expectGet(t, s2, fmt.Sprintf("w%d-key%03d", w, i), fmt.Sprintf("val-%d-%d", w, i), true)
		}
	}
}
