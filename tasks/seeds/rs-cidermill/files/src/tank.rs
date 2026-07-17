use std::fmt;

/// A juice tank on the pressing floor. Pours are all-or-nothing: a pour
/// that would overflow the tank is refused at the valve and the tank is
/// left unchanged.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Tank {
    pub label: String,
    pub capacity_l: u32,
    pub filled_l: u32,
}

/// A pour that was turned away at the valve.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RejectedPour {
    pub tank: String,
    pub litres: u32,
    pub over_by_l: u32,
}

impl fmt::Display for RejectedPour {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{} refused {} L ({} L over capacity)",
            self.tank, self.litres, self.over_by_l
        )
    }
}

impl Tank {
    pub fn new(label: &str, capacity_l: u32) -> Tank {
        Tank {
            label: label.to_string(),
            capacity_l,
            filled_l: 0,
        }
    }

    /// Litres still available before the tank tops out.
    pub fn headroom_l(&self) -> u32 {
        self.capacity_l - self.filled_l
    }

    /// All-or-nothing pour into the tank.
    #[must_use = "a refused pour has to be recorded on the day ledger"]
    pub fn pour(&mut self, litres: u32) -> Result<(), RejectedPour> {
        if litres > self.headroom_l() {
            return Err(RejectedPour {
                tank: self.label.clone(),
                litres,
                over_by_l: litres - self.headroom_l(),
            });
        }
        self.filled_l += litres;
        Ok(())
    }
}
