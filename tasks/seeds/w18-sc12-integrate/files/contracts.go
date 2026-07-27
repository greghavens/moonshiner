// Package wakedrain drains durable radio commands during a bounded device wake.
package wakedrain

import "errors"

var (
	// ErrMalformed marks a payload that can be safely discarded. Decoders may
	// wrap it with diagnostic context.
	ErrMalformed = errors.New("malformed radio frame")

	// ErrInvalidArgument reports a missing DrainWake dependency.
	ErrInvalidArgument = errors.New("wakedrain: invalid argument")
)

// Frame is the durable inbox record. ID is stable across wake cycles.
type Frame struct {
	ID      uint64
	Payload []byte
}

// Command is the decoder's device-neutral command representation.
type Command struct {
	Opcode   byte
	Argument uint32
}

// Inbox owns frames until Ack succeeds. Peek never removes or advances the
// head, and Ack must be called with the current head's ID.
type Inbox interface {
	Peek() (Frame, bool, error)
	Ack(id uint64) error
}

// Decoder validates and decodes one admitted payload.
type Decoder interface {
	Decode(payload []byte) (Command, error)
}

// Store applies a command transactionally. Apply is idempotent for a frameID,
// allowing a later wake to repeat an apply whose following Ack failed.
type Store interface {
	Apply(frameID uint64, command Command) error
}

// Health exposes the two device side effects used by the drain.
type Health interface {
	// RecordMalformed persists one aggregate counter update.
	RecordMalformed(count int) error
	// KickWatchdog records useful, durably consumed command progress.
	KickWatchdog()
}

// Report summarizes work completed by one DrainWake call.
type Report struct {
	Applied  int
	Dropped  int
	Deferred bool
}
