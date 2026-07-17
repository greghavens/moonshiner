//! Renders the CSS theme files the docs site ships for each palette.

pub struct Palette {
    pub name: &'static str,
    pub fg: &'static str,
    pub bg: &'static str,
    pub accent: &'static str,
}

/// The font stanza every theme ends with; the family list is a hard
/// requirement from the design team, quotes and all.
const FONT_STACK: &str = r"font-family: "Iosevka Custom", ui-monospace, monospace;";

pub fn font_stack() -> &'static str {
    FONT_STACK
}

/// One CSS rule: selector, then each `prop: value;` on its own indented line.
pub fn rule(selector: &str, props: &[(&str, &str)]) -> String {
    let mut body = String::new();
    for (prop, value) in props {
        body.push_str(&format!("  {prop}: {value};\n"));
    }
    format!("{selector} {{\n{body}}}\n")
}

/// The one-line accent rule used for links and callouts; `accent` arrives
/// as a hex color straight from the palette.
pub fn accent_rule(selector: &str, accent: &str) -> String {
    format!("{selector} {{ color: {{accent}}; }}\n")
}

/// A full theme file: banner, body rule, accent rule, font stanza.
pub fn render_theme(p: &Palette) -> String {
    let mut out = String::new();
    out.push_str(&format!("/* theme: {} */\n", p.name));
    out.push_str(&rule("body", &[("color", p.fg), ("background", p.bg)]));
    out.push_str(&accent_rule("a.accent", p.accent));
    out.push_str(&format!("code {{ {} }}\n", font_stack()));
    out
}
