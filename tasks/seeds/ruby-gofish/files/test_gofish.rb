# Acceptance tests for gofish.rb.  Run: ruby test_gofish.rb
require "minitest/autorun"
require "open3"
require "tmpdir"

GOFISH = File.expand_path("gofish.rb", __dir__)
FIXTURES = File.expand_path("fixtures", __dir__)

def run_gofish_files(deck_path, script_path, players, deal)
  out, err, st = Open3.capture3(RbConfig.ruby, GOFISH, deck_path, script_path,
                                players.to_s, deal.to_s)
  [out, err, st.exitstatus]
end

def run_gofish(deck:, script:, players:, deal:)
  Dir.mktmpdir do |dir|
    deck_path = File.join(dir, "deck.txt")
    script_path = File.join(dir, "script.txt")
    File.write(deck_path, deck.map { |c| "#{c}\n" }.join)
    File.write(script_path, script.map { |l| "#{l}\n" }.join)
    run_gofish_files(deck_path, script_path, players, deal)
  end
end

class TestDealAndBooks < Minitest::Test
  def test_round_robin_deal_lays_initial_books_and_can_end_the_game
    out, err, rc = run_gofish(
      deck: %w[AS 2S AC 2C AH 2H AD 2D],
      script: [],
      players: 2, deal: 4
    )
    assert_equal "", err
    assert_equal 0, rc
    assert_equal <<~T, out
      BOOK P1 A
      BOOK P2 2
      GAME OVER
      FINAL P1 1 A
      FINAL P2 1 2
    T
  end

  def test_initial_books_redraw_while_stock_lasts
    out, err, rc = run_gofish(
      deck: %w[AS 2S AC 2C AH 2H AD 2D 9C 9D],
      script: ["P2 9", "P2 9"],
      players: 2, deal: 4
    )
    assert_equal "", err
    assert_equal 0, rc
    assert_equal <<~T, out
      BOOK P1 A
      REDRAW P1 9C
      BOOK P2 2
      REDRAW P2 9D
      TURN P1
      ASK P1 P2 9
      GIVE P2 P1 9 x1
      ILLEGAL P2 has no cards
      SCRIPT END
      FINAL P1 1 A
      FINAL P2 1 2
    T
  end
end

