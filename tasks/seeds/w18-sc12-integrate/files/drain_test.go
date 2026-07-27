package wakedrain

import (
	"errors"
	"fmt"
	"reflect"
	"testing"
)

type inboxFake struct {
	frames    []Frame
	acked     []uint64
	peekCalls int
	peekErr   error
	ackErr    error
	events    *[]string
}

func (f *inboxFake) Peek() (Frame, bool, error) {
	f.peekCalls++
	if f.peekErr != nil {
		return Frame{}, false, f.peekErr
	}
	if len(f.frames) == 0 {
		return Frame{}, false, nil
	}
	if f.events != nil {
		*f.events = append(*f.events, fmt.Sprintf("peek:%d", f.frames[0].ID))
	}
	return f.frames[0], true, nil
}

func (f *inboxFake) Ack(id uint64) error {
	if f.events != nil {
		*f.events = append(*f.events, fmt.Sprintf("ack:%d", id))
	}
	if f.ackErr != nil {
		return f.ackErr
	}
	if len(f.frames) == 0 || f.frames[0].ID != id {
		return fmt.Errorf("ack %d is not the current head", id)
	}
	f.acked = append(f.acked, id)
	f.frames = f.frames[1:]
	return nil
}

type decoderFunc func([]byte) (Command, error)

func (f decoderFunc) Decode(payload []byte) (Command, error) {
	return f(payload)
}

type storeFunc func(uint64, Command) error

func (f storeFunc) Apply(id uint64, command Command) error {
	return f(id, command)
}

type healthFake struct {
	records []int
	kicks   int
	err     error
	events  *[]string
}

func (f *healthFake) RecordMalformed(count int) error {
	f.records = append(f.records, count)
	if f.events != nil {
		*f.events = append(*f.events, fmt.Sprintf("audit:%d", count))
	}
	return f.err
}

func (f *healthFake) KickWatchdog() {
	f.kicks++
	if f.events != nil {
		*f.events = append(*f.events, "kick")
	}
}

func payload(size int, marker byte) []byte {
	p := make([]byte, size)
	if len(p) != 0 {
		p[0] = marker
	}
	return p
}

func validDecoder(events *[]string, calls *[]int) Decoder {
	return decoderFunc(func(p []byte) (Command, error) {
		*calls = append(*calls, len(p))
		if events != nil {
			*events = append(*events, fmt.Sprintf("decode:%d", p[0]))
		}
		return Command{Opcode: p[0], Argument: uint32(len(p))}, nil
	})
}

func acceptingStore(events *[]string, ids *[]uint64) Store {
	return storeFunc(func(id uint64, command Command) error {
		*ids = append(*ids, id)
		if events != nil {
			*events = append(*events, fmt.Sprintf("apply:%d", id))
		}
		return nil
	})
}

func makeFrames(count, size int) []Frame {
	frames := make([]Frame, count)
	for i := range frames {
		frames[i] = Frame{
			ID:      uint64(i + 1),
			Payload: payload(size, byte(i+1)),
		}
	}
	return frames
}

func TestHappyPathPreservesApplyAckKickOrdering(t *testing.T) {
	events := []string{}
	inbox := &inboxFake{frames: makeFrames(2, 4), events: &events}
	decodeCalls := []int{}
	storeIDs := []uint64{}
	health := &healthFake{events: &events}

	report, err := DrainWake(
		inbox,
		validDecoder(&events, &decodeCalls),
		acceptingStore(&events, &storeIDs),
		health,
	)

	if err != nil {
		t.Fatalf("DrainWake returned %v", err)
	}
	if want := (Report{Applied: 2}); report != want {
		t.Fatalf("report = %+v, want %+v", report, want)
	}
	wantEvents := []string{
		"peek:1", "decode:1", "apply:1", "ack:1", "kick",
		"peek:2", "decode:2", "apply:2", "ack:2", "kick",
	}
	if !reflect.DeepEqual(events, wantEvents) {
		t.Fatalf("events = %v, want %v", events, wantEvents)
	}
}

func TestFrameBudgetAcceptsExactLimitAndDefersNextHead(t *testing.T) {
	inbox := &inboxFake{frames: makeFrames(MaxFramesPerWake+1, 1)}
	decodeCalls := []int{}
	storeIDs := []uint64{}
	health := &healthFake{}

	report, err := DrainWake(
		inbox,
		validDecoder(nil, &decodeCalls),
		acceptingStore(nil, &storeIDs),
		health,
	)

	if err != nil {
		t.Fatalf("DrainWake returned %v", err)
	}
	if report != (Report{Applied: MaxFramesPerWake, Deferred: true}) {
		t.Fatalf("report = %+v", report)
	}
	if len(inbox.frames) != 1 || inbox.frames[0].ID != uint64(MaxFramesPerWake+1) {
		t.Fatalf("deferred queue = %+v", inbox.frames)
	}
	if len(decodeCalls) != MaxFramesPerWake || len(storeIDs) != MaxFramesPerWake {
		t.Fatalf("decode calls = %d, store calls = %d", len(decodeCalls), len(storeIDs))
	}
	if health.kicks != MaxFramesPerWake {
		t.Fatalf("watchdog kicks = %d, want %d", health.kicks, MaxFramesPerWake)
	}
}

