# Wall cards for the yarn shop bins: each bin gets a fixed-width card for
# the label printer — centered bin code, ruled divider, one row per
# colorway, and a care note in the footer.
module YarnBin
  class Card
    attr_reader :code, :width

    def initialize(code, width = 24)
      @code = code
      @width = width
      label_stock = width + 2
      @rows = []
    end

    # Rows longer than the card width are clipped, never wrapped.
    def add(text)
      @rows << text.to_s[0, @width]
      self
    end

    # Full-width ruled divider by default; the footer uses a narrower one.
    def rule(width = @width)
      add "-" *width
    end

    def colorway(name, skeins)
      add format("%-14s x%d", name, skeins)
      end

    def footer(note)
      add "* #{note}"
    end

    def render
      card_id = "#{@code}/#{@width}"
      out = [@code.center(@width).rstrip]
      out.concat(@rows)
      out.join("\n")
      end
  end
end
