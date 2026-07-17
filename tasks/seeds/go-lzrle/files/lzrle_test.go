package lzrle

import (
	"bytes"
	"errors"
	"testing"
)

// frame assembles a wire frame by hand so the decoder contract can be pinned
// independently of whatever the encoder chooses to emit.
func frame(mode byte, origLen uint32, body []byte) []byte {
	f := []byte{'L', 'Z', 'R', '1', mode,
		byte(origLen >> 24), byte(origLen >> 16), byte(origLen >> 8), byte(origLen)}
	return append(f, body...)
}

// xorshift32 gives deterministic pseudo-random bytes without seeding globals.
func noise(n int) []byte {
	out := make([]byte, n)
	s := uint32(0x9e3779b9)
	for i := range out {
		s ^= s << 13
		s ^= s >> 17
		s ^= s << 5
		out[i] = byte(s)
	}
	return out
}

func roundTrip(t *testing.T, data []byte) {
	t.Helper()
	f := Compress(data)
	got, err := Decompress(f)
	if err != nil {
		t.Fatalf("Decompress(Compress(%d bytes)): %v", len(data), err)
	}
	if !bytes.Equal(got, data) {
		t.Fatalf("round trip mangled %d-byte input (got %d bytes back)", len(data), len(got))
	}
}

func TestRoundTripSmall(t *testing.T) {
	roundTrip(t, nil)
	roundTrip(t, []byte{})
	roundTrip(t, []byte("a"))
	roundTrip(t, []byte("ab"))
	roundTrip(t, []byte("hello, world"))
	all := make([]byte, 256)
	for i := range all {
		all[i] = byte(i)
	}
	roundTrip(t, all)
}

func TestRoundTripRepetitive(t *testing.T) {
	line := []byte("2026-03-01T10:00:00Z INFO gateway request served route=/api/v1/items status=200\n")
	var log []byte
	for i := 0; i < 200; i++ {
		log = append(log, line...)
	}
	roundTrip(t, log)

	roundTrip(t, bytes.Repeat([]byte{0}, 1000))
	roundTrip(t, bytes.Repeat([]byte("ab"), 5000))
	roundTrip(t, bytes.Repeat([]byte("abc"), 1))
}

func TestRoundTripLargerThanWindow(t *testing.T) {
	// Repeats spaced wider than the 4 KiB window mixed with noise; forces the
	// encoder to respect its own window limit.
	chunk := noise(3000)
	var data []byte
	for i := 0; i < 8; i++ {
		data = append(data, chunk...)
		data = append(data, bytes.Repeat([]byte{byte('A' + i)}, 700)...)
	}
	roundTrip(t, data)
	roundTrip(t, noise(64*1024))
}

func TestCompressIsDeterministic(t *testing.T) {
	data := append(noise(8000), bytes.Repeat([]byte("retry backoff jitter "), 400)...)
	a := Compress(data)
	b := Compress(data)
	if !bytes.Equal(a, b) {
		t.Fatal("Compress produced two different encodings for the same input")
	}
	dec, err := Decompress(a)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(Compress(dec), a) {
		t.Fatal("compress -> decompress -> compress is not a fixed point")
	}
}

func TestCompressionActuallyShrinks(t *testing.T) {
	run := bytes.Repeat([]byte{0}, 1000)
	if f := Compress(run); len(f) > 64 {
		t.Fatalf("1000-byte run compressed to %d bytes; RLE should crush this", len(f))
	}
	line := []byte("worker=7 queue=email dequeued job and ran it to completion\n")
	log := bytes.Repeat(line, 170) // ~10 KB
	if f := Compress(log); len(f) >= len(log)/2 {
		t.Fatalf("repetitive 10KB log compressed to %d bytes; want < %d", len(f), len(log)/2)
	}
}

func TestIncompressiblePassthrough(t *testing.T) {
	// All 256 byte values, each exactly once: no byte ever repeats, so no run
	// and no backreference is possible. Any correct encoder must fall back to
	// the raw frame: 9-byte header + the data verbatim.
	data := make([]byte, 256)
	for i := range data {
		data[i] = byte(i)
	}
	f := Compress(data)
	if len(f) != 9+len(data) {
		t.Fatalf("incompressible input framed as %d bytes; want exactly %d (raw passthrough)", len(f), 9+len(data))
	}
	if f[4] != 0 {
		t.Fatalf("incompressible input got mode byte %d; want 0 (raw)", f[4])
	}
	if !bytes.Equal(f[9:], data) {
		t.Fatal("raw frame body must be the input verbatim")
	}
	roundTrip(t, data)
}

func TestEmptyInputFrame(t *testing.T) {
	f := Compress(nil)
	got, err := Decompress(f)
	if err != nil || len(got) != 0 {
		t.Fatalf("empty round trip: got %q, %v", got, err)
	}
	if !bytes.Equal(Compress(nil), Compress([]byte{})) {
		t.Fatal("nil and empty slice must frame identically")
	}
}

