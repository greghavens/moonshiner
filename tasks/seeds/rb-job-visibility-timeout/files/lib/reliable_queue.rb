# frozen_string_literal: true

module ReliableQueue
  class LostLease < StandardError; end

  # A clock that tests and callers can advance without sleeping.
  class ManualClock
    attr_reader :now

    def initialize(now = 0)
      @now = now
    end

    def advance(seconds)
      raise ArgumentError, "seconds must be non-negative" if seconds.negative?

      @now += seconds
    end
  end

  Lease = Struct.new(
    :job_id,
    :payload,
    :worker_id,
    :token,
    :attempt,
    :expires_at,
    keyword_init: true
  )

  Job = Struct.new(
    :id,
    :payload,
    :state,
    :attempts,
    :worker_id,
    :lease_token,
    :lease_expires_at,
    :completed_worker_id,
    :completed_token,
    :result,
    keyword_init: true
  )

  class MemoryQueue
    def initialize(clock:)
      @clock = clock
      @jobs = {}
      @next_id = 0
    end

    def enqueue(payload)
      @next_id += 1
      job = Job.new(
        id: @next_id,
        payload: payload,
        state: :ready,
        attempts: 0
      )
      @jobs[job.id] = job
      job.id
    end

    def reserve(worker_id:, visibility_timeout:)
      validate_timeout!(visibility_timeout)
      job = @jobs.values.find { |candidate| reservable?(candidate) }
      return nil unless job

      job.attempts += 1
      job.state = :running
      job.worker_id = worker_id
      job.lease_token = "#{job.id}:#{job.attempts}"
      job.lease_expires_at = @clock.now + visibility_timeout

      Lease.new(
        job_id: job.id,
        payload: job.payload,
        worker_id: worker_id,
        token: job.lease_token,
        attempt: job.attempts,
        expires_at: job.lease_expires_at
      )
    end

    def heartbeat(lease, visibility_timeout:)
      validate_timeout!(visibility_timeout)
      job = @jobs.fetch(lease.job_id)
      return false unless active_lease?(job, lease)

      job.lease_expires_at = @clock.now + visibility_timeout
      true
    end

    def complete(lease, result)
      job = @jobs.fetch(lease.job_id)

      if job.state == :completed &&
         job.completed_worker_id == lease.worker_id &&
         job.completed_token == lease.token
        return job.result
      end

      unless active_lease?(job, lease)
        raise LostLease, "job #{lease.job_id} is no longer owned by #{lease.worker_id}"
      end

      job.state = :completed
      job.completed_worker_id = lease.worker_id
      job.completed_token = lease.token
      job.result = result
      job.worker_id = nil
      job.lease_token = nil
      job.lease_expires_at = nil
      result
    end

    def snapshot(job_id)
      job = @jobs.fetch(job_id)
      {
        state: job.state,
        attempts: job.attempts,
        worker_id: job.worker_id,
        lease_expires_at: job.lease_expires_at,
        result: job.result
      }
    end

    private

    def validate_timeout!(visibility_timeout)
      return if visibility_timeout.positive?

      raise ArgumentError, "visibility_timeout must be positive"
    end

    def reservable?(job)
      job.state == :ready ||
        (job.state == :running && job.lease_expires_at <= @clock.now)
    end

    def active_lease?(job, lease)
      job.state == :running &&
        job.worker_id == lease.worker_id &&
        job.lease_token == lease.token &&
        job.lease_expires_at > @clock.now
    end
  end

  class Worker
    def initialize(queue:, id:, visibility_timeout:, handler:)
      @queue = queue
      @id = id
      @visibility_timeout = visibility_timeout
      @handler = handler
    end

    def run_once
      lease = @queue.reserve(
        worker_id: @id,
        visibility_timeout: @visibility_timeout
      )
      return false unless lease

      heartbeat = -> { nil }
      result = @handler.call(lease.payload, heartbeat)
      @queue.complete(lease, result)
      true
    end
  end
end
