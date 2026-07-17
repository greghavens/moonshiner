package wire

import (
	"errors"
	"testing"
)

func mustDecode(t *testing.T, frame []byte) Record {
	t.Helper()
	rec, err := Decode(frame)
	if err != nil {
		t.Fatalf("Decode: %v", err)
	}
	return rec
}

func TestEncodeDecodeRoundTrip(t *testing.T) {
	var enc Encoder
	want := Record{Level: "info", Source: "auth", Msg: "session refreshed for user 8841"}
	got := mustDecode(t, enc.Encode(want))
	if got != want {
		t.Fatalf("round trip: got %+v, want %+v", got, want)
	}
	empty := mustDecode(t, enc.Encode(Record{}))
	if empty != (Record{}) {
		t.Fatalf("empty record round trip: got %+v", empty)
	}
}

func TestDecodeRejectsDamagedFrames(t *testing.T) {
	var enc Encoder
	frame := append([]byte(nil), enc.Encode(Record{Level: "warn", Source: "db", Msg: "slow query"})...)

	if _, err := Decode(frame[:5]); !errors.Is(err, ErrCorrupt) {
		t.Fatalf("truncated frame: got %v, want ErrCorrupt", err)
	}
	bad := append([]byte(nil), frame...)
	bad[0] = 'X'
	if _, err := Decode(bad); !errors.Is(err, ErrCorrupt) {
		t.Fatalf("bad magic: got %v, want ErrCorrupt", err)
	}
	bad = append([]byte(nil), frame...)
	bad[len(bad)-6] ^= 0x40 // flip a payload bit
	if _, err := Decode(bad); !errors.Is(err, ErrCorrupt) {
		t.Fatalf("flipped payload bit: got %v, want ErrCorrupt", err)
	}
}

func TestEarlierFramesStillDecodeAfterMoreEncodes(t *testing.T) {
	var enc Encoder
	records := []Record{
		{Level: "info", Source: "gateway", Msg: "request served route=/api/v1/items status=200 in 12ms"},
		{Level: "warn", Source: "cache", Msg: "eviction pressure high"},
		{Level: "error", Source: "worker", Msg: "job 77 failed"},
	}
	var frames [][]byte
	for _, r := range records {
		frames = append(frames, enc.Encode(r))
	}
	for i, r := range records {
		got, err := Decode(frames[i])
		if err != nil {
			t.Fatalf("frame %d no longer decodes: %v", i, err)
		}
		if got != r {
			t.Fatalf("frame %d: got %+v, want %+v", i, got, r)
		}
	}
}

func TestFlushedBatchSurvivesNewArrivals(t *testing.T) {
	frameFor := func(r Record) []byte {
		var enc Encoder
		return enc.Encode(r)
	}
	first := Record{Level: "info", Source: "auth", Msg: "login ok user=alice"}
	second := Record{Level: "info", Source: "auth", Msg: "login ok user=bob"}
	late := Record{Level: "error", Source: "auth", Msg: "login FAILED user=mallory"}

	var b Batch
	b.Add(frameFor(first))
	b.Add(frameFor(second))
	flushed := b.Flush()
	if len(flushed) != 2 || b.Len() != 0 {
		t.Fatalf("flush: got %d frames, batch kept %d", len(flushed), b.Len())
	}

	// The shipper is still uploading `flushed` when the next records arrive.
	b.Add(frameFor(late))

	if got := mustDecode(t, flushed[0]); got != first {
		t.Fatalf("flushed[0] changed after new records arrived: got %+v, want %+v", got, first)
	}
	if got := mustDecode(t, flushed[1]); got != second {
		t.Fatalf("flushed[1] changed after new records arrived: got %+v, want %+v", got, second)
	}
	if b.Len() != 1 {
		t.Fatalf("batch should hold exactly the late frame, has %d", b.Len())
	}
}

func TestShipperInterval(t *testing.T) {
	// One encoder, one batch, exactly like the shipper's main loop.
	var enc Encoder
	var b Batch
	records := []Record{
		{Level: "info", Source: "billing", Msg: "invoice 1001 issued to acct 5501 total=129.00"},
		{Level: "info", Source: "billing", Msg: "invoice 1002 issued to acct 8802 total=54.50"},
		{Level: "warn", Source: "billing", Msg: "retrying webhook"},
	}
	for _, r := range records {
		b.Add(enc.Encode(r))
	}
	flushed := b.Flush()

	// Next interval begins before the upload of `flushed` finishes.
	b.Add(enc.Encode(Record{Level: "info", Source: "billing", Msg: "ping"}))

	if len(flushed) != len(records) {
		t.Fatalf("flushed %d frames, want %d", len(flushed), len(records))
	}
	for i, want := range records {
		got, err := Decode(flushed[i])
		if err != nil {
			t.Fatalf("uploaded frame %d rejected by receiver: %v", i, err)
		}
		if got != want {
			t.Fatalf("uploaded frame %d: got %+v, want %+v", i, got, want)
		}
	}
}
