module FleetDesk
  module Services
    NotFound = Class.new(StandardError)

    # Fleet roster: registering vehicles, mileage log, per-vehicle lookups.
    class VehicleService
      def initialize(vehicles, orders)
        @vehicles = vehicles
        @orders = orders
      end

      def add_vehicle(unit:, desc:, mileage:)
        raise ArgumentError, "unit must not be blank" if unit.nil? || unit.strip.empty?
        raise ArgumentError, "duplicate unit #{unit}" if @vehicles.exists?(unit)
        unless mileage.is_a?(Integer) && mileage >= 0
          raise ArgumentError, "mileage must be a non-negative integer"
        end

        @vehicles.insert(unit: unit, desc: desc, mileage: mileage)
      end

      def fetch(unit)
        vehicle = require_vehicle(unit)
        open = @orders.for_unit(unit).count { |order| order[:status] == "open" }
        vehicle.merge(open_orders: open)
      end

      def log_mileage(unit, reading)
        vehicle = require_vehicle(unit)
        unless reading.is_a?(Integer) && reading >= 0
          raise ArgumentError, "mileage must be a non-negative integer"
        end
        if reading < vehicle[:mileage]
          raise ArgumentError, "odometer cannot roll back (#{vehicle[:mileage]} -> #{reading})"
        end

        vehicle[:mileage] = reading
        @vehicles.update(vehicle)
        vehicle
      end

      private

      def require_vehicle(unit)
        @vehicles.find(unit) or raise NotFound, "no vehicle with unit #{unit}"
      end
    end
  end
end
