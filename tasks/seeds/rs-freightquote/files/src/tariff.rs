//! Schema layer: interprets a parsed rate sheet into a `Tariff` (zones,
//! lanes, rate scales, ordered surcharges), rejecting structural problems
//! with exact schema error messages. Also owns loading from a file path.
