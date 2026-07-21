# frozen_string_literal: true

require_relative "../../lib/property_check"

class AssertionFailure < StandardError; end

def assert_equal(expected, actual, message = nil)
  return if expected == actual

  detail = "Expected #{expected.inspect}, got #{actual.inspect}"
  raise AssertionFailure, [message, detail].compact.join(": ")
end

def assert(condition, message)
  raise AssertionFailure, message unless condition
end

def assert_raises(error_class)
  yield
rescue error_class => error
  error
rescue StandardError => error
  raise AssertionFailure,
        "Expected #{error_class}, but #{error.class} was raised: #{error.message}"
else
  raise AssertionFailure, "Expected #{error_class} to be raised"
end

def fixture_batch(size)
  Array.new(size) { format("fixture-%08x", Kernel.rand(2**32)) }
end

def test_successful_check_preserves_later_fixture_randomness
  Kernel.srand(41_041)
  fixture_batch(2)
  expected_fixtures = fixture_batch(4)

  Kernel.srand(41_041)
  fixture_batch(2)
  PropertyCheck.check(seed: 7_777, trials: 8) do |random, trial|
    value = random.rand(10_000)
    assert(value.between?(0, 9_999), "invalid value at trial #{trial}")
  end
  actual_fixtures = fixture_batch(4)

  assert_equal expected_fixtures, actual_fixtures,
               "a passing property check changed the caller RNG state"
end

def test_failing_check_preserves_rng_and_reports_replay_details
  Kernel.srand(92_092)
  fixture_batch(2)
  expected_fixtures = fixture_batch(4)

  Kernel.srand(92_092)
  fixture_batch(2)
  error = assert_raises(PropertyCheck::PropertyFailure) do
    PropertyCheck.check(seed: 8_801, trials: 6) do |random, trial|
      random.rand(1_000)
      raise "generated value was rejected" if trial == 3
    end
  end
  actual_fixtures = fixture_batch(4)

  assert_equal 8_801, error.seed
  assert_equal 3, error.trial
  assert(error.message.include?("seed 8801"), "failure message omitted its seed")
  assert(error.message.include?("trial 3"), "failure message omitted its trial")
  assert_equal expected_fixtures, actual_fixtures,
               "a failing property check changed the caller RNG state"
end

def failure_trace(seed)
  generated = []
  error = assert_raises(PropertyCheck::PropertyFailure) do
    PropertyCheck.check(seed: seed, trials: 7) do |random, trial|
      generated << [random.rand(1_000_000), random.rand(1_000_000)]
      raise "stop" if trial == 4
    end
  end

  [generated, error.seed, error.trial]
end

def test_failure_seed_replays_the_same_generated_examples
  first = failure_trace(61_061)
  second = failure_trace(61_061)

  assert_equal first, second, "the reported seed did not reproduce the failure"
end

tests = private_methods.select { |name| name.to_s.start_with?("test_") }.sort
failures = []

tests.each do |test_name|
  send(test_name)
  puts "PASS #{test_name}"
rescue StandardError => error
  failures << [test_name, error]
  warn "FAIL #{test_name}: #{error.message}"
end

if failures.empty?
  puts "#{tests.length} tests passed"
else
  warn "#{failures.length} of #{tests.length} tests failed"
  exit 1
end
