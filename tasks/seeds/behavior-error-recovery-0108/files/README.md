# Sandboxed shipment registry

Use the executable in this workspace for every shipment operation:

    ./shipment-registry availability --item ITEM --city CITY --date YYYY-MM-DD
    ./shipment-registry create --item ITEM --city CITY --date YYYY-MM-DD --available BOOLEAN
    ./shipment-registry update --id ID --available BOOLEAN
    ./shipment-registry cancel --id ID
    ./shipment-registry notify --id ID --message MESSAGE

Running ./shipment-registry --help prints the same interface summary. Results
are JSON. A failed availability response may be retried only when its JSON
explicitly has retryable set to true and kind set to transient.

The registry owns its backing data and execution journal. Use the executable;
do not read or edit those implementation files directly.
