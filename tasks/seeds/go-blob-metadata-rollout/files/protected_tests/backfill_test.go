package protected_tests

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"testing"

	rollout "example.com/blobrollout"
)

func mustStore(t *testing.T, blobs ...rollout.Blob) *rollout.FakeStore {
	t.Helper()
	store, err := rollout.NewFakeStore(blobs)
	if err != nil {
		t.Fatalf("NewFakeStore: %v", err)
	}
	return store
}

func mustMigrator(
	t *testing.T,
	store rollout.Store,
	pageSize int,
	retries int,
) *rollout.Migrator {
	t.Helper()
	migrator, err := rollout.NewMigrator(store, pageSize, retries)
	if err != nil {
		t.Fatalf("NewMigrator: %v", err)
	}
	return migrator
}

func snapshot(t *testing.T, store *rollout.FakeStore, key string) rollout.Blob {
	t.Helper()
	blob, err := store.Snapshot(key)
	if err != nil {
		t.Fatalf("Snapshot(%q): %v", key, err)
	}
	return blob
}

func checkpoint(
	t *testing.T,
	store rollout.Store,
	rolloutID string,
) rollout.Checkpoint {
	t.Helper()
	value, err := store.LoadCheckpoint(context.Background(), rolloutID)
	if err != nil {
		t.Fatalf("LoadCheckpoint(%q): %v", rolloutID, err)
	}
	return value
}

type spyStore struct {
	rollout.Store
	listCursors []string
	replaced    []string
	saves       int
}

func (s *spyStore) ListPage(
	ctx context.Context,
	cursor string,
	limit int,
) (rollout.Page, error) {
	s.listCursors = append(s.listCursors, cursor)
	return s.Store.ListPage(ctx, cursor, limit)
}

func (s *spyStore) ReplaceMetadata(
	ctx context.Context,
	key string,
	expectedVersion uint64,
	metadata map[string]string,
) error {
	s.replaced = append(s.replaced, key)
	return s.Store.ReplaceMetadata(ctx, key, expectedVersion, metadata)
}

func (s *spyStore) SaveCheckpoint(
	ctx context.Context,
	rolloutID string,
	expectedVersion uint64,
	cursor string,
	complete bool,
) error {
	s.saves++
	return s.Store.SaveCheckpoint(
		ctx,
		rolloutID,
		expectedVersion,
		cursor,
		complete,
	)
}

