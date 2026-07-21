# frozen_string_literal: true

module PropertyCheck
  class PropertyFailure < StandardError
    attr_reader :seed, :trial

    def initialize(seed:, trial:, error:)
      @seed = seed
      @trial = trial
      super("property failed at trial #{trial} with seed #{seed}: #{error.message}")
    end
  end

  # Yields a Random instance and a zero-based trial number. Keeping the seed in
  # the failure makes a generated example reproducible from test output.
  def self.check(seed: Random.new_seed, trials: 100)
    raise ArgumentError, "trials must be positive" unless trials.positive?

    random = Random.new(seed)
    Kernel.srand(seed)

    trials.times do |trial|
      begin
        yield random, trial
      rescue StandardError => error
        raise PropertyFailure.new(seed: seed, trial: trial, error: error)
      end
    end

    true
  end
end
