//! Fixed-width table layout shared by the text outputs.

const NAME_WIDTH: usize = 10;

/// Left-align within `width`; never truncates.
pub fn pad_right(s: &str, width: usize) -> String {
    if s.len() >= width {
        s.to_string()
    } else {
        format!("{s}{}", " ".repeat(width - s.len()))
    }
}

pub fn table_header() -> String {
    format!(
        "{} {:>8} {:>9} {:>6}",
        pad_right("machine", NAME_WIDTH),
        "produced",
        "downtime",
        "scrap"
    )
}

pub fn table_row(machine: &str, produced: u64, downtime_min: u64, scrap: u64) -> String {
    format!(
        "{} {:>8} {:>9} {:>6}",
        pad_right(machine, NAME_WIDTH),
        produced,
        downtime_min,
        scrap
    )
}