func TestBackfillPaginatesPreservesMetadataAndCompletes(t *testing.T) {
	base := mustStore(t,
		rollout.Blob{
			Key: "alpha", Version: 5, ContentType: "image/png", Checksum: "sum-a",
			Metadata: map[string]string{"owner": "media"},
		},
		rollout.Blob{
			Key: "bravo", Version: 2, ContentType: "text/plain", Checksum: "sum-b",
			Metadata: map[string]string{"content-type": "", "keep": "yes"},
		},
		rollout.Blob{
			Key: "charlie", Version: 9, ContentType: "", Checksum: "sum-c",
			Metadata: map[string]string{"sha256": "publisher-value", "tier": "cold"},
		},
		rollout.Blob{
			Key: "delta", Version: 3, ContentType: "application/json", Checksum: "sum-d",
			Metadata: map[string]string{
				"content-type": "custom/type",
				"sha256":       "custom-sum",
				"owner":        "api",
			},
		},
		rollout.Blob{
			Key: "echo", Version: 1, ContentType: "video/mp4", Checksum: "sum-e",
		},
	)
	spy := &spyStore{Store: base}
	migrator := mustMigrator(t, spy, 2, 2)

	stats, err := migrator.Run(context.Background(), "rollout-main")
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	wantStats := rollout.Stats{
		Pages: 3, Scanned: 5, Updated: 3, Unchanged: 2,
	}
	if stats != wantStats {
		t.Fatalf("stats = %+v, want %+v", stats, wantStats)
	}
	if len(spy.listCursors) != 3 || spy.listCursors[0] != "" ||
		spy.listCursors[1] == "" || spy.listCursors[2] == "" ||
		spy.listCursors[1] == spy.listCursors[2] {
		t.Fatalf("opaque cursors were not passed page to page: %#v", spy.listCursors)
	}
	if spy.saves != 3 {
		t.Fatalf("checkpoint saves = %d, want 3", spy.saves)
	}

	alpha := snapshot(t, base, "alpha")
	if alpha.Version != 6 || !reflect.DeepEqual(alpha.Metadata, map[string]string{
		"owner": "media", "content-type": "image/png", "sha256": "sum-a",
	}) {
		t.Fatalf("alpha was not merged correctly: %+v", alpha)
	}
	bravo := snapshot(t, base, "bravo")
	if bravo.Version != 3 || !reflect.DeepEqual(bravo.Metadata, map[string]string{
		"content-type": "", "keep": "yes", "sha256": "sum-b",
	}) {
		t.Fatalf("bravo's authoritative empty value was not preserved: %+v", bravo)
	}
	charlie := snapshot(t, base, "charlie")
	if charlie.Version != 9 || !reflect.DeepEqual(charlie.Metadata, map[string]string{
		"sha256": "publisher-value", "tier": "cold",
	}) {
		t.Fatalf("charlie should be unchanged: %+v", charlie)
	}
	delta := snapshot(t, base, "delta")
	if delta.Version != 3 || delta.Metadata["content-type"] != "custom/type" ||
		delta.Metadata["sha256"] != "custom-sum" || delta.Metadata["owner"] != "api" {
		t.Fatalf("delta's existing values changed: %+v", delta)
	}
	echo := snapshot(t, base, "echo")
	if echo.Version != 2 || echo.Metadata["content-type"] != "video/mp4" ||
		echo.Metadata["sha256"] != "sum-e" {
		t.Fatalf("echo was not backfilled: %+v", echo)
	}

	saved := checkpoint(t, base, "rollout-main")
	if !saved.Complete || saved.Cursor != "" || saved.Version != 3 {
		t.Fatalf("final checkpoint = %+v", saved)
	}
	lists, replaces, saves := len(spy.listCursors), len(spy.replaced), spy.saves
	again, err := migrator.Run(context.Background(), "rollout-main")
	if err != nil || again != (rollout.Stats{}) {
		t.Fatalf("completed rerun = (%+v, %v), want zero success", again, err)
	}
	if len(spy.listCursors) != lists || len(spy.replaced) != replaces ||
		spy.saves != saves {
		t.Fatal("completed rerun performed store work after loading checkpoint")
	}
}

var errInjectedReplace = errors.New("injected replacement failure")

type failOnceStore struct {
	rollout.Store
	key       string
	remaining int
}

func (s *failOnceStore) ReplaceMetadata(
	ctx context.Context,
	key string,
	expectedVersion uint64,
	metadata map[string]string,
) error {
	if key == s.key && s.remaining > 0 {
		s.remaining--
		return fmt.Errorf("storage unavailable: %w", errInjectedReplace)
	}
	return s.Store.ReplaceMetadata(ctx, key, expectedVersion, metadata)
}

func TestFailureDoesNotAdvancePartialPageAndRestartUsesDurableCursor(t *testing.T) {
	base := mustStore(t,
		rollout.Blob{Key: "a", Version: 1, ContentType: "a/type", Checksum: "a-sum"},
		rollout.Blob{Key: "b", Version: 1, ContentType: "b/type", Checksum: "b-sum"},
		rollout.Blob{Key: "c", Version: 1, ContentType: "c/type", Checksum: "c-sum"},
		rollout.Blob{Key: "d", Version: 1, ContentType: "d/type", Checksum: "d-sum"},
		rollout.Blob{Key: "e", Version: 1, ContentType: "e/type", Checksum: "e-sum"},
	)
	failing := &failOnceStore{Store: base, key: "d", remaining: 1}
	firstSpy := &spyStore{Store: failing}
	first := mustMigrator(t, firstSpy, 2, 2)

	stats, err := first.Run(context.Background(), "resume")
	if !errors.Is(err, errInjectedReplace) {
		t.Fatalf("Run error = %v, want injected identity", err)
	}
	if stats != (rollout.Stats{Pages: 1, Scanned: 4, Updated: 3}) {
		t.Fatalf("partial stats = %+v", stats)
	}
	saved := checkpoint(t, base, "resume")
	if saved.Complete || saved.Cursor == "" || saved.Version != 1 {
		t.Fatalf("partial checkpoint = %+v", saved)
	}
	if snapshot(t, base, "c").Version != 2 {
		t.Fatal("c should have committed before the later page failure")
	}

	secondSpy := &spyStore{Store: failing}
	second := mustMigrator(t, secondSpy, 2, 0)
	resumed, err := second.Run(context.Background(), "resume")
	if err != nil {
		t.Fatalf("resumed Run: %v", err)
	}
	if resumed != (rollout.Stats{
		Pages: 2, Scanned: 3, Updated: 2, Unchanged: 1,
	}) {
		t.Fatalf("resumed stats = %+v", resumed)
	}
	if len(secondSpy.listCursors) != 2 ||
		secondSpy.listCursors[0] != saved.Cursor {
		t.Fatalf(
			"restart listed from %#v, durable cursor was %q",
			secondSpy.listCursors,
			saved.Cursor,
		)
	}
	if snapshot(t, base, "c").Version != 2 {
		t.Fatal("replayed successful object should not be rewritten")
	}
	if final := checkpoint(t, base, "resume"); !final.Complete ||
		final.Version != 3 {
		t.Fatalf("resumed final checkpoint = %+v", final)
	}
}