func TestDecodeByteBudgetAcceptsExactLimitAndDefersWithoutTouching(t *testing.T) {
	inbox := &inboxFake{frames: []Frame{
		{ID: 1, Payload: payload(96, 1)},
		{ID: 2, Payload: payload(96, 2)},
		{ID: 3, Payload: payload(64, 3)},
		{ID: 4, Payload: payload(1, 4)},
	}}
	decodeCalls := []int{}
	storeIDs := []uint64{}
	health := &healthFake{}

	report, err := DrainWake(
		inbox,
		validDecoder(nil, &decodeCalls),
		acceptingStore(nil, &storeIDs),
		health,
	)

	if err != nil {
		t.Fatalf("DrainWake returned %v", err)
	}
	if report != (Report{Applied: 3, Deferred: true}) {
		t.Fatalf("report = %+v", report)
	}
	if !reflect.DeepEqual(decodeCalls, []int{96, 96, 64}) {
		t.Fatalf("decoded sizes = %v", decodeCalls)
	}
	if !reflect.DeepEqual(inbox.acked, []uint64{1, 2, 3}) {
		t.Fatalf("acked = %v", inbox.acked)
	}
	if len(inbox.frames) != 1 || inbox.frames[0].ID != 4 {
		t.Fatalf("deferred head = %+v", inbox.frames)
	}
}

func TestOversizeFloodIsBoundedByFrameSlotsAndBypassesDecoder(t *testing.T) {
	inbox := &inboxFake{
		frames: makeFrames(MaxFramesPerWake+2, MaxFrameBytes+1),
	}
	decodeCalls := []int{}
	storeIDs := []uint64{}
	health := &healthFake{}

	report, err := DrainWake(
		inbox,
		validDecoder(nil, &decodeCalls),
		acceptingStore(nil, &storeIDs),
		health,
	)

	if err != nil {
		t.Fatalf("DrainWake returned %v", err)
	}
	if report != (Report{Dropped: MaxFramesPerWake, Deferred: true}) {
		t.Fatalf("report = %+v", report)
	}
	if len(decodeCalls) != 0 || len(storeIDs) != 0 {
		t.Fatalf("oversize traffic reached decoder/store: decode=%v store=%v", decodeCalls, storeIDs)
	}
	if !reflect.DeepEqual(health.records, []int{MaxFramesPerWake}) {
		t.Fatalf("audit writes = %v, want one aggregate", health.records)
	}
	if health.kicks != 0 {
		t.Fatalf("oversize flood kicked watchdog %d times", health.kicks)
	}
	if len(inbox.frames) != 2 {
		t.Fatalf("remaining frames = %d, want 2", len(inbox.frames))
	}
}

func TestMixedMalformedTrafficCoalescesAuditAndOnlyUsefulWorkKicks(t *testing.T) {
	inbox := &inboxFake{frames: []Frame{
		{ID: 1, Payload: payload(4, 0xee)},
		{ID: 2, Payload: payload(MaxFrameBytes+1, 0xaa)},
		{ID: 3, Payload: payload(5, 0x20)},
		{ID: 4, Payload: payload(3, 0xef)},
	}}
	decodeMarkers := []byte{}
	decoder := decoderFunc(func(p []byte) (Command, error) {
		decodeMarkers = append(decodeMarkers, p[0])
		if p[0] == 0xee || p[0] == 0xef {
			return Command{}, fmt.Errorf("wire detail: %w", ErrMalformed)
		}
		return Command{Opcode: p[0]}, nil
	})
	storeIDs := []uint64{}
	health := &healthFake{}

	report, err := DrainWake(inbox, decoder, acceptingStore(nil, &storeIDs), health)

	if err != nil {
		t.Fatalf("DrainWake returned %v", err)
	}
	if report != (Report{Applied: 1, Dropped: 3}) {
		t.Fatalf("report = %+v", report)
	}
	if !reflect.DeepEqual(decodeMarkers, []byte{0xee, 0x20, 0xef}) {
		t.Fatalf("decoder markers = %x", decodeMarkers)
	}
	if !reflect.DeepEqual(storeIDs, []uint64{3}) {
		t.Fatalf("store IDs = %v", storeIDs)
	}
	if !reflect.DeepEqual(inbox.acked, []uint64{1, 2, 3, 4}) {
		t.Fatalf("acked IDs = %v", inbox.acked)
	}
	if !reflect.DeepEqual(health.records, []int{3}) {
		t.Fatalf("audit writes = %v", health.records)
	}
	if health.kicks != 1 {
		t.Fatalf("watchdog kicks = %d, want 1", health.kicks)
	}
}

