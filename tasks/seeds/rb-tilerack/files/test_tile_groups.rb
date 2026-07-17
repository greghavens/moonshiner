require "minitest/autorun"
require "digest"
require_relative "tile_groups"

# The kiosk scan meter. Firmware contract: any time a word's letters are
# examined — compared, sorted, counted, fingerprinted, whatever — the meter
# ticks once for that word at that moment. A pairwise check reads two words,
# so it ticks twice; computing a per-word signature reads one word, so it
# ticks once. Grouping code must never read letters off-meter.
class ScanMeter
  attr_reader :count

  def initialize
    @count = 0
  end

  def tick!
    @count += 1
  end
end

class TileGroupsTest < Minitest::Test
  # Meter budget for rebuilding the tournament list on the kiosk.
  SCAN_BUDGET = 60_000

  # SHA-256 of the serialized tournament grouping (groups in
  # first-appearance order, words space-joined, groups newline-joined).
  TOURNAMENT_DIGEST =
    "eebfe9b5f56fdd8d6a90649857b50c9e88dcf215b3ef20b91d2dcf5cb71fe168"

  # The 2,625-word tournament list, rebuilt deterministically from a fixed
  # seed: 1,250 base racks, each scrambled into a family of 1..6 words.
  def self.tournament_corpus
    @tournament_corpus ||= begin
      rng = Random.new(20_260_714)
      letters = ("a".."z").to_a
      words = []
      seen = {}
      1250.times do |i|
        len = 5 + rng.rand(4)
        base = Array.new(len) { letters[rng.rand(26)] }
        family_size = case i % 10
                      when 0 then 6
                      when 3 then 4
                      when 6 then 3
                      when 8 then 2
                      else 1
                      end
        made = 0
        guard = 0
        while made < family_size && guard < 60
          guard += 1
          candidate = base.shuffle(random: rng).join
          next if seen[candidate]

          seen[candidate] = true
          words << candidate
          made += 1
        end
      end
      words
    end
  end

  def serialize(groups)
    groups.map { |group| group.join(" ") }.join("\n")
  end

  def test_words_sharing_a_rack_are_grouped
    words = %w[pears spare risen pares siren rinse stop tops opts gale]
    groups = TileGroups.build(words, counter: ScanMeter.new)
    assert_equal [%w[pears spare pares], %w[risen siren rinse],
                  %w[stop tops opts], %w[gale]], groups
  end

  def test_letter_counts_matter_not_just_letters
    groups = TileGroups.build(%w[aab abb ab baa], counter: ScanMeter.new)
    assert_equal [%w[aab baa], %w[abb], %w[ab]], groups
  end

  def test_singletons_stay_alone
    groups = TileGroups.build(%w[cat dog bird], counter: ScanMeter.new)
    assert_equal [%w[cat], %w[dog], %w[bird]], groups
  end

  def test_empty_list_gives_no_groups
    assert_equal [], TileGroups.build([], counter: ScanMeter.new)
  end

  def test_groups_keep_first_appearance_order
    words = %w[night thing stop tango pots]
    groups = TileGroups.build(words, counter: ScanMeter.new)
    assert_equal [%w[night thing], %w[stop pots], %w[tango]], groups
  end

  def test_tournament_list_fits_the_scan_budget
    words = self.class.tournament_corpus
    meter = ScanMeter.new
    groups = TileGroups.build(words, counter: meter)
    assert_equal TOURNAMENT_DIGEST, Digest::SHA256.hexdigest(serialize(groups)),
                 "tournament grouping changed (order and membership are contract)"
    assert_operator meter.count, :>=, words.size,
                    "the meter must see every word at least once"
    assert_operator meter.count, :<=, SCAN_BUDGET,
                    "scan meter read #{meter.count} for #{words.size} words " \
                    "(budget #{SCAN_BUDGET})"
  end
end
