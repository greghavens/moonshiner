//! Importer for the nightly till-roll exports the point-of-sale terminals
//! drop for the back office: one header line, then one line per record.

/// First line of every genuine export, exactly as the terminals print it.
const HEADER: &[u8] = b"CAF\xc9 TILL v2";

/// True when the first line of an export identifies a genuine till roll.
pub fn is_valid_header(line: &str) -> bool {
    line == String::from_utf8_lossy(HEADER)
}

/// Field patterns for record validation. Matcher syntax: `\d` matches any
/// ASCII digit, `\.` a literal dot, `\\` a literal backslash, `.` any
/// single character; a trailing `+` repeats the element one or more times;
/// everything else matches itself. Anchored at both ends.
const QTY_PATTERN: &str = "\\d+";
const PRICE_PATTERN: &str = "\\d+\\\\.\\d\\d";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Sale {
    pub name: String,
    pub qty: u32,
    pub price_cents: u32,
}

#[derive(Clone, Copy)]
enum Elem {
    Any,
    Digit,
    Lit(char),
}

fn lex(pattern: &str) -> Vec<(Elem, bool)> {
    let mut out = Vec::new();
    let mut chars = pattern.chars().peekable();
    while let Some(c) = chars.next() {
        let elem = match c {
            '\\' => match chars.next() {
                Some('d') => Elem::Digit,
                Some(other) => Elem::Lit(other),
                None => Elem::Lit('\\'),
            },
            '.' => Elem::Any,
            other => Elem::Lit(other),
        };
        let plus = chars.peek() == Some(&'+');
        if plus {
            chars.next();
        }
        out.push((elem, plus));
    }
    out
}

fn elem_matches(e: Elem, c: char) -> bool {
    match e {
        Elem::Any => true,
        Elem::Digit => c.is_ascii_digit(),
        Elem::Lit(l) => l == c,
    }
}

fn match_from(elems: &[(Elem, bool)], text: &[char]) -> bool {
    match elems.split_first() {
        None => text.is_empty(),
        Some((&(e, plus), rest)) => {
            if plus {
                let mut n = 0;
                while n < text.len() && elem_matches(e, text[n]) {
                    n += 1;
                }
                while n >= 1 {
                    if match_from(rest, &text[n..]) {
                        return true;
                    }
                    n -= 1;
                }
                false
            } else {
                !text.is_empty() && elem_matches(e, text[0]) && match_from(rest, &text[1..])
            }
        }
    }
}

/// True when the whole of `text` matches `pattern`.
pub fn matches(pattern: &str, text: &str) -> bool {
    let elems = lex(pattern);
    let chars: Vec<char> = text.chars().collect();
    match_from(&elems, &chars)
}

/// One record line: `ITEM;<name>;<qty>;<price>`. Anything else (TOTAL
/// lines, blank lines, notes the terminal appends) yields None.
pub fn parse_line(line: &str) -> Option<Sale> {
    let mut parts = line.split(';');
    if parts.next()? != "ITEM" {
        return None;
    }
    let name = parts.next()?.trim();
    let qty = parts.next()?.trim();
    let price = parts.next()?.trim();
    if parts.next().is_some() || name.is_empty() {
        return None;
    }
    if !matches(QTY_PATTERN, qty) || !matches(PRICE_PATTERN, price) {
        return None;
    }
    let (whole, cents) = price.split_once('.')?;
    let qty: u32 = qty.parse().ok()?;
    let price_cents = whole.parse::<u32>().ok()? * 100 + cents.parse::<u32>().ok()?;
    Some(Sale {
        name: name.to_string(),
        qty,
        price_cents,
    })
}

/// A whole export: the header line, then records in order. Non-record
/// lines between records are the terminal's own chatter and are skipped.
pub fn parse_export(text: &str) -> Result<Vec<Sale>, String> {
    let mut lines = text.lines();
    let header = lines.next().unwrap_or("");
    if !is_valid_header(header) {
        return Err("not a till roll".to_string());
    }
    Ok(lines.filter_map(parse_line).collect())
}

/// Grand total of an import, in cents.
pub fn total_cents(sales: &[Sale]) -> u32 {
    sales.iter().map(|s| s.qty * s.price_cents).sum()
}
