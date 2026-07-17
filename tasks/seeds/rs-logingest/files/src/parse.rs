//! Line parsing and timestamp normalization.

use crate::{IngestConfig, ParseError};

/// One parsed log line, timestamp already normalized to UTC epoch seconds.
#[derive(Debug, Clone)]
pub(crate) struct Record {
    pub epoch: i64,
    pub host: String,
    pub level: String,
    pub msg: String,
}

enum Offset {
    /// Trailing `Z`: the civil time is already UTC.
    Utc,
    /// Explicit `+HH:MM` / `-HH:MM` suffix, in minutes east of UTC.
    Explicit(i32),
    /// No suffix: agent-local wall time, interpreted via the configured
    /// fleet offset.
    Missing,
}

fn err(line: usize, msg: &str) -> ParseError {
    ParseError {
        line,
        msg: msg.to_string(),
    }
}

pub(crate) fn parse_line(
    raw: &str,
    line: usize,
    cfg: &IngestConfig,
) -> Result<Record, ParseError> {
    let raw = raw.trim();
    let (ts, rest) = raw
        .split_once(' ')
        .ok_or_else(|| err(line, "missing fields after timestamp"))?;
    let (civil, offset) = parse_timestamp(ts, line)?;
    let epoch = match offset {
        Offset::Utc => civil,
        Offset::Explicit(minutes) => civil - minutes as i64 * 60,
        Offset::Missing => civil - implied_offset_seconds(cfg),
    };
    let (host, rest) = take_bare_field(rest, "host", line)?;
    let (level, rest) = take_bare_field(rest, "level", line)?;
    let msg = take_quoted_field(rest, "msg", line)?;
    Ok(Record {
        epoch,
        host,
        level,
        msg,
    })
}

/// Seconds to subtract from a naive (offset-less) civil time to reach UTC.
/// `default_offset_minutes` is the fleet's offset in minutes east of UTC.
fn implied_offset_seconds(cfg: &IngestConfig) -> i64 {
    (cfg.default_offset_minutes as i64 % 60) * 60
}

/// `host=web-1` -> ("web-1", remainder). The value runs to the next space.
fn take_bare_field<'a>(
    rest: &'a str,
    name: &str,
    line: usize,
) -> Result<(String, &'a str), ParseError> {
    let rest = rest.trim_start();
    let after = rest
        .strip_prefix(&format!("{name}="))
        .ok_or_else(|| err(line, &format!("missing field '{name}'")))?;
    let end = after.find(' ').unwrap_or(after.len());
    if end == 0 {
        return Err(err(line, &format!("empty field '{name}'")));
    }
    Ok((after[..end].to_string(), &after[end..]))
}

/// `msg="db timeout"` -> "db timeout". Must be the last thing on the line.
fn take_quoted_field(rest: &str, name: &str, line: usize) -> Result<String, ParseError> {
    let rest = rest.trim_start();
    let after = rest
        .strip_prefix(&format!("{name}=\""))
        .ok_or_else(|| err(line, &format!("missing field '{name}'")))?;
    let close = after
        .find('"')
        .ok_or_else(|| err(line, &format!("unterminated {name}")))?;
    if !after[close + 1..].trim().is_empty() {
        return Err(err(line, &format!("trailing characters after {name}")));
    }
    Ok(after[..close].to_string())
}

/// Parse `YYYY-MM-DDTHH:MM:SS` plus an optional offset suffix. Returns the
/// civil time as "epoch seconds pretending the wall clock were UTC" plus the
/// offset designator; the caller applies the offset.
fn parse_timestamp(ts: &str, line: usize) -> Result<(i64, Offset), ParseError> {
    let b = ts.as_bytes();
    if b.len() < 19 {
        return Err(err(line, "bad timestamp"));
    }
    for (i, &c) in b[..19].iter().enumerate() {
        let ok = match i {
            4 | 7 => c == b'-',
            10 => c == b'T',
            13 | 16 => c == b':',
            _ => c.is_ascii_digit(),
        };
        if !ok {
            return Err(err(line, "bad timestamp"));
        }
    }
    let num = |a: usize, z: usize| -> i64 { ts[a..z].parse().unwrap() };
    let (year, month, day) = (num(0, 4), num(5, 7), num(8, 10));
    let (hour, minute, second) = (num(11, 13), num(14, 16), num(17, 19));
    if !(1..=12).contains(&month)
        || !(1..=31).contains(&day)
        || hour > 23
        || minute > 59
        || second > 59
    {
        return Err(err(line, "bad timestamp"));
    }
    let civil = days_from_civil(year, month, day) * 86_400 + hour * 3_600 + minute * 60 + second;
    let offset = match &ts[19..] {
        "" => Offset::Missing,
        "Z" => Offset::Utc,
        suffix => Offset::Explicit(
            parse_offset_suffix(suffix).ok_or_else(|| err(line, "bad timestamp"))?,
        ),
    };
    Ok((civil, offset))
}

/// `+HH:MM` / `-HH:MM` -> signed minutes east of UTC.
fn parse_offset_suffix(s: &str) -> Option<i32> {
    let b = s.as_bytes();
    if b.len() != 6 || b[3] != b':' {
        return None;
    }
    let sign = match b[0] {
        b'+' => 1,
        b'-' => -1,
        _ => return None,
    };
    if !(b[1].is_ascii_digit() && b[2].is_ascii_digit() && b[4].is_ascii_digit() && b[5].is_ascii_digit())
    {
        return None;
    }
    let hours: i32 = s[1..3].parse().ok()?;
    let minutes: i32 = s[4..6].parse().ok()?;
    if hours > 23 || minutes > 59 {
        return None;
    }
    Some(sign * (hours * 60 + minutes))
}

/// Days since 1970-01-01 for a proleptic-Gregorian civil date.
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}
