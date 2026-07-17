package kvwal

import (
	"bytes"
	"fmt"
	"sync"
	"testing"
)

func openStore(t *testing.T, dir string) *Store {
	t.Helper()
	st, err := Open(dir)
	if err != nil {
		t.Fatalf("Open(%q): %v", dir, err)
	}
	return st
}

func mustPut(t *testing.T, st *Store, k string, v []byte) {
	t.Helper()
	if err := st.Put(k, v); err != nil {
		t.Fatalf("Put(%q): %v", k, err)
	}
}

func mustClose(t *testing.T, st *Store) {
	t.Helper()
	if err := st.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
}

func wantGet(t *testing.T, st *Store, k string, want []byte) {
	t.Helper()
	got, ok := st.Get(k)
	if !ok {
		t.Fatalf("Get(%q) reports missing, want %q", k, want)
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("Get(%q) = %q, want %q", k, got, want)
	}
}

func wantMissing(t *testing.T, st *Store, k string) {
	t.Helper()
	if got, ok := st.Get(k); ok {
		t.Fatalf("Get(%q) = %q, want missing", k, got)
	}
}

func TestPutGetRoundTrip(t *testing.T) {
	st := openStore(t, t.TempDir())
	defer st.Close()
	mustPut(t, st, "host", []byte("edge-04"))
	mustPut(t, st, "port", []byte("8443"))

	wantGet(t, st, "host", []byte("edge-04"))
	wantGet(t, st, "port", []byte("8443"))
	wantMissing(t, st, "absent")
}

func TestEmptyKeyRejected(t *testing.T) {
	st := openStore(t, t.TempDir())
	defer st.Close()
	if err := st.Put("", []byte("v")); err == nil {
		t.Fatal("Put with empty key must return an error")
	}
	if n := st.Len(); n != 0 {
		t.Fatalf("rejected Put must not be stored, Len = %d", n)
	}
}

func TestOverwriteLastWinsAndLenCountsKeys(t *testing.T) {
	dir := t.TempDir()
	st := openStore(t, dir)
	mustPut(t, st, "cfg", []byte("v1"))
	mustPut(t, st, "cfg", []byte("v2"))
	mustPut(t, st, "cfg", []byte("v3"))

	if n := st.Len(); n != 1 {
		t.Fatalf("Len after overwriting one key = %d, want 1 (keys, not records)", n)
	}
	wantGet(t, st, "cfg", []byte("v3"))

	mustClose(t, st)
	st2 := openStore(t, dir)
	defer st2.Close()
	wantGet(t, st2, "cfg", []byte("v3"))
	if n := st2.Len(); n != 1 {
		t.Fatalf("Len after replay = %d, want 1 (last record for a key wins)", n)
	}
}

func TestDeleteTombstonesSurviveReopen(t *testing.T) {
	dir := t.TempDir()
	st := openStore(t, dir)
	mustPut(t, st, "keep", []byte("here"))
	mustPut(t, st, "gone", []byte("bye"))
	if err := st.Delete("gone"); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	wantMissing(t, st, "gone")
	if n := st.Len(); n != 1 {
		t.Fatalf("Len after delete = %d, want 1", n)
	}
	mustClose(t, st)

	st2 := openStore(t, dir)
	defer st2.Close()
	wantGet(t, st2, "keep", []byte("here"))
	wantMissing(t, st2, "gone")
	if n := st2.Len(); n != 1 {
		t.Fatalf("Len after reopen = %d, want 1 (tombstone must replay)", n)
	}
}

func TestDeleteMissingKeyIsNoop(t *testing.T) {
	dir := t.TempDir()
	st := openStore(t, dir)
	if err := st.Delete("ghost"); err != nil {
		t.Fatalf("Delete of a missing key must be a no-op, got error: %v", err)
	}
	wantMissing(t, st, "ghost")
	mustClose(t, st)

	st2 := openStore(t, dir)
	defer st2.Close()
	wantMissing(t, st2, "ghost")
	if n := st2.Len(); n != 0 {
		t.Fatalf("Len = %d, want 0", n)
	}
}

func TestDeleteThenRewriteReplaysInOrder(t *testing.T) {
	dir := t.TempDir()
	st := openStore(t, dir)
	mustPut(t, st, "cfg", []byte("old"))
	if err := st.Delete("cfg"); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	mustPut(t, st, "cfg", []byte("new"))
	mustClose(t, st)

	st2 := openStore(t, dir)
	defer st2.Close()
	wantGet(t, st2, "cfg", []byte("new"))
	if n := st2.Len(); n != 1 {
		t.Fatalf("Len = %d, want 1 (put after tombstone resurrects the key)", n)
	}
}

