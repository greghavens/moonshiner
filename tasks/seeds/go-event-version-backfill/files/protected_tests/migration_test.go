package protected_tests

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"testing"

	backfill "example.com/eventbackfill"
)

func mustStore(t *testing.T, events ...backfill.Event) *backfill.FakeStore {
	t.Helper()
	store, err := backfill.NewFakeStore(events)
	if err != nil {
		t.Fatalf("NewFakeStore: %v", err)
	}
	return store
}

func mustMigrator(
	t *testing.T,
	store backfill.Store,
	batchSize int,
	writeRetries int,
	conflictRetries int,
) *backfill.Migrator {
	t.Helper()
	migrator, err := backfill.NewMigrator(
		store,
		batchSize,
		writeRetries,
		conflictRetries,
	)
	if err != nil {
		t.Fatalf("NewMigrator: %v", err)
	}
	return migrator
}

func payload(
	eventType string,
	timestamp string,
	extra string,
) []byte {
	return []byte(fmt.Sprintf(
		`{"type":%q,"timestamp":%q,%s}`,
		eventType,
		timestamp,
		extra,
	))
}

func snapshot(
	t *testing.T,
	store *backfill.FakeStore,
	eventID string,
) backfill.Event {
	t.Helper()
	event, err := store.Snapshot(eventID)
	if err != nil {
		t.Fatalf("Snapshot(%q): %v", eventID, err)
	}
	return event
}

func rawObject(t *testing.T, value []byte) map[string]json.RawMessage {
	t.Helper()
	var object map[string]json.RawMessage
	if err := json.Unmarshal(value, &object); err != nil {
		t.Fatalf("decode payload %s: %v", value, err)
	}
	return object
}

func rawString(t *testing.T, object map[string]json.RawMessage, key string) string {
	t.Helper()
	raw, exists := object[key]
	if !exists {
		t.Fatalf("payload is missing %q: %v", key, object)
	}
	var value string
	if err := json.Unmarshal(raw, &value); err != nil {
		t.Fatalf("decode %q: %v", key, err)
	}
	return value
}

func compactJSON(t *testing.T, value []byte) string {
	t.Helper()
	var output bytes.Buffer
	if err := json.Compact(&output, value); err != nil {
		t.Fatalf("compact JSON %s: %v", value, err)
	}
	return output.String()
}

type spyStore struct {
	backfill.Store
	listTokens   []string
	listLimits   []int
	rewriteOrder []string
	operations   []string
	saves        int
}

func (s *spyStore) ListBatch(
	ctx context.Context,
	token string,
	limit int,
) (backfill.Batch, error) {
	s.listTokens = append(s.listTokens, token)
	s.listLimits = append(s.listLimits, limit)
	return s.Store.ListBatch(ctx, token, limit)
}

func (s *spyStore) RewriteV2(
	ctx context.Context,
	eventID string,
	expectedRevision uint64,
	operationID string,
	payload []byte,
) (backfill.RewriteResult, error) {
	s.rewriteOrder = append(s.rewriteOrder, eventID)
	s.operations = append(s.operations, operationID)
	return s.Store.RewriteV2(
		ctx,
		eventID,
		expectedRevision,
		operationID,
		payload,
	)
}

func (s *spyStore) SaveCheckpoint(
	ctx context.Context,
	migrationID string,
	expectedRevision uint64,
	token string,
	complete bool,
) error {
	s.saves++
	return s.Store.SaveCheckpoint(
		ctx,
		migrationID,
		expectedRevision,
		token,
		complete,
	)
}

