module FleetDesk
  module Routes
    # Read-only reporting endpoints for the wall display and unit history.
    module Reports
      def self.register(router, reports)
        router.get "/reports/shop" do |_req|
          Response.ok(reports.shop_dashboard)
        end

        router.get "/reports/unit/:unit" do |req|
          Response.ok(reports.unit_report(req.params[:unit]))
        end
      end
    end
  end
end
