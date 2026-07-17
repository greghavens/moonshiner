//! Fixed-width shift windows over one day of tickets.
//!
//! The scale house exports tickets sorted by punch time, so aggregation is
//! a single forward pass. A window covers `[start, start + width)` minutes:
//! a ticket punched exactly at a window's end belongs to the *next* window.
//! Windows with no tickets are dropped from the output.

use std::collections::BTreeMap;

/// Totals for one shift window that saw at least one ticket.
#[derive(Debug, Clone, PartialEq)]
pub struct ShiftSlice {
    pub start_minute: u32,
    /// Net kilograms per material; `BTreeMap` for a stable report order.
    pub totals: BTreeMap<String, f64>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum WindowError {
    OutOfOrder { prev: u32, got: u32 },
    BeforeOpen { open: u32, got: u32 },
}

impl std::fmt::Display for WindowError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WindowError::OutOfOrder { prev, got } => {
                write!(f, "export not sorted: minute {got} after {prev}")
            }
            WindowError::BeforeOpen { open, got } => {
                write!(f, "ticket at minute {got} before opening minute {open}")
            }
        }
    }
}

/// Streaming aggregator over sorted tickets.
pub struct ShiftWindows {
    width: u32,
    cur_start: u32,
    cur: BTreeMap<String, f64>,
    done: Vec<ShiftSlice>,
    last_minute: Option<u32>,
}

impl ShiftWindows {
    pub fn new(open_minute: u32, width: u32) -> Self {
        assert!(width > 0, "window width must be positive");
        ShiftWindows {
            width,
            cur_start: open_minute,
            cur: BTreeMap::new(),
            done: Vec::new(),
            last_minute: None,
        }
    }

    /// Account one ticket's net kilograms to the window its minute falls in.
    pub fn add(&mut self, minute: u32, material: &str, net_kg: f64) -> Result<(), WindowError> {
        if let Some(prev) = self.last_minute {
            if minute < prev {
                return Err(WindowError::OutOfOrder { prev, got: minute });
            }
        }
        if minute < self.cur_start {
            return Err(WindowError::BeforeOpen { open: self.cur_start, got: minute });
        }
        self.last_minute = Some(minute);
        while minute > self.cur_start + self.width {
            self.roll();
        }
        *self.cur.entry(material.to_string()).or_insert(0.0) += net_kg;
        Ok(())
    }

    fn roll(&mut self) {
        if !self.cur.is_empty() {
            let totals = std::mem::take(&mut self.cur);
            self.done.push(ShiftSlice { start_minute: self.cur_start, totals });
        }
        self.cur_start += self.width;
    }

    /// Close out the pass and hand back every populated window in order.
    pub fn finish(mut self) -> Vec<ShiftSlice> {
        if !self.cur.is_empty() {
            let totals = std::mem::take(&mut self.cur);
            self.done.push(ShiftSlice { start_minute: self.cur_start, totals });
        }
        self.done
    }
}
