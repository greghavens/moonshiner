//! Developer chemistry: what's on the shelf and the dilution math for
//! tonight's working batch.

pub use crate::log::FilmStock;

/// A developer on the shelf.
#[derive(Debug, Clone, PartialEq)]
pub enum Developer {
    D76,
    Rodinal,
    Xtol,
}

/// A working batch mixed for one session.
pub struct Batch {
    pub developer: Developer,
    pub stock_ml: u32,
    pub water_ml: u32,
}

/// The "1+9"-style dilution label the bottles are marked with.
fn dilution(batch: &Batch) -> String {
    let d = gcd(batch.stock_ml.max(1), batch.water_ml.max(1));
    format!("{}+{}", batch.stock_ml / d, batch.water_ml / d)
}

fn gcd(a: u32, b: u32) -> u32 {
    if b == 0 {
        a
    } else {
        gcd(b, a % b)
    }
}