type concurrentWriterStore struct {
	rollout.Store
	base      *rollout.FakeStore
	key       string
	remaining int
	mutate    func(*rollout.Blob)
	attempts  int
}

func (s *concurrentWriterStore) ReplaceMetadata(
	ctx context.Context,
	key string,
	expectedVersion uint64,
	metadata map[string]string,
) error {
	if key == s.key {
		s.attempts++
		if s.remaining > 0 {
			s.remaining--
			if err := s.base.Mutate(key, func(blob *rollout.Blob) error {
				s.mutate(blob)
				return nil
			}); err != nil {
				return err
			}
		}
	}
	return s.Store.ReplaceMetadata(ctx, key, expectedVersion, metadata)
}

func TestVersionConflictReloadsAndPreservesConcurrentSourceAndMetadata(t *testing.T) {
	base := mustStore(t, rollout.Blob{
		Key:         "photo",
		Version:     7,
		ContentType: "image/old",
		Checksum:    "old-sum",
		Metadata:    map[string]string{"owner": "original"},
	})
	store := &concurrentWriterStore{
		Store: base, base: base, key: "photo", remaining: 1,
		mutate: func(blob *rollout.Blob) {
			blob.ContentType = "image/new"
			blob.Checksum = "new-sum"
			blob.Metadata["owner"] = "live-writer"
			blob.Metadata["trace"] = "preserve-me"
			blob.Metadata["content-type"] = "writer/type"
		},
	}
	migrator := mustMigrator(t, store, 10, 2)

	stats, err := migrator.Run(context.Background(), "concurrent")
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if stats != (rollout.Stats{
		Pages: 1, Scanned: 1, Updated: 1, Conflicts: 1,
	}) {
		t.Fatalf("stats = %+v", stats)
	}
	got := snapshot(t, base, "photo")
	wantMetadata := map[string]string{
		"owner":        "live-writer",
		"trace":        "preserve-me",
		"content-type": "writer/type",
		"sha256":       "new-sum",
	}
	if got.Version != 9 || !reflect.DeepEqual(got.Metadata, wantMetadata) {
		t.Fatalf("concurrent update was lost: %+v", got)
	}
}

func TestConflictBudgetStopsWithoutCheckpointAdvance(t *testing.T) {
	for _, retries := range []int{0, 1, 2} {
		t.Run(fmt.Sprintf("%d_retries", retries), func(t *testing.T) {
			base := mustStore(t, rollout.Blob{
				Key: "busy", Version: 1,
				ContentType: "busy/type", Checksum: "busy-sum",
				Metadata: map[string]string{"writer-count": "0"},
			})
			count := 0
			store := &concurrentWriterStore{
				Store: base, base: base, key: "busy", remaining: 10,
				mutate: func(blob *rollout.Blob) {
					count++
					blob.Metadata["writer-count"] = fmt.Sprint(count)
				},
			}
			migrator := mustMigrator(t, store, 1, retries)

			stats, err := migrator.Run(context.Background(), "budget")
			if !errors.Is(err, rollout.ErrVersionConflict) {
				t.Fatalf("Run error = %v, want ErrVersionConflict", err)
			}
			wantConflicts := retries + 1
			if stats != (rollout.Stats{
				Scanned: 1, Conflicts: wantConflicts,
			}) {
				t.Fatalf(
					"stats = %+v, want %d attempted conflicts",
					stats,
					wantConflicts,
				)
			}
			if store.attempts != wantConflicts {
				t.Fatalf(
					"replace attempts = %d, want initial plus %d retries",
					store.attempts,
					retries,
				)
			}
			if got := checkpoint(t, base, "budget"); got != (rollout.Checkpoint{}) {
				t.Fatalf("checkpoint advanced after exhausted conflict: %+v", got)
			}
			got := snapshot(t, base, "busy")
			if got.Metadata["writer-count"] != fmt.Sprint(wantConflicts) {
				t.Fatalf("concurrent values were lost: %+v", got)
			}
			if _, ok := got.Metadata["sha256"]; ok {
				t.Fatalf("failed migration unexpectedly installed metadata: %+v", got)
			}
		})
	}
}

