# Acceptance tests for lights_out.rb.  Run: ruby test_lights_out.rb
require "minitest/autorun"
require_relative "lights_out"

BOARDS_DIR = File.expand_path("boards", __dir__)

def load_board(name)
  LightsOut.parse(File.read(File.join(BOARDS_DIR, "#{name}.txt")))
end

class TestParseAndRender < Minitest::Test
  def test_parse_reads_rows_top_to_bottom
    b = load_board("diag")
    5.times do |i|
      5.times do |j|
        assert_equal(i == j ? 1 : 0, b[i][j], "cell #{i},#{j}")
      end
    end
  end

  def test_parse_accepts_trailing_newline_and_none
    assert_equal LightsOut.parse("00000\n" * 5), LightsOut.parse(("00000\n" * 5).chomp)
  end

  def test_render_round_trips_every_fixture
    %w[center_cross all_on corners diag two_dots scatter lone_light dark near_scatter].each do |name|
      raw = File.read(File.join(BOARDS_DIR, "#{name}.txt"))
      b = LightsOut.parse(raw)
      assert_equal raw.chomp, LightsOut.render(b), "render(parse(#{name}))"
      assert_equal b, LightsOut.parse(LightsOut.render(b)), "parse(render(#{name}))"
    end
  end

  def test_parse_rejects_malformed_boards
    ["", "00000\n00000\n00000\n00000\n", "00000\n" * 6,
     "0000\n00000\n00000\n00000\n00000\n",
     "00000\n00000\n002000\n00000\n00000\n",
     "00000\n00000\n00x00\n00000\n00000\n",
     "00000\n\n00000\n00000\n00000\n00000\n"].each do |bad|
      assert_raises(ArgumentError, "should reject #{bad.inspect}") { LightsOut.parse(bad) }
    end
  end
end

class TestPress < Minitest::Test
  def test_center_press_toggles_plus_cross
    b = load_board("dark")
    got = LightsOut.press(b, 2, 2)
    assert_equal load_board("center_cross"), got
  end

  def test_corner_and_edge_presses_clip_to_board
    b = LightsOut.press(load_board("dark"), 0, 0)
    assert_equal LightsOut.parse("11000\n10000\n00000\n00000\n00000"), b
    b = LightsOut.press(load_board("dark"), 4, 2)
    assert_equal LightsOut.parse("00000\n00000\n00000\n00100\n01110"), b
    b = LightsOut.press(load_board("dark"), 2, 4)
    assert_equal LightsOut.parse("00000\n00001\n00011\n00001\n00000"), b
  end

  def test_press_toggles_rather_than_sets
    b = load_board("all_on")
    got = LightsOut.press(b, 2, 2)
    assert_equal LightsOut.parse("11111\n11011\n10001\n11011\n11111"), got
  end

  def test_press_twice_is_identity
    b = load_board("scatter")
    assert_equal b, LightsOut.press(LightsOut.press(b, 1, 3), 1, 3)
  end

  def test_press_does_not_mutate_its_input
    b = load_board("dark")
    before = Marshal.load(Marshal.dump(b))
    LightsOut.press(b, 2, 2)
    assert_equal before, b, "press must return a new board, not edit in place"
  end

  def test_press_rejects_out_of_range_coordinates
    b = load_board("dark")
    [[-1, 0], [0, -1], [5, 0], [0, 5], [7, 7]].each do |r, c|
      assert_raises(ArgumentError, "press(#{r},#{c})") { LightsOut.press(b, r, c) }
    end
  end
end

class TestQueries < Minitest::Test
  def test_lit_count
    assert_equal 0, LightsOut.lit_count(load_board("dark"))
    assert_equal 25, LightsOut.lit_count(load_board("all_on"))
    assert_equal 5, LightsOut.lit_count(load_board("center_cross"))
    assert_equal 19, LightsOut.lit_count(load_board("scatter"))
  end

  def test_solved_predicate
    assert LightsOut.solved?(load_board("dark"))
    refute LightsOut.solved?(load_board("two_dots"))
  end
end

class TestSolve < Minitest::Test
  PINNED = {
    "dark"         => [],
    "center_cross" => [[2, 2]],
    "diag"         => [[0, 0], [1, 1], [2, 2], [3, 3], [4, 4]],
    "corners"      => [[1, 0], [1, 4], [2, 0], [2, 1], [2, 3], [2, 4], [3, 0], [3, 4]],
    "two_dots"     => [[0, 1], [1, 0], [1, 1], [1, 2], [2, 1], [2, 3], [3, 2], [3, 3], [3, 4], [4, 3]],
    "scatter"      => [[0, 0], [0, 1], [1, 4], [2, 2], [3, 0], [4, 4]],
    "all_on"       => [[0, 0], [0, 1], [1, 0], [1, 1], [1, 3], [1, 4], [2, 2], [2, 3], [2, 4],
                       [3, 1], [3, 2], [3, 3], [4, 1], [4, 2], [4, 4]],
  }.freeze

  def test_pinned_solutions_for_solvable_fixtures
    PINNED.each do |name, want|
      assert_equal want, LightsOut.solve(load_board(name)), "solve(#{name})"
    end
  end

  def test_solutions_actually_clear_the_board
    PINNED.each_key do |name|
      b = load_board(name)
      LightsOut.solve(b).each { |r, c| b = LightsOut.press(b, r, c) }
      assert LightsOut.solved?(b), "applying solve(#{name}) must turn every light off"
    end
  end

  def test_press_lists_are_sorted_and_duplicate_free
    PINNED.each_key do |name|
      sol = LightsOut.solve(load_board(name))
      assert_equal sol.sort, sol, "solve(#{name}) must come back row-major sorted"
      assert_equal sol.uniq, sol, "solve(#{name}) must not press a cell twice"
    end
  end

  def test_unsolvable_boards_return_nil
    assert_nil LightsOut.solve(load_board("lone_light"))
    assert_nil LightsOut.solve(load_board("near_scatter"))
  end

  def test_solve_does_not_mutate_its_input
    b = load_board("all_on")
    before = Marshal.load(Marshal.dump(b))
    LightsOut.solve(b)
    assert_equal before, b
  end

  def test_single_press_boards_follow_the_stated_policy
    # A board lit by one press is always solvable; for most cells the stated
    # policy hands back exactly that press, but NOT for every cell — the two
    # pinned exceptions below separate the required policy from a "shortest
    # solution" solver.
    policy_exceptions = {
      [0, 3] => [[0, 1], [0, 2], [1, 0], [1, 2], [1, 4], [2, 0], [2, 1], [2, 3], [2, 4],
                 [3, 0], [3, 2], [3, 4], [4, 1], [4, 2], [4, 3]],
      [0, 4] => [[0, 0], [0, 2], [1, 0], [1, 2], [1, 4], [3, 0], [3, 2], [3, 4],
                 [4, 0], [4, 2], [4, 4]],
    }
    5.times do |r|
      5.times do |c|
        b = LightsOut.press(load_board("dark"), r, c)
        want = policy_exceptions.fetch([r, c], [[r, c]])
        got = LightsOut.solve(b)
        assert_equal want, got, "board lit by pressing #{r},#{c}"
        got.each { |pr, pc| b = LightsOut.press(b, pr, pc) }
        assert LightsOut.solved?(b)
      end
    end
  end
end
