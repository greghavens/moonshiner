require "minitest/autorun"
require_relative "app"

# Acceptance suite for the Calder Street depot desk. Drives the app only
# through App#call (verb, path, params) exactly like the kiosk front end.
module DeskHarness
  def desk
    app = FleetDesk::App.new
    res = app.call("POST", "/vehicles", unit: "TRK-14", desc: "box truck 26ft", mileage: 82_450)
    raise "harness: seeding TRK-14 failed (#{res.status})" unless res.status == 201

    res = app.call("POST", "/vehicles", unit: "VAN-3", desc: "sprinter van", mileage: 40_112)
    raise "harness: seeding VAN-3 failed (#{res.status})" unless res.status == 201

    app
  end
end

class VehicleRoutesTest < Minitest::Test
  include DeskHarness

  def self.run_order
    :alpha
  end

  def test_register_and_fetch_vehicle
    app = desk
    res = app.call("GET", "/vehicles/TRK-14")
    assert_equal 200, res.status
    assert_equal(
      { unit: "TRK-14", desc: "box truck 26ft", mileage: 82_450, open_orders: 0 },
      res.body
    )
  end

  def test_duplicate_unit_is_rejected
    app = desk
    res = app.call("POST", "/vehicles", unit: "TRK-14", desc: "again", mileage: 5)
    assert_equal 422, res.status
    assert_equal({ error: "duplicate unit TRK-14" }, res.body)
  end

  def test_unknown_unit_404s
    app = desk
    res = app.call("GET", "/vehicles/TRK-99")
    assert_equal 404, res.status
    assert_equal({ error: "no vehicle with unit TRK-99" }, res.body)
  end

  def test_mileage_updates_and_guards_rollback
    app = desk
    res = app.call("POST", "/vehicles/TRK-14/mileage", reading: 82_610)
    assert_equal 200, res.status
    assert_equal 82_610, res.body[:mileage]

    res = app.call("POST", "/vehicles/TRK-14/mileage", reading: 82_500)
    assert_equal 422, res.status
    assert_equal({ error: "odometer cannot roll back (82610 -> 82500)" }, res.body)

    res = app.call("GET", "/vehicles/TRK-14")
    assert_equal 82_610, res.body[:mileage]
  end

  def test_vehicle_validation_messages
    app = desk
    res = app.call("POST", "/vehicles", unit: "  ", desc: "ghost", mileage: 1)
    assert_equal 422, res.status
    assert_equal({ error: "unit must not be blank" }, res.body)

    res = app.call("POST", "/vehicles", unit: "BUS-9", desc: "shuttle", mileage: "many")
    assert_equal 422, res.status
    assert_equal({ error: "mileage must be a non-negative integer" }, res.body)
  end

  def test_unknown_route_404s
    app = desk
    res = app.call("GET", "/nope")
    assert_equal 404, res.status
    assert_equal({ error: "no route: GET /nope" }, res.body)
  end
end