func TestBoundedBatchesPreserveStorageOrderPayloadAndOpaqueTokens(t *testing.T) {
	base := mustStore(t,
		backfill.Event{
			ID: "zeta", Stream: "orders", Sequence: 41,
			SchemaVersion: 1, Revision: 7,
			Payload: payload(
				"order.created",
				"2032-04-05T06:07:08Z",
				`"event_type":"stale-reserved","tenant":"north",`+
					`"huge":900719925474099312345,`+
					`"nested":{"keep":[1,{"x":true}]}`,
			),
		},
		backfill.Event{
			ID: "alpha", Stream: "orders", Sequence: 42,
			SchemaVersion: 2, Revision: 11,
			Payload: []byte(
				`{"type":"order.paid","timestamp":"2032-04-05T07:00:00Z",` +
					`"event_type":"order.paid","occurred_at":"2032-04-05T07:00:00Z",` +
					`"publisher_extra":"untouched"}`,
			),
		},
		backfill.Event{
			ID: "mu", Stream: "orders", Sequence: 43,
			SchemaVersion: 1, Revision: 2,
			Payload: payload(
				"order.shipped",
				"2032-04-06T01:02:03Z",
				`"tags":["fragile","priority"],"nullable":null`,
			),
		},
		backfill.Event{
			ID: "beta", Stream: "invoices", Sequence: 9,
			SchemaVersion: 1, Revision: 5,
			Payload: payload(
				"invoice.sent",
				"2032-04-07T01:00:00Z",
				`"vendor":{"code":"V-7"},"enabled":false`,
			),
		},
		backfill.Event{
			ID: "omega", Stream: "invoices", Sequence: 10,
			SchemaVersion: 1, Revision: 6,
			Payload: payload(
				"invoice.viewed",
				"2032-04-08T01:00:00Z",
				`"source":"portal"`,
			),
		},
	)
	spy := &spyStore{Store: base}
	migrator := mustMigrator(t, spy, 3, 1, 1)

	first, err := migrator.RunBatch(context.Background(), "events-v2")
	if err != nil {
		t.Fatalf("first RunBatch: %v", err)
	}
	if first != (backfill.Stats{
		Batches: 1, Scanned: 3, Rewritten: 2, AlreadyV2: 1,
	}) {
		t.Fatalf("first stats = %+v", first)
	}
	if !reflect.DeepEqual(spy.rewriteOrder, []string{"zeta", "mu"}) {
		t.Fatalf("rewrite order = %#v, want storage order", spy.rewriteOrder)
	}
	if !reflect.DeepEqual(spy.listLimits, []int{3}) {
		t.Fatalf("list limits = %#v", spy.listLimits)
	}
	if base.PhysicalRewrites("beta") != 0 ||
		base.PhysicalRewrites("omega") != 0 {
		t.Fatal("RunBatch processed beyond its configured bound")
	}

	zeta := snapshot(t, base, "zeta")
	if zeta.SchemaVersion != 2 || zeta.Revision != 8 ||
		zeta.Stream != "orders" || zeta.Sequence != 41 {
		t.Fatalf("zeta envelope changed incorrectly: %+v", zeta)
	}
	zetaObject := rawObject(t, zeta.Payload)
	if rawString(t, zetaObject, "type") != "order.created" ||
		rawString(t, zetaObject, "timestamp") != "2032-04-05T06:07:08Z" {
		t.Fatal("legacy reader fields did not survive the additive upgrade")
	}
	if rawString(t, zetaObject, "event_type") != "order.created" ||
		rawString(t, zetaObject, "occurred_at") != "2032-04-05T06:07:08Z" {
		t.Fatal("v2 reader fields were not derived from the legacy fields")
	}
	if compactJSON(t, zetaObject["huge"]) != "900719925474099312345" ||
		compactJSON(t, zetaObject["nested"]) !=
			`{"keep":[1,{"x":true}]}` ||
		rawString(t, zetaObject, "tenant") != "north" {
		t.Fatalf("unknown fields were not preserved: %s", zeta.Payload)
	}
	alpha := snapshot(t, base, "alpha")
	if alpha.Revision != 11 || base.PhysicalRewrites("alpha") != 0 ||
		rawString(t, rawObject(t, alpha.Payload), "publisher_extra") !=
			"untouched" {
		t.Fatalf("existing v2 event was rewritten: %+v", alpha)
	}

	afterFirst := base.Checkpoint("events-v2")
	if afterFirst.Complete || afterFirst.Token == "" ||
		afterFirst.Revision != 1 {
		t.Fatalf("first checkpoint = %+v", afterFirst)
	}
	second, err := migrator.RunBatch(context.Background(), "events-v2")
	if err != nil {
		t.Fatalf("second RunBatch: %v", err)
	}
	if second != (backfill.Stats{
		Batches: 1, Scanned: 2, Rewritten: 2,
	}) {
		t.Fatalf("second stats = %+v", second)
	}
	if len(spy.listTokens) != 2 || spy.listTokens[1] != afterFirst.Token {
		t.Fatalf(
			"second list tokens = %#v, checkpoint token = %q",
			spy.listTokens,
			afterFirst.Token,
		)
	}
	final := base.Checkpoint("events-v2")
	if !final.Complete || final.Token == "" || final.Revision != 2 {
		t.Fatalf("final checkpoint = %+v", final)
	}
	if got := []uint64{
		snapshot(t, base, "zeta").Sequence,
		snapshot(t, base, "alpha").Sequence,
		snapshot(t, base, "mu").Sequence,
		snapshot(t, base, "beta").Sequence,
		snapshot(t, base, "omega").Sequence,
	}; !reflect.DeepEqual(got, []uint64{41, 42, 43, 9, 10}) {
		t.Fatalf("event positions changed: %v", got)
	}

	lists, rewrites, saves := len(spy.listTokens), len(spy.rewriteOrder), spy.saves
	again, err := migrator.RunBatch(context.Background(), "events-v2")
	if err != nil || again != (backfill.Stats{}) {
		t.Fatalf("completed rerun = (%+v, %v)", again, err)
	}
	if len(spy.listTokens) != lists || len(spy.rewriteOrder) != rewrites ||
		spy.saves != saves {
		t.Fatal("completed rerun performed store work after loading checkpoint")
	}
}

