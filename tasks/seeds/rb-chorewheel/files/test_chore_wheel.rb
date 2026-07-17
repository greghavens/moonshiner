require "minitest/autorun"
require "fileutils"
require_relative "chore_wheel"

# End-to-end contract for the co-op house's chore wheel: fair weekly
# assignment with history weighting, vacations that defer (never eat) a
# turn, swaps, and the plain-text chore sheet on disk.
class ChoreWheelTest < Minitest::Test
  SCRATCH = File.join(Dir.pwd, "chorewheel_scratch")

  def setup
    FileUtils.mkdir_p(SCRATCH)
    @path = File.join(SCRATCH, "house.sheet")
  end

  def teardown
    FileUtils.rm_rf(SCRATCH)
  end

  def house
    ChoreWheel.new(members: %w[ana ben chi], chores: %w[kitchen trash])
  end

  def duo
    ChoreWheel.new(members: %w[ana ben], chores: %w[dishes])
  end

  def busy_duo
    ChoreWheel.new(members: %w[ana ben], chores: %w[prep cook clean])
  end

  # -- roster ---------------------------------------------------------------

  def test_roster_validation
    err = assert_raises(ArgumentError) { ChoreWheel.new(members: [], chores: %w[trash]) }
    assert_equal "no members", err.message
    err = assert_raises(ArgumentError) { ChoreWheel.new(members: %w[ana], chores: []) }
    assert_equal "no chores", err.message
    err = assert_raises(ArgumentError) do
      ChoreWheel.new(members: ["ana", "bad name"], chores: %w[trash])
    end
    assert_equal 'invalid name: "bad name"', err.message
    err = assert_raises(ArgumentError) do
      ChoreWheel.new(members: %w[ana], chores: ["Trash!"])
    end
    assert_equal 'invalid name: "Trash!"', err.message
    err = assert_raises(ArgumentError) do
      ChoreWheel.new(members: %w[ana ben ana], chores: %w[trash])
    end
    assert_equal "duplicate member: ana", err.message
    err = assert_raises(ArgumentError) do
      ChoreWheel.new(members: %w[ana], chores: %w[trash trash])
    end
    assert_equal "duplicate chore: trash", err.message
  end

  def test_a_fresh_wheel_has_no_weeks
    wheel = house
    assert_equal 0, wheel.weeks_planned
    err = assert_raises(ArgumentError) { wheel.assignments(1) }
    assert_equal "no week 1", err.message
  end

  # -- fair assignment ------------------------------------------------------

  def test_first_week_follows_roster_order
    wheel = house
    plan = wheel.plan_week!
    assert_equal({ "kitchen" => "ana", "trash" => "ben" }, plan)
    assert_equal %w[kitchen trash], plan.keys
    assert_equal 1, wheel.weeks_planned
    assert_equal plan, wheel.assignments(1)
  end

  def test_history_weighting_balances_four_weeks
    wheel = house
    assert_equal({ "kitchen" => "ana", "trash" => "ben" }, wheel.plan_week!)
    assert_equal({ "kitchen" => "chi", "trash" => "ana" }, wheel.plan_week!)
    assert_equal({ "kitchen" => "ben", "trash" => "chi" }, wheel.plan_week!)
    assert_equal({ "ana" => 2, "ben" => 2, "chi" => 2 }, wheel.tally)
    assert_equal({ "kitchen" => "ana", "trash" => "ben" }, wheel.plan_week!)
  end

  def test_tally_counts_all_members_in_roster_order
    wheel = house
    assert_equal({ "ana" => 0, "ben" => 0, "chi" => 0 }, wheel.tally)
    assert_equal %w[ana ben chi], wheel.tally.keys
    wheel.plan_week!
    assert_equal({ "ana" => 1, "ben" => 1, "chi" => 0 }, wheel.tally)
  end

  def test_chores_can_double_up_when_members_run_out
    wheel = busy_duo
    assert_equal({ "prep" => "ana", "cook" => "ben", "clean" => "ana" },
                 wheel.plan_week!)
    assert_equal({ "prep" => "ben", "cook" => "ana", "clean" => "ben" },
                 wheel.plan_week!)
  end

  # -- vacations --------------------------------------------------------------

  def test_a_vacation_defers_the_turn_instead_of_eating_it
    wheel = duo
    wheel.vacation("ana", 3)
    picks = 5.times.map { wheel.plan_week!.fetch("dishes") }
    assert_equal %w[ana ben ben ana ben], picks
  end

  def test_a_week_with_everyone_away_plans_nothing
    wheel = ChoreWheel.new(members: %w[ana], chores: %w[dishes])
    wheel.vacation("ana", 1)
    assert_equal({}, wheel.plan_week!)
    assert_equal 1, wheel.weeks_planned
    assert_equal({}, wheel.assignments(1))
    assert_equal({ "dishes" => "ana" }, wheel.plan_week!)
  end

  def test_vacation_validation
    wheel = duo
    err = assert_raises(ArgumentError) { wheel.vacation("dave", 2) }
    assert_equal "unknown member: dave", err.message
    wheel.plan_week!
    err = assert_raises(ArgumentError) { wheel.vacation("ana", 1) }
    assert_equal "week 1 already planned", err.message
    wheel.vacation("ana", 2)
    wheel.vacation("ana", 2) # registering twice is fine
    assert_equal "ben", wheel.plan_week!.fetch("dishes")
  end

  # -- swaps -------------------------------------------------------------------

  def test_a_swap_exchanges_chores_for_that_week
    wheel = house
    wheel.plan_week!
    swapped = wheel.swap!(1, "ana", "ben")
    assert_equal({ "kitchen" => "ben", "trash" => "ana" }, swapped)
    assert_equal swapped, wheel.assignments(1)
    assert_equal({ "ana" => 1, "ben" => 1, "chi" => 0 }, wheel.tally)
  end

  def test_future_fairness_follows_the_swapped_history
    wheel = busy_duo
    wheel.plan_week!
    assert_equal({ "prep" => "ben", "cook" => "ana", "clean" => "ben" },
                 wheel.swap!(1, "ana", "ben"))
    assert_equal({ "ana" => 1, "ben" => 2 }, wheel.tally)
    assert_equal({ "prep" => "ana", "cook" => "ben", "clean" => "ana" },
                 wheel.plan_week!)
  end

  def test_swap_validation
    wheel = house
    wheel.plan_week!
    err = assert_raises(ArgumentError) { wheel.swap!(2, "ana", "ben") }
    assert_equal "no week 2", err.message
    err = assert_raises(ArgumentError) { wheel.swap!(1, "dave", "ana") }
    assert_equal "unknown member: dave", err.message
    err = assert_raises(ArgumentError) { wheel.swap!(1, "ana", "ana") }
    assert_equal "cannot swap ana with themselves", err.message
    err = assert_raises(ArgumentError) { wheel.swap!(1, "chi", "ana") }
    assert_equal "chi has no assignment in week 1", err.message
    err = assert_raises(ArgumentError) { wheel.swap!(1, "ana", "chi") }
    assert_equal "chi has no assignment in week 1", err.message
  end

  # -- the chore sheet on disk ---------------------------------------------------

  def test_sheet_format_is_exact
    wheel = house
    wheel.vacation("ana", 3)
    wheel.vacation("ben", 4)
    wheel.plan_week!
    wheel.plan_week!
    wheel.save(@path)
    expected = <<~SHEET
      choresheet v1
      members ana ben chi
      chores kitchen trash
      vacation 3 ana
      vacation 4 ben
      week 1 kitchen=ana trash=ben
      week 2 kitchen=chi trash=ana
    SHEET
    assert_equal expected, File.read(@path)
  end

  def test_sheet_round_trips_byte_stable_and_continues_identically
    wheel = house
    wheel.vacation("ben", 3)
    wheel.plan_week!
    wheel.plan_week!
    wheel.save(@path)
    copy = File.join(SCRATCH, "copy.sheet")
    loaded = ChoreWheel.load(@path)
    loaded.save(copy)
    assert_equal File.read(@path), File.read(copy)
    assert_equal 2, loaded.weeks_planned
    assert_equal wheel.assignments(1), loaded.assignments(1)
    assert_equal wheel.tally, loaded.tally
    assert_equal({ "kitchen" => "chi", "trash" => "ana" }, loaded.plan_week!)
    assert_equal wheel.plan_week!, loaded.assignments(3)
  end

  def test_empty_weeks_round_trip
    wheel = ChoreWheel.new(members: %w[ana], chores: %w[dishes])
    wheel.vacation("ana", 1)
    wheel.plan_week!
    wheel.save(@path)
    assert_includes File.read(@path), "week 1\n"
    loaded = ChoreWheel.load(@path)
    assert_equal({}, loaded.assignments(1))
    assert_equal({ "dishes" => "ana" }, loaded.plan_week!)
  end

  def test_load_rejects_a_foreign_file
    File.write(@path, "totally not a sheet\n")
    err = assert_raises(ArgumentError) { ChoreWheel.load(@path) }
    assert_equal "not a choresheet", err.message
  end
end
