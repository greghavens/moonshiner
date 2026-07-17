//! Single-pass streaming text statistics.
//!
//! Feed chunks of UTF-8 text in order and read the totals at the end; the
//! stream never has to be held in memory. Definitions:
//!
//! - a *line* is a maximal run of characters separated by `'\n'`; a trailing
//!   newline does not open a phantom empty line, but a non-empty final line
//!   without a newline still counts;
//! - a *word* is a maximal run of non-whitespace characters (per
//!   [`char::is_whitespace`]), and a word split across two `feed` calls is
//!   one word;
//! - `chars` counts Unicode scalar values, `bytes` counts UTF-8 bytes;
//! - line lengths are measured in chars and exclude the `'\n'` itself.
//!
//! There is no CRLF special-casing: `'\r'` is an ordinary whitespace
//! character and lands in the length of its line.

/// Totals for everything fed so far.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Summary {
    pub lines: usize,
    pub words: usize,
    pub chars: usize,
    pub bytes: usize,
    /// Length in chars of the longest line (0 when there are no lines).
    pub max_line_len: usize,
    /// Length in chars of the shortest line (0 when there are no lines).
    pub min_line_len: usize,
}

/// Streaming accumulator: create, `feed` any number of chunks, `summary`.
#[derive(Debug, Clone, Default)]
pub struct TextStats {
    bytes: usize,
    chars: usize,
    words: usize,
    newlines: usize,
    /// Chars in the line currently being read.
    cur_line: usize,
    /// True while the last char seen was part of a word.
    in_word: bool,
    /// Extremes over lines already closed by a newline.
    closed_min: Option<usize>,
    closed_max: Option<usize>,
}

impl TextStats {
    pub fn new() -> TextStats {
        TextStats::default()
    }

    /// Consume the next chunk of the stream. Chunk boundaries are arbitrary
    /// (they may split lines and words) but must fall on UTF-8 boundaries,
    /// which `&str` already guarantees.
    pub fn feed(&mut self, chunk: &str) {
        self.bytes += chunk.len();
        for ch in chunk.chars() {
            self.chars += 1;
            if ch == '\n' {
                self.close_line();
            } else {
                self.cur_line += 1;
            }
            if ch.is_whitespace() {
                self.in_word = false;
            } else if !self.in_word {
                self.in_word = true;
                self.words += 1;
            }
        }
    }

    fn close_line(&mut self) {
        let len = self.cur_line;
        self.closed_min = Some(self.closed_min.map_or(len, |m| m.min(len)));
        self.closed_max = Some(self.closed_max.map_or(len, |m| m.max(len)));
        self.newlines += 1;
        self.cur_line = 0;
    }

    /// Snapshot of the totals so far. Does not consume the accumulator; more
    /// chunks may be fed afterwards.
    pub fn summary(&self) -> Summary {
        let open_tail = self.cur_line > 0;
        let lines = self.newlines + usize::from(open_tail);
        let (mut min, mut max) = (self.closed_min, self.closed_max);
        if open_tail {
            min = Some(min.map_or(self.cur_line, |m| m.min(self.cur_line)));
            max = Some(max.map_or(self.cur_line, |m| m.max(self.cur_line)));
        }
        Summary {
            lines,
            words: self.words,
            chars: self.chars,
            bytes: self.bytes,
            max_line_len: max.unwrap_or(0),
            min_line_len: min.unwrap_or(0),
        }
    }
}
