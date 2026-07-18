module Subscriptions
  module Services
    class UpdateSubscription
      def initialize(repository)
        @repository = repository
      end

      def call(id:, plan: nil, seats: nil)
        @repository.transaction do |transaction|
          apply(transaction, id: id, plan: plan, seats: seats)
        end
      end

      # Used by the batch updater to group several subscriptions in one unit
      # of work without bypassing the same save/callback chain.
      def apply(transaction, id:, plan: nil, seats: nil)
        subscription = @repository.fetch(id)
        if !plan.nil? && subscription.change_plan(plan)
          @repository.save(subscription, [:plan], transaction)
        end
        if !seats.nil? && subscription.change_seats(seats)
          @repository.save(subscription, [:seats], transaction)
        end
        subscription
      end
    end
  end
end
