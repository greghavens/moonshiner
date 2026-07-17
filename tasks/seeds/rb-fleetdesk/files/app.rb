require_relative "request"
require_relative "response"
require_relative "router"
require_relative "store/memory_store"
require_relative "store/repos"
require_relative "services/vehicle_service"
require_relative "services/work_order_service"
require_relative "services/report_service"
require_relative "routes/vehicles"
require_relative "routes/work_orders"
require_relative "routes/reports"

module FleetDesk
  # Wires the depot desk together and dispatches requests. The kiosk front
  # end calls App#call with (verb, path, params) triples.
  class App
    def initialize
      store = Store::MemoryStore.new
      vehicles = Store::VehicleRepo.new(store)
      orders = Store::OrderRepo.new(store)

      reports = Services::ReportService.new(orders, vehicles)
      vehicle_service = Services::VehicleService.new(vehicles, orders)
      work_order_service = Services::WorkOrderService.new(orders, vehicles, reports)

      @router = Router.new
      Routes::Vehicles.register(@router, vehicle_service)
      Routes::WorkOrders.register(@router, work_order_service)
      Routes::Reports.register(@router, reports)
    end

    def call(verb, path, params = {})
      verb = verb.to_s.upcase
      hit = @router.match(verb, path)
      return Response.not_found("no route: #{verb} #{path}") unless hit

      handler, path_params = hit
      request = Request.new(verb, path, params.merge(path_params))
      handler.call(request)
    rescue Services::NotFound => e
      Response.not_found(e.message)
    rescue ArgumentError => e
      Response.unprocessable(e.message)
    end
  end
end
