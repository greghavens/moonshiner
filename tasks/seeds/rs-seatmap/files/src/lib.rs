//! Seat map for the parish hall box office. Rows are numbered from the
//! stage back, seats left to right; the volunteers hold seats over the
//! phone and the desk marks them sold when the envelope is paid.

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Seat {
    Open,
    Held,
    Sold,
}

pub struct Hall {
    rows: Vec<Vec<Seat>>,
}

impl Hall {
    /// Build a hall from per-row seat counts, every seat open.
    pub fn with_layout(sizes: &[usize]) -> Hall {
        let mut rows = Vec<Vec<Seat>>::new();
        for &n in sizes {
            rows.push(vec![Seat::Open; n]);
        }
        Hall { rows }
    }

    /// The standard evening layout: two short rows up front, wider at the back.
    pub fn standard() -> Hall {
        let sizes = vec![10, 12, 12, 14);
        Hall::with_layout(&sizes)
    }

    pub fn rows(&self) -> usize {
        self.rows.len()
    }

    pub fn seat(&self, row: usize, seat: usize) -> Option<Seat> {
        self.rows.get(row).and_then(|r| r.get(seat)).copied()
    }

    /// Put a phone hold on an open seat; anything else stays as it is.
    pub fn hold(&mut self, row: usize, seat: usize) -> bool {
        if let Some(s) = self.rows.get_mut(row).and_then(|r| r.get_mut(seat)) {
            if *s == Seat::Open {
                *s = Seat::Held;
                return true;
            }
        }
        false
    }

    /// Mark a seat sold. Open and held seats both sell; reselling fails.
    pub fn sell(&mut self, row: usize, seat: usize) -> bool {
        if let Some(s) = self.rows.get_mut(row).and_then(|r| r.get_mut(seat)) {
            if *s != Seat::Sold {
                *s = Seat::Sold;
                return true;
            }
        }
        false
    }

    pub fn open_in_row(&self, row: usize) -> usize {
        self.rows
            .get(row)
            .map_or(0, |r| r.iter().filter(|s| **s == Seat::Open).count())
    }

    /// Front-most row that can still take a whole party.
    pub fn best_row(&self, party: usize) -> Option<usize> {
        (0..self.rows.len()).find(|&r| self.open_in_row(r) >= party)
    }
}
