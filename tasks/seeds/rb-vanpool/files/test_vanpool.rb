require "minitest/autorun"
require "open3"
require "rbconfig"

require_relative "vanpool"

class VanpoolBehaviorTest < Minitest::Test
  def test_house_default_seat_count_is_seven
    route = VanPool::Route.new("elm")
    assert_equal 7, route.seats_left
  end

  def test_route_overrides_win_over_the_house_defaults
    route = VanPool::Route.new("oak", seats: 9)
    route.add_rider("Priya")
    assert_equal 8, route.seats_left
  end

  def test_a_full_van_refuses_another_rider
    route = VanPool::Route.new("elm", seats: 2)
    route.add_rider("Ana")
    route.add_rider("Ben")
    err = assert_raises(RuntimeError) { route.add_rider("Chidi") }
    assert_equal "van is full", err.message
    assert_equal 0, route.seats_left
  end

  def test_monthly_riders_get_the_punch_card_discount
    route = VanPool::Route.new("elm")
    assert_equal 260, route.fare_cents_for
    assert_equal 230, route.fare_cents_for(monthly: true)
  end

  def test_statement_has_headcount_fares_and_fuel_lines_in_order
    route = VanPool::Route.new("elm")
    route.add_rider("Priya")
    route.add_rider("Marco")
    assert_equal [
      "route elm: 2 rider(s), 4 leg(s)",
      "fares: 20.80",
      "fuel surcharge: 1.60"
    ], route.statement_lines(4)
  end

  def test_roster_is_alphabetical
    route = VanPool::Route.new("elm")
    route.add_rider("Marco")
    route.add_rider("Ana")
    assert_equal %w[Ana Marco], route.roster
  end
end

class WarningGateTest < Minitest::Test
  DRIVER = 'route = VanPool::Route.new("gate", seats: 3); ' \
           'route.add_rider("Check"); route.statement_lines(1)'

  def test_ruby_w_loads_and_runs_the_library_without_warnings
    out, err, status = Open3.capture3(
      RbConfig.ruby, "-w", "-I", __dir__, "-r", "vanpool", "-e", DRIVER
    )
    assert status.success?, "gate driver failed (#{status.exitstatus}): #{err}"
    assert err.empty?, "ruby -w must load vanpool.rb with no warnings, got:\n#{err}"
  end
end
