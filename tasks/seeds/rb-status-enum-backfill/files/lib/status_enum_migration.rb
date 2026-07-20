# frozen_string_literal: true

module StatusEnumMigration
  class UnknownStatus < ArgumentError; end
  class UnsupportedVersion < ArgumentError; end

  StoreRow = Struct.new(:id, :revision, :document, keyword_init: true)
  Result = Struct.new(:resume_token, :migrated_ids, :unmappable_rows, keyword_init: true)

  module StatusCatalog
    VERSION = 1

    CODES = {
      "pending" => 10,
      "active" => 20,
      "suspended" => 30,
      "closed" => 40
    }.freeze

    LEGACY_NAMES = {
      "pending" => "pending",
      "awaiting review" => "pending",
      "active" => "active",
      "enabled" => "active",
      "suspended" => "suspended",
      "on hold" => "suspended",
      "closed" => "closed",
      "archived" => "closed"
    }.freeze

    NAMES_BY_CODE = CODES.invert.freeze

    module_function

    def canonical_legacy(value)
      return nil unless value.is_a?(String)

      LEGACY_NAMES[normalize(value)]
    end

    def code_for_canonical(value)
      CODES[value]
    end

    def canonical_for_code(code)
      NAMES_BY_CODE[code]
    end

    def normalize(value)
      value.strip.downcase.gsub(/[[:space:]]+/, " ")
    end
    private_class_method :normalize
  end

  module StatusWriter
    module_function

    def attributes_for(status)
      code = StatusCatalog.code_for_canonical(status)
      raise UnknownStatus, "unknown canonical status: #{status.inspect}" unless code

      {
        "status" => status,
        "status_version" => StatusCatalog::VERSION,
        "status_code" => code
      }
    end
  end

  module StatusReader
    module_function

    def read(document)
      if document.key?("status_version") || document.key?("status_code")
        read_versioned(document)
      else
        read_legacy(document)
      end
    end

    def read_versioned(document)
      version = document.fetch("status_version")
      unless version == StatusCatalog::VERSION
        raise UnsupportedVersion, "unsupported status version: #{version.inspect}"
      end

      code = document.fetch("status_code")
      StatusCatalog.canonical_for_code(code) ||
        raise(UnknownStatus, "unknown status code for v#{version}: #{code.inspect}")
    end
    private_class_method :read_versioned

    def read_legacy(document)
      value = document.fetch("status")
      StatusCatalog.canonical_legacy(value) ||
        raise(UnknownStatus, "unmappable legacy status: #{value.inspect}")
    end
    private_class_method :read_legacy
  end

  module LegacyStatusReader
    module_function

    def read(document)
      document.fetch("status")
    end
  end

  class Backfill
    def initialize(store)
      @store = store
    end

    def run(resume_token: nil, limit:)
      raise ArgumentError, "limit must be a positive integer" unless limit.is_a?(Integer) && limit.positive?

      rows, next_token = @store.scan(after: resume_token, limit: limit)
      migrated_ids = []
      unmappable_rows = []

      rows.each do |row|
        document = row.document
        next if versioned?(document)

        canonical = StatusCatalog.canonical_legacy(document["status"])
        unless canonical
          unmappable_rows << { "id" => row.id, "status" => document["status"] }
          next
        end

        replacement = document.dup
        replacement["status_version"] = StatusCatalog::VERSION
        replacement["status_code"] = StatusCatalog.code_for_canonical(canonical)
        replacement.delete("status")

        if @store.compare_and_swap(row.id, row.revision, replacement)
          migrated_ids << row.id
        end
      end

      Result.new(
        resume_token: next_token,
        migrated_ids: migrated_ids,
        unmappable_rows: unmappable_rows
      )
    end

    private

    def versioned?(document)
      document.key?("status_version") || document.key?("status_code")
    end
  end

  class InMemoryStore
    Stored = Struct.new(:document, :revision, keyword_init: true)

    def initialize(documents)
      @rows = documents.map do |document|
        Stored.new(document: deep_copy(document), revision: 0)
      end
      @resume_positions = {}
      @write_counts = Hash.new(0)
    end

    def scan(after:, limit:)
      start_at = after.nil? ? 0 : decode_token(after)
      finish_at = [start_at + limit, @rows.length].min
      selected = @rows[start_at...finish_at] || []
      rows = selected.map do |stored|
        document = deep_copy(stored.document)
        StoreRow.new(
          id: document.fetch("id"),
          revision: stored.revision,
          document: document
        )
      end

      token = finish_at < @rows.length ? token_for(finish_at) : nil
      [rows, token]
    end

    def compare_and_swap(id, expected_revision, replacement)
      stored = @rows.find { |candidate| candidate.document["id"] == id }
      return false unless stored && stored.revision == expected_revision

      stored.document = deep_copy(replacement)
      stored.revision += 1
      @write_counts[id] += 1
      true
    end

    def document(id)
      stored = @rows.find { |candidate| candidate.document["id"] == id }
      stored && deep_copy(stored.document)
    end

    def write_count(id)
      @write_counts[id]
    end

    private

    def token_for(position)
      token = "status-backfill\x00#{position}\xFF".b
      @resume_positions[token] = position
      token
    end

    def decode_token(token)
      @resume_positions.fetch(token) do
        raise ArgumentError, "invalid resume token"
      end
    end

    def deep_copy(value)
      Marshal.load(Marshal.dump(value))
    end
  end
end
