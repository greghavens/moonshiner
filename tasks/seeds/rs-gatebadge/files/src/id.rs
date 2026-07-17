use std::fmt;

/// Marker for visitor badges (blue lanyard).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Visitor;

/// Marker for escorted-contractor badges (orange lanyard).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Contractor;

/// A badge number that only mixes with ledgers of the same badge kind —
/// handing a contractor badge to a visitor API is a compile error.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BadgeId<T> {
    raw: u32,
}

impl<T> BadgeId<T> {
    pub fn new(raw: u32) -> BadgeId<T> {
        BadgeId { raw }
    }

    pub fn raw(&self) -> u32 {
        self.raw
    }
}
