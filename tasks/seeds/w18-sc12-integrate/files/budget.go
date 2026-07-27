package wakedrain

// These limits are shared with the low-power firmware scheduler.
const (
	MaxFramesPerWake      = 8
	MaxDecodeBytesPerWake = 256
	MaxFrameBytes         = 96
)

// Admission describes what the drain should do with its current queue head.
type Admission uint8

const (
	// AdmissionDecode reserves one frame and its bytes for decoding.
	AdmissionDecode Admission = iota
	// AdmissionDropOversize reserves one frame but no decode bytes. The drain
	// must discard the head without handing it to the decoder.
	AdmissionDropOversize
	// AdmissionYield reserves nothing. The queue head belongs to a later wake.
	AdmissionYield
)

// WakeBudget is the already-ported scheduler budget. Its zero value is ready
// to use.
type WakeBudget struct {
	frames      int
	decodeBytes int
}

// Admit classifies the next FIFO head. Oversized frames consume a frame slot
// so an oversized flood cannot create unbounded work. A normal frame that
// would cross the cumulative byte budget is yielded without consuming either
// budget.
func (b *WakeBudget) Admit(frameBytes int) Admission {
	if b.frames >= MaxFramesPerWake {
		return AdmissionYield
	}
	if frameBytes > MaxFrameBytes {
		b.frames++
		return AdmissionDropOversize
	}
	if frameBytes < 0 || frameBytes > MaxDecodeBytesPerWake-b.decodeBytes {
		return AdmissionYield
	}
	b.frames++
	b.decodeBytes += frameBytes
	return AdmissionDecode
}
