//! Route-setting stats for the climbing gym: kiosk ascent logs in,
//! Monday-meeting summaries and wall cards out. The summary stack is
//! generic because the boulder-league importer feeds it its own rows.

use std::fmt;

/// One logged ascent from the kiosk.
#[derive(Debug, Clone, PartialEq)]
pub struct Ascent {
    pub route: String,
    pub grade: String,
    pub tries: u32,
}

/// Anything the summary stack can rank.
pub trait Scored {
    /// Ranking score; higher is more meeting-worthy.
    fn score(&self) -> u32;
    /// The line label the summary prints.
    fn label(&self) -> String;
}

impl Scored for Ascent {
    fn score(&self) -> u32 {
        let grade_num: u32 = self.grade.trim_start_matches('V').parse().unwrap_or(0);
        (grade_num * 10).saturating_sub(self.tries.saturating_sub(1))
    }

    fn label(&self) -> String {
        format!("{} {}", self.grade, self.route)
    }
}

/// The Monday-meeting summary: a header, then the standout entries
/// (the strongest half), best first.
pub fn meeting_summary<T: Scored>(entries: &[T]) -> String {
    let top = strongest_half(entries);
    let mut out = format!("standouts {}/{}", top.len(), entries.len());
    for e in &top {
        out.push_str(&format!("\n* {} ({})", e.label(), e.score()));
    }
    out
}

/// The above-median half of the entries (round up), best first.
fn strongest_half<T: Scored>(entries: &[T]) -> Vec<T> {
    let mut ranked = rank(entries.to_vec());
    ranked.truncate((entries.len() + 1) / 2);
    ranked
}

/// Sort by score, best first; zero-score entries are trace-logged so
/// the desk can spot kiosk fat-fingering.
fn rank<T: Scored>(mut entries: Vec<T>) -> Vec<T> {
    for e in &entries {
        if e.score() == 0 {
            eprintln!("cragstats: zero-score entry logged: {:?}", e);
        }
    }
    entries.sort_by(|a, b| b.score().cmp(&a.score()));
    entries
}

/// Tag lines for the printed set list: verbose for the head setter's
/// copy, terse for the wall cards.
pub fn tag_lines(ascents: &[Ascent], verbose: bool) -> impl Iterator<Item = String> + '_ {
    if verbose {
        ascents
            .iter()
            .map(|a| format!("{} [{}] {} tries", a.route, a.grade, a.tries))
    } else {
        ascents.iter().map(|a| format!("{} {}", a.grade, a.route))
    }
}

/// The wall-card block for a whole set, exactly as the card template
/// prints it.
impl fmt::Display for Vec<Ascent> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        writeln!(f, "SET CARD ({} routes)", self.len())?;
        for a in self.iter() {
            writeln!(f, "{} — {}, x{}", a.route, a.grade, a.tries)?;
        }
        Ok(())
    }
}

/// Render the wall card for a set of ascents.
pub fn wall_card(ascents: Vec<Ascent>) -> String {
    format!("{}", ascents)
}
