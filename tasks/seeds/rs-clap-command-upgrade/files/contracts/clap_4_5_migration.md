# clap 4.5 command migration contract (protected local copy)

This repository's offline `clap_v4` module records the first-party boundary
used for the upgrade. The old builder calls `Arg::takes_value` and
`Arg::multiple_occurrences` are removed. Value arity is declared with
`Arg::num_args`, while flags and counters use `ArgAction::SetTrue` and
`ArgAction::Count`.

Typed match access replaces the old stringly accessors. Callers use
`get_one`, `get_flag`, `get_count`, and `subcommand`; the old `value_of`,
`occurrences_of`, and `subcommand_name` accessors are absent from clap 4.
The local contract retains fail-closed trap stubs for those names only so the
shipped pre-migration integration can produce deterministic evidence.

Global arguments remain valid before or after a subcommand. Defaults are
reported through matches and help. Parser usage errors exit with status 2,
while `--help` is successful and exits with status 0. Migrating the API does
not authorize changing subcommand names, option spelling, conflicts, help
text, or application exit behavior.
