module Subscriptions
  # Owns one repository transaction and the work that becomes visible only
  # after that transaction commits.
  class UnitOfWork
    def initialize(repository)
      @repository = repository
      @after_commit = []
    end

    def after_commit(key, &block)
      raise ArgumentError, "callback key must not be blank" if key.to_s.empty?
      raise ArgumentError, "callback block required" unless block

      @after_commit << [key, block]
    end

    def run
      snapshot = @repository.snapshot
      begin
        result = yield self
      rescue StandardError
        @repository.restore(snapshot)
        @after_commit.clear
        raise
      end

      callbacks = @after_commit
      @after_commit = []
      callbacks.each { |_key, callback| callback.call }
      result
    end
  end
end
