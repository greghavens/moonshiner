package logtail

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

// The follower is strictly pull-based: nothing happens between Poll calls.
// Every test scripts file mutations by hand and then polls, so there are no
// goroutines, no watchers and no sleeps anywhere in this suite.

func logPath(t *testing.T) string {
	t.Helper()
	return filepath.Join(t.TempDir(), "app.log")
}

func writeAll(t *testing.T, path, data string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(data), 0o644); err != nil {
		t.Fatal(err)
	}
}

func appendTo(t *testing.T, path, data string) {
	t.Helper()
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteString(data); err != nil {
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
}

func mustPoll(t *testing.T, f *Follower) []string {
	t.Helper()
	lines, err := f.Poll()
	if err != nil {
		t.Fatalf("Poll: %v", err)
	}
	return lines
}

func expectLines(t *testing.T, got []string, want ...string) {
	t.Helper()
	if len(want) == 0 {
		if len(got) != 0 {
			t.Fatalf("Poll = %q, want no lines", got)
		}
		return
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Poll = %q, want %q", got, want)
	}
}

func TestReadsCompleteLinesAndTracksOffset(t *testing.T) {
	path := logPath(t)
	writeAll(t, path, "alpha\nbeta\n")
	f := New(path)
	defer f.Close()

	expectLines(t, mustPoll(t, f), "alpha", "beta")
	if got := f.State().Offset; got != 11 {
		t.Fatalf("State().Offset = %d, want 11 (both lines consumed)", got)
	}
	// Nothing new: an idle poll returns no lines and no error.
	expectLines(t, mustPoll(t, f))
}

func TestAppendsDeliveredExactlyOnce(t *testing.T) {
	path := logPath(t)
	writeAll(t, path, "one\n")
	f := New(path)
	defer f.Close()

	expectLines(t, mustPoll(t, f), "one")
	appendTo(t, path, "two\n")
	expectLines(t, mustPoll(t, f), "two")
	appendTo(t, path, "three\nfour\n")
	expectLines(t, mustPoll(t, f), "three", "four")
	expectLines(t, mustPoll(t, f))
}

func TestPartialLineHeldUntilNewlineArrives(t *testing.T) {
	path := logPath(t)
	writeAll(t, path, "par")
	f := New(path)
	defer f.Close()

	expectLines(t, mustPoll(t, f))
	if got := f.State().Offset; got != 0 {
		t.Fatalf("offset advanced past an unterminated line: %d, want 0", got)
	}
	appendTo(t, path, "tial\nnext-par")
	expectLines(t, mustPoll(t, f), "partial")
	if got := f.State().Offset; got != 8 {
		t.Fatalf("offset = %d, want 8 (only the terminated line is consumed)", got)
	}
	appendTo(t, path, "t\n")
	expectLines(t, mustPoll(t, f), "next-part")
}

func TestCarriageReturnsAreStripped(t *testing.T) {
	path := logPath(t)
	writeAll(t, path, "win\r\nlin\n")
	f := New(path)
	defer f.Close()
	expectLines(t, mustPoll(t, f), "win", "lin")
}

func TestMissingFileWaitsQuietly(t *testing.T) {
	path := logPath(t)
	f := New(path)
	defer f.Close()

	expectLines(t, mustPoll(t, f))
	expectLines(t, mustPoll(t, f))
	writeAll(t, path, "born\n")
	expectLines(t, mustPoll(t, f), "born")
}

func TestRenameAndRecreateRotation(t *testing.T) {
	path := logPath(t)
	writeAll(t, path, "one\ntwo\n")
	f := New(path)
	defer f.Close()
	expectLines(t, mustPoll(t, f), "one", "two")

	// Lines written to the old file after our last poll, then a logrotate-style
	// rename + recreate. One poll must deliver the old tail first, then the new
	// file from the top.
	appendTo(t, path, "three\n")
	if err := os.Rename(path, path+".1"); err != nil {
		t.Fatal(err)
	}
	writeAll(t, path, "four\n")

	expectLines(t, mustPoll(t, f), "three", "four")
	if got := f.State().Offset; got != 5 {
		t.Fatalf("offset = %d, want 5 (offset restarts in the new file)", got)
	}
	appendTo(t, path, "five\n")
	expectLines(t, mustPoll(t, f), "five")
}

func TestRotationFlushesUnterminatedTail(t *testing.T) {
	path := logPath(t)
	writeAll(t, path, "done\n")
	f := New(path)
	defer f.Close()
	expectLines(t, mustPoll(t, f), "done")

	// The old file ends without a newline. Once it has been rotated away that
	// line can never be completed, so it is delivered as-is.
	appendTo(t, path, "cut short")
	if err := os.Rename(path, path+".1"); err != nil {
		t.Fatal(err)
	}
	writeAll(t, path, "fresh\n")

	expectLines(t, mustPoll(t, f), "cut short", "fresh")
}

func TestRenameWithoutRecreateThenLater(t *testing.T) {
	path := logPath(t)
	writeAll(t, path, "old\n")
	f := New(path)
	defer f.Close()
	expectLines(t, mustPoll(t, f), "old")

	appendTo(t, path, "last words\n")
	if err := os.Rename(path, path+".1"); err != nil {
		t.Fatal(err)
	}
	// No replacement yet: the poll drains the rotated file and then waits.
	expectLines(t, mustPoll(t, f), "last words")
	expectLines(t, mustPoll(t, f))

	writeAll(t, path, "reborn\n")
	expectLines(t, mustPoll(t, f), "reborn")
}

func TestCopyTruncateRotation(t *testing.T) {
	path := logPath(t)
	writeAll(t, path, "aaaa\nbbbb\n")
	f := New(path)
	defer f.Close()
	expectLines(t, mustPoll(t, f), "aaaa", "bbbb")

	// copytruncate: same file, contents chopped to zero, new data appended.
	fh, err := os.OpenFile(path, os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	if err := fh.Truncate(0); err != nil {
		t.Fatal(err)
	}
	if _, err := fh.WriteString("cc\n"); err != nil {
		t.Fatal(err)
	}
	if err := fh.Close(); err != nil {
		t.Fatal(err)
	}

	expectLines(t, mustPoll(t, f), "cc")
	if got := f.State().Offset; got != 3 {
		t.Fatalf("offset = %d, want 3 (reset to the new content)", got)
	}
	appendTo(t, path, "dd\n")
	expectLines(t, mustPoll(t, f), "dd")
}

func TestShrunkenFileIsReadFromTheTop(t *testing.T) {
	path := logPath(t)
	writeAll(t, path, "0123456789012345\n")
	f := New(path)
	defer f.Close()
	expectLines(t, mustPoll(t, f), "0123456789012345")

	// Rewritten shorter in place (os.WriteFile truncates the existing file).
	writeAll(t, path, "z\n")
	expectLines(t, mustPoll(t, f), "z")
}

func TestResumeSkipsAlreadyDeliveredLines(t *testing.T) {
	path := logPath(t)
	writeAll(t, path, "l1\nl2\n")
	a := New(path)
	expectLines(t, mustPoll(t, a), "l1", "l2")
	saved := a.State()
	if err := a.Close(); err != nil {
		t.Fatal(err)
	}

	appendTo(t, path, "l3\n")
	b := Resume(path, saved)
	defer b.Close()
	expectLines(t, mustPoll(t, b), "l3")
}

func TestResumeRereadsThePendingPartialLine(t *testing.T) {
	path := logPath(t)
	writeAll(t, path, "head\nres")
	a := New(path)
	expectLines(t, mustPoll(t, a), "head")
	saved := a.State()
	if saved.Offset != 5 {
		t.Fatalf("saved offset = %d, want 5", saved.Offset)
	}
	if err := a.Close(); err != nil {
		t.Fatal(err)
	}

	// The partial "res" was never delivered, so after a restart it must come
	// out exactly once, completed.
	appendTo(t, path, "t\n")
	b := Resume(path, saved)
	defer b.Close()
	expectLines(t, mustPoll(t, b), "rest")
}

func TestResumeOffsetBeyondFileRestartsFromZero(t *testing.T) {
	path := logPath(t)
	writeAll(t, path, "tiny\n")
	f := Resume(path, State{Offset: 999})
	defer f.Close()
	expectLines(t, mustPoll(t, f), "tiny")
}

func TestPollReportsUnreadablePath(t *testing.T) {
	f := New(t.TempDir()) // a directory, not a log file
	defer f.Close()
	if _, err := f.Poll(); err == nil {
		t.Fatal("Poll on a directory returned no error")
	}
}

func TestCloseBeforeAnyFileExistsIsSafe(t *testing.T) {
	f := New(logPath(t))
	if err := f.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
}
