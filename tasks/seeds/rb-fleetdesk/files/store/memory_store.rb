module FleetDesk
  module Store
    # In-memory tables with per-table id sequences; stands in for the depot
    # database in tests and the kiosk demo mode.
    class MemoryStore
      def initialize
        @tables = Hash.new { |hash, key| hash[key] = {} }
        @sequences = Hash.new(0)
      end

      def next_id(table)
        @sequences[table] += 1
      end

      def put(table, key, record)
        @tables[table][key] = record
      end

      def get(table, key)
        @tables[table][key]
      end

      # Records in insertion order.
      def all(table)
        @tables[table].values
      end

      def key?(table, key)
        @tables[table].key?(key)
      end
    end
  end
end