func TestStoreFailureHasNoInlineRetryAndLeavesHeadOwned(t *testing.T) {
	storeErr := errors.New("flash controller busy")
	inbox := &inboxFake{frames: makeFrames(1, 4)}
	decodeCalls := []int{}
	storeCalls := 0
	store := storeFunc(func(uint64, Command) error {
		storeCalls++
		return storeErr
	})
	health := &healthFake{}

	report, err := DrainWake(inbox, validDecoder(nil, &decodeCalls), store, health)

	if !errors.Is(err, storeErr) {
		t.Fatalf("error = %v, want store error", err)
	}
	if report != (Report{}) {
		t.Fatalf("report = %+v", report)
	}
	if storeCalls != 1 {
		t.Fatalf("store calls = %d, want exactly 1", storeCalls)
	}
	if len(inbox.acked) != 0 || len(inbox.frames) != 1 {
		t.Fatalf("failing head was consumed: acked=%v remaining=%d", inbox.acked, len(inbox.frames))
	}
	if health.kicks != 0 {
		t.Fatalf("failed apply kicked watchdog")
	}
}

func TestMalformedAuditFlushesWhenLaterStoreFails(t *testing.T) {
	storeErr := errors.New("store offline")
	inbox := &inboxFake{frames: []Frame{
		{ID: 1, Payload: payload(2, 0xff)},
		{ID: 2, Payload: payload(2, 0x01)},
	}}
	decoder := decoderFunc(func(p []byte) (Command, error) {
		if p[0] == 0xff {
			return Command{}, ErrMalformed
		}
		return Command{Opcode: p[0]}, nil
	})
	health := &healthFake{}
	storeCalls := 0

	report, err := DrainWake(inbox, decoder, storeFunc(func(uint64, Command) error {
		storeCalls++
		return storeErr
	}), health)

	if !errors.Is(err, storeErr) {
		t.Fatalf("error = %v, want store error", err)
	}
	if report != (Report{Dropped: 1}) {
		t.Fatalf("report = %+v", report)
	}
	if !reflect.DeepEqual(health.records, []int{1}) {
		t.Fatalf("audit writes = %v", health.records)
	}
	if storeCalls != 1 || len(inbox.frames) != 1 || inbox.frames[0].ID != 2 {
		t.Fatalf("store calls=%d remaining=%+v", storeCalls, inbox.frames)
	}
}

func TestEarlierProcessingErrorWinsOverAuditError(t *testing.T) {
	storeErr := errors.New("store failed")
	auditErr := errors.New("audit failed")
	inbox := &inboxFake{frames: []Frame{
		{ID: 1, Payload: payload(2, 0xff)},
		{ID: 2, Payload: payload(2, 0x01)},
	}}
	decoder := decoderFunc(func(p []byte) (Command, error) {
		if p[0] == 0xff {
			return Command{}, fmt.Errorf("crc: %w", ErrMalformed)
		}
		return Command{Opcode: p[0]}, nil
	})
	health := &healthFake{err: auditErr}

	_, err := DrainWake(inbox, decoder, storeFunc(func(uint64, Command) error {
		return storeErr
	}), health)

	if !errors.Is(err, storeErr) {
		t.Fatalf("error = %v, want processing error", err)
	}
	if errors.Is(err, auditErr) {
		t.Fatalf("audit error replaced or joined processing error: %v", err)
	}
	if !reflect.DeepEqual(health.records, []int{1}) {
		t.Fatalf("audit writes = %v", health.records)
	}
}

func TestAuditOnlyFailureIsReturnedAfterDrops(t *testing.T) {
	auditErr := errors.New("audit flash failed")
	inbox := &inboxFake{frames: []Frame{
		{ID: 7, Payload: payload(MaxFrameBytes+1, 1)},
	}}
	decodeCalls := []int{}
	storeIDs := []uint64{}
	health := &healthFake{err: auditErr}

	report, err := DrainWake(
		inbox,
		validDecoder(nil, &decodeCalls),
		acceptingStore(nil, &storeIDs),
		health,
	)

	if !errors.Is(err, auditErr) {
		t.Fatalf("error = %v, want audit error", err)
	}
	if report != (Report{Dropped: 1}) {
		t.Fatalf("report = %+v", report)
	}
	if len(inbox.frames) != 0 || len(decodeCalls) != 0 || health.kicks != 0 {
		t.Fatalf("unexpected side effects: remaining=%d decode=%v kicks=%d", len(inbox.frames), decodeCalls, health.kicks)
	}
}

