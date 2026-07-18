module Subscriptions
  NotFound = Class.new(StandardError)

  class Repository
    def initialize(callbacks)
      @callbacks = callbacks
      @records = {}
    end

    def add(subscription)
      @records[subscription.id] = subscription
      subscription
    end

    def fetch(id)
      @records[id] or raise NotFound, "no subscription #{id}"
    end

    def save(subscription, changed_fields, transaction)
      subscription.record_save!
      @records[subscription.id] = subscription
      @callbacks.after_update(subscription, changed_fields, transaction)
      subscription
    end

    def transaction(&block)
      UnitOfWork.new(self).run(&block)
    end

    def snapshot
      Marshal.load(Marshal.dump(@records))
    end

    def restore(snapshot)
      @records = snapshot
    end
  end
end
