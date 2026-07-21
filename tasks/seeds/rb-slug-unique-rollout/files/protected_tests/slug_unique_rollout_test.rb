# frozen_string_literal: true

require "slug_unique_rollout"

TestCase = Struct.new(:name, :body, keyword_init: true)
TESTS = []

def test(name, &body)
  TESTS << TestCase.new(name: name, body: body)
end

def assert(condition, message = "assertion failed")
  raise message unless condition
end

def refute(condition, message = "refutation failed")
  raise message if condition
end

def assert_equal(expected, actual, message = nil)
  return if expected == actual

  raise(message || "expected #{expected.inspect}, got #{actual.inspect}")
end

def assert_nil(actual)
  raise "expected nil, got #{actual.inspect}" unless actual.nil?
end

def assert_raises(error_class)
  yield
rescue error_class => error
  error
else
  raise "expected #{error_class}, but nothing was raised"
end

def build_store(rows)
  SlugUniqueRollout::Store.new(rows)
end

test "deterministic collision policy and legacy aliases" do
  store = build_store(
    [
      { id: 1, title: "  Hello, World! ", legacy_slug: "old/first" },
      { id: 2, title: "Hello---World", legacy_slug: "old/second" }
    ]
  )
  rollout = SlugUniqueRollout::Rollout.new(store)
  rollout.expand!

  first = rollout.backfill_batch(limit: 1)
  second = rollout.backfill_batch(cursor: first.cursor, limit: 1)
  created = store.create(title: "HELLO WORLD", legacy_slug: "old/third")

  assert_equal("hello-world", store.post(1).slug)
  assert_equal("hello-world-2", store.post(2).slug)
  assert_equal("hello-world-3", created.slug)
  assert_equal(1, store.find_by_slug("old/first").id)
  assert_equal(2, store.find_by_slug("old/second").id)
  assert_equal(created.id, store.find_by_slug("old/third").id)
  assert(second.done)
end

test "batches are bounded and a failed batch is safe to retry" do
  store = build_store(
    [
      { id: 3, title: "Gamma", legacy_slug: "old/3" },
      { id: 2, title: "Beta", legacy_slug: "old/2" },
      { id: 1, title: "Alpha", legacy_slug: "old/1" }
    ]
  )
  rollout = SlugUniqueRollout::Rollout.new(store)
  rollout.expand!

  error = assert_raises(RuntimeError) do
    rollout.backfill_batch(
      limit: 2,
      before_slug_commit: lambda do |id, _candidate|
        raise "injected commit failure" if id == 2
      end
    )
  end
  assert_equal("injected commit failure", error.message)
  assert_equal("alpha", store.post(1).slug)
  assert_nil(store.post(2).slug)
  assert_equal(1, store.write_count(1))

  retried = rollout.backfill_batch(limit: 2)
  assert_equal([1, 2], retried.scanned_ids)
  assert_equal([2], retried.updated_ids)
  assert_equal(2, retried.cursor)
  assert_equal(1, store.write_count(1), "retry must not rewrite the completed row")

  last = rollout.backfill_batch(cursor: retried.cursor, limit: 2)
  assert_equal([3], last.scanned_ids)
  assert_equal([3], last.updated_ids)
  assert(last.done)
end

test "in-flight backfill slug is reserved against concurrent create" do
  store = build_store(
    [{ id: 1, title: "Race Winner", legacy_slug: "old/race" }]
  )
  rollout = SlugUniqueRollout::Rollout.new(store)
  rollout.expand!

  selected = Queue.new
  release = Queue.new
  worker = Thread.new do
    rollout.backfill_batch(
      limit: 1,
      before_slug_commit: lambda do |_id, candidate|
        selected << candidate
        release.pop
      end
    )
  end

  begin
    assert_equal("race-winner", selected.pop)
    created = store.create(title: "Race Winner", legacy_slug: "old/new")
    assert_equal("race-winner-2", created.slug)
    release << true
    worker.value

    assert_equal("race-winner", store.post(1).slug)
    assert_equal(1, store.find_by_slug("old/race").id)
    assert_equal(created.id, store.find_by_slug("old/new").id)
    assert_equal({}, store.instance_variable_get(:@slug_reservations),
                 "a successful claim must release its reservation")
  ensure
    release << true if worker.alive?
    worker.join
  end
end

test "in-flight backfills reserve slugs against each other" do
  store = build_store(
    [
      { id: 1, title: "Same Title", legacy_slug: "old/first" },
      { id: 2, title: "Same Title", legacy_slug: "old/second" }
    ]
  )
  rollout = SlugUniqueRollout::Rollout.new(store)
  rollout.expand!

  selected = Queue.new
  release = Queue.new
  workers = []
  workers << Thread.new do
    rollout.backfill_batch(
      limit: 1,
      before_slug_commit: lambda do |_id, candidate|
        selected << candidate
        release.pop
      end
    )
  end

  begin
    assert_equal("same-title", selected.pop)
    workers << Thread.new do
      rollout.backfill_batch(
        cursor: 1,
        limit: 1,
        before_slug_commit: lambda do |_id, candidate|
          selected << candidate
          release.pop
        end
      )
    end

    assert_equal("same-title-2", selected.pop)
    2.times { release << true }
    workers.each(&:value)

    assert_equal("same-title", store.post(1).slug)
    assert_equal("same-title-2", store.post(2).slug)
    assert_equal({}, store.instance_variable_get(:@slug_reservations))
  ensure
    workers.each { release << true if _1.alive? }
    workers.each(&:join)
  end
