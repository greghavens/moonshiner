module FleetDesk
  module Services
    # Work orders: opening jobs against a vehicle, booking labor and parts,
    # closing out, and the mechanics' board of open jobs.
    class WorkOrderService
      def initialize(orders, vehicles, reports)
        @orders = orders
        @vehicles = vehicles
        @reports = reports
      end

      def open_order(unit:, task:)
        raise NotFound, "no vehicle with unit #{unit}" unless @vehicles.exists?(unit)
        raise ArgumentError, "task must not be blank" if task.nil? || task.strip.empty?

        order = @orders.insert(
          unit: unit,
          task: task,
          status: "open",
          labor_minutes: 0,
          parts_cents: 0,
          labor_log: [],
          parts: []
        )
        @reports.invalidate
        order
      end

      def log_labor(id, minutes:, note: nil)
        order = require_open(id)
        unless minutes.is_a?(Integer) && minutes > 0
          raise ArgumentError, "minutes must be a positive integer"
        end

        order[:labor_minutes] += minutes
        order[:labor_log] << { minutes: minutes, note: note }
        @orders.update(order)
        order
      end

      def add_part(id, item:, cents:)
        order = require_open(id)
        raise ArgumentError, "item must not be blank" if item.nil? || item.strip.empty?
        unless cents.is_a?(Integer) && cents > 0
          raise ArgumentError, "cents must be a positive integer"
        end

        order[:parts_cents] += cents
        order[:parts] << { item: item, cents: cents }
        @orders.update(order)
        @reports.invalidate
        order
      end

      def close_order(id)
        order = require_open(id)
        order[:status] = "closed"
        @orders.update(order)
        @reports.invalidate
        order
      end

      def fetch(id)
        @orders.find(id) or raise NotFound, "no such work order: #{id}"
      end

      # Open jobs for the mechanics' board, oldest first.
      def board
        @orders.all.select { |order| order[:status] == "open" }.map do |order|
          {
            id: order[:id],
            unit: order[:unit],
            task: order[:task],
            labor_minutes: order[:labor_minutes]
          }
        end
      end

      private

      def require_open(id)
        order = fetch(id)
        raise ArgumentError, "work order #{id} is closed" unless order[:status] == "open"

        order
      end
    end
  end
end