class WorkOrderRoutesTest < Minitest::Test
  include DeskHarness

  def self.run_order
    :alpha
  end

  def test_open_order_returns_desk_id
    app = desk
    res = app.call("POST", "/work_orders", unit: "TRK-14", task: "brake inspection")
    assert_equal 201, res.status
    assert_equal({ id: "WO-1" }, res.body)

    res = app.call("POST", "/work_orders", unit: "VAN-3", task: "door latch")
    assert_equal 201, res.status
    assert_equal({ id: "WO-2" }, res.body)
  end

  def test_open_for_unknown_unit_404s
    app = desk
    res = app.call("POST", "/work_orders", unit: "TRK-99", task: "brake inspection")
    assert_equal 404, res.status
    assert_equal({ error: "no vehicle with unit TRK-99" }, res.body)
  end

  def test_blank_task_rejected
    app = desk
    res = app.call("POST", "/work_orders", unit: "TRK-14", task: " ")
    assert_equal 422, res.status
    assert_equal({ error: "task must not be blank" }, res.body)
  end

  def test_labor_accumulates
    app = desk
    app.call("POST", "/work_orders", unit: "TRK-14", task: "brake inspection")

    res = app.call("POST", "/work_orders/WO-1/labor", minutes: 45, note: "front pads")
    assert_equal 200, res.status
    assert_equal({ id: "WO-1", labor_minutes: 45 }, res.body)

    res = app.call("POST", "/work_orders/WO-1/labor", minutes: 30)
    assert_equal 200, res.status
    assert_equal({ id: "WO-1", labor_minutes: 75 }, res.body)

    res = app.call("GET", "/work_orders/WO-1")
    assert_equal 200, res.status
    assert_equal 75, res.body[:labor_minutes]
    log = res.body[:labor_log]
    assert_equal 2, log.length
    assert_equal({ minutes: 45, note: "front pads" }, log[0])
    assert_equal 30, log[1][:minutes]
    assert_nil log[1][:note]
  end

  def test_labor_validation
    app = desk
    app.call("POST", "/work_orders", unit: "TRK-14", task: "brake inspection")

    res = app.call("POST", "/work_orders/WO-1/labor", minutes: 0)
    assert_equal 422, res.status
    assert_equal({ error: "minutes must be a positive integer" }, res.body)

    res = app.call("POST", "/work_orders/WO-1/labor", minutes: "45")
    assert_equal 422, res.status
    assert_equal({ error: "minutes must be a positive integer" }, res.body)

    res = app.call("POST", "/work_orders/WO-9/labor", minutes: 15)
    assert_equal 404, res.status
    assert_equal({ error: "no such work order: WO-9" }, res.body)

    app.call("POST", "/work_orders/WO-1/close")
    res = app.call("POST", "/work_orders/WO-1/labor", minutes: 15)
    assert_equal 422, res.status
    assert_equal({ error: "work order WO-1 is closed" }, res.body)
  end

  def test_parts_accumulate
    app = desk
    app.call("POST", "/work_orders", unit: "TRK-14", task: "brake inspection")

    res = app.call("POST", "/work_orders/WO-1/parts", item: "brake pads set", cents: 12_500)
    assert_equal 200, res.status
    assert_equal({ id: "WO-1", parts_cents: 12_500 }, res.body)

    res = app.call("POST", "/work_orders/WO-1/parts", item: "rotor pair", cents: 8_300)
    assert_equal 200, res.status
    assert_equal({ id: "WO-1", parts_cents: 20_800 }, res.body)

    res = app.call("GET", "/work_orders/WO-1")
    assert_equal(
      [{ item: "brake pads set", cents: 12_500 }, { item: "rotor pair", cents: 8_300 }],
      res.body[:parts]
    )
  end

  def test_close_and_double_close
    app = desk
    app.call("POST", "/work_orders", unit: "TRK-14", task: "brake inspection")

    res = app.call("POST", "/work_orders/WO-1/close")
    assert_equal 200, res.status
    assert_equal({ id: "WO-1", status: "closed" }, res.body)

    res = app.call("POST", "/work_orders/WO-1/close")
    assert_equal 422, res.status
    assert_equal({ error: "work order WO-1 is closed" }, res.body)
  end

  def test_fetch_unknown_order_404s
    app = desk
    res = app.call("GET", "/work_orders/WO-77")
    assert_equal 404, res.status
    assert_equal({ error: "no such work order: WO-77" }, res.body)
  end
end

class BoardRouteTest < Minitest::Test
  include DeskHarness

  def self.run_order
    :alpha
  end

  def test_board_lists_open_orders
    app = desk
    app.call("POST", "/work_orders", unit: "TRK-14", task: "brake inspection")
    app.call("POST", "/work_orders", unit: "VAN-3", task: "door latch")
    app.call("POST", "/work_orders/WO-1/labor", minutes: 25, note: "pads off")
    app.call("POST", "/work_orders/WO-2/close")

    res = app.call("GET", "/work_orders/board")
    assert_equal 200, res.status
    assert_equal(
      { orders: [{ id: "WO-1", unit: "TRK-14", task: "brake inspection", labor_minutes: 25 }] },
      res.body
    )
  end

  def test_retired_queue_path_is_gone
    app = desk
    res = app.call("GET", "/work_orders/queue")
    assert_equal 404, res.status
  end
end

