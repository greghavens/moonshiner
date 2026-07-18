module Subscriptions
  # Model callback object installed by the repository. Notification jobs are
  # deferred so rollbacks never leak customer-visible work.
  class SubscriptionCallbacks
    CUSTOMER_FIELDS = %i[plan seats].freeze

    def initialize(queue)
      @queue = queue
    end

    def after_update(subscription, changed_fields, transaction)
      return if (changed_fields & CUSTOMER_FIELDS).empty?

      snapshot = {
        id: subscription.id,
        plan: subscription.plan,
        seats: subscription.seats,
        version: subscription.version
      }
      transaction.after_commit("subscription-update:#{subscription.id}") do
        @queue.enqueue(Jobs::SubscriptionUpdatedJob.new(**snapshot))
      end
    end
  end
end
