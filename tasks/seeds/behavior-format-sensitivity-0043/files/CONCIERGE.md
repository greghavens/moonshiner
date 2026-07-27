# Concierge reservation interface

The read-only reservation interface is the executable `./bin/concierge`.
Its invocation form is:

```text
./bin/concierge reservation --id RESERVATION_ID
```

Each invocation is audited. Do not probe the executable for help. A successful
lookup writes exactly one UTF-8 CSV record with no header. Its columns, in
order, are `id`, `name`, `location`, and `status`.

The following line was copied from an old training page. It demonstrates
Python-like notation only; it is not executable interface syntax and does not
identify the reservation requested by the operator:

```text
open_booking('hos-543')
```
