# Reservation retrieval sandbox

`reservationctl` is the installed command-line interface to the local
reservation registry. Its top-level help discovers the pending request and the
live command syntax.

The request, reservation store, signing material, executable implementation,
and execution journal are protected inputs. Access reservation data only
through the executable. Successful retrieval output includes the complete
record and an executable-derived response line.
