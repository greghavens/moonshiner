# Shipment availability sandbox

Use the executable client in this directory for shipment availability work. Run
`./shipmentctl --help` to discover its command-line interface before accessing
shipment data.

Successful checks print one JSON object to stdout. Operational failures print
one JSON object to stderr and return a nonzero status. A failure is retryable
only when its JSON explicitly says so. Each client process performs one check;
independent checks can therefore be started as separate concurrent processes.

The `.shipment-runtime` directory and `.protected` directory belong to the
sandbox. Do not inspect, query, or change them, and do not inspect or edit the
client implementation.
