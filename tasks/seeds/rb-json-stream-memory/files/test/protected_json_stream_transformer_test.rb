# frozen_string_literal: true

require "json"
require "stringio"
require_relative "../lib/json_stream_transformer"

class AssertionFailure < StandardError; end

class PeakBufferTracker
  attr_reader :observations, :peak_bytes

  def initialize(limit: nil)
    @limit = limit
    @observations = 0
    @peak_bytes = 0
  end

  def observe(bytes)
    @observations += 1
    @peak_bytes = [@peak_bytes, bytes].max
    return unless @limit && bytes > @limit

    raise AssertionFailure, "buffer grew to #{bytes} bytes (limit: #{@limit})"
  end
end

class ChunkedInput
  def initialize(source, chunk_sizes:, maximum_read:)
    @source = source.b
    @chunk_sizes = chunk_sizes
    @maximum_read = maximum_read
    @offset = 0
    @reads = 0
  end

  def read(length = nil)
    unless length && length.positive? && length <= @maximum_read
      raise AssertionFailure, "input must be read incrementally (requested #{length.inspect})"
    end
    return nil if @offset >= @source.bytesize

    requested = @chunk_sizes.fetch(@reads % @chunk_sizes.length)
    size = [length, requested, @source.bytesize - @offset].min
    chunk = @source.byteslice(@offset, size)
    @offset += size
    @reads += 1
    chunk
  end
end

def assert(condition, message)
  raise AssertionFailure, message unless condition
end

def assert_equal(expected, actual, message)
  return if expected == actual

  raise AssertionFailure, "#{message}\nexpected: #{expected.inspect}\n  actual: #{actual.inspect}"
end

def transform(source, tracker: PeakBufferTracker.new, initial_output: "")
  output = StringIO.new(initial_output.dup)
  output.seek(0, IO::SEEK_END)
  JsonStreamTransformer.new(buffer_tracker: tracker).transform(StringIO.new(source), output)
  [output.string, tracker]
end

def transform_input(input, tracker: PeakBufferTracker.new, initial_output: "")
  output = StringIO.new(initial_output.dup)
  output.seek(0, IO::SEEK_END)
  JsonStreamTransformer.new(buffer_tracker: tracker).transform(input, output)
  [output.string, tracker]
end

