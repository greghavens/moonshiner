module FleetDesk
  module Services
    # Shop-wide dashboard and per-vehicle history rollups.
    #
    # The shop dashboard is the hottest read in the app (the wall display
    # polls it), so it is memoized; write paths call #invalidate.
    class ReportService
      def initialize(orders, vehicles)
        @orders = orders
        @vehicles = vehicles
        @shop = nil
      end

      def shop_dashboard
        @shop ||= compute_shop
      end

      def invalidate
        @shop = nil
      end

      def unit_report(unit)
        vehicle = @vehicles.find(unit) or raise NotFound, "no vehicle with unit #{unit}"
        history = @orders.for_unit(unit)
        {
          unit: vehicle[:unit],
          desc: vehicle[:desc],
          mileage: vehicle[:mileage],
          orders: history.map { |o| { id: o[:id], task: o[:task], status: o[:status] } },
          labor_minutes: history.sum { |o| o[:labor_minutes] },
          parts_cents: history.sum { |o| o[:parts_cents] }
        }
      end

      private

      def compute_shop
        open = @orders.all.select { |order| order[:status] == "open" }
        {
          open_orders: open.length,
          labor_minutes: open.sum { |o| o[:labor_minutes] },
          parts_cents: open.sum { |o| o[:parts_cents] },
          units: open.map { |o| o[:unit] }.uniq.sort
        }
      end
    end
  end
end
