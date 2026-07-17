# Rack explorer for the tile-games club kiosk: groups a word list into
# "same rack" families — words spelled from exactly the same letter tiles
# (pears / spare / pares). The kiosk firmware meters dictionary work: every
# time a word's letters are examined, the scan meter ticks once for that
# word. Ops budgets renders by meter reading, not wall clock.
module TileGroups
  module_function

  # words:   unique lowercase words from the club's list.
  # counter: the kiosk scan meter (responds to tick!). Tick it once per
  #          word whose letters are examined, at the moment they are.
  #
  # Returns the families in first-appearance order; inside a family the
  # words keep their input order, led by the word that founded it.
  def build(words, counter:)
    families = []
    words.each do |word|
      home = families.find { |family| same_rack?(family.first, word, counter) }
      home ? home << word : families << [word]
    end
    families
  end

  # Two words draw from the same rack when their letter multisets match.
  # Examines both words' letters, so the meter ticks twice.
  def same_rack?(a, b, counter)
    counter.tick!
    counter.tick!
    a.chars.sort == b.chars.sort
  end
end
