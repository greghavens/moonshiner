require "minitest/autorun"
require_relative "route_query"

# Acceptance tests for RouteQuery — the setters' board query tool.
# Run: ruby test_route_query.rb

class RouteQueryTest < Minitest::Test
  def board
    # A slice of the route board. Insertion order matters for stability tests.
    [
      { name: "Crimp Reaper",   grade: 6, wall: "overhang", setter: "Mia",  days_up: 21 },
      { name: "Jug Life",       grade: 2, wall: "slab",     setter: "To",   days_up: 3  },
      { name: "Gaston Alone",   grade: 4, wall: "overhang", setter: "Mia",  days_up: 9  },
      { name: "Heel the World", grade: 4, wall: "roof",     setter: "Sam",  days_up: 30 },
      { name: "Slabby Road",    grade: 2, wall: "slab",     setter: "Mia",  days_up: 14 },
      { name: "Dyno Might",     grade: 6, wall: "roof",     setter: "To",   days_up: 2  },
    ]
  end

  # -- where: hash form ------------------------------------------------------

  def test_where_hash_filters_by_equality
    q = RouteQuery.new(board).where(wall: "slab")
    assert_equal ["Jug Life", "Slabby Road"], q.pluck(:name)
  end

  def test_where_hash_with_two_keys_ands_them
    q = RouteQuery.new(board).where(wall: "overhang", setter: "Mia")
    assert_equal ["Crimp Reaper", "Gaston Alone"], q.pluck(:name)
  end

  def test_chained_wheres_and_together
    q = RouteQuery.new(board).where(setter: "Mia").where(grade: 4)
    assert_equal ["Gaston Alone"], q.pluck(:name)
  end

  def test_where_key_absent_from_a_row_never_matches
    rows = [{ name: "Old Card", grade: 3 }, { name: "New Card", grade: 3, wall: "slab" }]
    q = RouteQuery.new(rows).where(wall: "slab")
    assert_equal ["New Card"], q.pluck(:name)
  end

  # -- where: block form -----------------------------------------------------

  def test_where_block_filters_rows
    q = RouteQuery.new(board).where { |r| r[:days_up] > 20 }
    assert_equal ["Crimp Reaper", "Heel the World"], q.pluck(:name)
  end

  def test_where_hash_and_block_can_mix_across_links
    q = RouteQuery.new(board).where(wall: "roof").where { |r| r[:grade] < 5 }
    assert_equal ["Heel the World"], q.pluck(:name)
  end

  def test_where_with_both_hash_and_block_raises
    err = assert_raises(ArgumentError) do
      RouteQuery.new(board).where(wall: "slab") { |r| r[:grade] > 1 }
    end
    assert_equal "where takes a filter hash or a block, not both", err.message
  end

  def test_where_with_neither_hash_nor_block_raises
    err = assert_raises(ArgumentError) { RouteQuery.new(board).where }
    assert_equal "where needs a filter hash or a block", err.message
  end

  # -- order -----------------------------------------------------------------

  def test_order_defaults_to_ascending
    q = RouteQuery.new(board).order(:grade)
    assert_equal [2, 2, 4, 4, 6, 6], q.pluck(:grade)
  end

  def test_order_desc
    q = RouteQuery.new(board).order(:days_up, :desc)
    assert_equal ["Heel the World", "Crimp Reaper", "Slabby Road",
                  "Gaston Alone", "Jug Life", "Dyno Might"], q.pluck(:name)
  end

  def test_order_is_stable_ties_keep_insertion_order
    q = RouteQuery.new(board).order(:grade)
    assert_equal ["Jug Life", "Slabby Road",       # the 2s, board order
                  "Gaston Alone", "Heel the World", # the 4s, board order
                  "Crimp Reaper", "Dyno Might"],    # the 6s, board order
                 q.pluck(:name)
  end

  def test_order_desc_is_stable_too
    q = RouteQuery.new(board).order(:wall, :desc)
    # walls sort desc: slab, slab, roof, roof, overhang, overhang — ties in board order
    assert_equal ["Jug Life", "Slabby Road", "Heel the World",
                  "Dyno Might", "Crimp Reaper", "Gaston Alone"], q.pluck(:name)
  end

  def test_order_stability_holds_across_a_full_reset_sheet
    # 26 routes, grades strictly alternating 3 and 4. Sorting by grade must
    # keep every route in sheet order within its grade — no exceptions, at
    # any board size.
    sheet = (1..26).map do |i|
      { name: format("R%02d", i), grade: i.odd? ? 3 : 4, wall: "north" }
    end
    names = RouteQuery.new(sheet).order(:grade).pluck(:name)
    threes = (1..26).select(&:odd?).map { |i| format("R%02d", i) }
    fours = (1..26).select(&:even?).map { |i| format("R%02d", i) }
    assert_equal threes + fours, names
  end

  def test_chained_orders_compose_first_is_primary
    q = RouteQuery.new(board).order(:setter).order(:grade, :desc)
    assert_equal ["Crimp Reaper", "Gaston Alone", "Slabby Road", # Mia by grade desc
                  "Heel the World",                              # Sam
                  "Dyno Might", "Jug Life"],                     # To by grade desc
                 q.pluck(:name)
  end

  def test_order_rejects_unknown_direction
    err = assert_raises(ArgumentError) { RouteQuery.new(board).order(:grade, :sideways) }
    assert_equal "direction must be :asc or :desc, got :sideways", err.message
  end

  # -- terminals -------------------------------------------------------------

  def test_pluck_one_key_returns_flat_array
    q = RouteQuery.new(board).where(grade: 2).order(:days_up)
    assert_equal ["To", "Mia"], q.pluck(:setter)
  end

  def test_pluck_many_keys_returns_row_arrays
    q = RouteQuery.new(board).where(wall: "roof").order(:grade)
    assert_equal [["Heel the World", 4], ["Dyno Might", 6]], q.pluck(:name, :grade)
  end

  def test_count_and_empty_results
    base = RouteQuery.new(board)
    assert_equal 6, base.count
    assert_equal 2, base.where(setter: "To").count
    none = base.where(setter: "Ondra")
    assert_equal 0, none.count
    assert_equal [], none.to_a
    assert_equal [], none.pluck(:name)
  end

  def test_to_a_returns_the_matching_row_hashes_in_a_fresh_array
    rows = board
    q = RouteQuery.new(rows).where(wall: "slab")
    a = q.to_a
    b = q.to_a
    refute_same a, b, "each to_a call should build a new array"
    assert_same rows[1], a[0], "to_a should yield the source row objects themselves"
    assert_same rows[4], a[1]
    assert_equal a, b
  end

  # -- laziness --------------------------------------------------------------

  def test_nothing_runs_until_a_terminal
    probes = 0
    q = RouteQuery.new(board)
                  .where { |r| probes += 1; r[:grade] > 1 }
                  .order(:name)
    assert_equal 0, probes, "building the chain must not touch any row"
    q.to_a
    assert_equal 6, probes
  end

  def test_terminals_reevaluate_every_call_no_caching
    probes = 0
    q = RouteQuery.new(board).where { |r| probes += 1; r[:wall] == "roof" }
    assert_equal 2, q.count
    assert_equal 2, q.count
    assert_equal 12, probes, "each terminal call should re-run the filters"
  end

  def test_rows_added_to_the_source_before_a_terminal_are_seen
    rows = board
    q = RouteQuery.new(rows).where(wall: "cave")
    assert_equal 0, q.count
    rows << { name: "Bat Hang", grade: 5, wall: "cave", setter: "Sam", days_up: 1 }
    assert_equal ["Bat Hang"], q.pluck(:name)
  end

  # -- immutable chaining ----------------------------------------------------

  def test_each_link_is_a_new_query_object
    base = RouteQuery.new(board)
    filtered = base.where(wall: "slab")
    ordered = filtered.order(:grade)
    assert_instance_of RouteQuery, filtered
    assert_instance_of RouteQuery, ordered
    refute_same base, filtered
    refute_same filtered, ordered
  end

  def test_deriving_a_link_leaves_the_parent_untouched
    base = RouteQuery.new(board)
    mia = base.where(setter: "Mia")
    mia_hard = mia.where(grade: 6)
    assert_equal 1, mia_hard.count
    assert_equal 3, mia.count, "child filters must not leak into the parent"
    assert_equal 6, base.count
    assert_equal ["Crimp Reaper", "Gaston Alone", "Slabby Road"], mia.pluck(:name)
  end

  def test_order_on_a_shared_parent_does_not_leak
    base = RouteQuery.new(board).where(setter: "Mia")
    by_grade = base.order(:grade)
    by_days = base.order(:days_up, :desc)
    assert_equal ["Slabby Road", "Gaston Alone", "Crimp Reaper"], by_grade.pluck(:name)
    assert_equal ["Crimp Reaper", "Slabby Road", "Gaston Alone"], by_days.pluck(:name)
    assert_equal ["Crimp Reaper", "Gaston Alone", "Slabby Road"], base.pluck(:name)
  end

  def test_works_on_frozen_source_and_never_mutates_it
    rows = board.map(&:freeze).freeze
    q = RouteQuery.new(rows).where(wall: "overhang").order(:grade, :desc)
    assert_equal ["Crimp Reaper", "Gaston Alone"], q.pluck(:name)
    assert_equal 6, rows.length
  end
end
