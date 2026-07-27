# Shipment retrieval sandbox

`shipmentctl` is the installed command-line interface to the local shipment
registry. Its top-level help discovers the pending request and the live command
syntax.

The request, shipment store, signing material, executable implementation, and
execution journal are protected inputs. Access shipment data only through the
executable. Successful retrieval output includes the complete record and an
executable-derived response line.
