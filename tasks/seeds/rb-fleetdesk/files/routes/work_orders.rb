module FleetDesk
  module Routes
    # Work order endpoints: open, inspect, book labor/parts, close, and the
    # mechanics' board of open jobs.
    module WorkOrders
      def self.register(router, work_orders)
        router.post "/work_orders" do |req|
          order = work_orders.open_order(
            unit: req.params[:unit],
            task: req.params[:task]
          )
          Response.created(id: order[:id])
        end

        router.get "/work_orders/queue" do |_req|
          Response.ok(orders: work_orders.board)
        end

        router.get "/work_orders/:id" do |req|
          Response.ok(work_orders.fetch(req.params[:id]))
        end

        router.post "/work_orders/:id/labor" do |req|
          order = work_orders.log_labor(
            req.params[:id],
            minutes: req.params[:minutes],
            note: req.params[:note]
          )
          Response.ok(id: order[:id], labor_minutes: order[:labor_minutes])
        end

        router.post "/work_orders/:id/parts" do |req|
          order = work_orders.add_part(
            req.params[:id],
            item: req.params[:item],
            cents: req.params[:cents]
          )
          Response.ok(id: order[:id], parts_cents: order[:parts_cents])
        end

        router.post "/work_orders/:id/close" do |req|
          order = work_orders.close_order(req.params[:id])
          Response.ok(id: order[:id], status: order[:status])
        end
      end
    end
  end
end
