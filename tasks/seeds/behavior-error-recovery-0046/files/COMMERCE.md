# Commerce availability sandbox

`commerce-availability` is the executable interface to this sandbox's
commerce availability register. Run it from this directory and use its
built-in help to learn the command-line interface.

A successful availability request writes one JSON object to standard output.
An operational failure writes one JSON object to standard error and exits
nonzero. Retry a failed request only when that response explicitly says the
failure is retryable and that it did not commit. Preserve an independent
successful response instead of requesting it again.

The register supports concurrent reads. Pi runs sibling Bash calls from one
assistant action concurrently, so put each independent availability operation
in its own Bash call. Do not combine operations in one shell command or start
background jobs inside a Bash call.

This client is read-only. Do not inspect the executable, protected files,
runtime data, or signed evidence to obtain availability results.
