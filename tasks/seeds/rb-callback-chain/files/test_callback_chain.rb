require "minitest/autorun"
require_relative "subscription_system"

class CallbackChainTest < Minitest::Test
  def self.run_order
    :alpha
  end

  def build_system
    queue = Subscriptions::JobQueue.new
    callbacks = Subscriptions::SubscriptionCallbacks.new(queue)
    repository = Subscriptions::Repository.new(callbacks)
    repository.add(Subscriptions::Subscription.new(id: "SUB-7", plan: "basic", seats: 3))
    repository.add(Subscriptions::Subscription.new(id: "SUB-8", plan: "team", seats: 8))
    service = Subscriptions::Services::UpdateSubscription.new(repository)
    mailer = Subscriptions::Mailers::SubscriptionMailer.new
    [repository, service, queue, mailer]
  end

  def test_combined_update_publishes_once_with_the_final_snapshot
    repository, service, queue, mailer = build_system
    changed = service.call(id: "SUB-7", plan: "enterprise", seats: 12)

    assert_equal ["enterprise", 12, 2], [changed.plan, changed.seats, changed.version]
    assert_equal 1, queue.jobs.length, "one committed customer update should enqueue once"
    assert_equal 1, queue.drain(mailer)
    assert_equal(
      [{
        to: "customer-SUB-7@example.test",
        subject: "Subscription updated",
        subscription_id: "SUB-7",
        plan: "enterprise",
        seats: 12,
        version: 2
      }],
      mailer.deliveries
    )
    assert_equal 2, repository.fetch("SUB-7").version
  end

  def test_direct_transaction_registration_keeps_the_latest_callback_per_key
    repository, = build_system
    events = []
    repository.transaction do |transaction|
      transaction.after_commit("customer:SUB-7") { events << "intermediate" }
      transaction.after_commit("audit:SUB-7") { events << "audit" }
      transaction.after_commit("customer:SUB-7") { events << "final" }
    end

    assert_equal ["final", "audit"], events
  end

  def test_distinct_subscriptions_in_one_transaction_each_publish
    repository, service, queue, mailer = build_system
    repository.transaction do |transaction|
      service.apply(transaction, id: "SUB-7", plan: "team", seats: 5)
      service.apply(transaction, id: "SUB-8", plan: "enterprise", seats: 20)
    end

    assert_equal 2, queue.jobs.length
    assert_equal ["SUB-7", "SUB-8"], queue.jobs.map(&:subscription_id)
    queue.drain(mailer)
    assert_equal [5, 20], mailer.deliveries.map { |delivery| delivery[:seats] }
    assert_equal [2, 2], mailer.deliveries.map { |delivery| delivery[:version] }
  end

  def test_noop_and_single_field_updates_keep_existing_behavior
    _repository, service, queue, mailer = build_system
    unchanged = service.call(id: "SUB-7", plan: "basic", seats: 3)
    assert_equal 0, unchanged.version
    assert_empty queue.jobs

    service.call(id: "SUB-7", plan: "team")
    assert_equal 1, queue.jobs.length
    queue.drain(mailer)
    assert_equal({ plan: "team", seats: 3, version: 1 }, mailer.deliveries[0].slice(:plan, :seats, :version))
  end

  def test_rollback_restores_the_record_and_publishes_nothing
    repository, service, queue, mailer = build_system
    error = assert_raises(ArgumentError) do
      service.call(id: "SUB-7", plan: "enterprise", seats: 0)
    end
    assert_equal "seats must be a positive integer", error.message

    restored = repository.fetch("SUB-7")
    assert_equal ["basic", 3, 0], [restored.plan, restored.seats, restored.version]
    assert_empty queue.jobs
    assert_equal 0, queue.drain(mailer)
    assert_empty mailer.deliveries
  end

  def test_separate_transactions_are_not_globally_deduplicated
    _repository, service, queue, mailer = build_system
    service.call(id: "SUB-7", plan: "team")
    service.call(id: "SUB-7", seats: 9)

    assert_equal 2, queue.jobs.length
    queue.drain(mailer)
    assert_equal 2, mailer.deliveries.length
    assert_equal(
      [["team", 3, 1], ["team", 9, 2]],
      mailer.deliveries.map { |delivery| delivery.values_at(:plan, :seats, :version) }
    )
  end

  def test_job_queue_does_not_hide_real_distinct_jobs
    _repository, _service, queue, mailer = build_system
    queue.enqueue(Subscriptions::Jobs::SubscriptionUpdatedJob.new(id: "SUB-7", plan: "team", seats: 4, version: 1))
    queue.enqueue(Subscriptions::Jobs::SubscriptionUpdatedJob.new(id: "SUB-7", plan: "team", seats: 5, version: 2))
    assert_equal 2, queue.drain(mailer)
    assert_equal [4, 5], mailer.deliveries.map { |delivery| delivery[:seats] }
  end
end
