use rs_charslice::{clip, first_line_summary, SUBJECT_BUDGET};

#[test]
fn short_ascii_passes_through() {
    assert_eq!(clip("fix typo", 72), "fix typo");
}

#[test]
fn exact_budget_ascii_untouched() {
    assert_eq!(clip("abcdefgh", 8), "abcdefgh");
}

#[test]
fn ascii_clip_is_budget_sized() {
    // 11 kept characters + the ellipsis = exactly the 12-character budget.
    assert_eq!(clip("refactor the frobnicator setup", 12), "refactor th…");
    assert_eq!(clip("refactor the frobnicator setup", 12).chars().count(), 12);
}

#[test]
fn budget_zero_yields_empty() {
    assert_eq!(clip("anything", 0), "");
}

#[test]
fn budget_one_is_just_an_ellipsis() {
    assert_eq!(clip("hello", 1), "…");
    assert_eq!(clip("h", 1), "h");
}

#[test]
fn empty_string_stays_empty() {
    assert_eq!(clip("", 5), "");
}

#[test]
fn summary_is_first_line_only() {
    let msg = "add retry to uploader\n\nRetries the flaky S3 path three times.\n";
    assert_eq!(first_line_summary(msg), "add retry to uploader");
}

#[test]
fn summary_without_newline() {
    assert_eq!(first_line_summary("bump minor version"), "bump minor version");
}

#[test]
fn summary_drops_trailing_whitespace() {
    assert_eq!(first_line_summary("tidy imports   \nbody"), "tidy imports");
}

#[test]
fn summary_at_exactly_72_chars_untouched() {
    let subject = "a".repeat(SUBJECT_BUDGET);
    assert_eq!(first_line_summary(&subject), subject);
}

#[test]
fn summary_at_73_chars_clipped() {
    let subject = "a".repeat(SUBJECT_BUDGET + 1);
    let expected = format!("{}…", "a".repeat(SUBJECT_BUDGET - 1));
    assert_eq!(first_line_summary(&subject), expected);
}

#[test]
fn summary_of_empty_message() {
    assert_eq!(first_line_summary(""), "");
    assert_eq!(first_line_summary("\nbody only"), "");
}

#[test]
fn accented_subject_within_budget_passes_through() {
    // 10 characters, budget 10: nothing to clip.
    assert_eq!(clip("café crème", 10), "café crème");
}

#[test]
fn french_subject_never_overclipped() {
    // 15 characters fits a budget of 18 with room to spare.
    assert_eq!(clip("déjà vu détecté", 18), "déjà vu détecté");
}

#[test]
fn kanji_subject_within_budget_passes_through() {
    // 30 characters — comfortably inside the 72-character subject budget.
    let msg = "リリースノート生成ツールの改行処理を修正し、絵文字対応を追加\n\n詳細は以下の通り。\n";
    assert_eq!(
        first_line_summary(msg),
        "リリースノート生成ツールの改行処理を修正し、絵文字対応を追加"
    );
}

#[test]
fn kanji_field_clip_is_budget_sized() {
    let clipped = clip("東京支社の月次報告書テンプレート更新", 12);
    assert_eq!(clipped, "東京支社の月次報告書テ…");
    assert_eq!(clipped.chars().count(), 12);
}

#[test]
fn emoji_deploy_note_clipped() {
    assert_eq!(clip("deploy 🚀🚀🚀🚀🚀 to prod", 10), "deploy 🚀🚀…");
}

#[test]
fn cyrillic_field_clipped() {
    let clipped = clip("Исправлена ошибка в парсере дат", 12);
    assert_eq!(clipped, "Исправлена …");
    assert_eq!(clipped.chars().count(), 12);
}

#[test]
fn mixed_emoji_subject_clip_pinned() {
    assert_eq!(
        clip("fix: 🎉 release pipeline emoji handling", 12),
        "fix: 🎉 rele…"
    );
}