func TestOperationIDIsDeterministicAndScopedToMigration(t *testing.T) {
	operationFor := func(t *testing.T, migrationID string) string {
		t.Helper()
		base := mustStore(t, backfill.Event{
			ID: "stable-event", Stream: "s", Sequence: 1,
			SchemaVersion: 1, Revision: 7,
			Payload: payload(
				"stable",
				"2034-01-01T00:00:00Z",
				`"keep":true`,
			),
		})
		spy := &spyStore{Store: base}
		migrator := mustMigrator(t, spy, 1, 0, 0)
		if _, err := migrator.RunBatch(context.Background(), migrationID); err != nil {
			t.Fatalf("RunBatch(%q): %v", migrationID, err)
		}
		if len(spy.operations) != 1 || spy.operations[0] == "" {
			t.Fatalf("operations for %q = %#v", migrationID, spy.operations)
		}
		return spy.operations[0]
	}

	first := operationFor(t, "migration-a")
	repeated := operationFor(t, "migration-a")
	otherMigration := operationFor(t, "migration-b")
	if first != repeated {
		t.Fatalf("operation ID is not deterministic: %q != %q", first, repeated)
	}
	if first == otherMigration {
		t.Fatalf("operation ID is not scoped to migration: %q", first)
	}
}

func TestUpgradePayloadDoesNotMutateInputAndReplacesReservedFields(t *testing.T) {
	input := []byte(
		`{"type":"current","timestamp":"2034-02-03T04:05:06Z",` +
			`"event_type":"stale-type","occurred_at":"stale-time","keep":17}`,
	)
	original := append([]byte(nil), input...)
	upgraded, err := backfill.UpgradePayloadV1(input)
	if err != nil {
		t.Fatalf("UpgradePayloadV1: %v", err)
	}
	if !bytes.Equal(input, original) {
		t.Fatalf("input payload was mutated: got %s, want %s", input, original)
	}
	object := rawObject(t, upgraded)
	if rawString(t, object, "event_type") != "current" ||
		rawString(t, object, "occurred_at") != "2034-02-03T04:05:06Z" ||
		compactJSON(t, object["keep"]) != "17" {
		t.Fatalf("reserved or unrelated fields are wrong: %s", upgraded)
	}
}

type fixedBatchStore struct {
	backfill.Store
	batch backfill.Batch
}

func (s *fixedBatchStore) ListBatch(
	context.Context,
	string,
	int,
) (backfill.Batch, error) {
	return s.batch, nil
}

