// Package wire encodes structured log records into self-describing frames
// for the log shipper. A frame is: the 2-byte magic "LG", a version byte,
// a big-endian uint32 payload length, the payload, and a big-endian CRC-32
// (IEEE) of the payload. Payload strings are u16-length-prefixed, in the
// order level, source, message.
package wire

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"hash/crc32"
)

// ErrCorrupt reports a frame that fails structural or checksum validation.
var ErrCorrupt = errors.New("wire: corrupt frame")

const version = 1

// Record is one structured log line.
type Record struct {
	Level  string
	Source string
	Msg    string
}

// Encoder converts records into frames. Keeping one Encoder per shipper
// goroutine amortizes buffer allocations across records.
type Encoder struct {
	buf     bytes.Buffer
	scratch bytes.Buffer
}

func writeString(buf *bytes.Buffer, s string) {
	var n [2]byte
	binary.BigEndian.PutUint16(n[:], uint16(len(s)))
	buf.Write(n[:])
	buf.WriteString(s)
}

// Encode renders one record as a wire frame.
func (e *Encoder) Encode(r Record) []byte {
	e.scratch.Reset()
	writeString(&e.scratch, r.Level)
	writeString(&e.scratch, r.Source)
	writeString(&e.scratch, r.Msg)
	payload := e.scratch.Bytes()

	e.buf.Reset()
	e.buf.WriteString("LG")
	e.buf.WriteByte(version)
	var word [4]byte
	binary.BigEndian.PutUint32(word[:], uint32(len(payload)))
	e.buf.Write(word[:])
	e.buf.Write(payload)
	binary.BigEndian.PutUint32(word[:], crc32.ChecksumIEEE(payload))
	e.buf.Write(word[:])
	return e.buf.Bytes()
}

// Batch accumulates frames between shipper upload intervals.
type Batch struct {
	frames [][]byte
}

// Add queues one frame for the next upload.
func (b *Batch) Add(frame []byte) {
	b.frames = append(b.frames, frame)
}

// Len reports how many frames are queued.
func (b *Batch) Len() int {
	return len(b.frames)
}

// Flush returns everything accumulated so far and readies the batch for
// the next interval.
func (b *Batch) Flush() [][]byte {
	out := b.frames
	b.frames = b.frames[:0]
	return out
}

// Decode parses and validates one frame.
func Decode(frame []byte) (Record, error) {
	if len(frame) < 11 {
		return Record{}, fmt.Errorf("%w: %d bytes is not even a header", ErrCorrupt, len(frame))
	}
	if frame[0] != 'L' || frame[1] != 'G' {
		return Record{}, fmt.Errorf("%w: bad magic % x", ErrCorrupt, frame[:2])
	}
	if frame[2] != version {
		return Record{}, fmt.Errorf("%w: unsupported version %d", ErrCorrupt, frame[2])
	}
	n := int(binary.BigEndian.Uint32(frame[3:7]))
	if len(frame) != 11+n {
		return Record{}, fmt.Errorf("%w: declared payload %d bytes, frame carries %d", ErrCorrupt, n, len(frame)-11)
	}
	payload := frame[7 : 7+n]
	sum := binary.BigEndian.Uint32(frame[7+n:])
	if crc32.ChecksumIEEE(payload) != sum {
		return Record{}, fmt.Errorf("%w: checksum mismatch", ErrCorrupt)
	}
	var rec Record
	for _, field := range []*string{&rec.Level, &rec.Source, &rec.Msg} {
		if len(payload) < 2 {
			return Record{}, fmt.Errorf("%w: payload cut off in string header", ErrCorrupt)
		}
		l := int(binary.BigEndian.Uint16(payload[:2]))
		payload = payload[2:]
		if len(payload) < l {
			return Record{}, fmt.Errorf("%w: string runs past payload end", ErrCorrupt)
		}
		*field = string(payload[:l])
		payload = payload[l:]
	}
	if len(payload) != 0 {
		return Record{}, fmt.Errorf("%w: %d trailing payload bytes", ErrCorrupt, len(payload))
	}
	return rec, nil
}
