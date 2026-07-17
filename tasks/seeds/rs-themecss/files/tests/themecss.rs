use rs_themecss::{accent_rule, font_stack, render_theme, rule, Palette};

#[test]
fn font_stack_keeps_the_quoted_family_names() {
    assert_eq!(
        font_stack(),
        "font-family: \"Iosevka Custom\", ui-monospace, monospace;"
    );
}

#[test]
fn rule_renders_props_one_per_line() {
    assert_eq!(
        rule(".sidebar", &[("width", "16rem"), ("padding", "1rem")]),
        ".sidebar {\n  width: 16rem;\n  padding: 1rem;\n}\n"
    );
}

#[test]
fn empty_rule_still_renders_a_block() {
    assert_eq!(rule(".stub", &[]), ".stub {\n}\n");
}

#[test]
fn accent_rule_uses_the_palette_color() {
    assert_eq!(
        accent_rule("a.accent", "#d08770"),
        "a.accent { color: #d08770; }\n"
    );
}

#[test]
fn full_theme_file_renders_every_section() {
    let p = Palette {
        name: "dusk",
        fg: "#e5e9f0",
        bg: "#2e3440",
        accent: "#d08770",
    };
    let want = "/* theme: dusk */\n\
                body {\n  color: #e5e9f0;\n  background: #2e3440;\n}\n\
                a.accent { color: #d08770; }\n\
                code { font-family: \"Iosevka Custom\", ui-monospace, monospace; }\n";
    assert_eq!(render_theme(&p), want);
}
