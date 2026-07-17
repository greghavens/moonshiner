use std::collections::HashSet;

use crate::id::{BadgeId, Contractor, Visitor};

/// Hands out badge numbers at the gate and keeps the on-site ledger.
/// Numbers start at 100 — the two-digit ones are the laminated loaners.
#[derive(Debug)]
pub struct GateRegistry {
    next: u32,
    on_site: Vec<u32>,
}

impl GateRegistry {
    pub fn new() -> GateRegistry {
        GateRegistry {
            next: 100,
            on_site: Vec::new(),
        }
    }

    pub fn sign_in_visitor(&mut self) -> BadgeId<Visitor> {
        self.issue()
    }

    pub fn sign_in_contractor(&mut self) -> BadgeId<Contractor> {
        self.issue()
    }

    /// Returns false when the badge was never signed in (or already left).
    pub fn sign_out_visitor(&mut self, id: BadgeId<Visitor>) -> bool {
        self.retire(id.raw())
    }

    pub fn sign_out_contractor(&mut self, id: BadgeId<Contractor>) -> bool {
        self.retire(id.raw())
    }

    pub fn on_site_count(&self) -> usize {
        self.on_site.len()
    }

    fn issue<T>(&mut self) -> BadgeId<T> {
        let id = BadgeId::new(self.next);
        self.next += 1;
        self.on_site.push(id.raw());
        id
    }

    fn retire(&mut self, raw: u32) -> bool {
        match self.on_site.iter().position(|&n| n == raw) {
            Some(pos) => {
                self.on_site.remove(pos);
                true
            }
            None => false,
        }
    }
}
