# frozen_string_literal: true

require "json"

class JsonStreamTransformer
  class InvalidDocument < StandardError; end
  class InvalidRecord < StandardError; end

  DOCUMENT_ERROR = "input must be a valid JSON array"

  def initialize(buffer_tracker:)
    @buffer_tracker = buffer_tracker
  end

  def transform(input, output)
    source = input.read
    @buffer_tracker.observe(source.bytesize)

    records = parse_document(source)
    transformed = records.each_with_index.map do |record, index|
      transform_record(record, index)
    end

    output.write(JSON.generate(transformed))
  end

  private

  def parse_document(source)
    parsed = JSON.parse(source)
    raise InvalidDocument, DOCUMENT_ERROR unless parsed.is_a?(Array)

    parsed
  rescue JSON::ParserError
    raise InvalidDocument, DOCUMENT_ERROR
  end

  def transform_record(record, index)
    unless record.is_a?(Hash)
      raise InvalidRecord, "record #{index} must be a JSON object"
    end

    record.merge("sequence" => index)
  end
end