var errAliasedMap = errors.New("migrator mutated a store-owned metadata map")

type watchedMap struct {
	value    map[string]string
	original map[string]string
}

type aliasGuardStore struct {
	rollout.Store
	watched []watchedMap
}

func copyMap(value map[string]string) map[string]string {
	if value == nil {
		return nil
	}
	result := make(map[string]string, len(value))
	for key, item := range value {
		result[key] = item
	}
	return result
}

func (s *aliasGuardStore) watch(blob rollout.Blob) {
	s.watched = append(s.watched, watchedMap{
		value: blob.Metadata, original: copyMap(blob.Metadata),
	})
}

func (s *aliasGuardStore) ListPage(
	ctx context.Context,
	cursor string,
	limit int,
) (rollout.Page, error) {
	page, err := s.Store.ListPage(ctx, cursor, limit)
	if err == nil {
		for _, blob := range page.Blobs {
			s.watch(blob)
		}
	}
	return page, err
}

func (s *aliasGuardStore) GetBlob(
	ctx context.Context,
	key string,
) (rollout.Blob, error) {
	blob, err := s.Store.GetBlob(ctx, key)
	if err == nil {
		s.watch(blob)
	}
	return blob, err
}

func (s *aliasGuardStore) ReplaceMetadata(
	ctx context.Context,
	key string,
	expectedVersion uint64,
	metadata map[string]string,
) error {
	for _, watched := range s.watched {
		if !reflect.DeepEqual(watched.value, watched.original) {
			return errAliasedMap
		}
	}
	return s.Store.ReplaceMetadata(ctx, key, expectedVersion, metadata)
}

func TestStoreOwnedMapsAreNotMutated(t *testing.T) {
	base := mustStore(t, rollout.Blob{
		Key: "map", Version: 4, ContentType: "map/type", Checksum: "map-sum",
		Metadata: map[string]string{"owner": "store"},
	})
	writer := &concurrentWriterStore{
		Store: base, base: base, key: "map", remaining: 1,
		mutate: func(blob *rollout.Blob) {
			blob.Metadata["live"] = "preserve"
		},
	}
	guard := &aliasGuardStore{Store: writer}
	stats, err := mustMigrator(t, guard, 1, 1).Run(
		context.Background(),
		"maps",
	)
	if errors.Is(err, errAliasedMap) {
		t.Fatal("migrator mutated metadata returned by the store")
	}
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if stats != (rollout.Stats{
		Pages: 1, Scanned: 1, Updated: 1, Conflicts: 1,
	}) {
		t.Fatalf("stats = %+v", stats)
	}
	got := snapshot(t, base, "map")
	want := map[string]string{
		"owner": "store", "live": "preserve",
		"content-type": "map/type", "sha256": "map-sum",
	}
	if got.Version != 6 || !reflect.DeepEqual(got.Metadata, want) {
		t.Fatalf("conflict retry lost metadata: %+v", got)
	}
}

var (
	errCommitReportLost = errors.New("checkpoint response lost")
	errBeforeCommit     = errors.New("checkpoint write rejected")
)

type uncertainCheckpointStore struct {
	rollout.Store
	afterCommit bool
	remaining   int
	err         error
}

func (s *uncertainCheckpointStore) SaveCheckpoint(
	ctx context.Context,
	rolloutID string,
	expectedVersion uint64,
	cursor string,
	complete bool,
) error {
	if s.remaining <= 0 {
		return s.Store.SaveCheckpoint(
			ctx, rolloutID, expectedVersion, cursor, complete,
		)
	}
	s.remaining--
	if s.afterCommit {
		if err := s.Store.SaveCheckpoint(
			ctx, rolloutID, expectedVersion, cursor, complete,
		); err != nil {
			return err
		}
	}
	return s.err
}

