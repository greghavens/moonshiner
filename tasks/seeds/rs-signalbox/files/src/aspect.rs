/// Signal aspect shown for a lamp code coming off the relay bus.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Aspect {
    /// Red — stop.
    Danger,
    /// Single yellow — be ready to stop at the next signal.
    Caution,
    /// Double yellow — the signal after next is at danger.
    Preliminary,
    /// Green.
    Clear,
}

/// Decode one lamp code from the relay bus. Codes above 3 are spare
/// positions on the bus and read as clear by design.
pub fn from_code(code: u8) -> Aspect {
    match code {
        0 => Aspect::Danger,
        1..=2 => Aspect::Caution,
        2 => Aspect::Preliminary,
        _ => Aspect::Clear,
    }
}

/// The most restrictive of two aspects wins when routes overlap.
pub fn more_restrictive(a: Aspect, b: Aspect) -> Aspect {
    if rank(a) <= rank(b) {
        a
    } else {
        b
    }
}

fn rank(a: Aspect) -> u8 {
    match a {
        Aspect::Danger => 0,
        Aspect::Caution => 1,
        Aspect::Preliminary => 2,
        Aspect::Clear => 3,
    }
}
