//! Storage accounting for the artifact registry.
//!
//! Every uploaded object is charged in whole 4 KiB accounting blocks — the
//! unit the month-end billing export uses. The ledger tracks charged bytes
//! per tenant and answers the quota question the upload gateway asks before
//! admitting a new object. Re-uploading an existing key replaces the old
//! charge; deleting an object frees it.

use std::collections::HashMap;

/// Accounting block size: uploads are charged in whole 4 KiB blocks.
pub const BLOCK: u64 = 4096;

/// Raw length rounded up to a whole number of accounting blocks.
fn charged_size(len: u64) -> u64 {
    (len + BLOCK - 1) / BLOCK * BLOCK
}

#[derive(Debug, Clone)]
struct StoredObject {
    key: String,
    charged: u32,
}

/// Per-tenant usage ledger for one storage region.
#[derive(Debug, Default)]
pub struct UsageLedger {
    tenants: HashMap<String, Vec<StoredObject>>,
}

impl UsageLedger {
    pub fn new() -> Self {
        Self::default()
    }

    /// Record an upload. Re-uploading an existing key replaces the previous
    /// object (the old charge is released).
    pub fn record_upload(&mut self, tenant: &str, key: &str, len: u64) {
        let objects = self.tenants.entry(tenant.to_string()).or_default();
        objects.retain(|o| o.key != key);
        objects.push(StoredObject {
            key: key.to_string(),
            charged: charged_size(len) as u32,
        });
    }

    /// Delete an object, freeing its charge. Returns false if the tenant or
    /// key was unknown.
    pub fn remove(&mut self, tenant: &str, key: &str) -> bool {
        match self.tenants.get_mut(tenant) {
            None => false,
            Some(objects) => {
                let before = objects.len();
                objects.retain(|o| o.key != key);
                objects.len() != before
            }
        }
    }

    /// Number of live objects a tenant has.
    pub fn object_count(&self, tenant: &str) -> usize {
        self.tenants.get(tenant).map_or(0, |o| o.len())
    }

    /// Charged bytes currently held by one tenant.
    pub fn tenant_usage(&self, tenant: &str) -> u64 {
        self.tenants
            .get(tenant)
            .map_or(0, |objects| objects.iter().map(|o| o.charged as u64).sum())
    }

    /// Charged bytes across every tenant in the region.
    pub fn total_usage(&self) -> u64 {
        self.tenants
            .keys()
            .map(|tenant| self.tenant_usage(tenant))
            .sum()
    }

    /// Would admitting an upload of `len` raw bytes push the tenant past its
    /// quota? Landing exactly on the quota is allowed.
    pub fn would_exceed(&self, tenant: &str, len: u64, quota: u64) -> bool {
        self.tenant_usage(tenant) + charged_size(len) > quota
    }

    /// The `n` heaviest tenants, largest first; ties break by tenant name so
    /// the report is reproducible.
    pub fn largest_tenants(&self, n: usize) -> Vec<(String, u64)> {
        let mut rows: Vec<(String, u64)> = self
            .tenants
            .keys()
            .map(|tenant| (tenant.clone(), self.tenant_usage(tenant)))
            .collect();
        rows.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
        rows.truncate(n);
        rows
    }
}