func TestInvalidEventsFailWithoutCheckpointAdvancement(t *testing.T) {
	invalidPayloads := []struct {
		name    string
		payload []byte
	}{
		{name: "invalid JSON", payload: []byte(`{"type":`)},
		{name: "non-object", payload: []byte(`["event","timestamp"]`)},
		{name: "missing timestamp", payload: []byte(`{"type":"event"}`)},
		{name: "empty type", payload: []byte(
			`{"type":"","timestamp":"2034-01-01"}`,
		)},
		{name: "non-string timestamp", payload: []byte(
			`{"type":"event","timestamp":17}`,
		)},
	}
	for _, test := range invalidPayloads {
		t.Run(test.name, func(t *testing.T) {
			base := mustStore(t, backfill.Event{
				ID: "invalid", Stream: "s", Sequence: 1,
				SchemaVersion: 1, Revision: 1, Payload: test.payload,
			})
			stats, err := mustMigrator(t, base, 1, 0, 0).RunBatch(
				context.Background(),
				"invalid-payload",
			)
			if err == nil {
				t.Fatal("RunBatch accepted malformed v1 payload")
			}
			if stats != (backfill.Stats{Scanned: 1}) ||
				base.PhysicalRewrites("invalid") != 0 ||
				base.Checkpoint("invalid-payload") != (backfill.Checkpoint{}) {
				t.Fatalf("invalid payload caused side effects: stats=%+v", stats)
			}
		})
	}

	t.Run("unsupported version", func(t *testing.T) {
		base := mustStore(t)
		store := &fixedBatchStore{
			Store: base,
			batch: backfill.Batch{
				Events: []backfill.Event{{
					ID: "future", Stream: "s", Sequence: 1,
					SchemaVersion: 3, Revision: 1,
					Payload: payload(
						"future",
						"2034-01-01T00:00:00Z",
						`"keep":true`,
					),
				}},
				NextToken: "opaque-final",
				Complete:  true,
			},
		}
		stats, err := mustMigrator(t, store, 1, 0, 0).RunBatch(
			context.Background(),
			"unsupported",
		)
		if !errors.Is(err, backfill.ErrUnsupportedVersion) {
			t.Fatalf("error = %v, want unsupported version", err)
		}
		if stats != (backfill.Stats{Scanned: 1}) ||
			base.Checkpoint("unsupported") != (backfill.Checkpoint{}) {
			t.Fatalf("unsupported event caused side effects: stats=%+v", stats)
		}
	})
}

var errCheckpointUnavailable = fmt.Errorf(
	"checkpoint service unavailable: %w",
	backfill.ErrTransient,
)

type rejectCheckpointOnceStore struct {
	backfill.Store
	remaining int
}

func (s *rejectCheckpointOnceStore) SaveCheckpoint(
	ctx context.Context,
	migrationID string,
	expectedRevision uint64,
	token string,
	complete bool,
) error {
	if s.remaining > 0 {
		s.remaining--
		return errCheckpointUnavailable
	}
	return s.Store.SaveCheckpoint(
		ctx,
		migrationID,
		expectedRevision,
		token,
		complete,
	)
}

func TestCheckpointFailureReplaysPageWithoutRewritingEvents(t *testing.T) {
	base := mustStore(t,
		backfill.Event{
			ID: "one", Stream: "s", Sequence: 1,
			SchemaVersion: 1, Revision: 1,
			Payload: payload("one", "2035-01-01T00:00:00Z", `"x":1`),
		},
		backfill.Event{
			ID: "two", Stream: "s", Sequence: 2,
			SchemaVersion: 1, Revision: 1,
			Payload: payload("two", "2035-01-02T00:00:00Z", `"x":2`),
		},
		backfill.Event{
			ID: "three", Stream: "s", Sequence: 3,
			SchemaVersion: 1, Revision: 1,
			Payload: payload("three", "2035-01-03T00:00:00Z", `"x":3`),
		},
	)
	reject := &rejectCheckpointOnceStore{Store: base, remaining: 1}
	spy := &spyStore{Store: reject}
	migrator := mustMigrator(t, spy, 2, 1, 1)

	first, err := migrator.RunBatch(context.Background(), "restart")
	if !errors.Is(err, errCheckpointUnavailable) {
		t.Fatalf("first error = %v, want checkpoint error identity", err)
	}
	if first != (backfill.Stats{Scanned: 2, Rewritten: 2}) {
		t.Fatalf("first partial stats = %+v", first)
	}
	if checkpoint := base.Checkpoint("restart"); checkpoint !=
		(backfill.Checkpoint{}) {
		t.Fatalf("failed batch advanced checkpoint: %+v", checkpoint)
	}
	if base.PhysicalRewrites("one") != 1 ||
		base.PhysicalRewrites("two") != 1 {
		t.Fatal("first attempt should physically rewrite both events once")
	}

	replayed, err := migrator.RunBatch(context.Background(), "restart")
	if err != nil {
		t.Fatalf("replayed RunBatch: %v", err)
	}
	if replayed != (backfill.Stats{
		Batches: 1, Scanned: 2, AlreadyV2: 2,
	}) {
		t.Fatalf("replayed stats = %+v", replayed)
	}
	if len(spy.listTokens) < 2 || spy.listTokens[0] != "" ||
		spy.listTokens[1] != "" {
		t.Fatalf("failed checkpoint was not replayed from its boundary: %#v", spy.listTokens)
	}
	if base.PhysicalRewrites("one") != 1 ||
		base.PhysicalRewrites("two") != 1 {
		t.Fatal("page replay physically rewrote an event more than once")
	}
}

