//! Multi-pattern literal scanner behind the log-redaction rule engine.
//!
//! A rule set compiles into one [`PatternSet`]; every inbound document is
//! scanned once and each hit is reported as a [`Match`] carrying the byte
//! offset and the id (input index) of the pattern that fired. Overlapping
//! hits all count, and the same position may fire several patterns.

/// One hit: `pattern` is the index of the pattern in the slice passed to
/// [`PatternSet::new`], `start` the byte offset of the first matched byte.
///
/// The derived ordering (start ascending, then pattern id ascending) is the
/// order [`PatternSet::find_all`] returns matches in.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Match {
    /// Byte offset of the first byte of the hit.
    pub start: usize,
    /// Index of the pattern in the compiled set.
    pub pattern: usize,
}

/// A compiled set of literal patterns.
pub struct PatternSet {
    patterns: Vec<String>,
}

impl PatternSet {
    /// Compile a set of literal patterns. Pattern ids are their indexes in
    /// `patterns`, in the given order (duplicates are legal and keep their
    /// own ids).
    ///
    /// # Panics
    /// Panics if any pattern is empty.
    pub fn new(patterns: &[&str]) -> PatternSet {
        for p in patterns {
            assert!(!p.is_empty(), "pattern must be non-empty");
        }
        PatternSet {
            patterns: patterns.iter().map(|p| p.to_string()).collect(),
        }
    }

    /// Number of patterns in the set.
    pub fn len(&self) -> usize {
        self.patterns.len()
    }

    /// True when the set holds no patterns.
    pub fn is_empty(&self) -> bool {
        self.patterns.is_empty()
    }

    /// Every occurrence of every pattern in `text` — overlaps included —
    /// ordered by start offset, then pattern id.
    pub fn find_all(&self, text: &str) -> Vec<Match> {
        let hay = text.as_bytes();
        let mut matches = Vec::new();
        for start in 0..hay.len() {
            let rest = &hay[start..];
            for (pattern, pat) in self.patterns.iter().enumerate() {
                let needle = pat.as_bytes();
                if needle.len() <= rest.len() && &rest[..needle.len()] == needle {
                    matches.push(Match { start, pattern });
                }
            }
        }
        matches
    }
}