end

test "a failed claim releases its slug reservation" do
  store = build_store(
    [{ id: 1, title: "Retry Me", legacy_slug: "old/retry" }]
  )
  rollout = SlugUniqueRollout::Rollout.new(store)
  rollout.expand!

  assert_raises(RuntimeError) do
    rollout.backfill_batch(
      limit: 1,
      before_slug_commit: lambda do |_id, _candidate|
        raise "injected failure"
      end
    )
  end

  created = store.create(title: "Retry Me", legacy_slug: "old/created")
  assert_equal("retry-me", created.slug,
               "the failed claim must not retain the lowest suffix")

  retried = rollout.backfill_batch(limit: 1)
  assert_equal([1], retried.updated_ids)
  assert_equal("retry-me-2", store.post(1).slug)
end

test "finalization enforces required unique slugs and closes legacy writes" do
  store = build_store(
    [{ id: 7, title: "Ready", legacy_slug: "old/ready" }]
  )
  rollout = SlugUniqueRollout::Rollout.new(store)
  rollout.expand!

  assert_raises(SlugUniqueRollout::MigrationIncomplete) { rollout.finalize! }
  refute(store.unique_slugs_enforced?)

  result = rollout.backfill_batch(limit: 10)
  assert(result.done)
  assert(rollout.finalize!)
  assert(store.unique_slugs_enforced?)

  assert_raises(SlugUniqueRollout::ConstraintViolation) do
    store.insert_legacy(title: "Too late", legacy_slug: "old/late")
  end

  created = store.create(title: "Ready", legacy_slug: "old/after")
  assert_equal("ready-2", created.slug)
  assert_equal(created.id, store.find_by_slug("old/after").id)
end

test "failed finalization leaves enforcement disabled" do
  duplicates = build_store(
    [
      { id: 1, title: "First", legacy_slug: "old/first", slug: "same" },
      { id: 2, title: "Second", legacy_slug: "old/second", slug: "same" }
    ]
  )
  duplicate_rollout = SlugUniqueRollout::Rollout.new(duplicates)
  duplicate_rollout.expand!

  assert_raises(SlugUniqueRollout::DuplicateSlug) do
    duplicate_rollout.finalize!
  end
  refute(duplicates.unique_slugs_enforced?)
  inserted = duplicates.insert_legacy(title: "Still allowed", legacy_slug: "old/third")
  assert_nil(inserted.slug)

  alias_conflict = build_store(
    [
      { id: 1, title: "Canonical", legacy_slug: "old/one", slug: "old/two" },
      { id: 2, title: "Alias", legacy_slug: "old/two", slug: "canonical-two" }
    ]
  )
  conflict_rollout = SlugUniqueRollout::Rollout.new(alias_conflict)
  conflict_rollout.expand!

  assert_raises(SlugUniqueRollout::DuplicateSlug) do
    conflict_rollout.finalize!
  end
  refute(alias_conflict.unique_slugs_enforced?)

  missing_alias = build_store(
    [{ id: 1, title: "Missing Alias", legacy_slug: "old/missing", slug: "ready" }]
  )
  missing_alias.instance_variable_get(:@aliases).delete("old/missing")
  missing_alias_rollout = SlugUniqueRollout::Rollout.new(missing_alias)
  missing_alias_rollout.expand!

  assert_raises(SlugUniqueRollout::MigrationIncomplete) do
    missing_alias_rollout.finalize!
  end
  refute(missing_alias.unique_slugs_enforced?)
end

test "empty titles use the post fallback and limits must be positive" do
  store = build_store(
    [{ id: 1, title: " !!! ", legacy_slug: "old/empty" }]
  )
  rollout = SlugUniqueRollout::Rollout.new(store)
  rollout.expand!

  assert_raises(ArgumentError) { rollout.backfill_batch(limit: 0) }
  assert_raises(ArgumentError) { rollout.backfill_batch(limit: 1.5) }
  rollout.backfill_batch(limit: 1)
  assert_equal("post", store.post(1).slug)
end

failures = []
TESTS.each do |example|
  example.body.call
  puts "PASS #{example.name}"
rescue StandardError => error
  failures << [example.name, error]
  warn "FAIL #{example.name}: #{error.class}: #{error.message}"
end

if failures.empty?
  puts "#{TESTS.length} tests passed"
else
  warn "#{failures.length} of #{TESTS.length} tests failed"
  exit 1
end
