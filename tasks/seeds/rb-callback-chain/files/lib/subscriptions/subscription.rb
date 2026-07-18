module Subscriptions
  class Subscription
    attr_reader :id, :plan, :seats, :version

    def initialize(id:, plan:, seats:, version: 0)
      @id = id
      @plan = plan
      @seats = seats
      @version = version
    end

    def change_plan(plan)
      raise ArgumentError, "plan must not be blank" if plan.nil? || plan.strip.empty?
      return false if plan == @plan

      @plan = plan
      true
    end

    def change_seats(seats)
      unless seats.is_a?(Integer) && seats.positive?
        raise ArgumentError, "seats must be a positive integer"
      end
      return false if seats == @seats

      @seats = seats
      true
    end

    def record_save!
      @version += 1
    end
  end
end