func TestDecodeHandBuiltStreams(t *testing.T) {
	cases := []struct {
		name string
		body []byte
		want string
	}{
		{"literals", []byte{0x01, 3, 'a', 'b', 'c'}, "abc"},
		{"run", []byte{0x02, 5, 'x'}, "xxxxx"},
		{"literals then run", []byte{0x01, 2, 'h', 'i', 0x02, 4, '!'}, "hi!!!!"},
		{"match copies earlier output", []byte{0x01, 6, 'a', 'b', 'c', 'd', 'e', 'f', 0x03, 0x00, 0x06, 3}, "abcdefabc"},
		{"overlapping match", []byte{0x01, 2, 'a', 'b', 0x03, 0x00, 0x02, 6}, "abababab"},
	}
	for _, c := range cases {
		got, err := Decompress(frame(1, uint32(len(c.want)), c.body))
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		if string(got) != c.want {
			t.Fatalf("%s: got %q, want %q", c.name, got, c.want)
		}
	}
}

func TestBadMagic(t *testing.T) {
	f := frame(0, 1, []byte{'z'})
	f[0] = 'X'
	if _, err := Decompress(f); !errors.Is(err, ErrBadMagic) {
		t.Fatalf("wrong magic: got %v, want ErrBadMagic", err)
	}
	junk := []byte("GIF89a-definitely-not-ours")
	if _, err := Decompress(junk); !errors.Is(err, ErrBadMagic) {
		t.Fatalf("foreign bytes: got %v, want ErrBadMagic", err)
	}
}

func TestTruncatedFrames(t *testing.T) {
	if _, err := Decompress(nil); !errors.Is(err, ErrTruncated) {
		t.Fatalf("nil frame: got %v, want ErrTruncated", err)
	}
	if _, err := Decompress([]byte{'L', 'Z', 'R', '1', 0}); !errors.Is(err, ErrTruncated) {
		t.Fatalf("header cut short: got %v, want ErrTruncated", err)
	}
	// Raw frame promising 4 bytes but carrying 2.
	if _, err := Decompress(frame(0, 4, []byte("ab"))); !errors.Is(err, ErrTruncated) {
		t.Fatalf("short raw body: got %v, want ErrTruncated", err)
	}
	// Token stream cut off mid-token, one for each token kind.
	for _, body := range [][]byte{
		{0x01, 5, 'a', 'b'},
		{0x02},
		{0x02, 9},
		{0x03, 0x00},
		{0x03, 0x00, 0x01},
	} {
		if _, err := Decompress(frame(1, 9, body)); !errors.Is(err, ErrTruncated) {
			t.Fatalf("truncated token %v: got %v, want ErrTruncated", body, err)
		}
	}
}

func TestCorruptStreams(t *testing.T) {
	cases := []struct {
		name string
		f    []byte
	}{
		{"unknown mode", frame(7, 1, []byte{'a'})},
		{"raw body longer than declared", frame(0, 1, []byte("abc"))},
		{"unknown token tag", frame(1, 1, []byte{0x09, 1, 'a'})},
		{"zero-count literal", frame(1, 1, []byte{0x01, 0, 0x01, 1, 'a'})},
		{"zero-count run", frame(1, 1, []byte{0x02, 0, 'x', 0x01, 1, 'a'})},
		{"zero-length match", frame(1, 3, []byte{0x01, 2, 'a', 'b', 0x03, 0x00, 0x01, 0})},
		{"match distance zero", frame(1, 5, []byte{0x01, 2, 'a', 'b', 0x03, 0x00, 0x00, 3})},
		{"match reaches before start", frame(1, 5, []byte{0x01, 2, 'a', 'b', 0x03, 0x00, 0x03, 3})},
		{"match with no output yet", frame(1, 3, []byte{0x03, 0x00, 0x01, 3})},
		{"stream shorter than declared", frame(1, 4, []byte{0x01, 3, 'a', 'b', 'c'})},
		{"stream longer than declared", frame(1, 2, []byte{0x01, 3, 'a', 'b', 'c'})},
		{"run overshoots declared length", frame(1, 4, []byte{0x02, 200, '-'})},
	}
	for _, c := range cases {
		if _, err := Decompress(c.f); !errors.Is(err, ErrCorrupt) {
			t.Fatalf("%s: got %v, want ErrCorrupt", c.name, err)
		}
	}
}

func TestCorruptionOfRealFrameIsCaught(t *testing.T) {
	data := bytes.Repeat([]byte("heartbeat ok "), 300)
	f := Compress(data)
	cut := f[:len(f)-3]
	if _, err := Decompress(cut); err == nil {
		t.Fatal("frame with the tail chopped off decompressed without error")
	} else if !errors.Is(err, ErrTruncated) && !errors.Is(err, ErrCorrupt) {
		t.Fatalf("chopped frame: got %v, want ErrTruncated or ErrCorrupt", err)
	}
}
