require "minitest/autorun"
require_relative "inspection_log"

# Acceptance tests for InspectionLog — the apiary's hive-inspection dataset.
# Run: ruby test_inspection_log.rb

class InspectionLogTest < Minitest::Test
  LINES = [
    "A7|2026-03-02|4|Q|calm",
    "B2|2026-03-02|6|-|testy near entrance",
    "C1|2026-03-09|3|Q|",
    "A7|2026-03-16|6|Q|supered",
    "D4|2026-03-16|2|-|weak, robbing screen on",
    "B2|2026-03-23|7|Q|requeened",
  ].freeze

  def log
    InspectionLog.new(LINES)
  end

  # -- each: the one method everything hangs off -----------------------------

  def test_each_yields_parsed_row_hashes_in_order
    rows = []
    log.each { |row| rows << row }
    assert_equal 6, rows.length
    assert_equal({ hive: "A7", date: "2026-03-02", frames: 4, queen: true, note: "calm" }, rows[0])
    assert_equal({ hive: "D4", date: "2026-03-16", frames: 2, queen: false, note: "weak, robbing screen on" }, rows[4])
    assert_equal({ hive: "C1", date: "2026-03-09", frames: 3, queen: true, note: "" }, rows[2])
  end

  def test_each_with_a_block_returns_the_log_itself
    l = log
    assert_same l, l.each { |_row| }
  end

  def test_each_without_a_block_returns_an_external_enumerator
    e = log.each
    assert_instance_of Enumerator, e
    assert_equal "A7", e.next[:hive]
    assert_equal "B2", e.next[:hive]
    assert_equal "C1", e.next[:hive]
  end

  def test_exhausted_external_enumerator_raises_stop_iteration
    e = InspectionLog.new(LINES.first(1)).each
    e.next
    assert_raises(StopIteration) { e.next }
  end

  # -- Enumerable behaves exactly like a core collection ----------------------

  def test_map_select_reduce_work
    assert_equal %w[A7 B2 C1 A7 D4 B2], log.map { |r| r[:hive] }
    assert_equal %w[A7 C1 A7 B2], log.select { |r| r[:queen] }.map { |r| r[:hive] }
    assert_equal 28, log.reduce(0) { |sum, r| sum + r[:frames] }
  end

  def test_count_min_by_first_and_include
    assert_equal 6, log.count
    assert_equal 4, log.count { |r| r[:queen] }
    assert_equal "D4", log.min_by { |r| r[:frames] }[:hive]
    assert_equal %w[A7 B2], log.first(2).map { |r| r[:hive] }
    row = { hive: "B2", date: "2026-03-23", frames: 7, queen: true, note: "requeened" }
    assert_includes log, row
  end

  def test_sort_by_composite_key_orders_like_core
    hives = log.sort_by { |r| [r[:frames], r[:date]] }.map { |r| r[:hive] }
    assert_equal %w[D4 C1 A7 B2 A7 B2], hives
  end

  def test_each_with_index_matches_core_semantics
    pairs = log.each_with_index.map { |row, i| [row[:hive], i] }
    assert_equal [["A7", 0], ["B2", 1], ["C1", 2], ["A7", 3], ["D4", 4], ["B2", 5]], pairs
    assert_equal log.to_a.each_with_index.to_a, log.each_with_index.to_a
  end

  def test_each_dot_with_index_takes_an_offset_like_core
    numbered = log.each.with_index(1).map { |row, n| "#{n}. #{row[:hive]}" }
    assert_equal ["1. A7", "2. B2", "3. C1", "4. A7", "5. D4", "6. B2"], numbered
  end

  # -- rows are parsed on demand, never at construction ------------------------

  def test_construction_never_parses
    InspectionLog.new(["total nonsense"]) # must not raise here
    InspectionLog.new(["A7|2026-03-02"])  # nor here
  end

  def test_iteration_stops_before_an_unreached_bad_line
    l = InspectionLog.new(LINES.first(3) + ["A7|2026-03-16|six|Q|supered"])
    assert_equal %w[A7 B2 C1], l.first(3).map { |r| r[:hive] }
    e = l.each
    assert_equal "A7", e.next[:hive]
  end

  def test_reaching_a_bad_frames_field_raises_with_line_number_and_raw_line
    l = InspectionLog.new(LINES.first(3) + ["A7|2026-03-16|six|Q|supered"])
    err = assert_raises(ArgumentError) { l.to_a }
    assert_equal "bad inspection line 4: A7|2026-03-16|six|Q|supered", err.message
  end

  def test_wrong_field_count_raises_with_line_number_and_raw_line
    l = InspectionLog.new(["A7|2026-03-02|4|Q|calm", "B2|2026-03-02|6"])
    err = assert_raises(ArgumentError) { l.map { |r| r } }
    assert_equal "bad inspection line 2: B2|2026-03-02|6", err.message
  end

  def test_unknown_queen_flag_raises_with_line_number_and_raw_line
    l = InspectionLog.new(["A7|2026-03-02|4|maybe|calm"])
    err = assert_raises(ArgumentError) { l.each { |_r| } }
    assert_equal "bad inspection line 1: A7|2026-03-02|4|maybe|calm", err.message
  end

  # -- scan: the lazy pipeline ------------------------------------------------

  def test_scan_returns_a_lazy_enumerator_over_parsed_rows
    s = log.scan
    assert_kind_of Enumerator::Lazy, s
    assert_equal %w[A7 B2 C1 A7 D4 B2], s.map { |r| r[:hive] }.to_a
  end

  def test_building_a_scan_chain_touches_nothing
    seen = 0
    log.scan.map { |r| seen += 1; r[:hive] }.select { |h| h.start_with?("A") }
    assert_equal 0, seen, "no row may be parsed or mapped before the chain is forced"
  end

  def test_scan_first_n_processes_exactly_n_rows
    seen = []
    got = log.scan.map { |r| seen << r[:hive]; r[:hive] }.first(2)
    assert_equal %w[A7 B2], got
    assert_equal %w[A7 B2], seen
  end

  def test_scan_select_stops_as_soon_as_it_has_enough
    probed = 0
    first_broodless = log.scan.select { |r| probed += 1; !r[:queen] }.first(1)
    assert_equal ["B2"], first_broodless.map { |r| r[:hive] }
    assert_equal 2, probed, "rows after the first match must not be probed"
  end

  def test_scan_never_reaches_a_bad_line_it_does_not_need
    l = InspectionLog.new(LINES.first(2) + ["garbage line"])
    assert_equal %w[A7 B2], l.scan.map { |r| r[:hive] }.first(2)
    err = assert_raises(ArgumentError) { l.scan.map { |r| r[:hive] }.to_a }
    assert_equal "bad inspection line 3: garbage line", err.message
  end

  # -- edge: the empty log -----------------------------------------------------

  def test_empty_log_behaves_like_an_empty_collection
    empty = InspectionLog.new([])
    assert_equal [], empty.to_a
    assert_nil empty.first
    assert_equal 0, empty.count
    assert_equal 0, empty.reduce(0) { |sum, r| sum + r[:frames] }
    assert_raises(StopIteration) { empty.each.next }
    assert_equal [], empty.scan.map { |r| r }.to_a
  end
end
