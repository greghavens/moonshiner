module Subscriptions
  module Jobs
    class SubscriptionUpdatedJob
      attr_reader :subscription_id, :plan, :seats, :version

      def initialize(id:, plan:, seats:, version:)
        @subscription_id = id
        @plan = plan
        @seats = seats
        @version = version
      end

      def perform(mailer)
        mailer.deliver_update(
          subscription_id: @subscription_id,
          plan: @plan,
          seats: @seats,
          version: @version
        )
      end
    end
  end
end
