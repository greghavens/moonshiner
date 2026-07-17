//! Duplicate-event detection.

use crate::parse::Record;

/// Identity of a log event for de-duplication. Two lines describing the same
/// event must map to the same key; distinct events must never collide.
pub(crate) fn dedup_key(rec: &Record) -> String {
    format!("{}|{}|{}", rec.epoch, rec.level, rec.msg)
}