class TestAskGiveAndGoFish < Minitest::Test
  def test_give_keeps_the_turn_and_a_miss_passes_it
    out, err, rc = run_gofish(
      deck: %w[3H 7C 7D KS 7S 2C],
      script: ["P2 7", "P2 3", "P1 K", "P2 Q"],
      players: 2, deal: 3
    )
    assert_equal "", err
    assert_equal 0, rc
    assert_equal <<~T, out
      TURN P1
      ASK P1 P2 7
      GIVE P2 P1 7 x1
      ASK P1 P2 3
      GOFISH P1
      STOCK EMPTY
      TURN P2
      ASK P2 P1 K
      GOFISH P2
      STOCK EMPTY
      TURN P1
      ILLEGAL P1 does not hold Q
      SCRIPT END
      FINAL P1 0 -
      FINAL P2 0 -
    T
  end

  def test_lucky_draw_keeps_turn_and_a_drawn_book_redraws
    out, err, rc = run_gofish(
      deck: %w[5C 9C 5D 9D 5H 5S QH],
      script: ["P2 5", "P2 5", "P2 Q", "P1 9"],
      players: 2, deal: 2
    )
    assert_equal "", err
    assert_equal 0, rc
    assert_equal <<~T, out
      TURN P1
      ASK P1 P2 5
      GOFISH P1
      DRAW P1 5H
      LUCKY P1
      ASK P1 P2 5
      GOFISH P1
      DRAW P1 5S
      BOOK P1 5
      REDRAW P1 QH
      LUCKY P1
      ASK P1 P2 Q
      GOFISH P1
      STOCK EMPTY
      TURN P2
      ASK P2 P1 9
      GOFISH P2
      STOCK EMPTY
      TURN P1
      SCRIPT END
      FINAL P1 1 5
      FINAL P2 0 -
    T
  end

  def test_multi_card_give_book_by_give_and_giver_redraw
    out, err, rc = run_gofish(
      deck: %w[JC JD JH 2C 3C JS 7C 6D 7D 8H 9H],
      script: ["P3 J", "P2 J", "P3 7", "P2 2", "P1 3", "P1 8"],
      players: 3, deal: 3
    )
    assert_equal "", err
    assert_equal 0, rc
    assert_equal <<~T, out
      TURN P1
      ASK P1 P3 J
      GIVE P3 P1 J x2
      ASK P1 P2 J
      GIVE P2 P1 J x1
      BOOK P1 J
      ASK P1 P3 7
      GIVE P3 P1 7 x1
      REDRAW P3 8H
      ASK P1 P2 2
      GOFISH P1
      DRAW P1 9H
      TURN P2
      ASK P2 P1 3
      GOFISH P2
      STOCK EMPTY
      TURN P3
      ASK P3 P1 8
      GOFISH P3
      STOCK EMPTY
      TURN P1
      SCRIPT END
      FINAL P1 1 J
      FINAL P2 0 -
      FINAL P3 0 -
    T
  end

  def test_asker_who_empties_out_with_a_dry_stock_loses_the_turn
    out, err, rc = run_gofish(
      deck: %w[KC KD KH 2C KS 2D],
      script: ["P2 K", "P1 2"],
      players: 2, deal: 3
    )
    assert_equal "", err
    assert_equal 0, rc
    assert_equal <<~T, out
      TURN P1
      ASK P1 P2 K
      GIVE P2 P1 K x1
      BOOK P1 K
      TURN P2
      ILLEGAL P1 has no cards
      SCRIPT END
      FINAL P1 1 K
      FINAL P2 0 -
    T
  end

  def test_players_with_no_cards_and_no_stock_are_skipped
    out, err, rc = run_gofish(
      deck: %w[4C 8C 4H 4D 8D 8H],
      script: ["P3 4", "P2 4", "P3 8", "P1 8"],
      players: 3, deal: 2
    )
    assert_equal "", err
    assert_equal 0, rc
    assert_equal <<~T, out
      TURN P1
      ASK P1 P3 4
      GIVE P3 P1 4 x1
      ASK P1 P2 4
      GOFISH P1
      STOCK EMPTY
      TURN P2
      ASK P2 P3 8
      GIVE P3 P2 8 x1
      ASK P2 P1 8
      GOFISH P2
      STOCK EMPTY
      SKIP P3
      TURN P1
      SCRIPT END
      FINAL P1 0 -
      FINAL P2 0 -
      FINAL P3 0 -
    T
  end
end

class TestIllegalAsks < Minitest::Test
  def test_ask_validation_order_and_messages
    out, err, rc = run_gofish(
      deck: %w[5C 6C 7C 9C],
      script: ["P1 5", "P4 9", "P2 9", "P2", "P2 5X", "P2 5"],
      players: 3, deal: 1
    )
    assert_equal "", err
    assert_equal 0, rc
    assert_equal <<~T, out
      TURN P1
      ILLEGAL P1 cannot ask P1
      ILLEGAL P1 cannot ask P4
      ILLEGAL P1 does not hold 9
      ILLEGAL bad line: P2
      ILLEGAL bad line: P2 5X
      ASK P1 P2 5
      GOFISH P1
      DRAW P1 9C
      TURN P2
      SCRIPT END
      FINAL P1 0 -
      FINAL P2 0 -
      FINAL P3 0 -
    T
  end

  def test_empty_handed_targets_cannot_be_asked
    out, err, rc = run_gofish(
      deck: %w[5C 6C 5D],
      script: ["P3 5", "P3 5", "P2 5"],
      players: 3, deal: 1
    )
    assert_equal "", err
    assert_equal 0, rc
    assert_equal <<~T, out
      TURN P1
      ASK P1 P3 5
      GIVE P3 P1 5 x1
      ILLEGAL P3 has no cards
      ASK P1 P2 5
      GOFISH P1
      STOCK EMPTY
      TURN P2
      SCRIPT END
      FINAL P1 0 -
      FINAL P2 0 -
      FINAL P3 0 -
    T
  end

  def test_comments_and_blank_script_lines_are_skipped
    out, _err, rc = run_gofish(
      deck: %w[3H 7C 7D KS 7S 2C],
      script: ["# opening", "", "P2 7", "   ", "# done"],
      players: 2, deal: 3
    )
    assert_equal 0, rc
    assert_equal <<~T, out
      TURN P1
      ASK P1 P2 7
      GIVE P2 P1 7 x1
      SCRIPT END
      FINAL P1 0 -
      FINAL P2 0 -
    T
  end
