# frozen_string_literal: true

require "status_enum_migration"

Test = Struct.new(:name, :body, keyword_init: true)
TESTS = []

def test(name, &body)
  TESTS << Test.new(name: name, body: body)
end

def assert(condition, message = "assertion failed")
  raise message unless condition
end

def assert_equal(expected, actual)
  return if expected == actual

  raise "expected #{expected.inspect}, got #{actual.inspect}"
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

test "new writes use the v1 enum and reject noncanonical values" do
  assert_equal(
    { "status" => "active", "status_version" => 1, "status_code" => 20 },
    StatusEnumMigration::StatusWriter.attributes_for("active")
  )

  assert_raises(StatusEnumMigration::UnknownStatus) do
    StatusEnumMigration::StatusWriter.attributes_for("enabled")
  end
  assert_raises(StatusEnumMigration::UnknownStatus) do
    StatusEnumMigration::StatusWriter.attributes_for("invented")
  end
end

test "current reads accept legacy spellings and versioned values" do
  assert_equal(
    "active",
    StatusEnumMigration::StatusReader.read("status" => "  Enabled  ")
  )
  assert_equal(
    "suspended",
    StatusEnumMigration::StatusReader.read(
      "status" => "old shadow value",
      "status_version" => 1,
      "status_code" => 30
    )
  )

  assert_raises(StatusEnumMigration::UnsupportedVersion) do
    StatusEnumMigration::StatusReader.read("status_version" => 2, "status_code" => 30)
  end
end

test "backfill maps known rows and reports unmappable rows without data loss" do
  store = StatusEnumMigration::InMemoryStore.new(
    [
      {
        "id" => "known",
        "status" => "Awaiting Review",
        "metadata" => { "source" => "import", "flags" => ["keep"] }
      },
      { "id" => "unknown", "status" => "waiting for legal", "owner" => "ops" }
    ]
  )

  result = StatusEnumMigration::Backfill.new(store).run(limit: 2)

  assert_equal(["known"], result.migrated_ids)
  assert_equal(
    [{ "id" => "unknown", "status" => "waiting for legal" }],
    result.unmappable_rows
  )
  assert_nil(result.resume_token)
  assert_equal(1, store.document("known")["status_version"])
  assert_equal(10, store.document("known")["status_code"])
  assert_equal(
    { "source" => "import", "flags" => ["keep"] },
    store.document("known")["metadata"]
  )
  assert_equal(
    { "id" => "unknown", "status" => "waiting for legal", "owner" => "ops" },
    store.document("unknown")
  )
  assert_equal(0, store.write_count("unknown"))
end

test "migrated rows remain readable by the legacy reader" do
  store = StatusEnumMigration::InMemoryStore.new(
    [{ "id" => "legacy", "status" => "On Hold", "account_id" => 7 }]
  )

  StatusEnumMigration::Backfill.new(store).run(limit: 1)

  assert_equal(
    "On Hold",
    StatusEnumMigration::LegacyStatusReader.read(store.document("legacy"))
  )
end

test "opaque resume tokens continue in storage order and retries are idempotent" do
  store = StatusEnumMigration::InMemoryStore.new(
    [
      { "id" => "first", "status" => "pending" },
      { "id" => "second", "status" => "archived" },
      {
        "id" => "already",
        "status" => "active",
        "status_version" => 1,
        "status_code" => 20
      }
    ]
  )
  backfill = StatusEnumMigration::Backfill.new(store)

  first = backfill.run(limit: 1)
  assert_equal(["first"], first.migrated_ids)
  assert(first.resume_token.include?("\x00".b), "test store should issue a binary token")

  second = backfill.run(resume_token: first.resume_token, limit: 1)
  assert_equal(["second"], second.migrated_ids)

  third = backfill.run(resume_token: second.resume_token, limit: 1)
  assert_equal([], third.migrated_ids)
  assert_nil(third.resume_token)
  assert_equal(0, store.write_count("already"))

  retried = backfill.run(limit: 1)
  assert_equal([], retried.migrated_ids)
  assert_equal(1, store.write_count("first"))
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
