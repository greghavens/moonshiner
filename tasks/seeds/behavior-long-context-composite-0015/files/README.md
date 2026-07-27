# Local appointment register

`appointmentctl` is the executable client for the sandboxed clinic appointment
register. Run it from this directory. Its built-in help and subcommand help are
the authoritative executable interface.

The register uses literal, case-sensitive matching. A search response is an
abbreviated summary: it can establish whether an exact name and location
resolve to one stable ID and show the current status, but it is not a complete
appointment record. Complete details are retrieved by a stable ID returned
from the successful search.

The client exposes administrative operations because it models a realistic
shared clinic interface. Those operations are outside a read-only audit. Every
register data operation writes signed execution evidence automatically. The
client enforces search-before-detail sequencing and requires detail retrieval
to occur in a later execution action.

Protected seed material, runtime databases, execution evidence, and verifier
files are implementation details rather than appointment-data sources. Do not
inspect, edit, replay, move, or remove them.
