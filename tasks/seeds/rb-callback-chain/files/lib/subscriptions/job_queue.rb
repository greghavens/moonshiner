module Subscriptions
  class JobQueue
    attr_reader :jobs

    def initialize
      @jobs = []
    end

    def enqueue(job)
      @jobs << job
    end

    def drain(mailer)
      performed = 0
      until @jobs.empty?
        @jobs.shift.perform(mailer)
        performed += 1
      end
      performed
    end
  end
end