var errRewriteCommitUnknown = fmt.Errorf(
	"rewrite response lost: %w",
	backfill.ErrTransient,
)

type commitThenErrorRewriteStore struct {
	backfill.Store
	remaining  int
	operations []string
}

func (s *commitThenErrorRewriteStore) RewriteV2(
	ctx context.Context,
	eventID string,
	expectedRevision uint64,
	operationID string,
	payload []byte,
) (backfill.RewriteResult, error) {
	s.operations = append(s.operations, operationID)
	result, err := s.Store.RewriteV2(
		ctx,
		eventID,
		expectedRevision,
		operationID,
		payload,
	)
	if err == nil && s.remaining > 0 {
		s.remaining--
		return result, errRewriteCommitUnknown
	}
	return result, err
}

func TestAmbiguousRewriteRetriesSameOperationExactlyOnce(t *testing.T) {
	base := mustStore(t, backfill.Event{
		ID: "ambiguous", Stream: "s", Sequence: 1,
		SchemaVersion: 1, Revision: 9,
		Payload: payload(
			"ambiguous",
			"2036-01-01T00:00:00Z",
			`"unknown":{"retain":true}`,
		),
	})
	store := &commitThenErrorRewriteStore{Store: base, remaining: 1}
	migrator := mustMigrator(t, store, 1, 1, 0)

	stats, err := migrator.RunBatch(context.Background(), "ambiguous-write")
	if err != nil {
		t.Fatalf("RunBatch: %v", err)
	}
	if stats != (backfill.Stats{
		Batches: 1, Scanned: 1, Rewritten: 1, WriteRetries: 1,
	}) {
		t.Fatalf("stats = %+v", stats)
	}
	if len(store.operations) != 2 ||
		store.operations[0] == "" ||
		store.operations[0] != store.operations[1] {
		t.Fatalf("rewrite operation IDs = %#v", store.operations)
	}
	if base.PhysicalRewrites("ambiguous") != 1 {
		t.Fatalf(
			"physical rewrites = %d, want exactly one",
			base.PhysicalRewrites("ambiguous"),
		)
	}
	if !base.Checkpoint("ambiguous-write").Complete {
		t.Fatal("successful idempotent replay did not advance checkpoint")
	}
}

var errBusyWriter = fmt.Errorf("write transport busy: %w", backfill.ErrTransient)
var errPermanentWriter = errors.New("permanent write failure")

type alwaysTransientRewriteStore struct {
	backfill.Store
	calls int
}

func (s *alwaysTransientRewriteStore) RewriteV2(
	context.Context,
	string,
	uint64,
	string,
	[]byte,
) (backfill.RewriteResult, error) {
	s.calls++
	return backfill.RewriteResult{}, errBusyWriter
}

func TestWriteRetryBudgetCountsRetriesAfterInitialAttempt(t *testing.T) {
	base := mustStore(t, backfill.Event{
		ID: "busy", Stream: "s", Sequence: 1,
		SchemaVersion: 1, Revision: 1,
		Payload: payload("busy", "2037-01-01T00:00:00Z", `"x":true`),
	})
	store := &alwaysTransientRewriteStore{Store: base}
	migrator := mustMigrator(t, store, 1, 2, 0)

	stats, err := migrator.RunBatch(context.Background(), "retry-budget")
	if !errors.Is(err, errBusyWriter) {
		t.Fatalf("error = %v, want transient error identity", err)
	}
	if stats != (backfill.Stats{
		Scanned: 1, WriteRetries: 2,
	}) {
		t.Fatalf("partial stats = %+v", stats)
	}
	if store.calls != 3 {
		t.Fatalf("rewrite calls = %d, want initial + two retries", store.calls)
	}
	if base.PhysicalRewrites("busy") != 0 ||
		base.Checkpoint("retry-budget") != (backfill.Checkpoint{}) {
		t.Fatal("failed write changed event or checkpoint")
	}
}

