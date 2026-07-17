package arc

import (
	"bytes"
	"encoding/binary"
	"errors"
	"hash/crc32"
	"os"
	"path/filepath"
	"testing"
)

func sampleFiles() []File {
	return []File{
		{Name: "config/app.toml", Data: []byte("retries = 3\ntimeout = \"5s\"\n")},
		{Name: "notes/α-draft.md", Data: []byte("# Draft α\nbinary-ish: \x00\x01\xfe\xff done")},
		{Name: "empty.marker", Data: nil},
		{Name: "blobs/big.bin", Data: bytes.Repeat([]byte{0xAB, 0x00, 0x5C}, 400)},
	}
}

func writeSample(t *testing.T) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "bundle.arc")
	if err := Write(path, sampleFiles()); err != nil {
		t.Fatalf("Write: %v", err)
	}
	return path
}

// corrupt rewrites the byte at offset via fn and returns the path.
func corrupt(t *testing.T, path string, offset int64, fn func(b byte) byte) {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read for corruption: %v", err)
	}
	if offset < 0 {
		offset += int64(len(raw))
	}
	raw[offset] = fn(raw[offset])
	if err := os.WriteFile(path, raw, 0o644); err != nil {
		t.Fatalf("write corrupted: %v", err)
	}
}

func TestRoundTrip(t *testing.T) {
	path := writeSample(t)
	files := sampleFiles()

	entries, err := List(path)
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(entries) != len(files) {
		t.Fatalf("List returned %d entries, want %d", len(entries), len(files))
	}
	for i, f := range files {
		e := entries[i]
		if e.Name != f.Name {
			t.Errorf("entry %d: Name = %q, want %q (stored order must be preserved)", i, e.Name, f.Name)
		}
		if e.Size != int64(len(f.Data)) {
			t.Errorf("entry %q: Size = %d, want %d", f.Name, e.Size, len(f.Data))
		}
		if want := crc32.ChecksumIEEE(f.Data); e.CRC32 != want {
			t.Errorf("entry %q: CRC32 = %#08x, want IEEE %#08x", f.Name, e.CRC32, want)
		}

		got, err := ReadFile(path, f.Name)
		if err != nil {
			t.Fatalf("ReadFile(%q): %v", f.Name, err)
		}
		if !bytes.Equal(got, f.Data) {
			t.Errorf("ReadFile(%q) = %d bytes, payload does not round-trip", f.Name, len(got))
		}
	}
}

func TestEmptyArchive(t *testing.T) {
	path := filepath.Join(t.TempDir(), "empty.arc")
	if err := Write(path, nil); err != nil {
		t.Fatalf("Write of empty archive: %v", err)
	}
	entries, err := List(path)
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(entries) != 0 {
		t.Fatalf("List of empty archive = %v, want no entries", entries)
	}
	if _, err := ReadFile(path, "anything"); !errors.Is(err, ErrNotFound) {
		t.Errorf("ReadFile on empty archive: error = %v, want ErrNotFound", err)
	}
}

func TestHeaderLayout(t *testing.T) {
	path := writeSample(t)
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read archive: %v", err)
	}
	if len(raw) < 9 {
		t.Fatalf("archive is only %d bytes; the fixed header alone is 9", len(raw))
	}
	if !bytes.Equal(raw[:4], []byte("ARCP")) {
		t.Errorf("magic = %q, want \"ARCP\"", raw[:4])
	}
	if raw[4] != 1 {
		t.Errorf("version byte = %d, want 1", raw[4])
	}
	if n := binary.LittleEndian.Uint32(raw[5:9]); n != 4 {
		t.Errorf("entry count = %d, want 4", n)
	}
}

func TestDeterministicBytes(t *testing.T) {
	dir := t.TempDir()
	p1 := filepath.Join(dir, "one.arc")
	p2 := filepath.Join(dir, "two.arc")
	if err := Write(p1, sampleFiles()); err != nil {
		t.Fatalf("Write: %v", err)
	}
	if err := Write(p2, sampleFiles()); err != nil {
		t.Fatalf("Write: %v", err)
	}
	b1, _ := os.ReadFile(p1)
	b2, _ := os.ReadFile(p2)
	if !bytes.Equal(b1, b2) {
		t.Errorf("same input produced different archive bytes (%d vs %d); output must be deterministic", len(b1), len(b2))
	}
}