func TestEmptyValueIsLegalAndDistinctFromMissing(t *testing.T) {
	dir := t.TempDir()
	st := openStore(t, dir)
	mustPut(t, st, "flag", []byte{})
	got, ok := st.Get("flag")
	if !ok {
		t.Fatal("Get of a key with an empty value must report present")
	}
	if len(got) != 0 {
		t.Fatalf("Get = %q, want empty value", got)
	}
	mustClose(t, st)

	st2 := openStore(t, dir)
	defer st2.Close()
	got, ok = st2.Get("flag")
	if !ok || len(got) != 0 {
		t.Fatalf("after reopen Get = (%q, %v), want empty value present", got, ok)
	}
}

func TestValueSlicesAreIsolated(t *testing.T) {
	st := openStore(t, t.TempDir())
	defer st.Close()

	buf := []byte("original")
	mustPut(t, st, "k", buf)
	buf[0] = 'X' // caller reuses its buffer
	wantGet(t, st, "k", []byte("original"))

	out, _ := st.Get("k")
	out[0] = 'Z' // caller scribbles on the returned slice
	wantGet(t, st, "k", []byte("original"))
}

func TestKeysSortedAscending(t *testing.T) {
	st := openStore(t, t.TempDir())
	defer st.Close()
	mustPut(t, st, "zebra", []byte("1"))
	mustPut(t, st, "apple", []byte("2"))
	mustPut(t, st, "mango", []byte("3"))
	mustPut(t, st, "gone", []byte("4"))
	if err := st.Delete("gone"); err != nil {
		t.Fatalf("Delete: %v", err)
	}

	got := st.Keys()
	want := []string{"apple", "mango", "zebra"}
	if len(got) != len(want) {
		t.Fatalf("Keys() = %v, want %v (live keys only)", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("Keys() = %v, want %v (sorted ascending)", got, want)
		}
	}
}

func TestPersistenceAcrossManyReopens(t *testing.T) {
	dir := t.TempDir()

	st := openStore(t, dir)
	mustPut(t, st, "a", []byte("1"))
	mustPut(t, st, "b", []byte("2"))
	mustClose(t, st)

	st = openStore(t, dir)
	mustPut(t, st, "c", []byte("3"))
	if err := st.Delete("a"); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	mustClose(t, st)

	st = openStore(t, dir)
	defer st.Close()
	wantMissing(t, st, "a")
	wantGet(t, st, "b", []byte("2"))
	wantGet(t, st, "c", []byte("3"))
	if n := st.Len(); n != 2 {
		t.Fatalf("Len after two sessions = %d, want 2", n)
	}
}

func TestWritesAfterCloseFail(t *testing.T) {
	st := openStore(t, t.TempDir())
	mustPut(t, st, "k", []byte("v"))
	mustClose(t, st)

	if err := st.Put("k2", []byte("v")); err == nil {
		t.Fatal("Put after Close must return an error")
	}
	if err := st.Delete("k"); err == nil {
		t.Fatal("Delete after Close must return an error")
	}
	if err := st.Compact(); err == nil {
		t.Fatal("Compact after Close must return an error")
	}
}

func TestConcurrentPutsAllSurviveReopen(t *testing.T) {
	dir := t.TempDir()
	st := openStore(t, dir)

	const writers, perWriter = 8, 50
	var wg sync.WaitGroup
	for w := 0; w < writers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			for i := 0; i < perWriter; i++ {
				k := fmt.Sprintf("w%d-k%03d", w, i)
				v := []byte(fmt.Sprintf("v%d.%d", w, i))
				if err := st.Put(k, v); err != nil {
					t.Errorf("concurrent Put(%q): %v", k, err)
					return
				}
			}
		}(w)
	}
	wg.Wait()

	if n := st.Len(); n != writers*perWriter {
		t.Fatalf("Len after concurrent writes = %d, want %d", n, writers*perWriter)
	}
	mustClose(t, st)

	st2 := openStore(t, dir)
	defer st2.Close()
	if n := st2.Len(); n != writers*perWriter {
		t.Fatalf("Len after reopen = %d, want %d (every concurrent Put must be in the log)", n, writers*perWriter)
	}
	wantGet(t, st2, "w3-k007", []byte("v3.7"))
	wantGet(t, st2, "w7-k049", []byte("v7.49"))
}