type permanentRewriteStore struct {
	backfill.Store
	calls int
}

func (s *permanentRewriteStore) RewriteV2(
	context.Context,
	string,
	uint64,
	string,
	[]byte,
) (backfill.RewriteResult, error) {
	s.calls++
	return backfill.RewriteResult{}, errPermanentWriter
}

func TestPermanentWriteErrorIsNotRetried(t *testing.T) {
	base := mustStore(t, backfill.Event{
		ID: "permanent", Stream: "s", Sequence: 1,
		SchemaVersion: 1, Revision: 1,
		Payload: payload(
			"permanent",
			"2038-01-01T00:00:00Z",
			`"keep":true`,
		),
	})
	store := &permanentRewriteStore{Store: base}
	stats, err := mustMigrator(t, store, 1, 5, 0).RunBatch(
		context.Background(),
		"permanent-write",
	)
	if !errors.Is(err, errPermanentWriter) {
		t.Fatalf("error = %v, want permanent write error", err)
	}
	if stats != (backfill.Stats{Scanned: 1}) || store.calls != 1 ||
		base.Checkpoint("permanent-write") != (backfill.Checkpoint{}) {
		t.Fatalf("permanent error was retried or checkpointed: stats=%+v calls=%d", stats, store.calls)
	}
}

type conflictOnceStore struct {
	backfill.Store
	base       *backfill.FakeStore
	eventID    string
	remaining  int
	becomesV2  bool
	operations []string
}

func (s *conflictOnceStore) RewriteV2(
	ctx context.Context,
	eventID string,
	expectedRevision uint64,
	operationID string,
	upgraded []byte,
) (backfill.RewriteResult, error) {
	s.operations = append(s.operations, operationID)
	if eventID == s.eventID && s.remaining > 0 {
		s.remaining--
		err := s.base.Mutate(eventID, func(event *backfill.Event) error {
			if s.becomesV2 {
				event.SchemaVersion = 2
				event.Payload = []byte(
					`{"type":"writer.v2","timestamp":"2040-02-01T00:00:00Z",` +
						`"event_type":"writer.v2","occurred_at":"2040-02-01T00:00:00Z",` +
						`"writer_owned":"yes"}`,
				)
				return nil
			}
			event.Payload = payload(
				"writer.updated",
				"2040-01-02T03:04:05Z",
				`"live_unknown":{"generation":2},"event_type":"stale"`,
			)
			return nil
		})
		if err != nil {
			return backfill.RewriteResult{}, err
		}
	}
	return s.Store.RewriteV2(
		ctx,
		eventID,
		expectedRevision,
		operationID,
		upgraded,
	)
}

func TestConflictReloadsLatestV1AndRecomputesWithoutLosingFields(t *testing.T) {
	base := mustStore(t, backfill.Event{
		ID: "live-v1", Stream: "s", Sequence: 5,
		SchemaVersion: 1, Revision: 4,
		Payload: payload(
			"original",
			"2040-01-01T00:00:00Z",
			`"original_unknown":"old"`,
		),
	})
	store := &conflictOnceStore{
		Store: base, base: base, eventID: "live-v1", remaining: 1,
	}
	migrator := mustMigrator(t, store, 1, 0, 1)

	stats, err := migrator.RunBatch(context.Background(), "concurrent-v1")
	if err != nil {
		t.Fatalf("RunBatch: %v", err)
	}
	if stats != (backfill.Stats{
		Batches: 1, Scanned: 1, Rewritten: 1, Conflicts: 1,
	}) {
		t.Fatalf("stats = %+v", stats)
	}
	if len(store.operations) != 2 ||
		store.operations[0] != store.operations[1] {
		t.Fatalf("operation changed across conflict: %#v", store.operations)
	}
	current := snapshot(t, base, "live-v1")
	object := rawObject(t, current.Payload)
	if current.SchemaVersion != 2 ||
		rawString(t, object, "type") != "writer.updated" ||
		rawString(t, object, "event_type") != "writer.updated" ||
		rawString(t, object, "occurred_at") != "2040-01-02T03:04:05Z" ||
		compactJSON(t, object["live_unknown"]) != `{"generation":2}` {
		t.Fatalf("latest v1 payload was not recomputed: %s", current.Payload)
	}
	if _, staleUnknownSurvived := object["original_unknown"]; staleUnknownSurvived {
		t.Fatal("migration resurrected a field removed by the concurrent writer")
	}
}