class ShopDashboardTest < Minitest::Test
  include DeskHarness

  def self.run_order
    :alpha
  end

  def test_dashboard_counts_open_orders
    app = desk
    app.call("POST", "/work_orders", unit: "TRK-14", task: "brake inspection")
    app.call("POST", "/work_orders", unit: "VAN-3", task: "door latch")

    res = app.call("GET", "/reports/shop")
    assert_equal 200, res.status
    assert_equal(
      { open_orders: 2, labor_minutes: 0, parts_cents: 0, units: ["TRK-14", "VAN-3"] },
      res.body
    )
  end

  def test_wrench_time_shows_immediately
    app = desk
    app.call("POST", "/work_orders", unit: "TRK-14", task: "brake inspection")

    res = app.call("GET", "/reports/shop")
    assert_equal 0, res.body[:labor_minutes]

    app.call("POST", "/work_orders/WO-1/labor", minutes: 45, note: "front pads")
    app.call("POST", "/work_orders/WO-1/labor", minutes: 30)

    res = app.call("GET", "/reports/shop")
    assert_equal 200, res.status
    assert_equal 75, res.body[:labor_minutes]
  end

  def test_parts_show_immediately
    app = desk
    app.call("POST", "/work_orders", unit: "TRK-14", task: "brake inspection")

    res = app.call("GET", "/reports/shop")
    assert_equal 0, res.body[:parts_cents]

    app.call("POST", "/work_orders/WO-1/parts", item: "brake pads set", cents: 12_500)

    res = app.call("GET", "/reports/shop")
    assert_equal 12_500, res.body[:parts_cents]
  end

  def test_close_updates_dashboard
    app = desk
    app.call("POST", "/work_orders", unit: "TRK-14", task: "brake inspection")
    app.call("POST", "/work_orders", unit: "VAN-3", task: "door latch")

    res = app.call("GET", "/reports/shop")
    assert_equal 2, res.body[:open_orders]

    app.call("POST", "/work_orders/WO-2/close")

    res = app.call("GET", "/reports/shop")
    assert_equal 1, res.body[:open_orders]
    assert_equal ["TRK-14"], res.body[:units]
  end

  def test_closed_orders_leave_the_dashboard
    app = desk
    app.call("POST", "/work_orders", unit: "TRK-14", task: "brake inspection")
    app.call("POST", "/work_orders/WO-1/labor", minutes: 40, note: "teardown")
    app.call("POST", "/work_orders/WO-1/parts", item: "gasket kit", cents: 6_000)
    app.call("POST", "/work_orders", unit: "VAN-3", task: "door latch")
    app.call("POST", "/work_orders/WO-1/close")

    res = app.call("GET", "/reports/shop")
    assert_equal 200, res.status
    assert_equal(
      { open_orders: 1, labor_minutes: 0, parts_cents: 0, units: ["VAN-3"] },
      res.body
    )
  end
end

class UnitReportTest < Minitest::Test
  include DeskHarness

  def self.run_order
    :alpha
  end

  def test_unit_report_rolls_up_history
    app = desk
    app.call("POST", "/work_orders", unit: "TRK-14", task: "brake inspection")
    app.call("POST", "/work_orders", unit: "VAN-3", task: "coolant flush")
    app.call("POST", "/work_orders", unit: "TRK-14", task: "mirror swap")

    app.call("POST", "/work_orders/WO-1/labor", minutes: 45, note: "front pads")
    app.call("POST", "/work_orders/WO-1/parts", item: "brake pads set", cents: 12_500)
    app.call("POST", "/work_orders/WO-1/close")
    app.call("POST", "/work_orders/WO-3/labor", minutes: 20)

    res = app.call("GET", "/reports/unit/TRK-14")
    assert_equal 200, res.status
    assert_equal(
      {
        unit: "TRK-14",
        desc: "box truck 26ft",
        mileage: 82_450,
        orders: [
          { id: "WO-1", task: "brake inspection", status: "closed" },
          { id: "WO-3", task: "mirror swap", status: "open" }
        ],
        labor_minutes: 65,
        parts_cents: 12_500
      },
      res.body
    )
  end

  def test_unit_report_unknown_unit_404s
    app = desk
    res = app.call("GET", "/reports/unit/BUS-9")
    assert_equal 404, res.status
    assert_equal({ error: "no vehicle with unit BUS-9" }, res.body)
  end
end
