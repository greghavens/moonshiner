# frozen_string_literal: true

require "reliable_queue"

class TestFailure < StandardError; end

module Assertions
  def assert(value, message = nil)
    raise TestFailure, (message || "expected #{value.inspect} to be truthy") unless value

    value
  end

  def refute(value, message = nil)
    raise TestFailure, (message || "expected #{value.inspect} to be falsey") if value

    true
  end

  def assert_equal(expected, actual, message = nil)
    return actual if expected == actual

    raise TestFailure,
          (message || "expected #{expected.inspect}, got #{actual.inspect}")
  end

  def refute_equal(unexpected, actual, message = nil)
    return actual unless unexpected == actual

    raise TestFailure,
          (message || "expected a value other than #{unexpected.inspect}")
  end

  def assert_nil(actual, message = nil)
    assert_equal(nil, actual, message)
  end

  def assert_raises(error_class)
    yield
  rescue error_class => error
    error
  rescue StandardError => error
    raise TestFailure,
          "expected #{error_class}, but #{error.class} was raised: #{error.message}"
  else
    raise TestFailure, "expected #{error_class} to be raised"
  end
end

class VisibilityTimeoutTest
  include Assertions

  def setup
    @clock = ReliableQueue::ManualClock.new
    @queue = ReliableQueue::MemoryQueue.new(clock: @clock)
  end

  def test_long_job_heartbeat_prevents_a_second_execution
    job_id = @queue.enqueue("report")
    executions = []

    second_worker = ReliableQueue::Worker.new(
      queue: @queue,
      id: "worker-b",
      visibility_timeout: 7,
      handler: lambda do |payload, _heartbeat|
        executions << ["worker-b", payload]
        :duplicate
      end
    )

    first_worker = ReliableQueue::Worker.new(
      queue: @queue,
      id: "worker-a",
      visibility_timeout: 7,
      handler: lambda do |payload, heartbeat|
        executions << ["worker-a", payload]
        @clock.advance(4)
        assert heartbeat.call
        @clock.advance(4)
        assert heartbeat.call
        assert_equal 15, @queue.snapshot(job_id)[:lease_expires_at]
        @clock.advance(4)
        refute second_worker.run_once
        :finished
      end
    )

    assert first_worker.run_once
    assert_equal [["worker-a", "report"]], executions
    assert_equal(
      { state: :completed, attempts: 1, worker_id: nil,
        lease_expires_at: nil, result: :finished },
      @queue.snapshot(job_id)
    )
  end

  def test_only_the_current_owner_and_token_can_heartbeat
    job_id = @queue.enqueue("owned")
    lease = @queue.reserve(worker_id: "worker-a", visibility_timeout: 10)
    impostor = ReliableQueue::Lease.new(
      job_id: lease.job_id,
      payload: lease.payload,
      worker_id: "worker-b",
      token: lease.token,
      attempt: lease.attempt,
      expires_at: lease.expires_at
    )
    wrong_token = ReliableQueue::Lease.new(
      job_id: lease.job_id,
      payload: lease.payload,
      worker_id: lease.worker_id,
      token: "forged-token",
      attempt: lease.attempt,
      expires_at: lease.expires_at
    )

    @clock.advance(4)
    refute @queue.heartbeat(impostor, visibility_timeout: 10)
    refute @queue.heartbeat(wrong_token, visibility_timeout: 10)
    assert_raises(ReliableQueue::LostLease) do
      @queue.complete(impostor, :forged_result)
    end
    assert_raises(ReliableQueue::LostLease) do
      @queue.complete(wrong_token, :forged_result)
    end
    assert_equal 10, @queue.snapshot(job_id)[:lease_expires_at]
    assert @queue.heartbeat(lease, visibility_timeout: 10)
    assert_equal 14, @queue.snapshot(job_id)[:lease_expires_at]
  end

  def test_worker_callback_raises_immediately_after_lease_loss
    job_id = @queue.enqueue("lost")
    callback_rejected = false

    replacement = ReliableQueue::Worker.new(
      queue: @queue,
      id: "replacement",
      visibility_timeout: 10,
      handler: ->(_payload, _heartbeat) { :replacement_result }
    )
    original = ReliableQueue::Worker.new(
      queue: @queue,
      id: "original",
      visibility_timeout: 10,
      handler: lambda do |_payload, heartbeat|
        @clock.advance(10)
        assert replacement.run_once
        begin
          heartbeat.call
        rescue ReliableQueue::LostLease
          callback_rejected = true
        end
        :stale_result
      end
    )

    assert_raises(ReliableQueue::LostLease) { original.run_once }
    assert callback_rejected
    assert_equal(
      { state: :completed, attempts: 2, worker_id: nil,
        lease_expires_at: nil, result: :replacement_result },
      @queue.snapshot(job_id)
    )
  end

  def test_job_is_retried_after_its_worker_really_dies
    job_id = @queue.enqueue("retry-me")
    dead_lease = @queue.reserve(worker_id: "dead-worker", visibility_timeout: 10)

    @clock.advance(10)
    retry_lease = @queue.reserve(worker_id: "replacement", visibility_timeout: 10)

    assert_equal job_id, retry_lease.job_id
    assert_equal 2, retry_lease.attempt
    refute_equal dead_lease.token, retry_lease.token
    assert_equal "replacement", @queue.snapshot(job_id)[:worker_id]
    refute @queue.heartbeat(dead_lease, visibility_timeout: 10)
    assert_raises(ReliableQueue::LostLease) do
      @queue.complete(dead_lease, :stale_result)
    end

    assert_equal :replacement_result, @queue.complete(retry_lease, :replacement_result)
    assert_equal :replacement_result, @queue.snapshot(job_id)[:result]
  end

  def test_completing_the_same_lease_is_idempotent
    job_id = @queue.enqueue("once")
    lease = @queue.reserve(worker_id: "worker-a", visibility_timeout: 10)
    impostor = ReliableQueue::Lease.new(
      job_id: lease.job_id,
      payload: lease.payload,
      worker_id: "worker-b",
      token: lease.token,
      attempt: lease.attempt,
      expires_at: lease.expires_at
    )

    assert_equal :first_result, @queue.complete(lease, :first_result)
    assert_equal :first_result, @queue.complete(lease, :different_result)
    assert_raises(ReliableQueue::LostLease) do
      @queue.complete(impostor, :forged_result)
    end
    assert_equal 1, @queue.snapshot(job_id)[:attempts]
    assert_equal :first_result, @queue.snapshot(job_id)[:result]
    assert_nil @queue.reserve(worker_id: "worker-b", visibility_timeout: 10)
  end
end

failures = []
tests = VisibilityTimeoutTest.instance_methods(false).grep(/^test_/).sort
tests.each do |method_name|
  test = VisibilityTimeoutTest.new
  begin
    test.setup
    test.public_send(method_name)
    print "."
  rescue StandardError => error
    print "F"
    failures << [method_name, error]
  end
end
puts

failures.each do |method_name, error|
  warn "#{method_name}: #{error.class}: #{error.message}"
end
puts "#{tests.length} tests, #{failures.length} failures"
exit(failures.empty? ? 0 : 1)
