//! Shelf-tag codes for the branch library. A spine sticker like
//! "FIC-ADK-03" means section FIC, author cluster ADK, slot 3 counted
//! from the left bookend. Tags borrow straight from the scanned line;
//! nothing here owns the strings.

#[derive(Debug, PartialEq, Eq)]
pub struct ShelfTag<'a> {
    pub section: &'a str,
    pub cluster: &'a' str,
    pub slot: u8,
}

/// Parse a "SEC-CLU-NN" sticker. Section and cluster borrow from the
/// input; a tag with an empty part or a non-numeric slot is a mis-print.
pub fn parse(code: &str) -> Option<ShelfTag<'_>> {
    let mut parts = code.splitn(3, '-');
    let section = parts.next()?;
    let cluster = parts.next()?;
    let slot = parts.next()?.trim().parse().ok()?;
    if section.is_empty() || cluster.is_empty() {
        return None;
    }
    Some(ShelfTag { section, cluster, slot })
}

/// Two tags shelve in the same bay when section and cluster agree.
pub fn same_bay(a: &ShelfTag, b: &ShelfTag) -> bool {
    a.section == b.section && a.cluster == b.cluster
}

/// The printed spine label, slot zero-padded to two digits.
pub fn spine_label(tag: &ShelfTag) -> String {
    format!("{}-{}-{:02}", tag.section, tag.cluster, tag.slot)
}

/// Shelving order: section, then cluster, then slot.
pub fn sort_key<'a>(tag: &ShelfTag<'a>) -> (&'a str, &'a str, u8) {
    (tag.section, tag.cluster, tag.slot)
}
