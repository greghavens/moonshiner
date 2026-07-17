require "minitest/autorun"
require_relative "rain_report"

# Acceptance tests for RainReport — terminal charts for the garden club's
# rain-gauge network. Run: ruby test_rain_report.rb

class SparklineTest < Minitest::Test
  def test_scales_min_to_max_across_the_eight_bars
    assert_equal "▁▂▃▄▅▆▇██", RainReport.sparkline([0, 1, 3, 5, 7, 9, 11, 13, 14])
  end

  def test_exact_halves_round_away_from_zero
    # 9 maps to level 4.5 -> bar 5 (▆), never banker's-rounded down to 4 (▅)
    assert_equal "▇▆▃▁▂▅█", RainReport.sparkline([12, 9, 4, 0, 2, 7, 14])
  end

  def test_equal_values_get_equal_bars
    assert_equal "▁██▃▁", RainReport.sparkline([0, 10, 10, 3, 0])
  end

  def test_negative_readings_are_fine
    assert_equal "▁▅█", RainReport.sparkline([-3, -1, 1])
  end

  def test_floats_are_fine
    assert_equal "▁▃█", RainReport.sparkline([0.25, 0.5, 1.0])
  end

  def test_all_equal_series_renders_the_lowest_bar
    assert_equal "▁▁▁", RainReport.sparkline([5, 5, 5])
  end

  def test_single_reading_renders_the_lowest_bar
    assert_equal "▁", RainReport.sparkline([42])
  end

  def test_empty_series_renders_an_empty_string
    assert_equal "", RainReport.sparkline([])
  end
end

class HistogramTest < Minitest::Test
  def test_buckets_span_min_to_max_and_lines_are_pinned
    values = [0, 2, 11, 5, 0, 1, 7, 12, 3, 0, 6, 9]
    expected = [
      " 0.0..3.0 |██████████ 5",
      " 3.0..6.0 |████ 2",
      " 6.0..9.0 |████ 2",
      "9.0..12.0 |██████ 3",
    ].join("\n")
    assert_equal expected, RainReport.histogram(values, buckets: 4, width: 10)
  end

  def test_empty_buckets_are_still_rendered
    expected = [
      "  0.0..4.0 |███ 1",
      "  4.0..8.0 |██████ 2",
      " 8.0..12.0 | 0",
      "12.0..16.0 |██████ 2",
    ].join("\n")
    assert_equal expected, RainReport.histogram([0, 4, 4, 15, 16], buckets: 4, width: 6)
  end

  def test_a_boundary_value_opens_the_next_bucket
    expected = [
      "-4.0..-1.0 |█ 1",
      " -1.0..2.0 |████ 3",
    ].join("\n")
    assert_equal expected, RainReport.histogram([-4, -1, 0, 2], buckets: 2, width: 4)
  end

  def test_nonzero_counts_always_draw_at_least_one_bar_char
    values = [0] * 20 + [10]
    expected = [
      " 0.0..5.0 |████████ 20",
      "5.0..10.0 |█ 1",
    ].join("\n")
    assert_equal expected, RainReport.histogram(values, buckets: 2, width: 8)
  end

  def test_all_equal_values_land_in_the_first_bucket
    expected = [
      "7.0..7.0 |████ 3",
      "7.0..7.0 | 0",
      "7.0..7.0 | 0",
    ].join("\n")
    assert_equal expected, RainReport.histogram([7, 7, 7], buckets: 3, width: 4)
  end

  def test_single_value_single_bucket
    assert_equal "5.0..5.0 |███ 1", RainReport.histogram([5], buckets: 1, width: 3)
  end

  def test_no_trailing_newline_anywhere
    out = RainReport.histogram([1, 2], buckets: 1, width: 2)
    refute out.end_with?("\n")
    refute_includes out, "\n\n"
  end

  def test_empty_series_raises
    err = assert_raises(ArgumentError) { RainReport.histogram([], buckets: 3, width: 5) }
    assert_equal "no data", err.message
  end

  def test_bucket_and_width_arguments_are_validated
    err = assert_raises(ArgumentError) { RainReport.histogram([1], buckets: 0, width: 5) }
    assert_equal "buckets must be >= 1, got 0", err.message
    err = assert_raises(ArgumentError) { RainReport.histogram([1], buckets: 3, width: -2) }
    assert_equal "width must be >= 1, got -2", err.message
  end
end
