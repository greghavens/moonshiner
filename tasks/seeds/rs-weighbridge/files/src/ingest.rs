//! Weigh-ticket ingest: one export line per weighment, shared by both
//! scale lanes.
//!
//! A ticket line is six pipe-separated fields:
//!
//! ```text
//! head|minute|truck|material|net|unit
//! ```
//!
//! `head` is the scale head model (`S1` legacy outbound lane, `S2` new
//! inbound lane), `minute` is the minute-of-day the ticket was punched
//! (0..=1439), `net` is the net weight exactly as printed on the paper
//! ticket, and `unit` is `kg` or `lb`.

/// Pounds to kilograms, NIST conversion factor.
pub const LB_TO_KG: f64 = 0.45359237;

/// One parsed weigh ticket, net weight still in the punched unit.
#[derive(Debug, Clone, PartialEq)]
pub struct Ticket {
    pub head: String,
    pub minute: u32,
    pub truck: String,
    pub material: String,
    pub net: f64,
    pub unit: String,
}

#[derive(Debug, Clone, PartialEq)]
pub enum TicketError {
    FieldCount(usize),
    UnknownHead(String),
    BadMinute(String),
    BadNet(String),
    BadUnit(String),
}

impl std::fmt::Display for TicketError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TicketError::FieldCount(n) => write!(f, "expected 6 fields, got {n}"),
            TicketError::UnknownHead(h) => write!(f, "unknown scale head {h:?}"),
            TicketError::BadMinute(m) => write!(f, "bad minute-of-day {m:?}"),
            TicketError::BadNet(n) => write!(f, "bad net weight {n:?}"),
            TicketError::BadUnit(u) => write!(f, "bad unit {u:?} (kg or lb)"),
        }
    }
}

/// Parse one export line into a [`Ticket`].
pub fn parse_ticket(line: &str) -> Result<Ticket, TicketError> {
    let parts: Vec<&str> = line.trim().split('|').collect();
    if parts.len() != 6 {
        return Err(TicketError::FieldCount(parts.len()));
    }
    let head = parts[0].to_string();
    if head != "S1" && head != "S2" {
        return Err(TicketError::UnknownHead(head));
    }
    let minute: u32 = parts[1]
        .parse()
        .map_err(|_| TicketError::BadMinute(parts[1].to_string()))?;
    if minute >= 1440 {
        return Err(TicketError::BadMinute(parts[1].to_string()));
    }
    let truck = parts[2].to_string();
    let material = parts[3].to_string();
    let mut net: f64 = parts[4]
        .parse()
        .map_err(|_| TicketError::BadNet(parts[4].to_string()))?;
    if !net.is_finite() || net <= 0.0 {
        return Err(TicketError::BadNet(parts[4].to_string()));
    }
    let unit = parts[5].to_string();
    if unit != "kg" && unit != "lb" {
        return Err(TicketError::BadUnit(unit));
    }
    // The legacy S1 heads punch tickets in pounds no matter which display
    // profile the lane is configured with, so bring those onto the site
    // standard as early as possible.
    if head == "S1" && unit == "lb" {
        net *= LB_TO_KG;
    }
    Ok(Ticket { head, minute, truck, material, net, unit })
}

/// Net weight in kilograms for a value as punched on the ticket.
pub fn to_kg(net: f64, unit: &str) -> Result<f64, TicketError> {
    match unit {
        "kg" => Ok(net),
        "lb" => Ok(net * LB_TO_KG),
        other => Err(TicketError::BadUnit(other.to_string())),
    }
}