end

class TestCliErrors < Minitest::Test
  USAGE = "usage: gofish.rb <deck-file> <script-file> <players> <cards-each>\n"

  def test_wrong_arity_prints_usage
    out, err, st = Open3.capture3(RbConfig.ruby, GOFISH, "only-one-arg")
    assert_equal "", out
    assert_equal USAGE, err
    assert_equal 2, st.exitstatus
  end

  def test_player_count_must_be_2_to_4
    [1, 5, "two"].each do |n|
      _out, err, rc = run_gofish(deck: %w[2C 3C], script: [], players: n, deal: 1)
      assert_equal "error: players must be 2-4\n", err
      assert_equal 2, rc
    end
  end

  def test_deal_size_must_be_positive
    _out, err, rc = run_gofish(deck: %w[2C 3C], script: [], players: 2, deal: 0)
    assert_equal "error: cards-each must be >= 1\n", err
    assert_equal 2, rc
  end

  def test_bad_deck_lines_are_reported_with_line_numbers
    _out, err, rc = run_gofish(deck: %w[7H 7X], script: [], players: 2, deal: 1)
    assert_equal "error: bad deck line 2: 7X\n", err
    assert_equal 2, rc

    _out, err, rc = run_gofish(deck: %w[10H 2C], script: [], players: 2, deal: 1)
    assert_equal "error: bad deck line 1: 10H\n", err
    assert_equal 2, rc
  end

  def test_duplicate_cards_are_rejected
    _out, err, rc = run_gofish(deck: %w[7H 2C 7H 3C], script: [], players: 2, deal: 2)
    assert_equal "error: duplicate card 7H\n", err
    assert_equal 2, rc
  end

  def test_deck_must_cover_the_deal
    _out, err, rc = run_gofish(deck: %w[7H 2C 3C], script: [], players: 2, deal: 2)
    assert_equal "error: not enough cards\n", err
    assert_equal 2, rc
  end

  def test_unreadable_files_are_reported
    Dir.mktmpdir do |dir|
      script_path = File.join(dir, "script.txt")
      File.write(script_path, "")
      missing = File.join(dir, "nope.txt")
      _out, err, rc = run_gofish_files(missing, script_path, 2, 1)
      assert_equal "error: cannot read #{missing}\n", err
      assert_equal 2, rc
    end
  end
end

class TestFullGame < Minitest::Test
  def test_full_52_card_game_reaches_the_pinned_final_books
    out, err, rc = run_gofish_files(File.join(FIXTURES, "full_deck.txt"),
                                    File.join(FIXTURES, "full_script.txt"), 3, 7)
    assert_equal "", err
    assert_equal 0, rc
    want = File.read(File.join(FIXTURES, "full_expected.txt"))
    assert_equal want, out
    assert_includes out, "GAME OVER"
    books = out.lines.count { |l| l.start_with?("BOOK ") }
    assert_equal 13, books, "every rank must end up in a book"
  end
end
