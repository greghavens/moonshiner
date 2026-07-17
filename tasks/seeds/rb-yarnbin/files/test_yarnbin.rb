require "minitest/autorun"
require "open3"
require "rbconfig"

require_relative "yarnbin"

class YarnBinBehaviorTest < Minitest::Test
  def full_card
    card = YarnBin::Card.new("B7", 20)
    card.rule
    card.colorway("Merino Rust", 4)
    card.rule(6)
    card.footer("hand wash only")
    card
  end

  def test_the_printed_card_is_byte_exact
    assert_equal(
      "         B7\n" \
      "--------------------\n" \
      "Merino Rust    x4\n" \
      "------\n" \
      "* hand wash only",
      full_card.render
    )
  end

  def test_rule_defaults_to_the_card_width_and_takes_a_narrower_one
    card = YarnBin::Card.new("A1", 8)
    card.rule
    card.rule(3)
    assert_equal "   A1\n--------\n---", card.render
  end

  def test_long_rows_are_clipped_to_the_card_width_never_wrapped
    card = YarnBin::Card.new("C2", 10)
    card.add("Alpaca Cloud Heather DK")
    assert_equal "    C2\nAlpaca Clo", card.render
  end

  def test_colorway_rows_pad_the_name_column
    card = YarnBin::Card.new("D9", 24)
    card.colorway("Slate", 12)
    assert_equal "           D9\nSlate          x12", card.render
  end

  def test_builder_calls_chain
    card = YarnBin::Card.new("E3", 12)
    assert_same card, card.add("row")
    assert_same card, card.rule
    assert_same card, card.footer("dry flat")
  end
end

class WarningGateTest < Minitest::Test
  DRIVER = 'c = YarnBin::Card.new("gate", 16); c.rule; ' \
           'c.colorway("Check", 1); c.rule(4); c.footer("ok"); c.render'

  def test_ruby_w_loads_and_runs_the_library_without_warnings
    out, err, status = Open3.capture3(
      RbConfig.ruby, "-w", "-I", __dir__, "-r", "yarnbin", "-e", DRIVER
    )
    assert status.success?, "gate driver failed (#{status.exitstatus}): #{err}"
    assert err.empty?, "ruby -w must load yarnbin.rb with no warnings, got:\n#{err}"
  end
end
