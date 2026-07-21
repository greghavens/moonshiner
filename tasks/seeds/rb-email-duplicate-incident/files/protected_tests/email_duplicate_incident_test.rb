# frozen_string_literal: true

require "email_duplicate_incident"

class EmailDuplicateIncidentTest
  include EmailDuplicateIncident

  INCIDENT_DIR = File.expand_path("../incident", __dir__)

  class AssertionFailure < StandardError; end

  def assert(value, message = "expected a truthy value")
    raise AssertionFailure, message unless value
  end

  def assert_equal(expected, actual, message = nil)
    return if expected == actual

    raise AssertionFailure, message || "expected #{expected.inspect}, got #{actual.inspect}"
  end

  def assert_empty(value, message = nil)
    assert(value.empty?, message || "expected #{value.inspect} to be empty")
  end

  def assert_nil(value, message = nil)
    assert(value.nil?, message || "expected nil, got #{value.inspect}")
  end

  def assert_includes(value, member, message = nil)
    assert(value.include?(member), message || "expected #{value.inspect} to include #{member.inspect}")
  end

  def assert_match(pattern, value, message = nil)
    assert(pattern.match?(value), message || "expected #{value.inspect} to match #{pattern.inspect}")
  end

  def assert_raises(error_class)
    yield
  rescue error_class => error
    return error
  rescue StandardError => error
    raise AssertionFailure,
          "expected #{error_class}, got #{error.class}: #{error.message}"
  else
    raise AssertionFailure, "expected #{error_class} to be raised"
  end

  def build_system(conflicts_before_commit:)
    audit = AuditTrail.new
    store = TransactionStore.new(
      accounts: [{ id: 41, email: "owner@example.test", renewed_by_job_id: nil }],
      audit: audit,
      conflicts_before_commit: conflicts_before_commit
    )
    mailer = RecordingMailer.new(audit: audit)
    queue = DurableMailQueue.new(store: store, mailer: mailer, audit: audit)
    job = RenewalReceiptJob.new(store: store, mail_queue: queue, audit: audit)
    [job, queue, store, mailer, audit]
  end

  def test_incident_logs_correlate_duplicate_delivery_with_transaction_retry
    job_log = File.read(File.join(INCIDENT_DIR, "job.log"))
    audit_log = File.read(File.join(INCIDENT_DIR, "audit.log"))

    deliveries = job_log.lines.grep(/event=provider_accepted/)
    assert_equal 2, deliveries.length
    assert deliveries.all? { |line| line.include?("delivery_key=renewal-receipt:41") }
    assert_includes audit_log, "transaction_attempt=1 event=rollback reason=serialization_failure"
    assert_includes audit_log, "transaction_attempt=2 event=commit"
  end

  def test_retry_commits_one_durable_intent_before_any_external_delivery
    job, queue, store, mailer, audit = build_system(conflicts_before_commit: 1)

    assert_equal :renewed, job.perform(account_id: 41, job_id: "renewal-job-884")

    assert_equal 2, store.transaction_attempts
    assert_equal "renewal-job-884", store.account(41).renewed_by_job_id
    assert_empty mailer.deliveries, "a transaction attempt must not invoke the mail provider"
    assert_equal [
      {
        delivery_key: "renewal-receipt:41",
        account_id: 41,
        recipient: "owner@example.test",
        template: "renewal_receipt"
      }
    ], store.pending_mail.map(&:to_h)
    assert_equal [1], audit.entries("transaction_rolled_back").map { |entry| entry[:transaction_attempt] }
    assert_equal [2], audit.entries("transaction_committed").map { |entry| entry[:transaction_attempt] }

    assert_equal ["renewal-receipt:41"], queue.drain
    assert_equal 1, mailer.deliveries.length
    delivered_intent = mailer.deliveries.first.reject do |field, _value|
      field == :provider_message_id
    end
    assert_equal(
      {
        delivery_key: "renewal-receipt:41",
        account_id: 41,
        recipient: "owner@example.test",
        template: "renewal_receipt"
      },
      delivered_intent
    )
    events = audit.entries.map { |entry| entry[:event] }
    assert events.index("provider_accepted_mail") > events.index("transaction_committed"),
           "the provider must only be invoked after the transaction commits"
    assert_empty store.pending_mail
    assert_empty queue.drain
    assert_equal 1, mailer.deliveries.length
  end

  def test_exhausted_retries_leave_no_renewal_intent_or_delivery
    job, _queue, store, mailer, audit = build_system(conflicts_before_commit: 3)

    error = assert_raises(SerializationFailure) do
      job.perform(account_id: 41, job_id: "renewal-job-884")
    end

    assert_match(/after 3 attempts/, error.message)
    assert_equal 3, store.transaction_attempts
    assert_nil store.account(41).renewed_by_job_id
    assert_empty store.pending_mail
    assert_empty mailer.deliveries
    assert_equal 3, audit.entries("transaction_rolled_back").length
    assert_equal 1, audit.entries("job_failed").length
  end

  def test_replaying_a_completed_job_is_idempotent
    job, queue, store, mailer, audit = build_system(conflicts_before_commit: 1)

    assert_equal :renewed, job.perform(account_id: 41, job_id: "renewal-job-884")
    staged_intents = audit.entries("mail_intent_staged").length
    assert_equal :already_renewed, job.perform(account_id: 41, job_id: "renewal-job-884")
    assert_equal ["renewal-receipt:41"], store.pending_mail.map(&:delivery_key)
    assert_equal staged_intents, audit.entries("mail_intent_staged").length,
                 "a replay must not stage another mail intent"

    queue.drain
    assert_equal :already_renewed, job.perform(account_id: 41, job_id: "renewal-job-884")
    assert_empty store.pending_mail
    assert_equal 1, mailer.deliveries.length
    assert_equal staged_intents, audit.entries("mail_intent_staged").length,
                 "a replay after delivery must not stage another mail intent"
  end

  def test_transaction_body_errors_propagate_without_side_effects
    job, _queue, store, mailer, audit = build_system(conflicts_before_commit: 0)

    assert_raises(KeyError) do
      job.perform(account_id: 999, job_id: "renewal-job-884")
    end

    assert_equal 1, store.transaction_attempts
    assert_nil store.account(41).renewed_by_job_id
    assert_empty store.pending_mail
    assert_empty mailer.deliveries
    assert_equal ["KeyError"], audit.entries("job_failed").map { |entry| entry[:error] }
  end
end

failures = []
tests = EmailDuplicateIncidentTest.instance_methods(false).grep(/\Atest_/).sort
tests.each do |test_name|
  begin
    EmailDuplicateIncidentTest.new.public_send(test_name)
    puts "PASS #{test_name}"
  rescue StandardError => error
    failures << [test_name, error]
    warn "FAIL #{test_name}: #{error.class}: #{error.message}"
  end
end

unless failures.empty?
  warn "#{failures.length} of #{tests.length} tests failed"
  exit 1
end

puts "#{tests.length} tests passed"
