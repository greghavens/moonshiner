use rs_signalbox::aspect::{from_code, more_restrictive, Aspect};
use rs_signalbox::command::{classify, Action};
use rs_signalbox::plan::{check_release, dwell_secs, journal_line, lever_label, DEFAULT_DWELL_SECS};

#[test]
fn every_console_keyword_maps_to_its_action() {
    assert_eq!(classify("set"), Action::SetRoute);
    assert_eq!(classify("clear"), Action::ClearRoute);
    assert_eq!(classify("release"), Action::ReleaseLock);
    assert_eq!(classify("dim"), Action::Unknown);
    assert_eq!(classify(""), Action::Unknown);
}

#[test]
fn lamp_codes_decode_per_the_aspect_table() {
    assert_eq!(from_code(0), Aspect::Danger);
    assert_eq!(from_code(1), Aspect::Caution);
    assert_eq!(from_code(2), Aspect::Preliminary, "code 2 is double yellow");
    assert_eq!(from_code(3), Aspect::Clear);
    assert_eq!(from_code(7), Aspect::Clear, "spare bus positions read clear");
}

#[test]
fn overlapping_routes_show_the_more_restrictive_aspect() {
    assert_eq!(more_restrictive(Aspect::Clear, Aspect::Caution), Aspect::Caution);
    assert_eq!(more_restrictive(Aspect::Danger, Aspect::Clear), Aspect::Danger);
    assert_eq!(
        more_restrictive(Aspect::Preliminary, Aspect::Preliminary),
        Aspect::Preliminary
    );
}

#[test]
fn dwell_falls_back_to_the_house_default() {
    assert_eq!(dwell_secs(""), DEFAULT_DWELL_SECS);
    assert_eq!(dwell_secs("   "), DEFAULT_DWELL_SECS);
    assert_eq!(dwell_secs("not-a-number"), DEFAULT_DWELL_SECS);
    assert_eq!(dwell_secs(" 90 "), 90);
    assert_eq!(dwell_secs("120"), 120);
}

#[test]
fn lever_labels_are_trimmed_and_uppercased() {
    assert_eq!(lever_label("  7b "), "7B");
    assert_eq!(lever_label("12A"), "12A");
    assert_eq!(lever_label("\t 3 \t"), "3");
}

#[test]
fn refused_requests_journal_with_the_real_reason() {
    let err = check_release("12", true).unwrap_err();
    assert_eq!(err.to_string(), "lever 12: points locked out");
    assert_eq!(journal_line(&err), "REFUSED lever 12: points locked out");
    assert!(check_release("12", false).is_ok());
}
