module Subscriptions
  module Mailers
    class SubscriptionMailer
      attr_reader :deliveries

      def initialize
        @deliveries = []
      end

      def deliver_update(subscription_id:, plan:, seats:, version:)
        @deliveries << {
          to: "customer-#{subscription_id}@example.test",
          subject: "Subscription updated",
          subscription_id: subscription_id,
          plan: plan,
          seats: seats,
          version: version
        }
      end
    end
  end
end
