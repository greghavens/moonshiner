module FleetDesk
  module Routes
    # Vehicle roster endpoints.
    module Vehicles
      def self.register(router, vehicles)
        router.post "/vehicles" do |req|
          vehicle = vehicles.add_vehicle(
            unit: req.params[:unit],
            desc: req.params[:desc],
            mileage: req.params[:mileage]
          )
          Response.created(vehicle)
        end

        router.get "/vehicles/:unit" do |req|
          Response.ok(vehicles.fetch(req.params[:unit]))
        end

        router.post "/vehicles/:unit/mileage" do |req|
          vehicle = vehicles.log_mileage(req.params[:unit], req.params[:reading])
          Response.ok(vehicle)
        end
      end
    end
  end
end