tests = {
  "preserves exact compact output and input order" => lambda do
    records = [
      {
        "id" => "first",
        "message" => "comma, bracket ] and quote \" stay data",
        "nested" => { "items" => [1, { "shape" => "{[]}" }] }
      },
      { "id" => "café", "enabled" => true, "amount" => -1250.0 },
      { "id" => "last", "sequence" => "replace me", "value" => nil }
    ]
    source = " \n[\n  #{records.map { |record| JSON.generate(record) }.join(",\n  ")}\n]\t"
    expected_records = records.each_with_index.map do |record, index|
      record.merge("sequence" => index)
    end

    actual, = transform(source)
    assert_equal(JSON.generate(expected_records), actual, "output bytes changed")
    assert_equal(%w[first café last], JSON.parse(actual).map { |record| record.fetch("id") }, "record order changed")
  end,

  "keeps an empty document output exact" => lambda do
    actual, = transform("  [ \n ] \t")
    assert_equal("[]", actual, "empty array encoding changed")
  end,

  "handles escapes and UTF-8 split across short input chunks" => lambda do
    records = [
      { "id" => "café", "text" => "quote: \" slash: \\\\ snowman: ☃", "nested" => [{ "ok" => true }] },
      { "id" => "最後", "sequence" => 91 }
    ]
    source = " \n[#{records.map { |record| JSON.generate(record) }.join(",\r\n")}]\t"
    input = ChunkedInput.new(source, chunk_sizes: [1, 2, 1, 3, 5, 1], maximum_read: 4_096)
    expected = JSON.generate(records.each_with_index.map { |record, index| record.merge("sequence" => index) })

    actual, tracker = transform_input(input)
    assert_equal(expected, actual, "chunked input changed the compact output")
    assert(tracker.observations.positive?, "buffer tracker was never called")
  end,

  "preserves the whole-document nesting boundary" => lambda do
    accepted_record = { "leaf" => true }
    98.times { accepted_record = { "nested" => accepted_record } }
    accepted_source = JSON.generate([accepted_record], max_nesting: false)
    accepted_expected = JSON.generate(
      [accepted_record.merge("sequence" => 0)],
      max_nesting: false
    )

    actual, = transform(accepted_source)
    assert_equal(accepted_expected, actual, "accepted nesting boundary changed")

    rejected_record = { "nested" => accepted_record }
    rejected_source = JSON.generate([rejected_record], max_nesting: false)
    output = StringIO.new("unchanged")
    output.seek(0, IO::SEEK_END)

    begin
      JsonStreamTransformer.new(buffer_tracker: PeakBufferTracker.new).transform(
        StringIO.new(rejected_source),
        output
      )
      raise AssertionFailure, "expected InvalidDocument at the whole-document nesting limit"
    rescue JsonStreamTransformer::InvalidDocument => error
      assert_equal(JsonStreamTransformer::DOCUMENT_ERROR, error.message, "nesting failure message changed")
      assert_equal("unchanged", output.string, "nesting failure wrote partial output")
    end
  end,

  "reports malformed and non-array documents without touching output" => lambda do
    [
      "[{\"id\":1}, {\"id\":]",
      "{\"id\":1}",
      "[1,] trailing",
      "[1] trailing",
      "[[\"not an object\"], {\"id\":]",
      "[{\"id\":1}\v]"
    ].each do |source|
      output = StringIO.new("unchanged")
      output.seek(0, IO::SEEK_END)

      begin
        JsonStreamTransformer.new(buffer_tracker: PeakBufferTracker.new).transform(StringIO.new(source), output)
        raise AssertionFailure, "expected InvalidDocument for #{source.inspect}"
      rescue JsonStreamTransformer::InvalidDocument => error
        assert_equal(JsonStreamTransformer::DOCUMENT_ERROR, error.message, "document failure message changed")
        assert_equal("unchanged", output.string, "failed document wrote partial output")
      end
    end
  end,

  "reports the original record index without touching output" => lambda do
    source = JSON.generate([{ "id" => "ok" }, ["not", "an", "object"], { "id" => "late" }])
    output = StringIO.new("unchanged")
    output.seek(0, IO::SEEK_END)

    begin
      JsonStreamTransformer.new(buffer_tracker: PeakBufferTracker.new).transform(StringIO.new(source), output)
      raise AssertionFailure, "expected InvalidRecord"
    rescue JsonStreamTransformer::InvalidRecord => error
      assert_equal("record 1 must be a JSON object", error.message, "record failure changed")
      assert_equal("unchanged", output.string, "failed record wrote partial output")
    end
  end,

  "keeps the tracked input buffer bounded as record count grows" => lambda do
    records = Array.new(20_000) do |index|
      { "id" => index, "label" => "event-#{index % 100}" }
    end
    source = JSON.generate(records)
    tracker = PeakBufferTracker.new(limit: 4_096)

    actual, = transform(source, tracker: tracker)
    decoded = JSON.parse(actual)

    assert_equal(records.length, decoded.length, "records were dropped")
    assert_equal(0, decoded.first.fetch("sequence"), "first sequence is wrong")
    assert_equal(records.length - 1, decoded.last.fetch("sequence"), "last sequence is wrong")
    assert(tracker.observations.positive?, "buffer tracker was never called")
    assert(tracker.peak_bytes.positive?, "buffer tracker did not report buffered bytes")
    assert(tracker.peak_bytes <= 4_096, "peak buffer was not bounded")
  end,

  "does not request an unbounded input read" => lambda do
    records = Array.new(500) { |index| { "id" => index, "value" => "x" * 20 } }
    source = JSON.generate(records)
    input = ChunkedInput.new(source, chunk_sizes: [97, 31, 211], maximum_read: 4_096)

    actual, = transform_input(input)
    decoded = JSON.parse(actual)
    assert_equal(records.length, decoded.length, "incremental input lost records")
    assert_equal(499, decoded.last.fetch("sequence"), "incremental input sequence is wrong")
  end
}

failures = []
tests.each do |name, test|
  test.call
  puts "PASS #{name}"
rescue StandardError => error
  failures << [name, error]
  warn "FAIL #{name}: #{error.class}: #{error.message}"
end

unless failures.empty?
  warn "#{failures.length} of #{tests.length} tests failed"
  exit 1
end

puts "#{tests.length} tests passed"
