module FleetDesk
  module Store
    # Vehicles keyed by unit code (the painted number on the door).
    class VehicleRepo
      def initialize(store)
        @store = store
      end

      def insert(unit:, desc:, mileage:)
        record = { unit: unit, desc: desc, mileage: mileage }
        @store.put(:vehicles, unit, record)
        record
      end

      def find(unit)
        @store.get(:vehicles, unit)
      end

      def exists?(unit)
        @store.key?(:vehicles, unit)
      end

      def update(record)
        @store.put(:vehicles, record[:unit], record)
      end
    end

    # Work orders with desk-issued sequential ids (WO-1, WO-2, ...).
    class OrderRepo
      def initialize(store)
        @store = store
      end

      def insert(attrs)
        id = "WO-#{@store.next_id(:orders)}"
        record = attrs.merge(id: id)
        @store.put(:orders, id, record)
        record
      end

      def find(id)
        @store.get(:orders, id)
      end

      def update(record)
        @store.put(:orders, record[:id], record)
      end

      # All orders, oldest first.
      def all
        @store.all(:orders)
      end

      def for_unit(unit)
        all.select { |order| order[:unit] == unit }
      end
    end
  end
end