func TestConflictWithCoexistingV2WriterDoesNotOverwriteIt(t *testing.T) {
	base := mustStore(t, backfill.Event{
		ID: "live-v2", Stream: "s", Sequence: 6,
		SchemaVersion: 1, Revision: 2,
		Payload: payload("old", "2040-01-01T00:00:00Z", `"old":true`),
	})
	store := &conflictOnceStore{
		Store: base, base: base, eventID: "live-v2",
		remaining: 1, becomesV2: true,
	}
	migrator := mustMigrator(t, store, 1, 0, 1)

	stats, err := migrator.RunBatch(context.Background(), "coexist")
	if err != nil {
		t.Fatalf("RunBatch: %v", err)
	}
	if stats != (backfill.Stats{
		Batches: 1, Scanned: 1, AlreadyV2: 1, Conflicts: 1,
	}) {
		t.Fatalf("stats = %+v", stats)
	}
	current := snapshot(t, base, "live-v2")
	if base.PhysicalRewrites("live-v2") != 0 ||
		rawString(t, rawObject(t, current.Payload), "writer_owned") != "yes" {
		t.Fatalf("coexisting v2 writer was overwritten: %+v", current)
	}
}

type alwaysConflictStore struct {
	backfill.Store
	calls int
}

func (s *alwaysConflictStore) RewriteV2(
	context.Context,
	string,
	uint64,
	string,
	[]byte,
) (backfill.RewriteResult, error) {
	s.calls++
	return backfill.RewriteResult{}, backfill.ErrVersionConflict
}

func TestConflictRetryBudgetCountsRetriesAfterInitialAttempt(t *testing.T) {
	base := mustStore(t, backfill.Event{
		ID: "contended", Stream: "s", Sequence: 1,
		SchemaVersion: 1, Revision: 1,
		Payload: payload(
			"contended",
			"2041-01-01T00:00:00Z",
			`"unknown":"value"`,
		),
	})
	store := &alwaysConflictStore{Store: base}
	migrator := mustMigrator(t, store, 1, 0, 2)

	stats, err := migrator.RunBatch(context.Background(), "conflict-budget")
	if !errors.Is(err, backfill.ErrVersionConflict) {
		t.Fatalf("error = %v, want version conflict", err)
	}
	if stats != (backfill.Stats{Scanned: 1, Conflicts: 3}) {
		t.Fatalf("partial stats = %+v", stats)
	}
	if store.calls != 3 {
		t.Fatalf("rewrite calls = %d, want initial + two retries", store.calls)
	}
	if base.Checkpoint("conflict-budget") != (backfill.Checkpoint{}) {
		t.Fatal("conflicted batch advanced its checkpoint")
	}
}

var errCheckpointCommitUnknown = fmt.Errorf(
	"checkpoint response lost: %w",
	backfill.ErrTransient,
)
var errCheckpointPermanent = errors.New("permanent checkpoint response failure")

type commitThenErrorCheckpointStore struct {
	backfill.Store
	remaining        int
	mismatchToken    bool
	mismatchComplete bool
	returnErr        error
}

func (s *commitThenErrorCheckpointStore) SaveCheckpoint(
	ctx context.Context,
	migrationID string,
	expectedRevision uint64,
	token string,
	complete bool,
) error {
	if s.remaining == 0 {
		return s.Store.SaveCheckpoint(
			ctx,
			migrationID,
			expectedRevision,
			token,
			complete,
		)
	}
	s.remaining--
	durableToken := token
	durableComplete := complete
	if s.mismatchToken {
		durableToken = ""
	}
	if s.mismatchComplete {
		durableComplete = !complete
	}
	if err := s.Store.SaveCheckpoint(
		ctx,
		migrationID,
		expectedRevision,
		durableToken,
		durableComplete,
	); err != nil {
		return err
	}
	if s.returnErr != nil {
		return s.returnErr
	}
	return errCheckpointCommitUnknown
}