func TestWriteRejectsBadInput(t *testing.T) {
	dir := t.TempDir()
	dup := []File{
		{Name: "a.txt", Data: []byte("one")},
		{Name: "a.txt", Data: []byte("two")},
	}
	if err := Write(filepath.Join(dir, "dup.arc"), dup); err == nil {
		t.Errorf("Write with duplicate entry names: error = nil, want non-nil")
	}
	if err := Write(filepath.Join(dir, "noname.arc"), []File{{Name: "", Data: []byte("x")}}); err == nil {
		t.Errorf("Write with empty entry name: error = nil, want non-nil")
	}
}

func TestBadMagic(t *testing.T) {
	path := writeSample(t)
	corrupt(t, path, 0, func(b byte) byte { return b ^ 0xFF })
	if _, err := List(path); !errors.Is(err, ErrBadMagic) {
		t.Errorf("List with corrupted magic: error = %v, want ErrBadMagic", err)
	}
	if _, err := ReadFile(path, "config/app.toml"); !errors.Is(err, ErrBadMagic) {
		t.Errorf("ReadFile with corrupted magic: error = %v, want ErrBadMagic", err)
	}
}

func TestUnsupportedVersion(t *testing.T) {
	path := writeSample(t)
	corrupt(t, path, 4, func(byte) byte { return 2 })
	if _, err := List(path); !errors.Is(err, ErrVersion) {
		t.Errorf("List of version-2 archive: error = %v, want ErrVersion", err)
	}
}

func TestChecksumMismatchDetectedOnRead(t *testing.T) {
	path := writeSample(t)
	// Payloads sit at the end of the file; flipping the final byte damages
	// the last entry's payload without touching the entry table.
	corrupt(t, path, -1, func(b byte) byte { return b ^ 0x01 })

	if _, err := ReadFile(path, "blobs/big.bin"); !errors.Is(err, ErrChecksum) {
		t.Errorf("ReadFile of damaged payload: error = %v, want ErrChecksum", err)
	}

	// Listing consults only the table — it must still succeed.
	entries, err := List(path)
	if err != nil {
		t.Fatalf("List after payload damage: %v (listing must not extract payloads)", err)
	}
	if len(entries) != 4 {
		t.Fatalf("List after payload damage returned %d entries, want 4", len(entries))
	}

	// Undamaged siblings must still read cleanly.
	got, err := ReadFile(path, "config/app.toml")
	if err != nil {
		t.Fatalf("ReadFile of intact entry after unrelated damage: %v", err)
	}
	if !bytes.Equal(got, []byte("retries = 3\ntimeout = \"5s\"\n")) {
		t.Errorf("intact entry no longer round-trips after unrelated damage")
	}
}

func TestTruncatedPayload(t *testing.T) {
	path := writeSample(t)
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read archive: %v", err)
	}
	// Chop most of the last payload off.
	if err := os.WriteFile(path, raw[:len(raw)-600], 0o644); err != nil {
		t.Fatalf("truncate: %v", err)
	}

	if _, err := ReadFile(path, "blobs/big.bin"); !errors.Is(err, ErrTruncated) {
		t.Errorf("ReadFile of truncated payload: error = %v, want ErrTruncated", err)
	}
	if _, err := List(path); err != nil {
		t.Errorf("List with truncated payload region: %v (the table is intact; listing must work)", err)
	}
}

func TestTruncatedTable(t *testing.T) {
	path := writeSample(t)
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read archive: %v", err)
	}
	for _, keep := range []int{6, 12} { // mid-count and mid-table; magic intact
		short := filepath.Join(t.TempDir(), "short.arc")
		if err := os.WriteFile(short, raw[:keep], 0o644); err != nil {
			t.Fatalf("write truncated: %v", err)
		}
		if _, err := List(short); !errors.Is(err, ErrTruncated) {
			t.Errorf("List of %d-byte archive: error = %v, want ErrTruncated", keep, err)
		}
	}
}

func TestReadFileUnknownName(t *testing.T) {
	path := writeSample(t)
	if _, err := ReadFile(path, "config/app.tom"); !errors.Is(err, ErrNotFound) {
		t.Errorf("ReadFile of near-miss name: error = %v, want ErrNotFound", err)
	}
}
