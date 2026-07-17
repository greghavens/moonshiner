use super::chem::Developer;

/// The film stock a roll was shot on.
#[derive(Debug, Clone, PartialEq)]
pub struct FilmStock {
    pub name: String,
    pub iso: u32,
}

/// One souped roll.
pub struct Entry {
    pub stock: FilmStock,
    pub developer: Developer,
    pub minutes: u32,
    frames: u32,
}

impl Entry {
    pub fn new(stock: FilmStock, developer: Developer, minutes: u32, frames: u32) -> Entry {
        Entry { stock, developer, minutes, frames }
    }

    /// Frames that fit the contact-sheet grid (a 36-frame sheet).
    fn sheet_frames(&self) -> u32 {
        self.frames.min(36)
    }
}