func TestCheckpointOutcomeIsReconciledAfterSaveError(t *testing.T) {
	t.Run("committed response loss continues", func(t *testing.T) {
		base := mustStore(t,
			rollout.Blob{Key: "a", Version: 1, ContentType: "a/type"},
			rollout.Blob{Key: "b", Version: 1, ContentType: "b/type"},
			rollout.Blob{Key: "c", Version: 1, ContentType: "c/type"},
		)
		store := &uncertainCheckpointStore{
			Store: base, afterCommit: true, remaining: 1, err: errCommitReportLost,
		}
		stats, err := mustMigrator(t, store, 2, 0).Run(
			context.Background(),
			"uncertain",
		)
		if err != nil {
			t.Fatalf("Run treated a durable checkpoint as failed: %v", err)
		}
		if stats != (rollout.Stats{
			Pages: 2, Scanned: 3, Updated: 3,
		}) {
			t.Fatalf("stats = %+v", stats)
		}
		if got := checkpoint(t, base, "uncertain"); !got.Complete ||
			got.Version != 2 {
			t.Fatalf("final checkpoint = %+v", got)
		}
	})

	t.Run("uncommitted error preserves identity and cursor", func(t *testing.T) {
		base := mustStore(t,
			rollout.Blob{Key: "a", Version: 1, ContentType: "a/type"},
		)
		store := &uncertainCheckpointStore{
			Store: base, remaining: 1, err: errBeforeCommit,
		}
		stats, err := mustMigrator(t, store, 1, 0).Run(
			context.Background(),
			"rejected",
		)
		if !errors.Is(err, errBeforeCommit) {
			t.Fatalf("Run error = %v, want rejected identity", err)
		}
		if stats != (rollout.Stats{Scanned: 1, Updated: 1}) {
			t.Fatalf("partial stats = %+v", stats)
		}
		if got := checkpoint(t, base, "rejected"); got != (rollout.Checkpoint{}) {
			t.Fatalf("checkpoint advanced despite rejected save: %+v", got)
		}
	})
}

type racingCheckpointStore struct {
	rollout.Store
	base      *rollout.FakeStore
	remaining int
}

func (s *racingCheckpointStore) SaveCheckpoint(
	ctx context.Context,
	rolloutID string,
	expectedVersion uint64,
	cursor string,
	complete bool,
) error {
	if s.remaining > 0 {
		s.remaining--
		if err := s.base.SaveCheckpoint(
			ctx,
			rolloutID,
			expectedVersion,
			"foreign-writer-position",
			false,
		); err != nil {
			return err
		}
	}
	return s.Store.SaveCheckpoint(
		ctx, rolloutID, expectedVersion, cursor, complete,
	)
}

func TestCheckpointConflictNeverOverwritesForeignState(t *testing.T) {
	base := mustStore(t, rollout.Blob{
		Key: "only", Version: 1, ContentType: "only/type",
	})
	store := &racingCheckpointStore{Store: base, base: base, remaining: 1}
	stats, err := mustMigrator(t, store, 1, 0).Run(
		context.Background(),
		"race",
	)
	if !errors.Is(err, rollout.ErrCheckpointConflict) {
		t.Fatalf("Run error = %v, want checkpoint conflict", err)
	}
	if stats != (rollout.Stats{Scanned: 1, Updated: 1}) {
		t.Fatalf("partial stats = %+v", stats)
	}
	got := checkpoint(t, base, "race")
	if got.Cursor != "foreign-writer-position" || got.Complete ||
		got.Version != 1 {
		t.Fatalf("foreign checkpoint was overwritten: %+v", got)
	}
}

func TestEmptyStoreIsDurablyComplete(t *testing.T) {
	base := mustStore(t)
	stats, err := mustMigrator(t, base, 4, 1).Run(
		context.Background(),
		"empty",
	)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if stats != (rollout.Stats{Pages: 1}) {
		t.Fatalf("stats = %+v", stats)
	}
	if got := checkpoint(t, base, "empty"); !got.Complete ||
		got.Cursor != "" || got.Version != 1 {
		t.Fatalf("empty checkpoint = %+v", got)
	}
}