func TestDecoderInfrastructureErrorLeavesHeadAndDoesNotAudit(t *testing.T) {
	decodeErr := errors.New("decoder unavailable")
	inbox := &inboxFake{frames: makeFrames(1, 2)}
	storeIDs := []uint64{}
	health := &healthFake{}

	report, err := DrainWake(
		inbox,
		decoderFunc(func([]byte) (Command, error) {
			return Command{}, decodeErr
		}),
		acceptingStore(nil, &storeIDs),
		health,
	)

	if !errors.Is(err, decodeErr) {
		t.Fatalf("error = %v, want decoder error", err)
	}
	if report != (Report{}) || len(inbox.acked) != 0 || len(inbox.frames) != 1 {
		t.Fatalf("head ownership changed: report=%+v acked=%v remaining=%d", report, inbox.acked, len(inbox.frames))
	}
	if len(health.records) != 0 || health.kicks != 0 || len(storeIDs) != 0 {
		t.Fatalf("unexpected side effects: health=%+v store=%v", health, storeIDs)
	}
}

func TestAckFailureAfterApplyIsNotReportedOrKicked(t *testing.T) {
	ackErr := errors.New("inbox journal failed")
	inbox := &inboxFake{frames: makeFrames(1, 3), ackErr: ackErr}
	decodeCalls := []int{}
	storeIDs := []uint64{}
	health := &healthFake{}

	report, err := DrainWake(
		inbox,
		validDecoder(nil, &decodeCalls),
		acceptingStore(nil, &storeIDs),
		health,
	)

	if !errors.Is(err, ackErr) {
		t.Fatalf("error = %v, want ack error", err)
	}
	if report != (Report{}) {
		t.Fatalf("report = %+v", report)
	}
	if !reflect.DeepEqual(storeIDs, []uint64{1}) || len(inbox.frames) != 1 {
		t.Fatalf("apply/ownership mismatch: store=%v remaining=%d", storeIDs, len(inbox.frames))
	}
	if health.kicks != 0 {
		t.Fatalf("failed acknowledgement kicked watchdog")
	}
}

func TestMalformedAckFailureDoesNotCountOrAudit(t *testing.T) {
	ackErr := errors.New("ack failed")
	inbox := &inboxFake{frames: makeFrames(1, 2), ackErr: ackErr}
	health := &healthFake{}

	report, err := DrainWake(
		inbox,
		decoderFunc(func([]byte) (Command, error) {
			return Command{}, ErrMalformed
		}),
		storeFunc(func(uint64, Command) error {
			t.Fatal("store called for malformed frame")
			return nil
		}),
		health,
	)

	if !errors.Is(err, ackErr) {
		t.Fatalf("error = %v, want ack error", err)
	}
	if report != (Report{}) || len(health.records) != 0 || health.kicks != 0 {
		t.Fatalf("failed drop was counted: report=%+v health=%+v", report, health)
	}
}

func TestInboxErrorAndInvalidArgumentsPreserveContracts(t *testing.T) {
	peekErr := errors.New("inbox unavailable")
	decodeCalls := []int{}
	storeIDs := []uint64{}
	health := &healthFake{}
	report, err := DrainWake(
		&inboxFake{peekErr: peekErr},
		validDecoder(nil, &decodeCalls),
		acceptingStore(nil, &storeIDs),
		health,
	)
	if !errors.Is(err, peekErr) || report != (Report{}) {
		t.Fatalf("peek failure: report=%+v err=%v", report, err)
	}

	validInbox := &inboxFake{}
	validDecoder := decoderFunc(func([]byte) (Command, error) { return Command{}, nil })
	validStore := storeFunc(func(uint64, Command) error { return nil })
	cases := []struct {
		name    string
		inbox   Inbox
		decoder Decoder
		store   Store
		health  Health
	}{
		{"nil inbox", nil, validDecoder, validStore, health},
		{"nil decoder", validInbox, nil, validStore, health},
		{"nil store", validInbox, validDecoder, nil, health},
		{"nil health", validInbox, validDecoder, validStore, nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			report, err := DrainWake(tc.inbox, tc.decoder, tc.store, tc.health)
			if !errors.Is(err, ErrInvalidArgument) || report != (Report{}) {
				t.Fatalf("report=%+v err=%v", report, err)
			}
		})
	}
}