func TestAmbiguousCheckpointSaveIsAcceptedOnlyWhenTargetIsDurable(t *testing.T) {
	newBase := func(t *testing.T) *backfill.FakeStore {
		return mustStore(t, backfill.Event{
			ID: "checkpointed", Stream: "s", Sequence: 1,
			SchemaVersion: 1, Revision: 1,
			Payload: payload(
				"checkpointed",
				"2042-01-01T00:00:00Z",
				`"keep":"me"`,
			),
		})
	}

	matchingErrors := []struct {
		name string
		err  error
	}{
		{name: "transient error", err: errCheckpointCommitUnknown},
		{name: "permanent error", err: errCheckpointPermanent},
	}
	for _, test := range matchingErrors {
		t.Run("matching durable state/"+test.name, func(t *testing.T) {
			base := newBase(t)
			store := &commitThenErrorCheckpointStore{
				Store: base, remaining: 1, returnErr: test.err,
			}
			migrator := mustMigrator(t, store, 1, 0, 0)
			stats, err := migrator.RunBatch(context.Background(), "checkpoint")
			if err != nil {
				t.Fatalf("RunBatch: %v", err)
			}
			if stats != (backfill.Stats{
				Batches: 1, Scanned: 1, Rewritten: 1,
			}) {
				t.Fatalf("stats = %+v", stats)
			}
			if checkpoint := base.Checkpoint("checkpoint"); !checkpoint.Complete ||
				checkpoint.Token == "" {
				t.Fatalf("durable checkpoint = %+v", checkpoint)
			}
		})
	}

	mismatches := []struct {
		name             string
		mismatchToken    bool
		mismatchComplete bool
	}{
		{name: "token only", mismatchToken: true},
		{name: "completion only", mismatchComplete: true},
		{name: "both fields", mismatchToken: true, mismatchComplete: true},
	}
	for _, test := range mismatches {
		t.Run("different durable state/"+test.name, func(t *testing.T) {
			base := newBase(t)
			store := &commitThenErrorCheckpointStore{
				Store:            base,
				remaining:        1,
				mismatchToken:    test.mismatchToken,
				mismatchComplete: test.mismatchComplete,
			}
			migrator := mustMigrator(t, store, 1, 0, 0)
			stats, err := migrator.RunBatch(context.Background(), "checkpoint")
			if !errors.Is(err, errCheckpointCommitUnknown) {
				t.Fatalf("error = %v, want original save error", err)
			}
			if stats != (backfill.Stats{Scanned: 1, Rewritten: 1}) {
				t.Fatalf("partial stats = %+v", stats)
			}
			checkpoint := base.Checkpoint("checkpoint")
			if checkpoint.Revision != 1 ||
				checkpoint.Complete == test.mismatchComplete ||
				(checkpoint.Token == "") != test.mismatchToken {
				t.Fatalf("unexpected durable checkpoint: %+v", checkpoint)
			}
		})
	}
}

type oversizedBatchStore struct {
	backfill.Store
}

func (s *oversizedBatchStore) ListBatch(
	ctx context.Context,
	token string,
	limit int,
) (backfill.Batch, error) {
	return s.Store.ListBatch(ctx, token, limit+1)
}

func TestOversizedStorePageIsRejectedBeforeAnyRewrite(t *testing.T) {
	base := mustStore(t,
		backfill.Event{
			ID: "a", Stream: "s", Sequence: 1,
			SchemaVersion: 1, Revision: 1,
			Payload: payload("a", "2043-01-01T00:00:00Z", `"x":1`),
		},
		backfill.Event{
			ID: "b", Stream: "s", Sequence: 2,
			SchemaVersion: 1, Revision: 1,
			Payload: payload("b", "2043-01-02T00:00:00Z", `"x":2`),
		},
	)
	migrator := mustMigrator(
		t,
		&oversizedBatchStore{Store: base},
		1,
		0,
		0,
	)

	stats, err := migrator.RunBatch(context.Background(), "oversized")
	if !errors.Is(err, backfill.ErrBatchTooLarge) {
		t.Fatalf("error = %v, want oversized batch", err)
	}
	if stats != (backfill.Stats{}) ||
		base.PhysicalRewrites("a") != 0 ||
		base.PhysicalRewrites("b") != 0 ||
		base.Checkpoint("oversized") != (backfill.Checkpoint{}) {
		t.Fatalf("oversized batch caused side effects: stats=%+v", stats)
	}
}
