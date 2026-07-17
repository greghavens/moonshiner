//! Hand-written parser for the `.rates` tariff sheet format: `[section]`
//! headers, `key = value` entries, typed values (int / $money / percent% /
//! "string" / bool), `#` comments, and parse errors carrying 1-based line
//! numbers. No external crates — this parser is part of the deliverable.
